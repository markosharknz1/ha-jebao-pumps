# Jebao Local (Home Assistant integration)

Local-only Home Assistant integration for Jebao WiFi devices, built on this
project's own reverse-engineered LAN protocol (see the repo root
[README.md](../../README.md) and [PROTOCOL.md](../../PROTOCOL.md)). No
cloud account, no internet dependency at runtime.

## Architecture

Design borrows from two existing community Jebao integrations (see
[SPEC.md](../../SPEC.md) Phase 7 for the detailed comparison) while
replacing their protocol layer entirely with this project's own
implementation:

- **Discovery-first config flow** (`config_flow.py`) - scan the LAN or enter
  an IP manually, pattern borrowed from `jrigling/homeassistant-jebao`.
- **Single shared coordinator per config entry** (`coordinator.py`),
  created once in `__init__.py` before platforms are set up, and shared via
  `hass.data` - `jrigling`'s repo has a real bug where each platform
  independently creates its own coordinator (up to 5 redundant pollers per
  device); this integration follows `chrisc123`'s correct pattern instead.
- **Generic, schema-driven entity factory** across `switch.py`/`select.py`/
  `number.py`/`binary_sensor.py` - which entities get created is entirely
  driven by the device's own datapoint schema (`bool` writable → switch,
  `enum` writable → select, `uint8` writable → number, `fault` → binary
  sensor), not hardcoded per product. Pattern borrowed from `chrisc123`'s
  integration, which already demonstrated this scales across multiple
  Jebao product lines.
- **Self-healing IP recovery**: not yet implemented in the coordinator's
  reconnect path beyond a basic rediscovery attempt - `jrigling`'s DHCP-based
  IP-drift recovery is a good pattern to add later (see Known Gaps below).

`jebao_gizwits/` here is a vendored copy of the top-level library (HA custom
components need to be self-contained for HACS installation - there's no
published PyPI package to depend on). `jebao_gizwits/schemas/` bundles all
29 WiFi-capable product schemas this project extracted from the vendor app
- see [docs/SUPPORTED_MODELS.md](../../docs/SUPPORTED_MODELS.md).

## Known gaps

- **Not tested against a live Home Assistant instance.** Validated for
  correct imports and API usage against the real `homeassistant` package,
  and the underlying protocol logic is proven on real hardware, but the
  integration's own runtime behavior (config flow UX, entity lifecycle,
  coordinator error handling in practice) hasn't been exercised inside a
  running HA yet.
- **Schedule programming isn't exposed.** The 48 daily timer slots
  (`AutoTime00`-`AutoTime47`, each an 8-byte packed structure) and date/time
  sync attributes are decodable but have no entity type yet - they're
  `binary` data type, not one of the four types this integration currently
  maps to entities.
- **Bit-type writes beyond `SwitchON` are unverified per-attribute.** The
  write formula is confirmed correct and should generalize to every
  bit-type attribute on every bundled product (same schema-driven formula,
  just different `byte_offset`/`bit_offset`/`len` per attribute) - but only
  `SwitchON` has actually been confirmed against real captured frames and
  live hardware. Treat other bit-type writes (`Mode`, `FeedSwitch`, etc.) as
  likely-correct-but-unverified until tested.
- **No DHCP-based IP-drift recovery.** The coordinator retries a plain
  rediscovery broadcast on connection failure, but doesn't hook into HA's
  DHCP discovery step the way `jrigling`'s integration does, which would
  catch IP changes proactively rather than only on the next failed poll.
- **Read reliability for manually-toggled power state.** See
  [SPEC.md](../../SPEC.md) Phase 4's write-caveat section - `SwitchON`/
  `AutoMode` reads don't reliably reflect a pump turned on via a raw
  `SwitchON` write outside the schedule system. This affects this
  integration's switch entity's reported state, not just the underlying
  library.
