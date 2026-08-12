"""Recovering value->label maps for uint8 attrs that are really enums.

`Mode` is a declared `enum` on 13 bundled products but a bare `uint8` on
3 - the choices only exist in the attribute's free-text desc. Those got a
0-255 number box, and the Lovelace card rendered an empty dropdown for
them, which is how this was noticed on a real Wavemaker Pro
(SPEC.md Phase 27).
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local.jebao_gizwits.mode_options import (  # noqa: E402
    mode_options_for,
    parse_mode_options,
)
from custom_components.jebao_local.jebao_gizwits.schema import (  # noqa: E402
    known_product_keys,
    load_by_product_key,
)

WAVEMAKER_PRO = "50dbc92221fd4d33ae69a1fedd43b555"
DC_PUMP_PRO = "0696a19599bc484f8e1866f5ccf4ee7e"
DD_LIGHT = "877bbcc8df614559864db4de18014286"
BASE_WAVEMAKER = "54114ccdac1e41c0bb17e222887c07ba"


def _mode(product_key, name="Mode"):
    schema = load_by_product_key(product_key)
    return mode_options_for(schema.by_name(name))


def test_wavemaker_pro_mode_is_recovered_and_translated():
    assert _mode(WAVEMAKER_PRO) == {
        0: "Pulse wave", 1: "Sine wave", 2: "Constant flow", 3: "Random wave",
        4: "Tidal", 5: "Nutrient delivery", 6: "Circulation", 7: "Feed mode",
        8: "Custom wave",
    }


def test_numbering_is_per_product_not_assumed():
    """The whole reason this is parsed rather than tabulated: these two
    products disagree about what 0, 1 and 2 mean."""
    pro, dc = _mode(WAVEMAKER_PRO), _mode(DC_PUMP_PRO)
    assert pro[0] == "Pulse wave" and dc[0] == "Constant flow"
    assert pro[2] == "Constant flow" and dc[2] == "Sine wave"


def test_the_other_desc_format_also_parses():
    # "0为Manual Control，1为Schedule，..." rather than "0.x 1.y"
    assert _mode(DD_LIGHT) == {0: "Manual Control", 1: "Schedule", 2: "Acclimation Mode"}


def test_automode_is_picked_up_too():
    assert _mode(WAVEMAKER_PRO, "AutoMode")[4] == "Tidal"


def test_a_declared_enum_is_left_alone():
    """Products whose Mode is a real enum already get a select from the
    enum list; they must not be double-handled here."""
    schema = load_by_product_key(BASE_WAVEMAKER)
    assert mode_options_for(schema.by_name("Mode")) == {}


def test_prose_descs_do_not_produce_bogus_options():
    for desc in [
        "",
        "Byte0: start hour, Byte1: start minute",
        "range is 5~100",
        "0.only one choice",          # too few to be a choice list
        "1.first 2.second",           # doesn't start at 0
        "0.a 0.b",                    # duplicate value
    ]:
        assert parse_mode_options(desc) == {}, desc


def test_no_false_positives_across_the_whole_catalog():
    """Only genuinely-enumerated uint8 attrs should match - a parser that
    grabbed numbers out of prose would quietly mislabel controls."""
    matched = {
        (load_by_product_key(pk).name_en, a.name)
        for pk in known_product_keys()
        for a in load_by_product_key(pk).attrs
        if mode_options_for(a)
    }
    assert {n for n, _ in matched} == {
        "Local Wavemaker Pro (WiFi+BLE)",
        "DC Pump Pro (WiFi+BLE)",
        "D-D Marine Light (WiFi+BLE)",
    }, matched


def test_every_recovered_label_is_english():
    for pk in known_product_keys():
        for a in load_by_product_key(pk).attrs:
            for value, label in mode_options_for(a).items():
                assert label.isascii(), f"{a.name} {value} left untranslated: {label}"
