"""The Jebao Local integration.

Coordinator lifetime pattern: created exactly once here, before platforms
are forwarded, and shared via hass.data. This is chrisc123's correct pattern
(jrigling's repo has each platform lazily create its own coordinator, a real
bug that leads to multiple redundant pollers for the same device)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import JebaoLocalCoordinator
from .panel import async_register_card, async_register_panel
from .services import async_register_services

PLATFORMS = [
    Platform.FAN, Platform.SWITCH, Platform.SELECT, Platform.NUMBER,
    Platform.BINARY_SENSOR, Platform.SENSOR, Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    scan_interval = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
    coordinator = JebaoLocalCoordinator(hass, entry, scan_interval)
    await coordinator.async_load_schema()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Idempotent (checked internally) - safe to call for every config entry,
    # including the second/third pump added to the same HA instance.
    await async_register_card(hass)
    await async_register_panel(hass)
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: JebaoLocalCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_close()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
