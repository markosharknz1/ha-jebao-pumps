"""Phase 4, careful retry: send control payload via cmd 0x90, log every frame
that comes back (no early break) with timestamps, then do a clean separate
read_status() to check the final state."""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from jebao_gizwits.control import build_control_payload
from jebao_gizwits.protocol import CMD_SERIAL_TRANSMIT_REQUEST
from jebao_gizwits.schema import load
from jebao_gizwits.session import GizwitsSession

PUMP_IP = "192.168.1.77"


async def main():
    schema = load(ROOT / "fixtures" / "datapoint_schema.json")

    session = GizwitsSession(PUMP_IP)
    await session.connect()
    await session.authenticate()
    print("Connected + authenticated.")

    before = await session.read_status()
    before_values = schema.decode_status(before)
    original_flow = before_values["Flow"]
    print(f"Before: Flow={original_flow} Frequency={before_values['Frequency']}\n")

    new_flow = original_flow + 5 if original_flow <= 95 else original_flow - 5
    payload = build_control_payload(schema, before, {"Flow": new_flow})
    print(f"Sending control payload via cmd 0x90 at t=0: Flow {original_flow} -> {new_flow}")
    print(f"p0 payload: {len(payload)} bytes, first 16: {payload[:16].hex()}\n")

    start = time.monotonic()
    await session._send(CMD_SERIAL_TRANSMIT_REQUEST, payload)

    print("Logging every frame for 10s (no filtering)...")
    end = start + 10
    while time.monotonic() < end:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        try:
            frame = await asyncio.wait_for(session._recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        t = time.monotonic() - start
        decoded_hint = ""
        if len(frame.payload) >= 1 and frame.payload[0] in (0x02, 0x03, 0x04):
            try:
                v = schema.decode_status(frame.payload[1:])
                decoded_hint = f"  [Flow={v['Flow']} Freq={v['Frequency']}]"
            except Exception:
                pass
        print(f"[t={t:5.2f}s] command={frame.command:#06x} flag={frame.flag:#x} "
              f"len={len(frame.payload)} payload[:24]={frame.payload[:24]!r}{decoded_hint}")

    print("\nDone logging. Doing a clean fresh read_status()...")
    await asyncio.sleep(0.5)
    after = await session.read_status()
    after_values = schema.decode_status(after)
    print(f"Final: Flow={after_values['Flow']} Frequency={after_values['Frequency']}")

    await session.close()


if __name__ == "__main__":
    asyncio.run(main())
