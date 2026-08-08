"""Guard the translation files against the mistakes hassfest rejects.

Home Assistant requires every translation *key* to be a `[a-z0-9-_]+`
slug. An earlier attempt to translate select options used the vendor's raw
Chinese enum values as keys, which is invalid - and because it only failed
in CI's hassfest job, it shipped in three pushes before being noticed
(SPEC.md Phase 21). This runs the same key check locally, and keeps
strings.json and translations/en.json from drifting apart, since they're
maintained as copies of each other.
"""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = REPO_ROOT / "custom_components" / "jebao_local"
STRINGS = INTEGRATION / "strings.json"
EN = INTEGRATION / "translations" / "en.json"

SLUG = re.compile(r"^[a-z0-9]([a-z0-9_-]*[a-z0-9])?$")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _all_keys(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield ".".join(path + (key,)), key
            yield from _all_keys(value, path + (key,))


def test_every_translation_key_is_a_valid_slug():
    bad = [
        dotted for dotted, key in _all_keys(_load(STRINGS)) if not SLUG.match(key)
    ]
    assert not bad, (
        "hassfest rejects non-slug translation keys "
        f"(need [a-z0-9-_]+, no leading/trailing -_): {bad}"
    )


def test_en_translations_match_strings_json():
    # They're kept as byte-identical copies; a drift here means one was
    # edited and the other forgotten.
    assert _load(EN) == _load(STRINGS), "translations/en.json is out of sync with strings.json"


def test_every_service_field_is_documented():
    """Every field in services.yaml needs a strings.json entry, and vice
    versa - hassfest checks this and it's easy to add one and forget the
    other (which is exactly what happened with the Pro's slot fields)."""
    import yaml

    services = yaml.safe_load((INTEGRATION / "services.yaml").read_text(encoding="utf-8"))
    strings = _load(STRINGS)["services"]

    assert set(services) == set(strings), (
        f"services.yaml vs strings.json service mismatch: "
        f"{set(services) ^ set(strings)}"
    )
    for name, spec in services.items():
        yaml_fields = set((spec or {}).get("fields", {}))
        doc_fields = set(strings[name].get("fields", {}))
        assert yaml_fields == doc_fields, (
            f"{name}: fields differ between services.yaml and strings.json: "
            f"{yaml_fields ^ doc_fields}"
        )
