"""Tests for compiling a speed sequence into the pump's daily timer slots.

The arithmetic here is the whole feature - a step landing on the wrong
side of midnight, or an old slot left behind by a previous program, means
a tank running at the wrong speed for hours without anyone watching. So
the awkward cases (midnight wrap, repeat truncation, slot exhaustion) get
more attention than the happy path.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local.jebao_gizwits.program import (  # noqa: E402
    MINUTES_PER_DAY,
    Step,
    compile_program,
    parse_time_of_day,
)
from custom_components.jebao_local.jebao_gizwits.schedule import (  # noqa: E402
    decode_slot,
    encode_slot,
    is_slot_enabled,
    slot_fields,
)


def _spans(compiled):
    """(start, end) in minutes since midnight, for readable assertions."""
    return [
        (s.start_hour * 60 + s.start_minute, s.end_hour * 60 + s.end_minute)
        for s in compiled
    ]


# --- parse_time_of_day -----------------------------------------------------


@pytest.mark.parametrize("text,expected", [("00:00", 0), ("08:30", 510), ("23:59", 1439), ("7:05", 425), ("08:30:00", 510)])
def test_times_of_day_parse(text, expected):
    assert parse_time_of_day(text) == expected


@pytest.mark.parametrize("text", ["", "8", "24:00", "08:60", "breakfast", "-1:00"])
def test_nonsense_times_are_rejected(text):
    with pytest.raises(ValueError):
        parse_time_of_day(text)


# --- the headline case -----------------------------------------------------


def test_the_asked_for_case_100_percent_then_40_percent():
    compiled = compile_program(
        [Step(minutes=240, speed=100), Step(minutes=360, speed=40)],
        start=parse_time_of_day("08:00"),
    )
    assert _spans(compiled) == [(8 * 60, 12 * 60), (12 * 60, 18 * 60)]
    assert [s.flow for s in compiled] == [100, 40]


def test_steps_run_back_to_back_with_no_gaps():
    compiled = compile_program([Step(90, 100), Step(45, 60), Step(30, 20)], start=60)
    spans = _spans(compiled)
    for (_, end), (next_start, _) in zip(spans, spans[1:]):
        assert end == next_start


def test_hours_and_minutes_are_the_same_thing():
    by_hours = compile_program([Step(minutes=120, speed=80)])
    by_minutes = compile_program([Step(minutes=120, speed=80)])
    assert _spans(by_hours) == _spans(by_minutes)


# --- midnight --------------------------------------------------------------


def test_a_step_crossing_midnight_becomes_two_slots():
    compiled = compile_program([Step(240, 100)], start=22 * 60)
    assert _spans(compiled) == [(22 * 60, MINUTES_PER_DAY), (0, 2 * 60)]
    assert all(s.flow == 100 for s in compiled)


def test_a_step_ending_exactly_at_midnight_uses_hour_24_not_0():
    compiled = compile_program([Step(120, 100)], start=22 * 60)
    assert len(compiled) == 1
    assert (compiled[0].end_hour, compiled[0].end_minute) == (24, 0)


def test_a_slot_ending_at_midnight_still_encodes_as_enabled():
    # 00:00-24:00 at mode 0 speed 0 would be all-zero bytes - i.e.
    # indistinguishable from an empty slot - if end were encoded as 00:00.
    compiled = compile_program([Step(MINUTES_PER_DAY, 0)])
    raw = encode_slot(**compiled[0].as_kwargs(), slot_len=8)
    assert is_slot_enabled(raw)


def test_a_full_day_from_midnight_is_one_slot():
    compiled = compile_program([Step(MINUTES_PER_DAY, 55)])
    assert _spans(compiled) == [(0, MINUTES_PER_DAY)]


# --- repeat ----------------------------------------------------------------


def test_repeat_tiles_the_pattern_across_the_whole_day():
    compiled = compile_program([Step(120, 100), Step(120, 50)], repeat=True)
    assert len(compiled) == 12  # six 4-hour cycles
    assert _spans(compiled)[0] == (0, 120)
    assert _spans(compiled)[-1] == (22 * 60, MINUTES_PER_DAY)
    assert [s.flow for s in compiled[:4]] == [100, 50, 100, 50]


def test_repeat_covers_the_day_with_no_gaps_even_when_the_cycle_doesnt_divide_evenly():
    # 7-hour cycle into 24 hours: the last step is truncated, not dropped.
    compiled = compile_program([Step(240, 100), Step(180, 40)], repeat=True)
    spans = _spans(compiled)
    assert spans[0][0] == 0
    covered = sum(end - start for start, end in spans)
    assert covered == MINUTES_PER_DAY


def test_repeat_from_a_start_time_wraps_back_round_to_it():
    compiled = compile_program([Step(360, 100)], start=8 * 60, repeat=True)
    spans = _spans(compiled)
    assert spans[0][0] == 8 * 60
    assert sum(end - start for start, end in spans) == MINUTES_PER_DAY


def test_without_repeat_the_rest_of_the_day_is_left_alone():
    compiled = compile_program([Step(60, 100)], start=8 * 60)
    assert _spans(compiled) == [(8 * 60, 9 * 60)]


# --- rejections ------------------------------------------------------------


def test_a_program_longer_than_a_day_is_refused_with_a_useful_message():
    with pytest.raises(ValueError, match="more than a day"):
        compile_program([Step(MINUTES_PER_DAY, 100), Step(60, 50)])


def test_too_many_periods_for_the_pumps_48_slots_is_refused():
    with pytest.raises(ValueError, match="48"):
        compile_program([Step(1, 100), Step(1, 50)], repeat=True)


def test_an_empty_program_is_refused():
    with pytest.raises(ValueError, match="at least one step"):
        compile_program([])


def test_a_zero_length_step_is_refused_naming_the_step():
    with pytest.raises(ValueError, match="step 2"):
        compile_program([Step(60, 100), Step(0, 50)])


def test_an_out_of_range_speed_is_refused_naming_the_step():
    with pytest.raises(ValueError, match="step 1"):
        compile_program([Step(60, 300)])


# --- modes -----------------------------------------------------------------


def test_a_step_without_a_mode_inherits_the_devices_current_one():
    compiled = compile_program([Step(60, 100)], default_mode=4)
    assert compiled[0].mode == 4


def test_a_step_can_override_the_mode_per_step():
    compiled = compile_program([Step(60, 100, mode=2), Step(60, 40)], default_mode=4)
    assert [s.mode for s in compiled] == [2, 4]


def test_a_mode_override_survives_a_midnight_split():
    compiled = compile_program([Step(120, 100, mode=7)], start=23 * 60)
    assert [s.mode for s in compiled] == [7, 7]


# --- encoding against every real slot layout -------------------------------


@pytest.mark.parametrize("slot_len", [6, 8, 9])
def test_compiled_slots_encode_and_decode_on_every_layout(slot_len):
    """Only bytes 0-5 are set, so a program is valid on all three layouts."""
    compiled = compile_program(
        [Step(240, 100), Step(360, 40)], start=8 * 60, default_mode=1
    )
    for index, slot in enumerate(compiled):
        raw = encode_slot(**slot.as_kwargs(), slot_len=slot_len)
        assert len(raw) == slot_len
        decoded = decode_slot(index, raw)
        assert decoded is not None
        assert decoded.start_hour == slot.start_hour
        assert decoded.end_hour == slot.end_hour
        assert decoded.mode == slot.mode
        assert decoded.flow == slot.flow


def test_six_byte_layout_has_no_frequency_field():
    assert "frequency" not in slot_fields(6)
    assert slot_fields(6) == (
        "start_hour", "start_minute", "end_hour", "end_minute", "mode", "flow",
    )


# --- slot mode tables, from each product's own desc -------------------------

import json  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.schema import load_by_product_key  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.schedule import slot_mode_options  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.enum_translations import translate  # noqa: E402

_SCHEMA_DIR = REPO_ROOT / "custom_components/jebao_local/jebao_gizwits/schemas"


def _schedule_products():
    for path in sorted(_SCHEMA_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        names = {a["name"] for a in raw["entities"][0]["attrs"]}
        if "AutoTime00" in names:
            yield raw["product_key"]


def test_every_schedule_product_publishes_a_slot_mode_table():
    keys = list(_schedule_products())
    assert len(keys) == 13
    for key in keys:
        assert slot_mode_options(load_by_product_key(key)), key


def test_base_wavemaker_slot_modes_are_offset_from_live_modes():
    table = slot_mode_options(load_by_product_key("54114ccdac1e41c0bb17e222887c07ba"))
    assert translate(table[0]) == "Stop"
    assert translate(table[4]) == "Constant flow"


def test_dc_pump_pro_uses_the_slot_desc_not_automode():
    # AutoMode says 0 = constant flow; the slot byte itself says 0 = pulse.
    key = next(k for k in _schedule_products()
               if load_by_product_key(k).name_en.startswith("DC Pump Pro"))
    table = slot_mode_options(load_by_product_key(key))
    assert translate(table[0]) == "Pulse wave"
    assert translate(table[2]) == "Constant flow"
