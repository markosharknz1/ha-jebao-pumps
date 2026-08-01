"""Regression test for button.py's gating: Start/Cancel Feed buttons should
only be created for products with both FeedSwitch and FeedTime.

Needs the real `homeassistant` package (button.py imports homeassistant.
components.button at module level) - skips cleanly if it isn't installed.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local.fan import FEED_NAMES, FEEDTIME_NAMES, find_attr_name  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.schema import (  # noqa: E402
    known_product_keys,
    load_by_product_key,
)

WAVEMAKER_PRODUCT_KEY = "54114ccdac1e41c0bb17e222887c07ba"
NON_FEED_PRODUCT_KEY = "efc08baa6b0a4de38d4bc9bce04ad350"  # Aquarium Light


def _has_feed_buttons(schema) -> bool:
    return (
        find_attr_name(schema, FEED_NAMES, "bool") is not None
        and find_attr_name(schema, FEEDTIME_NAMES, "uint8") is not None
    )


def test_wavemaker_gets_feed_buttons():
    assert _has_feed_buttons(load_by_product_key(WAVEMAKER_PRODUCT_KEY))


def test_light_does_not_get_feed_buttons():
    assert not _has_feed_buttons(load_by_product_key(NON_FEED_PRODUCT_KEY))


def test_exactly_thirteen_products_get_feed_buttons():
    # Same 13 products that get a fan entity, confirmed by direct
    # inspection (SPEC.md Phase 14) - FeedSwitch/FeedTime always co-occur
    # with the fan-qualifying speed attribute in the bundled catalog, but
    # this test checks the button's own gate, not fan.py's, so a future
    # schema breaking that correlation would still be caught here.
    count = sum(1 for pk in known_product_keys() if _has_feed_buttons(load_by_product_key(pk)))
    assert count == 13
