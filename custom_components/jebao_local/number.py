"""uint8 status_writable attributes -> numbers (e.g. Flow%, Frequency%)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity
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
        JebaoNumber(coordinator, attr.name)
        for attr in coordinator.schema.attrs
        if attr.writable and attr.data_type == "uint8" and attr.uint_spec is not None
    ]
    async_add_entities(entities)


class JebaoNumber(JebaoLocalEntity, NumberEntity):
    def __init__(self, coordinator: JebaoLocalCoordinator, attr_name: str) -> None:
        super().__init__(coordinator, attr_name)
        self._attr_name = attr_name
        self._attr_translation_key = attr_name.lower()
        self.attr_name = attr_name
        attr = coordinator.schema.by_name(attr_name)
        us = attr.uint_spec
        self._attr_native_min_value = us.min
        self._attr_native_max_value = us.max
        self._attr_native_step = us.ratio or 1

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.attr_name)
        return float(value) if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_write({self.attr_name: value})
