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

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.dt import utcnow

from .const import DOMAIN
from .coordinator import JebaoLocalCoordinator
from .entity import JebaoLocalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: JebaoLocalCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [JebaoConnectivityBinarySensor(coordinator)]
    entities += [
        JebaoFaultBinarySensor(coordinator, attr.name)
        for attr in coordinator.schema.attrs
        if attr.is_problem and attr.data_type == "bool"
    ]
    async_add_entities(entities)


class JebaoConnectivityBinarySensor(JebaoLocalEntity, BinarySensorEntity):
    """Dropout sensor: on = the pump is reachable, off = it has dropped out.

    Unlike the other entities this one is ALWAYS available - that's the point:
    it has to be able to report "off" when polling is failing. Pairs with the
    jebao_local_dropout / _reconnected events and the dropout notification."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Connectivity"

    def __init__(self, coordinator: JebaoLocalCoordinator) -> None:
        super().__init__(coordinator, "connectivity")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tracker = self.coordinator.dropouts
        last_contact = tracker.last_contact
        downtime = None
        if not self.coordinator.last_update_success and tracker.offline_since is not None:
            downtime = int((utcnow() - tracker.offline_since).total_seconds())
        return {
            "last_contact": last_contact.isoformat() if last_contact else None,
            "downtime_seconds": downtime,
        }


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
