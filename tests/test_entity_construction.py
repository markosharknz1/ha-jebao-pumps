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
