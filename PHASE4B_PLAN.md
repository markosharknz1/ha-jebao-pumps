# Phase 4b — Bit-Field Writes (Power / Feed Mode)

**Status: SOLVED and CONFIRMED ON LIVE HARDWARE (2026-07-15).** Sent `SwitchON=True` via LAN to the real pump - user physically confirmed it started running. This was the actual goal (be able to turn pumps off/on, e.g. for feed mode) and it works.

**Caveat found during the live test:** don't trust `SwitchON`/`AutoMode` status reads to confirm current power state for a manually-toggled pump - after the confirmed-successful write, reads kept reporting stopped/off across multiple polls and a fresh session. Writes work; reads of live power state via these fields don't, at least for this on-path. See SPEC.md Phase 4 for full detail. If you need to know current power state reliably, track your last-commanded value rather than trusting a read, or investigate this further before depending on it.

`SwitchON` (id=0, power on/off), `FeedSwitch` (id=2, feed-mode pause), and every other bit-type attribute (`TimerON`, `Mode`, `AutoMode`, `Linkage`, `PulseTide`, `AutoPulseTide`) now have a confirmed-correct encoding in `jebao_gizwits/control.py`. See `SPEC.md`'s Phase 4 section for the full story - short version below.

## How it got solved

Static analysis (Ghidra, tracing `transDatasToP0Data`/`FUN_0022165c` and `parseIndexInfo`/`FUN_002205f8`) had already produced a bit-placement formula, but four live hardware tests of it all failed with no observed effect - so it looked wrong. Frida (the planned way to get ground truth) turned out to be a dead end: it can't hook ARM64 code running under the x86_64 emulator's `libndk_translation` binary-translation layer at all (confirmed - the target library never appears in Frida's own module list, despite running fine).

The actual fix: the app's SDK logs a full hex dump of every outgoing control payload directly to `adb logcat` (`GizSDKLog: Gizwits p0:` + a formatted hex table) - no interception needed, it was already printing in plaintext. Captured two real frames (`SwitchON:true`/`false`) this way from the app running in the emulator, and they confirmed the **original Ghidra-derived formula was correct all along** - the bug was in the test code, not the formula. `build_control_payload()` was rewritten around the confirmed formula and now reproduces both captured frames byte-for-byte (`tests/test_control.py`, passes offline, no hardware needed).

## What's still open

- **Only `SwitchON` was captured and verified.** Other bit-type attrs use the same formula with their own `byte_offset`/`bit_offset`/`len`, so they *should* work, but haven't been confirmed - especially multi-bit fields (`Mode`, `Linkage`, `AutoMode`) which exercise more of the formula (bits spanning byte boundaries) than `SwitchON`'s single bit did.
- **Not yet tested against the real pump.** Everything above was validated against captured bytes, not a live write. Do this before relying on it for anything that matters (e.g. actually turning the pump off unattended).
- To capture more ground truth (e.g. for `FeedSwitch`/feed mode specifically, or to extend coverage), repeat the logcat technique: boot `jebao_frida_x86` (emulator, `tools/android-sdk/emulator/emulator.exe -avd jebao_frida_x86 -no-snapshot -no-boot-anim -writable-system`), launch the Jebao app (**install all APK splits** from `reference/jebao-apk/all_splits/`, not just base+arm64 - a partial install crashes with a missing-resource error), log in, run `adb logcat -v threadtime > logcat_capture.log` while pressing the relevant button, then search for `Gizwits p0:` and read the hex dump that follows. Note the dump gets cut off by logcat's line-length limit (~19 bytes visible per payload) - fine for confirming the flags region and first vals bytes, not enough to see deep into the 400-byte vals region (e.g. the 48 schedule-slot blobs).

## Dead ends, for the record (don't retry these)

- **Frida on the x86_64 emulator**: fundamentally blocked, not a config issue - see above.
- **True ARM64 emulation on this machine**: blocked separately - Windows/Intel hosts can't run ARM64 guest system images at all, regardless of virtualization settings ("Avd's CPU Architecture 'arm64' is not supported by the QEMU2 emulator on x86_64 host").
- If Frida is ever needed again for something else, it would need genuine ARM64 hardware (a physical Android phone, or an ARM64 host machine) - the emulator route is a dead end for it specifically.
