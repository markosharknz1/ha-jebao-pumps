"""Shared base entity - pattern borrowed from jrigling/homeassistant-jebao's
entity.py, which (unlike chrisc123/jebao_aqua-homeassistant) uses a properly
typed DeviceInfo instead of a raw dict, and centralizes it in one base class
instead of repeating device_info/name properties in every platform file."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TCP_PORT
from .coordinator import JebaoLocalCoordinator


class JebaoLocalEntity(CoordinatorEntity[JebaoLocalCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: JebaoLocalCoordinator, unique_id_suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.did}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.did)},
            name=coordinator.schema.name,
            manufacturer="Jebao",
            model=coordinator.schema.name,
            configuration_url=f"http://{coordinator.host}:{TCP_PORT}",
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
