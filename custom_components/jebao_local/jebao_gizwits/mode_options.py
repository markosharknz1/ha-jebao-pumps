"""Recover value->label maps for uint8 attributes that are really enums.

Some products declare an attribute as `uint8` (min 0, max 255) when it is
actually a small enumeration - the vendor only writes the choices down in
the attribute's free-text `desc`. `Mode` is the notable case: it is a
proper `enum` on 13 bundled products but a bare `uint8` on 3, including
the Local Wavemaker Pro. Those got a 0-255 number box in Home Assistant,
and the Lovelace card rendered an empty dropdown for them.

The numbering is genuinely per-product and cannot be assumed - the
Wavemaker Pro and the DC Pump Pro disagree on what 0, 1 and 2 mean:

    Wavemaker Pro:  0.pulse  1.sine  2.constant flow  3.random ...
    DC Pump Pro:    0.constant flow  1.pulse  2.sine  3.random ...

so the desc is parsed per attribute rather than mapped from a table.

Two desc styles appear in the catalog:
    "0.脉冲造浪 1.正弦造浪 2.恒流造浪 ..."                 (dot form)
    "模式，互斥关系，0为Manual Control，1为Schedule，..."   (为 form)

Anything that doesn't parse cleanly yields no options at all, so an
unrecognised format degrades to the plain number entity rather than
inventing labels.
"""
from __future__ import annotations

import re

from .enum_translations import translate

# "0.脉冲造浪" / "4.喂食模式" - label runs to the next "<digits>." or the end.
_DOT_FORM = re.compile(r"(?<![\d.])(\d{1,3})\.\s*([^\d]+?)(?=\s*(?:\d{1,3}\.)|$)")
# "0为Manual Control，1为Schedule" - label runs to the next separator.
_WEI_FORM = re.compile(r"(\d{1,3})\s*为\s*([^，,。;；]+)")

# Values above this are not plausible choices for a small enumeration -
# guards against picking numbers out of prose (ranges, units, byte
# offsets) and calling them modes.
_MAX_PLAUSIBLE_VALUE = 32


def parse_mode_options(desc: str) -> dict[int, str]:
    """value -> English label, or {} when the desc isn't an enumeration."""
    if not desc:
        return {}

    matches = _WEI_FORM.findall(desc) or _DOT_FORM.findall(desc)
    options: dict[int, str] = {}
    for raw_value, raw_label in matches:
        value = int(raw_value)
        label = raw_label.strip(" ，,。.;；:：")
        if not label or value > _MAX_PLAUSIBLE_VALUE or value in options:
            return {}  # ambiguous or out of range - don't guess
        options[value] = translate(label)

    # A single "0.something" is far more likely to be prose than a choice
    # list, and a set that doesn't start at 0 suggests we mis-parsed.
    if len(options) < 2 or min(options) != 0:
        return {}
    return options


def mode_options_for(attr) -> dict[int, str]:
    """Options for a schema Attr, or {} if it isn't a labelled uint8."""
    if attr.data_type != "uint8" or not attr.writable:
        return {}
    return parse_mode_options(attr.desc)
