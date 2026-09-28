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

// Numeric mode values inside a schedule slot (AutoTimeNN Byte4). These are
// PER PRODUCT - both the numbering and the set of modes differ - so they're
// keyed by the slot's byte length, which is what distinguishes the layouts
// (see jebao_gizwits/schedule.py). Taken from each schema's own desc text.
const SLOT_MODE_LABELS_BY_LEN = {
  // Base wavemaker: 0停机 1.经典造浪 2.正弦造浪 3.随机造浪 4.恒流造浪 5.喂食
  8: {
    0: "Stop", 1: "Classic wave", 2: "Sine wave", 3: "Random wave",
    4: "Constant flow", 5: "Feeding",
  },
  // Wavemaker Pro: 0.脉冲造浪 1.正弦造浪 2.恒流造浪 3.随机造浪 4.潮汐涨落
  // 5.营养输送 6.环流造浪 7.喂食模式 8.自定义造浪
  9: {
    0: "Pulse wave", 1: "Sine wave", 2: "Constant flow", 3: "Random wave",
    4: "Tidal", 5: "Nutrient delivery", 6: "Circulation", 7: "Feeding",
    8: "Custom wave",
  },
};

// A slot's field set is inferred from which keys the backend actually sent
// (the Schedule sensor only reports fields the product has). Only used as
// a fallback now - integrations from 0.4.2 publish slot_len and the
// product's own slot_modes table directly.
const slotLenOf = (slot) =>
  !slot ? 8 : ("cust_wave_freq" in slot || "feed_time" in slot) ? 9 : ("frequency" in slot ? 8 : 6);
const modeLabelsForLen = (len) => SLOT_MODE_LABELS_BY_LEN[len] || SLOT_MODE_LABELS_BY_LEN[8];

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const pad2 = (n) => String(n).padStart(2, "0");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hhmm = (mins) => mins >= 1440 ? "24:00" : `${pad2(Math.floor(mins / 60))}:${pad2(mins % 60)}`;
const MINUTES_PER_DAY = 1440;
const SLOT_COUNT = 48;
// Comfortably longer than HA's 10s refresh hold-off plus a slow pump.
const PENDING_TIMEOUT_MS = 30000;

// Does the pump's reported schedule now match a program we wrote?
function sameProgram(slots, expected) {
  const key = (start, end, speed, mode) => `${start}-${end}-${speed}-${Number(mode)}`;
  const have = slots.map((s) => key(s.start_hour * 60 + s.start_minute, s.end_hour * 60 + s.end_minute, s.flow, s.mode)).sort();
  const want = expected.map((s) => key(s.start, s.end, s.speed, s.mode)).sort();
  return have.length === want.length && have.every((k, i) => k === want[i]);
}

// Mirror of jebao_gizwits/program.py's compile_program, so the editor can
// draw exactly what will be written before anything is sent. The backend
// re-validates everything - this is only the preview - but keeping the two
// in step means the preview never promises something the pump won't do.
function compileProgram(steps, startMins, repeat) {
  if (!steps.length) return { error: "Add at least one step." };
  for (const [i, s] of steps.entries()) {
    if (!(s.minutes >= 1)) return { error: `Step ${i + 1} needs a length.` };
  }
  const total = steps.reduce((a, s) => a + s.minutes, 0);
  if (!repeat && total > MINUTES_PER_DAY) {
    return { error: `The steps add up to ${(total / 60).toFixed(1)} h - more than a day.` };
  }
  const limit = repeat ? MINUTES_PER_DAY : total;
  const slots = [];
  let cursor = startMins, placed = 0, i = 0;
  while (placed < limit) {
    const step = steps[i++ % steps.length];
    const len = Math.min(step.minutes, limit - placed);
    const begin = cursor % MINUTES_PER_DAY;
    const end = begin + len;
    const pieces = end <= MINUTES_PER_DAY ? [[begin, end]] : [[begin, MINUTES_PER_DAY], [0, end - MINUTES_PER_DAY]];
    for (const [s, e] of pieces) slots.push({ start: s, end: e, speed: step.speed, mode: step.mode });
    cursor += len;
    placed += len;
  }
  if (slots.length > SLOT_COUNT) {
    return { error: `Needs ${slots.length} timer slots; the pump has ${SLOT_COUNT}. Use fewer or longer steps${repeat ? ", or turn repeat off" : ""}.` };
  }
  return { slots, total };
}

