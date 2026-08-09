"""Actually construct every entity for every bundled product.

The rest of the suite tests dispatch *rules* - which products get a fan,
which get feed buttons - but never built the entity objects themselves.
So a constructor that raises, or two entities colliding on a unique_id,
would only ever show up as "Error adding entity" in a real HA log
(SPEC.md Phase 22). With 48 products and ~1100 entities that's a lot of
surface to leave untested.

Needs the real `homeassistant` package; skips cleanly without it.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local import (  # noqa: E402
    binary_sensor,
    button,
    fan as fan_platform,
    number,
    select,
    sensor,
    switch,
)
from custom_components.jebao_local.fan import (  # noqa: E402
    FEED_NAMES,
    FEEDTIME_NAMES,
    fan_attr_names,
    find_attr_name,
)
from custom_components.jebao_local.jebao_gizwits.clock import HMS_ATTR, YMD_ATTR  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.schedule import slot_attr_name  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.schema import (  # noqa: E402
    known_product_keys,
    load_by_product_key,
)


class FakeCoordinator:
    """Enough of JebaoLocalCoordinator for entity constructors."""

    def __init__(self, schema):
        self.schema = schema
        self.did = "testdid123"
        self.host = "10.0.0.5"
        self.mac = "aabbccddeeff"
        self.data = {}
        self.last_update_success = True

    @property
    def hass(self):
        return None

    def async_add_listener(self, *args, **kwargs):
        return lambda: None


def build_entities(schema):
    """Mirror each platform's async_setup_entry filtering."""
    coordinator = FakeCoordinator(schema)
    entities = []

    fan_attrs = fan_attr_names(schema)
    fan_switch = fan_attrs[0] if fan_attrs else None
    fan_speed = fan_attrs[1] if fan_attrs else None
    if fan_attrs:
        entities.append(fan_platform.JebaoFan(coordinator, fan_switch, fan_speed))

    entities += [
        switch.JebaoSwitch(coordinator, a.name)
        for a in schema.attrs
        if a.writable and a.data_type == "bool" and a.name != fan_switch
    ]
    entities += [
        number.JebaoNumber(coordinator, a.name)
        for a in schema.attrs
        if a.writable and a.data_type in ("uint8", "uint16")
        and a.uint_spec is not None and a.name != fan_speed
    ]
    entities += [
        select.JebaoSelect(coordinator, a.name)
        for a in schema.attrs
        if a.writable and a.data_type == "enum" and a.enum_values
    ]
    entities += [
        binary_sensor.JebaoFaultBinarySensor(coordinator, a.name)
        for a in schema.attrs
        if a.is_problem and a.data_type == "bool"
    ]

    entities.append(sensor.JebaoStateSensor(coordinator))
    if fan_attrs:
        entities.append(sensor.JebaoSpeedSensor(coordinator))
    names = {a.name for a in schema.attrs}
    if slot_attr_name(0) in names:
        entities.append(sensor.JebaoScheduleSensor(coordinator))
    if YMD_ATTR in names and HMS_ATTR in names:
        entities.append(sensor.JebaoDeviceClockSensor(coordinator))
    entities += [
        sensor.JebaoReadonlySensor(coordinator, a.name)
        for a in schema.attrs
        if a.is_readonly_status and a.data_type == "uint8"
    ]
    entities.append(sensor.JebaoIpAddressSensor(coordinator))
    entities.append(sensor.JebaoMacAddressSensor(coordinator))

    feed = find_attr_name(schema, FEED_NAMES, "bool")
    if feed and find_attr_name(schema, FEEDTIME_NAMES, "uint8"):
        entities += [
            button.JebaoStartFeedButton(coordinator, feed),
            button.JebaoCancelFeedButton(coordinator, feed),
        ]
    return entities


ALL_KEYS = known_product_keys()


@pytest.mark.parametrize("product_key", ALL_KEYS)
def test_entities_construct_without_raising(product_key):
    assert build_entities(load_by_product_key(product_key))


@pytest.mark.parametrize("product_key", ALL_KEYS)
def test_no_duplicate_unique_or_object_ids(product_key):
    schema = load_by_product_key(product_key)
    seen_uid, seen_oid = {}, {}
    for entity in build_entities(schema):
        uid = entity._attr_unique_id
        oid = entity._attr_suggested_object_id
        assert uid, f"{type(entity).__name__} has no unique_id"
        assert uid not in seen_uid, (
            f"{schema.name_en}: {type(entity).__name__} and {seen_uid[uid]} "
            f"share unique_id {uid!r} - HA would reject the second"
        )
        assert oid not in seen_oid, (
            f"{schema.name_en}: {type(entity).__name__} and {seen_oid[oid]} "
            f"share object_id {oid!r} - entity_ids would collide"
        )
        seen_uid[uid] = type(entity).__name__
        seen_oid[oid] = type(entity).__name__


