"""Round-trip and edge-case tests for jebao_gizwits/schedule.py, the 48-slot
AutoTimeNN encode/decode - see SPEC.md's schedule-programming phase for
where the 8-byte format was confirmed (the app's own encode/decode JS, the
schema's own byte_offset spacing, and the schema's own Byte0..Byte7 desc
text, all three agreeing).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from custom_components.jebao_local.jebao_gizwits.schedule import (
    SLOT_COUNT,
    clear_slot,
    decode_slot,
    encode_slot,
    is_slot_enabled,
    slot_attr_name,
)


def test_encode_matches_the_real_app_default_slot_example():
    # AutoTime00's default value in the wavemaker app's own template JS is
    # [12, 31, 0, 0, 1, 100] (frequency/pulse_tide unset -> 0 in this repo's
    # extraction), see tools/ha_test/autotime_context.txt.
    raw = encode_slot(start_hour=12, start_minute=31, end_hour=0, end_minute=0, mode=1, flow=100)
    assert raw == bytes([12, 31, 0, 0, 1, 100, 0, 0])


def test_decode_matches_the_app_new_slot_default_object():
    # The same template's `Dt` object - the app's own "new slot" default:
    # {startHour:0, startMinute:0, endHour:24, endMinute:0, mode:1,
    # frequency:100, flow:100, pulseTide:0}.
    raw = encode_slot(start_hour=0, start_minute=0, end_hour=24, end_minute=0, mode=1, flow=100, frequency=100)
    slot = decode_slot(1, raw)
    assert slot.index == 1
    assert slot.start_hour == 0
    assert slot.end_hour == 24
    assert slot.mode == 1
    assert slot.flow == 100
    assert slot.frequency == 100
    assert slot.pulse_tide == 0


def test_round_trip_all_fields():
    raw = encode_slot(
        start_hour=8, start_minute=15, end_hour=20, end_minute=45,
        mode=2, flow=75, frequency=50, pulse_tide=3,
    )
    slot = decode_slot(5, raw)
    assert (slot.start_hour, slot.start_minute, slot.end_hour, slot.end_minute) == (8, 15, 20, 45)
    assert (slot.mode, slot.flow, slot.frequency, slot.pulse_tide) == (2, 75, 50, 3)


def test_all_zero_bytes_decode_as_disabled():
    assert decode_slot(0, clear_slot()) is None
    assert not is_slot_enabled(clear_slot())


def test_all_0xee_bytes_decode_as_disabled():
    erased = bytes([0xEE] * 8)
    assert decode_slot(0, erased) is None
    assert not is_slot_enabled(erased)


def test_a_real_configured_slot_is_enabled():
    raw = encode_slot(start_hour=8, start_minute=0, end_hour=20, end_minute=0, mode=0, flow=50)
    assert is_slot_enabled(raw)


def test_encoding_the_all_zero_combination_is_rejected():
    # Would be indistinguishable from a disabled slot on read-back.
    with pytest.raises(ValueError):
        encode_slot(start_hour=0, start_minute=0, end_hour=0, end_minute=0, mode=0, flow=0)


@pytest.mark.parametrize("field,value", [
    ("start_hour", 25),
    ("end_hour", 25),
    ("start_minute", 60),
    ("end_minute", 60),
])
def test_out_of_range_time_fields_are_rejected(field, value):
    kwargs = dict(start_hour=1, start_minute=0, end_hour=2, end_minute=0, mode=0, flow=50)
    kwargs[field] = value
    with pytest.raises(ValueError):
        encode_slot(**kwargs)


def test_decode_requires_exactly_eight_bytes():
    with pytest.raises(ValueError):
        decode_slot(0, bytes(7))


def test_slot_attr_name_bounds():
    assert slot_attr_name(0) == "AutoTime00"
    assert slot_attr_name(47) == "AutoTime47"
    assert SLOT_COUNT == 48
    with pytest.raises(ValueError):
        slot_attr_name(48)
    with pytest.raises(ValueError):
        slot_attr_name(-1)
