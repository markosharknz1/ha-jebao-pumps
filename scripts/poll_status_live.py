"""Diagnostic: poll read_status() repeatedly, printing raw byte0/1 and decoded
key fields each time, so a live toggle in the app can be correlated with the
exact byte that changes."""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from jebao_gizwits.schema import load
from jebao_gizwits.session import GizwitsSession

PUMP_IP = "192.168.1.77"
POLL_SECONDS = 35
INTERVAL = 1.0


async def main():
    schema = load(ROOT / "fixtures" / "datapoint_schema.json")

    session = GizwitsSession(PUMP_IP)
    await session.connect()
    await session.authenticate()
    print(f"Connected+authenticated. Polling every {INTERVAL}s for {POLL_SECONDS}s.")
    print("Toggle the pump / change mode / flow in the app now.\n")

    start = time.monotonic()
    last_raw = None
    n = 0
    while time.monotonic() - start < POLL_SECONDS:
        raw = await session.read_status()
        n += 1
        if raw != last_raw:
            values = schema.decode_status(raw)
            print(f"[{n:3d}] t={time.monotonic()-start:5.1f}s  byte0={raw[0]:#04x} byte1={raw[1]:#04x}  "
                  f"SwitchON={values['SwitchON']!r} AutoMode={values['AutoMode']!r} Mode={values['Mode']!r} "
                  f"Flow={values['Flow']} Freq={values['Frequency']}")
            last_raw = raw
        await asyncio.sleep(INTERVAL)

    await session.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
