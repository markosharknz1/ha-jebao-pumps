/*
 * Jebao Pump card
 * ---------------
 * A native Lovelace card for the jebao_local integration. It discovers this
 * integration's pumps directly from Home Assistant's own entity/device
 * registries (or, on older frontends, by pattern-matching entity_ids), then
 * shows only the controls whose backing entities actually exist for that
 * particular pump - different Jebao product lines expose different
 * attribute sets (not every pump has Feed mode, or a Mode select), so the
 * card asks the pump's own entities what's available rather than assuming.
 *
 * Config:
 *   type: custom:jebao-pump-card
 *   dids: ["qp50gpt5i8h4mfkio0enik"]   # optional; omit = every pump HA knows about
 *   name: Display tank                 # optional heading override (single-pump only)
 *
 * No token, no YAML entity lists required for the common case - just add the
 * card with no config and it discovers everything itself. Writes go through
 * HA's own switch/select/number services, authenticated as the logged-in
 * dashboard user - there is nothing to configure.
 */

const ENTITY_RE = /^(switch|select|number|binary_sensor)\.jebao_([a-z0-9]+)_(.+)$/;

// Cosmetic English labels for the wave-mode enum - the device's Mode select
// only speaks these Chinese strings (see custom_components/jebao_local/
// select.py; the schema's raw enum_values are passed straight through as
// the entity's valid options), so this is purely a display translation.
// Falls back to showing the raw option text for anything not listed here,
// which keeps other product lines' enums (this table is wavemaker-specific)
// from ending up blank.
const MODE_LABELS = {
  "经典造浪": "Classic wave",
  "正弦造浪": "Sine wave",
  "随机造浪": "Random wave",
  "恒流造浪": "Constant flow",
};
const modeLabel = (v) => MODE_LABELS[v] || v;

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

class JebaoPumpCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._feedTimers = new Map(); // did -> {deadline, intervalId}
    this._built = false;
  }

  setConfig(config) {
    this._config = config || {};
  }

  getCardSize() {
    return Math.max(3, this._pumps().length * 4);
  }

  static getStubConfig() {
    return {};
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  // -- discovering pumps ---------------------------------------------------

  _pumps() {
    if (!this._hass) return [];
    const want = this._config.dids
      ? new Set(this._config.dids.map((d) => String(d).toLowerCase()))
      : null;

    const pumps = new Map(); // did -> {did, name, entities: {suffix: entity_id}}
    const registryEntities = this._hass.entities;
    const registryDevices = this._hass.devices;

    const addFromEntityId = (entityId, deviceName) => {
      const m = entityId.match(ENTITY_RE);
      if (!m) return;
      const [, , did, suffix] = m;
      if (want && !want.has(did)) return;
      if (!pumps.has(did)) pumps.set(did, { did, name: deviceName || did, entities: {} });
      pumps.get(did).entities[suffix] = entityId;
    };

    if (registryEntities && registryDevices) {
      // Preferred: the frontend's own entity/device registry cache (HA
      // 2024.8+), so grouping is correct even if entity_id naming ever
      // changes - no regex-on-device-name guessing.
      for (const [entityId, ent] of Object.entries(registryEntities)) {
        if (ent.platform !== "jebao_local") continue;
        const device = ent.device_id ? registryDevices[ent.device_id] : null;
        const name = device ? device.name_by_user || device.name : null;
        addFromEntityId(entityId, name);
      }
    } else {
      // Fallback for older frontends without hass.entities/hass.devices:
      // pattern-match straight out of hass.states. Device name isn't known
      // here, so the did itself is shown as a heading instead.
      for (const entityId of Object.keys(this._hass.states)) {
        addFromEntityId(entityId, null);
      }
    }

    return [...pumps.values()].sort((a, b) => a.name.localeCompare(b.name));
  }

  // -- reading HA state ------------------------------------------------------

  _state(entityId) {
    return entityId ? this._hass.states[entityId] : undefined;
  }

  _faults(pump) {
    return Object.entries(pump.entities)
      .filter(([suffix, id]) => suffix.startsWith("fault") && this._state(id)?.state === "on")
      .map(([suffix]) => suffix.replace(/^fault[_-]?/, "").replace(/_/g, " "));
  }

  // -- calling services --------------------------------------------------

  _call(domain, service, data) {
    if (!this._hass) return Promise.resolve();
    return this._hass.callService(domain, service, data);
  }

  _togglePower(pump) {
    const id = pump.entities.switchon;
    const on = this._state(id)?.state === "on";
    this._call("switch", on ? "turn_off" : "turn_on", { entity_id: id });
  }

  _setMode(pump, option) {
    this._call("select", "select_option", { entity_id: pump.entities.mode, option });
  }

  _setNumber(entityId, value) {
    this._call("number", "set_value", { entity_id: entityId, value });
  }

  _toggleFeedSwitch(pump, on) {
    this._call("switch", on ? "turn_on" : "turn_off", { entity_id: pump.entities.feedswitch });
  }

  _feedNow(pump) {
    const minutesState = this._state(pump.entities.feedtime);
    const minutes = clamp(Number(minutesState?.state) || 5, 1, 60);
    this._setNumber(pump.entities.feedtime, minutes);
    this._toggleFeedSwitch(pump, true);
    this._startFeedCountdown(pump, minutes * 60);
  }

  _stopFeed(pump) {
    this._clearFeedCountdown(pump.did);
    this._toggleFeedSwitch(pump, false);
    this._render();
  }

  _startFeedCountdown(pump, seconds) {
    this._clearFeedCountdown(pump.did);
    const deadline = Date.now() + seconds * 1000;
    const intervalId = setInterval(() => {
      if (Date.now() >= deadline) {
        this._clearFeedCountdown(pump.did);
        this._toggleFeedSwitch(pump, false);
      }
      this._render();
    }, 1000);
    this._feedTimers.set(pump.did, { deadline, intervalId });
    this._render();
  }

  _clearFeedCountdown(did) {
    const t = this._feedTimers.get(did);
    if (t) clearInterval(t.intervalId);
    this._feedTimers.delete(did);
  }

  _feedCountdownText(did) {
    const t = this._feedTimers.get(did);
    if (!t) return null;
    const left = Math.max(0, Math.round((t.deadline - Date.now()) / 1000));
    const mm = String(Math.floor(left / 60)).padStart(2, "0");
    const ss = String(left % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  }

  // -- rendering -----------------------------------------------------------

  _renderPump(pump) {
    const e = pump.entities;
    const power = e.switchon ? this._state(e.switchon) : null;
    const mode = e.mode ? this._state(e.mode) : null;
    const flow = e.flow ? this._state(e.flow) : null;
    const freq = e.frequency ? this._state(e.frequency) : null;
    const feedOn = e.feedswitch ? this._state(e.feedswitch)?.state === "on" : false;
    const faults = this._faults(pump);
    const countdown = this._feedCountdownText(pump.did);
    const title = this._config.name && this._pumps().length === 1 ? this._config.name : pump.name;

    return `
      <div class="pump" data-did="${pump.did}">
        <div class="pump-head">
          <span class="pump-name">${title}</span>
          ${
            e.switchon
              ? `<button class="pw ${power?.state === "on" ? "on" : ""}" data-act="power">
                   ${power?.state === "on" ? "On" : "Off"}
                 </button>`
              : ""
          }
        </div>

        ${
          faults.length
            ? `<div class="faults">⚠ ${faults.map((f) => f).join(", ")}</div>`
            : ""
        }

        ${
          mode
            ? `<div class="row">
                 <label>Wave mode</label>
                 <select data-act="mode">
                   ${(mode.attributes.options || [])
                     .map(
                       (o) =>
                         `<option value="${o}" ${o === mode.state ? "selected" : ""}>${modeLabel(o)}</option>`
                     )
                     .join("")}
                 </select>
               </div>`
            : ""
        }

        ${flow ? this._sliderRow("Flow", "flow", flow) : ""}
        ${freq ? this._sliderRow("Frequency", "frequency", freq) : ""}

        ${
          e.feedswitch && e.feedtime
            ? `<div class="feed">
                 <div class="row">
                   <label>Feed mode</label>
                   <button class="pw small ${feedOn ? "on" : ""}" data-act="feedtoggle">
                     ${feedOn ? "On" : "Off"}
                   </button>
                 </div>
                 ${this._sliderRow("Feed duration (min)", "feedtime", this._state(e.feedtime))}
                 ${
                   countdown
                     ? `<div class="row">
                          <span class="countdown">${countdown}</span>
                          <button data-act="stopfeed">Stop</button>
                        </div>`
                     : `<button class="feednow" data-act="feednow">Feed now</button>`
                 }
               </div>`
            : ""
        }

        ${!e.switchon && !mode && !flow && !freq && !e.feedswitch ? `<div class="empty">No controllable attributes found for this pump.</div>` : ""}
      </div>`;
  }

  _sliderRow(label, suffix, state) {
    if (!state) return "";
    const min = state.attributes.min ?? 0;
    const max = state.attributes.max ?? 100;
    const step = state.attributes.step ?? 1;
    const value = Number(state.state) || 0;
    return `
      <div class="row">
        <label>${label}</label>
        <input type="range" min="${min}" max="${max}" step="${step}" value="${value}" data-act="slider" data-suffix="${suffix}">
        <span class="val">${value}${suffix === "feedtime" ? " min" : "%"}</span>
      </div>`;
  }

  _render() {
    const pumps = this._pumps();

    if (!this._built) {
      this.shadowRoot.innerHTML = `${this._style()}<ha-card class="card"><div id="body"></div></ha-card>`;
      this._built = true;
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
      this.shadowRoot.addEventListener("change", (e) => this._onChange(e));
      this.shadowRoot.addEventListener("input", (e) => this._onInput(e));
    }

    const body = this.shadowRoot.getElementById("body");
    if (!pumps.length) {
      body.innerHTML = `<div class="empty">No jebao_local pumps found. Add one via Settings &rarr; Devices &amp; Services first.</div>`;
      return;
    }
    body.innerHTML = pumps.map((p) => this._renderPump(p)).join("");
  }

  _pumpFor(el) {
    const did = el.closest(".pump")?.dataset.did;
    return this._pumps().find((p) => p.did === did);
  }

  _onClick(e) {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const pump = this._pumpFor(el);
    if (!pump) return;
    const act = el.dataset.act;
    if (act === "power") this._togglePower(pump);
    else if (act === "feedtoggle") this._toggleFeedSwitch(pump, !(this._state(pump.entities.feedswitch)?.state === "on"));
    else if (act === "feednow") this._feedNow(pump);
    else if (act === "stopfeed") this._stopFeed(pump);
  }

  _onChange(e) {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const pump = this._pumpFor(el);
    if (!pump) return;
    if (el.dataset.act === "mode") this._setMode(pump, el.value);
    else if (el.dataset.act === "slider") this._setNumber(pump.entities[el.dataset.suffix], Number(el.value));
  }

  _onInput(e) {
    // Live-update the displayed slider value while dragging, without
    // spamming a service call per pixel - the actual write happens on
    // "change" (pointer release), handled above.
    const el = e.target.closest('[data-act="slider"]');
    if (!el) return;
    const valEl = el.parentElement.querySelector(".val");
    if (valEl) valEl.textContent = `${el.value}${el.dataset.suffix === "feedtime" ? " min" : "%"}`;
  }

  _style() {
    return `<style>
      :host { --primary: var(--primary-color, #03a9f4); }
      .card { padding: 8px 16px 14px; }
      .pump + .pump { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--divider-color, #eee); }
      .pump-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
      .pump-name { font-size: 1.05rem; font-weight: 500; color: var(--primary-text-color); }
      .faults { color: var(--error-color, #db4437); font-size: 0.85rem; margin: 2px 0 8px; }
      .row { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
      .row label { font-size: 0.85rem; color: var(--secondary-text-color); min-width: 110px; }
      .row .val { font-variant-numeric: tabular-nums; font-size: 0.85rem; min-width: 46px; text-align: right; color: var(--secondary-text-color); }
      input[type=range] { flex: 1; accent-color: var(--primary); }
      select { flex: 1; background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 1px solid var(--divider-color, #ccc); border-radius: 6px; padding: 4px 6px; }
      button { font: inherit; cursor: pointer; border-radius: 999px; border: 1px solid var(--divider-color, #ccc);
        background: var(--card-background-color, #fff); color: var(--primary-text-color); padding: 4px 14px; }
      .pw { margin-left: auto; }
      .pw.small { margin-left: 0; padding: 2px 12px; font-size: 0.85rem; }
      .pw.on { background: var(--primary); color: var(--text-primary-color, #fff); border-color: transparent; }
      .feed { margin-top: 4px; }
      .feednow { width: 100%; margin-top: 2px; background: var(--primary); color: var(--text-primary-color, #fff); border-color: transparent; }
      .countdown { font-variant-numeric: tabular-nums; font-weight: 600; color: var(--primary); }
      .empty { color: var(--secondary-text-color); font-size: 0.9rem; padding: 8px 0; }
    </style>`;
  }

  disconnectedCallback() {
    for (const did of [...this._feedTimers.keys()]) this._clearFeedCountdown(did);
  }
}

customElements.define("jebao-pump-card", JebaoPumpCard);

// Register in the card picker so it's discoverable from "Add Card" with no
// YAML at all.
window.customCards = window.customCards || [];
window.customCards.push({
  type: "jebao-pump-card",
  name: "Jebao Pump",
  description: "Control a Jebao pump - power, wave mode, flow/frequency, and feed mode with a timer. Auto-discovers your pumps, no config needed.",
});
