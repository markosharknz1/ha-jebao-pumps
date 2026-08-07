"""Config flow - discovery-first menu pattern borrowed from
jrigling/homeassistant-jebao (the better of the two reference integrations
for onboarding UX), adapted to this project's actual LAN protocol."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_DID, CONF_MAC, CONF_PRODUCT_KEY, DEFAULT_SCAN_INTERVAL, DISCOVERY_TIMEOUT, DOMAIN
from .jebao_gizwits.discovery import DiscoveredDevice, discover, discover_one
from .jebao_gizwits.schema import known_product_keys, load_by_product_key

if TYPE_CHECKING:
    # Only for type checkers - the actual import path has moved across HA
    # versions (homeassistant.components.dhcp vs homeassistant.helpers.
    # service_info.dhcp), and importing homeassistant.components.dhcp for
    # real would pull in its aiodhcpwatcher dependency for no reason, since
    # we only ever read plain attributes off the object HA hands us.
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

_LOGGER = logging.getLogger(__name__)


def _normalize_mac(mac: str) -> str:
    return (mac or "").lower().replace(":", "")


def _product_names(product_keys: set[str]) -> dict[str, str | None]:
    """product_key -> English product name from the bundled schema, or None
    for a model this integration has no schema for. Blocking file I/O -
    call via async_add_executor_job."""
    names: dict[str, str | None] = {}
    for key in product_keys:
        try:
            names[key] = load_by_product_key(key).name_en
        except KeyError:
            names[key] = None
    return names


def discovery_label(device: DiscoveredDevice, name_en: str | None) -> str:
    """Human label for the discovery picker: what the device *is*, where it
    is, and enough MAC to tell identical models apart against a router's
    client list - not the raw cloud did, which means nothing to a person."""
    name = name_en or f"Unsupported model ({device.product_key[:8]}...)"
    mac_tail = device.mac_hex[-4:] if device.mac_hex else "????"
    return f"{name} - {device.ip} (MAC ...{mac_tail})"


def find_entry_by_mac(
    entries: list[config_entries.ConfigEntry], mac: str
) -> config_entries.ConfigEntry | None:
    """Pulled out of async_step_dhcp so the matching logic (the part worth
    getting right) is testable with plain fake entries, without needing to
    fake HA's ConfigFlow/hass internals just to call _async_current_entries()."""
    target = _normalize_mac(mac)
    if not target:
        return None
    for entry in entries:
        stored = _normalize_mac(entry.data.get(CONF_MAC) or "")
        if stored and stored == target:
            return entry
    return None


class JebaoLocalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, DiscoveredDevice] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(step_id="user", menu_options=["discover", "manual"])

    async def async_step_discover(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            device = self._discovered[user_input["device"]]
            return await self._finish(device)

        devices = await discover(timeout=DISCOVERY_TIMEOUT)
        configured_dids = {e.data[CONF_DID] for e in self._async_current_entries()}
        candidates = [d for d in devices if d.did not in configured_dids]
        already = len(devices) - len(candidates)

        if not candidates:
            # Distinguish "found nothing at all" from "found only pumps you
            # already have" - otherwise a fully-configured setup reads as a
            # discovery failure.
            reason = (
                f"All {already} pump(s) found on the network are already configured."
                if already
                else "No pumps responded to LAN discovery within "
                f"{DISCOVERY_TIMEOUT:.0f}s. Make sure the pump is powered on and on "
                "the same network/VLAN as Home Assistant (LAN discovery is a UDP "
                "broadcast, which many routers do not forward between VLANs or "
                "between 2.4GHz guest/IoT networks). You can also add it manually "
                "if you know its IP."
            )
            return self.async_show_form(
                step_id="discover",
                errors={"base": "no_devices_found"},
                description_placeholders={"reason": reason},
            )

        self._discovered = {d.did: d for d in candidates}
        names = await self.hass.async_add_executor_job(
            _product_names, {d.product_key for d in candidates}
        )
        options = {d.did: discovery_label(d, names[d.product_key]) for d in candidates}
        # Same-model pumps read identically except for IP/MAC - sort by
        # label so they at least group together in the picker.
        options = dict(sorted(options.items(), key=lambda kv: kv[1]))
        return self.async_show_form(
            step_id="discover",
            data_schema=vol.Schema({vol.Required("device"): vol.In(options)}),
            description_placeholders={
                # Say how many were seen in total, so a device that's missing
                # from this list because it's already added doesn't look like
                # discovery failed to find it.
                "found": str(len(devices)),
                "already": (
                    f" {already} already configured (not listed)." if already else ""
                ),
            },
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            device = await discover_one(host, timeout=DISCOVERY_TIMEOUT)
            if device is None:
                errors["base"] = "cannot_connect"
            else:
                return await self._finish(device)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def _finish(self, device: DiscoveredDevice) -> FlowResult:
        await self.async_set_unique_id(device.did)
        self._abort_if_unique_id_configured(updates={CONF_HOST: device.ip})

        # known_product_keys()/load_by_product_key() do blocking file I/O
        # (Path.glob/read_text) - must not run directly on the event loop.
        known_keys = await self.hass.async_add_executor_job(known_product_keys)
        if device.product_key not in known_keys:
            return self.async_abort(
                reason="unsupported_product",
                description_placeholders={
                    "product_key": device.product_key,
                    "name": device.did,
                },
            )

        schema = await self.hass.async_add_executor_job(load_by_product_key, device.product_key)
        return self.async_create_entry(
            title=schema.name_en,
            data={
                CONF_HOST: device.ip,
                CONF_DID: device.did,
                CONF_PRODUCT_KEY: device.product_key,
                CONF_MAC: device.mac_hex,
            },
            options={"scan_interval": DEFAULT_SCAN_INTERVAL},
        )

    async def async_step_dhcp(self, discovery_info: "DhcpServiceInfo") -> FlowResult:
        """Recover a registered pump's IP after a DHCP lease renewal moves it.

        Manifest's "dhcp": [{"registered_devices": true}] restricts this to
        MACs already stored on one of our config entries - HA calls this on
        every DHCP lease it observes matching a registered device, so this
        fires proactively (no waiting for a read to fail first, unlike
        coordinator.py's rediscovery-on-failure path, which stays as a
        fallback for whenever DHCP watching itself doesn't catch a move -
        e.g. a stale ARP cache, or the device never renewing its lease).
        Never creates a new entry from DHCP alone - pattern borrowed from
        jrigling/homeassistant-jebao, which found the same thing: a bare MAC
        match isn't enough to safely identify an unconfigured pump's product
        type, so new devices still go through LAN discovery/manual entry.
        """
        entry = find_entry_by_mac(self._async_current_entries(), discovery_info.macaddress)
        if entry is None:
            return self.async_abort(reason="dhcp_mac_not_registered")

        if entry.data.get(CONF_HOST) != discovery_info.ip:
            _LOGGER.info(
                "DHCP recovery: %s moved %s -> %s",
                entry.title, entry.data.get(CONF_HOST), discovery_info.ip,
            )
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_HOST: discovery_info.ip}
            )
            self.hass.async_create_task(self.hass.config_entries.async_reload(entry.entry_id))
        return self.async_abort(reason="already_configured")

    @staticmethod
    def async_get_options_flow(entry: config_entries.ConfigEntry) -> "JebaoLocalOptionsFlow":
        return JebaoLocalOptionsFlow(entry)


class JebaoLocalOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        from .const import MAX_SCAN_INTERVAL, MIN_SCAN_INTERVAL

        current = self.entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("scan_interval", default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                    )
                }
            ),
        )
