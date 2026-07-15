"""Data update coordinator for a single Jebao pump/light/etc over LAN.

Pattern borrowed from reviewing two existing community integrations
(chrisc123/jebao_aqua-homeassistant, jrigling/homeassistant-jebao): one
coordinator instance per config entry, created exactly once in
__init__.py:async_setup_entry and shared via hass.data - never created
per-platform (jrigling's repo has a real bug where each platform lazily
creates its own coordinator, causing up to 5 redundant pollers per device).
Self-healing IP-recovery-via-rediscovery is borrowed from jrigling's
coordinator, which handles DHCP lease changes moving the pump's IP.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_DID, CONF_PRODUCT_KEY, DISCOVERY_TIMEOUT, DOMAIN
from .jebao_gizwits.discovery import discover
from .jebao_gizwits.schema import DatapointSchema, load_by_product_key
from .jebao_gizwits.session import GizwitsSession, ProtocolError

_LOGGER = logging.getLogger(__name__)


class JebaoLocalCoordinator(DataUpdateCoordinator[dict[str, object]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, scan_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{entry.data[CONF_DID]}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.entry = entry
        self.host: str = entry.data["host"]
        self.did: str = entry.data[CONF_DID]
        self.product_key: str = entry.data[CONF_PRODUCT_KEY]
        self.schema: DatapointSchema = load_by_product_key(self.product_key)
        self._session: GizwitsSession | None = None

    async def _ensure_session(self) -> GizwitsSession:
        if self._session is None:
            session = GizwitsSession(self.host)
            await session.connect()
            await session.authenticate()
            self._session = session
        return self._session

    async def _async_update_data(self) -> dict[str, object]:
        try:
            session = await self._ensure_session()
            raw = await session.read_status()
        except (OSError, ProtocolError) as err:
            _LOGGER.debug("Read failed for %s (%s): %s, trying reconnect", self.did, self.host, err)
            self._session = None
            try:
                session = await self._ensure_session()
                raw = await session.read_status()
            except (OSError, ProtocolError) as err2:
                _LOGGER.debug("Reconnect failed for %s, trying rediscovery: %s", self.did, err2)
                recovered = await self._try_recover_via_discovery()
                if not recovered:
                    raise UpdateFailed(
                        f"Cannot reach {self.did} at {self.host}, and rediscovery didn't find it"
                    ) from err2
                session = await self._ensure_session()
                raw = await session.read_status()

        return self.schema.decode_status(raw)

    async def _try_recover_via_discovery(self) -> bool:
        """The pump's IP may have changed (DHCP lease renewal). Broadcast
        discovery and, if we find this did at a new address, adopt it and
        persist the change into the config entry."""
        devices = await discover(timeout=DISCOVERY_TIMEOUT)
        for device in devices:
            if device.did == self.did and device.ip != self.host:
                _LOGGER.info("Jebao device %s moved %s -> %s, updating", self.did, self.host, device.ip)
                self.host = device.ip
                new_data = dict(self.entry.data)
                new_data["host"] = device.ip
                self.hass.config_entries.async_update_entry(self.entry, data=new_data)
                return True
            if device.did == self.did:
                return True  # same IP, device is just slow/temporarily unreachable
        return False

    async def async_write(self, changes: dict[str, object]) -> None:
        """Write one or more attributes, then refresh state to reflect it.

        Note: writes are only confirmed correct for byte-type (uint8, e.g.
        Flow/Frequency-style) and the SwitchON bit-type attribute on the
        wavemaker this project was built against - see
        jebao_gizwits/schema.py::load_by_product_key's docstring. Also see
        SPEC.md: a successful write to a bit-type attribute is not always
        reflected back correctly by read_status() for a manually-toggled
        pump - the coordinator's next refresh may not show the change even
        though the device applied it.
        """
        from .jebao_gizwits.control import build_control_payload

        session = await self._ensure_session()
        payload = build_control_payload(self.schema, changes)
        await session.send_control(payload)
        await self.async_request_refresh()

    async def async_close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
