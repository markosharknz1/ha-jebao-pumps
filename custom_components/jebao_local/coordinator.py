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

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DID,
    CONF_MAC,
    CONF_PRODUCT_KEY,
    DISCOVERY_TIMEOUT,
    DOMAIN,
    SESSION_TIMEOUT,
)
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
        # Absent on entries created before this field was added - MAC is
        # only used for the device page's "Connections" display (see
        # entity.py), so it's fine for it to just not show up there.
        self.mac: str | None = entry.data.get(CONF_MAC)
        # Loaded by async_load_schema() (see __init__.py:async_setup_entry) -
        # load_by_product_key() does blocking file I/O (Path.read_text), so
        # it can't run directly here in a synchronous __init__ called from
        # an async context; HA's event-loop guard flags it if it does.
        self.schema: DatapointSchema = None  # type: ignore[assignment]
        self._session: GizwitsSession | None = None

    async def async_load_schema(self) -> None:
        self.schema = await self.hass.async_add_executor_job(load_by_product_key, self.product_key)

    async def _ensure_session(self) -> GizwitsSession:
        if self._session is None:
            session = GizwitsSession(self.host)
            try:
                await session.connect()
                await session.authenticate()
            except BaseException:
                # connect() may well have succeeded before authenticate()
                # failed. Without this the socket is never closed and never
                # reachable again (self._session was never assigned), so
                # every failed poll leaked one connection to a device that
                # only tolerates a couple - which is itself a good way to
                # produce the ECONNRESET that got us here.
                await session.close()
                raise
            self._session = session
        return self._session

    async def _drop_session(self) -> None:
        """Discard the current session, closing it first.

        Setting self._session = None on its own leaks the socket exactly
        like the case above.
        """
        session, self._session = self._session, None
        if session is not None:
            await session.close()

    async def _read_once(self) -> bytes:
        """One connect(+authenticate as needed)+read, bounded by a timeout.

        Home Assistant does not time out a coordinator update, so an
        unbounded read on a half-open socket would stall this pump's
        polling until a restart.
        """
        async with asyncio.timeout(SESSION_TIMEOUT):
            session = await self._ensure_session()
            return await session.read_status()

    async def _async_update_data(self) -> dict[str, object]:
        # Three attempts: as-is, with a fresh session (the usual fix for a
        # socket the pump has since dropped), and finally after rediscovery
        # in case it moved. Every attempt is inside the try, including the
        # last - previously the post-rediscovery read sat outside any
        # handler, so a failure there escaped as an "Unexpected error
        # fetching ... data" traceback instead of a plain UpdateFailed.
        last_err: Exception | None = None
        for attempt in range(3):
            if attempt == 2:
                _LOGGER.debug("Reconnect failed for %s, trying rediscovery", self.did)
                try:
                    recovered = await self._try_recover_via_discovery()
                except (OSError, ProtocolError) as err:
                    last_err = err
                    break
                if not recovered:
                    break
            try:
                raw = await self._read_once()
            except (OSError, ProtocolError, TimeoutError) as err:
                last_err = err
                _LOGGER.debug(
                    "Read attempt %d failed for %s (%s): %s", attempt + 1, self.did, self.host, err
                )
                await self._drop_session()
                continue
            return self.schema.decode_status(raw)

        raise UpdateFailed(f"Cannot reach {self.did} at {self.host}: {last_err}") from last_err

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

        payload = build_control_payload(self.schema, changes)
        # Same retry-with-a-fresh-session shape as reads: a write is very
        # often the first thing to touch a socket the pump quietly dropped
        # since the last poll, and without dropping the dead session on
        # failure every later write would reuse it.
        for attempt in range(2):
            try:
                async with asyncio.timeout(SESSION_TIMEOUT):
                    session = await self._ensure_session()
                    await session.send_control(payload)
                break
            except (OSError, ProtocolError, TimeoutError):
                await self._drop_session()
                if attempt == 1:
                    raise
        await self.async_request_refresh()

    async def async_close(self) -> None:
        await self._drop_session()
