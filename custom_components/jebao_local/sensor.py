"""Two sensors, both borrowed from jrigling/homeassistant-jebao's precedent
(SPEC.md Phase 14):

- Speed: mirrors the fan entity's percentage as a proper SensorEntity with
  state_class=MEASUREMENT. A fan's percentage attribute alone doesn't get
  HA's long-term statistics/history graphing - a real sensor does. Only
  created for products that got a fan entity in the first place (fan.py).
- State: a synthesized, human-readable "what is this pump doing right now"
  summary (Off / Feeding / Fault: X / Running (mode)), combining several
  raw datapoints into one glanceable value for dashboards and automations
  that would otherwise need to check several entities at once. Degrades
  gracefully for products missing some of those datapoints - only power
  is required for it to report anything at all.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import ranged_value_to_percentage

from .const import DOMAIN
from .coordinator import JebaoLocalCoordinator
from .entity import JebaoLocalEntity
from .fan import FEED_NAMES, MODE_NAMES, SWITCH_NAMES as POWER_NAMES, fan_attr_names, find_attr_name as _find_attr_name


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: JebaoLocalCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[JebaoLocalEntity] = [JebaoStateSensor(coordinator)]
    if fan_attr_names(coordinator.schema):
        entities.append(JebaoSpeedSensor(coordinator))
    async_add_entities(entities)


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
        self._fault_attrs = [a.name for a in schema.attrs if a.is_fault and a.data_type == "bool"]

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
                    return f"Running ({mode_val})"
            return "On"

        return None
