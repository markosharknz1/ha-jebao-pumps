# Changelog

All notable changes to this project are documented here.

## Unreleased

- **Added a logo and a real integration icon.** An original pump-icon badge
  (not a copy of Jebao's actual logo/trademark artwork - just referencing
  the brand name in text, with UNOFFICIAL in red) now appears at the top
  of the README and, via `custom_components/jebao_local/brand/{icon,logo}
  .png` (HA's 2026.3+ brands-proxy convention - no manifest changes
  needed), as this integration's actual icon in HA and HACS instead of
  "icon not available". Source SVGs and a ready-to-upload GitHub
  social-preview image are in `docs/logo/` - GitHub has no API for social
  preview images, so that one still needs a manual upload (Settings →
  General → Social preview).
- **The Jebao Pump card now has a visual editor** - open a card's settings
  (pencil icon) and pick a specific pump from a dropdown instead of typing
  `dids: [...]` in YAML, for the common "one card per pump" dashboard
  layout. Also fixed a real bug this surfaced: the card only ever looked
  for a power attribute named `switchon`, but several products (mostly
  lights) name theirs plain `switch` - those pumps had a real, working
  power switch entity that the card simply never showed. Found by
  rendering the card against all 29 bundled product schemas at once
  (generated from the actual entity-dispatch rules in switch.py/number.py/
  select.py/binary_sensor.py/fan.py, not guessed) rather than just the 2
  hand-picked shapes used in earlier testing - all 29 now render correctly.
- **Fixed two real bugs found on the first live Home Assistant install**
  (thank you for the log output): (1) schema loading did blocking file I/O
  (`Path.read_text`) directly on the event loop, in both the coordinator's
  `__init__` and the config flow's `_finish` - HA's event-loop guard
  correctly flagged this. Fixed by deferring the coordinator's schema load
  to a new `async_load_schema()` (called via `hass.async_add_executor_job`
  before the first refresh) and wrapping the config flow's equivalent call
  the same way. (2) `binary_sensor.py` set `_attr_entity_category =
  "diagnostic"` (a raw string) instead of `EntityCategory.DIAGNOSTIC` -
  newer HA core validates this strictly and rejects the string, which was
  crashing every fault binary_sensor's entity registration.
- **Pumps with a speed control now get a native `fan` entity** instead of a
  separate switch + number - matches `jrigling/homeassistant-jebao`'s own
  precedent for a different Jebao model, gets a proper speed-slider UI for
  free, and (unlike `number`) `fan` entities are exposed to Google
  Assistant's smart-home API with real speed control. Applies to 13 of the
  29 bundled products - 9 with a single `Motor_Speed` attribute (clean 1:1
  fit) and the 4 wavemaker variants, where `Flow` becomes the fan's speed
  and `Frequency` (a pulse rate, not a speed) stays a separate `number`.
  `fan.py`'s `fan_attr_names()` decides per-product; `switch.py`/`number.py`
  exclude whatever a fan claims so there's no duplicate entity. The native
  card and the Control panel (`fan.jebao_<did>_fan` vs.
  `switch.jebao_<did>_switchon` + `number.jebao_<did>_flow`) both handle
  either shape automatically.
- **Native Lovelace card** (`custom:jebao-pump-card`): add it from the card
  picker with zero YAML. It discovers this integration's pumps itself (via
  HA's entity/device registries, with a fallback for older frontends) and
  only renders the controls each pump's own entities actually support -
  power, wave mode, flow, frequency, and feed mode with a live countdown
  timer. No token needed - it calls HA's own switch/select/number services
  as the logged-in user. Bundled and auto-registered by `panel.py`
  (`async_register_card`), which also injects it as a Lovelace resource on
  startup so there's no manual "Settings > Dashboards > Resources" step.
- **Fixed a real HACS-delivery bug**: the Control panel (the tank-grouping/
  settings-profile tool) originally shipped in `www/jebao/designer.html`,
  outside `custom_components/` - but HACS only ever copies
  `custom_components/`, so a HACS install would never have actually
  delivered that file. Moved it to `custom_components/jebao_local/panel/
  designer.html`, served (and given a sidebar entry where supported) by the
  same `panel.py`, so both HACS and manual installs deliver it correctly.
- **Tank dashboards**: `dashboards/jebao-dashboard.yaml` (Lovelace, pumps
  grouped into tank sections, now using the native card) and `dashboards/
  jebao-tank-scripts.yaml` (tank-wide on/off, feed mode with an auto-off
  timer).
- **Entity IDs are now stable and ASCII** (`switch.jebao_<did>_switchon`,
  etc.) instead of being derived from the vendor's often non-ASCII product
  name, which HA's slugify could mangle unpredictably. Also added English
  display names for the common pump attributes via HA's entity
  translations.
- **Device/config-entry name is now English**, not the vendor schema's raw
  Chinese product name - e.g. "Local Wavemaker (WiFi+BLE)" instead of
  "本地造浪泵_WIFI_BLE" in the config flow's "Name and assign" dialog and
  the device page. Every bundled schema JSON now carries a `name_en` field
  (cleaned up from `docs/product_catalog.json`'s catalog), with a fallback
  to the Chinese name for any schema that predates this field.
- **MAC address now shows on the device page** (Settings → Devices &
  Services → Devices → the pump → Connections), for cross-referencing
  against a router's client list when you have several identical pumps.
  `did` stays the identifier HA uses internally (unique_id, entity_ids) -
  the MAC is purely a UI convenience, previously captured during discovery
  but discarded.

## 0.1.0

Initial release.

- **Local LAN protocol implementation** (`jebao_gizwits/`) of the Gizwits
  "GAgent" WiFi protocol Jebao pumps actually speak: UDP discovery, TCP
  status reads, and writes — including the bit-packed boolean/enum write
  encoding, reverse-engineered from the vendor app's own debug logging.
  See [METHODOLOGY.md](METHODOLOGY.md) and [SPEC.md](SPEC.md) for how.
- **Home Assistant integration** (`custom_components/jebao_local/`),
  schema-driven so it isn't hardcoded to one product: config flow with LAN
  discovery or manual IP entry, `DataUpdateCoordinator`-based polling, and
  switch/select/number/binary_sensor entities generated from each device's
  datapoint schema.
- **29 bundled WiFi-capable product schemas** (wavemakers, dosing pumps,
  lights, filters, other pumps) extracted from the vendor app — see
  [docs/SUPPORTED_MODELS.md](docs/SUPPORTED_MODELS.md).
- Verified live against real hardware: discovery, status reads, numeric
  writes, and the `SwitchON` boolean write. Verified to coexist cleanly
  alongside another real HA integration (no domain/dependency conflicts).

### Known gaps

- The integration itself hasn't been exercised inside a running Home
  Assistant instance (config flow → entity creation → live update cycle) —
  only import/static checks have run so far.
- Bit-type writes other than `SwitchON` use the same confirmed encoding
  formula but haven't been individually tested against live hardware.
- Schedule programming (48 daily timer slots) is decodable but not yet
  exposed as an HA entity.
