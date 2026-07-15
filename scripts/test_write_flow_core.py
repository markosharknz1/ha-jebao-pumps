"""Phase 4, hypothesis: the JSON datapoint schema describes the CLOUD API's
attribute set, which isn't guaranteed to match the compiled firmware's LAN
attrFlags_t/attrVals_t struct size. Test a much smaller "core" control
struct - just ids 0-13 (the single status byte0-1 bitfields + the six
uint8 values at bytes 2-7), excluding the 48 schedule-slot blobs and
date/time fields - via both cmd 0x90 and cmd 0x93."""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from jebao_gizwits.control import attr_flags_size, attr_vals_size, build_control_payload
from jebao_gizwits.protocol import CMD_SERIAL_TRANSMIT_REQUEST
from jebao_gizwits.schema import load
from jebao_gizwits.session import GizwitsSession

PUMP_IP = "192.168.1.77"
CORE_MAX_ID = 13  # ids 0-13: byte0-1 bitfields + Flow/Frequency/FeedTime/AutoFlow/AutoFreq/AutoFeedTime


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

    print(f"Core-only sizing (max_id={CORE_MAX_ID}): "
          f"attrFlags={attr_flags_size(schema, CORE_MAX_ID)} bytes, "
          f"attrVals={attr_vals_size(schema, CORE_MAX_ID)} bytes\n")

    session = GizwitsSession(PUMP_IP)
    await session.connect()
    await session.authenticate()

    before = await session.read_status()
    original_flow = schema.decode_status(before)["Flow"]
    new_flow = original_flow + 5 if original_flow <= 95 else original_flow - 5
    print(f"Before: Flow={original_flow}  ->  target {new_flow}\n")

    payload = build_control_payload(schema, before, {"Flow": new_flow}, max_id=CORE_MAX_ID)
    print(f"Core payload ({len(payload)} bytes): {payload.hex()}\n")

    print("--- via cmd 0x90 ---")
    await session._send(CMD_SERIAL_TRANSMIT_REQUEST, payload)
    await log_frames_for(session, schema, 3)

    check1 = schema.decode_status(await session.read_status())["Flow"]
    print(f"Flow after cmd 0x90 attempt: {check1}\n")

    if check1 != new_flow:
        print("--- via cmd 0x93 ---")
        resp = await session.send_control(payload)
        print(f"  0x93 response payload: {resp!r}")
        await asyncio.sleep(0.5)
        check2 = schema.decode_status(await session.read_status())["Flow"]
        print(f"Flow after cmd 0x93 attempt: {check2}\n")
    else:
        check2 = check1

    final_flow = check2 if check2 == new_flow else check1
    if final_flow == new_flow:
        print("*** CORE-SIZED PAYLOAD WORKED. Restoring... ***")
        cur = await session.read_status()
        restore = build_control_payload(schema, cur, {"Flow": original_flow}, max_id=CORE_MAX_ID)
        await session._send(CMD_SERIAL_TRANSMIT_REQUEST, restore)
        await log_frames_for(session, schema, 3)
        final = schema.decode_status(await session.read_status())["Flow"]
        print(f"Restored Flow={final} (should be {original_flow})")
    else:
        print("*** Core-sized payload also had no effect. ***")

    await session.close()


if __name__ == "__main__":
    asyncio.run(main())
