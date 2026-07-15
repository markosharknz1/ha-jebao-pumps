"""
Phase 1A: experimental control-write test against the real wavemaker pump.

Reuses the READ handshake already confirmed working (test_gizwits_lan.py),
then attempts a control write using the 323-byte frame format documented in
the python-jebao package (built for MDP-20000, NOT this wavemaker) as a
starting hypothesis, since it shares the same 0x93/0x94 message-type
constants we already observed working for reads on this device.

This is genuinely experimental: opcode semantics (TURN_ON_OFF etc.) are only
confirmed for MDP-20000 in python-jebao, not for this MOW-class wavemaker.
Script reads status before, attempts the write, reads status after, and
reverts (turns back off) if it appears to have actually changed power state.
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

DEVICE_IP = "192.168.1.77"
LAN_PORT = 12416

MSG_CONTROL_OR_EXTENDED_REQUEST = 0x93
CONTROL_COMMAND_SIZE = 323


def send_local_command(command: bytes, payload: bytes = b"") -> bytes:
    header = b"\x00\x00\x00\x03"
    flag = b"\x00"
    length = len(flag + command + payload).to_bytes(1, byteorder="big")
    return header + length + flag + command + payload


def decode_leb128(data: bytes):
    result = 0
    shift = 0
    for i, byte in enumerate(data):
        result |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return result, i + 1
        shift += 7
    return None, 0


def split_messages(response: bytes):
    pattern = b"\x00\x00\x00\x03"
    messages = []
    i = 0
    while True:
        idx = response.find(pattern, i)
        if idx == -1:
            break
        leb_start = idx + len(pattern)
        length, leb_len = decode_leb128(response[leb_start:])
        if length is None:
            break
        body_start = leb_start + leb_len
        body_end = body_start + length
        body = response[body_start:body_end]
        messages.append({"offset": idx, "length": length, "body": body})
        i = body_end
    return messages


def get_switch_state(status_payload: bytes) -> bool:
    """Byte0 bit0 = SwitchON per the bundled model file."""
    return bool(status_payload[0] & 0x01)


async def read_status(reader, writer) -> bytes:
    """Run the full read handshake (auth + status query), return the 0x94 payload."""
    pkt1 = send_local_command(b"\x00\x06")
    writer.write(pkt1)
    await writer.drain()
    resp1 = await asyncio.wait_for(reader.read(1024), timeout=5)
    binding_key = resp1[-12:]

    pkt2 = send_local_command(b"\x00\x08", binding_key)
    writer.write(pkt2)
    await writer.drain()
    await asyncio.wait_for(reader.read(1024), timeout=5)

    pkt3 = send_local_command(b"\x00\x93", b"\x00\x00\x00\x02\x02")
    writer.write(pkt3)
    await writer.drain()

    buf = b""
    for _ in range(5):
        try:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=2)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        buf += chunk

    messages = split_messages(buf)
    for m in messages:
        if len(m["body"]) >= 3 and m["body"][1:3] == b"\x00\x94":
            return m["body"][3:]
    raise RuntimeError(f"No 0x94 status message found in response: {buf.hex()}")


def build_mdp_style_control_command(sequence: int, opcode1: int, opcode2: int = 0, param1: int = 0, param2: int = 0) -> bytes:
    """323-byte control frame, format per python-jebao (MDP-20000). Experimental for this device."""
    buffer = bytearray(CONTROL_COMMAND_SIZE)
    buffer[0:4] = b"\x00\x00\x00\x03"
    buffer[4] = 0xBD
    buffer[5] = 0x02
    buffer[8] = MSG_CONTROL_OR_EXTENDED_REQUEST
    buffer[9:13] = sequence.to_bytes(4, "big")
    buffer[13] = 0x01
    buffer[21] = opcode1
    buffer[22] = opcode2
    buffer[23] = param1
    buffer[24] = param2
    return bytes(buffer)


async def try_write(reader, writer, opcode1, opcode2, param1=0, param2=0, sequence=1):
    cmd = build_mdp_style_control_command(sequence, opcode1, opcode2, param1, param2)
    print(f"TX control command ({len(cmd)} bytes), opcode1=0x{opcode1:02x} opcode2=0x{opcode2:02x} param1={param1} param2={param2}")
    print(f"TX hex (first 30 bytes): {cmd[:30].hex()}")
    writer.write(cmd)
    await writer.drain()

    buf = b""
    for i in range(5):
        try:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=2)
        except asyncio.TimeoutError:
            print(f"  read attempt {i}: timeout")
            break
        if not chunk:
            print(f"  read attempt {i}: connection closed")
            break
        print(f"  read attempt {i}: {len(chunk)} bytes: {chunk[:60].hex()}{'...' if len(chunk) > 60 else ''}")
        buf += chunk
    return buf


async def main():
    print(f"Connecting to {DEVICE_IP}:{LAN_PORT} ...")
    reader, writer = await asyncio.open_connection(DEVICE_IP, LAN_PORT)
    print("Connected.\n")

    try:
        print("--- Reading status BEFORE write attempt ---")
        before = await read_status(reader, writer)
        switch_before = get_switch_state(before)
        print(f"SwitchON before: {switch_before}")
        print(f"Full status payload hex: {before.hex()[:60]}...\n")

        print("--- Attempting experimental control write: TURN_ON_OFF opcode1=0x01, opcode2=0x01 (ON) ---")
        write_resp = await try_write(reader, writer, opcode1=0x01, opcode2=0x01, sequence=1)
        print()

        print("--- Reconnecting to read status AFTER write attempt ---")
        writer.close()
        await writer.wait_closed()
        reader2, writer2 = await asyncio.open_connection(DEVICE_IP, LAN_PORT)
        after = await read_status(reader2, writer2)
        switch_after = get_switch_state(after)
        print(f"SwitchON after: {switch_after}")
        print(f"Full status payload hex: {after.hex()[:60]}...\n")

        if switch_after and not switch_before:
            print("*** IT WORKED: pump switched ON. Reverting to OFF now. ***")
            revert_resp = await try_write(reader2, writer2, opcode1=0x01, opcode2=0x00, sequence=2)
            writer2.close()
            await writer2.wait_closed()
            reader3, writer3 = await asyncio.open_connection(DEVICE_IP, LAN_PORT)
            final = await read_status(reader3, writer3)
            print(f"SwitchON after revert: {get_switch_state(final)}")
            writer3.close()
            await writer3.wait_closed()
        else:
            print("No state change detected. Write command likely not recognized in this format/opcode by this device.")
            writer2.close()
            await writer2.wait_closed()

    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
