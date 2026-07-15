# Supported Jebao Product Models

The Jebao Aqua app (v3.3.55) bundles 42 product datapoint schemas locally
(`reference/jebao-apk/decompiled/resources/assets/productConfig/`, one JSON
per `product_key`) - this covers every product line the app supports, not
just the wavemaker this project started with. Full machine-readable catalog:
[`product_catalog.json`](product_catalog.json). Individual schemas are
copied to `fixtures/product_schemas/`.

## How connectivity type was determined

The datapoint schema JSON is **identical** between a WiFi-only product and
its WiFi+BLE sibling (same attrs, same everything except the `name` field) -
confirmed by diffing `户外水泵` (Outdoor Water Pump) against `户外水泵WiFi_BLE`.
So connectivity can't be read from schema *content*.

Two signals were considered:

1. **Product name** - Bluetooth-primary products are prefixed `蓝牙`
   ("Bluetooth"); dual-mode products are suffixed `WiFi_BLE`/`_BLE`; everything
   else is WiFi-only. This is the signal actually used in the catalog - it's
   a direct, human-authored label from the same team that built the product
   line, not an inference.
2. **`protocolType`** in the schema - `"standard"` vs `"var_len"` correlates
   strongly with WiFi vs Bluetooth (`var_len`'s compact variable-length
   encoding suits BLE's small MTU far better than WiFi's always-full-length
   payload), but it is **not a reliable rule on its own**: one product,
   `多头WIFI滴定泵` (Multi-Head **WiFi** Dosing Pump), has `protocolType: var_len`
   despite its name explicitly saying WiFi. Don't use `protocolType` alone to
   decide LAN-discoverability - check the catalog's `connectivity` field
   (name-derived) instead, and treat any `⚠️` note in the table below as a
   "verify before trusting" flag.

**Practical detection rule for this library:** a product is LAN-discoverable
(the `discover()` UDP broadcast this project implements will find it) if its
catalog `connectivity` is `"WiFi only"` or `"WiFi + Bluetooth (dual mode)"`.
Bluetooth-only products won't respond to LAN discovery at all and are **out
of scope** for this project's protocol implementation - they'd need a BLE
GATT client and (per point 2 above) usually a different, `var_len` payload
encoder, not covered here.

## Full catalog

30 WiFi-capable (8 of those dual-mode WiFi+BLE), 12 Bluetooth-only, 1 naming exception (flagged with ⚠️ below).

