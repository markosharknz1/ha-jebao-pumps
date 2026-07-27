"""Compatibility check: jebao_local coexisting in the same Home Assistant
instance as another real-world integration (markosharknz1/aipai-light-ha),
without vendoring that repo's source into this one.

Looks for a sibling checkout of aipai-light-ha next to this repo (i.e.
`../aipai-light-ha` relative to this file's repo root), or a path given via
the AIPAI_LIGHT_HA_PATH env var. Skips cleanly if neither is found - this is
meant to be a real, rerunnable check for anyone who happens to have both
repos checked out, not a hard CI requirement.

Requires the `homeassistant` package installed (`pip install homeassistant`)
and, for aipai_light specifically, `paho-mqtt` (its manifest.json
requirement). Run with pytest's socket/homeassistant-plugin auto-registration
disabled - see the module docstring in the original investigation for why:
    pytest tests/test_ha_integration_compat.py -p no:socket -p no:homeassistant
(the `pytest-homeassistant-custom-component` package, if installed, has a
real Windows-specific event-loop conflict when its plugin auto-loads - not
needed here anyway, this file only does plain imports/schema checks.)
"""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _find_aipai_checkout() -> Path | None:
    env_path = os.environ.get("AIPAI_LIGHT_HA_PATH")
    if env_path and (Path(env_path) / "custom_components" / "aipai_light").is_dir():
        return Path(env_path)
    sibling = REPO_ROOT.parent / "aipai-light-ha"
    if (sibling / "custom_components" / "aipai_light").is_dir():
        return sibling
    return None


AIPAI_PATH = _find_aipai_checkout()
pytestmark = pytest.mark.skipif(
    AIPAI_PATH is None,
    reason="No aipai-light-ha checkout found - set AIPAI_LIGHT_HA_PATH or "
    "check it out at ../aipai-light-ha next to this repo to run this test",
)


@pytest.fixture(autouse=True, scope="module")
def _add_aipai_to_path():
    if AIPAI_PATH is not None:
        sys.path.insert(0, str(AIPAI_PATH))
    yield


def _manifest(base: Path, domain: str) -> dict:
    return json.loads((base / "custom_components" / domain / "manifest.json").read_text(encoding="utf-8"))


def test_manifests_are_valid_and_domains_dont_collide():
    jebao = _manifest(REPO_ROOT, "jebao_local")
    aipai = _manifest(AIPAI_PATH, "aipai_light")

    for m, folder in [(jebao, "jebao_local"), (aipai, "aipai_light")]:
        assert m["domain"] == folder
        assert m.get("config_flow") is True
        assert "iot_class" in m

    assert jebao["domain"] != aipai["domain"]


def test_no_requirement_version_conflicts():
    jebao = _manifest(REPO_ROOT, "jebao_local")
    aipai = _manifest(AIPAI_PATH, "aipai_light")
    jebao_pkgs = {r.split(">=")[0].split("==")[0] for r in jebao.get("requirements", [])}
    aipai_pkgs = {r.split(">=")[0].split("==")[0] for r in aipai.get("requirements", [])}
    assert not (jebao_pkgs & aipai_pkgs), f"overlapping requirements: {jebao_pkgs & aipai_pkgs}"


def test_jebao_local_modules_import_cleanly():
    importlib.import_module("custom_components.jebao_local")
    for mod in ["entity", "coordinator", "config_flow", "switch", "select", "number", "binary_sensor", "panel", "fan"]:
        importlib.import_module(f"custom_components.jebao_local.{mod}")


def test_aipai_light_modules_import_cleanly():
    for mod in ["const", "config_flow", "hub", "switch", "select", "number", "sensor", "button"]:
        importlib.import_module(f"custom_components.aipai_light.{mod}")


def test_aipai_light_config_flow_schema_constructs():
    from custom_components.aipai_light.config_flow import STEP_USER_SCHEMA

    validated = STEP_USER_SCHEMA({"serial": "TEST123"})
    assert validated["serial"] == "TEST123"


def test_both_integrations_use_distinct_network_resources():
    from custom_components.jebao_local.const import TCP_PORT as jebao_port

    assert jebao_port == 12416
    aipai_manifest = _manifest(AIPAI_PATH, "aipai_light")
    assert aipai_manifest["iot_class"] == "cloud_polling"
