"""set_schedule_slot / clear_schedule_slot services for the 48-slot
AutoTimeNN schedule (see jebao_gizwits/schedule.py for the confirmed byte
format and jebao_gizwits/control.py for the "binary" write support this
relies on).

A schedule slot isn't naturally an entity (48 slots x up to 8 fields each
would be an unreasonable number of entities for one pump), so this is
exposed as services instead, targeting a device via Home Assistant's
standard `device_id` selector - resolved back to this integration's config
entry and coordinator via the device registry. Registered once per Home
Assistant instance (idempotent, see _SERVICES_REGISTERED_KEY), not per
config entry, since services are domain-wide.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import JebaoLocalCoordinator
from .jebao_gizwits.clock import HMS_ATTR, YMD_ATTR, encode_clock
from .jebao_gizwits.schedule import SLOT_COUNT, clear_slot, encode_slot, slot_attr_name

ATTR_DEVICE_ID = "device_id"
ATTR_SLOT = "slot"
ATTR_START_HOUR = "start_hour"
ATTR_START_MINUTE = "start_minute"
ATTR_END_HOUR = "end_hour"
ATTR_END_MINUTE = "end_minute"
ATTR_MODE = "mode"
ATTR_FLOW = "flow"
ATTR_FREQUENCY = "frequency"
ATTR_PULSE_TIDE = "pulse_tide"

SERVICE_SET_SCHEDULE_SLOT = "set_schedule_slot"
SERVICE_CLEAR_SCHEDULE_SLOT = "clear_schedule_slot"
SERVICE_SYNC_CLOCK = "sync_clock"

_SLOT_SCHEMA = vol.All(int, vol.Range(min=0, max=SLOT_COUNT - 1))
_BYTE_SCHEMA = vol.All(int, vol.Range(min=0, max=255))

SET_SCHEDULE_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SLOT): _SLOT_SCHEMA,
        vol.Required(ATTR_START_HOUR): vol.All(int, vol.Range(min=0, max=24)),
        vol.Required(ATTR_START_MINUTE): vol.All(int, vol.Range(min=0, max=59)),
        vol.Required(ATTR_END_HOUR): vol.All(int, vol.Range(min=0, max=24)),
        vol.Required(ATTR_END_MINUTE): vol.All(int, vol.Range(min=0, max=59)),
        vol.Required(ATTR_MODE): _BYTE_SCHEMA,
        vol.Required(ATTR_FLOW): _BYTE_SCHEMA,
        vol.Optional(ATTR_FREQUENCY, default=0): _BYTE_SCHEMA,
        vol.Optional(ATTR_PULSE_TIDE, default=0): _BYTE_SCHEMA,
    }
)

CLEAR_SCHEDULE_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SLOT): _SLOT_SCHEMA,
    }
)

SYNC_CLOCK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
    }
)

_SERVICES_REGISTERED_KEY = "jebao_local_services_registered"


def _resolve_coordinator(hass: HomeAssistant, device_id: str) -> JebaoLocalCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"No device found for device_id {device_id!r}")
    for entry_id in device.config_entries:
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is not None:
            return coordinator
    raise ServiceValidationError(f"Device {device_id!r} is not a Jebao Local pump")


def _require_schedule_support(coordinator: JebaoLocalCoordinator, slot: int) -> str:
    attr_name = slot_attr_name(slot)
    try:
        coordinator.schema.by_name(attr_name)
    except KeyError:
        raise ServiceValidationError(
            f"{coordinator.schema.name_en} does not support schedule programming"
        ) from None
    return attr_name


def _require_clock_support(coordinator: JebaoLocalCoordinator) -> None:
    try:
        coordinator.schema.by_name(YMD_ATTR)
        coordinator.schema.by_name(HMS_ATTR)
    except KeyError:
        raise ServiceValidationError(
            f"{coordinator.schema.name_en} does not have a settable clock"
        ) from None


def async_register_services(hass: HomeAssistant) -> None:
    if hass.data.get(_SERVICES_REGISTERED_KEY):
        return

    async def _handle_set_schedule_slot(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call.data[ATTR_DEVICE_ID])
        attr_name = _require_schedule_support(coordinator, call.data[ATTR_SLOT])
        raw = encode_slot(
            start_hour=call.data[ATTR_START_HOUR],
            start_minute=call.data[ATTR_START_MINUTE],
            end_hour=call.data[ATTR_END_HOUR],
            end_minute=call.data[ATTR_END_MINUTE],
            mode=call.data[ATTR_MODE],
            flow=call.data[ATTR_FLOW],
            frequency=call.data[ATTR_FREQUENCY],
            pulse_tide=call.data[ATTR_PULSE_TIDE],
        )
        await coordinator.async_write({attr_name: raw})

    async def _handle_clear_schedule_slot(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call.data[ATTR_DEVICE_ID])
        attr_name = _require_schedule_support(coordinator, call.data[ATTR_SLOT])
        await coordinator.async_write({attr_name: clear_slot()})

    async def _handle_sync_clock(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call.data[ATTR_DEVICE_ID])
        _require_clock_support(coordinator)
        # Local wall-clock time, not UTC - the pump has no timezone concept
        # and its schedule slots are wall-clock times; the vendor app's own
        # sendLocalTime syncs local time too (see jebao_gizwits/clock.py).
        now = dt_util.now()
        await coordinator.async_write(encode_clock(now))

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCHEDULE_SLOT, _handle_set_schedule_slot, schema=SET_SCHEDULE_SLOT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_SCHEDULE_SLOT, _handle_clear_schedule_slot, schema=CLEAR_SCHEDULE_SLOT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_CLOCK, _handle_sync_clock, schema=SYNC_CLOCK_SCHEMA
    )
    hass.data[_SERVICES_REGISTERED_KEY] = True
