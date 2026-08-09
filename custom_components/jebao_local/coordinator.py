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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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

    async def async_load_schema(self) -> None:
        self.schema = await self.hass.async_add_executor_job(load_by_product_key, self.product_key)

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[GizwitsSession]:
        """Open a session, hand it over, and always close it.

        Connections are deliberately NOT kept between polls. The GAgent
        protocol expects a heartbeat ping/pong (0x0015/0x0016) roughly
        every 4 seconds on an open TCP session (PROTOCOL.md), which this
        client never sends - so a session held across a 10s poll interval
        sat idle past what the device tolerates and was dropped, making
        every poll rediscover a dead socket. Rather than run a heartbeat
        task per pump, each operation gets its own short-lived
        connection: on a LAN the extra handshake costs milliseconds, and
        it leaves the pump - which has very few connection slots - with
        no long-lived connection from Home Assistant at all.

        The `finally` is the important part: it is what guarantees the
        socket is released even when authenticate() or the read raises.
        """
        session = GizwitsSession(self.host)
        try:
            await session.connect()
            await session.authenticate()
            yield session
        finally:
            await session.close()

    async def _read_once(self) -> bytes:
        """One connect+authenticate+read+close, bounded by a timeout.

        Home Assistant does not time out a coordinator update, so an
        unbounded read on a half-open socket would stall this pump's
        polling until a restart.
        """
        async with asyncio.timeout(SESSION_TIMEOUT):
            async with self._session_scope() as session:
                return await session.read_status()

    async def _async_update_data(self) -> dict[str, object]:
        # Two attempts: as-is, then again after rediscovery in case the
        # pump moved. There is deliberately no immediate second try on the
        # same address - with a fresh connection per read there is no
        # stale socket for a retry to clear, and this device has few
        # connection slots, so a failed poll should cost it as little as
        # possible. Both attempts are guarded; previously the
        # post-rediscovery read sat outside any handler, so a failure
        # there escaped as an "Unexpected error fetching ... data"
        # traceback instead of a plain UpdateFailed.
        last_err: Exception | None = None
        for attempt in range(2):
            if attempt == 1:
                _LOGGER.debug("Read failed for %s, trying rediscovery", self.did)
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
        # One retry: unlike a read, a failed write is worth attempting
        # again rather than waiting for the next poll, since the user is
        # sitting there expecting the pump to do something.
        for attempt in range(2):
            try:
                async with asyncio.timeout(SESSION_TIMEOUT):
                    async with self._session_scope() as session:
                        await session.send_control(payload)
                break
            except (OSError, ProtocolError, TimeoutError):
                if attempt == 1:
                    raise
        await self.async_request_refresh()

    async def async_close(self) -> None:
        """Nothing to tear down: connections are per-operation and closed
        by _session_scope. Kept because __init__.py calls it on unload."""
        return None
