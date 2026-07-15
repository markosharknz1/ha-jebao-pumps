"""
Phase 1A: bounded brute-force of control-write opcode1 values against the
real wavemaker pump, looking for ANY opcode that changes ANY status
attribute. Builds on the working read/write infrastructure already verified
this session (test_gizwits_lan.py, test_gizwits_write.py).

For each candidate opcode1, sends a 323-byte control frame (format borrowed
from python-jebao, confirmed accepted by this device's firmware even though
its specific opcodes are for a different Jebao product), then reads status
and diffs against baseline. On any detected change, attempts to revert with
opcode2=0 and reports whether the revert succeeded.

Single persistent connection for the whole sweep (auth handshake once).
"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

DEVICE_IP = "192.168.1.77"
LAN_PORT = 12416
MODEL_PATH = (
    "C:/jebao-ha/reference/jebao_aqua-homeassistant/custom_components/"
    "jebao_aqua/models/54114ccdac1e41c0bb17e222887c07ba.json"
)
CONTROL_COMMAND_SIZE = 323
MSG_CONTROL_OR_EXTENDED_REQUEST = 0x93

OPCODE_RANGE = range(0x00, 0x51)  # 0x00 - 0x50 inclusive


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


def swap_endian(hex_str: str) -> str:
    if len(hex_str) >= 4:
        return hex_str[2:4] + hex_str[0:2] + hex_str[4:]
    return hex_str


def extract_bits(byte_val, bit_offset, length):
    mask = (1 << length) - 1
    return (byte_val >> bit_offset) & mask


def parse_device_status(payload: bytes, attribute_model: dict):
    status = {}
    hex_payload = payload.hex()
    swap_needed = any(
        a["position"]["byte_offset"] == 0
        and (a["position"]["bit_offset"] + a["position"]["len"] > 8)
        for a in attribute_model["attrs"]
    )
    if swap_needed:
        hex_payload = swap_endian(hex_payload)
    payload_bytes = bytes.fromhex(hex_payload)

    for attr in attribute_model["attrs"]:
        byte_offset = attr["position"]["byte_offset"]
        bit_offset = attr["position"]["bit_offset"]
        length = attr["position"]["len"]
        data_type = attr.get("data_type", "unknown")
        if byte_offset >= len(payload_bytes) or attr["type"] == "fault":
            continue  # skip fault block, known to be unreliable (padding bytes)
        if data_type == "bool":
            value = bool(extract_bits(payload_bytes[byte_offset], bit_offset, length))
        elif data_type == "enum":
            enum_values = attr.get("enum", [])
            idx = extract_bits(payload_bytes[byte_offset], bit_offset, length)
            value = enum_values[idx] if idx < len(enum_values) else f"<idx {idx}>"
        elif data_type == "uint8":
            value = payload_bytes[byte_offset]
        else:
            value = None
        status[attr["name"]] = value
    return status


async def drain(reader, total_timeout=1.5):
    buf = b""
    loop = asyncio.get_event_loop()
    end = loop.time() + total_timeout
    while loop.time() < end:
        remaining = end - loop.time()
        if remaining <= 0:
            break
        try:
            chunk = await asyncio.wait_for(reader.read(2048), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
    return buf


async def read_status(reader, writer, model):
    pkt = send_local_command(b"\x00\x93", b"\x00\x00\x00\x02\x02")
    writer.write(pkt)
    await writer.drain()
    buf = await drain(reader, total_timeout=2.0)
    messages = split_messages(buf)
    for m in messages:
        if len(m["body"]) >= 3 and m["body"][1:3] == b"\x00\x94" and len(m["body"]) > 100:
            return parse_device_status(m["body"][3:], model)
    return None


def build_control_command(sequence: int, opcode1: int, opcode2: int = 0, param1: int = 0, param2: int = 0) -> bytes:
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


async def send_write(reader, writer, sequence, opcode1, opcode2=1, param1=0, param2=0):
    cmd = build_control_command(sequence, opcode1, opcode2, param1, param2)
    writer.write(cmd)
    await writer.drain()
    await drain(reader, total_timeout=1.0)


def diff_status(before, after):
    changes = {}
    for k in before:
        if before.get(k) != after.get(k):
            changes[k] = (before.get(k), after.get(k))
    return changes


async def main():
    with open(MODEL_PATH) as f:
        model = json.load(f)

    print(f"Connecting to {DEVICE_IP}:{LAN_PORT} ...")
    reader, writer = await asyncio.open_connection(DEVICE_IP, LAN_PORT)

    # Auth handshake (once for the whole sweep)
    pkt1 = send_local_command(b"\x00\x06")
    writer.write(pkt1)
    await writer.drain()
    resp1 = await asyncio.wait_for(reader.read(1024), timeout=5)
    binding_key = resp1[-12:]
    pkt2 = send_local_command(b"\x00\x08", binding_key)
    writer.write(pkt2)
    await writer.drain()
    await asyncio.wait_for(reader.read(1024), timeout=5)
    print("Auth handshake done.\n")

    baseline = await read_status(reader, writer, model)
    print(f"BASELINE status: {baseline}\n")
    if baseline is None:
        print("Could not read baseline status, aborting.")
        return

    sequence = 10
    findings = []

    for opcode1 in OPCODE_RANGE:
        print(f"[opcode1=0x{opcode1:02x}] sending control write (opcode2=1)...", end=" ")
        try:
            await send_write(reader, writer, sequence, opcode1, opcode2=1)
            sequence += 1
            after = await read_status(reader, writer, model)
        except Exception as e:
            print(f"ERROR: {e}")
            continue

        if after is None:
            print("no status response")
            continue

        changes = diff_status(baseline, after)
        if changes:
            print(f"*** CHANGE: {changes} ***")
            findings.append({"opcode1": opcode1, "opcode2": 1, "changes": changes})

            # Attempt revert with opcode2=0
            try:
                await send_write(reader, writer, sequence, opcode1, opcode2=0)
                sequence += 1
                reverted = await read_status(reader, writer, model)
                revert_changes = diff_status(baseline, reverted) if reverted else "no response"
                print(f"    revert attempt (opcode2=0) -> remaining diff from baseline: {revert_changes}")
                if reverted:
                    baseline = reverted  # adopt new state as baseline going forward
            except Exception as e:
                print(f"    revert attempt errored: {e}")
        else:
            print("no change")

        await asyncio.sleep(0.3)

    print("\n\n=== SWEEP COMPLETE ===")
    print(f"Findings ({len(findings)}):")
    for f in findings:
        print(f"  opcode1=0x{f['opcode1']:02x} opcode2={f['opcode2']}: {f['changes']}")

    final_status = await read_status(reader, writer, model)
    print(f"\nFinal status: {final_status}")

    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
