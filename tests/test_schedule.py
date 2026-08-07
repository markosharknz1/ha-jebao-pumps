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
    schedule_slot_len,
    slot_attr_name,
    slot_fields,
)
from custom_components.jebao_local.jebao_gizwits.schema import load_by_product_key

BASE_WAVEMAKER_KEY = "54114ccdac1e41c0bb17e222887c07ba"
PRO_WAVEMAKER_KEY = "50dbc92221fd4d33ae69a1fedd43b555"


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


# --- per-product slot layouts (SPEC.md Phase 19) -------------------------
# The Local Wavemaker Pro uses 9-byte slots whose trailing fields differ
# from the base wavemaker's 8-byte ones. Hardcoding 8 anywhere would make
# that product's schedule sensor raise instead of decode.


def test_slot_length_comes_from_each_products_own_schema():
    assert schedule_slot_len(load_by_product_key(BASE_WAVEMAKER_KEY)) == 8
    assert schedule_slot_len(load_by_product_key(PRO_WAVEMAKER_KEY)) == 9


def test_product_without_a_schedule_reports_no_slot_length():
    light = load_by_product_key("efc08baa6b0a4de38d4bc9bce04ad350")
    assert schedule_slot_len(light) is None


def test_the_two_layouts_have_different_trailing_fields():
    # Shared prefix through frequency, then they diverge - this is the
    # whole reason the layout can't be assumed.
    assert slot_fields(8)[:7] == slot_fields(9)[:7]
    assert slot_fields(8)[7:] == ("pulse_tide",)
    assert slot_fields(9)[7:] == ("feed_time", "cust_wave_freq")


def test_pro_nine_byte_slot_round_trips():
    raw = encode_slot(
        start_hour=9, start_minute=30, end_hour=18, end_minute=45,
        mode=4, flow=80, frequency=60, feed_time=3, cust_wave_freq=55,
        slot_len=9,
    )
    assert raw == bytes([9, 30, 18, 45, 4, 80, 60, 3, 55])
    slot = decode_slot(2, raw)
    assert slot.slot_len == 9
    assert (slot.feed_time, slot.cust_wave_freq) == (3, 55)
    assert slot.pulse_tide is None  # the Pro has no such field


def test_decode_picks_the_layout_from_the_byte_count():
    eight = decode_slot(0, bytes([8, 0, 20, 0, 1, 75, 50, 2]))
    assert eight.pulse_tide == 2 and eight.feed_time is None
    nine = decode_slot(0, bytes([8, 0, 20, 0, 1, 75, 50, 2, 33]))
    assert nine.feed_time == 2 and nine.cust_wave_freq == 33
    assert nine.pulse_tide is None


def test_as_dict_only_reports_fields_the_product_actually_has():
    nine = decode_slot(1, bytes([9, 30, 18, 45, 4, 80, 60, 3, 55]))
    d = nine.as_dict()
    assert "cust_wave_freq" in d and "pulse_tide" not in d
    eight = decode_slot(1, bytes([8, 0, 20, 0, 1, 75, 50, 2]))
    assert "pulse_tide" in eight.as_dict() and "cust_wave_freq" not in eight.as_dict()


def test_nine_byte_disabled_sentinels():
    assert decode_slot(0, bytes(9)) is None
    assert decode_slot(0, bytes([0xEE] * 9)) is None
    assert clear_slot(9) == bytes(9)


def test_an_undecoded_slot_length_is_refused_not_guessed():
    with pytest.raises(ValueError):
        decode_slot(0, bytes(7))
    with pytest.raises(ValueError):
        encode_slot(1, 0, 2, 0, mode=1, flow=50, slot_len=12)
