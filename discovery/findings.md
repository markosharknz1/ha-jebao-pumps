# Findings Log

Running log for Phase 1 (Protocol Discovery). Every conclusion here must cite a capture file in `discovery/captures/` or describe a specific live test performed — no unverified guesses.

Phase 1 is not complete until this file answers all six questions in SPEC.md §1D.

## Status

In progress. Hardware confirmed available: pump paired in Jebao Aqua app, on same WiFi as dev machine.

## 1A — Gizwits over WiFi?

**Pump IP:** `192.168.1.77` (MAC `24-ec-4a-ee-a4-d4`), identified via router admin device list on 2026-07-14.

**Finding: no TCP LAN listener at all.**
- `discovery/scan_lan.py` swept the full `192.168.1.0/24` subnet on candidate IoT/Gizwits ports (80, 443, 12416, 6668, 8888, 9999) — pump did not respond on any.
- `discovery/scan_host_ports.py` then did a full TCP sweep of **all 65,535 ports** against `192.168.1.77` directly — zero open ports.
- Live test performed 2026-07-14, pump powered on and paired/connected per the app. This is a direct probe result, not a guess.
- **Implication:** the pump does not run a local TCP server at all (not on port 12416, not anywhere). If it's reachable locally over WiFi, it's not via a listening TCP socket — either it's purely outbound-to-cloud, or it uses UDP, or WiFi local control isn't exposed on this hardware. TCP-based Gizwits LAN handshake (1A) is very likely a dead end on its own — **not yet tested**: UDP-based discovery/control (some Gizwits/Tuya-family devices use UDP broadcast, e.g. 6666/6667) has not been ruled out.
**Correction — the pump DOES speak Gizwits, over both UDP and TCP.** The full-port TCP scan above was misleading: it was a burst of 512 concurrent connection attempts across all 65535 ports, which very likely overwhelmed the pump's embedded (lwIP-class) TCP stack and caused it to silently drop the SYN for port 12416 specifically. Confirmed by retesting port 12416 alone immediately afterward — it connected on the first clean attempt. **Lesson for future scans against this hardware: don't burst-scan it; probe candidate ports individually or with low concurrency.**

### UDP discovery (port 12414) — WORKS

