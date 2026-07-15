# Jebao Local Control

Full spec: [SPEC.md](SPEC.md). Read it before doing any work here. For the bit-field write history specifically, read [PHASE4B_PLAN.md](PHASE4B_PLAN.md) - the encoding is now solved (see below), that file has the how and what's still unverified.

**Repo is now git-initialized with a local commit (2026-07-15) - not pushed anywhere, that needs explicit user approval.** Public-facing docs live at the repo root: [README.md](README.md) (overview/status), [PROTOCOL.md](PROTOCOL.md) (technical wire-protocol reference), [METHODOLOGY.md](METHODOLOGY.md) (how each finding was actually made - written for external readers, not just this project's own history). `.gitignore` excludes `tools/`, `reference/` (multi-GB, reproducible), `.env`, and `*.pcap`/`*.pcapng` (one old capture in `discovery/captures/` had a leaked `USER_TOKEN` in an HTTP header - excluded the whole capture format, not just that file, as a blanket precaution).

**Multi-product support added (2026-07-15):** the vendor app bundles 42 product datapoint schemas locally (not just this project's wavemaker) - see [docs/SUPPORTED_MODELS.md](docs/SUPPORTED_MODELS.md) for the full catalog and how WiFi vs Bluetooth-only connectivity was determined (name-based, not schema-based - the schema content is identical between WiFi-only and WiFi+BLE siblings of the same product). 29 WiFi-capable ("standard" protocolType) schemas are bundled in `fixtures/product_schemas/` and in `custom_components/jebao_local/jebao_gizwits/schemas/` for the HA integration's use. `jebao_gizwits/schema.py::load_by_product_key()`/`known_product_keys()` added for schema lookup by product.

**Home Assistant integration built (2026-07-15):** `custom_components/jebao_local/` - discovery-first config flow, single shared coordinator, generic schema-driven entity factory (switch/select/number/binary_sensor). Architecture borrows from reviewing two existing community Jebao integrations (`chrisc123/jebao_aqua-homeassistant` for the schema-driven entity pattern and multi-device coordinator shape, `jrigling/homeassistant-jebao` for the discovery-first config flow and per-entity DeviceInfo pattern - see SPEC.md Phase 7 for the detailed comparison and which bugs in each were deliberately *not* replicated). Validated for correct HA API usage (imports cleanly against the real `homeassistant` package) but **not yet tested against a running HA instance** - see `custom_components/jebao_local/README.md`'s "Known gaps" section before extending it.

## Current phase

**Prerequisite gate (P0) — COMPLETE.** This was a strategy pivot from the old "decrypt the algorithm" approach (see `discovery/findings.md` for that history — do not repeat those routes). The pump runs standard **Gizwits GAgent** firmware; the goal is to implement the documented GAgent LAN protocol, not reverse-engineer anything proprietary.

- **P0.2 (LAN reachability): DONE.** This dev machine (192.168.1.64) is on the pump's subnet. UDP discovery broadcast to `255.255.255.255:12414` got a live reply from the pump at `192.168.1.77`, whose embedded `product_key` matches the spec's known constant exactly. Saved to `fixtures/discovery_reply.bin`.
- **P0.1 (datapoint schema fetch): DONE.** `USER_TOKEN` captured via mitmdump proxy (see below) and fetched to `fixtures/datapoint_schema.json`. Schema confirms this is the wavemaker/MOW-class product (`本地造浪泵_WIFI_BLE`), with real bit-level attrs (SwitchON, PulseTide, FeedSwitch, TimerON, AutoPulseTide, etc.).

**Phase 1 (datapoint schema decoder) — COMPLETE.** `jebao_gizwits/schema.py` + `scripts/print_schema.py`. 71 datapoints total (64 writable + 7 fault), 401-byte status payload. This is a wavemaker (MOW-class) pump: power, wave mode, flow%, frequency%, feed-pause, 48-slot daily schedule, master/slave linkage, clock sync, motor fault flags. See SPEC.md Phase 1 for the full breakdown.

**Phase 2 (LAN discovery) — COMPLETE.** `jebao_gizwits/protocol.py` (GAgent frame encode/decode, varint length) + `jebao_gizwits/discovery.py` (`discover()`, `parse_discovery_reply()`). Verified against both the fixture and a fresh live broadcast — same device, same fields. Correction to the original phase description: the UDP discovery reply has no passcode field (confirmed against `node-ph803w`'s actual parsing code, not just the prose doc) — passcode is fetched over TCP in Phase 3.

Cloned reference repos this phase: `reference/node-ph803w/` (transport/discovery/read authority) and `reference/homebridge-clearlight-sauna/` (write-frame authority, `src/gizwits/protocol.ts`).

**Phase 3 (LAN session: connect, authenticate, read status) — COMPLETE.** `jebao_gizwits/session.py`: `connect()`, `authenticate()`, `read_status()`. Live-verified against the pump at 192.168.1.77.

**Important finding from this phase — read before touching writes in Phase 4:** `SwitchON` (byte 0 bit 0) does NOT reflect live power state on this firmware, confirmed across ~75s of live on/off toggling. Use **`AutoMode`** instead to know if the pump is actually running (`0`='停机'/stopped, `1`-`4`=running in that wave mode: Classic/Sine/Random/Constant-flow, `5`=feeding). This was also where a real decode bug got fixed: `decode_status()` in `jebao_gizwits/schema.py` now treats `bit_offset` as an absolute bit address (`byte_offset*8 + bit_offset`) that can span byte boundaries - the earlier single-byte-shift version silently zeroed out any field with `bit_offset > 7` (which is exactly what made `AutoMode` look permanently stopped before the fix). Re-check this reasoning before trusting any other multi-bit field blindly.

**Phase 4 (LAN control / writes) — SOLVED for encoding, byte-type verified live, bit-type verified against real captured frames but not yet live.** Built `jebao_gizwits/control.py` from the official Gizwits SDK source, then refined via Ghidra disassembly of `libGizWifiDaemon.so` (the app's own native protocol engine - not stripped, full function names available). Ghidra setup: `tools/ghidra_11.3.1_PUBLIC/` + portable JDK 21 at `tools/jdk-21.0.11+10/` (Ghidra needs JDK 21+). Project saved at `tools/ghidra_project/` (reusable - `analyzeHeadless.bat <project> gizproj -process libGizWifiDaemon.so -noanalysis -scriptPath tools/ghidra_scripts -postScript <script>.py`). Scripts in `tools/ghidra_scripts/`, decompiled output in `reference/jebao-apk/decompiled_native/`.

**Confirmed: cmd `0x93` is the real write command.** **Confirmed: `attrFlags_t` is byte-reversed** relative to naive ascending-id order. **`Flow`/`Frequency` (byte-type/uint8) writes work reliably** - verified with multiple precise live value changes.

**Bit-type writes (`Mode`, `AutoMode`, `SwitchON`, `TimerON`, `Linkage`, power on/off) - encoding solved 2026-07-15.** The Ghidra-derived formula (`dest_byte = byte_offset - ((bit_offset+i)>>3) + ((total_writable_bits-1)>>3)`, `dest_bit = (bit_offset+i)&7`) turned out to be correct all along - earlier live-hardware failures were a test-code bug, not a formula bug. Confirmed via a completely different technique: the app's SDK logs a full hex dump of every outgoing p0 payload to `adb logcat` (`GizSDKLog: Gizwits p0:`) - captured two real `SwitchON` write frames this way (see [PHASE4B_PLAN.md](PHASE4B_PLAN.md) for the full how-to, including the Android emulator setup under `tools/android-sdk/`). `build_control_payload()` now reproduces both captured frames byte-for-byte (`tests/test_control.py`). **Only `SwitchON` has been verified this way** - other bit-type attrs (especially multi-bit ones like `Mode`) use the same formula but haven't been captured/confirmed individually, and none of this has been tested against the real pump yet (only against captured bytes). Do that before relying on it.

**Also learned:** the `0x93` ack payload (`00 16 <did>`, echoing device ID) is NOT an error/success indicator - it's identical regardless of outcome, judge success only by re-reading status. The real app sends all-zero `attrVals_t` for unflagged attributes rather than carrying forward current status - `build_control_payload()` now does the same.

**Frida is a dead end on this machine specifically** - `libGizWifiDaemon.so` runs under x86_64 emulator's ARM binary-translation layer, which Frida cannot hook into (confirmed: the library never appears in Frida's own module enumeration). Would need genuine ARM64 hardware to revisit. Not needed anymore since the logcat technique solved this without it.

**CONFIRMED ON LIVE HARDWARE (2026-07-15): sent `SwitchON=True` via LAN, user physically confirmed the pump started running.** This is the actual goal (turn pump on/off) and it works.

**New caveat found in the same test: don't trust `SwitchON`/`AutoMode` reads to confirm power state for a manually-toggled pump.** After the confirmed-successful write, `read_status()` kept reporting `SwitchON=False`/`AutoMode='停机'` across multiple polls and a fresh TCP session - not staleness, the device's status payload just doesn't reflect this particular on-path via these fields. (Already knew `SwitchON` reads were unreliable per Phase 3; this extends that to `AutoMode` too, specifically for manual/non-schedule power-on.) **Writes are trustworthy. Reads of live power state are not, for a manually-toggled pump - track last-commanded state instead.**

**Current deliverable state: read (discovery, status, decode) + byte-type write (Flow, Frequency) + bit-type write (SwitchON/power confirmed, others should work by the same formula but untested individually) all working on real hardware.** Reading back true live power state remains unreliable - see caveat above.

## Getting a USER_TOKEN (if it expires and needs re-capture)

PCAPdroid's VPN mode broke the phone's internet in one attempt — don't use it. Proven method instead: a manual WiFi proxy pointed at the dev machine, since the datapoint request is plain HTTP (no CA cert needed).

1. On the dev machine (must be on the same LAN as the phone), run `mitmdump -s scripts/capture_token.py -p 8080 --listen-host 0.0.0.0` (mitmdump is already installed at `C:\Program Files\mitmproxy\bin\mitmdump.exe`).
2. On the phone: Wi-Fi settings → network → modify/advanced → Proxy → Manual → hostname = dev machine's LAN IP, port `8080`.
3. Open the Jebao Aqua app and go to the pump's device screen (triggers the datapoint fetch). Other apps' HTTPS traffic will fail while the proxy is active (expected — we're not installing a CA cert) — leave the proxy on only long enough to trigger this one screen.
4. `scripts/capture_token.py` auto-detects the `GET /app/datapoint` request and writes the token to `C:\jebao-ha\captured_token.txt`.
5. Set the phone's proxy back to "None" immediately.
6. Move the token into `C:\jebao-ha\.env` as `JEBAO_USER_TOKEN=<value>`, delete `captured_token.txt`, stop mitmdump, then run `python scripts/fetch_datapoint_schema.py`.

## Ground rules

- `reference/` is read-only. Never edit files inside it; it exists only to be read for protocol/architecture patterns.
- Every claim in `discovery/findings.md` (old) or this file must cite a capture file (`fixtures/...` or `discovery/captures/...`) or a live test result — no unverified guesses.
- Python 3.11+, `asyncio` throughout. Zero cloud dependency at runtime once the prerequisite gate + Phase 1-4 are done (the only cloud touch is the one-time datapoint schema fetch in P0.1).
- One phase at a time. Do not scaffold Phase 2+ library code before the prerequisite gate and Phase 1 pass their checkpoints.
- Reference implementations for protocol details: `Apollon77/node-ph803w` (transport/discovery/read), `homebridge-clearlight-sauna` (write-datapoint frame). Do not invent frame formats — pull them from these or from captured bytes.

## Hardware / access status

- Live pump confirmed reachable at `192.168.1.77` on the home LAN (192.168.1.0/24), from this dev machine.
- `did` = `QP50gPt5I8h4mFKIo0ENIK`, firmware `4.1.4`, hardware string `04D30Q29`.
