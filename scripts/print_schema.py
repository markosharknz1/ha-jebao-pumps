"""Phase 1 checkpoint: print every datapoint in the schema, human-readable.

Everything printed here is pulled from fixtures/datapoint_schema.json via
jebao_gizwits.schema - no hardcoded byte meanings live in this script.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from jebao_gizwits.schema import load

SCHEMA_PATH = ROOT / "fixtures" / "datapoint_schema.json"


def format_value_spec(attr) -> str:
    if attr.data_type == "bool":
        return "bool (0/1)"
    if attr.data_type == "enum":
        return "enum: " + ", ".join(f"{i}={v}" for i, v in enumerate(attr.enum_values))
    if attr.data_type == "uint8":
        us = attr.uint_spec
        return f"uint8 [{us.min}..{us.max}] (ratio={us.ratio}, addition={us.addition})"
    if attr.data_type == "binary":
        return f"binary blob ({attr.position.len} bytes)"
    return attr.data_type


def format_location(attr) -> str:
    p = attr.position
    if p.unit == "bit":
        return f"byte {p.byte_offset} bit {p.bit_offset} (len {p.len} bit)"
    end = p.byte_offset + p.len - 1
    return f"byte {p.byte_offset}..{end}" if p.len > 1 else f"byte {p.byte_offset}"


def main():
    schema = load(SCHEMA_PATH)
    print(f"Schema: {schema.name}  product_key={schema.product_key}")
    print(f"packetVersion={schema.packet_version}  protocolType={schema.protocol_type}")
    print(f"Status payload size (inferred from attrs): {schema.status_size_bytes} bytes")
    print(f"Total datapoints: {len(schema.attrs)}")
    print()

    for kind, label in (("status_writable", "WRITABLE ATTRIBUTES"), ("fault", "FAULT FLAGS")):
        group = [a for a in schema.attrs if a.dp_type == kind]
        print(f"=== {label} ({len(group)}) ===")
        for a in group:
            print(f"  [{a.id:3d}] {a.name:20s} {format_location(a):28s} {format_value_spec(a)}")
            detail = a.display_name + (f" - {a.desc}" if a.desc else "")
            print(f"        {detail}")
        print()


if __name__ == "__main__":
    main()
