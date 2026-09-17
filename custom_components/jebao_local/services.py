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

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import JebaoLocalCoordinator
from .jebao_gizwits.clock import HMS_ATTR, YMD_ATTR, encode_clock
from .jebao_gizwits.schedule import (
    SLOT_COUNT,
    clear_slot,
    encode_slot,
    is_slot_enabled,
    schedule_slot_len,
    slot_attr_name,
    slot_mode_options,
)
from .jebao_gizwits.mode_options import mode_options_for
from .jebao_gizwits.enum_translations import translate as translate_label
from .jebao_gizwits.program import Step, compile_program, describe, parse_time_of_day
from .off_timer import DATA_OFF_TIMERS, power_attr_of, resolve_deadline
from .fan import MODE_NAMES, find_attr_name

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
ATTR_FEED_TIME = "feed_time"
ATTR_CUST_WAVE_FREQ = "cust_wave_freq"
ATTR_MINUTES = "minutes"
ATTR_UNTIL = "until"
ATTR_STEPS = "steps"
ATTR_START = "start"
ATTR_REPEAT = "repeat"
ATTR_SPEED = "speed"
ATTR_HOURS = "hours"

SERVICE_SET_SCHEDULE_SLOT = "set_schedule_slot"
SERVICE_CLEAR_SCHEDULE_SLOT = "clear_schedule_slot"
SERVICE_SYNC_CLOCK = "sync_clock"
SERVICE_TIMED_OFF = "timed_off"
SERVICE_CANCEL_TIMED_OFF = "cancel_timed_off"
SERVICE_SET_PROGRAM = "set_program"
SERVICE_CLEAR_PROGRAM = "clear_program"

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
        # Only some products' slot layouts have these - ignored for the
        # ones that don't (see jebao_gizwits/schedule.py FIELDS_BY_LEN).
        vol.Optional(ATTR_PULSE_TIDE, default=0): _BYTE_SCHEMA,
        vol.Optional(ATTR_FEED_TIME, default=0): _BYTE_SCHEMA,
        vol.Optional(ATTR_CUST_WAVE_FREQ, default=0): _BYTE_SCHEMA,
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

# device_id is optional here, unlike the schedule services: "everything
# off while I feed" is the common case and naming each pump for it would
# be tedious and easy to get wrong.
TIMED_OFF_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Exclusive(ATTR_MINUTES, "duration"): vol.All(int, vol.Range(min=1, max=1440)),
        vol.Exclusive(ATTR_UNTIL, "duration"): cv.string,
    }
)

CANCEL_TIMED_OFF_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
    }
)

# A step is a duration plus a speed. `hours` is a float so "1.5" works;
# `minutes` is there for anyone who'd rather not write fractions.
STEP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SPEED): _BYTE_SCHEMA,
        vol.Exclusive(ATTR_HOURS, "length"): vol.All(
            vol.Coerce(float), vol.Range(min=0.0166, max=24)
        ),
        vol.Exclusive(ATTR_MINUTES, "length"): vol.All(int, vol.Range(min=1, max=1440)),
        # Either the product's own mode number or its name ("constant
        # flow") - numbering differs between products, names don't.
        vol.Optional(ATTR_MODE): vol.Any(int, cv.string),
        vol.Optional(ATTR_FREQUENCY): _BYTE_SCHEMA,
    }
)

SET_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_STEPS): vol.All(cv.ensure_list, [STEP_SCHEMA], vol.Length(min=1)),
        vol.Optional(ATTR_START, default="00:00"): cv.string,
        vol.Optional(ATTR_REPEAT, default=False): cv.boolean,
    }
)

CLEAR_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
    }
)

# Slots are written in batches rather than one command per slot (48 round
# trips) or all at once (a ~400-byte control payload, which is the size of
# the device's entire status frame and not something to assume it accepts).
_SLOT_WRITE_BATCH = 6

_LOGGER = logging.getLogger(__name__)

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


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[JebaoLocalCoordinator]:
    """Coordinators this call applies to - the named devices, or all of
    them when device_id is omitted."""
    device_ids = call.data.get(ATTR_DEVICE_ID)
    if not device_ids:
        return [c for c in hass.data.get(DOMAIN, {}).values() if hasattr(c, "did")]
    return [_resolve_coordinator(hass, d) for d in device_ids]


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


# Slot modes that are a poor automatic choice for "just keep doing what
# you're doing" - a program shouldn't silently stop or feed.
_NOT_A_DEFAULT = {"stop", "feeding", "feed mode"}


