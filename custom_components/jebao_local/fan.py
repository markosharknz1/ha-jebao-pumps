"""On/off + a single speed attribute -> a fan entity, instead of a separate
switch + number. Matches jrigling/homeassistant-jebao's precedent for a
different Jebao pump model (MDP-20000) - HA's fan card gives a proper
speed-slider UI and, unlike a plain number entity, fan devices are exposed
to Google Assistant's smart-home API with real speed control.

Applies to any bundled product with an on/off attribute alongside exactly
one clear "this is the speed" uint8 attribute - 13 of the 29 bundled
products qualify (see fan_attr_names' SPEED_NAMES for the pumps' physical
meaning of each candidate name). The other uint8 attributes present on
those same products (Frequency - how often the pump pulses, not how fast;
AutoFlow/AutoFreq - the scheduled-mode counterparts; nightflow - a
timed night-time reduction) are deliberately left as ordinary number
entities in number.py - only the one attribute a user would think of as
"the pump's speed" moves here.
"""
from __future__ import annotations

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import percentage_to_ranged_value, ranged_value_to_percentage

from .const import DOMAIN
from .coordinator import JebaoLocalCoordinator
from .entity import JebaoLocalEntity
from .jebao_gizwits.schema import Attr, DatapointSchema

# Case-insensitive candidate names for the pump's power and speed attributes,
# gathered from scanning all 29 bundled schemas' actual attribute names (see
# docs/product_catalog.json / SPEC.md Phase 9). Order matters for SPEED_NAMES:
# "flow" is preferred where a product has it (the wavemaker family calls its
# speed "Flow", distinct from "Frequency" which is a pulse-rate, not a speed).
SWITCH_NAMES = ("switchon", "switch")
SPEED_NAMES = ("flow", "motor_speed")


def fan_attr_names(schema: DatapointSchema) -> tuple[str, str] | None:
    """Return (switch_attr_name, speed_attr_name) if this product should get
    a fan entity, else None. Names are returned with their original casing
    (schema.by_name is case-sensitive) even though the match itself isn't."""
    by_lower: dict[str, str] = {}
    for a in schema.attrs:
        if a.writable:
            by_lower.setdefault(a.name.lower(), a.name)

    switch_name = next((by_lower[n] for n in SWITCH_NAMES if n in by_lower), None)
    if switch_name is None:
        return None

    for n in SPEED_NAMES:
        if n not in by_lower:
            continue
        attr = schema.by_name(by_lower[n])
        if attr.data_type == "uint8" and attr.uint_spec is not None:
            return switch_name, by_lower[n]
    return None


# Shared with sensor.py/button.py, which need the same "find an attribute by
# candidate name, case-insensitively" logic for mode/feed-mode detection
# that fan_attr_names above uses for power/speed.
MODE_NAMES = ("mode",)
FEED_NAMES = ("feedswitch",)
FEEDTIME_NAMES = ("feedtime",)


def find_attr_name(schema: DatapointSchema, names: tuple[str, ...], data_type: str) -> str | None:
    by_lower: dict[str, str] = {}
    for a in schema.attrs:
        if a.writable and a.data_type == data_type:
            by_lower.setdefault(a.name.lower(), a.name)
    return next((by_lower[n] for n in names if n in by_lower), None)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: JebaoLocalCoordinator = hass.data[DOMAIN][entry.entry_id]
    attrs = fan_attr_names(coordinator.schema)
    if attrs is None:
        return
    async_add_entities([JebaoFan(coordinator, *attrs)])


class JebaoFan(JebaoLocalEntity, FanEntity):
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: JebaoLocalCoordinator, switch_attr_name: str, speed_attr_name: str) -> None:
        super().__init__(coordinator, "fan")
        self._attr_translation_key = "fan"
        self.switch_attr_name = switch_attr_name
        self.speed_attr_name = speed_attr_name
        speed_attr: Attr = coordinator.schema.by_name(speed_attr_name)
        us = speed_attr.uint_spec
        self._speed_range = (us.min, us.max)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get(self.switch_attr_name))

    @property
    def percentage(self) -> int | None:
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.speed_attr_name)
        if not isinstance(value, (int, float)):
            return None
        return ranged_value_to_percentage(self._speed_range, value)

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None, **kwargs
    ) -> None:
        changes: dict[str, object] = {self.switch_attr_name: True}
        if percentage is not None and percentage > 0:
            changes[self.speed_attr_name] = round(percentage_to_ranged_value(self._speed_range, percentage))
        await self.coordinator.async_write(changes)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write({self.switch_attr_name: False})

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage == 0:
            await self.async_turn_off()
            return
        speed = round(percentage_to_ranged_value(self._speed_range, percentage))
        await self.coordinator.async_write({self.speed_attr_name: speed})
