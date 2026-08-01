"""Start Feed / Cancel Feed as one-shot buttons, alongside the existing
FeedSwitch switch and FeedTime number entities - pattern borrowed from
jrigling/homeassistant-jebao (SPEC.md Phase 14). A ButtonEntity is the more
semantically correct HA entity type for a momentary trigger than toggling
a switch and expecting something else to turn it back off - useful for
automations regardless of whether a given pump's firmware auto-clears
FeedSwitch on its own (unconfirmed for this project's wavemaker; see the
Control panel/card's own feed-timer logic, kept as-is either way since
these buttons don't replace it, just add a simpler trigger for the common
case of "feed now, for however long FeedTime already says").

Only created for products with both FeedSwitch and FeedTime - same gate
the card and number.py's dispatch already use.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import JebaoLocalCoordinator
from .entity import JebaoLocalEntity
from .fan import FEED_NAMES, FEEDTIME_NAMES, find_attr_name


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: JebaoLocalCoordinator = hass.data[DOMAIN][entry.entry_id]
    schema = coordinator.schema
    feed_attr = find_attr_name(schema, FEED_NAMES, "bool")
    feedtime_attr = find_attr_name(schema, FEEDTIME_NAMES, "uint8")
    if feed_attr is None or feedtime_attr is None:
        return
    async_add_entities([
        JebaoStartFeedButton(coordinator, feed_attr),
        JebaoCancelFeedButton(coordinator, feed_attr),
    ])


class JebaoStartFeedButton(JebaoLocalEntity, ButtonEntity):
    _attr_icon = "mdi:fishbowl"

    def __init__(self, coordinator: JebaoLocalCoordinator, feed_attr: str) -> None:
        super().__init__(coordinator, "startfeed")
        self._attr_translation_key = "startfeed"
        self._feed_attr = feed_attr

    async def async_press(self) -> None:
        await self.coordinator.async_write({self._feed_attr: True})


class JebaoCancelFeedButton(JebaoLocalEntity, ButtonEntity):
    _attr_icon = "mdi:cancel"

    def __init__(self, coordinator: JebaoLocalCoordinator, feed_attr: str) -> None:
        super().__init__(coordinator, "cancelfeed")
        self._attr_translation_key = "cancelfeed"
        self._feed_attr = feed_attr

    async def async_press(self) -> None:
        await self.coordinator.async_write({self._feed_attr: False})