def _live_mode_label(coordinator: JebaoLocalCoordinator) -> str | None:
    """What the pump's current mode is *called*, or None.

    Deliberately the label and not the number: the live Mode attribute and
    a schedule slot's mode byte are numbered differently on several
    products (see schedule.slot_mode_options), so the only safe way to
    carry "whatever it's doing now" into a slot is by name.
    """
    attr = find_attr_name(coordinator.schema, MODE_NAMES, "enum") or find_attr_name(
        coordinator.schema, MODE_NAMES, "uint8"
    )
    if attr is None:
        return None
    value = (coordinator.data or {}).get(attr)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        definition = coordinator.schema.by_name(attr)
    except KeyError:
        return None

    # Two ways a product publishes its mode names: an `enum` list (labels
    # positional, still in Chinese) or a uint8 whose desc enumerates them
    # (mode_options_for already translates those). Normalise to English so
    # the comparison against the slot table is like for like.
    enum = getattr(definition, "enum_values", None)
    if enum:
        index = int(value)
        return translate_label(enum[index]) if 0 <= index < len(enum) else None
    return mode_options_for(definition).get(int(value))


def _default_slot_mode(coordinator: JebaoLocalCoordinator) -> int:
    """The mode to use for steps that don't name one."""
    table = slot_mode_options(coordinator.schema)
    if not table:
        raise ServiceValidationError(
            f"{coordinator.schema.name_en} doesn't publish a list of schedule modes - "
            "give each step an explicit mode number"
        )

    live = _live_mode_label(coordinator)
    if live is not None:
        for number, label in table.items():
            if translate_label(label).casefold() == live.casefold():
                return number

    # No live mode to copy (several products don't expose one). Fall back
    # only when the choice is unambiguous - on the 6-byte pumps the table
    # is just stop/auto/feed, so "auto" is the only sensible answer.
    candidates = [n for n, l in table.items() if translate_label(l).casefold() not in _NOT_A_DEFAULT]
    if len(candidates) == 1:
        return candidates[0]
    known = ", ".join(translate_label(table[n]) for n in sorted(table))
    raise ServiceValidationError(
        f"can't tell which mode {coordinator.schema.name_en} should run in - "
        f"give each step a mode (it has: {known})"
    )


def _resolve_mode(coordinator: JebaoLocalCoordinator, value, step_no: int) -> int:
    """A step's `mode`, given either as a number or as a name.

    Resolved against the slot's own Byte4 table, which is not the same as
    the live Mode attribute's numbering on every product.
    """
    if isinstance(value, bool):
        raise ServiceValidationError(f"step {step_no}: mode must be a number or a name")
    if isinstance(value, int):
        return value

    table = slot_mode_options(coordinator.schema)
    wanted = str(value).strip().casefold()
    for number, label in table.items():
        if wanted in (label.casefold(), translate_label(label).casefold()):
            return number
    known = ", ".join(translate_label(table[n]) for n in sorted(table)) or "none published"
    raise ServiceValidationError(
        f"step {step_no}: {coordinator.schema.name_en} has no mode called {value!r} "
        f"(it has: {known})"
    )


def _timer_switch_attr(coordinator: JebaoLocalCoordinator) -> str | None:
    """The master switch that lets the schedule actually run."""
    return find_attr_name(coordinator.schema, ("timeron", "timer_on", "timer"), "bool")


async def _write_slots(coordinator: JebaoLocalCoordinator, slots: dict[str, bytes]) -> None:
    items = list(slots.items())
    for start in range(0, len(items), _SLOT_WRITE_BATCH):
        await coordinator.async_write(dict(items[start : start + _SLOT_WRITE_BATCH]))


