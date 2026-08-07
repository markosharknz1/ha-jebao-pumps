"""Three sensors:

- Speed and State are both borrowed from jrigling/homeassistant-jebao's
  precedent (SPEC.md Phase 14). Speed mirrors the fan entity's percentage as
  a proper SensorEntity with state_class=MEASUREMENT - a fan's percentage
  attribute alone doesn't get HA's long-term statistics/history graphing, a
  real sensor does. Only created for products that got a fan entity in the
  first place (fan.py). State is a synthesized, human-readable "what is this
  pump doing right now" summary (Off / Feeding / Fault: X / Running (mode)),
  combining several raw datapoints into one glanceable value for dashboards
  and automations that would otherwise need to check several entities at
  once. Degrades gracefully for products missing some of those datapoints -
  only power is required for it to report anything at all.
- Schedule is a read-back of the 48-slot AutoTimeNN schedule (see
  jebao_gizwits/schedule.py) - one entity, not 48+, since a slot isn't
  naturally its own entity and this project's set_schedule_slot/
  clear_schedule_slot services (services.py) already cover writing.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import ranged_value_to_percentage

from .const import DOMAIN
from .coordinator import JebaoLocalCoordinator
from .entity import JebaoLocalEntity
from .fan import FEED_NAMES, MODE_NAMES, SWITCH_NAMES as POWER_NAMES, fan_attr_names, find_attr_name as _find_attr_name
from .jebao_gizwits.clock import HMS_ATTR, YMD_ATTR, decode_clock
from .jebao_gizwits.enum_translations import translate as _translate_enum
from .jebao_gizwits.schedule import (
    SLOT_COUNT,
    ScheduleSlot,
    decode_slot,
    schedule_slot_len,
    slot_attr_name,
)

_SCHEDULE_PROBE_ATTR = slot_attr_name(0)  # AutoTime00 - presence implies all 48 slots exist


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: JebaoLocalCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[JebaoLocalEntity] = [JebaoStateSensor(coordinator)]
    if fan_attr_names(coordinator.schema):
        entities.append(JebaoSpeedSensor(coordinator))
    if _has_schedule(coordinator.schema):
        entities.append(JebaoScheduleSensor(coordinator))
    if _has_attrs(coordinator.schema, (YMD_ATTR, HMS_ATTR)):
        entities.append(JebaoDeviceClockSensor(coordinator))
    # status_readonly attrs match no other platform's filter (they're
    # neither writable nor faults), so without this they'd be read from the
    # device every poll and then silently dropped.
    entities.extend(
        JebaoReadonlySensor(coordinator, attr.name)
        for attr in coordinator.schema.attrs
        if attr.is_readonly_status and attr.data_type == "uint8"
    )
    async_add_entities(entities)


def _has_attrs(schema, names: tuple[str, ...]) -> bool:
    try:
        for name in names:
            schema.by_name(name)
        return True
    except KeyError:
        return False


def _has_schedule(schema) -> bool:
    return _has_attrs(schema, (_SCHEDULE_PROBE_ATTR,))


class JebaoSpeedSensor(JebaoLocalEntity, SensorEntity):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: JebaoLocalCoordinator) -> None:
        super().__init__(coordinator, "speed")
        self._attr_translation_key = "speed"
        _switch_attr, speed_attr = fan_attr_names(coordinator.schema)
        self._speed_attr = speed_attr
        us = coordinator.schema.by_name(speed_attr).uint_spec
        self._range = (us.min, us.max)

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self._speed_attr)
        if not isinstance(value, (int, float)):
            return None
        return ranged_value_to_percentage(self._range, value)


class JebaoStateSensor(JebaoLocalEntity, SensorEntity):
    def __init__(self, coordinator: JebaoLocalCoordinator) -> None:
        super().__init__(coordinator, "state")
        self._attr_translation_key = "state"
        schema = coordinator.schema
        fan_attrs = fan_attr_names(schema)
        self._power_attr = fan_attrs[0] if fan_attrs else _find_attr_name(schema, POWER_NAMES, "bool")
        self._mode_attr = _find_attr_name(schema, MODE_NAMES, "enum")
        self._feed_attr = _find_attr_name(schema, FEED_NAMES, "bool")
        # is_problem, not is_fault - 'alert' attrs are fault conditions too
        # (see binary_sensor.py) and should show up in the State summary.
        self._fault_attrs = [a.name for a in schema.attrs if a.is_problem and a.data_type == "bool"]

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        data = self.coordinator.data

        for fault_name in self._fault_attrs:
            if data.get(fault_name):
                return f"Fault: {fault_name.replace('Fault_', '').replace('_', ' ')}"

        if self._feed_attr and data.get(self._feed_attr):
            return "Feeding"

        if self._power_attr:
            if not data.get(self._power_attr):
                return "Off"
            if self._mode_attr:
                mode_val = data.get(self._mode_attr)
                if isinstance(mode_val, str):
                    return f"Running ({_translate_enum(mode_val)})"
            return "On"

        return None


class JebaoScheduleSensor(JebaoLocalEntity, SensorEntity):
    """State is the number of programmed (enabled) slots; the full decoded
    schedule is exposed as an attribute for automations/dashboards to
    consume via templates, since a state string can't hold a list."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: JebaoLocalCoordinator) -> None:
        super().__init__(coordinator, "schedule")
        self._attr_translation_key = "schedule"

    def _decoded_slots(self) -> list[ScheduleSlot]:
        if self.coordinator.data is None:
            return []
        slots = []
        for index in range(SLOT_COUNT):
            raw = self.coordinator.data.get(slot_attr_name(index))
            if not isinstance(raw, (bytes, bytearray)):
                continue
            try:
                slot = decode_slot(index, raw)
            except ValueError:
                # A product whose slot layout isn't decoded yet - report no
                # periods rather than breaking the whole sensor.
                return []
            if slot is not None:
                slots.append(slot)
        return slots

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self._decoded_slots())

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        # slot_len tells the card which per-product slot layout this pump
        # uses (8-byte base wavemaker vs 9-byte Pro - different fields and
        # different mode numbering), without it having to guess from a
        # slot that may not exist yet on a freshly-configured pump.
        return {
            "slot_len": schedule_slot_len(self.coordinator.schema),
            "slots": [slot.as_dict() for slot in self._decoded_slots()],
        }