// Turn what's already on the pump back into editable steps, so opening the
// editor starts from the current program rather than a blank page. Only
// possible when the periods run back to back; a schedule with gaps can't
// be expressed as a sequence and the editor starts from a template instead.
function stepsFromSlots(slots) {
  if (!slots.length) return null;
  const spans = slots
    .map((s) => ({ start: s.start_hour * 60 + s.start_minute, end: s.end_hour * 60 + s.end_minute, s }))
    .sort((a, b) => a.start - b.start);
  // Rotate so a program that wraps midnight starts where it really starts.
  let first = 0;
  for (let k = 0; k < spans.length; k++) {
    const prevEnd = spans[(k - 1 + spans.length) % spans.length].end % MINUTES_PER_DAY;
    if (prevEnd !== spans[k].start) { first = k; break; }
  }
  const ordered = spans.slice(first).concat(spans.slice(0, first));
  const steps = [];
  for (let k = 0; k < ordered.length; k++) {
    const cur = ordered[k];
    if (k > 0 && ordered[k - 1].end % MINUTES_PER_DAY !== cur.start) return null; // a gap
    const len = cur.end - cur.start;
    if (len <= 0) return null;
    const prev = steps[steps.length - 1];
    // Re-join a period that was split at midnight.
    if (prev && cur.start === 0 && prev.speed === cur.s.flow && prev.mode === String(cur.s.mode)) prev.minutes += len;
    else steps.push({ minutes: len, speed: cur.s.flow, mode: String(cur.s.mode) });
  }
  return { start: ordered[0].start, steps };
}

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
    this._program = new Map(); // did -> {start, repeat, steps[], error, busy}
    // Writes the pump has accepted but Home Assistant hasn't re-read yet.
    // HA refreshes straight after the first write, then holds any further
    // refresh for up to 10s - so without this, a second delete (or most of
    // a multi-slot program) looks like it did nothing and people click again.
    this._pending = new Map(); // did -> {slots: Map(index -> stamp), clearing, program}
    this._schedNote = new Map(); // did -> a message to show, e.g. a timeout
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

  // Which per-product slot layout this pump uses. The backend publishes it
  // (see sensor.py) so a pump with no periods programmed yet still gets the
  // right form; fall back to inferring from an existing slot for older
  // integration versions that didn't send it.
  _slotLen(pump) {
    const s = this._state(pump.entities.schedule);
    const declared = s?.attributes?.slot_len;
    if (declared === 6 || declared === 8 || declared === 9) return declared;
    const existing = this._scheduleSlots(pump)[0];
    return existing ? slotLenOf(existing) : 8;
  }

  // This pump's own slot-mode table ({"0": "Stop", ...}), published by the
  // Schedule sensor. The hardcoded table is only for older integrations.
  _slotModes(pump) {
    const published = this._state(pump.entities.schedule)?.attributes?.slot_modes;
    if (published && Object.keys(published).length) return published;
    return modeLabelsForLen(this._slotLen(pump));
  }

  _slotModeLabel(pump, mode) {
    return this._slotModes(pump)[String(mode)] ?? `Mode ${mode}`;
  }

  // 6-byte pumps (filters, return pumps) call their speed a 档位 "level"
  // rather than a flow percentage. Every one of them still defines it as
  // 0-100 (AutoGears uint_spec), so the control is the same slider - only
  // the wording differs.
  _speedUnit(pump) {
    return this._slotLen(pump) === 6 ? "level" : "%";
  }

  _fmtSpeed(pump, v) {
    return this._speedUnit(pump) === "%" ? `${v}%` : `level ${v}`;
  }

  // -- program editing ---------------------------------------------------

  // The mode a new step starts in: whatever the pump is running right now,
  // matched by name (a slot's mode numbers differ from the live Mode
  // attribute's - see schedule.slot_mode_options). Every step shows a real
  // mode; there is deliberately no "current mode" placeholder, since that
  // would leave the step's actual behaviour invisible in the editor.
  _defaultStepMode(pump) {
    const modes = Object.entries(this._slotModes(pump));
    if (!modes.length) return "";
    const liveId = pump.entities.mode;
    const live = liveId && liveId.startsWith("select.") ? this._state(liveId)?.state : null;
    if (live) {
      const wanted = modeLabel(live).toLowerCase();
      const hit = modes.find(([, label]) => label.toLowerCase() === wanted);
      if (hit) return hit[0];
    }
    // No readable live mode (filters and return pumps don't expose one):
    // the first mode that actually runs the pump - never stop or feed.
    const running = modes.find(([, label]) => !/^(stop|feed)/i.test(label));
    return (running || modes[0])[0];
  }

  _openProgram(pump) {
    const existing = stepsFromSlots(this._scheduleSlots(pump));
    const mode = this._defaultStepMode(pump);
    this._program.set(pump.did, existing
      ? { start: hhmm(existing.start), repeat: false, steps: existing.steps, error: null, busy: false, fromPump: true }
      : {
          start: "08:00", repeat: false, error: null, busy: false, fromPump: false,
          // The example from the feature request, as a starting point.
          steps: [{ minutes: 240, speed: 100, mode }, { minutes: 360, speed: 40, mode }],
        });
    this._render(true);
  }

  _programPreview(prog) {
    const [h, m] = String(prog.start || "00:00").split(":").map(Number);
    const steps = prog.steps.map((s) => ({ ...s, minutes: Number(s.minutes) || 0, speed: Number(s.speed) || 0 }));
    return compileProgram(steps, ((h || 0) * 60 + (m || 0)) % MINUTES_PER_DAY, !!prog.repeat);
  }

  async _writeProgram(pump) {
    const prog = this._program.get(pump.did);
    if (!prog || prog.busy) return;
    const preview = this._programPreview(prog);
    if (preview.error) { prog.error = preview.error; this._render(true); return; }
    const existingCount = this._scheduleSlots(pump).length;
    if (existingCount && !prog.fromPump &&
        !confirm(`Replace the ${existingCount} period${existingCount === 1 ? "" : "s"} already on ${pump.name}?`)) return;

    prog.busy = true; prog.error = null;
    this._render(true);
    try {
      await this._call("jebao_local", "set_program", {
        device_id: pump.deviceId,
        start: prog.start,
        repeat: !!prog.repeat,
        steps: prog.steps.map((s) => {
          const step = { speed: clamp(Number(s.speed) || 0, 0, 255), minutes: Number(s.minutes) };
          if (s.mode !== "" && s.mode != null) step.mode = Number(s.mode);
          return step;
        }),
      });
      this._program.delete(pump.did);
      // The editor closes immediately, so without this the old schedule
      // would sit there looking like the write was ignored.
      const pend = this._pendingFor(pump.did);
      this._schedNote.delete(pump.did);
      pend.program = {
        expected: preview.slots,
        stamp: this._expirePending(pump,
          (p, s) => p.program?.stamp === s && ((p.program = null), true),
          "The pump hasn't confirmed the new program yet - it may still be applying, or be offline. Check again shortly."),
      };
    } catch (err) {
      // Service validation errors (e.g. "can't tell which mode...") are
      // written for people - show them where the user is looking.
      prog.busy = false;
      prog.error = err?.message || String(err);
    }
    this._render(true);
  }

  _editSteps(pump, act, i) {
    const prog = this._program.get(pump.did);
    if (!prog) return;
    const steps = prog.steps;
    if (act === "progadd") {
      const last = steps[steps.length - 1];
      steps.push({ minutes: 60, speed: last ? last.speed : 50, mode: last ? last.mode : this._defaultStepMode(pump) });
    } else if (act === "progdel" && steps.length > 1) steps.splice(i, 1);
    else if (act === "progup" && i > 0) [steps[i - 1], steps[i]] = [steps[i], steps[i - 1]];
    else if (act === "progdown" && i < steps.length - 1) [steps[i + 1], steps[i]] = [steps[i], steps[i + 1]];
    else return;
    prog.error = null;
    this._render(true);
  }

  // Field edits. Structural changes re-render straight away; typing into a
  // number box only refreshes the preview, so focus and caret aren't lost.
  _onProgramInput(pump, el, commit) {
    const prog = this._program.get(pump.did);
    if (!prog) return;
    const field = el.dataset.prog;
    const step = prog.steps[Number(el.dataset.step)];
    if (field === "start") prog.start = el.value;
    else if (field === "repeat") prog.repeat = el.checked;
    else if (field === "mode") step.mode = el.value;
    else if (field === "speed") {
      step.speed = Number(el.value) || 0;
      const val = el.parentElement.querySelector(".val");
      if (val) val.textContent = this._fmtSpeed(pump, step.speed);
    } else if (field === "hours" || field === "mins") {
      const row = el.closest(".row");
      const h = clamp(Number(row.querySelector('[data-prog="hours"]').value) || 0, 0, 24);
      const m = clamp(Number(row.querySelector('[data-prog="mins"]').value) || 0, 0, 59);
      step.minutes = h * 60 + m;
    }
    prog.error = null;
    if (commit) this._render(true);
    else this._refreshProgramPreview(pump);
  }

  _refreshProgramPreview(pump) {
    const prog = this._program.get(pump.did);
    const root = this.shadowRoot.querySelector(`.pump[data-did="${pump.did}"] .program`);
    if (!prog || !root) return;
    const fresh = document.createElement("div");
    fresh.innerHTML = this._programEditor(pump, prog);
    for (const sel of [".timeline", ".progsum, .progerr", '[data-act="progwrite"]']) {
      const oldEl = root.querySelector(sel);
      const newEl = fresh.querySelector(sel);
      if (oldEl && newEl) oldEl.replaceWith(newEl);
    }
  }

  async _clearProgram(pump) {
    const n = this._scheduleSlots(pump).length;
    if (!n || !confirm(`Erase all ${n} period${n === 1 ? "" : "s"} from ${pump.name}? It goes back to manual control.`)) return;
    const pend = this._pendingFor(pump.did);
    this._schedNote.delete(pump.did);
    pend.clearing = this._expirePending(pump,
      (p, s) => p.clearing === s && ((p.clearing = null), true),
      "The pump hasn't confirmed the schedule was cleared - it may be offline. Try again.");
    this._render(true);
    try {
      await this._call("jebao_local", "clear_program", { device_id: pump.deviceId });
    } catch (err) {
      pend.clearing = null;
      this._schedNote.set(pump.did, err?.message || String(err));
      this._render(true);
    }
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
    const byte = (v) => clamp(Number(v) || 0, 0, 255);
    const data = {
      device_id: pump.deviceId,
      slot: form.slot ?? this._nextFreeSlot(pump),
      start_hour: sh, start_minute: sm,
      end_hour: eh, end_minute: em,
      mode: Number(form.mode),
      flow: byte(form.flow),
      frequency: byte(form.frequency),
    };
    // Trailing fields differ per product - send only the ones this pump's
    // slot layout actually has (the service ignores the rest, but sending
    // a field the product has no concept of would be misleading).
    if (this._slotLen(pump) === 9) {
      data.feed_time = byte(form.feed_time);
      data.cust_wave_freq = byte(form.cust_wave_freq);
    } else {
      data.pulse_tide = byte(form.pulse_tide);
    }
    this._call("jebao_local", "set_schedule_slot", data);
    this._editSlot.delete(pump.did);
    this._render();
  }

  _pendingFor(did) {
    if (!this._pending.has(did)) this._pending.set(did, { slots: new Map(), clearing: null, program: null });
    return this._pending.get(did);
  }

  // Drop whatever the pump's latest reported schedule now confirms.
  _prunePending(pump, slots) {
    const pend = this._pending.get(pump.did);
    if (!pend) return null;
    const present = new Set(slots.map((s) => s.index));
    for (const index of [...pend.slots.keys()]) if (!present.has(index)) pend.slots.delete(index);
    if (pend.clearing && !slots.length) pend.clearing = null;
    if (pend.program && sameProgram(slots, pend.program.expected)) pend.program = null;
    if (!pend.slots.size && !pend.clearing && !pend.program) {
      this._pending.delete(pump.did);
      return null;
    }
    return pend;
  }

  // If the pump never reports the change, stop showing it as in progress
  // and say so - leaving it greyed out forever would be worse than useless.
  _expirePending(pump, expire, message) {
    const stamp = Date.now();
    setTimeout(() => {
      const pend = this._pending.get(pump.did);
      if (!pend || !expire(pend, stamp)) return;
      this._schedNote.set(pump.did, message);
      this._render(true);
    }, PENDING_TIMEOUT_MS);
    return stamp;
  }

  async _deleteSlot(pump, slot) {
    const pend = this._pendingFor(pump.did);
    if (pend.slots.has(slot)) return;  // already on its way
    this._schedNote.delete(pump.did);
    pend.slots.set(slot, this._expirePending(pump,
      (p, s) => p.slots.get(slot) === s && (p.slots.delete(slot), true),
      "The pump hasn't confirmed that period was removed - it may be offline. Try again."));
    this._render(true);
    try {
      await this._call("jebao_local", "clear_schedule_slot", { device_id: pump.deviceId, slot });
    } catch (err) {
      pend.slots.delete(slot);
      this._schedNote.set(pump.did, err?.message || String(err));
      this._render(true);
    }
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
          frequency: existing.frequency,
          // Whichever trailing fields this product's slots carry.
          pulse_tide: existing.pulse_tide ?? 0,
          feed_time: existing.feed_time ?? 0,
          cust_wave_freq: existing.cust_wave_freq ?? 0,
        }
      // Same defaults as the vendor app's own "new slot" object (see
      // jebao_gizwits/schedule.py's module docstring).
      : {
          slot: null, start: "00:00", end: "00:00", mode: 1,
          flow: 100, frequency: 100,
          pulse_tide: 0, feed_time: 0, cust_wave_freq: 0,
        });
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

        ${this._modeRow(pump)}

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
    const prog = this._program.get(pump.did);
    const pend = this._prunePending(pump, slots);
    const note = this._schedNote.get(pump.did);
    const noteHtml = note ? `<div class="progerr">${esc(note)}</div>` : "";

    // The program editor replaces the slot list while it's open - showing
    // both would mean two conflicting views of the same 48 slots.
    const body = prog ? this._programEditor(pump, prog) : pend?.program ? `
      ${this._timeline(pump, pend.program.expected, true)}
      <div class="pendingnote"><span class="spinner"></span>Writing program &mdash; waiting for the pump to confirm&hellip;</div>
    ` : `
      ${noteHtml}
      ${this._timeline(pump, slots.map((s) => ({
          start: s.start_hour * 60 + s.start_minute,
          end: s.end_hour * 60 + s.end_minute,
          speed: s.flow, mode: s.mode,
        })))}
      ${[...slots].sort((a, b) => (a.start_hour * 60 + a.start_minute) - (b.start_hour * 60 + b.start_minute)).map((s) => `
        ${(() => {
          const removing = !!pend && (!!pend.clearing || pend.slots.has(s.index));
          return `
        <div class="slotrow ${removing ? "pending" : ""}" data-slot="${s.index}">
          <span class="slottime">${pad2(s.start_hour)}:${pad2(s.start_minute)}&ndash;${pad2(s.end_hour)}:${pad2(s.end_minute)}</span>
          <span class="slotmode">${removing
            ? `<span class="spinner"></span>Removing&hellip;`
            : `${esc(this._slotModeLabel(pump, s.mode))} &middot; ${this._fmtSpeed(pump, s.flow)}`}</span>
          <button class="small2" data-act="schededit" data-slot="${s.index}" ${removing ? "disabled" : ""}>Edit</button>
          <button class="small2 danger" data-act="scheddelete" data-slot="${s.index}" ${removing ? "disabled" : ""} aria-label="Remove period">✕</button>
        </div>`;
        })()}`).join("")}
      ${form ? this._slotForm(form, this._slotLen(pump), this._slotModes(pump)) : `
        <div class="row schedbtns">
          <button class="feednow half" data-act="progopen">${slots.length ? "Edit as program" : "Build program"}</button>
          <button class="half" data-act="schedadd">+ Single period</button>
        </div>
        ${slots.length ? `<button class="linkbtn danger" data-act="progclear" ${pend?.clearing ? "disabled" : ""}>${
          pend?.clearing ? `<span class="spinner"></span>Clearing&hellip;` : "Clear all periods"}</button>` : ""}`}
    `;

    return `
      <div class="sched">
        <button class="schedhead" data-act="schedtoggle">
          <span>${open ? "▾" : "▸"} Schedule</span>
          <span class="schedcount">${pend?.program ? "updating&hellip;" : slots.length ? `${slots.length} period${slots.length === 1 ? "" : "s"}` : "none set"}</span>
        </button>
        ${!open ? "" : `
          ${clockState ? `
            <div class="row">
              <label>Device clock</label>
              <span class="clockval">${esc(clockState.state)}</span>
              <button class="small2" data-act="syncclock" title="Set the pump's clock to Home Assistant's current time - schedules fire off this clock">Sync</button>
            </div>` : ""}
          ${body}
        `}
      </div>`;
  }

  // A 24-hour bar chart: one bar per period, height = speed. The same
  // picture the vendor apps use for light programs, and the quickest way
  // to see gaps or a step that landed at the wrong time.
  _timeline(pump, periods, pending = false) {
    const unitMax = 100;
    const now = new Date();
    const nowMins = now.getHours() * 60 + now.getMinutes();
    const bars = periods.map((p) => {
      const h = Math.max(4, Math.round((clamp(p.speed, 0, unitMax) / unitMax) * 100));
      const label = `${hhmm(p.start)}–${hhmm(p.end)} · ${this._slotModeLabel(pump, p.mode)} · ${this._fmtSpeed(pump, p.speed)}`;
      return `<rect class="bar" x="${p.start}" y="${100 - h}" width="${Math.max(1, p.end - p.start)}" height="${h}"><title>${esc(label)}</title></rect>`;
    }).join("");
    return `
      <div class="timeline ${pending ? "pending" : ""}">
        <svg viewBox="0 0 1440 100" preserveAspectRatio="none" role="img" aria-label="24-hour schedule">
          ${[360, 720, 1080].map((x) => `<line class="grid" x1="${x}" x2="${x}" y1="0" y2="100"/>`).join("")}
          ${bars}
          <line class="now" x1="${nowMins}" x2="${nowMins}" y1="0" y2="100"/>
        </svg>
        ${bars ? "" : `<div class="none">No periods</div>`}
        <div class="ticks"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>
      </div>`;
  }

  _programEditor(pump, prog) {
    const modes = this._slotModes(pump);
    const preview = this._programPreview(prog);
    const modeOptions = (selected) => `
      ${Object.entries(modes).map(([v, l]) =>
        `<option value="${v}" ${String(selected) === v ? "selected" : ""}>${esc(l)}</option>`).join("")}`;

    const stepRows = prog.steps.map((s, i) => {
      const mins = Number(s.minutes) || 0;
      return `
      <div class="step" data-step="${i}">
        <div class="stephead">
          <span class="stepno">${i + 1}</span>
          <select data-prog="mode" data-step="${i}" aria-label="Step ${i + 1} mode">${modeOptions(s.mode)}</select>
          <button class="icon" data-act="progup" data-step="${i}" ${i === 0 ? "disabled" : ""} aria-label="Move up">↑</button>
          <button class="icon" data-act="progdown" data-step="${i}" ${i === prog.steps.length - 1 ? "disabled" : ""} aria-label="Move down">↓</button>
          <button class="icon danger" data-act="progdel" data-step="${i}" ${prog.steps.length === 1 ? "disabled" : ""} aria-label="Remove step">✕</button>
        </div>
        <div class="row">
          <label>Speed</label>
          <input type="range" min="0" max="100" value="${clamp(Number(s.speed) || 0, 0, 100)}" data-prog="speed" data-step="${i}" aria-label="Step ${i + 1} speed">
          <span class="val">${this._fmtSpeed(pump, Number(s.speed) || 0)}</span>
        </div>
        <div class="row">
          <label>For</label>
          <input type="number" class="num" min="0" max="24" value="${Math.floor(mins / 60)}" data-prog="hours" data-step="${i}" aria-label="Hours">
          <span class="unit">h</span>
          <input type="number" class="num" min="0" max="59" step="5" value="${mins % 60}" data-prog="mins" data-step="${i}" aria-label="Minutes">
          <span class="unit">min</span>
        </div>
      </div>`;
    }).join("");

    const summary = preview.error
      ? `<div class="progerr">${esc(preview.error)}</div>`
      : `<div class="progsum">${preview.slots.length} of ${SLOT_COUNT} slots &middot; ${
          prog.repeat ? "repeats all day" : `${hhmm(preview.slots[0].start)} → ${hhmm((preview.slots[0].start + preview.total) % MINUTES_PER_DAY || MINUTES_PER_DAY)}, then manual`}</div>`;

    return `
      <div class="program">
        <div class="row">
          <label>Start at</label>
          <input type="time" data-prog="start" value="${esc(prog.start)}">
          <label class="check"><input type="checkbox" data-prog="repeat" ${prog.repeat ? "checked" : ""}> Repeat all day</label>
        </div>
        ${this._timeline(pump, preview.error ? [] : preview.slots)}
        ${summary}
        ${stepRows}
        <button class="addslot" data-act="progadd">+ Add step</button>
        ${prog.error ? `<div class="progerr">${esc(prog.error)}</div>` : ""}
        <div class="row">
          <button class="feednow half" data-act="progwrite" ${prog.busy || preview.error ? "disabled" : ""}>${prog.busy ? "Writing…" : "Write to pump"}</button>
          <button class="half" data-act="progcancel" ${prog.busy ? "disabled" : ""}>Cancel</button>
        </div>
        <div class="hint">Saved on the pump itself - keeps running if Home Assistant is off.</div>
      </div>`;
  }

  _slotForm(form, slotLen, modes) {
    // Trailing fields are per product (see the mode-label table above):
    // the base wavemaker's slot ends in a pulse/tide value, the Pro's in a
    // feed duration and a custom-wave frequency, and 6-byte pumps have
    // neither - nor a frequency.
    const extras = slotLen === 6 ? "" : slotLen === 9
      ? `<label class="inline">Feed min</label>
         <input type="number" min="0" max="255" data-form="feed_time" value="${form.feed_time ?? 0}">
         <label class="inline">Custom Hz</label>
         <input type="number" min="0" max="100" data-form="cust_wave_freq" value="${form.cust_wave_freq ?? 0}">`
      : `<label class="inline">Pulse</label>
         <input type="number" min="0" max="255" data-form="pulse_tide" value="${form.pulse_tide ?? 0}">`;

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
            ${Object.entries(modes).map(([v, l]) =>
              `<option value="${v}" ${Number(v) === Number(form.mode) ? "selected" : ""}>${esc(l)}</option>`).join("")}
          </select>
        </div>
        <div class="row">
          <label>${slotLen === 6 ? "Level" : "Flow %"}</label>
          <input type="number" min="0" max="100" data-form="flow" value="${form.flow}">
          ${slotLen === 6 ? "" : `<label class="inline">Freq %</label>
          <input type="number" min="0" max="100" data-form="frequency" value="${form.frequency}">`}
        </div>
        ${extras ? `<div class="row">
          <label>&nbsp;</label>
          ${extras}
        </div>` : ""}
        <div class="row">
          <button class="feednow half" data-act="schedsave">Save</button>
          <button class="half" data-act="schedcancel">Cancel</button>
        </div>
      </div>`;
  }

  // "Mode" is not one entity kind across the catalogue: it is a declared
  // enum on 13 products (-> select), a labelled uint8 on 3 (-> select,
  // built from the schema desc, see mode_options.py) and a plain bool on
  // 13 more (-> switch). The card used to grab whatever entity was named
  // "mode" and always draw a <select> from `attributes.options`, so on
  // the 16 non-enum products - including the Wavemaker Pro - it rendered
  // an empty, unusable dropdown. Render by the entity's actual domain.
  _modeRow(pump) {
    const id = pump.entities.mode;
    if (!id) return "";
    const domain = id.split(".")[0];
    const state = this._state(id);
    if (!state) return "";

    if (domain === "select") {
      const options = state.attributes.options || [];
      if (!options.length) return "";  // never draw an empty dropdown
      return `
        <div class="row">
          <label>Wave mode</label>
          <select data-act="mode">
            ${options.map((o) =>
              `<option value="${o}" ${o === state.state ? "selected" : ""}>${modeLabel(o)}</option>`
            ).join("")}
          </select>
        </div>`;
    }
    if (domain === "switch") {
      const on = state.state === "on";
      return `
        <div class="row">
          <label>Mode</label>
          <button class="pw small ${on ? "on" : ""}" data-act="modetoggle">${on ? "On" : "Off"}</button>
        </div>`;
    }
    if (domain === "number") {
      // Fallback only: a uint8 whose choices we could not recover.
      return this._sliderRow("Mode", "mode", state);
    }
    return "";
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
    if (!force && this._built && (this._editSlot.size || this._program.size)) return;
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
    else if (act === "modetoggle") {
      const id = pump.entities.mode;
      this._call("switch", this._state(id)?.state === "on" ? "turn_off" : "turn_on", { entity_id: id });
    }
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
    else if (act === "progopen") this._openProgram(pump);
    else if (act === "progclear") this._clearProgram(pump);
    else if (act === "progwrite") this._writeProgram(pump);
    else if (act === "progcancel") { this._program.delete(pump.did); this._render(true); }
    else if (act.startsWith("prog")) this._editSteps(pump, act, Number(el.dataset.step));
    else if (act === "schedcancel") {
      this._editSlot.delete(pump.did);
      this._render(true);
    }
  }

  _onChange(e) {
    // Belt-and-braces for the slot form's select/time inputs - some
    // browsers only fire "change" (not "input") for these.
    const progEl = e.target.closest("[data-prog]");
    if (progEl) {
      const progPump = this._pumpFor(progEl);
      // Selects, checkbox and time commit on change; sliders and number
      // boxes already updated the preview while being edited.
      if (progPump) this._onProgramInput(progPump, progEl, ["mode", "repeat", "start"].includes(progEl.dataset.prog));
      return;
    }
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
    const progEl = e.target.closest("[data-prog]");
    if (progEl) {
      const progPump = this._pumpFor(progEl);
      if (progPump && !["mode", "repeat"].includes(progEl.dataset.prog)) this._onProgramInput(progPump, progEl, false);
      return;
    }
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
      button:disabled { opacity: 0.45; cursor: default; }
      .schedbtns { margin-top: 10px; }
      .linkbtn { border: none; background: none; padding: 2px 0; font-size: 0.8rem; }
      .timeline { margin: 8px 0 4px; position: relative; }
      .timeline svg { display: block; width: 100%; height: 64px; border-radius: 6px;
        background: var(--secondary-background-color, rgba(127,127,127,0.08)); }
      .timeline .bar { fill: var(--primary); opacity: 0.85; stroke: var(--card-background-color, #fff); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
      .timeline .bar:hover { opacity: 1; }
      .timeline .grid { stroke: var(--divider-color, rgba(127,127,127,0.25)); stroke-width: 2; vector-effect: non-scaling-stroke; }
      .timeline .now { stroke: var(--error-color, #db4437); stroke-width: 1.5; vector-effect: non-scaling-stroke; stroke-dasharray: 3 3; }
      .timeline .none { position: absolute; top: 0; left: 0; right: 0; height: 64px; display: grid; place-items: center;
        font-size: 0.8rem; color: var(--secondary-text-color); pointer-events: none; }
      .ticks { display: flex; justify-content: space-between; font-size: 0.7rem; color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums; margin-top: 2px; }
      .program { margin-top: 6px; }
      .program input[type=time], .program input.num { font: inherit; padding: 3px 6px; border-radius: 6px;
        border: 1px solid var(--divider-color, #ccc); background: var(--card-background-color, #fff); color: var(--primary-text-color); }
      .program input.num { width: 58px; }
      .program .unit { font-size: 0.82rem; color: var(--secondary-text-color); }
      .program .check { min-width: unset; margin-left: auto; display: flex; align-items: center; gap: 4px; color: var(--primary-text-color); }
      .progsum { font-size: 0.8rem; color: var(--secondary-text-color); margin-bottom: 6px; }
      .progerr { font-size: 0.82rem; color: var(--error-color, #db4437); margin: 4px 0 8px; }
      .step { border: 1px solid var(--divider-color, #ddd); border-radius: 8px; padding: 6px 10px 2px; margin: 8px 0; }
      .stephead { display: flex; align-items: center; gap: 6px; }
      .stepno { width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; font-size: 0.75rem;
        font-weight: 600; background: var(--primary); color: var(--text-primary-color, #fff); flex: none; }
      .icon { padding: 1px 8px; font-size: 0.8rem; }
      .slotrow.pending .slottime, .slotrow.pending .slotmode { opacity: 0.55; }
      .slotrow.pending .slottime { text-decoration: line-through; }
      .timeline.pending .bar { opacity: 0.4; }
      .pendingnote { font-size: 0.82rem; color: var(--secondary-text-color); margin: 4px 0 8px; display: flex; align-items: center; }
      .spinner { display: inline-block; width: 10px; height: 10px; margin-right: 6px; vertical-align: -1px; border-radius: 50%;
        border: 2px solid var(--divider-color, #ccc); border-top-color: var(--primary); animation: spin 0.8s linear infinite; }
      @keyframes spin { to { transform: rotate(360deg); } }
      @media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
      .hint { font-size: 0.75rem; color: var(--secondary-text-color); text-align: center; margin-top: 2px; }
      .step .row label { min-width: 60px; }
      @media (max-width: 420px) { .row label { min-width: 70px; } .program .check { margin-left: 0; } .row { flex-wrap: wrap; } }
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
