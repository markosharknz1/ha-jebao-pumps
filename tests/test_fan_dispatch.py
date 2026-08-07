"""Regression test for fan.py's fan_attr_names() - which bundled products get
a fan entity (on/off + a single speed) instead of separate switch/number
entities, and that the percentage math round-trips correctly against the
device's actual reported value range.

Needs the real `homeassistant` package (fan.py imports homeassistant.
components.fan at module level, same as every other platform file in this
integration) - skips cleanly if it isn't installed, same idea as
test_ha_integration_compat.py but not tied to that file's unrelated
AIPAI-checkout gate.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local.fan import fan_attr_names  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.schema import (  # noqa: E402
    known_product_keys,
    load_by_product_key,
)

# This project's own wavemaker - the pump this whole library was built
# against. Confirmed live: Flow is the physical speed, Frequency is a
# separate pulse-rate control (not absorbed into the fan), per the user's
# own description of the hardware - see SPEC.md Phase 9.
WAVEMAKER_PRODUCT_KEY = "54114ccdac1e41c0bb17e222887c07ba"

# A product with no speed-like attribute at all - should not get a fan.
NON_FAN_PRODUCT_KEY = "efc08baa6b0a4de38d4bc9bce04ad350"  # Aquarium Light

# The one bundled product whose speed attribute isn't a plain 0-100 range
# (30-100) - the interesting case for the percentage-remapping math.
LEGACY_WAVEMAKER_PRODUCT_KEY = "f65982cb65da43baa0c722c84dd2740b"


def test_wavemaker_gets_fan_with_flow_not_frequency():
    schema = load_by_product_key(WAVEMAKER_PRODUCT_KEY)
    assert fan_attr_names(schema) == ("SwitchON", "Flow")


def test_light_does_not_get_a_fan():
    schema = load_by_product_key(NON_FAN_PRODUCT_KEY)
    assert fan_attr_names(schema) is None


def test_expected_number_of_bundled_products_get_a_fan():
    # Locks in the count found by scanning every bundled schema (SPEC.md
    # Phase 9) - a change here means either a bundled schema changed or the
    # matching rule did, either of which is worth a second look. Was 13 of
    # 29 until the Local Wavemaker Pro was added in Phase 19.
    count = sum(1 for pk in known_product_keys() if fan_attr_names(load_by_product_key(pk)) is not None)
    assert count == 15


def test_percentage_round_trips_for_a_plain_0_100_range():
    from homeassistant.util.percentage import percentage_to_ranged_value, ranged_value_to_percentage

    schema = load_by_product_key(WAVEMAKER_PRODUCT_KEY)
    _, speed_attr = fan_attr_names(schema)
    us = schema.by_name(speed_attr).uint_spec
    rng = (us.min, us.max)
    assert rng == (0, 100)
    assert ranged_value_to_percentage(rng, 60) == 60
    assert round(percentage_to_ranged_value(rng, 60)) == 60


def test_percentage_handles_a_nonzero_minimum_device_range():
    from homeassistant.util.percentage import percentage_to_ranged_value, ranged_value_to_percentage

    schema = load_by_product_key(LEGACY_WAVEMAKER_PRODUCT_KEY)
    _, speed_attr = fan_attr_names(schema)
    us = schema.by_name(speed_attr).uint_spec
    rng = (us.min, us.max)
    assert rng == (30, 100)
    # The device's lowest speed is still "on", so it must map to a nonzero
    # percentage - 0% is reserved for off in HA's fan model.
    assert ranged_value_to_percentage(rng, 30) > 0
    assert ranged_value_to_percentage(rng, 100) == 100
    assert 30 <= round(percentage_to_ranged_value(rng, 50)) <= 100