`discovery/test_udp_discovery.py` replicates the exact broadcast from the chrisc123 repo (`custom_components/jebao_aqua/discovery.py`): 8-byte payload `00 00 00 03 03 00 00 03` to `255.255.255.255:12414`. The pump responded immediately (live test, 2026-07-14) with a 127-byte Gizwits-formatted response containing:
- `device_id` (Gizwits): `QP50gPt5I8h4mFKIo0ENIK`
- MAC: `24:ec:4a:ee:a4:d4` (matches router's ARP entry for 192.168.1.77)
- Model code: `04D30Q29`
- **`product_key`: `54114ccdac1e41c0bb17e222887c07ba`**
- Cloud host: `usapi.gizwits.com:80` (US region)
- Firmware version: `4.1.4`

This means **the product_key can be extracted directly from a local UDP broadcast — no cloud login or APK decompilation required** to identify which datapoint model applies to a given pump.

### TCP LAN control (port 12416) — WORKS, root cause of "old repos fail" identified

The chrisc123 repo's `product_key` `54114ccdac1e41c0bb17e222887c07ba` **exactly matches** a bundled model file already in that repo: `custom_components/jebao_aqua/models/54114ccdac1e41c0bb17e222887c07ba.json`, labeled `"Wavemaker Pump (New BLE enabled MLW Series - TBC?)"` — i.e. the repo author already reverse-engineered this exact pump model's datapoints but flagged it "to be confirmed."

Ran the repo's local status-read handshake live against the pump (`discovery/test_gizwits_lan.py`, connects to `192.168.1.77:12416`):
1. TX `00 00 00 03 03 00 00 06` → RX 20 bytes, last 12 bytes = binding key
2. TX command `0x0008` + binding key → RX 9-byte ack
3. TX command `0x0093` + `00 00 00 02 02` (status query) → **RX 457 bytes across two reads**

**The handshake and status query fully work.** The reference repo's own response parser (`_extract_device_status_payload` in `api.py`) is broken for this hardware: it assumes the whole response is a single Gizwits-framed message and just slices the last N bytes of the buffer using the length from the *first* message header. In practice the pump replies with **three separate framed messages** back-to-back (two small acks, command `0x0062` and `0x0009`, then the real status push on command `0x0094`, 430-byte payload) — sometimes arriving across two separate TCP reads. A parser that doesn't walk each frame individually and pick out `0x0094` will get garbage or nothing.

Wrote a proper multi-frame parser (`split_messages` in `test_gizwits_lan.py`) that walks the buffer by repeated `00 00 00 03` + LEB128-length headers. Feeding the `0x0094` message's payload into the existing model file's `parse_device_status` logic produced fully correct, sensible live values (pump was off at capture time):

```
SwitchON: False, PulseTide: False, FeedSwitch: False, TimerON: False
Mode: 经典造浪 (Classic/Square Wave), Linkage: 独立 (Independent)
Flow: 0, Frequency: 2, FeedTime: 0
```

Fixture saved: `discovery/captures/status_query_response_pump_off_2026-07-14.txt` (full hex + parsed output).

**Caveat:** the `Fault_*` fields (byte_offset 400 in the model) read as noise — they land in a run of `0xee` padding bytes past the real 430-byte payload, not actual fault data. The model's fault-block byte offset is likely wrong for this firmware, or faults are reported in a different message. Not blocking for Phase 1; flag for verification in Phase 2 once we test with an actual fault condition, or by decoding more of the raw payload structure.

**Conclusion for 1A:** the WiFi/Gizwits transport works on the new BLE-capable hardware, unmodified from the legacy protocol (UDP discovery on 12414, TCP control on 12416, same framing). The prior repos' failure was very likely a **client-side response-parsing bug** (single-message assumption), not a missing datapoint model or a protocol change on the pump's side. **Decision gate: WiFi protocol is solved. BLE (1C) is now optional/bonus rather than required**, though still worth doing for completeness and because BLE-only control could matter if WiFi drops.

### Control write — attempted, frame format accepted but wrong opcode for this device

Discovered a third reference source: pip package `python-jebao==0.1.6` (dependency of the jrigling repo, not bundled in either repo's source — downloaded and extracted to `reference/python-jebao-pkg/`). It implements a much more complete protocol client, built specifically for the **MDP-20000** (dosing pump), a different Jebao product line than our wavemaker. Its `const.py` confirms our observed opcodes are the real Gizwits message types, not device-specific: `MSG_CONTROL_OR_EXTENDED_REQUEST = 0x93`, `MSG_CONTROL_RESPONSE_OR_EXTENDED_DATA = 0x94` — exactly what we saw working for reads. So 0x93 is dual-purpose: a small 5-byte payload → status read; a full 323-byte frame with opcode/param bytes at offsets 21-24 → control write.

Ran `discovery/test_gizwits_write.py` against the live pump: built the 323-byte control frame per python-jebao's format, using its `TURN_ON_OFF` opcode (opcode1=0x01, opcode2=0x01 for "on"). Live test, 2026-07-14:
- Pump accepted the frame and responded with what looks like a legitimate protocol ACK (command 0x0094, short payload echoing the device_id) — not an error or dropped connection. So the 323-byte frame *shape* is understood by this firmware.
- Status re-read immediately after: `SwitchON` unchanged (still False). The MDP-20000-specific opcode did not toggle this wavemaker.

**This is expected, not a dead end** — MDP (dosing) and MOW (wavemaker) product lines have entirely different datapoint models (dosing ml/channels vs flow%/frequency/mode/linkage), so their opcode-to-attribute mappings at bytes 21-24 are almost certainly different too. Fixture saved: `discovery/captures/write_attempt_2026-07-14.txt`.

## 1B — WiFi packet capture (Android, PCAPdroid)

Captured with PCAPdroid on the Android phone running Jebao Aqua, filtered to that app, across two sessions (2026-07-14, ~06:56-06:58 and ~06:58-07:02 local phone time), while performing: power on, power off, power on + flow change, mode change. **Exported as PCAPdroid's CSV connection log, not a raw `.pcap`** — so this only shows connection metadata (src/dst/port/protocol/bytes), not actual packet payloads.

**Finding: the official app is cloud-only for control — it never opens a local TCP connection to the pump.** Across ~6 minutes of capture spanning multiple pump actions, there is zero TCP traffic to `192.168.1.77` or port 12416 anywhere in either log. Only local-network activity is the expected UDP discovery broadcast (port 12414, matches 1A). Everything else routes through Gizwits cloud infrastructure:
- HTTPS + plain HTTP to `usapi.gizwits.com` (matches the region/host reported in the UDP discovery response)
- **TLS on port 8883 to `usm2m.gizwits.com`** — 8883 is the standard MQTTS port, strongly suggesting Gizwits uses MQTT-over-TLS as the real-time control/push channel
- HTTPS to `appmonitor.gizwits.com` (telemetry/analytics, not device control)

**Implication:** the app's own traffic can't be used to observe local LAN write commands, because it doesn't send any — even when phone and pump are on the same network. This isn't evidence that local write control is impossible (1A already proved local *reads* work fine over TCP/12416), just that we can't learn the write opcodes by watching the official app. The MQTT/HTTPS cloud control payloads likely use the same underlying Gizwits datapoint/attribute-ID encoding we already decoded locally (same product data model, different transport) — if captured with payload bytes, they'd very likely reveal the write format that could then be replayed unencrypted over the local TCP/12416 connection.

**Follow-up capture (2026-07-14, later same session):** user enabled PCAPdroid's TLS decryption (its own local CA, installed on the phone) and re-captured as a real `.pcap` (`discovery/captures/pcapdroid_session3_2026-07-14_decrypt_attempt.pcap`, 215 packets) while repeating the same pump actions. Parsed with scapy.

Connection breakdown confirms the control channel: **95 packets on TCP port 8883 to `52.2.9.108`** (MQTTS, by far the busiest connection — matches the earlier CSV finding). Also present: HTTPS to `52.5.104.140`/`175.178.204.40` (port 443), and plain HTTP to `52.5.104.140:80`.

**The plain HTTP traffic decoded cleanly** (no decryption needed): `GET /app/datapoint?product_key=54114ccdac1e41c0bb17e222887c07ba` with headers `x-gizwits-application-id: c3703c4888ec4736a3a0d9425c321604` and a live `x-gizwits-user-token`. Response: `304 Not Modified` (client's cached copy, matching ETag, is up to date) — i.e. this just confirms the product_key/datapoint schema we already have from the bundled model file; not new information, and not the control channel.

**TLS decryption did not work, on any connection** — inspected raw bytes of the port 8883 (MQTTS) and port 443 (HTTPS) traffic; both are still genuine encrypted TLS records (0x16 handshake, 0x17 application data) despite PCAPdroid's CA being installed and enabled. This strongly suggests the app uses certificate pinning and/or a network security config that rejects user-installed CAs — a common hardening choice for IoT cloud vendors — applied consistently across all its connections, not just MQTT. Defeating this would require heavier tooling (rooting the phone + Frida-based SSL-unpinning, or repackaging the APK to strip the pinning) — not attempted, out of proportion for this stage.

**Conclusion for 1B:** live capture of the app's cloud control traffic is a dead end without device-rooting-level effort. The Gizwits application-id (`c3703c4888ec4736a3a0d9425c321604`) is confirmed to match the constant already in the chrisc123 reference repo. Next productive path is **1E (APK static analysis)** — decompiling the APK doesn't depend on defeating live TLS at all, and jadx is already installed on this machine.

## 1E — APK analysis

Obtained `Jebao Aqua 3.3.55` as an XAPK from APKPure (2026-07-14) after APKMirror had no listing for this app — noted as lower-provenance than an ADB pull (community-uploaded, not signature-verified against the developer), used with that caveat in mind. Saved to `reference/jebao-apk/Jebao_Aqua_3.3.55_APKPure.xapk`. Extracted `base.apk` (`com.jebao.android.apk` inside the XAPK) and the `arm64_v8a` split. Decompiled `base.apk` with jadx 1.5.5 (invoked directly via `java -cp jadx-gui-*-all.jar jadx.cli.JadxCLI`, since only the GUI launcher was installed via winget) → `reference/jebao-apk/decompiled/` (12,766 classes, 79 errors — normal for an app this size).

**The app is React Native.** Found `resources/assets/templates/.../com.gizwits.rn.jiebao.zaolang/` ("造浪" = wave-making) — confirmed as our exact wavemaker's UI template by its image assets (`classic_btn`, `sine_btn`, `radom_btn`, `constantspeed_btn` — matching our `Mode` enum's 4 values exactly; `feeding_btn`, `timing_on_btn`, `linkage_on_btn` matching our other attributes). Its `index.js` bundle's default state object confirms our attribute names (`SwitchON`, `FeedSwitch`, `FeedTime`, `TimerON`, `Frequency`, all `Fault_*` names) and reveals a few more not in our bundled model: `Motor_Speed` (possibly an alias/variant of `Flow`), `AutoMode`, `AutoGears`, `AutoFeedTime`, `AutoTime00`-`AutoTime03` (6-element arrays — likely scheduling data: hour/min/sec/day fields), `YMDData`/`HMSData` (date/time). No control/write bridge call is visible in this JS — it only manages UI state.

**Root cause of why writes aren't visible in decompiled source: the protocol engine is a compiled native library, not Java or JS.** `com/gizwits/gizwifisdk/GizWifiDaemon.java` declares `native` JNI methods and calls `System.loadLibrary("GizWifiDaemon")`. The actual library, `lib/arm64-v8a/libGizWifiDaemon.so` (3.19 MB, found in the `arm64_v8a` split), is where LAN discovery, the TCP handshake, and — almost certainly — the control/write packet construction actually happen. This is compiled ARM64 machine code; recovering the opcode-to-attribute mapping from it would require a disassembler (Ghidra/IDA), not jadx. That's a substantially larger undertaking than APK decompilation and wasn't attempted — flagged as a possible future path, not pursued this session.

**Conclusion for 1E:** confirms the GIZWITS_APP_ID constant, product_key, and attribute-name set already established, and confirms this is a React Native app whose actual wire protocol is implemented in a closed, compiled native library. No new opcode-level information recovered.

### Bounded opcode brute-force — completed, no hits

Ran `discovery/test_gizwits_bruteforce.py` (2026-07-14): single persistent connection, auth handshake once, then swept `opcode1` from `0x00` to `0x50` (81 values) using the same 323-byte MDP-style control frame, `opcode2=1` fixed, reading status back after each and diffing against baseline. **Zero changes detected across the entire range.** Final status after the sweep matched the original baseline exactly.

**Interpretation:** the pump ACK-ing this frame (seen earlier in the single-opcode test) does not necessarily mean it's actually parsing opcode1/opcode2/param1/param2 as MDP-20000 does — it may just be gracefully acknowledging any well-formed `0x93` extended-length message regardless of content. Two live hypotheses not yet tested:
1. The frame *shape* itself is wrong for this device (different byte offsets for the command fields within the 323 bytes, or a different total frame size).
2. Writes might use a **different message type entirely** — command `0x92` was never tried (only `0x93`, which we've confirmed is used for reads). A common convention in older Gizwits/ESP8266 "GAgent" firmware is `0x92` = control/set, `0x93` = read/get, with the write payload mirroring the same bit-packed layout as the status response (byte0 bit0 = SwitchON, etc.) rather than a fixed opcode/param scheme. Worth a quick targeted test before escalating to Ghidra or Frida.

### Targeted 0x92 hypothesis test — ruled out

Ran `discovery/test_gizwits_0x92.py`: tried command `0x92` (hypothesis: separate control/set opcode, per common older Gizwits/ESP8266 convention) with four payload shapes (raw byte0 value, read-command-mirrored prefix, mask+value, and a `0x93`-with-write-shaped-payload variant). **`0x92` got zero response in all three variants** — the device silently ignores it entirely, unlike `0x93` which always ACKs something. This cleanly rules out the "separate 0x92 write opcode" hypothesis for this firmware. The `0x93`-with-small-payload variant got an ACK but no attribute change.

**Net result after four concrete attempts this session** (single MDP-opcode test, 81-value opcode1 brute-force, four 0x92 payload variants, one 0x93 write-shaped-payload variant): **no combination produced any observable state change.** The write path for this wavemaker remains unsolved. Given the volume of live-hardware writes already attempted with no signal, further blind guessing has a low expected payoff — the remaining paths (native library disassembly, or defeating TLS pinning to read the real cloud control payload) both require real ground-truth data rather than more guessing.

## Summary: paths to find the wavemaker's write opcodes (none completed yet)

1. **Bounded local opcode brute-force** — try a range of `opcode1` values (0x00-0x40 or so) against the pump over the existing working TCP/12416 connection, polling status after each and stopping on any change. Lowest effort, uses infrastructure already built and verified this session. Not yet attempted beyond the single MDP-20000-opcode test; needs explicit go-ahead given it's multiple real hardware writes.
2. **Root the phone + Frida SSL-unpinning** to defeat the app's certificate pinning and make the live-capture route (1B) readable. Moderate-high effort.
3. **Disassemble `libGizWifiDaemon.so`** with Ghidra/IDA. High effort, most complete answer if pursued.

**Gizwits cloud API login (username/password) was not attempted** — per policy, Claude does not perform password-based authentication on the user's behalf. It also turned out to be unnecessary: the product_key and datapoint model were obtained entirely from local UDP discovery + the repo's bundled model file, with zero cloud interaction. If cloud datapoint-fetching is ever needed for a different model, the user would need to run that login step themselves.

## 1B — WiFi packet capture

_(pending — only needed if 1A fails)_

## 1C — BLE reverse engineering

_(pending)_

## 1E — APK analysis

_(pending)_

## Open questions status

- Do the pumps expose any LAN listener at all, or is WiFi purely pump→cloud? — **Answered: yes.** TCP 12416 LAN listener confirmed working for status reads, no cloud round-trip needed.
- Can BLE and WiFi be used simultaneously, or does app pairing lock one out? — unanswered (this pump is a wavemaker/MOW-class; MDP dosing-specific question, and BLE testing generally, still pending)
- Do MDP dosing schedules live on the pump (survive network loss) or in the cloud? — unanswered (no MDP unit tested yet, this session used a wavemaker)
- Is there per-device authentication (passcode/key) on the LAN protocol, and where does the app store it? — **Partially answered:** no passcode/token needed for local status reads — just the binding-key handshake (steps 1-2), which the pump hands out to anyone on the LAN who asks. No app-stored secret was required. Whether *writes* require additional auth is untested.
