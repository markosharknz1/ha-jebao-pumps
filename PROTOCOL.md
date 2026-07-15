# Jebao GAgent LAN Protocol Reference

This documents the wire protocol Jebao's WiFi-capable devices speak locally,
as reverse-engineered by this project. It's the Gizwits "GAgent" protocol -
not Jebao-specific, this same firmware family is used by other IoT vendors
too (see [`Apollon77/node-ph803w`](https://github.com/Apollon77/node-ph803w)
for a different product on the same platform). Everything here is confirmed
against either a real reference implementation, real captured bytes from
this project's own hardware, or both - see [SPEC.md](SPEC.md) for exactly
how each piece was established, and [`fixtures/`](fixtures/) for the actual
captured bytes backing this document.

## Transport

- **UDP 12414** - discovery. Client broadcasts to `255.255.255.255:12414`; device replies from its own IP, port 12414.
- **TCP 12416** - control/read session with a specific device, once you know its IP.

## Frame format

Every message, on both UDP and TCP, shares this envelope:

```
00 00 00 03  <varint length>  00  <cmd: 2 bytes, big-endian>  <payload>
```

- Bytes 0-3: static magic `00 00 00 03` - required, device ignores anything else.
- Next 1-4 bytes: length of everything *after* the length field (flag + cmd + payload), as a LEB128 varint (last byte has MSB unset).
- Next byte: flag, always `00` on every request and read response seen. **One exception**: a control-write ack (`0x94`) was observed with flag `0x01` once - not an error indicator (confirmed - it appeared on both a working write and a no-op write), meaning unclear, don't rely on it.
- Next 2 bytes: command (see table below).
- Rest: command-specific payload.

## Commands

| Command | Direction | Purpose |
|---|---|---|
| `0x0003` | client→device (UDP) | Discovery request |
| `0x0004` | device→client (UDP) | Discovery response |
| `0x0006` | client→device (TCP) | Passcode request |
| `0x0007` | device→client (TCP) | Passcode response |
| `0x0008` | client→device (TCP) | Login request (using the passcode) |
| `0x0009` | device→client (TCP) | Login response (last payload byte: `0x00`=success) |
| `0x0015`/`0x0016` | both (TCP) | Heartbeat ping/pong, every ~4s |
| `0x0090`/`0x0091` | both (TCP) | Serial data transmit - used for status reads |
| `0x0093`/`0x0094` | both (TCP) | Serial data control - used for writes |

### Discovery (`0x0003`/`0x0004`)

Request payload is empty - the whole frame is just `00 00 00 03 03 00 00 03`
(8 bytes total).

Response payload (field layout confirmed against
[`node-ph803w`'s `lib/discovery.js`](https://github.com/Apollon77/node-ph803w/blob/main/lib/discovery.js),
not just its prose docs):

```
<2-byte length><did string>
<2-byte length><mac, 6 raw bytes>
<2-byte length><wifi firmware string>
<2-byte length><product_key string>
<8 bytes: MCU attributes, unused>
<null-terminated api server string>
<null-terminated firmware version string>
<remaining bytes: device-specific extra data, if any>
```

No passcode in this reply, despite what an earlier draft of this project's
own plan assumed - the passcode is a separate TCP exchange (below). See
`jebao_gizwits/discovery.py`.

### Session setup (`0x0006`-`0x0009`)

1. Connect TCP to the device's IP, port 12416.
2. Send `0x0006` with an empty payload.
3. Device replies `0x0007` with `<2-byte length><passcode bytes>` - a
   randomly-generated string set when the device was provisioned. (If this
   comes back empty, the device isn't in binding mode - not applicable to
   normal operation once already bound.)
4. Send `0x0008` with `<2-byte length><the passcode bytes just received>`.
5. Device replies `0x0009` with a 1-byte payload: `0x00` = success.

This registers your TCP connection with the device - required before reads
or writes will work. See `jebao_gizwits/session.py::authenticate()`.

The device sends 1-2 unsolicited/duplicate frames (observed: a repeated
`0x0009` login ack, and an unrecognized `0x0062`) immediately after login,
before the frame you actually asked for - tolerate and skip non-matching
frames rather than assuming the very next frame is always the reply you want.

### Reading status (`0x0090`/`0x0091`)

Request: `0x0090` with a 1-byte payload, `0x02` (the "p0 protocol" read-status action code).

Response: `0x0091` with payload `<1 action byte><raw status bytes>`. Action
byte is `0x03` ("status reply", direct response to your request) or `0x04`
("status report", an unsolicited push - the device sends these on its own
too, not just in reply to a read).

The raw status bytes are decoded using the device's own **datapoint
schema** (see below) - byte/bit positions are entirely schema-driven, there
is no fixed struct.

### Writing (control) (`0x0093`/`0x0094`)

Request: `0x0093` with payload `<4-byte sequence number><p0 control payload>`.

The p0 control payload is:

```
0x01                      (action: control device)
<attrFlags_t>              (bitmask: which attributes are being set)
<attrVals_t>                (values: full writable-state buffer, only flagged bytes/bits matter)
```

**`attrFlags_t`**: one bit per writable attribute `id` (from the datapoint
schema), LSB-first (`byte = id // 8`, `bit = id % 8`) - **then the whole
buffer is byte-reversed** (a flag for id `N` actually lands at
`flags_byte[flagsSize - (N // 8) - 1]`). This reversal was traced from
Ghidra decompilation of the vendor app's native library
(`transDatasToP0Data`) and confirmed against a real captured write frame.

**`attrVals_t`**: sized to cover every writable attribute's byte range.
- **Byte-type attributes** (`uint8`): value goes directly at the attribute's
  `byte_offset` - no reversal, no transformation beyond the schema's own
  `ratio`/`addition` linear scale (`raw = (display - addition) / ratio`).
- **Bit-type attributes** (`bool`/`enum`): for each bit `i` (0-indexed from
  the value's LSB) of the attribute's value:
  ```
  dest_byte = attr.byte_offset - ((attr.bit_offset + i) >> 3) + ((total_writable_bits - 1) >> 3)
  dest_bit  = (attr.bit_offset + i) & 7
  ```
  where `total_writable_bits` is the sum of `len` across every writable
  bit-type attribute in the schema (e.g. 12 for this project's wavemaker).
  `byte_offset`/`bit_offset` are the schema's raw values, not normalized -
  a bit-type attribute whose bits span a byte boundary in the schema
  (uncommon but present, e.g. this project's `Linkage` attribute) splits
  across two destination bytes accordingly. **This formula was derived from
  static analysis and confirmed byte-for-byte against a real captured
  frame** (`SwitchON` true/false) - see [METHODOLOGY.md](METHODOLOGY.md)
  for how.
- **Unflagged bytes**: zero. The real app doesn't carry forward current
  status into unflagged bytes, and neither does this implementation - the
  device only applies attributes actually marked in `attrFlags_t`.

Response: `0x0094` with payload `<4-byte sequence number, echoed><body>`.
**The response body is not a reliable success/failure indicator** - a body
of `00 16 <22-char did>` (the device echoing its own ID) was observed on
both successful and unsuccessful writes, including a deliberate no-op test.
Always verify a write by re-reading status afterward.

**Known caveat**: after a confirmed-successful `SwitchON=true` write (pump
physically verified running), subsequent status reads continued to report
`SwitchON=false` and `AutoMode` as stopped, across multiple polls and even a
fresh TCP session. Writes are reliable; reads don't necessarily reflect a
manually-triggered power-on state via these particular fields. See
[SPEC.md](SPEC.md) Phase 4 for full detail - this needs more investigation
before anything should depend on reading true live power state.

## Datapoint schema

Every product has a JSON schema (fetched from
`http://usapi.gizwits.com/app/datapoint?product_key=<key>`, or bundled
locally in this project - see `docs/SUPPORTED_MODELS.md`) that defines every
attribute: name, data type (`bool`/`enum`/`uint8`/`binary`), its bit/byte
position in the status payload, and (for `uint8`) its value range and linear
scale (`ratio`/`addition`).

Two top-level `protocolType` values were found across the 42 bundled
products: `"standard"` (this project's implementation, WiFi-oriented, always
sends a full-length payload) and `"var_len"` (a different, more compact
variable-length encoding, correlates strongly but not perfectly with
Bluetooth-primary products - **not implemented by this project**, see
`docs/SUPPORTED_MODELS.md` for the one known exception to that correlation).

Position fields:
- `unit: "byte"` - value lives at `byte_offset`, is `len` bytes wide.
- `unit: "bit"` - value is `len` bits wide, starting at absolute bit address
  `byte_offset * 8 + bit_offset` (LSB-first), which **can span past a single
  byte** (e.g. this project's `AutoMode` attribute has `bit_offset=9`,
  spanning into the next byte). Treating `bit_offset` as an index within a
  single byte (`raw[byte_offset] >> bit_offset`) is a real bug this project
  hit and fixed - see `jebao_gizwits/schema.py::decode_status`.

This same schema drives both reading (`decode_status`) and writing
(`build_control_payload`) - see `jebao_gizwits/schema.py` and
`jebao_gizwits/control.py`.
