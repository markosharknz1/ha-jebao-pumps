# Changelog

All notable changes to this project are documented here.

## Unreleased

- **Translated the remaining Chinese labels shown in the UI.** Checked the
  vendor app's own bundled i18n data first (it embeds 53 per-product
  `language:{en:{...},zh:{...}}` objects) - real finding: even in "English"
  mode, the app itself never translates a single datapoint label or enum
  value, only unrelated things like company info and fault messages. No
  vendor English source existed to adopt, so this project translated the
  actual (small, closed) set itself: wave modes, a linkage (master/slave)
  selector, calibration steps, and a day/night light-cycle selector - about
  20 distinct terms across all 29 bundled schemas. Wave-mode selects
  (`Mode`/`AutoMode`) and the `Linkage`/`CALSet` selects now show English
  option labels via HA's own entity `state` translations (the underlying
  value the protocol writes is untouched); the synthesized `State` sensor's
  "Running (X)" text is translated too, in Python, since HA's translation
  layer doesn't reach into a sensor's own formatted string.
- **Schedule programming** - the 48 daily timer slots (`AutoTime00`..
  `AutoTime47`) are now readable and writable, closing the last item on the
  status table. The 8-byte-per-slot format
  (`[startHour, startMinute, endHour, endMinute, mode, flow, frequency,
  pulseTide]`) was never actually confirmed before now - only an unverified
  Phase 1 guess existed. Confirmed this time from three independent static
  sources agreeing byte-for-byte: the vendor app's own `encode`/`decode` JS
  functions, the schema JSON's own byte_offset spacing, and the schema's own
  per-attribute description text (see SPEC.md Phase 15 for the full trail).
  New `jebao_local.set_schedule_slot` / `jebao_local.clear_schedule_slot`
  services (targeting a device, not an entity - 48 slots x up to 8 fields
  each is too many entities for one pump) and a new `Schedule` sensor
  exposing the currently-programmed slots as an attribute for dashboards and
  automations. `control.py`'s write-payload builder gained support for
  `binary` (multi-byte) attributes, reusing the already-confirmed
  byte-type placement rule rather than inventing a new one. Not yet
  re-confirmed against a real captured write frame the way `SwitchON`/
  `Flow`/`Frequency` were - see the "Known gaps" note below.
- **Three features mined from `jrigling/homeassistant-jebao`** (a reference
  integration for a different Jebao model, already studied for the fan
  entity pattern):
  - **DHCP-based IP recovery** - `manifest.json` now declares
    `"dhcp": [{"registered_devices": true}]`, and `config_flow.py` handles
    `async_step_dhcp`: as soon as HA's own network watcher sees a
    *registered* pump's MAC show up with a new IP anywhere on the network,
    the config entry updates and reloads immediately - no waiting for a
    read to fail first, unlike the existing rediscovery-on-failure path in
    `coordinator.py`, which stays as a fallback. Closes a gap this project
    had flagged since early on; we already had the MAC stored for the
    device-page Connections display, so this reused that.
  - **Speed and State sensors** (`sensor.py`) - Speed mirrors the fan
    entity's percentage as a proper `SensorEntity` with
    `state_class: measurement` (fan attributes alone don't get HA's
    long-term statistics/history graphing; a real sensor does), only for
    products that got a fan entity. State synthesizes several raw
    datapoints into one glanceable value - `Off`, `Feeding`,
    `Fault: Overcurrent`, `Running (经典造浪)` - for dashboards and
    automations that would otherwise need to check several entities at once.
  - **Start Feed / Cancel Feed buttons** (`button.py`) - one-shot
    `ButtonEntity`s alongside the existing `FeedSwitch`/`FeedTime`
    entities, the more semantically correct HA entity type for a momentary
    trigger (useful for automations either way, regardless of whether a
    given pump's firmware auto-clears feed mode on its own).

  All three are schema-driven like everything else in this integration -
  gated on whichever real attributes a given product actually has, verified
  against all 29 bundled schemas, not just the wavemaker.
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
