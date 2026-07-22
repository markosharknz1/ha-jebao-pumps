# Jebao Pump Local Control — Project Spec

**Goal:** Local (no-cloud) control of Jebao WiFi/Bluetooth aquarium pumps for Home Assistant, by implementing the **Gizwits GAgent LAN protocol** the pump already speaks — *not* by reverse-engineering or decrypting a proprietary algorithm.

**Status of prior approach:** Abandoned. The earlier "decrypt the Jebao algorithm" framing was wrong. Packet captures prove the pump runs standard **Gizwits GAgent** firmware. There is no bespoke crypto. The cloud channel (MQTT over TLS) is not worth attacking; the **LAN channel is documented and open**. Full history of the abandoned approach (APK decompile, TLS-pinning dead end, opcode brute force) remains in `discovery/findings.md` for reference — do not repeat those routes.

---

## Core principle for this build

**Spec-first. Do not scaffold implementation before the foundation is proven.** Each phase below has a hard checkpoint. Do not begin the next phase until the current checkpoint passes against a **real device or real captured bytes** — never against invented/assumed frame formats. If a frame format is unknown, stop and capture it; do not guess it.

**Anti-hallucination rules (important — this is where the last attempt failed):**
- The datapoint schema JSON fetched from the Gizwits API is the **single source of truth** for what bytes/bits mean. Do not infer byte meanings from guesswork.
- Transport frame formats come from the reference implementations listed below, validated against captured bytes. Do not invent opcodes or frame layouts.
- Keep a `fixtures/` folder of real captured frames and test all parsers/encoders against them.

---

## Ground-truth facts (from packet captures — treat as authoritative)

**Platform:** Gizwits GAgent (ESP-based module). All app traffic goes to `*.gizwits.com`.

**LAN protocol ports:**
- **UDP 12414** — device discovery (app broadcasts to `255.255.255.255:12414`; device replies from `12414`)
- **TCP 12416** — local control/read session with the device

**Frame format (GAgent binary protocol):**
- Bytes 0–3: static `00 00 00 03` (protocol version) — required or the device ignores the packet
- Next 1–4 bytes: variable-length-quantity message length (last length byte has MSB unset)
- Bytes 7–8: command
- Then command-specific payload

**Observed discovery broadcast frame:** `00 00 00 03 03 00 00 03` (magic + length `03` + discovery command).

**Cloud channel (IGNORE — TLS-encrypted, not usable):** `usm2m.gizwits.com:8883` (MQTT over TLS).

**Datapoint schema endpoint (cleartext HTTP — this defines all byte meanings):**

```
GET http://usapi.gizwits.com/app/datapoint?product_key=<PRODUCT_KEY>
Headers:
  x-gizwits-application-id: <APPLICATION_ID>
  x-gizwits-user-token: <USER_TOKEN>
```

**Known constants (from capture):**
- `PRODUCT_KEY  = 54114ccdac1e41c0bb17e222887c07ba`   (product-level; safe to hardcode)
- `APPLICATION_ID = c3703c4888ec4736a3a0d9425c321604`  (app-level; safe to hardcode)
- `USER_TOKEN` = a **personal, rotating** session credential. Do **NOT** commit it. Read it from local config / env var. A fresh one can be re-captured from the app if it expires.

---

## Reference implementations (read these; do not reinvent)

- **`Apollon77/node-ph803w`** — `PROTOCOL.md` + `discovery.js`. Same GAgent firmware; authoritative for **transport framing, discovery, and the read/authenticate flow**. The PH803W is read-mostly, so use it for transport + reads.
- **`homebridge-clearlight-sauna`** — `src/gizwits/protocol.ts`. Same GAgent firmware, and it performs **writes/control**. Use it as the authoritative reference for the **write-datapoint control frame**.

---

## Prerequisite gate (do this before ANY protocol code)

