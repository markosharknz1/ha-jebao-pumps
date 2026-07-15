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
    """Broadcast a discovery frame and collect replies for `timeout` seconds."""
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
        transport.sendto(encode_frame(CMD_DISCOVER_REQUEST), ("255.255.255.255", DISCOVERY_PORT))
        await asyncio.sleep(timeout)
    finally:
        transport.close()

    return list(found.values())


async def discover_one(ip: str, timeout: float = DEFAULT_TIMEOUT) -> DiscoveredDevice | None:
    """Send a directed (unicast) discovery frame to a known IP - useful when
    a device's address is already known but its did/product_key aren't."""
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
        transport.sendto(encode_frame(CMD_DISCOVER_REQUEST), (ip, DISCOVERY_PORT))
        await asyncio.sleep(timeout)
    finally:
        transport.close()

    return result
