"""Regression test: build_control_payload must reproduce real captured write
frames byte-for-byte. These captures came from logcat hex dumps of the real
Jebao app (GizSDKLog "Gizwits p0:" debug output) sending SwitchON true/false
via the Android emulator - see SPEC.md Phase 4 for how they were obtained.

The captured .bin files include the outer TCP frame (magic + varint length +
flag + cmd + 4-byte sequence number = 13 bytes) before the p0 payload -
build_control_payload only returns the p0 payload, so the outer frame is
stripped for comparison. The captures were truncated by logcat's line-length
limit (only ~19 bytes of the 409-byte p0 payload were visible), so this test
only checks that visible prefix - full-length regression coverage would need
a fresh capture with an unbounded log sink.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jebao_gizwits.control import build_control_payload
from jebao_gizwits.schema import load

FIXTURES = ROOT / "fixtures"


def _p0_payload(captured_frame: bytes) -> bytes:
    return captured_frame[13:]


def test_switchon_false_matches_capture():
    schema = load(FIXTURES / "datapoint_schema.json")
    captured = (FIXTURES / "captured_writes" / "switchon_false.bin").read_bytes()
    expected = _p0_payload(captured)
    built = build_control_payload(schema, {"SwitchON": False})
    assert built[: len(expected)] == expected, (
        f"expected {expected.hex()}, got {built[:len(expected)].hex()}"
    )


def test_switchon_true_matches_capture():
    schema = load(FIXTURES / "datapoint_schema.json")
    captured = (FIXTURES / "captured_writes" / "switchon_true.bin").read_bytes()
    expected = _p0_payload(captured)
    built = build_control_payload(schema, {"SwitchON": True})
    assert built[: len(expected)] == expected, (
        f"expected {expected.hex()}, got {built[:len(expected)].hex()}"
    )


if __name__ == "__main__":
    test_switchon_false_matches_capture()
    test_switchon_true_matches_capture()
    print("All control.py regression tests passed.")
