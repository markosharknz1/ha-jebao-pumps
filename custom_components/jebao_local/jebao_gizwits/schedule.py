"""Decode/encode for the 48-slot AutoTimeNN schedule attributes.

Each AutoTimeNN attribute (AutoTime00..AutoTime47) is 8 raw bytes:
[start_hour, start_minute, end_hour, end_minute, mode, flow, frequency,
pulse_tide]. This was never confirmed against a live capture (unlike
SwitchON/Flow/Frequency - see control.py), but is confirmed from three
independent static sources, all agreeing byte-for-byte (see SPEC.md's
schedule-programming phase):

1. The wavemaker app template's own JS (com.gizwits.rn.jiebao.zaolang/
   index.js) has real `encode`/`decode` functions operating on this exact
   field order, plus a labeled default-slot object:
   `{startHour:0, startMinute:0, endHour:24, endMinute:0, mode:1,
   frequency:100, flow:100, pulseTide:0, id:1}`.
2. The bundled schema JSON's own byte_offset spacing: AutoTime00..AutoTime47
   sit at byte_offset 8, 16, 24, ... 384 - exactly 8 bytes apart, back to
   back, len=8 each.
3. The schema JSON's own (Chinese) `desc` string for every AutoTimeNN
   attribute spells out Byte0..Byte7 in this same order.

A slot is "unused" when all 8 bytes are 0x00 or all 8 bytes are 0xEE - both
are treated as "no time period configured here" by the app's own
schedule-editor code (the same JS file's `At()` function filters out both
before decoding).
"""
from __future__ import annotations

from dataclasses import dataclass

SLOT_LEN = 8
SLOT_COUNT = 48

_ALL_ZERO = bytes(SLOT_LEN)
_ALL_ERASED = bytes([0xEE] * SLOT_LEN)
_DISABLED_SENTINELS = (_ALL_ZERO, _ALL_ERASED)


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
    pulse_tide: int


def slot_attr_name(index: int) -> str:
    if not (0 <= index < SLOT_COUNT):
        raise ValueError(f"slot index out of range 0..{SLOT_COUNT - 1}: {index}")
    return f"AutoTime{index:02d}"


def is_slot_enabled(raw: bytes) -> bool:
    return bytes(raw) not in _DISABLED_SENTINELS


def decode_slot(index: int, raw: bytes) -> ScheduleSlot | None:
    """None if the slot is unused (see module docstring for the two sentinels)."""
    raw = bytes(raw)
    if len(raw) != SLOT_LEN:
        raise ValueError(f"expected {SLOT_LEN} bytes, got {len(raw)}")
    if not is_slot_enabled(raw):
        return None
    start_hour, start_minute, end_hour, end_minute, mode, flow, frequency, pulse_tide = raw
    return ScheduleSlot(
        index=index,
        start_hour=start_hour,
        start_minute=start_minute,
        end_hour=end_hour,
        end_minute=end_minute,
        mode=mode,
        flow=flow,
        frequency=frequency,
        pulse_tide=pulse_tide,
    )


def encode_slot(
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    mode: int,
    flow: int,
    frequency: int = 0,
    pulse_tide: int = 0,
) -> bytes:
    if not (0 <= start_hour <= 24):
        raise ValueError(f"start_hour out of range 0..24: {start_hour}")
    if not (0 <= end_hour <= 24):
        raise ValueError(f"end_hour out of range 0..24: {end_hour}")
    if not (0 <= start_minute <= 59):
        raise ValueError(f"start_minute out of range 0..59: {start_minute}")
    if not (0 <= end_minute <= 59):
        raise ValueError(f"end_minute out of range 0..59: {end_minute}")
    for field_name, value in (("mode", mode), ("flow", flow), ("frequency", frequency), ("pulse_tide", pulse_tide)):
        if not (0 <= value <= 255):
            raise ValueError(f"{field_name} out of byte range 0..255: {value}")

    raw = bytes((start_hour, start_minute, end_hour, end_minute, mode, flow, frequency, pulse_tide))
    if raw in _DISABLED_SENTINELS:
        raise ValueError(
            "this combination of fields is indistinguishable from a disabled "
            "slot (all-zero) - use clear_slot() to disable a slot instead"
        )
    return raw


def clear_slot() -> bytes:
    return _ALL_ZERO
