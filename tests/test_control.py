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

# The binary-write tests below exercise the AutoTimeNN schedule support,
# which only exists in the copy vendored under custom_components/jebao_local
# - this repo has two copies of jebao_gizwits, the root-level one above being
# the original standalone library the SwitchON capture tests were written
# against, which hasn't been kept in lockstep with later additions.
from custom_components.jebao_local.jebao_gizwits.control import (
    attr_flags_size as _ha_attr_flags_size,
    build_control_payload as _ha_build_control_payload,
)
from custom_components.jebao_local.jebao_gizwits.schema import load as _ha_load_schema

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


def test_binary_write_places_bytes_directly_at_byte_offset():
    """AutoTimeNN (data_type "binary") isn't covered by a real capture (see
    control.py's own docstring), but reuses the byte-type placement rule
    that IS confirmed for uint8: placed directly at byte_offset, no
    reversal. AutoTime00 is byte_offset=8, len=8 in the schema."""
    schema = _ha_load_schema(FIXTURES / "datapoint_schema.json")
    raw_slot = bytes([12, 31, 0, 0, 1, 100, 0, 0])
    built = _ha_build_control_payload(schema, {"AutoTime00": raw_slot})

    action_byte_len = 1
    flags_len = _ha_attr_flags_size(schema)
    vals_start = action_byte_len + flags_len
    byte_offset = schema.by_name("AutoTime00").position.byte_offset
    assert built[vals_start + byte_offset : vals_start + byte_offset + 8] == raw_slot


def test_binary_write_wrong_length_is_rejected():
    schema = _ha_load_schema(FIXTURES / "datapoint_schema.json")
    try:
        _ha_build_control_payload(schema, {"AutoTime00": bytes(7)})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a short binary value")


def test_binary_write_does_not_disturb_other_attrs():
    schema = _ha_load_schema(FIXTURES / "datapoint_schema.json")
    raw_slot = bytes([12, 31, 0, 0, 1, 100, 0, 0])
    built_with_switch = _ha_build_control_payload(schema, {"SwitchON": True, "AutoTime00": raw_slot})
    built_switch_only = _ha_build_control_payload(schema, {"SwitchON": True})
    flags_len = _ha_attr_flags_size(schema)
    vals_start = 1 + flags_len
    byte_offset = schema.by_name("AutoTime00").position.byte_offset
    # The vals region before AutoTime00's own bytes (including SwitchON's
    # bit-packed region) must be identical whether or not AutoTime00 was
    # also written - only the flags byte (checked separately below) and
    # AutoTime00's own slot should differ.
    assert built_with_switch[vals_start : vals_start + byte_offset] == built_switch_only[vals_start : vals_start + byte_offset]
    assert built_with_switch[vals_start + byte_offset : vals_start + byte_offset + 8] == raw_slot
    assert built_switch_only[vals_start + byte_offset : vals_start + byte_offset + 8] == bytes(8)


def test_uint16_round_trips_big_endian():
    """uint16 datapoints (light Colour_Kelvin, dosing-pump liquid volumes)
    were dropped entirely before Phase 20 - number.py only handled uint8.
    Big-endian matches every other multi-byte field in this protocol
    (discovery.py's ">H", the frame header) but is not itself confirmed
    against a captured uint16 write - see SPEC.md Phase 20."""
    schema = _ha_load_schema(
        ROOT / "custom_components" / "jebao_local" / "jebao_gizwits" / "schemas"
        / "877bbcc8df614559864db4de18014286.json"
    )
    attr = schema.by_name("Colour_Kelvin")
    assert attr.data_type == "uint16"

    built = _ha_build_control_payload(schema, {"Colour_Kelvin": 10000})
    vals_start = 1 + _ha_attr_flags_size(schema)
    off = attr.position.byte_offset
    assert built[vals_start + off : vals_start + off + 2] == bytes([0x27, 0x10])

    # ...and decode_status reads it back to the same number.
    raw = bytearray(schema.status_size_bytes)
    raw[off : off + 2] = bytes([0x27, 0x10])
    assert schema.decode_status(bytes(raw))["Colour_Kelvin"] == 10000


def test_uint16_range_is_enforced():
    schema = _ha_load_schema(
        ROOT / "custom_components" / "jebao_local" / "jebao_gizwits" / "schemas"
        / "877bbcc8df614559864db4de18014286.json"
    )
    try:
        _ha_build_control_payload(schema, {"Colour_Kelvin": 70000})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an out-of-range uint16")


if __name__ == "__main__":
    test_switchon_false_matches_capture()
    test_switchon_true_matches_capture()
    test_binary_write_places_bytes_directly_at_byte_offset()
    test_binary_write_wrong_length_is_rejected()
    test_binary_write_does_not_disturb_other_attrs()
    test_uint16_round_trips_big_endian()
    test_uint16_range_is_enforced()
    print("All control.py regression tests passed.")
