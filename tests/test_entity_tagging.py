"""Every entity publishes which pump it is on and what it does.

The Lovelace card used to work out both of those by parsing the entity_id.
Renaming a device in Home Assistant rewrites every one of its entity ids, so
a tidied-up install showed an empty card. The registries hold the same facts,
but `hass.devices` is not guaranteed to be populated in every frontend
context - so the integration now simply says it, as plain state attributes
that are there for every user in every context (entity.py's jebao_tag).

If these attributes ever stop being published, the card silently falls back
to the registry and then to id-matching, i.e. silently back to the bug - so
they are tested here rather than left to the card to discover.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local import binary_sensor, sensor  # noqa: E402
from custom_components.jebao_local.dropouts import DropoutTracker  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.schema import (  # noqa: E402
    known_product_keys,
    load_by_product_key,
)

from test_entity_construction import FakeCoordinator, build_entities  # noqa: E402

ALL_KEYS = sorted(known_product_keys())
DID = "testdid123"


@pytest.mark.parametrize("product_key", ALL_KEYS)
def test_every_entity_tags_its_pump_and_its_role(product_key):
    schema = load_by_product_key(product_key)
    roles = []
    for entity in build_entities(schema):
        tag = entity.jebao_tag
        assert tag["jebao_did"] == DID
        role = tag["jebao_role"]
        # The role is the entity's own unique_id suffix - the same key the
        # card has always used to find, say, this pump's fan or schedule.
        assert role == entity.unique_id[len(DID) + 1 :].lower()
        assert role
        roles.append(role)
    assert len(roles) == len(set(roles)), (
        f"{schema.name_en}: two entities share a role, so the card would only "
        f"see one of them: {sorted(r for r in roles if roles.count(r) > 1)}"
    )


class _TrackingCoordinator(FakeCoordinator):
    """FakeCoordinator plus the dropout state the diagnostic entities read."""

    def __init__(self, schema):
        super().__init__(schema)
        self.dropouts = DropoutTracker()


def _wavemaker():
    for product_key in ALL_KEYS:
        schema = load_by_product_key(product_key)
        if "Wavemaker" in (schema.name_en or ""):
            return schema
    return load_by_product_key(ALL_KEYS[0])


@pytest.mark.parametrize(
    "factory",
    [
        lambda c: sensor.JebaoScheduleSensor(c),
        lambda c: sensor.JebaoDropoutsSensor(c),
        lambda c: binary_sensor.JebaoConnectivityBinarySensor(c),
    ],
    ids=["schedule", "dropouts", "connectivity"],
)
def test_entities_with_their_own_attributes_still_carry_the_tag(factory):
    """These three override extra_state_attributes. An override replaces the
    base property outright, so each has to merge the tag back in - exactly
    the kind of thing that gets forgotten when a fourth one is added."""
    entity = factory(_TrackingCoordinator(_wavemaker()))
    attributes = entity.extra_state_attributes
    assert attributes.get("jebao_did") == DID
    assert attributes.get("jebao_role") == entity.jebao_tag["jebao_role"]
    # ...and did not lose its own attributes in the merge.
    assert len(attributes) > 2


@pytest.mark.parametrize("product_key", ALL_KEYS)
def test_tags_survive_a_device_rename(product_key):
    """The tag must not be derived from anything a rename touches: not the
    entity_id, not the suggested object id, not the device name."""
    schema = load_by_product_key(product_key)
    for entity in build_entities(schema):
        before = dict(entity.jebao_tag)
        entity.entity_id = "sensor.renamed_by_the_user"
        entity._attr_suggested_object_id = "renamed_by_the_user"  # noqa: SLF001
        assert entity.jebao_tag == before
