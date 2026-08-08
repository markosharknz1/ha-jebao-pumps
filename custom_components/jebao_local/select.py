"""Enum status_writable attributes -> selects.

Options are shown in English. This is done here rather than through HA's
own per-entity `state` translations because those require translation
*keys* to be `[a-z0-9-_]+` slugs, and this vendor's enum values are
Chinese - hassfest rejects them outright (SPEC.md Phase 21). So the entity
reports translated options and maps the user's choice back to the raw
value on write; the wire format is unchanged.
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import JebaoLocalCoordinator
from .entity import JebaoLocalEntity
from .jebao_gizwits.enum_translations import translate, untranslate


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: JebaoLocalCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        JebaoSelect(coordinator, attr.name)
        for attr in coordinator.schema.attrs
        if attr.writable and attr.data_type == "enum" and attr.enum_values
    ]
    async_add_entities(entities)


class JebaoSelect(JebaoLocalEntity, SelectEntity):
    def __init__(self, coordinator: JebaoLocalCoordinator, attr_name: str) -> None:
        super().__init__(coordinator, attr_name)
        self._attr_name = attr_name
        self._attr_translation_key = attr_name.lower()
        self.attr_name = attr_name
        attr = coordinator.schema.by_name(attr_name)
        self._attr_options = [translate(v) for v in attr.enum_values]

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.attr_name)
        return translate(value) if isinstance(value, str) else None

    async def async_select_option(self, option: str) -> None:
        # Back to the vendor's own value - control.py encodes an enum write
        # via enum_values.index(), so it must be the raw string.
        await self.coordinator.async_write({self.attr_name: untranslate(option)})
