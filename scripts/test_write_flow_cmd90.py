"""Phase 4 alt hypothesis: send the p0 control payload (action=0x01) via
command 0x90 (the same 'transmit' command used for reads) instead of 0x93,
since PROTOCOL.md's only worked example uses 0x90 for every p0 action and
0x93 got a non-standard echoed-DID response with no effect."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from jebao_gizwits.control import build_control_payload
from jebao_gizwits.protocol import CMD_SERIAL_TRANSMIT_REQUEST, CMD_SERIAL_TRANSMIT_RESPONSE
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
    print(f"Before: Flow={original_flow} Frequency={before_values['Frequency']}")

    new_flow = original_flow + 5 if original_flow <= 95 else original_flow - 5
    print(f"\nSending control payload via cmd 0x90: Flow {original_flow} -> {new_flow}")

    payload = build_control_payload(schema, before, {"Flow": new_flow})
    print(f"p0 payload: {len(payload)} bytes, first 16: {payload[:16].hex()}")

    await session._send(CMD_SERIAL_TRANSMIT_REQUEST, payload)
    for i in range(5):
        frame = await session._recv()
        print(f"[resp {i}] command={frame.command:#06x} flag={frame.flag:#x} payload[:20]={frame.payload[:20]!r}")
        if frame.command == CMD_SERIAL_TRANSMIT_RESPONSE:
            break

    await asyncio.sleep(1)
    after = await session.read_status()
    after_values = schema.decode_status(after)
    print(f"\nAfter:  Flow={after_values['Flow']} Frequency={after_values['Frequency']}")

    if after_values["Flow"] == new_flow:
        print("\n*** WRITE CONFIRMED via cmd 0x90 ***")
        print(f"Restoring Flow to {original_flow}...")
        restore_payload = build_control_payload(schema, after, {"Flow": original_flow})
        await session._send(CMD_SERIAL_TRANSMIT_REQUEST, restore_payload)
        for i in range(5):
            frame = await session._recv()
            if frame.command == CMD_SERIAL_TRANSMIT_RESPONSE:
                break
        await asyncio.sleep(1)
        final = await session.read_status()
        print(f"Final Flow={schema.decode_status(final)['Flow']} (should be {original_flow})")
    else:
        print(f"\n*** cmd 0x90 write did not take either: still Flow={after_values['Flow']} ***")

    await session.close()


if __name__ == "__main__":
    asyncio.run(main())
