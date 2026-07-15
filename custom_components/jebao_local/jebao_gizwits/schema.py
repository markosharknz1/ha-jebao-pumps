"""Typed model of the Gizwits datapoint schema (fixtures/datapoint_schema.json).

The JSON is the single source of truth for what every byte/bit means - this
module only parses it, it never hardcodes byte offsets or attribute meanings.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Position:
    byte_offset: int
    bit_offset: int
    len: int
    unit: str  # "bit" or "byte"


@dataclass(frozen=True)
class UintSpec:
    min: int
    max: int
    addition: int
    ratio: float


@dataclass(frozen=True)
class Attr:
    id: int
    name: str
    display_name: str
    desc: str
    data_type: str  # "bool" | "enum" | "uint8" | "binary"
    dp_type: str  # "status_writable" | "fault"
    position: Position
    enum_values: tuple[str, ...] | None = None
    uint_spec: UintSpec | None = None

    @property
    def writable(self) -> bool:
        return self.dp_type == "status_writable"

    @property
    def is_fault(self) -> bool:
        return self.dp_type == "fault"


@dataclass(frozen=True)
class DatapointSchema:
    name: str
    product_key: str
    packet_version: str
    protocol_type: str
    attrs: tuple[Attr, ...]

    def by_name(self, name: str) -> Attr:
        for a in self.attrs:
            if a.name == name:
                return a
        raise KeyError(f"no datapoint named {name!r}")

    def decode_status(self, raw: bytes) -> dict[str, object]:
        """Map a raw status payload to attribute values using this schema.

        For bit-unit fields, `bit_offset` is an absolute bit address measured
        from `byte_offset * 8`, LSB-first, and can span past a single byte's
        8 bits (confirmed live: `AutoMode` has bit_offset=9/len=3, which
        reaches into byte_offset+1 - a naive "shift within one byte" decode
        makes it read as always 0, which is exactly the bug that caused
        AutoMode to look permanently 'stopped' during Phase 3 live testing).

        Convention (bool/enum bitfields, uint8 linear scale via ratio/addition)
        follows the Gizwits DataPoint schema as documented in
        reference/node-ph803w/api/en/openapi_apps.json. This device's uint8
        attrs all have ratio=1/addition=0, so the scale direction is a no-op
        here either way - worth re-checking against a live capture if a
        future device has non-trivial ratio/addition.
        """
        values: dict[str, object] = {}
        for attr in self.attrs:
            p = attr.position
            if p.unit == "bit":
                start = p.byte_offset * 8 + p.bit_offset
                v = 0
                for i in range(p.len):
                    abs_bit = start + i
                    bit = (raw[abs_bit // 8] >> (abs_bit % 8)) & 1
                    v |= bit << i
                if attr.data_type == "bool":
                    values[attr.name] = bool(v)
                elif attr.data_type == "enum" and attr.enum_values is not None:
                    values[attr.name] = attr.enum_values[v] if v < len(attr.enum_values) else v
                else:
                    values[attr.name] = v
            else:
                chunk = raw[p.byte_offset : p.byte_offset + p.len]
                if attr.data_type == "uint8" and attr.uint_spec is not None:
                    us = attr.uint_spec
                    values[attr.name] = chunk[0] * us.ratio + us.addition
                else:
                    values[attr.name] = chunk
        return values

    @property
    def status_size_bytes(self) -> int:
        """Size of the raw status payload this schema describes, inferred from attrs."""
        end = 0
        for a in self.attrs:
            p = a.position
            end = max(end, p.byte_offset + (p.len if p.unit == "byte" else 1))
        return end


def load(path: str | Path) -> DatapointSchema:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if len(data["entities"]) != 1:
        raise ValueError(f"expected exactly one entity, got {len(data['entities'])}")
    raw_attrs = data["entities"][0]["attrs"]

    attrs = []
    for a in raw_attrs:
        pos = a["position"]
        position = Position(
            byte_offset=pos["byte_offset"],
            bit_offset=pos["bit_offset"],
            len=pos["len"],
            unit=pos["unit"],
        )
        uint_spec = None
        if "uint_spec" in a:
            us = a["uint_spec"]
            uint_spec = UintSpec(min=us["min"], max=us["max"], addition=us["addition"], ratio=us["ratio"])
        enum_values = tuple(a["enum"]) if "enum" in a else None

        attrs.append(
            Attr(
                id=a["id"],
                name=a["name"],
                display_name=a["display_name"],
                desc=a.get("desc", ""),
                data_type=a["data_type"],
                dp_type=a["type"],
                position=position,
                enum_values=enum_values,
                uint_spec=uint_spec,
            )
        )

    attrs.sort(key=lambda a: a.id)

    return DatapointSchema(
        name=data["name"],
        product_key=data["product_key"],
        packet_version=data["packetVersion"],
        protocol_type=data["protocolType"],
        attrs=tuple(attrs),
    )


_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"


def load_by_product_key(product_key: str) -> DatapointSchema:
    """Load a bundled product schema by its Gizwits product_key.

    Bundled schemas were extracted from the Jebao Aqua app's own bundled
    productConfig assets (see docs/SUPPORTED_MODELS.md) - one JSON per
    product this library can control over LAN. Only "standard" protocolType
    (WiFi) products are bundled; "var_len" (Bluetooth-primary) products use a
    different encoding this library doesn't implement, see the docs.

    Note: the write-payload bit-placement formula in control.py was derived
    from Ghidra decompilation of the shared SDK function used by every
    "standard" protocolType product, and empirically confirmed correct for
    the wavemaker (this project's original device) via a real captured
    frame - not yet individually re-confirmed for every other bundled
    product. It should generalize (same SDK function, same formula, just
    different byte_offset/bit_offset/len per attribute), but treat writes on
    other product lines as unverified until tested.
    """
    path = _SCHEMAS_DIR / f"{product_key}.json"
    if not path.exists():
        raise KeyError(
            f"no bundled schema for product_key {product_key!r} - "
            f"see docs/SUPPORTED_MODELS.md for the list of supported products"
        )
    return load(path)


def known_product_keys() -> tuple[str, ...]:
    """All product_keys this library has a bundled schema for."""
    if not _SCHEMAS_DIR.exists():
        return ()
    return tuple(sorted(p.stem for p in _SCHEMAS_DIR.glob("*.json")))
