"""Decode/encode for the 48-slot AutoTimeNN schedule attributes.

Each AutoTimeNN attribute holds one daily time period. The byte layout is
**per product**, not universal - confirmed the hard way when a real user's
"Local Wavemaker Pro" turned up with 9-byte slots whose fields differ from
the 8-byte base wavemaker (SPEC.md Phase 18/19):

    base wavemaker (8 bytes, byte_offset 8..391):
      [start_h, start_m, end_h, end_m, mode, flow, frequency, pulse_tide]
    wavemaker Pro (9 bytes, byte_offset 11..):
      [start_h, start_m, end_h, end_m, mode, flow, frequency, feed_time,
       cust_wave_freq]

...and the mode numbering differs too (base: 0 stop / 1 classic / 2 sine /
3 random / 4 constant flow / 5 feeding; Pro: 0 pulse / 1 sine / 2 constant
flow / 3 random / 4 tidal / 5 nutrient / 6 circulation / 7 feeding /
8 custom). So nothing here hardcodes a length or a field list: the layout
is taken from `len` in the product's own schema, and the trailing field
names come from FIELDS_BY_LEN, which is derived from each product
schema's own `desc` text (which spells out Byte0..ByteN explicitly).

Sources for the shared leading fields (all three agreeing byte-for-byte,
see SPEC.md's schedule-programming phase):
1. The vendor app's own JS `encode`/`decode` functions for the base
   wavemaker template.
2. The schema JSON's byte_offset spacing (slots sit back to back).
3. The schema JSON's own `desc` string on every AutoTimeNN attribute,
   which enumerates Byte0..ByteN - the authority for the per-product
   trailing fields, since it differs between products.

A slot is "unused" when all its bytes are 0x00 or all are 0xEE - both are
treated as "no time period configured" by the app's own schedule-editor
code (its `At()` function filters out both before decoding).
"""
from __future__ import annotations

from dataclasses import dataclass, field

SLOT_COUNT = 48

# Fields after the shared [start_h, start_m, end_h, end_m, mode, flow,
# frequency] prefix, keyed by the slot length the schema declares. Taken
# from each product schema's own Byte0..ByteN desc text.
_COMMON_FIELDS = ("start_hour", "start_minute", "end_hour", "end_minute", "mode", "flow", "frequency")
FIELDS_BY_LEN: dict[int, tuple[str, ...]] = {
    8: _COMMON_FIELDS + ("pulse_tide",),
    9: _COMMON_FIELDS + ("feed_time", "cust_wave_freq"),
}

# Kept for callers that predate variable-length support; the base
# wavemaker this project was built against uses 8-byte slots.
SLOT_LEN = 8


def slot_fields(slot_len: int) -> tuple[str, ...]:
    try:
        return FIELDS_BY_LEN[slot_len]
    except KeyError:
        raise ValueError(
            f"unknown schedule slot length {slot_len} - this product's AutoTimeNN "
            f"layout hasn't been decoded yet (known: {sorted(FIELDS_BY_LEN)})"
        ) from None


@dataclass(frozen=True)
class ScheduleSlot:
    index: int
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    mode: int
    flow: int
    frequency: int
    # Product-specific trailing fields - only the ones this product's slot
    # layout actually defines are populated (see FIELDS_BY_LEN).
    pulse_tide: int | None = None
    feed_time: int | None = None
    cust_wave_freq: int | None = None
    slot_len: int = SLOT_LEN

    def as_dict(self) -> dict[str, int]:
        """Only the fields this slot actually has, for UI/attribute use -
        so a Pro slot doesn't advertise a pulse_tide it has no concept of."""
        out = {"index": self.index}
        for name in slot_fields(self.slot_len):
            out[name] = getattr(self, name)
        return out


def slot_attr_name(index: int) -> str:
    if not (0 <= index < SLOT_COUNT):
        raise ValueError(f"slot index out of range 0..{SLOT_COUNT - 1}: {index}")
    return f"AutoTime{index:02d}"


def schedule_slot_len(schema) -> int | None:
    """Slot length this product declares, or None if it has no schedule."""
    try:
        return schema.by_name(slot_attr_name(0)).position.len
    except KeyError:
        return None


def is_slot_enabled(raw: bytes) -> bool:
    raw = bytes(raw)
    if not raw:
        return False
    return raw != bytes(len(raw)) and raw != bytes([0xEE] * len(raw))


def decode_slot(index: int, raw: bytes) -> ScheduleSlot | None:
    """None if the slot is unused (see module docstring for the sentinels)."""
    raw = bytes(raw)
    names = slot_fields(len(raw))  # raises for a layout we haven't decoded
    if not is_slot_enabled(raw):
        return None
    values = dict(zip(names, raw))
    return ScheduleSlot(index=index, slot_len=len(raw), **values)


def encode_slot(
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    mode: int,
    flow: int,
    frequency: int = 0,
    pulse_tide: int = 0,
    feed_time: int = 0,
    cust_wave_freq: int = 0,
    slot_len: int = SLOT_LEN,
) -> bytes:
    if not (0 <= start_hour <= 24):
        raise ValueError(f"start_hour out of range 0..24: {start_hour}")
    if not (0 <= end_hour <= 24):
        raise ValueError(f"end_hour out of range 0..24: {end_hour}")
    if not (0 <= start_minute <= 59):
        raise ValueError(f"start_minute out of range 0..59: {start_minute}")
    if not (0 <= end_minute <= 59):
        raise ValueError(f"end_minute out of range 0..59: {end_minute}")

    supplied = {
        "start_hour": start_hour, "start_minute": start_minute,
        "end_hour": end_hour, "end_minute": end_minute,
        "mode": mode, "flow": flow, "frequency": frequency,
        "pulse_tide": pulse_tide, "feed_time": feed_time,
        "cust_wave_freq": cust_wave_freq,
    }
    names = slot_fields(slot_len)
    for name in names:
        value = supplied[name]
        if not (0 <= value <= 255):
            raise ValueError(f"{name} out of byte range 0..255: {value}")

    raw = bytes(supplied[name] for name in names)
    if not is_slot_enabled(raw):
        raise ValueError(
            "this combination of fields is indistinguishable from a disabled "
            "slot (all-zero) - use clear_slot() to disable a slot instead"
        )
    return raw


def clear_slot(slot_len: int = SLOT_LEN) -> bytes:
    return bytes(slot_len)
