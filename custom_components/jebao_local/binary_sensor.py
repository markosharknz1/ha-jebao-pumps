"""'fault' and 'alert' type attributes -> binary sensors (device_class=PROBLEM).

'alert' only turned up once products beyond the original 29 were bundled
(SPEC.md Phase 20). Every alert attribute in the catalog is a fault
condition by another name - OpenCircuit, OverTemp, OverCurrent, and two
literally called Fault_Fan/Fault_UART - so they get the same treatment as
'fault'. Before this they matched no platform's filter at all (every one
gates on `writable` or `is_fault`) and were silently dropped, i.e. a real
over-temperature flag the device was reporting never reached HA.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import JebaoLocalCoordinator
from .entity import JebaoLocalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: JebaoLocalCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        JebaoFaultBinarySensor(coordinator, attr.name)
        for attr in coordinator.schema.attrs
        if attr.is_problem and attr.data_type == "bool"
    ]
    async_add_entities(entities)


class JebaoFaultBinarySensor(JebaoLocalEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: JebaoLocalCoordinator, attr_name: str) -> None:
        super().__init__(coordinator, attr_name)
        self._attr_name = attr_name
        self._attr_translation_key = attr_name.lower()
        self.attr_name = attr_name

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get(self.attr_name))
