"""
Phase 1A: targeted test of command 0x92 (hypothesis: control/set, paired with
0x93 read/get, per common older Gizwits/ESP8266 GAgent convention) with a
handful of payload shapes mirroring the status bit-layout, trying to turn
SwitchON on. Low-volume (a few attempts), not a full brute force.
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


def swap_endian(hex_str):
    if len(hex_str) >= 4:
        return hex_str[2:4] + hex_str[0:2] + hex_str[4:]
    return hex_str


def extract_bits(byte_val, bit_offset, length):
    mask = (1 << length) - 1
    return (byte_val >> bit_offset) & mask


def parse_device_status(payload, model):
    status = {}
    hex_payload = payload.hex()
    swap_needed = any(
        a["position"]["byte_offset"] == 0 and (a["position"]["bit_offset"] + a["position"]["len"] > 8)
        for a in model["attrs"]
    )
    if swap_needed:
        hex_payload = swap_endian(hex_payload)
    payload_bytes = bytes.fromhex(hex_payload)
    for attr in model["attrs"]:
        bo, bito, ln = attr["position"]["byte_offset"], attr["position"]["bit_offset"], attr["position"]["len"]
        dt = attr.get("data_type")
        if bo >= len(payload_bytes) or attr["type"] == "fault":
            continue
        if dt == "bool":
            v = bool(extract_bits(payload_bytes[bo], bito, ln))
        elif dt == "enum":
            idx = extract_bits(payload_bytes[bo], bito, ln)
            ev = attr.get("enum", [])
            v = ev[idx] if idx < len(ev) else f"<idx {idx}>"
        elif dt == "uint8":
            v = payload_bytes[bo]
        else:
            v = None
        status[attr["name"]] = v
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
    for m in split_messages(buf):
        if len(m["body"]) >= 3 and m["body"][1:3] == b"\x00\x94" and len(m["body"]) > 100:
            return parse_device_status(m["body"][3:], model)
    return None


def diff_status(before, after):
    return {k: (before.get(k), after.get(k)) for k in before if before.get(k) != after.get(k)}


async def try_payload(reader, writer, model, label, command, payload, baseline):
    print(f"\n--- {label}: command={command.hex()} payload={payload.hex()} ---")
    pkt = send_local_command(command, payload)
    writer.write(pkt)
    await writer.drain()
    resp = await drain(reader, total_timeout=1.5)
    print(f"  raw response ({len(resp)} bytes): {resp.hex()[:120]}{'...' if len(resp) > 60 else ''}")
    after = await read_status(reader, writer, model)
    if after is None:
        print("  could not read status after")
        return
    changes = diff_status(baseline, after)
    if changes:
        print(f"  *** CHANGE: {changes} ***")
    else:
        print("  no change")
    return after


async def main():
    with open(MODEL_PATH) as f:
        model = json.load(f)

    print(f"Connecting to {DEVICE_IP}:{LAN_PORT} ...")
    reader, writer = await asyncio.open_connection(DEVICE_IP, LAN_PORT)

    pkt1 = send_local_command(b"\x00\x06")
    writer.write(pkt1)
    await writer.drain()
    resp1 = await asyncio.wait_for(reader.read(1024), timeout=5)
    binding_key = resp1[-12:]
    pkt2 = send_local_command(b"\x00\x08", binding_key)
    writer.write(pkt2)
    await writer.drain()
    await asyncio.wait_for(reader.read(1024), timeout=5)
    print("Auth done.")

    baseline = await read_status(reader, writer, model)
    print(f"BASELINE: {baseline}")

    # Variant A: command 0x92, payload = just byte0 with SwitchON bit set
    await try_payload(reader, writer, model, "0x92 raw byte0=0x01", b"\x00\x92", b"\x01", baseline)

    # Variant B: command 0x92, mirroring read command's "00 00 00 02" prefix + value byte
    await try_payload(reader, writer, model, "0x92 with 00000002 prefix + 0x01", b"\x00\x92", b"\x00\x00\x00\x02\x01", baseline)

    # Variant C: command 0x92, 5-byte full status-like prefix (mask=0xff + value)
    await try_payload(reader, writer, model, "0x92 mask+value", b"\x00\x92", b"\xff\x01", baseline)

    # Variant D: command 0x93 (known-good read opcode) but with a WRITE-shaped payload:
    # mimic status format offset 0..4 as if setting the whole byte0-4 block directly
    await try_payload(reader, writer, model, "0x93 with byte0..4 write-shaped payload", b"\x00\x93", b"\x01\x00\x00\x02\x00", baseline)

    final = await read_status(reader, writer, model)
    print(f"\nFinal status: {final}")

    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
