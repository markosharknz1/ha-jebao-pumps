#!/usr/bin/env python3
"""Read a pump's raw status and watch which bits actually move.

Built for a bug report that can't be reproduced without the hardware:
on a 4-head doser, turning a channel ON propagated between the vendor app
and Home Assistant, but turning it OFF propagated in neither direction.

That symptom has two halves and this separates them:

  * If the app turns a channel off and `--watch` shows *no bit changing*,
    the device is not reporting the change over LAN - a read/firmware
    issue, and nothing in Home Assistant can fix it.
  * If a bit does change, `--watch` names which attribute it belongs to.
    If that is a different attribute than expected, the schema's bit
    mapping is wrong for this product.
  * `--set NAME=off` then shows whether our write moves that same bit.

Read-only unless you pass --set.

    python tools/diagnose_device.py                 # find devices
    python tools/diagnose_device.py 10.42.1.90      # one status dump
    python tools/diagnose_device.py 10.42.1.90 --watch
    python tools/diagnose_device.py 10.42.1.90 --set channe1=off
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "jebao_local"))

from jebao_gizwits.control import build_control_payload  # noqa: E402
from jebao_gizwits.discovery import discover, discover_one  # noqa: E402
from jebao_gizwits.schema import load_by_product_key  # noqa: E402
from jebao_gizwits.session import GizwitsSession  # noqa: E402


def bit_owner(schema, abs_bit: int) -> str:
    """Which attribute owns this absolute bit position, per the schema."""
    for a in schema.attrs:
        p = a.position
        if p.unit != "bit":
            continue
        start = p.byte_offset * 8 + p.bit_offset
        if start <= abs_bit < start + p.len:
            return f"{a.name}[{abs_bit - start}]" if p.len > 1 else a.name
    return "(unmapped)"


def changed_bits(before: bytes, after: bytes):
    for i in range(min(len(before), len(after))):
        diff = before[i] ^ after[i]
        while diff:
            bit = (diff & -diff).bit_length() - 1
            yield i * 8 + bit, (after[i] >> bit) & 1
            diff &= diff - 1


async def read_status(ip: str) -> bytes:
    session = GizwitsSession(ip)
    try:
        await asyncio.wait_for(session.connect(), 8)
        await asyncio.wait_for(session.authenticate(), 8)
        return await asyncio.wait_for(session.read_status(), 8)
    finally:
        await session.close()


def dump(schema, raw: bytes) -> None:
    values = schema.decode_status(raw)
    bools = [(a.name, values.get(a.name)) for a in schema.attrs
             if a.data_type == "bool" and a.writable]
    print("  writable booleans:")
    for name, value in bools:
        print(f"    {'ON ' if value else 'off'}  {name}")
    print(f"  first 8 status bytes: {' '.join(f'{b:08b}' for b in raw[:8])}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ip", nargs="?", help="pump IP; omit to list what's on the LAN")
    ap.add_argument("--watch", action="store_true", help="poll and report every bit that changes")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--set", dest="assign", metavar="NAME=on|off",
                    help="write one boolean, showing the status before and after")
    args = ap.parse_args()

    if not args.ip:
        for d in sorted(await discover(timeout=6.0), key=lambda x: x.ip):
            try:
                name = load_by_product_key(d.product_key).name_en
            except KeyError:
                name = f"UNSUPPORTED {d.product_key}"
            print(f"{d.ip:<16} {name:<40} fw={d.wifi_firmware}")
        return 0

    device = await discover_one(args.ip, timeout=6.0)
    if device is None:
        print(f"{args.ip} did not answer discovery")
        return 1
    schema = load_by_product_key(device.product_key)
    print(f"{schema.name_en}  ({device.did})  fw={device.wifi_firmware}\n")

    raw = await read_status(args.ip)
    dump(schema, raw)

    if args.assign:
        name, _, value = args.assign.partition("=")
        want = value.strip().lower() in ("on", "true", "1", "yes")
        attr = schema.by_name(name)
        payload = build_control_payload(schema, {name: want})
        print(f"\nwriting {name}={'ON' if want else 'off'}")
        print(f"  schema says: byte_offset={attr.position.byte_offset} "
              f"bit_offset={attr.position.bit_offset} -> status bit "
              f"{attr.position.byte_offset * 8 + attr.position.bit_offset}")
        print(f"  p0 payload: {payload[:12].hex()}...")
        session = GizwitsSession(args.ip)
        try:
            await session.connect()
            await session.authenticate()
            await session.send_control(payload)
        finally:
            await session.close()
        await asyncio.sleep(2.0)
        after = await read_status(args.ip)
        moved = list(changed_bits(raw, after))
        print("\nafter the write:")
        if not moved:
            print("  NOTHING CHANGED - the device ignored this write")
        for abs_bit, now in moved:
            print(f"  bit {abs_bit:<4} -> {now}   ({bit_owner(schema, abs_bit)})")
        return 0

    if args.watch:
        print(f"\nwatching every {args.interval}s - now toggle the channel in the app "
              f"(Ctrl+C to stop)\n")
        previous = raw
        while True:
            await asyncio.sleep(args.interval)
            try:
                current = await read_status(args.ip)
            except Exception as err:  # noqa: BLE001
                print(f"  read failed: {type(err).__name__}: {err}")
                continue
            moved = list(changed_bits(previous, current))
            if moved:
                for abs_bit, now in moved:
                    print(f"  bit {abs_bit:<4} -> {now}   ({bit_owner(schema, abs_bit)})")
                previous = current
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nstopped")