def async_register_services(hass: HomeAssistant) -> None:
    if hass.data.get(_SERVICES_REGISTERED_KEY):
        return

    async def _handle_set_schedule_slot(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call.data[ATTR_DEVICE_ID])
        attr_name = _require_schedule_support(coordinator, call.data[ATTR_SLOT])
        # Slot length and field layout are per-product (an 8-byte base
        # wavemaker slot and a 9-byte Pro slot don't even hold the same
        # fields) - take it from this device's own schema.
        slot_len = schedule_slot_len(coordinator.schema)
        try:
            raw = encode_slot(
                start_hour=call.data[ATTR_START_HOUR],
                start_minute=call.data[ATTR_START_MINUTE],
                end_hour=call.data[ATTR_END_HOUR],
                end_minute=call.data[ATTR_END_MINUTE],
                mode=call.data[ATTR_MODE],
                flow=call.data[ATTR_FLOW],
                frequency=call.data[ATTR_FREQUENCY],
                pulse_tide=call.data[ATTR_PULSE_TIDE],
                feed_time=call.data[ATTR_FEED_TIME],
                cust_wave_freq=call.data[ATTR_CUST_WAVE_FREQ],
                slot_len=slot_len,
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        await coordinator.async_write({attr_name: raw})

    async def _handle_clear_schedule_slot(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call.data[ATTR_DEVICE_ID])
        attr_name = _require_schedule_support(coordinator, call.data[ATTR_SLOT])
        await coordinator.async_write(
            {attr_name: clear_slot(schedule_slot_len(coordinator.schema))}
        )

    async def _handle_sync_clock(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call.data[ATTR_DEVICE_ID])
        _require_clock_support(coordinator)
        # Local wall-clock time, not UTC - the pump has no timezone concept
        # and its schedule slots are wall-clock times; the vendor app's own
        # sendLocalTime syncs local time too (see jebao_gizwits/clock.py).
        now = dt_util.now()
        await coordinator.async_write(encode_clock(now))

    async def _handle_set_program(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call.data[ATTR_DEVICE_ID])
        _require_schedule_support(coordinator, 0)
        slot_len = schedule_slot_len(coordinator.schema)

        # Only worked out if some step actually needs it - a program that
        # names a mode on every step must not fail because the device's
        # own current mode is ambiguous.
        live_mode = (
            _default_slot_mode(coordinator)
            if any(ATTR_MODE not in s for s in call.data[ATTR_STEPS])
            else 0
        )
        steps = []
        for step_no, raw_step in enumerate(call.data[ATTR_STEPS], start=1):
            if ATTR_HOURS in raw_step:
                minutes = max(1, round(raw_step[ATTR_HOURS] * 60))
            elif ATTR_MINUTES in raw_step:
                minutes = raw_step[ATTR_MINUTES]
            else:
                raise ServiceValidationError(
                    f"step {step_no} needs a length - give it 'hours' or 'minutes'"
                )
            mode = raw_step.get(ATTR_MODE)
            steps.append(
                Step(
                    minutes=minutes,
                    speed=raw_step[ATTR_SPEED],
                    mode=None if mode is None else _resolve_mode(coordinator, mode, step_no),
                    frequency=raw_step.get(ATTR_FREQUENCY),
                )
            )

        try:
            compiled = compile_program(
                steps,
                start=parse_time_of_day(call.data[ATTR_START]),
                repeat=call.data[ATTR_REPEAT],
                default_mode=live_mode,
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        writes: dict[str, bytes] = {}
        for index, slot in enumerate(compiled):
            try:
                writes[slot_attr_name(index)] = encode_slot(**slot.as_kwargs(), slot_len=slot_len)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err

        # Anything left over from a previous program has to go, or the old
        # periods keep firing alongside the new ones. Only slots that
        # currently hold something are cleared, to save round trips.
        blank = clear_slot(slot_len)
        data = coordinator.data or {}
        for index in range(len(compiled), SLOT_COUNT):
            name = slot_attr_name(index)
            if is_slot_enabled(bytes(data.get(name) or b"")):
                writes[name] = blank

        await _write_slots(coordinator, writes)

        # The schedule does nothing unless the pump's timer is switched
        # on - programming one and leaving it disabled looks like a
        # failure to anyone watching the tank.
        timer_attr = _timer_switch_attr(coordinator)
        if timer_attr is not None and not data.get(timer_attr, False):
            await coordinator.async_write({timer_attr: True})

        _LOGGER.info(
            "Programmed %s with %s period(s): %s",
            coordinator.schema.name_en, len(compiled), "; ".join(describe(compiled)),
        )

    async def _handle_clear_program(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call.data[ATTR_DEVICE_ID])
        _require_schedule_support(coordinator, 0)
        blank = clear_slot(schedule_slot_len(coordinator.schema))
        data = coordinator.data or {}
        writes = {
            slot_attr_name(i): blank
            for i in range(SLOT_COUNT)
            if is_slot_enabled(bytes(data.get(slot_attr_name(i)) or b""))
        }
        if writes:
            await _write_slots(coordinator, writes)

    async def _handle_timed_off(call: ServiceCall) -> None:
        manager = hass.data[DATA_OFF_TIMERS]
        try:
            deadline = resolve_deadline(call.data.get(ATTR_MINUTES), call.data.get(ATTR_UNTIL))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        targets = _targets(hass, call)
        skipped = [c.schema.name_en for c in targets if power_attr_of(c) is None]
        usable = [c for c in targets if power_attr_of(c) is not None]
        if not usable:
            raise ServiceValidationError(
                "None of the selected devices have a power control that can be timed"
                + (f" (skipped: {', '.join(skipped)})" if skipped else "")
            )
        for coordinator in usable:
            await manager.async_start(coordinator, deadline)

    async def _handle_cancel_timed_off(call: ServiceCall) -> None:
        manager = hass.data[DATA_OFF_TIMERS]
        for coordinator in _targets(hass, call):
            await manager.async_cancel(coordinator)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_PROGRAM, _handle_set_program, schema=SET_PROGRAM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_PROGRAM, _handle_clear_program, schema=CLEAR_PROGRAM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_TIMED_OFF, _handle_timed_off, schema=TIMED_OFF_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_TIMED_OFF, _handle_cancel_timed_off, schema=CANCEL_TIMED_OFF_SCHEMA
    )
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