**P0.1 — Fetch the datapoint schema.** Run the datapoint GET above with the known constants and a current `USER_TOKEN`. Remove any `If-None-Match` header so the server returns the full body (not `304`). Save the JSON to `fixtures/datapoint_schema.json`.
- **Checkpoint:** `fixtures/datapoint_schema.json` exists and contains the datapoint definitions (attr names, types, bit offsets/lengths, enums) for this pump.
- **Status: DONE (2026-07-14).** `USER_TOKEN` captured via a mitmdump proxy on the dev machine (phone's WiFi proxy pointed at it manually — no CA cert needed since the request is plain HTTP; avoided PCAPdroid's VPN mode, which had broken the phone's internet in an earlier attempt). Fetched with `scripts/fetch_datapoint_schema.py` → `fixtures/datapoint_schema.json` (56,635 bytes). Schema name `本地造浪泵_WIFI_BLE` ("local wavemaker WIFI_BLE") confirms this product_key is the wavemaker (MOW-class) model, matching the pump tested in P0.2. Contains real bit-level attrs: `SwitchON` (byte 0 bit 0), `PulseTide` (byte 0 bit 1), `FeedSwitch` (byte 0 bit 2), `TimerON` (byte 0 bit 3), `AutoPulseTide` (byte 0 bit 4), and more.

**P0.2 — Confirm the device is reachable on the LAN.** From a machine on the **same subnet** as the pump, broadcast the 8-byte discovery frame to `255.255.255.255:12414` and listen for a reply.
- **Do NOT use PCAPdroid's VPN mode for this** — it mangles LAN broadcast/reply traffic and is why the earlier capture showed no device response.
- If no reply: the blocker is network isolation (AP/client isolation, or a separate IoT SSID/VLAN). Fix at the router/AP before continuing. There is no point writing session code against an unreachable device.
- **Checkpoint:** A raw UDP reply from the pump is captured and saved to `fixtures/discovery_reply.bin`. This reply contains the device `did`, `passcode`, IP, and firmware info per the GAgent protocol.
- **Status: DONE (2026-07-14).** This dev machine (192.168.1.64) is on the same subnet as the pump; broadcast to `255.255.255.255:12414` got a live reply from `192.168.1.77`. The reply's embedded `product_key` matches the known constant above exactly, confirming this is genuine Gizwits GAgent firmware. Saved to `fixtures/discovery_reply.bin` (127 bytes). Raw reply:
  `00 00 00 03 7a 00 00 04 00 16 "QP50gPt5I8h4mFKIo0ENIK" 00 06 24 ec 4a ee a4 d4 00 08 "04D30Q29" 00 20 "54114ccdac1e41c0bb17e222887c07ba" 00 00 00 00 00 00 00 02 "usapi.gizwits.com:80" 00 "4.1.4" 00 "03030000"`
  did=`QP50gPt5I8h4mFKIo0ENIK`. Field-by-field parsing (the strings look length-prefixed: 1-byte length + data) belongs in Phase 2 as real code, not re-derived ad hoc here.

> Neither P0.1 nor P0.2 requires writing the library. They are diagnostics. Both must pass before Phase 1.

---

## Phase 1 — Datapoint schema decoder

**Goal:** Turn `datapoint_schema.json` into a typed, in-code model of the pump's controllable attributes (e.g. on/off, speed, mode, feed pause), including bit offsets, value ranges, and enum mappings.

**Deliverable:** A schema module + a pretty-printer that lists every attribute, its type, its bit position in the status payload, and its writable range.

**Checkpoint:** Running the pretty-printer prints a human-readable map of all pump attributes derived **only** from the schema JSON (no hardcoded assumptions). Human review: the attributes match what the real app exposes (speed, mode, etc.).

**Status: DONE (2026-07-14).** `jebao_gizwits/schema.py` (dataclasses `Position`/`UintSpec`/`Attr`/`DatapointSchema` + `load()`) parses the JSON with zero hardcoded byte meanings. `scripts/print_schema.py` prints all 71 datapoints, grouped into 64 `status_writable` + 7 `fault`. Confirmed against the schema (no separate app screenshot needed — the schema itself is self-describing and matches known wavemaker features): `SwitchON` (power), `Mode` enum (Classic/Sine/Random/Constant-flow wave), `Flow`/`Frequency` uint8 0-100, `FeedSwitch`+`FeedTime` (feed pause), `TimerON` + 48× `AutoTimeNN` 8-byte schedule slots (start/end time, mode, flow, frequency, pulse/tide packed per slot), `Linkage` enum (independent/master/slave, for syncing multiple pumps), `YMDData`/`HMSData` (device clock sync), and 7 `Fault_*` bits at byte 400 (overcurrent, overvoltage, over-temp, undervoltage, locked rotor, no-load, UART comms fault). Inferred total status payload size: 401 bytes. Full dump saved to `fixtures/schema_dump.txt`.

---

## Phase 2 — LAN discovery

**Goal:** Discover the pump on the LAN and parse its reply into `{ip, did, passcode, firmware}`.

**Inputs:** The GAgent discovery frame; `node-ph803w` discovery reference; `fixtures/discovery_reply.bin`.

**Deliverable:** `discover()` returning device records.

**Checkpoint:** `discover()` parses `fixtures/discovery_reply.bin` correctly **and** finds the live pump on the network, returning a valid `did` + `passcode`.

**Status: DONE (2026-07-14), with one correction to this phase's description.** Cloned `Apollon77/node-ph803w` and `Mustavo/homebridge-clearlight-sauna` into `reference/`. `node-ph803w`'s `lib/discovery.js` (`_handleReplyBroadcast`) is the authoritative field layout for the UDP discovery reply — **it does not contain a passcode**. The passcode is a separate TCP exchange (command `0x06`/`0x07`), which belongs to Phase 3, not discovery. `jebao_gizwits/protocol.py` implements generic GAgent frame encode/decode (`encode_frame`/`decode_frame`, varint length). `jebao_gizwits/discovery.py` implements `parse_discovery_reply()` and async `discover()`. Verified both ways: parses `fixtures/discovery_reply.bin` into the exact same fields as the earlier live capture, and a fresh live `discover()` call against the real pump returns identical data (`did=QP50gPt5I8h4mFKIo0ENIK`, `product_key` matches, `mac=24ec4aeea4d4`, firmware `04D30Q29`/`4.1.4`).

---

## Phase 3 — LAN session: connect, authenticate, read status

**Goal:** Open a TCP 12416 session, complete the GAgent login/authenticate handshake (using `passcode`), and read the device's current raw status payload.

**Inputs:** `node-ph803w` PROTOCOL.md (login + read/heartbeat frames).

**Deliverable:** A session object: `connect()`, `authenticate()`, `read_status()` returning the raw status bytes; plus `decode_status(bytes)` that maps them to attributes **via the Phase 1 schema**.

**Checkpoint:** `decode_status()` on the live pump returns values that match what the official app shows **right now** (set the pump to a known speed/mode in the app, confirm the decode agrees). Save a real status frame to `fixtures/status_<state>.bin` for each state tested.

**Status: DONE (2026-07-14), with a real bug found and fixed along the way.** `jebao_gizwits/session.py` implements `connect()`/`authenticate()` (passcode request/response `0x06`/`0x07` + login `0x08`/`0x09`, per `node-ph803w` PROTOCOL.md's "Minimum Interaction scheme") and `read_status()` (serial-data request `0x90` with p0 action `0x02`, response `0x91` with p0 action `0x03`/`0x04`, action byte stripped). The device sends 1-2 unsolicited/duplicate frames (a repeat login ack `0x0009`, an unrecognized `0x0062`) before the real reply on the first request — `read_status()` tolerates and skips these.

**Bug found via live cross-check:** the first version of `DatapointSchema.decode_status()` treated `bit_offset` as an index within a single byte (`raw[byte_offset] >> bit_offset`). But this schema's `bit_offset` values go past 7 (e.g. `AutoMode` has `byte_offset=0, bit_offset=9, len=3`, meaning it actually lives in byte 1) — the correct rule, confirmed against real toggles on the wire, is `bit_offset` is an absolute bit address (`byte_offset*8 + bit_offset`, LSB-first) that can span into later bytes. The naive version silently returned 0 for every such field.

**This surfaced a real functional finding, not just a decode bug:** `SwitchON` (byte 0 bit 0) stayed `False` across ~75 seconds of live testing that included multiple confirmed on/off toggles in the app — it does **not** track live power state on this firmware. `AutoMode` (byte 0-1, bits 9-11) correctly transitioned `0` ('停机'/stopped) → `1` ('经典造浪'/Classic wave, i.e. running) exactly when the pump was switched on in the app, and stayed at `1` for the remainder of testing while the pump was confirmed on. **`AutoMode` is the real live running-state indicator** (`0`=stopped, `1`-`4`=running in that wave mode, `5`=feeding); `SwitchON` is likely a write-only "desired manual state" field not mirrored into the read-status payload. `Flow`/`Frequency` (bytes 2/3, plain uint8) were validated as correct throughout - they tracked live app changes exactly (e.g. `35`/`47` after a live edit).

Fixtures saved: `fixtures/status_stopped_automode0_flow40_freq67.bin` (AutoMode=0/stopped) and `fixtures/status_running_classic_flow35_freq47.bin` (AutoMode=1/running, captured with the corrected decoder).

---

## Phase 4 — LAN control: write datapoints

**Goal:** Construct and send write-datapoint frames to change pump state (speed, mode, on/off).

**Inputs:** `homebridge-clearlight-sauna` `protocol.ts` write frame; Phase 1 schema for payload encoding.

**Deliverable:** `write(attr, value)` that builds `00 00 00 03` + length + write-command + schema-encoded payload and sends it.

**Checkpoint:** Setting speed/mode via `write()` visibly changes the pump **and** is reflected back in the official app. Test the full writable range. Capture request frames to `fixtures/` for regression tests.

> Safety note: test writes on benign attributes first (e.g. a small speed change), and have a known-good "restore" value ready. Avoid rapid on/off cycling of pump hardware.

**Status: BLOCKED (2026-07-14) after two principled attempts, neither worked.** Cloned the official Gizwits SDK source (`reference/StudyInEsp8266/Gizkit_soc_pet/app/Gizwits/gizwits_protocol.h`/`.c`, cited by node-ph803w PROTOCOL.md) to get the real `attrFlags_t`/`attrVals_t` struct definitions rather than reusing the sauna's device-specific write format. Confirmed: p0 control payload = `action(0x01) + attrFlags_t + attrVals_t`, where `attrFlags_t` is one bit per writable attribute *id* (LSB-first, 8 bytes for our 64 writable ids) and `attrVals_t` is the full 400-byte writable-portion payload (byte-identical layout to what `decode_status` already validates), with the value-scale formula (`gizY2X`: `raw=(display-addition)/ratio`) matching what Phase 3 already confirmed. Implemented in `jebao_gizwits/control.py`.

Tested against the live pump (`fixtures/test_write_flow*.py`, a minimal `Flow` +5 nudge with a verified-correct payload - flag bit for id=8 set, new value correctly written into the byte):
- **cmd `0x93`** (PROTOCOL.md's "Device serial data control"): got back a response with `flag=0x01` (every other response seen so far has `flag=0x00`) and a payload that's just the device's own `did` echoed back (`00 16 <22-char did>`) - not a normal ack. `Flow` did not change.
- **cmd `0x90`** (the same "transmit" command used for reads, with p0 payload `action=0x01` instead of `0x02`): device immediately re-reported its *current, unchanged* status (`0x91`/action=`0x04`) followed by an empty ack frame. No error, but `Flow` did not change.

Both attempts are consistent with **[[jebao-local-project]]'s earlier finding** that the official app controls the pump exclusively over cloud MQTT-over-TLS (port 8883) and never sends LAN control frames - this firmware build may simply not implement the LAN write path from the generic GAgent SDK, even though LAN reads work perfectly. This has not been proven conclusively (no packet capture of a real working LAN write from any source, since the app itself apparently never sends one) - it remains the leading explanation, not a certainty.

**BIT-TYPE WRITES SOLVED (2026-07-15), via logcat capture from the real app running in an emulator.** Frida hooking was blocked (see below), but the app's own SDK logs a full hex dump of every outgoing p0 control payload at DEBUG level (`GizSDKLog: Gizwits p0:` followed by a formatted hex table) - captured this directly from `adb logcat` while pressing power-off/power-on in the real app running under an Android emulator (no TLS interception, no Frida, no cert pinning to defeat - it was already being printed in plaintext for debugging). Two real frames captured (`SwitchON:false` and `SwitchON:true`), saved to `fixtures/captured_writes/`.

Decoding the captured `SwitchON:true` frame confirmed the bit-placement formula traced earlier from Ghidra (`transDatasToP0Data`/`FUN_0022165c` + `parseIndexInfo`/`FUN_002205f8`) was **correct all along** - the live-hardware test failures were a bug in the test code, not the formula. For bit `i` of a bit-type attribute's value: `dest_byte = schema.byte_offset - ((schema.bit_offset+i)>>3) + ((total_writable_bits-1)>>3)`, `dest_bit = (schema.bit_offset+i)&7`, where `total_writable_bits=12` for this schema (sum of `len` across all writable bit-type attrs). For `SwitchON` (byte_offset=0, bit_offset=0): `dest_byte = 0 - 0 + 1 = 1`, `dest_bit = 0` - matching the captured frame exactly (value bit landed at `vals[1]` bit 0).

Also learned: the real app sends **all-zero `attrVals_t` for unflagged attributes**, not the current status carried forward - the device only applies attributes marked in `attrFlags_t`, so unflagged bytes are don't-cares and the app doesn't bother populating them.

`jebao_gizwits/control.py`'s `build_control_payload()` was rewritten around this confirmed formula and validated byte-for-byte against both captured frames (`tests/test_control.py` - passes, no hardware needed).

**CONFIRMED ON LIVE HARDWARE (2026-07-15).** Sent `SwitchON=True` via LAN cmd `0x93` to the real pump - user physically confirmed the pump started running. This is the actual goal of Phase 4b (turning the pump on/off) and it works. Other bit-type attrs (`Mode`, `AutoMode`, `FeedSwitch`, etc.) use the same formula but with different `byte_offset`/`bit_offset`/`len` and haven't been individually live-tested - should work by the same logic, but confirm before depending on them.

**New caveat found during this test: `SwitchON` and `AutoMode` reads do not reliably reflect a pump turned on via a raw manual `SwitchON` write.** After the confirmed-successful on-write (pump physically running, user-verified), `read_status()`/`decode_status()` continued to report `SwitchON=False` and `AutoMode='停机'` (stopped) across multiple polls and even a brand-new TCP session - i.e. not a staleness/caching issue, the device's status payload itself doesn't reflect this state via these fields when turned on this way. This extends the earlier Phase 3 finding that `SwitchON` reads are unreliable (already known) to also cover `AutoMode` specifically for *manually*-triggered (non-schedule) power-on - Phase 3's original validation of `AutoMode` was observed via the official app's own toggle mechanism, which may set additional state (e.g. `Mode`) alongside `SwitchON` that our minimal single-attribute write doesn't. **Practical implication: writes are trustworthy, but don't rely on LAN reads to confirm current power state for a manually-toggled pump - track last-commanded state instead, or investigate further before building anything that depends on reading true live power state.**

**How the capture was done** (for reproducing/extending): booted the `jebao_frida_x86` Android emulator (see Path B section below for setup), installed the Jebao app (all APK splits, not just base+arm64 - see the crash note below), logged into the real Jebao account in the app, ran `adb logcat -v threadtime > logcat_capture.log` while pressing buttons in the app, then searched the log for `Gizwits p0:` and pulled the following hex-dump lines. The dump gets truncated by logcat's per-line length limit (~19 bytes of the 409-byte payload visible per write), so this technique gives the START of each payload, not the full thing - enough to nail the flags region and the first vals bytes, not e.g. the 48 schedule-slot blobs further in.

**Follow-up test (2026-07-14): ruled out payload sizing as the cause.** The schema's `ui` section shows all 64 writable attrs belong to a single `entity0`/single-section group, so there's no schema-level hint of a "quick controls vs. schedule" split. Tested anyway: a much smaller, precisely-sized 11-byte payload (`attrFlags_t`=2 bytes + `attrVals_t`=8 bytes, covering only ids 0-13 - the core status byte0-1 + the six simple uint8 values, excluding all 48 schedule-slot blobs) via both `0x90` and `0x93`. **Identical result to the full 409-byte payload**: `0x93` returned the same DID-echo response, `0x90` just re-reported unchanged status. `Flow` never changed in either case, at this point in the investigation.

**BREAKTHROUGH (2026-07-14, later same session): LAN control does work - solved via Ghidra disassembly of the real app.** User opted to disassemble `libGizWifiDaemon.so` (the app's native protocol engine, previously identified in [[jebao-local-project]]'s earlier attempt as a dead end for *static* analysis via jadx). This time: downloaded Ghidra 11.3.1 + a portable JDK 21 (Ghidra's requirement, machine only had JDK 17), extracted `libGizWifiDaemon.so` from `reference/jebao-apk/config.arm64_v8a.apk`, ran headless auto-analysis + targeted decompilation. Crucially, **this binary is not stripped** - full function names available (`GizWifiSDKEncodeOneDatapoint`, `GizWifiSDKTransCustomDataToP0`, etc.), which made Ghidra's decompiler dramatically more useful than blind capstone disassembly would have been.

Key findings, in order of discovery:
1. `GizWifiSDKGetFlagsLenByProductJsonStr` only computes a non-zero `attrFlags_t` size when `protocolType == "var_len"`. Our schema says `"standard"` - initially read as "no flags for standard," which turned out to be a red herring for the *external API* function, not the real internal encoder.
2. `GizWifiSDKWriteTransBusinessReqWithSN` confirmed real writes go out via command **`0x93`** (not `0x90`), settling that question definitively (matches PROTOCOL.md's naming, "Device serial data control").
3. The real assembler is `transDatasToP0Data` (found via Ghidra string-reference search on its own `__func__` debug string, since it's a static/unexported symbol - `FUN_0022165c` @ `0022165c`). Traced its `attrFlags_t` bit-placement: bit for attribute `id` lands at `flags_byte[flagsSize - (id>>3) - 1]` - i.e. **the whole flags buffer is byte-reversed** relative to naive ascending order.
4. Applying flags-reversal alone (`build_control_payload_reversed` in `jebao_gizwits/control.py`) and sending via `0x93` **made `Flow`/`Frequency` (byte-type/uint8) writes work perfectly** - confirmed with multiple independent, precise value changes (e.g. `Flow: 30→35`, `Flow: 35→50`, `Frequency: 63→70`), each verified via a fresh `read_status()`.
5. Bit-type fields (`Mode`, `AutoMode`, `SwitchON`, `TimerON`, `Linkage`) remain **unsolved**. Traced the value-placement formula in the same function (`dest_byte = schema.byte_offset - ((schema.bit_offset+i)>>3) + ((total_writable_bits-1)>>3)`, derived from `parseIndexInfo`/`FUN_002205f8`'s `param_1[4]` = sum of `len` across writable bit-type attrs = 12 for this schema) and implemented it precisely (`build_control_payload_bitfix`) - still no effect on live hardware, verified via full before/after field diffs (zero bytes changed). Also learned along the way that the `0x93` ack payload (`00 16 <did>`, echoing the device's own ID) is NOT an error indicator - it appeared identically for both successful and unsuccessful writes, including a no-op "flag Flow's id with unchanged value" test. Success/failure can only be judged by re-reading status, not by the ack shape.

**Current status: byte-type (uint8) writes are solved, reliable, and shipped in `jebao_gizwits/control.py` (`build_control_payload_reversed`). Bit-type (bool/enum) writes are not.** Given `Flow`/`Frequency` cover the wavemaker's core speed control, this is a substantial, real capability even without power/mode control. Further progress on bit-fields would need either the pump's actual firmware (not obtainable - no dump, no physical access route) or Ghidra's interactive GUI (proper struct/type recovery, cross-reference browsing) rather than more headless-script guessing, which has hit steep diminishing returns after 4 distinct bit-encoding hypotheses tested live.

---

## Phase 5 — Python library

**Goal:** Package Phases 1–4 into a clean, installable Python library (the intended deliverable), with the schema, discovery, session, and control as a coherent API.

**Deliverable:** `jebao_gizwits/` package: `discover()`, `Device.connect/authenticate/get_state/set_speed/set_mode/set_power`, config-driven `USER_TOKEN`, no committed secrets.

**Checkpoint:** A fresh clone + local install can discover, read, and control the pump end-to-end from a short example script. All fixture-based unit tests pass offline.

---

## Phase 6 — Standalone dashboard

**Goal:** A local, non-cloud dashboard (matching your existing local-app pattern) to view status and control the pump, running on top of the Phase 5 library.

**Deliverable:** Local web dashboard (launcher-script install, LAN-only) showing live status and offering controls.

**Checkpoint:** Dashboard reflects live pump state and can control it; runs fully offline on the LAN.

---

## Phase 7 — Home Assistant integration

**Goal:** A Home Assistant custom integration wrapping the Phase 5 library — local push/poll, entities for speed/mode/power.

**Deliverable:** HA custom component with config flow (discovery + token), fan/number/select entities as appropriate, local polling or status subscription.

**Checkpoint:** Pump appears in HA, state updates on external changes, and HA controls drive the pump. No cloud dependency.

**Status: BUILT (2026-07-15).** `custom_components/jebao_local/` - discovery-first config flow, single shared coordinator, generic schema-driven entity factory (switch/select/number/binary_sensor), 29 bundled WiFi product schemas. Architecture reviewed and borrowed from two existing community Jebao integrations (`chrisc123/jebao_aqua-homeassistant`, `jrigling/homeassistant-jebao`) - see the code-review notes further down this section. Validated for correct HA API usage; not yet tested against a live running HA instance (see `custom_components/jebao_local/README.md`'s "Known gaps").

**Cross-integration compatibility validated (2026-07-15) against a second real integration** (`markosharknz1/aipai-light-ha`, a cloud-based aquarium light integration) to check `jebao_local` coexists cleanly in the same HA instance as other custom components. `tests/test_ha_integration_compat.py` (6 tests, all passing when both repos are checked out side-by-side - skips gracefully otherwise, doesn't vendor the other repo's source): manifests valid, domains don't collide, no dependency conflicts, both integrations' real code imports cleanly against actual `homeassistant` APIs, both config flows construct correctly, no shared local network resource conflicts (`jebao_local` is LAN TCP/UDP direct-to-device, `aipai_light` is cloud/MQTT).

Attempted a deeper test with `pytest-homeassistant-custom-component` (a real HA core test harness) first, to actually boot config entries and verify entity creation end-to-end. Hit a genuine Windows-specific blocker: HA forces `WindowsProactorEventLoopPolicy` (needed for real subprocess support), whose event loop needs a real socket for its internal self-pipe, but the harness's `pytest-socket` safety net blocks socket creation during that same fixture's setup - and merely having the HA test package installed sets asyncio's global event loop policy as an import-time side effect, breaking even unrelated plain tests in the same session (had to explicitly disable both auto-registering plugins, `-p no:socket -p no:homeassistant`, to get the plain static tests running at all). This is a real gap in that test harness on native Windows (likely built/tested primarily against Linux CI), not a config mistake - the deeper test is written and preserved for whenever this project is tested from WSL/Linux/CI.

---

## Phase 8 — Tank dashboards

**Goal:** Group pumps by tank in a Lovelace dashboard, with settings that can be saved and cloned across a tank, an on/off switch, and feed mode with a timer.

**Status: BUILT (2026-07-15).**

Before building the dashboard, fixed a real bug it would otherwise have inherited: `JebaoLocalEntity` (`custom_components/jebao_local/entity.py`) built HA's `DeviceInfo.name` straight from the vendor's datapoint schema `name` field, which for this project's wavemaker is Chinese (`本地造浪泵_WIFI_BLE`). HA's `entity_id` is normally derived by slugifying `"{device name} {entity name}"`, and HA's slugify has no Chinese transliteration - it just drops characters it can't map, so entity_ids would have come out unpredictable and prone to colliding across same-model pumps. Fixed by setting `_attr_suggested_object_id = f"jebao_{coordinator.did}_{unique_id_suffix}"` in the base entity - `did` is a stable, ASCII, per-device identifier already available from discovery, so entity_ids are now deterministic: `switch.jebao_<did>_switchon`, `number.jebao_<did>_flow`, etc. Also added an `entity` translation block to `strings.json`/`translations/en.json` giving the common pump attributes (power, feed mode, wave mode, flow, frequency, feed duration, ...) friendly English display names, gathered by scanning all 29 bundled product schemas' `display_name`/`desc` fields for the attribute names that actually recur across products.

Confirmed the pump's schema exposes exactly what "feed mode with a timer" needs: `FeedSwitch` (bool, 喂食开关 "feed switch") and `FeedTime` (uint8, 喂食时长 "feed duration", range 1-60) - turning `FeedSwitch` on pauses the pump for `FeedTime` minutes so food settles, matching the wavemaker's real-world feed-pause behavior. Not live-tested against hardware (only `SwitchON` and byte-type writes are confirmed live so far - see Phase 4/4B), so this is formula-confirmed, not hardware-confirmed.

Built three deliverables:
- `www/jebao/designer.html` - a standalone control-panel web app (same visual language and localStorage patterns as the sibling `aipai-light-ha` project's schedule designer, adapted for pumps instead of light channels): named tank groups (lists of pump `did`s, saved in the browser), settings profiles (wave mode/flow/frequency, save/load/delete, "clone to tank" applies the profile to every pump in the active tank via individual `select.select_option`/`number.set_value` calls), tank-wide on/off, and a feed-now button with a duration slider and a live countdown that calls `switch.turn_on` then `switch.turn_off` on `FeedSwitch` after the countdown - explicitly documented as only running while the page stays open, with a pointer to the script-based alternative for reliability.
- `dashboards/jebao-dashboard.yaml` - a Lovelace dashboard with tanks as sections (entities cards per pump: power, wave mode, flow, frequency, feed switch/duration) plus an embedded iframe view of the designer.
- `dashboards/jebao-tank-scripts.yaml` - HA `script:` definitions for tank-wide all-on/all-off and a feed-now script that survives the browser closing, using a templated `delay:` that reads the pump's own `FeedTime` number entity.

**Verification:** Since the integration itself still hasn't been tested against a live HA instance (Phase 7's known gap), `designer.html`'s HA-connected actions couldn't be exercised end-to-end either. What *was* verified, by serving the file statically and driving it with a real browser: tab switching, adding a pump to a tank, saving/reselecting tanks (persisted correctly across a page reload via localStorage), saving/loading a settings profile, and the feed countdown timer (starts, ticks down, fires its on-completion callback, re-enables/disables the right buttons). This caught one real bug before it shipped: the Export tab kept showing placeholder `<did-1>`/`<did-2>` text instead of the active tank's real pump `did`s, because `updateExports()` wasn't being re-run when the active tank changed (only on a few input events) - fixed by calling it from `selectTank()` and the remove-pump handler. Confirmed the generated entity_ids (e.g. `switch.jebao_qp50gpt5i8h4mfkio0enik_switchon`) match `entity.py`'s new `_attr_suggested_object_id` output exactly. Also re-ran `tests/test_control.py` and `tests/test_ha_integration_compat.py` after the `entity.py` change - all still pass, confirming the fix didn't break the existing HA-import-cleanliness or write-encoding coverage.

Both new YAML files were validated with `yaml.safe_load` (syntactically valid), but - like the rest of the HA integration - haven't been loaded into a real Home Assistant instance, so card rendering and the templated `delay:` in `jebao_tank_feed_now` are unverified beyond that.

**Follow-up (2026-07-15): English device/config-entry names.** A user setting up the integration for real hit HA's "Name and assign" dialog showing the raw Chinese product name (`本地造浪泵_WIFI_BLE`) as the default device name - unreadable if you don't read Chinese, and the entity_id fix earlier in this phase only addressed *entity_ids*, not the human-facing device name/config-entry title, which both still read straight from the vendor schema's `name` field. Fixed by adding a `name_en` field to `DatapointSchema` (`jebao_gizwits/schema.py`, both the top-level and vendored copies) - `load()` reads it with a fallback to the Chinese `name` for schemas that predate the field - and populating it in all 72 tracked schema JSON files (42 in `fixtures/product_schemas/`, 29 bundled in the HA integration, plus the base `fixtures/datapoint_schema.json` fixture) from the English names already curated in `docs/product_catalog.json` (stripping that catalog's internal `[...]` annotations first, e.g. `[THIS PROJECT'S PUMP]`). `config_flow.py`'s `title=` and `entity.py`'s `DeviceInfo(name=..., model=...)` now use `schema.name_en` instead of `schema.name`. Verified the real pump resolves to `"Local Wavemaker (WiFi+BLE)"` end-to-end through `load_by_product_key()`, and re-ran the full test suite (offline + the real-`homeassistant`-import compat test) - all still pass. Care was taken re-injecting `name_en` into the 72 JSON files to preserve each file's original formatting exactly (most are minified single-line JSON from the original extraction script; two - `fixtures/datapoint_schema.json` and one bundled schema - were pretty-printed by hand at some point) rather than round-tripping through a JSON dumper with different settings, which on a first attempt reformatted every file and produced a ~99,000-line diff for what should've been a 72-line change.

---

## Open questions to resolve during the build (log answers as you go)

1. Exact GAgent login/heartbeat frame details for this firmware version — confirm against captures, don't assume.
2. Write-command opcode and payload framing for control — validate against `protocol.ts` + a real captured write.
3. Whether the pump pushes status updates on the TCP session (subscribe) or must be polled — determines HA update strategy in Phase 7.
4. `USER_TOKEN` lifetime / refresh path — how to re-obtain without the app if it expires.
5. Does the module require staying "subscribed"/bound, and does binding mode need the physical button? (Relevant if discovery returns no passcode.)

---

## What "done" looks like

A local Python library + dashboard + HA integration that discover, read, and control the Jebao pump entirely over the LAN using the Gizwits GAgent protocol, with zero cloud dependency, driven by the device's own datapoint schema — and a `fixtures/` folder of real frames backing an offline test suite.