| Product (English) | Original name | product_key | protocolType | Connectivity | # datapoints |
|---|---|---|---|---|---|
| ATI Lights (third-party brand) | ATI_lights | `9cb3ec792e054c748eafe675469894e6` | standard | WiFi only (by name) | 43 |
| Aquarium Light | 水族灯 | `efc08baa6b0a4de38d4bc9bce04ad350` | standard | WiFi only (by name) | 18 |
| Aquarium Light (WiFi+BLE) | 水族灯WiFi_BLE | `1588f1dd744a47a5a550b426d5e9cce2` | standard | WiFi + Bluetooth (dual mode, by name) | 45 |
| Aquarium Pump (WiFi+BLE) | 水族泵WIFI_BLE | `6a5c47b3ea364ecb841b47f5997a1775` | standard | WiFi + Bluetooth (dual mode, by name) | 66 |
| Aquarium Pump (with AP time-sync) | 水族泵_有AP校时 | `02039876751049deb404d1d89221ec4b` | standard | WiFi only (by name) | 66 |
| Aquatic Plant Light | 水草灯 | `a4167c09ed81480c83bec3b334b7ec75` | standard | WiFi only (by name) | 45 |
| Aquatic Plant Light (WiFi+BLE) | 水草灯WiFi_BLE | `cb5ef5c7f1994511bd0d5655c9789429` | standard | WiFi + Bluetooth (dual mode, by name) | 45 |
| Bluetooth Aquarium Light | 蓝牙水族灯 | `386c9120642c4377911b3141e7c92e39` | var_len | Bluetooth (BLE) only (by name) | 45 |
| Bluetooth Aquarium Pump | 蓝牙水族泵 | `ef4649b70d9a4c0aac513df7c4803a2d` | var_len | Bluetooth (BLE) only (by name) | 66 |
| Bluetooth Aquatic Plant Light | 蓝牙水草灯 | `684082625e0d4c16a16adf48f7c032b4` | var_len | Bluetooth (BLE) only (by name) | 45 |
| Bluetooth External Filter | 蓝牙缸外过滤器 | `64236a674d8342fcba0ffc5eb3965083` | var_len | Bluetooth (BLE) only (by name) | 66 |
| Bluetooth Feeder | 蓝牙喂食器 | `1d1720fa7d8346e78eebc7c99be7ae42` | var_len | Bluetooth (BLE) only (by name) | 22 |
| Bluetooth Fish Tank Feeder | 蓝牙鱼缸喂食器 | `d8be6d07e415444f834a91e9041e60db` | var_len | Bluetooth (BLE) only (by name) | 22 |
| Bluetooth Freshwater Light | 蓝牙淡水灯 | `48fc85f8cf83420489e0ae033b03216b` | var_len | Bluetooth (BLE) only (by name) | 45 |
| Bluetooth Multi-Head Dosing Pump | 蓝牙多头滴定泵 | `a97b4d69bc7b41648a10cec4e285d919` | var_len | Bluetooth (BLE) only (by name) | 42 |
| Bluetooth Outdoor Water Pump | 蓝牙户外水泵 | `e7b4649fdf8d413ba0a60d57fdde7101` | var_len | Bluetooth (BLE) only (by name) | 66 |
| Bluetooth Simple Aquarium Light | 蓝牙简易水族灯 | `035d465e0531422195bab05bd478cd03` | var_len | Bluetooth (BLE) only (by name) | 66 |
| Bluetooth Water Pump Speed Controller | 蓝牙水泵调速器 | `b9b1a9dfd90c49b08b88be84e7df9e6b` | var_len | Bluetooth (BLE) only (by name) | 66 |
| Bluetooth Wavemaker | 蓝牙造浪泵 | `0bc0064708414a2593b2390ca9dbfff1` | var_len | Bluetooth (BLE) only (by name) | 71 |
| Dosing Pump (no AP time-sync) | 滴定泵_无AP校时 | `5b3c136fd4b74f3fb2a366a254c76c9a` | standard | WiFi only (by name) | 23 |
| Dosing Pump (with AP time-sync) | 滴定泵_有AP校时 | `25c5b146791f465bbefdbfd312b9e8ea` | standard | WiFi only (by name) | 25 |
| External (Hang-on/Canister) Filter | 缸外过滤器 | `352beee71a4641cf823c314a46835a2c` | standard | WiFi only (by name) | 66 |
| External Filter (WiFi+BLE) | 缸外过滤器WiFi_BLE | `90b603b450c14b55b41e90724020203c` | standard | WiFi + Bluetooth (dual mode, by name) | 66 |
| Feeder | 喂食器 | `0cdca0490a1747f1b90aff0e1ae5293a` | standard | WiFi only (by name) | 22 |
| Freshwater Light | 淡水灯 | `8ec86278bf1f42d0a7a91c96c4aaaeed` | standard | WiFi only (by name) | 45 |
| Freshwater Light (WiFi+BLE) | 淡水灯WiFi_BLE | `da46a2b19a2c44b2bdac61a871a25382` | standard | WiFi + Bluetooth (dual mode, by name) | 45 |
| Local Timer LED Light (no AP time-sync) | 本地定时LED灯_无AP校时 | `cf4aaef856b84f6ea9cea29030eff19b` | standard | WiFi only (by name) | 43 |
| Local Timer LED Light (with AP time-sync) | 本地定时LED灯_有AP校时 | `83139ec4bc7a406495a8a52aa6d3e75d` | standard | WiFi only (by name) | 45 |
| Local Wavemaker (WiFi+BLE) [THIS PROJECT'S PUMP] | 本地造浪泵_WIFI_BLE | `54114ccdac1e41c0bb17e222887c07ba` | standard | WiFi + Bluetooth (dual mode, by name) | 71 |
| Local Wavemaker (no AP time-sync) | 本地造浪泵_无AP校时 | `f0d844ab0d4947ac9527a286160bc705` | standard | WiFi only (by name) | 69 |
| Local Wavemaker (with AP time-sync) | 本地造浪泵_有AP校时 | `1d8c63eaccac4205b92c84d77d5a08fb` | standard | WiFi only (by name) | 71 |
| Multi-Head WiFi Dosing Pump | 多头WIFI滴定泵 | `3b181431429e46029f850348869cc66f` | var_len | WiFi only (by name) ⚠️ NAME says WiFi-capable but protocolType=var_len (usually a BLE signal) - exception to the general pattern, verify before assuming LAN support | 42 |
| Outdoor Water Pump | 户外水泵 | `02a69eb3b44b4a60a2187cbdce76a04e` | standard | WiFi only (by name) | 66 |
| Outdoor Water Pump (WiFi+BLE) | 户外水泵WiFi_BLE | `3e75dd53e5f44c3784d2fc655008fdf5` | standard | WiFi + Bluetooth (dual mode, by name) | 66 |
| Single-Head Dosing Pump (no AP time-sync) | 单头滴定泵_无AP校时 | `778ea24746c14a8886eee24ec7922412` | standard | WiFi only (by name) | 12 |
| Single-Head Dosing Pump (with AP time-sync) | 单头滴定泵_有AP校时 | `031f8753d7ad47a4bf46d89b17f40282` | standard | WiFi only (by name) | 14 |
| Single-Head Wireless Timer Switch | 单头无线定时开关 | `79325028f5754ffc811fcb2d4506c654` | standard | WiFi only (by name) | 23 |
| Water Pump | 水泵 | `bd0febe99e724e3b8640ed955cd81972` | standard | WiFi only (by name) | 14 |
| Water Pump Speed Controller | 水泵调速器 | `954b3e52aa5141539dfcaa2fff6c9e7f` | standard | WiFi only (by name) | 66 |
| Water Pump Speed Controller (WiFi+BLE) | 水泵调速器WiFi_BLE | `35abf13fa5444553b4a7cd0d184f3430` | standard | WiFi + Bluetooth (dual mode, by name) | 66 |
| Wavemaker (base/legacy) | 造浪泵 | `f65982cb65da43baa0c722c84dd2740b` | standard | WiFi only (by name) | 19 |
| Wireless Timer Switch | 无线定时开关 | `db6a58856402414283ec174642629eea` | standard | WiFi only (by name) | 71 |
