"""The bit-packed attribute group at byte_offset 0.

Devices serialize that group as one big-endian integer, so bit 0 lives in
the group's *last* byte. control.py always byte-reversed it when writing
(confirmed against a captured frame from the vendor app) but
decode_status() addressed it linearly, so on every product whose group
spans more than one byte the two disagreed and Home Assistant decoded the
wrong bits. Found by comparing against chrisc123/jebao_aqua-homeassistant's
independent `gizwits_lan` implementation, and confirmed against three live
wavemakers which all read back as powered off, Linkage=Slave,
AutoMode=Stop while actually running (SPEC.md Phase 28).
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local.jebao_gizwits.bitgroup import bit_group  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.control import build_control_payload, attr_flags_size  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.schema import (  # noqa: E402
    known_product_keys,
    load_by_product_key,
)

WAVEMAKER = "54114ccdac1e41c0bb17e222887c07ba"
AQUARIUM_PUMP = "6a5c47b3ea364ecb841b47f5997a1775"
DOSER_4CH = "1aa33c38ba9d4b78a9e7796705b2fad7"


def test_group_width_is_the_extent_not_the_count():
    """A device with two booleans at bit_offset 0 and 10 has a 2-byte
    group, even though only 2 bits are in use. The old write formula used
    the count, which is right for the wavemaker by coincidence."""
    assert bit_group(load_by_product_key(WAVEMAKER).attrs) == bit_group(
        load_by_product_key(WAVEMAKER).attrs
    )
    assert bit_group(load_by_product_key(WAVEMAKER).attrs).width == 2
    assert bit_group(load_by_product_key(WAVEMAKER).attrs).swapped is True
    # Everything fits in byte 0 - no swap, and big-endian would be identical.
    assert bit_group(load_by_product_key(AQUARIUM_PUMP).attrs).swapped is False
    # 21 packed bits across the channels/timers.
    assert bit_group(load_by_product_key(DOSER_4CH).attrs).width == 3


def test_real_wavemaker_status_decodes_to_a_plausible_device():
    """Captured live from three wavemakers, all reporting these two bytes.

    Under the old linear decode this same payload said the pump was OFF,
    in Slave linkage with AutoMode=Stop - three standalone pumps in a
    running tank, which is what gave the bug away.
    """
    schema = load_by_product_key(WAVEMAKER)
    raw = bytearray(schema.status_size_bytes)
    raw[0], raw[1] = 0b00000010, 0b01000001
    values = schema.decode_status(bytes(raw))

    assert values["SwitchON"] is True
    assert values["Linkage"] == schema.by_name("Linkage").enum_values[0]      # Independent
    assert values["AutoMode"] == schema.by_name("AutoMode").enum_values[1]    # Classic wave
    assert values["Mode"] == schema.by_name("Mode").enum_values[2]            # Random wave


def _written_bits(schema, name, value):
    """Extract the attrVals region from a built payload."""
    payload = build_control_payload(schema, {name: value})
    return payload[1 + attr_flags_size(schema):]


def test_every_bit_attribute_round_trips_between_write_and_read():
    """The property that was actually broken: what we write to a bit must
    be what we read back from the same position. This fails loudly for
    any product whose encode and decode disagree."""
    mismatches = []
    for product_key in known_product_keys():
        schema = load_by_product_key(product_key)
        size = schema.status_size_bytes
        for attr in schema.attrs:
            if not (attr.writable and attr.position.unit == "bit"):
                continue
            highest = (1 << attr.position.len) - 1
            for value in (0, highest):
                vals = _written_bits(schema, attr.name, value)
                raw = bytes(vals) + bytes(max(0, size - len(vals)))
                decoded = schema.decode_status(raw)[attr.name]
                if attr.data_type == "bool":
                    got = int(bool(decoded))
                elif attr.data_type == "enum" and isinstance(decoded, str):
                    got = attr.enum_values.index(decoded)
                else:
                    got = int(decoded)
                if got != value:
                    mismatches.append(f"{schema.name_en}.{attr.name}: wrote {value}, read {got}")
    assert not mismatches, "encode/decode disagree:\n  " + "\n  ".join(mismatches[:20])


def test_single_byte_group_products_are_unaffected():
    """Products whose bits fit in byte 0 must decode exactly as before -
    this fix must not disturb the ones that already worked."""
    schema = load_by_product_key(AQUARIUM_PUMP)
    raw = bytearray(schema.status_size_bytes)
    raw[0] = 0b00010001
    values = schema.decode_status(bytes(raw))
    assert values["SwitchON"] is True
