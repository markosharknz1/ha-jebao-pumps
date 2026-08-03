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
 * HA's own fan/switch/select/number services, authenticated as the logged-in
 * dashboard user - there is nothing to configure. Pumps with a single clear
 * speed attribute get a native `fan` entity (see fan.py) instead of a
 * separate switch + number - the card renders that as a proper speed slider
 * with a combined power/speed control, same as HA's own fan card would.
 *
 * Has a visual editor (JebaoPumpCardEditor below) - editing the card in the
 * dashboard UI gives a pump-picker dropdown instead of needing to hand-type
 * a dids: list in YAML mode, for the common "one card per pump" layout.
 */

const ENTITY_RE = /^(switch|select|number|binary_sensor|fan|sensor)\.jebao_([a-z0-9]+)_(.+)$/;

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

// Numeric mode values inside a schedule slot (AutoTimeNN Byte4) - from the
// schema's own desc text: 0停机 1.经典造浪 2.正弦造浪 3.随机造浪 4.恒流造浪 5.喂食.
const SLOT_MODE_LABELS = {
  0: "Stop",
  1: "Classic wave",
  2: "Sine wave",
  3: "Random wave",
  4: "Constant flow",
  5: "Feeding",
};

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const pad2 = (n) => String(n).padStart(2, "0");

// Shared between the card and its config editor, so "which pumps exist and
// what are they called" is answered identically in both places.
function discoverPumps(hass, dids) {
  if (!hass) return [];
  const want = dids ? new Set(dids.map((d) => String(d).toLowerCase())) : null;

  const pumps = new Map(); // did -> {did, name, entities: {suffix: entity_id}}
  const registryEntities = hass.entities;
  const registryDevices = hass.devices;

  const addFromEntityId = (entityId, deviceName, deviceId) => {
    const m = entityId.match(ENTITY_RE);
    if (!m) return;
    const [, , did, suffix] = m;
    if (want && !want.has(did)) return;
    if (!pumps.has(did)) pumps.set(did, { did, name: deviceName || did, deviceId: null, entities: {} });
    pumps.get(did).entities[suffix] = entityId;
    // The schedule/clock services target a HA device_id, which only the
    // registry path can supply - the fallback path leaves it null and the
    // card hides the schedule editor for that pump.
    if (deviceId) pumps.get(did).deviceId = deviceId;
  };

  if (registryEntities && registryDevices) {
    // Preferred: the frontend's own entity/device registry cache (HA
    // 2024.8+), so grouping is correct even if entity_id naming ever
    // changes - no regex-on-device-name guessing.
    for (const [entityId, ent] of Object.entries(registryEntities)) {
      if (ent.platform !== "jebao_local") continue;
      const device = ent.device_id ? registryDevices[ent.device_id] : null;
      const name = device ? device.name_by_user || device.name : null;
      addFromEntityId(entityId, name, ent.device_id || null);
    }
  } else {
    // Fallback for older frontends without hass.entities/hass.devices:
    // pattern-match straight out of hass.states. Device name isn't known
    // here, so the did itself is shown as a heading instead.
    for (const entityId of Object.keys(hass.states)) {
      addFromEntityId(entityId, null, null);
    }
  }

  return [...pumps.values()].sort((a, b) => a.name.localeCompare(b.name));
}

class JebaoPumpCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._feedTimers = new Map(); // did -> {deadline, intervalId}
    this._schedOpen = new Set(); // dids with the schedule section expanded
    this._editSlot = new Map(); // did -> {slot|null (null = new), ...form values}
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

  static getConfigElement() {
    return document.createElement("jebao-pump-card-editor");
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  // -- discovering pumps ---------------------------------------------------

  _pumps() {
    return discoverPumps(this._hass, this._config.dids);
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

  // The power attribute's suffix varies by product - most use "switchon"
  // but several (mostly light products) use plain "switch" instead (see
  // fan.py's SWITCH_NAMES, which already accounts for both; found by
  // rendering all 29 bundled products through this card and noticing
  // lights showed no power toggle at all despite having a real entity).
  _powerEntityId(pump) {
    return pump.entities.switchon || pump.entities.switch || null;
  }

  // Pumps with a single clear speed attribute (Flow, or Motor_Speed on other
  // product lines) get a native `fan` entity instead of a separate switch +
  // number - see fan.py. Power and speed both route through it there;
  // pumps without one still use the plain switch.
  _togglePower(pump) {
    const e = pump.entities;
    if (e.fan) {
      const on = this._state(e.fan)?.state === "on";
      this._call("fan", on ? "turn_off" : "turn_on", { entity_id: e.fan });
      return;
    }
    const powerId = this._powerEntityId(pump);
    const on = this._state(powerId)?.state === "on";
    this._call("switch", on ? "turn_off" : "turn_on", { entity_id: powerId });
  }

  _setFanSpeed(pump, percentage) {
    this._call("fan", "set_percentage", { entity_id: pump.entities.fan, percentage });
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

  // -- schedule editing ----------------------------------------------------

  _scheduleSlots(pump) {
    const s = this._state(pump.entities.schedule);
    return (s && Array.isArray(s.attributes.slots)) ? s.attributes.slots : [];
  }

  _saveSlot(pump) {
    const form = this._editSlot.get(pump.did);
    if (!form) return;
    const [sh, sm] = form.start.split(":").map(Number);
    let [eh, em] = form.end.split(":").map(Number);
    // An HTML time input can't express 24:00, but the device (and the
    // vendor app's own default slot) uses end 24:00 for "until end of
    // day" - treat a midnight end as that.
    if (eh === 0 && em === 0) eh = 24;
    this._call("jebao_local", "set_schedule_slot", {
      device_id: pump.deviceId,
      slot: form.slot ?? this._nextFreeSlot(pump),
      start_hour: sh, start_minute: sm,
      end_hour: eh, end_minute: em,
      mode: Number(form.mode),
      flow: clamp(Number(form.flow) || 0, 0, 255),
      frequency: clamp(Number(form.frequency) || 0, 0, 255),
      pulse_tide: clamp(Number(form.pulse_tide) || 0, 0, 255),
    });
    this._editSlot.delete(pump.did);
    this._render();
  }

  _deleteSlot(pump, slot) {
    this._call("jebao_local", "clear_schedule_slot", { device_id: pump.deviceId, slot });
  }

  _syncClock(pump) {
    this._call("jebao_local", "sync_clock", { device_id: pump.deviceId });
  }

  _nextFreeSlot(pump) {
    const used = new Set(this._scheduleSlots(pump).map((s) => s.index));
    for (let i = 0; i < 48; i++) if (!used.has(i)) return i;
    return 47;
  }

  _openEditor(pump, slot) {
    const existing = slot != null ? this._scheduleSlots(pump).find((s) => s.index === slot) : null;
    this._editSlot.set(pump.did, existing
      ? {
          slot,
          start: `${pad2(existing.start_hour)}:${pad2(existing.start_minute)}`,
          end: `${pad2(existing.end_hour === 24 ? 0 : existing.end_hour)}:${pad2(existing.end_minute)}`,
          mode: existing.mode, flow: existing.flow,
          frequency: existing.frequency, pulse_tide: existing.pulse_tide,
        }
      // Same defaults as the vendor app's own "new slot" object (see
      // jebao_gizwits/schedule.py's module docstring).
      : { slot: null, start: "00:00", end: "00:00", mode: 1, flow: 100, frequency: 100, pulse_tide: 0 });
    this._render(true);
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
    const fan = e.fan ? this._state(e.fan) : null;
    const powerId = this._powerEntityId(pump);
    const power = fan || (powerId ? this._state(powerId) : null);
    const isOn = power?.state === "on";
    const mode = e.mode ? this._state(e.mode) : null;
    // Pumps with a fan entity absorb their speed attribute into it (see
    // fan.py) - flow/motor_speed no longer exist as separate number
    // entities there, so this only ever fires for pumps that didn't get one.
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
            power
              ? `<button class="pw ${isOn ? "on" : ""}" data-act="power">${isOn ? "On" : "Off"}</button>`
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

        ${fan ? this._fanSpeedRow(fan) : ""}
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

        ${this._scheduleSection(pump)}

        ${!power && !mode && !flow && !freq && !e.feedswitch ? `<div class="empty">No controllable attributes found for this pump.</div>` : ""}
      </div>`;
  }

  // The schedule editor needs the pump's HA device_id (the services target
  // devices, not entities) - only known via the registry discovery path, so
  // the section quietly doesn't render on the old-frontend fallback path.
  _scheduleSection(pump) {
    if (!pump.entities.schedule || !pump.deviceId) return "";
    const slots = this._scheduleSlots(pump);
    const open = this._schedOpen.has(pump.did);
    const clockState = pump.entities.deviceclock ? this._state(pump.entities.deviceclock) : null;
    const form = this._editSlot.get(pump.did);

    return `
      <div class="sched">
        <button class="schedhead" data-act="schedtoggle">
          <span>${open ? "▾" : "▸"} Schedule</span>
          <span class="schedcount">${slots.length ? `${slots.length} period${slots.length === 1 ? "" : "s"}` : "none set"}</span>
        </button>
        ${!open ? "" : `
          ${clockState ? `
            <div class="row">
              <label>Device clock</label>
              <span class="clockval">${clockState.state}</span>
              <button class="small2" data-act="syncclock" title="Set the pump's clock to Home Assistant's current time - schedules fire off this clock">Sync</button>
            </div>` : ""}
          ${slots.map((s) => `
            <div class="slotrow" data-slot="${s.index}">
              <span class="slottime">${pad2(s.start_hour)}:${pad2(s.start_minute)}&ndash;${pad2(s.end_hour)}:${pad2(s.end_minute)}</span>
              <span class="slotmode">${SLOT_MODE_LABELS[s.mode] ?? `Mode ${s.mode}`}${s.mode !== 0 ? ` &middot; ${s.flow}%` : ""}</span>
              <button class="small2" data-act="schededit" data-slot="${s.index}">Edit</button>
              <button class="small2 danger" data-act="scheddelete" data-slot="${s.index}">✕</button>
            </div>`).join("")}
          ${form ? this._slotForm(form) : `<button class="addslot" data-act="schedadd">+ Add period</button>`}
        `}
      </div>`;
  }

  _slotForm(form) {
    return `
      <div class="slotform">
        <div class="row">
          <label>${form.slot != null ? `Edit period (slot ${form.slot})` : "New period"}</label>
        </div>
        <div class="row">
          <label>Start&ndash;end</label>
          <input type="time" data-form="start" value="${form.start}">
          <input type="time" data-form="end" value="${form.end}" title="00:00 as the end time means end of day (24:00)">
        </div>
        <div class="row">
          <label>Mode</label>
          <select data-form="mode">
            ${Object.entries(SLOT_MODE_LABELS).map(([v, l]) =>
              `<option value="${v}" ${Number(v) === Number(form.mode) ? "selected" : ""}>${l}</option>`).join("")}
          </select>
        </div>
        <div class="row">
          <label>Flow %</label>
          <input type="number" min="0" max="100" data-form="flow" value="${form.flow}">
          <label class="inline">Freq %</label>
          <input type="number" min="0" max="100" data-form="frequency" value="${form.frequency}">
          <label class="inline">Pulse</label>
          <input type="number" min="0" max="255" data-form="pulse_tide" value="${form.pulse_tide}">
        </div>
        <div class="row">
          <button class="feednow half" data-act="schedsave">Save</button>
          <button class="half" data-act="schedcancel">Cancel</button>
        </div>
      </div>`;
  }

  _fanSpeedRow(fanState) {
    const value = Number(fanState.attributes.percentage) || 0;
    const step = fanState.attributes.percentage_step || 1;
    return `
      <div class="row">
        <label>Speed</label>
        <input type="range" min="0" max="100" step="${step}" value="${value}" data-act="fanspeed">
        <span class="val">${value}%</span>
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

  _render(force) {
    // Skip state-driven re-renders while a slot form is open - innerHTML
    // replacement would wipe what the user is typing. Explicit UI actions
    // pass force=true; the form's own inputs sync into _editSlot on
    // "input" so nothing is lost either way.
    if (!force && this._built && this._editSlot.size) return;
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
    else if (act === "schedtoggle") {
      this._schedOpen.has(pump.did) ? this._schedOpen.delete(pump.did) : this._schedOpen.add(pump.did);
      this._render(true);
    } else if (act === "syncclock") this._syncClock(pump);
    else if (act === "schedadd") this._openEditor(pump, null);
    else if (act === "schededit") this._openEditor(pump, Number(el.dataset.slot));
    else if (act === "scheddelete") this._deleteSlot(pump, Number(el.dataset.slot));
    else if (act === "schedsave") this._saveSlot(pump);
    else if (act === "schedcancel") {
      this._editSlot.delete(pump.did);
      this._render(true);
    }
  }

  _onChange(e) {
    // Belt-and-braces for the slot form's select/time inputs - some
    // browsers only fire "change" (not "input") for these.
    const formEl = e.target.closest("[data-form]");
    if (formEl) {
      const formPump = this._pumpFor(formEl);
      const form = formPump && this._editSlot.get(formPump.did);
      if (form) form[formEl.dataset.form] = formEl.value;
      return;
    }
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const pump = this._pumpFor(el);
    if (!pump) return;
    if (el.dataset.act === "mode") this._setMode(pump, el.value);
    else if (el.dataset.act === "slider") this._setNumber(pump.entities[el.dataset.suffix], Number(el.value));
    else if (el.dataset.act === "fanspeed") this._setFanSpeed(pump, Number(el.value));
  }

  _onInput(e) {
    // Slot-form fields sync straight into _editSlot so the form survives
    // any re-render (see _render's skip-while-editing note).
    const formEl = e.target.closest("[data-form]");
    if (formEl) {
      const pump = this._pumpFor(formEl);
      const form = pump && this._editSlot.get(pump.did);
      if (form) form[formEl.dataset.form] = formEl.value;
      return;
    }
    // Live-update the displayed slider value while dragging, without
    // spamming a service call per pixel - the actual write happens on
    // "change" (pointer release), handled above.
    const el = e.target.closest('[data-act="slider"], [data-act="fanspeed"]');
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
      .sched { margin-top: 10px; border-top: 1px dashed var(--divider-color, #eee); padding-top: 6px; }
      .schedhead { width: 100%; display: flex; justify-content: space-between; align-items: center;
        border: none; background: none; padding: 4px 0; font-size: 0.9rem; color: var(--primary-text-color); }
      .schedcount { color: var(--secondary-text-color); font-size: 0.82rem; }
      .clockval { flex: 1; font-variant-numeric: tabular-nums; font-size: 0.85rem; color: var(--secondary-text-color); }
      .slotrow { display: flex; align-items: center; gap: 8px; margin: 6px 0; font-size: 0.88rem; }
      .slottime { font-variant-numeric: tabular-nums; font-weight: 500; }
      .slotmode { flex: 1; color: var(--secondary-text-color); }
      .small2 { padding: 2px 10px; font-size: 0.8rem; }
      .danger { color: var(--error-color, #db4437); }
      .addslot { width: 100%; margin-top: 4px; border-style: dashed; color: var(--secondary-text-color); }
      .slotform { border: 1px solid var(--divider-color, #ccc); border-radius: 8px; padding: 8px 10px; margin-top: 6px; }
      .slotform input[type=time], .slotform input[type=number] { font: inherit; padding: 3px 6px; border-radius: 6px;
        border: 1px solid var(--divider-color, #ccc); background: var(--card-background-color, #fff);
        color: var(--primary-text-color); }
      .slotform input[type=number] { width: 60px; }
      .slotform label.inline { min-width: unset; }
      .half { flex: 1; }
    </style>`;
  }

  disconnectedCallback() {
    for (const did of [...this._feedTimers.keys()]) this._clearFeedCountdown(did);
  }
}

customElements.define("jebao-pump-card", JebaoPumpCard);

// Visual editor (HA's card-editor contract: setConfig/hass in, a
// "config-changed" CustomEvent out). Lets you pick one pump from a
// dropdown instead of hand-typing a dids: list in YAML mode - the point
// of this being a native card in the first place.
class JebaoPumpCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._built = false;
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._built) {
      this.shadowRoot.innerHTML = `<style>
          .wrap { padding: 12px 0; display: flex; flex-direction: column; gap: 14px; }
          .field { display: flex; flex-direction: column; gap: 6px; }
          label { font-size: 0.85rem; color: var(--secondary-text-color); }
          select, input { font: inherit; padding: 6px 8px; border-radius: 6px;
            border: 1px solid var(--divider-color, #ccc); background: var(--card-background-color, #fff);
            color: var(--primary-text-color); }
        </style>
        <div class="wrap">
          <div class="field">
            <label for="pumpSel">Pump</label>
            <select id="pumpSel"></select>
          </div>
          <div class="field">
            <label for="nameInput">Heading override (optional, only used with one pump selected)</label>
            <input id="nameInput" type="text" placeholder="Leave blank to use the device's own name">
          </div>
        </div>`;
      this._built = true;
      this.shadowRoot.getElementById("pumpSel").addEventListener("change", () => this._emit());
      this.shadowRoot.getElementById("nameInput").addEventListener("input", () => this._emit());
    }

    const pumps = discoverPumps(this._hass, null);
    const currentDid = ((this._config.dids || [])[0] || "").toLowerCase();
    const sel = this.shadowRoot.getElementById("pumpSel");
    const focused = this.shadowRoot.activeElement === sel;
    sel.innerHTML =
      `<option value="">All pumps</option>` +
      pumps.map((p) => `<option value="${p.did}" ${p.did === currentDid ? "selected" : ""}>${p.name} (${p.did})</option>`).join("");
    if (!focused) sel.value = currentDid || "";

    const nameInput = this.shadowRoot.getElementById("nameInput");
    if (this.shadowRoot.activeElement !== nameInput) nameInput.value = this._config.name || "";
  }

  _emit() {
    const sel = this.shadowRoot.getElementById("pumpSel");
    const name = this.shadowRoot.getElementById("nameInput").value.trim();
    const newConfig = { ...this._config };
    if (sel.value) newConfig.dids = [sel.value];
    else delete newConfig.dids;
    if (name) newConfig.name = name;
    else delete newConfig.name;
    this._config = newConfig;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: newConfig }, bubbles: true, composed: true }));
  }
}
customElements.define("jebao-pump-card-editor", JebaoPumpCardEditor);

// Register in the card picker so it's discoverable from "Add Card" with no
// YAML at all.
window.customCards = window.customCards || [];
window.customCards.push({
  type: "jebao-pump-card",
  name: "Jebao Pump",
  description: "Control a Jebao pump - power, wave mode, flow/frequency, and feed mode with a timer. Auto-discovers your pumps, no config needed.",
});
