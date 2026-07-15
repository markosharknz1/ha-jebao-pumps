"""Config flow - discovery-first menu pattern borrowed from
jrigling/homeassistant-jebao (the better of the two reference integrations
for onboarding UX), adapted to this project's actual LAN protocol."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_DID, CONF_PRODUCT_KEY, DEFAULT_SCAN_INTERVAL, DISCOVERY_TIMEOUT, DOMAIN
from .jebao_gizwits.discovery import DiscoveredDevice, discover, discover_one
from .jebao_gizwits.schema import known_product_keys, load_by_product_key

_LOGGER = logging.getLogger(__name__)


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

        if not candidates:
            return self.async_show_form(
                step_id="discover",
                errors={"base": "no_devices_found"},
                description_placeholders={
                    "reason": "No new pumps responded to LAN discovery within "
                    f"{DISCOVERY_TIMEOUT:.0f}s. Make sure the pump is powered on, "
                    "on the same network/VLAN as Home Assistant, and not already "
                    "configured. You can also add it manually if you know its IP."
                },
            )

        self._discovered = {d.did: d for d in candidates}
        options = {d.did: f"{d.did} ({d.ip}, product_key={d.product_key[:8]}...)" for d in candidates}
        return self.async_show_form(
            step_id="discover",
            data_schema=vol.Schema({vol.Required("device"): vol.In(options)}),
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

        if device.product_key not in known_product_keys():
            return self.async_abort(
                reason="unsupported_product",
                description_placeholders={
                    "product_key": device.product_key,
                    "name": device.did,
                },
            )

        schema = load_by_product_key(device.product_key)
        return self.async_create_entry(
            title=schema.name,
            data={
                CONF_HOST: device.ip,
                CONF_DID: device.did,
                CONF_PRODUCT_KEY: device.product_key,
            },
            options={"scan_interval": DEFAULT_SCAN_INTERVAL},
        )

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
