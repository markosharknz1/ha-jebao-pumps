# Methodology: How This Was Actually Figured Out

This project went through several genuinely different investigative
techniques before landing on working answers. If you're reverse-engineering
a similar closed IoT protocol, the sequence of what worked and what didn't
is probably more useful than the end result alone - so here it is, roughly
in the order it happened.

## Starting point: wrong assumption, correct pivot

The project's first attempt assumed the pump used some bespoke, undocumented
encryption scheme and tried to "decrypt" it. That framing was wrong. A
packet capture of the device's discovery broadcast showed a `product_key`
and firmware strings that identified it as running completely standard
**Gizwits GAgent** firmware - a known, documented IoT platform used by many
unrelated vendors, not a Jebao-specific invention. Once that was clear, the
project restarted from a "implement the documented protocol" framing
instead of "break the encryption" - a much better foundation, and the
lesson generalizes: before reverse-engineering anything, check whether
you're actually looking at an off-the-shelf platform with existing public
documentation and reference implementations.

## Reads: reference implementations + real captures, no guessing

Discovery, session setup, and status reads were solved by combining:
1. **A public reference implementation for the same firmware family**
   ([`node-ph803w`](https://github.com/Apollon77/node-ph803w), built for an
   unrelated pH meter product on the same GAgent platform) - authoritative
   for frame structure, since it's real working code, not a guess.
2. **Live packet captures against the actual pump** to confirm the
   reference's framing matched byte-for-byte, and to fill in
   product-specific details (the datapoint schema).

The one real bug found during this phase was subtle: the datapoint schema's
`bit_offset` field can exceed 7 (meaning it spans past the byte named by
`byte_offset`), and a naive decoder that treated it as "shift within one
byte" silently zeroed out any such field. This was caught by cross-checking
a decoded attribute (`AutoMode`) against the pump's real behavior while
physically toggling it in the vendor app - the decoded value never changed,
which was the tell that something was structurally wrong, not just a
threshold/scaling issue.

**Lesson:** for the read path, "find a reference implementation for the
same underlying platform, then validate against live captures" is a
reliable, low-effort strategy. It does not work for writes - see below.

## Writes: five different techniques, four of them dead ends

Getting *reads* working was straightforward. Getting *writes* working (in
particular, anything involving a boolean/enum attribute like power on/off)
took five distinct approaches, in this order:

### 1. Reference implementation reuse - wrong assumption

The most direct reference available for a *write* frame
([`homebridge-clearlight-sauna`](https://github.com/Mustavo/homebridge-clearlight-sauna))
was for a different product (a sauna) on the same firmware family. Its write
format was specific to that product's own small set of attributes (a
hardcoded 13-byte payload with a type-selector byte), not the generic
Gizwits control structure. Applying it to this project's pump produced a
payload the device accepted (no error) but had no effect. **Lesson**: a
reference implementation for the same platform is authoritative for
platform-level framing (magic bytes, commands, session setup) but not
necessarily for a specific write-payload *content* format, which can be
product-specific even on identical firmware.

### 2. Official SDK source - right structure, wrong details

Cloning the actual Gizwits SDK C source (bundled in an unrelated open-source
ESP8266 project on GitHub) gave the real `attrFlags_t`/`attrVals_t` struct
definitions - a flags bitmask plus a values buffer, which is the genuinely
correct general shape. Implementing this literally (flags ascending by
attribute id, values copied forward from current status) produced payloads
that were still silently ignored by the device. This was frustrating because
the *structure* was right and it still didn't work - which turned out to
mean the *byte ordering within that structure* was the missing piece, not
something visible from a header file alone.

### 3. Static binary analysis (Ghidra) - found real bugs, couldn't fully verify

The vendor app's actual native protocol library (`libGizWifiDaemon.so`)
turned out to be **unstripped** - it still had all its original function
names, which is what made this viable at all. Downloading Ghidra and running
headless decompilation on specific functions (found by searching for
`__func__` debug-string references, since some of the relevant functions
were static/unexported) revealed:
- The real write command (`0x93`, confirmed from
  `GizWifiSDKWriteTransBusinessReqWithSN`).
- That the `attrFlags_t` bitmask is **byte-reversed** relative to naive
  ascending order - this alone fixed writes for simple numeric attributes.
- A candidate formula for where bit-type attribute *values* land within the
  values buffer, derived by manually tracing pointer arithmetic through
  decompiled pseudo-C across several functions
  (`transDatasToP0Data`/`parseIndexInfo`).

Four distinct variations of that bit-placement formula were tried against
the live pump. All four failed - no observable effect on the physical pump,
confirmed via full before/after field diffs. This was genuinely confusing
at the time, because the formula had been derived carefully from real
decompiled code, not guessed - but headless pseudo-C reading has a real
failure mode: it's easy to misread a pointer-heavy, un-typed decompilation
and be *confident* in an incorrect derivation. (It later turned out the
formula was actually correct - see below. The bug was in test code, not
the formula. Static analysis alone couldn't distinguish those two
possibilities; only a real captured frame could.)

**Lesson:** unstripped binaries with real function names make Ghidra
dramatically more useful than blind disassembly - worth checking for early.
But headless static analysis has a genuine confidence ceiling: without
either an interactive GUI session (for proper struct/type recovery and
faster cross-reference browsing) or independent ground truth, you can
derive something that *looks* rigorous and still be wrong, with no way to
tell from the analysis alone.

### 4. Dynamic instrumentation (Frida) - blocked by emulator architecture, not a config problem

The plan to get ground truth was to hook the app's own `SSL_write`/
`SSL_read` functions with Frida (targeting the app's own statically-linked
OpenSSL, which sidesteps certificate pinning entirely - you read the
plaintext right at the boundary before/after the app's own encryption,
without ever touching TLS). This required a rooted Android environment.

Real ARM64 hardware wasn't available, so the plan was an Android emulator.
This hit two sequential, genuinely different blockers:
- **Direct ARM64 emulation**: flatly refused to boot on a Windows/Intel
  host - not a settings issue, Google's emulator doesn't support ARM64
  guest images on x86_64 hosts at all.
- **x86_64 emulation with Google's built-in ARM-translation layer**
  (`libndk_translation`, which normally lets ARM-only native libraries run
  transparently on a faster x86_64 system image): booted fine, the app ran
  correctly - but **Frida could not see or hook the translated library at
  all**. Confirmed directly: the target library never appeared in Frida's
  own module enumeration, out of ~280 modules the process had loaded,
  despite the library actively executing (visible via its log output).
  Frida hooks work by patching real machine code in place; code that's
  being translated/JIT'd by an opaque layer isn't something it can reach.

**Lesson:** if you're planning to use Frida against an ARM-only native
library and your only option is x86_64-with-ARM-translation emulation,
verify Frida can actually see the target module *before* investing further
- this is a hard architectural wall, not something more configuration
fixes. It's not documented clearly anywhere obvious; it had to be
discovered by trying.

### 5. Reading the vendor's own debug log - the actual breakthrough

With Frida blocked, the fallback was much lower-tech: just watch
`adb logcat` while using the real app. It turned out the vendor's own SDK
logs a full hex dump of every outgoing control payload at debug level
(tagged `GizSDKLog`, message `"Gizwits p0:"` followed by a formatted hex
table) - in **plaintext**, no TLS interception, no hooking, nothing to
defeat. This had been sitting there the entire time; the only reason it
wasn't found sooner is that nobody had thought to just grep the log for
outgoing writes specifically until the higher-effort techniques ran out.

Two real frames (`SwitchON` true and false) captured this way were enough
to confirm the Ghidra-derived formula from step 3 was **correct all along**
- the earlier live-hardware test failures had been a bug in the test
harness, not the formula. Rebuilding the write encoder around the confirmed
formula and validating it byte-for-byte against the captured frames
(`tests/test_control.py` - an offline regression test, no hardware needed
to run it) closed the loop, and a subsequent live test against the real
pump confirmed a `SwitchON=true` write actually turns the pump on.

**Lesson, and probably the single most useful one from this whole project:**
when reverse-engineering a vendor's app, **check what it already logs before
building interception infrastructure**. Debug/diagnostic logging that
developers leave in production builds is an extremely common, extremely
low-effort source of ground truth, and it's easy to overlook once you're
deep into more sophisticated techniques (TLS interception, dynamic
instrumentation, binary patching) that feel more "real." Try `adb logcat`
early, not as a last resort.

## Getting the full product catalog: free, once you know where to look

A late request was to find every product model the app supports, not just
the one pump this project started with. This turned out to require no new
technique at all - the already-decompiled APK had a `productConfig` assets
folder bundling all 42 product schemas locally (the app ships them so it
doesn't need a network round-trip for every product it might ever connect
to). Diffing a matched WiFi-only/WiFi+BLE product pair showed the schema
content is identical between them - connectivity type isn't a schema field,
it's only readable from the product's name string (`"WiFi_BLE"` suffix, or
a Bluetooth-branded `蓝牙` prefix) and correlates with, but isn't perfectly
predicted by, the schema's `protocolType` field (one bundled product breaks
that correlation) - so both signals ended up documented rather than
collapsed into an oversimplified single rule. **Lesson:** once you've done
the work to decompile something, look at what else is sitting in the
decompiled output before assuming you need a new investigative pass - a lot
of adjacent value can be free.
