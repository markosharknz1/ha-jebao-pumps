"""Phase 3 checkpoint: connect, authenticate, read status from the live pump.

Listens for a while after the initial read in case the first reply is stale
and the device pushes an updated status shortly after (node-ph803w
PROTOCOL.md notes this happens ~6s after the first 90/91 exchange).
"""
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from jebao_gizwits.protocol import CMD_SERIAL_TRANSMIT_RESPONSE
from jebao_gizwits.schema import load
from jebao_gizwits.session import GizwitsSession

PUMP_IP = "192.168.1.77"
LISTEN_SECONDS = 60


def print_key_values(values):
    for name in ("SwitchON", "PulseTide", "FeedSwitch", "TimerON", "Mode", "Linkage", "Flow", "Frequency"):
        print(f"    {name:15s} = {values.get(name)!r}")


async def main():
    schema = load(ROOT / "fixtures" / "datapoint_schema.json")

    session = GizwitsSession(PUMP_IP)
    await session.connect()
    print(f"Connected to {PUMP_IP}:12416")

    passcode = await session.authenticate()
    print(f"Authenticated, passcode={passcode!r}")

    raw = await session.read_status()
    print(f"\n[initial read] {len(raw)} bytes, first 16 bytes: {raw[:16].hex()}")
    values = schema.decode_status(raw)
    print_key_values(values)

    print(f"\nListening for pushed updates for {LISTEN_SECONDS}s (toggle the pump in the app now if you want)...")
    end = asyncio.get_event_loop().time() + LISTEN_SECONDS
    n = 0
    while True:
        remaining = end - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            frame = await asyncio.wait_for(session._recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        n += 1
        if frame.command == CMD_SERIAL_TRANSMIT_RESPONSE and frame.payload:
            raw = frame.payload[1:]
            print(f"\n[push #{n}] {len(raw)} bytes, first 16 bytes: {raw[:16].hex()}")
            if len(raw) == schema.status_size_bytes:
                values = schema.decode_status(raw)
                print_key_values(values)
                (ROOT / "fixtures" / f"status_push{n}.bin").write_bytes(raw)
        else:
            print(f"\n[frame #{n}] command={frame.command:#06x} payload={frame.payload!r}")

    await session.close()


if __name__ == "__main__":
    asyncio.run(main())
