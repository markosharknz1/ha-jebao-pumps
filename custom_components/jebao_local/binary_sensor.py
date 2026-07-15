"""'fault' type attributes -> binary sensors (device_class=PROBLEM)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
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
        if attr.is_fault and attr.data_type == "bool"
    ]
    async_add_entities(entities)


class JebaoFaultBinarySensor(JebaoLocalEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = "diagnostic"

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