def test_the_whole_catalog_builds_a_sane_number_of_entities():
    total = sum(len(build_entities(load_by_product_key(pk))) for pk in ALL_KEYS)
    # Guards against a filter regression quietly dropping whole platforms.
    assert total > 900, f"only {total} entities across {len(ALL_KEYS)} products"


def test_every_product_reports_ip_and_mac():
    """The device page has no native field for an IP, and the MAC only
    appears there when the config entry has one stored - which older
    entries didn't, so some devices showed a MAC and others showed
    nothing (SPEC.md Phase 23). Both are now always present as entities.
    """
    for product_key in ALL_KEYS:
        entities = build_entities(load_by_product_key(product_key))
        kinds = {type(e).__name__ for e in entities}
        assert "JebaoIpAddressSensor" in kinds
        assert "JebaoMacAddressSensor" in kinds


def test_ip_and_mac_stay_available_when_the_pump_is_unreachable():
    """These are exactly the values you want visible when a pump has
    dropped off, so unlike every other entity they must not go
    unavailable with the coordinator."""
    schema = load_by_product_key(ALL_KEYS[0])
    coordinator = FakeCoordinator(schema)
    coordinator.data = None  # what an unreachable pump looks like
    assert sensor.JebaoIpAddressSensor(coordinator).available
    assert sensor.JebaoMacAddressSensor(coordinator).available


def test_mac_sensor_formats_and_handles_a_missing_mac():
    schema = load_by_product_key(ALL_KEYS[0])
    coordinator = FakeCoordinator(schema)
    coordinator.mac = "24ec4aeea4d4"
    assert sensor.JebaoMacAddressSensor(coordinator).native_value == "24:ec:4a:ee:a4:d4"
    # Entries predating MAC capture, before the backfill has run.
    coordinator.mac = None
    assert sensor.JebaoMacAddressSensor(coordinator).native_value is None


# --- device naming (SPEC.md Phase 23) ------------------------------------
# Device name and model were both schema.name_en, so HA's device list read
# "Local Wavemaker (WiFi+BLE)" with the identical string as its subtitle,
# and two identical pumps differed only by HA appending "2".


def test_device_name_is_not_just_the_model_repeated():
    from custom_components.jebao_local.entity import default_device_name

    model = "Local Wavemaker (WiFi+BLE)"
    name = default_device_name(model, "24ec4aeea4d4", "somedid")
    assert name != model
    assert name == "Local Wavemaker a4d4"


def test_identical_products_get_distinguishable_names():
    from custom_components.jebao_local.entity import default_device_name

    model = "Local Wavemaker (WiFi+BLE)"
    a = default_device_name(model, "24ec4aeea4d4", "did-a")
    b = default_device_name(model, "24ec4aee2b7c", "did-b")
    assert a != b, "two identical pumps would rely on HA appending '2'"


def test_name_falls_back_to_did_when_mac_not_yet_backfilled():
    from custom_components.jebao_local.entity import default_device_name

    # Entries predating MAC capture, before __init__.py's backfill runs.
    name = default_device_name("Local Wavemaker (WiFi+BLE)", None, "qp50gpt5i8h4mfkio0enik")
    assert name == "Local Wavemaker enik"


def test_only_a_trailing_parenthetical_is_stripped():
    from custom_components.jebao_local.entity import default_device_name

    # "Pro" and similar must survive - it's part of the product identity.
    assert default_device_name("Local Wavemaker Pro (WiFi+BLE)", "aabbccdd33f5", "d") == (
        "Local Wavemaker Pro 33f5"
    )
    # A product with no parenthetical at all is left alone.
    assert default_device_name("Feeder", "aabbccdd1111", "d") == "Feeder 1111"


def test_every_bundled_product_gets_a_name_distinct_from_its_model():
    from custom_components.jebao_local.entity import default_device_name

    for product_key in ALL_KEYS:
        schema = load_by_product_key(product_key)
        name = default_device_name(schema.name_en, "24ec4aeea4d4", "somedid")
        assert name and name != schema.name_en, schema.name_en
