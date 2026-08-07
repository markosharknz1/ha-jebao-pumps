"""Chinese -> English translations for enum-attribute *values* (Mode,
AutoMode, Linkage, CALSet, etc.) across the 29 bundled product schemas.

This is a translation this project had to do itself. The vendor app was
checked first for a real, authoritative source instead of guessing: its
main JS bundle (index.android.bundle) embeds 53 per-template
`language:{en:{...}, zh:{...}, ja:{...}}` i18n objects, and does have
genuine English strings for plenty of things (company info, ALARM_TEXT_1..7
fault messages like "Controller overvoltage"). But every `DP-<AttrName>`
key - the datapoint labels, which is where these enum values would be
translated too - has *identical* text under `en` and `zh`: the vendor's own
English locale silently falls back to untranslated Chinese for every
datapoint label and enum value in the app itself, confirmed by extracting
and diffing all 53 blocks, not assumed from a spot check. So there is no
vendor-provided English source for these specific strings to adopt - the
mapping below is this project's own translation of a small, closed set of
domain terms (wave modes, calibration steps, master/slave linkage, a
day/night light cycle) gathered from every enum attribute's declared
`enum` list across all 29 bundled schemas.

`translate()` is a safe no-op for anything not in the table (a future
product's not-yet-seen enum value passes through unchanged rather than
raising), since this project's schema catalog can grow.
"""
from __future__ import annotations

ENUM_TRANSLATIONS: dict[str, str] = {
    # Wave modes (Mode, mode, AutoMode)
    "经典造浪": "Classic wave",
    "正弦造浪": "Sine wave",
    "随机造浪": "Random wave",
    "恒流造浪": "Constant flow",
    # AutoMode extras (scheduled-mode state)
    "停机": "Stop",
    "喂食": "Feeding",
    "自动": "Auto",
    # mode (light/pump day-night cycle + control source)
    "夜晚": "Night",
    "定时": "Timer",
    "手动": "Manual",
    "日出": "Sunrise",
    "日落": "Sunset",
    "早晨": "Morning",
    "白天": "Day",
    # Linkage (multi-device master/slave grouping). The Pro wavemaker
    # splits plain "slave" into synchronous/asynchronous - two pumps
    # running in step, versus deliberately out of step to avoid a
    # standing wave.
    "主机": "Master",
    "从机": "Slave",
    "独立": "Independent",
    "同步从机": "Slave (synchronised)",
    "异步从机": "Slave (alternating)",
    # CALSet (calibration step selector)
    "校准1": "Calibration 1",
    "校准2": "Calibration 2",
    "校准3": "Calibration 3",
    "校准4": "Calibration 4",
}


def translate(value: str) -> str:
    return ENUM_TRANSLATIONS.get(value, value)
