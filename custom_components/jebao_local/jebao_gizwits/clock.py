"""Encode/decode for the pump's internal clock datapoints (YMDData/HMSData).

The 48-slot AutoTimeNN schedule (schedule.py) fires off this clock, and
nothing but the vendor app ever set it - so schedules on a pump that lost
power or drifted fire at the wrong time until something re-syncs it.

Format confirmed from the vendor app's own `sendLocalTime` function in the
wavemaker template JS (reference/jebao-apk/decompiled/.../
com.gizwits.rn.jiebao.zaolang/index.js), with a second independent call
site (the manual date-picker path) agreeing, and both matching the schema
JSON's own desc text (SPEC.md Phase 17):

    YMDData: [year // 100, year % 100, month (1-based), day]
    HMSData: [0, hour, minute, second]

e.g. 2026-08-03 14:30:05 -> YMDData [20, 26, 8, 3], HMSData [0, 14, 30, 5].
The app sends *local* time, not UTC - the pump has no timezone concept, its
schedule slots are wall-clock times.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

YMD_ATTR = "YMDData"
HMS_ATTR = "HMSData"


@dataclass(frozen=True)
class DeviceClock:
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int

    def as_datetime(self) -> datetime | None:
        """None if the device clock holds an impossible date (e.g. the
        all-zero state of a pump whose clock was never set)."""
        try:
            return datetime(self.year, self.month, self.day, self.hour, self.minute, self.second)
        except ValueError:
            return None


def encode_clock(now: datetime) -> dict[str, bytes]:
    """`now` should be local wall-clock time - the pump's schedule slots
    are wall-clock times and the vendor app syncs local time too."""
    if not (0 <= now.year <= 9999):
        raise ValueError(f"year out of range 0..9999: {now.year}")
    return {
        YMD_ATTR: bytes((now.year // 100, now.year % 100, now.month, now.day)),
        HMS_ATTR: bytes((0, now.hour, now.minute, now.second)),
    }


def decode_clock(ymd: bytes, hms: bytes) -> DeviceClock:
    ymd = bytes(ymd)
    hms = bytes(hms)
    if len(ymd) != 4 or len(hms) != 4:
        raise ValueError(f"expected 4+4 bytes, got {len(ymd)}+{len(hms)}")
    return DeviceClock(
        year=ymd[0] * 100 + ymd[1],
        month=ymd[2],
        day=ymd[3],
        hour=hms[1],
        minute=hms[2],
        second=hms[3],
    )
