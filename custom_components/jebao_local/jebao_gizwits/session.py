"""LAN session: connect, authenticate, read status (Phase 3).

Handshake and command sequence come from Apollon77/node-ph803w PROTOCOL.md
("Minimum Interaction scheme"): passcode request/response, then login
request/response, then a serial-data read-status request.
"""
from __future__ import annotations

import asyncio
import logging
import struct

from .protocol import (
    CMD_LOGIN_REQUEST,
    CMD_LOGIN_RESPONSE,
    CMD_PASSCODE_REQUEST,
    CMD_PASSCODE_RESPONSE,
    CMD_SERIAL_CONTROL_REQUEST,
    CMD_SERIAL_CONTROL_RESPONSE,
    CMD_SERIAL_TRANSMIT_REQUEST,
    CMD_SERIAL_TRANSMIT_RESPONSE,
    encode_frame,
    read_frame,
)

TCP_PORT = 12416

_LOGGER = logging.getLogger(__name__)

# p0 protocol action byte (node-ph803w PROTOCOL.md "p0 protocol" section)
P0_ACTION_READ_STATUS = 0x02
P0_ACTION_STATUS_REPLY = 0x03
P0_ACTION_STATUS_REPORT = 0x04


class ProtocolError(RuntimeError):
    pass


class GizwitsSession:
    def __init__(self, ip: str, port: int = TCP_PORT):
        self.ip = ip
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.passcode: bytes | None = None
        self._seq = 0

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.ip, self.port)

    async def close(self) -> None:
        """Idempotent, and never raises.

        Clears the reader/writer first so a second close (or a close
        racing a failed connect) is a no-op, and swallows errors from
        wait_closed: the connection is already being torn down because
        something went wrong, and a failure to flush must not mask the
        original error or leave the caller unable to reconnect.
        """
        writer, self._writer, self._reader = self._writer, None, None
        if writer is None:
            return
        writer.close()  # this is what actually releases the socket
        try:
            await writer.wait_closed()
        except OSError:
            pass  # peer already gone; nothing left to flush
        # CancelledError is deliberately NOT caught - swallowing it would
        # break cancellation, and close() above has already done the work.

    async def _send(self, command: int, payload: bytes = b"") -> None:
        assert self._writer is not None
        self._writer.write(encode_frame(command, payload))
        await self._writer.drain()

    async def _recv(self):
        assert self._reader is not None
        return await read_frame(self._reader)

    async def authenticate(self) -> bytes:
        """Passcode request/response, then login request/response. Returns the passcode."""
        await self._send(CMD_PASSCODE_REQUEST)
        frame = await self._recv()
        if frame.command != CMD_PASSCODE_RESPONSE:
            raise ProtocolError(f"expected passcode response, got command {frame.command:#06x}")
        if len(frame.payload) < 2:
            raise ProtocolError("passcode response too short - device likely not in binding mode")
        n = struct.unpack(">H", frame.payload[:2])[0]
        passcode = frame.payload[2 : 2 + n]
        if len(passcode) != n:
            raise ProtocolError("passcode response length mismatch")
        self.passcode = passcode

        await self._send(CMD_LOGIN_REQUEST, struct.pack(">H", len(passcode)) + passcode)
        frame = await self._recv()
        if frame.command != CMD_LOGIN_RESPONSE:
            raise ProtocolError(f"expected login response, got command {frame.command:#06x}")
        if not frame.payload or frame.payload[-1] != 0x00:
            raise ProtocolError(f"login failed, response payload: {frame.payload!r}")

        return passcode

    async def read_status(self, max_skip: int = 5) -> bytes:
        """Send a p0 read-status request and return the raw status payload
        (with the p0 action byte stripped). Tolerates unsolicited frames
        (e.g. a duplicate login ack) arriving before the actual reply."""
        await self._send(CMD_SERIAL_TRANSMIT_REQUEST, bytes([P0_ACTION_READ_STATUS]))

        for _ in range(max_skip):
            frame = await self._recv()
            if frame.command == CMD_SERIAL_TRANSMIT_RESPONSE:
                if not frame.payload:
                    raise ProtocolError("empty status response")
                action = frame.payload[0]
                if action not in (P0_ACTION_STATUS_REPLY, P0_ACTION_STATUS_REPORT):
                    raise ProtocolError(f"expected status reply/report action, got {action:#x}")
                return frame.payload[1:]
            # unsolicited/duplicate frame (e.g. a repeated login ack) - skip it
            _LOGGER.debug("skipping unrelated frame command=%#06x payload=%r", frame.command, frame.payload)
            continue

        raise ProtocolError(f"no serial transmit response after skipping {max_skip} unrelated frames")

    async def send_control(self, p0_payload: bytes, max_skip: int = 5) -> bytes:
        """Send a p0 control payload (action byte + attrFlags_t + attrVals_t,
        see jebao_gizwits.control) via command 0x93, and return the response
        payload with the sequence number stripped.

        Per node-ph803w PROTOCOL.md, 0x93 requests carry a 4-byte increasing
        sequence number before the p0 payload; the 0x94 response echoes the
        same sequence number and is sent only to the requesting client.
        """
        self._seq += 1
        seq = self._seq
        await self._send(CMD_SERIAL_CONTROL_REQUEST, struct.pack(">I", seq) + p0_payload)

        for _ in range(max_skip):
            frame = await self._recv()
            if frame.command == CMD_SERIAL_CONTROL_RESPONSE:
                if len(frame.payload) < 4:
                    raise ProtocolError(f"control response too short: {frame.payload!r}")
                resp_seq = struct.unpack(">I", frame.payload[:4])[0]
                if resp_seq != seq:
                    raise ProtocolError(f"control response seq mismatch: sent {seq}, got {resp_seq}")
                return frame.payload[4:]
            _LOGGER.debug("skipping unrelated frame command=%#06x payload=%r", frame.command, frame.payload)
            continue

        raise ProtocolError(f"no control response after skipping {max_skip} unrelated frames")
