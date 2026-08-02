"""Every distinct enum value across all 29 bundled product schemas must have
an English translation - this is a small, closed vocabulary (wave modes,
calibration steps, master/slave linkage, a day/night light cycle), gathered
by inspecting every enum attribute's declared `enum` list across the whole
catalog (see jebao_gizwits/enum_translations.py's module docstring for why
this had to be this project's own translation - the vendor app's own
English locale doesn't actually translate these).

This test exists to catch a future bundled schema introducing a new enum
value this project hasn't translated yet.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local.jebao_gizwits.enum_translations import (  # noqa: E402
    ENUM_TRANSLATIONS,
    translate,
)
from custom_components.jebao_local.jebao_gizwits.schema import (  # noqa: E402
    known_product_keys,
    load_by_product_key,
)


def _all_enum_values() -> set[str]:
    values: set[str] = set()
    for product_key in known_product_keys():
        schema = load_by_product_key(product_key)
        for attr in schema.attrs:
            if attr.data_type == "enum" and attr.enum_values:
                values.update(attr.enum_values)
    return values


def test_every_bundled_enum_value_has_a_translation():
    missing = sorted(v for v in _all_enum_values() if v not in ENUM_TRANSLATIONS)
    assert not missing, f"untranslated enum values found in bundled schemas: {missing}"


def test_translate_is_a_safe_noop_for_unknown_values():
    assert translate("some future value not yet catalogued") == "some future value not yet catalogued"


def test_translate_returns_known_mappings():
    assert translate("经典造浪") == "Classic wave"
    assert translate("主机") == "Master"
