"""Every form a config flow shows must supply the placeholders its own
translated description uses, and only name error keys that exist.

Home Assistant does not fail loudly on either: a missing placeholder renders
the raw formatjs error ("Translation [formatjs Error: MISSING_VALUE] ...")
in the dialog, and an unknown error key renders as the bare key. Both shipped
in v0.7.1 - the "nothing found" branch reused the `discover` step, whose
description is written around {found} and {already}, and passed {reason}
instead, with errors={"base": "no_devices_found"} that had no string at all.

These tests drive the real flow handler, so they catch a step that shows a
form without the placeholders its description needs.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.jebao_local import config_flow as cf  # noqa: E402
from custom_components.jebao_local.const import DOMAIN  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.discovery import DiscoveredDevice  # noqa: E402

STRINGS = json.loads(
    (ROOT / "custom_components" / "jebao_local" / "strings.json").read_text(encoding="utf-8")
)
PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")


class _FakeHass:
    """Only the one hass method the flow actually uses."""

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _flow(devices, configured=()):
    flow = cf.JebaoLocalConfigFlow()
    flow.hass = _FakeHass()
    flow.flow_id = "test"
    flow.handler = DOMAIN

    async def _discover(timeout):  # noqa: ARG001
        return list(devices)

    cf.discover = _discover  # module-level import in config_flow
    flow._async_current_entries = lambda: list(configured)  # noqa: SLF001
    return flow


def _device(did="did1", ip="10.0.0.5"):
    return DiscoveredDevice(
        ip=ip, did=did, mac_hex="aabbccddeeff", wifi_firmware="1.0",
        # A product_key the bundled schemas do not know, so the test does not
        # depend on which models happen to ship - discovery_label falls back
        # to "Unsupported model (...)", which is still a valid label.
        product_key="0" * 32, api_server="", version="", extra=b"",
    )


def _check(result):
    """Assert a form result is renderable: placeholders present, errors known."""
    step = result["step_id"]
    strings = STRINGS["config"]["step"].get(step)
    assert strings is not None, f"step {step!r} has no strings.json entry"

    needed = set()
    for text in (strings.get("description", ""), strings.get("title", "")):
        needed |= set(PLACEHOLDER.findall(text))
    supplied = set(result.get("description_placeholders") or {})
    assert needed <= supplied, (
        f"step {step!r} description uses {sorted(needed - supplied)} but the flow "
        f"supplies {sorted(supplied)} - Home Assistant renders a MISSING_VALUE error"
    )

    known_errors = set(STRINGS["config"].get("error", {}))
    used = set((result.get("errors") or {}).values())
    assert used <= known_errors, (
        f"step {step!r} uses error key(s) {sorted(used - known_errors)} with no "
        "strings.json entry - Home Assistant renders the raw key"
    )


@pytest.fixture(autouse=True)
def _restore_discover():
    original = cf.discover
    yield
    cf.discover = original


@pytest.mark.asyncio
async def test_discovery_with_devices_is_renderable():
    result = await _flow([_device()]).async_step_discover()
    assert result["step_id"] == "discover"
    _check(result)


@pytest.mark.asyncio
async def test_discovery_with_nothing_found_is_renderable():
    result = await _flow([]).async_step_discover()
    assert result["step_id"] == "no_devices"
    _check(result)
    assert "LAN discovery" in result["description_placeholders"]["reason"]


@pytest.mark.asyncio
async def test_discovery_with_only_configured_devices_is_renderable():
    class _Entry:
        data = {"did": "did1"}

    result = await _flow([_device("did1")], configured=[_Entry()]).async_step_discover()
    assert result["step_id"] == "no_devices"
    _check(result)
    # Distinguishable from finding nothing at all - that distinction is the
    # whole reason this branch has its own message.
    assert "already configured" in result["description_placeholders"]["reason"]


@pytest.mark.asyncio
async def test_submitting_no_devices_scans_again():
    flow = _flow([])
    calls = []
    original = cf.discover

    async def _counting(timeout):
        calls.append(timeout)
        return await original(timeout)

    cf.discover = _counting
    await flow.async_step_no_devices({})
    assert calls, "submitting the no-devices form should rescan"
