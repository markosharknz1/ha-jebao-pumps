"""Shared base entity - pattern borrowed from jrigling/homeassistant-jebao's
entity.py, which (unlike chrisc123/jebao_aqua-homeassistant) uses a properly
typed DeviceInfo instead of a raw dict, and centralizes it in one base class
instead of repeating device_info/name properties in every platform file."""
from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TCP_PORT
from .coordinator import JebaoLocalCoordinator


class JebaoLocalEntity(CoordinatorEntity[JebaoLocalCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: JebaoLocalCoordinator, unique_id_suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.did}_{unique_id_suffix}"
        # Product names come from the vendor's (often non-ASCII, e.g. Chinese)
        # datapoint schema - HA's slugify() drops characters it can't
        # transliterate, which makes entity_ids built from the device name
        # unpredictable and prone to colliding across same-model pumps.
        # did is a stable, ASCII, per-device identifier, so anchor entity_ids
        # to it directly instead: switch.jebao_<did>_switchon, etc.
        self._attr_suggested_object_id = f"jebao_{coordinator.did}_{unique_id_suffix}".lower()
        # did stays the identifier HA uses internally (unique_id, config
        # entry dedup, entity_id) - it's what's already threaded through
        # the rest of the integration and is guaranteed unique by the
        # Gizwits cloud, unlike MAC addresses which cheap WiFi modules can
        # occasionally duplicate. MAC is only surfaced here, as a
        # `connections` entry, so it shows up on the device page for
        # cross-referencing against a router's client list - `identifiers`
        # (did) is never rendered as visible text by HA's UI, but
        # `connections` is.
        connections = {(CONNECTION_NETWORK_MAC, format_mac(coordinator.mac))} if coordinator.mac else set()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.did)},
            connections=connections,
            name=coordinator.schema.name_en,
            manufacturer="Jebao",
            model=coordinator.schema.name_en,
            configuration_url=f"http://{coordinator.host}:{TCP_PORT}",
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
