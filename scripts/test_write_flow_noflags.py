"""Phase 4, ground-truth-informed retry: Ghidra decompilation of
GizWifiSDKGetFlagsLenByProductJsonStr shows the firmware only computes a
non-zero attrFlags_t length when protocolType == "var_len". Our schema says
protocolType == "standard", so flagsLen == 0 for this device - meaning the
real control payload likely has NO flags region at all:

    action(0x01) + attrVals_t(400 bytes, full writable state, no flags)

instead of action + attrFlags_t(8 bytes) + attrVals_t(400 bytes) from the
PET demo (which is presumably the "var_len" product's format, not ours)."""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from jebao_gizwits.control import attr_vals_size
from jebao_gizwits.protocol import CMD_SERIAL_TRANSMIT_REQUEST
from jebao_gizwits.schema import load
from jebao_gizwits.session import GizwitsSession

PUMP_IP = "192.168.1.77"


def build_noflags_payload(schema, base_status, changes):
    vals_size = attr_vals_size(schema)
    vals = bytearray(base_status[:vals_size])
    for name, new_value in changes.items():
        attr = schema.by_name(name)
        p = attr.position
        if p.unit == "byte" and attr.data_type == "uint8":
            us = attr.uint_spec
            raw_v = round((float(new_value) - us.addition) / us.ratio)
            vals[p.byte_offset] = raw_v & 0xFF
        else:
            raise NotImplementedError("this quick test only handles uint8 byte attrs")
    return bytes([0x01]) + bytes(vals)


async def log_frames_for(session, schema, seconds):
    start = time.monotonic()
    end = start + seconds
    while time.monotonic() < end:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        try:
            frame = await asyncio.wait_for(session._recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        t = time.monotonic() - start
        hint = ""
        if len(frame.payload) >= 1 and frame.payload[0] in (0x02, 0x03, 0x04):
            try:
                v = schema.decode_status(frame.payload[1:])
                hint = f"  [Flow={v['Flow']} Freq={v['Frequency']}]"
            except Exception:
                pass
        print(f"  [t={t:5.2f}s] command={frame.command:#06x} flag={frame.flag:#x} "
              f"len={len(frame.payload)} payload[:24]={frame.payload[:24]!r}{hint}")


async def main():
    schema = load(ROOT / "fixtures" / "datapoint_schema.json")

    session = GizwitsSession(PUMP_IP)
    await session.connect()
    await session.authenticate()

    before = await session.read_status()
    original_flow = schema.decode_status(before)["Flow"]
    new_flow = original_flow + 5 if original_flow <= 95 else original_flow - 5
    print(f"Before: Flow={original_flow}  ->  target {new_flow}\n")

    payload = build_noflags_payload(schema, before, {"Flow": new_flow})
    print(f"No-flags payload ({len(payload)} bytes): action=0x{payload[0]:02x}, "
          f"first 8 vals bytes: {payload[1:9].hex()}\n")

    print("--- sending via cmd 0x90 ---")
    await session._send(CMD_SERIAL_TRANSMIT_REQUEST, payload)
    await log_frames_for(session, schema, 3)

    after = schema.decode_status(await session.read_status())
    print(f"\nFlow after: {after['Flow']}")

    if after["Flow"] == new_flow:
        print("\n*** NO-FLAGS PAYLOAD WORKED! Restoring original value... ***")
        cur = await session.read_status()
        restore = build_noflags_payload(schema, cur, {"Flow": original_flow})
        await session._send(CMD_SERIAL_TRANSMIT_REQUEST, restore)
        await log_frames_for(session, schema, 3)
        final = schema.decode_status(await session.read_status())
        print(f"Restored Flow={final['Flow']} (should be {original_flow})")
    else:
        print("\n*** Still no effect. ***")

    await session.close()


if __name__ == "__main__":
    asyncio.run(main())
