"""LAN discovery (Phase 2).

Broadcasts a GAgent discovery frame to UDP 12414 and parses replies. Reply
field layout is taken directly from Apollon77/node-ph803w's
lib/discovery.js `_handleReplyBroadcast` (not guessed), and validated
against fixtures/discovery_reply.bin.

Note: unlike the SPEC.md phase description, the UDP discovery reply does
NOT contain a device passcode - per PROTOCOL.md, the passcode is obtained
separately over the TCP session (command 0x06/0x07, see Phase 3).
"""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

from .protocol import CMD_DISCOVER_REQUEST, CMD_DISCOVER_RESPONSE, decode_frame, encode_frame

DISCOVERY_PORT = 12414
DEFAULT_TIMEOUT = 5.0

# A single broadcast is not enough in practice: UDP is lossy, these are
# cheap WiFi modules, and a busy/congested 2.4GHz network drops frames -
# a real user with 5 pumps saw only 4 of them consistently. Probes are
# re-sent across the listen window so one lost packet (in either
# direction) no longer means a missing device. Offsets are front-loaded
# so a normal scan still resolves quickly, with later retries to catch
# slow or contended responders.
PROBE_OFFSETS = (0.0, 0.4, 1.2, 2.5)


@dataclass(frozen=True)
class DiscoveredDevice:
    ip: str
    did: str
    mac_hex: str
    wifi_firmware: str
    product_key: str
    api_server: str
    version: str
    extra: bytes


def _read_u16(buf: bytes, pos: int) -> tuple[int, int]:
    return struct.unpack(">H", buf[pos : pos + 2])[0], pos + 2


def _read_len_prefixed_bytes(buf: bytes, pos: int) -> tuple[bytes, int]:
    n, pos = _read_u16(buf, pos)
    return buf[pos : pos + n], pos + n


def _read_len_prefixed_str(buf: bytes, pos: int) -> tuple[str, int]:
    raw, pos = _read_len_prefixed_bytes(buf, pos)
    return raw.decode("ascii"), pos


def _read_null_terminated_str(buf: bytes, pos: int) -> tuple[str, int]:
    end = buf.index(b"\x00", pos)
    return buf[pos:end].decode("ascii"), end + 1


def parse_discovery_reply(ip: str, raw: bytes) -> DiscoveredDevice:
    frame = decode_frame(raw)
    if frame.command != CMD_DISCOVER_RESPONSE:
        raise ValueError(f"not a discover-response frame: command={frame.command:#06x}")

    payload = frame.payload
    pos = 0
    did, pos = _read_len_prefixed_str(payload, pos)
    mac, pos = _read_len_prefixed_bytes(payload, pos)
    wifi_firmware, pos = _read_len_prefixed_str(payload, pos)
    product_key, pos = _read_len_prefixed_str(payload, pos)
    pos += 8  # MCU attributes, skipped (per node-ph803w reference)
    api_server, pos = _read_null_terminated_str(payload, pos)
    version, pos = _read_null_terminated_str(payload, pos)
    extra = payload[pos:]

    return DiscoveredDevice(
        ip=ip,
        did=did,
        mac_hex=mac.hex(),
        wifi_firmware=wifi_firmware,
        product_key=product_key,
        api_server=api_server,
        version=version,
        extra=extra,
    )


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_reply):
        self._on_reply = on_reply

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._on_reply(addr[0], data)


async def discover(timeout: float = DEFAULT_TIMEOUT) -> list[DiscoveredDevice]:
    """Broadcast a discovery frame and collect replies for `timeout` seconds.

    The probe is re-sent several times across the window (PROBE_OFFSETS) -
    devices are deduplicated by did, so extra replies are free, but a
    dropped packet no longer hides a pump. Replies keep arriving for the
    full timeout regardless of when the last probe went out.
    """
    found: dict[str, DiscoveredDevice] = {}

    def on_reply(ip: str, data: bytes) -> None:
        try:
            device = parse_discovery_reply(ip, data)
        except ValueError:
            return
        found[device.did] = device

    loop = asyncio.get_event_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _DiscoveryProtocol(on_reply),
        local_addr=("0.0.0.0", 0),
        allow_broadcast=True,
    )
    try:
        frame = encode_frame(CMD_DISCOVER_REQUEST)
        elapsed = 0.0
        for offset in PROBE_OFFSETS:
            if offset >= timeout:
                break
            if offset > elapsed:
                await asyncio.sleep(offset - elapsed)
                elapsed = offset
            transport.sendto(frame, ("255.255.255.255", DISCOVERY_PORT))
        if timeout > elapsed:
            await asyncio.sleep(timeout - elapsed)
    finally:
        transport.close()

    return list(found.values())


async def discover_one(ip: str, timeout: float = DEFAULT_TIMEOUT) -> DiscoveredDevice | None:
    """Send a directed (unicast) discovery frame to a known IP, for the
    manual-entry config flow path where the user already knows the pump's
    address but we still need its did/product_key to pick a schema."""
    result: DiscoveredDevice | None = None

    def on_reply(reply_ip: str, data: bytes) -> None:
        nonlocal result
        try:
            result = parse_discovery_reply(reply_ip, data)
        except ValueError:
            pass

    loop = asyncio.get_event_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _DiscoveryProtocol(on_reply),
        local_addr=("0.0.0.0", 0),
    )
    try:
        frame = encode_frame(CMD_DISCOVER_REQUEST)
        elapsed = 0.0
        for offset in PROBE_OFFSETS:
            if offset >= timeout or result is not None:
                break
            if offset > elapsed:
                await asyncio.sleep(offset - elapsed)
                elapsed = offset
            transport.sendto(frame, (ip, DISCOVERY_PORT))
        # Unlike the broadcast scan there's only one device to hear from,
        # so stop as soon as it answers instead of burning the full timeout.
        while result is None and elapsed < timeout:
            await asyncio.sleep(0.1)
            elapsed += 0.1
    finally:
        transport.close()

    return result
