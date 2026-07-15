"""
Phase 1A: replicate the local LAN status-read handshake from the chrisc123
reference repo (custom_components/jebao_aqua/api.py: get_local_device_data)
against the real pump, to see whether the TCP/12416 protocol still works on
the newer ESP32C3/BLE-enabled hardware.
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
    """Walk the buffer splitting it into individual Gizwits framed messages.

    Frame: 4-byte header (00 00 00 03) + LEB128 length + that many bytes
    of (flag + command + payload).
    """
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
            print(f"  [split] failed to decode LEB128 at offset {leb_start}")
            break
        body_start = leb_start + leb_len
        body_end = body_start + length
        body = response[body_start:body_end]
        messages.append({"offset": idx, "length": length, "body": body})
        i = body_end
    return messages


def extract_device_status_payload(response: bytes):
    messages = split_messages(response)
    print(f"  [split] found {len(messages)} message(s) in buffer")
    for m in messages:
        flag = m["body"][:1].hex() if len(m["body"]) >= 1 else ""
        command = m["body"][1:3].hex() if len(m["body"]) >= 3 else ""
        payload = m["body"][3:]
        print(f"  [msg] offset={m['offset']} declared_len={m['length']} flag={flag} command={command} payload_len={len(payload)} payload={payload.hex()}")

    # The status-query response is expected to carry command 0x0094 (push/
    # response to our 0x0093 query) per observed traffic; treat its payload
    # as the device status bytes.
    for m in messages:
        if len(m["body"]) >= 3 and m["body"][1:3] == b"\x00\x94":
            return m["body"][3:]

    # Fallback: return the payload of the longest message found.
    if messages:
        longest = max(messages, key=lambda m: len(m["body"]))
        if len(longest["body"]) > 3:
            print("  [split] no 0x0094 message found; falling back to longest message")
            return longest["body"][3:]
    return None


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
        if byte_offset >= len(payload_bytes):
            status[attr["name"]] = f"<out of range: byte_offset {byte_offset} >= payload len {len(payload_bytes)}>"
            continue
        if data_type == "bool":
            value = bool(extract_bits(payload_bytes[byte_offset], bit_offset, length))
        elif data_type == "enum":
            enum_values = attr.get("enum", [])
            idx = extract_bits(payload_bytes[byte_offset], bit_offset, length)
            value = enum_values[idx] if idx < len(enum_values) else f"<enum idx {idx} out of range>"
        elif data_type == "uint8":
            value = payload_bytes[byte_offset]
        elif data_type == "binary":
            value = payload_bytes[byte_offset:byte_offset + length].hex()
        else:
            value = None
        status[attr["name"]] = value
    return status


async def main():
    with open(MODEL_PATH) as f:
        model = json.load(f)

    print(f"Connecting to {DEVICE_IP}:{LAN_PORT} ...")
    reader, writer = await asyncio.open_connection(DEVICE_IP, LAN_PORT)
    print("Connected.")

    try:
        print("\n--- Step 1: send 0x0006 (get binding key) ---")
        pkt1 = send_local_command(b"\x00\x06")
        print(f"TX: {pkt1.hex()}")
        writer.write(pkt1)
        await writer.drain()
        resp1 = await asyncio.wait_for(reader.read(1024), timeout=5)
        print(f"RX ({len(resp1)} bytes): {resp1.hex()}")
        binding_key = resp1[-12:]
        print(f"Extracted binding_key: {binding_key.hex()}")

        print("\n--- Step 2: send 0x0008 + binding_key ---")
        pkt2 = send_local_command(b"\x00\x08", binding_key)
        print(f"TX: {pkt2.hex()}")
        writer.write(pkt2)
        await writer.drain()
        resp2 = await asyncio.wait_for(reader.read(1024), timeout=5)
        print(f"RX ({len(resp2)} bytes): {resp2.hex()}")

        print("\n--- Step 3: send 0x0093 + \\x00\\x00\\x00\\x02\\x02 (query status) ---")
        pkt3 = send_local_command(b"\x00\x93", b"\x00\x00\x00\x02\x02")
        print(f"TX: {pkt3.hex()}")
        writer.write(pkt3)
        await writer.drain()

        # Read repeatedly for a few seconds in case status arrives in a
        # separate, delayed packet after the initial ACK.
        resp3 = b""
        for i in range(5):
            try:
                chunk = await asyncio.wait_for(reader.read(1024), timeout=2)
            except asyncio.TimeoutError:
                print(f"  read attempt {i}: timeout, no more data")
                break
            if not chunk:
                print(f"  read attempt {i}: connection closed by peer")
                break
            print(f"  read attempt {i}: {len(chunk)} bytes: {chunk.hex()}")
            resp3 += chunk

        print(f"\nTotal RX after step 3: ({len(resp3)} bytes): {resp3.hex()}")

        payload = extract_device_status_payload(resp3)
        if payload:
            print(f"\nStatus payload: {payload.hex()}")
            parsed = parse_device_status(payload, model)
            print("\n--- Parsed device status ---")
            for k, v in parsed.items():
                print(f"  {k}: {v}")
        else:
            print("\nCould not extract status payload from response.")

    finally:
        writer.close()
        await writer.wait_closed()
        print("\nConnection closed.")


if __name__ == "__main__":
    asyncio.run(main())
