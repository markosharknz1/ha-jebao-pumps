"""Tests for services.py's voluptuous schemas and schedule-support gate -
the parts that don't need a running Home Assistant instance (device
registry resolution is exercised manually against a live install, same as
the rest of this integration's write path - see SPEC.md).

Needs the real `homeassistant` package (services.py imports
homeassistant.core/helpers at module level) - skips cleanly if it isn't
installed, same convention as test_buttons.py/test_fan_dispatch.py.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")
import voluptuous as vol  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local.services import (  # noqa: E402
    CLEAR_SCHEDULE_SLOT_SCHEMA,
    SET_SCHEDULE_SLOT_SCHEMA,
    _require_clock_support,
    _require_schedule_support,
)
from custom_components.jebao_local.jebao_gizwits.schema import load_by_product_key  # noqa: E402

WAVEMAKER_PRODUCT_KEY = "54114ccdac1e41c0bb17e222887c07ba"
NON_SCHEDULE_PRODUCT_KEY = "efc08baa6b0a4de38d4bc9bce04ad350"  # Aquarium Light


class _FakeCoordinator:
    def __init__(self, schema):
        self.schema = schema


def test_set_schedule_slot_schema_accepts_a_valid_call():
    data = SET_SCHEDULE_SLOT_SCHEMA(
        {
            "device_id": "abc123",
            "slot": 0,
            "start_hour": 8,
            "start_minute": 0,
            "end_hour": 20,
            "end_minute": 0,
            "mode": 1,
            "flow": 100,
        }
    )
    assert data["frequency"] == 0  # default fills in
    assert data["pulse_tide"] == 0


@pytest.mark.parametrize("field,value", [("slot", 48), ("slot", -1), ("start_hour", 25), ("flow", 256)])
def test_set_schedule_slot_schema_rejects_out_of_range_values(field, value):
    data = {
        "device_id": "abc123", "slot": 0, "start_hour": 8, "start_minute": 0,
        "end_hour": 20, "end_minute": 0, "mode": 1, "flow": 100,
    }
    data[field] = value
    with pytest.raises(vol.Invalid):
        SET_SCHEDULE_SLOT_SCHEMA(data)


def test_clear_schedule_slot_schema_accepts_a_valid_call():
    data = CLEAR_SCHEDULE_SLOT_SCHEMA({"device_id": "abc123", "slot": 47})
    assert data["slot"] == 47


def test_wavemaker_supports_schedule():
    coordinator = _FakeCoordinator(load_by_product_key(WAVEMAKER_PRODUCT_KEY))
    assert _require_schedule_support(coordinator, 0) == "AutoTime00"
    assert _require_schedule_support(coordinator, 47) == "AutoTime47"


def test_non_schedule_product_is_rejected():
    from homeassistant.exceptions import ServiceValidationError

    coordinator = _FakeCoordinator(load_by_product_key(NON_SCHEDULE_PRODUCT_KEY))
    with pytest.raises(ServiceValidationError):
        _require_schedule_support(coordinator, 0)


def test_wavemaker_supports_clock_sync():
    coordinator = _FakeCoordinator(load_by_product_key(WAVEMAKER_PRODUCT_KEY))
    _require_clock_support(coordinator)  # must not raise


def test_non_clock_product_is_rejected_for_clock_sync():
    from homeassistant.exceptions import ServiceValidationError

    coordinator = _FakeCoordinator(load_by_product_key(NON_SCHEDULE_PRODUCT_KEY))
    with pytest.raises(ServiceValidationError):
        _require_clock_support(coordinator)
