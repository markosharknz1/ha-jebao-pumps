"""Regression test for find_entry_by_mac() - the actual matching logic
behind DHCP-based IP recovery (config_flow.py:async_step_dhcp). Extracted
into a standalone function specifically so this is testable with plain
fake config entries, without needing to fake HA's ConfigFlow/hass
internals just to exercise it - see the docstring on the function itself.

Needs the real `homeassistant` package (config_flow.py imports
homeassistant.config_entries for the ConfigEntry type at module level,
same as every other platform file in this integration) - skips cleanly if
it isn't installed.
"""
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local.config_flow import find_entry_by_mac  # noqa: E402


@dataclass
class FakeEntry:
    """Just enough of a ConfigEntry for find_entry_by_mac's purposes -
    it only ever reads .data."""

    data: dict = field(default_factory=dict)


def test_matches_on_normalized_mac_regardless_of_case_or_colons():
    entries = [FakeEntry({"mac": "24ec4aeea4d4"})]
    # HA's DHCP watcher delivers colon-separated, but hand-authored config
    # entry data (or an older schema) might not be - both must match.
    assert find_entry_by_mac(entries, "24:EC:4A:EE:A4:D4") is entries[0]
    assert find_entry_by_mac(entries, "24ec4aeea4d4") is entries[0]


def test_no_match_returns_none():
    entries = [FakeEntry({"mac": "24ec4aeea4d4"})]
    assert find_entry_by_mac(entries, "aabbccddeeff") is None


def test_empty_incoming_mac_never_matches():
    entries = [FakeEntry({"mac": "24ec4aeea4d4"})]
    assert find_entry_by_mac(entries, "") is None


def test_entries_missing_mac_are_skipped_not_falsely_matched():
    # Entries created before CONF_MAC was added to config entry data have
    # no "mac" key at all - a naive "" == "" comparison would wrongly treat
    # an empty incoming MAC as matching every such entry.
    entries = [FakeEntry({}), FakeEntry({"mac": "24ec4aeea4d4"})]
    assert find_entry_by_mac(entries, "") is None
    assert find_entry_by_mac(entries, "24ec4aeea4d4") is entries[1]


def test_picks_the_right_entry_among_several():
    entries = [
        FakeEntry({"mac": "111111111111"}),
        FakeEntry({"mac": "222222222222"}),
        FakeEntry({"mac": "333333333333"}),
    ]
    assert find_entry_by_mac(entries, "22:22:22:22:22:22") is entries[1]
