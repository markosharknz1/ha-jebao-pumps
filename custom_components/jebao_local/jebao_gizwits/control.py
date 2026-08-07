"""Write-datapoint control frame construction (Phase 4).

p0 control payload = action(0x01) + attrFlags_t + attrVals_t, sent via
command 0x93 (GizwitsSession.send_control). Structure and byte layout
reverse-engineered from Ghidra decompilation of the real app's native
protocol library, libGizWifiDaemon.so (see reference/jebao-apk/ and
tools/ghidra_project/), and confirmed against real captured write frames
from the live app (logcat hex dumps, see fixtures/captured_writes/ and
tests/test_control.py) - not just static analysis. Full history is in
SPEC.md's Phase 4 section.

Confirmed against real captured frames (SwitchON true/false via logcat):
- attrFlags_t: one bit per writable attribute id (byte = id//8, bit = id%8),
  then the WHOLE flags buffer is byte-reversed: a flag for id N lands at
  `flagsSize - (N>>3) - 1`.
- attrVals_t byte-type (uint8) fields: placed directly at their schema
  byte_offset, no reversal.
- attrVals_t bit-type (bool/enum) fields: NOT simple absolute-bit addressing.
  Traced from transDatasToP0Data (FUN_0022165c) and parseIndexInfo
  (FUN_002205f8) in libGizWifiDaemon.so, and confirmed byte-for-byte against
  a real captured SwitchON write: for bit `i` (0-indexed from LSB) of a
  bit-type attribute's value,
      dest_byte = schema.byte_offset - ((schema.bit_offset + i) >> 3) + ((total_writable_bits - 1) >> 3)
      dest_bit  = (schema.bit_offset + i) & 7
  where total_writable_bits is the sum of `len` across every writable
  bit-type attribute (12 for this schema). byte_offset/bit_offset are the
  raw schema values, not normalized.
- Unflagged attrVals_t bytes: the real app sends these as zero, not carried
  forward from current status - the device only applies flagged attributes
  (confirmed by the flags mechanism itself), so this is safe and is what we
  do here too, matching the real app exactly rather than guessing.
- attrVals_t "binary" fields (e.g. the AutoTimeNN schedule slots, see
  jebao_gizwits/schedule.py): placed directly at byte_offset like uint8,
  just multi-byte - this reuses the already-confirmed byte-type placement
  rule above rather than a new one, since the SDK's byte-type placement
  logic has no reason to care about a field's width. Not yet individually
  confirmed against a real captured AutoTimeNN write frame.

The p0 control-ack response (from GizwitsSession.send_control) is NOT a
reliable success/failure signal - it returns the same "00 16 <did>" payload
regardless of whether the write actually took effect. Always verify with a
fresh read_status() + decode_status().
"""
from __future__ import annotations

from .schema import DatapointSchema

P0_ACTION_CONTROL_DEVICE = 0x01


def _writable_attrs(schema: DatapointSchema):
    return [a for a in schema.attrs if a.writable]


def attr_flags_size(schema: DatapointSchema, max_id: int | None = None) -> int:
    if max_id is None:
        max_id = max(a.id for a in _writable_attrs(schema))
    return max_id // 8 + 1


def attr_vals_size(schema: DatapointSchema, max_id: int | None = None) -> int:
    size = 0
    for a in _writable_attrs(schema):
        if max_id is not None and a.id > max_id:
            continue
        p = a.position
        size = max(size, p.byte_offset + (p.len if p.unit == "byte" else 1))
    return size


def _total_writable_bits(schema: DatapointSchema, max_id: int | None = None) -> int:
    total = 0
    for a in _writable_attrs(schema):
        if max_id is not None and a.id > max_id:
            continue
        if a.position.unit == "bit":
            total += a.position.len
    return total


def build_control_payload(
    schema: DatapointSchema, changes: dict[str, object], max_id: int | None = None
) -> bytes:
    """Build the p0 control payload (action byte + attrFlags_t + attrVals_t).

    `changes` maps attribute name -> new value (bool for bool attrs, the
    enum label or index for enum attrs, numeric for uint8 attrs). Unflagged
    bytes are zero, matching the real app's own behavior - the device only
    applies attributes flagged in attrFlags_t.
    """
    flags_size = attr_flags_size(schema, max_id)
    vals_size = attr_vals_size(schema, max_id)
    total_bits = _total_writable_bits(schema, max_id)

    flags = bytearray(flags_size)
    vals = bytearray(vals_size)

    for name, new_value in changes.items():
        attr = schema.by_name(name)
        if not attr.writable:
            raise ValueError(f"{name!r} is not a writable attribute")
        p = attr.position
        flags[attr.id // 8] |= 1 << (attr.id % 8)

        if p.unit == "bit":
            if attr.data_type == "bool":
                raw_v = 1 if new_value else 0
            elif attr.data_type == "enum" and attr.enum_values is not None:
                raw_v = (
                    attr.enum_values.index(new_value)
                    if isinstance(new_value, str)
                    else int(new_value)
                )
            else:
                raw_v = int(new_value)

            max_v = (1 << p.len) - 1
            if not (0 <= raw_v <= max_v):
                raise ValueError(f"{name}: value {raw_v} out of range 0..{max_v}")

            for i in range(p.len):
                local_bit = p.bit_offset + i
                dest_byte = p.byte_offset - (local_bit >> 3) + ((total_bits - 1) >> 3)
                dest_bit = local_bit & 7
                if (raw_v >> i) & 1:
                    vals[dest_byte] |= 1 << dest_bit
                else:
                    vals[dest_byte] &= ~(1 << dest_bit) & 0xFF
        elif attr.data_type == "binary":
            raw_bytes = bytes(new_value)
            if len(raw_bytes) != p.len:
                raise ValueError(f"{name}: expected {p.len} bytes, got {len(raw_bytes)}")
            vals[p.byte_offset : p.byte_offset + p.len] = raw_bytes
        else:
            if attr.data_type not in ("uint8", "uint16") or attr.uint_spec is None:
                raise ValueError(f"writing data_type {attr.data_type!r} not implemented ({name})")
            us = attr.uint_spec
            raw_v = round((float(new_value) - us.addition) / us.ratio)
            if not (us.min <= raw_v <= us.max):
                raise ValueError(f"{name}: value {new_value} out of range {us.min}..{us.max}")
            if attr.data_type == "uint16":
                # Big-endian, same convention as decode_status - see the
                # note there; consistent with the rest of the protocol but
                # not confirmed against a captured uint16 write.
                vals[p.byte_offset : p.byte_offset + 2] = (raw_v & 0xFFFF).to_bytes(2, "big")
            else:
                vals[p.byte_offset] = raw_v & 0xFF

    flags = bytes(reversed(flags))
    return bytes([P0_ACTION_CONTROL_DEVICE]) + flags + bytes(vals)
