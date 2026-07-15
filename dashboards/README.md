# Tank dashboards — install guide

Three files, three separate installs. All of it is optional on top of the
core `jebao_local` integration - skip anything you don't want.

| File | What it is |
|---|---|
| [`jebao-dashboard.yaml`](jebao-dashboard.yaml) | Lovelace dashboard - pumps grouped into tank sections, plus an embedded Control panel view |
| [`jebao-tank-scripts.yaml`](jebao-tank-scripts.yaml) | HA `script:` entries - tank-wide on/off, and feed mode with a timer that survives closing the browser |
| [`../www/jebao/designer.html`](../www/jebao/designer.html) | The Control panel itself - a standalone page for tank groups, cloneable settings profiles, and a feed-now button |

**Prerequisite:** `jebao_local` itself is already installed (see the main
[README](../README.md#quick-start-home-assistant)) and at least one pump has
been added via its config flow.

## 1. Install the Control panel page

Copy the file into your HA config's `www/` folder, preserving the `jebao/`
subfolder so it's served at `/local/jebao/designer.html`:

```
<homeassistant config dir>/
  www/
    jebao/
      designer.html      <- copy it here
```

No restart needed - HA serves `www/` live. Open
`http://<your-ha-host>:8123/local/jebao/designer.html` directly to confirm
it loads before wiring it into a dashboard.

## 2. Add the Lovelace dashboard

Settings → Dashboards → **Add dashboard** → open it → top-right ⋮ → **Edit
dashboard** → ⋮ → **Raw configuration editor** → paste in the contents of
[`jebao-dashboard.yaml`](jebao-dashboard.yaml).

Then edit it for your own pumps: the file ships with one real example
entity set (`qp50gpt5i8h4mfkio0enik`, wired to whichever pump this project
was built against) and one placeholder "Frag tank" section. Duplicate the
grid-section pattern per tank, and swap in your own pumps' `did`s. See the
comment block at the top of the file for the exact entity_id pattern and
how to find a pump's `did`.

## 3. Add the tank scripts (optional, for tank-wide on/off and a reliable feed timer)

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

## 4. Get a Long-Lived Access Token for the Control panel

The Control panel's Apply/Clone-to-tank/Feed-now buttons call HA's REST API
directly from your browser - they need a token to authenticate:

Your HA user profile (click your name, bottom-left) → scroll to
**Long-Lived Access Tokens** → **Create Token** → paste it into the Control
panel's Setup tab. It's stored in that browser's `localStorage` only - never
sent anywhere except your own HA instance.

Leave the **HA base URL** field blank if you're opening the page through
`/local/jebao/designer.html` from inside HA itself (same origin); fill it
in only if you're opening the file some other way.

## Known gap

None of this - the dashboard, the scripts, or the Control panel's live HA
calls - has been exercised against a running Home Assistant instance yet,
because `jebao_local` itself hasn't (see the main README's Status table).
The entity_id pattern is verified correct against the integration's actual
code, and the Control panel's own tank/profile/timer logic was verified in
a real browser (see `SPEC.md` Phase 8), but the HA-connected parts are
untested end-to-end. Treat the install steps above as correct, but watch
for surprises the first time you actually click a button that talks to HA.
