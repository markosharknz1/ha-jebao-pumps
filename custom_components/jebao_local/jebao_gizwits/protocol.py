"""GAgent binary frame encode/decode.

Format (from Apollon77/node-ph803w PROTOCOL.md, validated against
fixtures/discovery_reply.bin):
    00 00 00 03 <varint length> 00 <cmd:u16be> <payload>

`length` covers everything after itself: the flag byte, the 2-byte command,
and the payload. The flag byte is always 0x00 in every capture and reference
implementation seen so far.
"""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

MAGIC = b"\x00\x00\x00\x03"

CMD_ONBOARD_REQUEST = 0x0001
CMD_ONBOARD_RESPONSE = 0x0002
CMD_DISCOVER_REQUEST = 0x0003
CMD_DISCOVER_RESPONSE = 0x0004
CMD_STARTUP_BROADCAST = 0x0005
CMD_PASSCODE_REQUEST = 0x0006
CMD_PASSCODE_RESPONSE = 0x0007
CMD_LOGIN_REQUEST = 0x0008
CMD_LOGIN_RESPONSE = 0x0009
CMD_HEARTBEAT_REQUEST = 0x0015
CMD_HEARTBEAT_RESPONSE = 0x0016
CMD_SERIAL_TRANSMIT_REQUEST = 0x0090
CMD_SERIAL_TRANSMIT_RESPONSE = 0x0091
CMD_SERIAL_CONTROL_REQUEST = 0x0093
CMD_SERIAL_CONTROL_RESPONSE = 0x0094


def encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_varint(buf: bytes, offset: int = 0) -> tuple[int, int]:
    """Returns (value, bytes_consumed)."""
    result = 0
    shift = 0
    pos = offset
    while True:
        b = buf[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            return result, pos - offset
        shift += 7


def encode_frame(command: int, payload: bytes = b"") -> bytes:
    data = b"\x00" + struct.pack(">H", command) + payload
    return MAGIC + encode_varint(len(data)) + data


@dataclass(frozen=True)
class Frame:
    command: int
    payload: bytes
    flag: int = 0x00


def decode_frame(buf: bytes) -> Frame:
    """Decode a frame. The flag byte is normally 0x00 in every request/read
    response seen so far, but a control response (0x94) has been observed
    with flag=0x01 - meaning is unconfirmed (possibly an error/status flag
    specific to that path), so it's surfaced on Frame.flag rather than
    rejected here."""
    if buf[:4] != MAGIC:
        raise ValueError(f"bad magic: {buf[:4]!r}, expected {MAGIC!r}")
    length, n = decode_varint(buf, 4)
    header_len = 4 + n
    data = buf[header_len : header_len + length]
    if len(data) != length:
        raise ValueError(f"frame length mismatch: header says {length}, got {len(data)} bytes")
    flag = data[0]
    command = struct.unpack(">H", data[1:3])[0]
    payload = data[3:]
    return Frame(command=command, payload=payload, flag=flag)


async def read_frame(reader: asyncio.StreamReader) -> Frame:
    """Read one full frame off a TCP stream, following the varint length prefix."""
    magic = await reader.readexactly(4)
    if magic != MAGIC:
        raise ValueError(f"bad magic: {magic!r}, expected {MAGIC!r}")

    length_bytes = bytearray()
    while True:
        b = await reader.readexactly(1)
        length_bytes += b
        if not (b[0] & 0x80):
            break
    length, _ = decode_varint(bytes(length_bytes), 0)

    data = await reader.readexactly(length)
    return decode_frame(magic + bytes(length_bytes) + data)
