# Tank dashboards — install guide

Everything below is optional on top of the core `jebao_local` integration -
skip anything you don't want. Nothing here needs a separate file copy
anymore: the native card and the Control panel are both bundled inside
`custom_components/jebao_local/` and register themselves automatically the
moment the integration loads, whether you installed it via HACS or by
copying the folder by hand. (Earlier versions of this project shipped the
Control panel in a separate `www/` folder that HACS could never actually
deliver - fixed now, see `CHANGELOG.md`.)

| What | What it is |
|---|---|
| `custom:jebao-pump-card` | A native Lovelace card, added from the card picker with **zero YAML** - it discovers your pumps itself and only shows the controls each one actually has |
| [`jebao-dashboard.yaml`](jebao-dashboard.yaml) | An example dashboard using that card, with pumps grouped into tank sections, plus an embedded Control panel view |
| [`jebao-tank-scripts.yaml`](jebao-tank-scripts.yaml) | HA `script:` entries - tank-wide on/off, and feed mode with a timer that survives closing the browser |
| Control panel (`/jebao_local/designer.html`) | A separate, complementary tool for managing *several* pumps at once: named tank groups, settings profiles you save once and clone across a tank |

**Prerequisite:** `jebao_local` itself is already installed (see the main
[README](../README.md#quick-start-home-assistant)) and at least one pump has
been added via its config flow.

## The fast path: just add the card

Open any dashboard → **Edit** → **Add Card** → search **"Jebao Pump"** →
add it. That's it - no config, no entity picking. It'll show every pump HA
knows about, with only the controls each one actually supports (a dosing
pump with no Feed mode just won't show that section). Pumps with a single
clear speed attribute (like this project's wavemaker - Flow) get a combined
power+speed control backed by a native `fan` entity instead of a separate
on/off switch and a slider; pumps with no speed attribute just get plain
on/off. The card figures out which is which itself - this only matters if
you're writing your own automations against the raw entity_ids instead of
using the card (see `jebao-tank-scripts.yaml` for both patterns).

To scope a card to specific pumps (e.g. one card per tank), edit the card
in the visual editor's YAML mode and add a `dids:` list:

```yaml
type: custom:jebao-pump-card
dids: ["qp50gpt5i8h4mfkio0enik"]
```

`<did>` is the pump's device id (lowercased) - find it in any of its
entity_ids (Developer Tools → States, filter `jebao_` is fastest), or in
the config-flow discovery list from when you first added it.

## The full example dashboard (tank sections + Control panel)

If you want the tank-grouped layout with quick-action buttons and the
embedded Control panel view ready-made: Settings → Dashboards → **Add
dashboard** → open it → top-right ⋮ → **Edit dashboard** → ⋮ → **Raw
configuration editor** → paste in the contents of
[`jebao-dashboard.yaml`](jebao-dashboard.yaml), then edit the example
`did`s for your own pumps.

## Tank scripts (optional, for tank-wide on/off and a reliable feed timer)

Open [`jebao-tank-scripts.yaml`](jebao-tank-scripts.yaml), edit the example
`did` to match your own pump(s) (duplicate the pattern for more tanks - see
the commented-out examples at the bottom of the file), then merge its
contents under a `script:` key in `configuration.yaml`:

```yaml
script: !include dashboards/jebao-tank-scripts.yaml
```

(or paste the entries directly under an existing `script:` block, or use a
packages file - whatever your `configuration.yaml` already does for other
scripts). Reload scripts (Developer Tools → YAML → Scripts, or restart HA),
then the `script.<name>` entities referenced by the dashboard's "Display
tank actions" card will exist.

## The Control panel (optional, for managing several pumps at once)

The native card is per-tank/per-pump; the Control panel is a separate tool
for batch operations across many pumps - named tank groups, and settings
profiles (wave mode/flow/frequency) you save once and clone onto every pump
in a tank with one click. It's a standalone page, not a native card, so
unlike the card above it needs a Long-Lived Access Token to call HA's REST
API from your browser:

Your HA user profile (click your name, bottom-left) → scroll to
**Long-Lived Access Tokens** → **Create Token** → paste it into the Control
panel's Setup tab. It's stored in that browser's `localStorage` only - never
sent anywhere except your own HA instance. Leave the **HA base URL** field
blank if you're opening the panel from inside HA itself (same origin,
`/jebao_local/designer.html` - reachable from the dashboard's Control panel
view, or your sidebar if this HA version supports the iframe panel type);
fill it in only if you're opening the page some other way.

## Known gap

None of this - the native card, the dashboard, the scripts, or the Control
panel's live HA calls - has been exercised against a running Home Assistant
instance yet, because `jebao_local` itself hasn't (see the main README's
Status table). The card's rendering/feature-detection logic and the Control
panel's tank/profile/timer logic were both verified against a mocked `hass`
object in a real browser (see `SPEC.md`), but the actual HA-connected parts
- the card's `hass.callService` calls, the panel's REST calls, and the
Lovelace-resource/sidebar-panel auto-registration in `panel.py` - are
untested end-to-end. Treat the install steps above as correct, but watch
for surprises the first time you actually click a button that talks to a
real HA instance.
