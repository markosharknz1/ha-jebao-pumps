# Changelog

All notable changes to this project are documented here.

## Unreleased

- **Tank dashboards**: `dashboards/jebao-dashboard.yaml` (Lovelace, pumps
  grouped into tank sections) and `dashboards/jebao-tank-scripts.yaml`
  (tank-wide on/off, feed mode with an auto-off timer). `www/jebao/
  designer.html` is a control-panel web app for saving named tank groups
  and settings profiles (wave mode/flow/frequency) and cloning a profile
  across a tank with one click.
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
