"""Phase 4 checkpoint: the smallest possible real write - nudge Flow by a few
percent, confirm via read-back, then restore the original value."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from jebao_gizwits.control import build_control_payload
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
    print(f"Before: Flow={original_flow} Frequency={before_values['Frequency']} AutoMode={before_values['AutoMode']!r}")

    new_flow = original_flow + 5 if original_flow <= 95 else original_flow - 5
    print(f"\nSending control frame: Flow {original_flow} -> {new_flow}")

    payload = build_control_payload(schema, before, {"Flow": new_flow})
    print(f"Control payload: {len(payload)} bytes, first 16: {payload[:16].hex()}")

    resp = await session.send_control(payload)
    print(f"Control response payload: {resp!r}")

    await asyncio.sleep(1)
    after = await session.read_status()
    after_values = schema.decode_status(after)
    print(f"\nAfter:  Flow={after_values['Flow']} Frequency={after_values['Frequency']} AutoMode={after_values['AutoMode']!r}")

    if after_values["Flow"] == new_flow:
        print("\n*** WRITE CONFIRMED: Flow changed as commanded. ***")
    else:
        print(f"\n*** WRITE DID NOT TAKE: expected Flow={new_flow}, still {after_values['Flow']} ***")

    print(f"\nRestoring Flow to original value {original_flow}...")
    restore_payload = build_control_payload(schema, after, {"Flow": original_flow})
    resp = await session.send_control(restore_payload)
    await asyncio.sleep(1)
    final = await session.read_status()
    final_values = schema.decode_status(final)
    print(f"Final: Flow={final_values['Flow']} (should be {original_flow})")

    await session.close()


if __name__ == "__main__":
    asyncio.run(main())
