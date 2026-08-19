"""The bit-packed attribute group at byte_offset 0.

Devices pack their booleans and small enums into a group of bits starting
at byte_offset 0, and serialize that group as **one big-endian integer**:
`bit_offset` counts from the LSB, so bit 0 lives in the group's *last*
byte. A 2-byte group is the classic layout; the multi-channel dosers use
21 bits (3 bytes).

This module exists because reads and writes used to disagree about that.
`control.py` byte-reversed the group when writing (correct, and confirmed
byte-for-byte against a captured frame from the vendor app), while
`schema.py`'s decode addressed bits linearly from byte 0 (wrong whenever
the group spans more than one byte). The result was that on 21 of the 48
bundled products - every wavemaker and every doser - Home Assistant
decoded the wrong bits: three live wavemakers all read back as powered
off, in Slave linkage, AutoMode "Stop", when they were running,
Independent and on Classic wave. That mismatch is almost certainly what
this project previously recorded as an unexplained device quirk, that
"reading back live power state is unreliable for manually-toggled pumps".

The layout rules here match chrisc123/jebao_aqua-homeassistant's
`gizwits_lan` module, an independent implementation of the same protocol
validated against hardware this project does not have (including the
dosers). Deliberately including its two quirks:

* A group counts as byte-swapped when any of its attributes ends past
  bit 7 (`bit_offset + len > 7`), i.e. it needs more than one byte.
* A swapped group is at least 2 bytes wide even if its bits would fit in
  one, which is the historical layout every 16-bit device expects.

Note the width is the *extent* of the group (the highest bit any
attribute reaches), not the number of bits in use - a device with two
booleans at bit_offset 0 and 10 still has a 2-byte group.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BitGroup:
    """How the byte_offset-0 bit group is laid out for one product."""

    swapped: bool
    width: int  # bytes; 0 when the product has no such group

    def value_of(self, raw: bytes, bit_offset: int, length: int) -> int:
        """Read `length` bits at `bit_offset` out of the group."""
        group = int.from_bytes(raw[: self.width], "big")
        return (group >> bit_offset) & ((1 << length) - 1)

    def place(self, buf: bytearray, bit_offset: int, length: int, value: int) -> None:
        """Write `value` into the group in `buf`, leaving other bits alone."""
        mask = (1 << length) - 1
        group = int.from_bytes(buf[: self.width], "big")
        group &= ~(mask << bit_offset)
        group |= (value & mask) << bit_offset
        buf[0 : self.width] = group.to_bytes(self.width, "big")


def bit_group(attrs) -> BitGroup:
    """Work out the group layout from a product's attribute list.

    Considers every attribute, not just writable ones: the group's extent
    is a property of the wire format, and a read-only fault bit occupying
    a high bit still makes the group wider.
    """
    ends = [
        a.position.bit_offset + a.position.len
        for a in attrs
        if a.position.unit == "bit" and a.position.byte_offset == 0
    ]
    if not ends:
        return BitGroup(swapped=False, width=0)

    max_end = max(ends)
    if max_end <= 7:
        # Fits inside byte 0 with room to spare - plain single-byte layout,
        # where big-endian and linear addressing are the same thing.
        return BitGroup(swapped=False, width=1)
    return BitGroup(swapped=True, width=max(2, (max_end + 7) // 8))
