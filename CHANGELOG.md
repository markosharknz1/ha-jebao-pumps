# Changelog

All notable changes to this project are documented here.

## Unreleased

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
