"""Tests for jebao_gizwits/clock.py - the YMDData/HMSData encoding confirmed
from the vendor app's own sendLocalTime function (SPEC.md Phase 17).
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from custom_components.jebao_local.jebao_gizwits.clock import (
    HMS_ATTR,
    YMD_ATTR,
    decode_clock,
    encode_clock,
)


def test_encode_matches_the_apps_own_sendlocaltime_shape():
    # The app splits "2026-08-03 14:30:05" into YMD [20,26,8,3] and
    # HMS [0,14,30,5] - byte1 of HMS is a literal zero.
    enc = encode_clock(datetime(2026, 8, 3, 14, 30, 5))
    assert enc[YMD_ATTR] == bytes([20, 26, 8, 3])
    assert enc[HMS_ATTR] == bytes([0, 14, 30, 5])


def test_round_trip():
    now = datetime(2031, 12, 31, 23, 59, 58)
    enc = encode_clock(now)
    clock = decode_clock(enc[YMD_ATTR], enc[HMS_ATTR])
    assert clock.as_datetime() == now


def test_never_set_all_zero_clock_decodes_but_is_not_a_valid_datetime():
    # A pump whose clock was never set reports all-zero bytes (the app
    # template's own default state) - year 0, month 0, day 0 is not a real
    # date, and as_datetime() reports that rather than inventing one.
    clock = decode_clock(bytes(4), bytes(4))
    assert clock.year == 0 and clock.month == 0
    assert clock.as_datetime() is None


def test_decode_rejects_wrong_lengths():
    with pytest.raises(ValueError):
        decode_clock(bytes(3), bytes(4))
    with pytest.raises(ValueError):
        decode_clock(bytes(4), bytes(5))
