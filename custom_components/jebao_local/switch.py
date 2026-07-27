"""Bool status_writable attributes -> switches. Generic dispatch pattern
borrowed from chrisc123/jebao_aqua-homeassistant (schema drives which
entities get created, not a hardcoded per-model list)."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import JebaoLocalCoordinator
from .entity import JebaoLocalEntity
from .fan import fan_attr_names


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: JebaoLocalCoordinator = hass.data[DOMAIN][entry.entry_id]
    # If this product got a fan entity (see fan.py), its power attribute is
    # controlled there instead of as a standalone switch.
    fan_attrs = fan_attr_names(coordinator.schema)
    fan_switch_name = fan_attrs[0] if fan_attrs else None
    entities = [
        JebaoSwitch(coordinator, attr.name)
        for attr in coordinator.schema.attrs
        if attr.writable and attr.data_type == "bool" and attr.name != fan_switch_name
    ]
    async_add_entities(entities)


class JebaoSwitch(JebaoLocalEntity, SwitchEntity):
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

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_write({self.attr_name: True})

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write({self.attr_name: False})
