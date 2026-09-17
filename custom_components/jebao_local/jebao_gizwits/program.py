"""Compile a sequence of "run at this speed for this long" steps into the
pump's own 48 daily timer slots.

The pump already has everything needed for this - `schedule.py`'s
AutoTimeNN slots each hold a start time, an end time, a mode and a speed -
but they are addressed one at a time by absolute clock time, which is a
miserable way to express "100% for four hours, then 40% for six". This
module does the arithmetic: durations in, wall-clock slots out.

Doing it this way (rather than having Home Assistant change the speed on a
timer) means the program lives **on the pump**. It keeps running if Home
Assistant is off, updating, or unplugged - which for a tank is the whole
point.

Only bytes 0-5 of a slot are set here (start h/m, end h/m, mode, speed).
That is deliberate: those six bytes carry the same meaning on every
schedule-capable product, whether its slots are 6, 8 or 9 bytes long,
while the trailing bytes differ per product (see schedule.py). So a
program compiled here is valid on all of them.

Two wrinkles the arithmetic has to handle:

* **Midnight.** The schedule is a daily loop, so a step running 22:00-02:00
  is not one slot - it is 22:00-24:00 plus 00:00-02:00. Periods are split
  at midnight rather than rejected.
* **Repeat.** "100% for 2h then 50% for 2h" usually means all day, not
  once. `repeat=True` tiles the pattern around the clock, truncating
  whatever step is running when it arrives back at the start time.
"""
from __future__ import annotations

from dataclasses import dataclass

from .schedule import SLOT_COUNT

MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True)
class Step:
    """One leg of a program: hold `speed` for `minutes`."""

    minutes: int
    speed: int
    mode: int | None = None       # None = keep whatever mode the device is in
    frequency: int | None = None  # ignored by products without a frequency byte


@dataclass(frozen=True)
class CompiledSlot:
    """A single wall-clock period, ready for schedule.encode_slot()."""

    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    mode: int
    flow: int
    frequency: int

    def as_kwargs(self) -> dict[str, int]:
        return {
            "start_hour": self.start_hour,
            "start_minute": self.start_minute,
            "end_hour": self.end_hour,
            "end_minute": self.end_minute,
            "mode": self.mode,
            "flow": self.flow,
            "frequency": self.frequency,
        }


def parse_time_of_day(value: str) -> int:
    """'08:30' -> minutes since midnight. Accepts H:MM and HH:MM:SS."""
    parts = str(value).strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"'{value}' is not a time like 08:30")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"'{value}' is not a time like 08:30") from None
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ValueError(f"'{value}' is not a time like 08:30")
    return hour * 60 + minute


def _hm(minutes: int) -> tuple[int, int]:
    """Minutes-since-midnight to (hour, minute), with 1440 as 24:00.

    The end of a period that runs to midnight is 24:00, not 00:00 - the
    slot encoder allows hour 24 for exactly this, and it also keeps such a
    slot from encoding as all-zero bytes, which would read back as
    "disabled".
    """
    return divmod(minutes, 60) if minutes < MINUTES_PER_DAY else (24, 0)


def compile_program(
    steps: list[Step],
    start: int = 0,
    repeat: bool = False,
    default_mode: int = 0,
    default_frequency: int = 0,
    slot_count: int = SLOT_COUNT,
) -> list[CompiledSlot]:
    """Turn duration-based steps into absolute daily slots.

    `start` is minutes since midnight. Raises ValueError with a message
    meant to be shown to the user, since every one of these is something
    they can fix in the call.
    """
    if not steps:
        raise ValueError("a program needs at least one step")
    for index, step in enumerate(steps, start=1):
        if step.minutes < 1:
            raise ValueError(f"step {index} has no duration - each step needs at least 1 minute")
        if not (0 <= step.speed <= 255):
            raise ValueError(f"step {index}: speed {step.speed} is outside 0-255")

    total = sum(step.minutes for step in steps)
    if not repeat and total > MINUTES_PER_DAY:
        hours = total / 60
        raise ValueError(
            f"the steps add up to {hours:.1f} hours, which is more than a day - "
            "the pump's schedule repeats every 24 hours, so a program has to fit in one"
        )

    # Walk the day laying steps end to end, wrapping past midnight, until
    # the program is used up (or the day is full, when repeating).
    limit = MINUTES_PER_DAY if repeat else total
    compiled: list[CompiledSlot] = []
    cursor = start
    placed = 0
    index = 0
    while placed < limit:
        step = steps[index % len(steps)]
        index += 1
        length = min(step.minutes, limit - placed)  # truncate the last one
        mode = default_mode if step.mode is None else step.mode
        frequency = default_frequency if step.frequency is None else step.frequency

        begin = cursor % MINUTES_PER_DAY
        finish = begin + length
        # A period crossing midnight is two slots, not one.
        pieces = [(begin, finish)] if finish <= MINUTES_PER_DAY else [
            (begin, MINUTES_PER_DAY), (0, finish - MINUTES_PER_DAY)
        ]
        for piece_start, piece_end in pieces:
            sh, sm = _hm(piece_start)
            eh, em = _hm(piece_end)
            compiled.append(
                CompiledSlot(sh, sm, eh, em, mode, step.speed, frequency)
            )
        cursor += length
        placed += length

    if len(compiled) > slot_count:
        raise ValueError(
            f"this program needs {len(compiled)} timer slots but the pump only has "
            f"{slot_count} - use fewer or longer steps"
            + (", or turn repeat off" if repeat else "")
        )
    return compiled


def describe(compiled: list[CompiledSlot], mode_labels: dict[int, str] | None = None) -> list[str]:
    """Human-readable lines for logging and the service response."""
    labels = mode_labels or {}
    out = []
    for slot in compiled:
        mode = labels.get(slot.mode, str(slot.mode))
        out.append(
            f"{slot.start_hour:02d}:{slot.start_minute:02d}-"
            f"{slot.end_hour:02d}:{slot.end_minute:02d} {mode} at {slot.flow}"
        )
    return out