class JebaoDeviceClockSensor(JebaoLocalEntity, SensorEntity):
    """The pump's own internal clock (YMDData/HMSData), which the 48 timer
    slots fire off. Diagnostic - its point is making clock drift visible
    (and giving the card's schedule editor something to show next to its
    Sync button). Reported as a plain string, not device_class TIMESTAMP:
    a timestamp sensor needs a timezone-aware value, and inventing a
    timezone for a device that has no concept of one would misrepresent
    what the pump actually stores (a wall-clock time)."""

    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: JebaoLocalCoordinator) -> None:
        super().__init__(coordinator, "deviceclock")
        self._attr_translation_key = "deviceclock"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        ymd = self.coordinator.data.get(YMD_ATTR)
        hms = self.coordinator.data.get(HMS_ATTR)
        if not isinstance(ymd, (bytes, bytearray)) or not isinstance(hms, (bytes, bytearray)):
            return None
        clock = decode_clock(ymd, hms)
        when = clock.as_datetime()
        if when is None:
            return "Not set"
        return when.strftime("%Y-%m-%d %H:%M:%S")


class JebaoReadonlySensor(JebaoLocalEntity, SensorEntity):
    """A 'status_readonly' datapoint - readable, not writable, not a fault.

    Only appears on products outside the app's locally-bundled set (SPEC.md
    Phase 20), where it's always `time1`, a uint8 the device reports and
    the vendor app displays. Diagnostic rather than primary state, and
    deliberately not given a device_class/unit: the schema carries no unit
    for it and inventing one (minutes? seconds?) would be a guess dressed
    up as fact.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: JebaoLocalCoordinator, attr_name: str) -> None:
        super().__init__(coordinator, attr_name)
        self._attr_name = attr_name
        self._attr_translation_key = attr_name.lower()
        self.attr_name = attr_name

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.attr_name)
        return value if isinstance(value, (int, float)) else None
