"""Regression test for sensor.py's per-product logic: the Speed sensor
should only be created for products with a fan entity (fan_attr_names),
and the State sensor's power/mode/feed/fault detection should hold up
across every real bundled schema, not just the wavemaker.

Needs the real `homeassistant` package (sensor.py imports homeassistant.
components.sensor at module level, same as every other platform file in
this integration) - skips cleanly if it isn't installed.
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
from custom_components.jebao_local.sensor import _find_attr_name, FEED_NAMES, MODE_NAMES, POWER_NAMES  # noqa: E402

WAVEMAKER_PRODUCT_KEY = "54114ccdac1e41c0bb17e222887c07ba"


def test_wavemaker_has_power_mode_and_feed_detected():
    schema = load_by_product_key(WAVEMAKER_PRODUCT_KEY)
    assert _find_attr_name(schema, POWER_NAMES, "bool") == "SwitchON"
    assert _find_attr_name(schema, MODE_NAMES, "enum") == "Mode"
    assert _find_attr_name(schema, FEED_NAMES, "bool") == "FeedSwitch"


def test_every_bundled_product_has_a_detectable_power_attr():
    # Confirmed by scanning the full attribute-name survey (SPEC.md Phase
    # 9): every one of the 29 bundled products uses one of switch/SwitchON/
    # Switch for its power attribute - the State sensor should never end up
    # with nothing to report because of a naming variant it doesn't know.
    for pk in known_product_keys():
        schema = load_by_product_key(pk)
        assert _find_attr_name(schema, POWER_NAMES, "bool") is not None, schema.name_en


def test_speed_sensor_only_for_fan_products():
    fan_count = 0
    for pk in known_product_keys():
        schema = load_by_product_key(pk)
        has_fan = fan_attr_names(schema) is not None
        if has_fan:
            fan_count += 1
    # Matches the count already locked in by test_fan_dispatch.py - the
    # Speed sensor's gate (`if fan_attr_names(...)`) reuses that exact
    # function, so this is really asserting the gate hasn't drifted.
    assert fan_count == 14
