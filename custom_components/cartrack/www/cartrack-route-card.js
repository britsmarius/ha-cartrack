/*
 * Cartrack route card
 *
 * Draws the route each vehicle drove on a chosen day, read from Home
 * Assistant's recorder history of the Cartrack device trackers, and lists
 * Cartrack's own trips for that day underneath.
 *
 *   type: custom:cartrack-route-card
 *   title: Routes                # optional
 *   height: 420                  # map height in px, optional
 *   show_trips: true             # optional
 *   entities:
 *     - device_tracker.ranger
 *     - entity: device_tracker.mini
 *       name: Mini
 *       color: "#ff9800"
 *
 * Served by the Cartrack integration; no dashboard resource is needed.
 */

const CARD_VERSION = "0.3.0";
const PALETTE = ["#4285f4", "#ea4335", "#f9ab00", "#34a853", "#a142f4", "#ff6d01"];
const MAX_PATH_POINTS = 800;
const MIN_POINT_SPACING_KM = 0.02;
const MAX_DRIVING_GAP_S = 300;
const LIVE_REFRESH_MS = 30000;

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[c]);

const haversineKm = (a, b) => {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371.0088 * Math.asin(Math.min(1, Math.sqrt(h)));
};

// --- Time zone helpers: days follow the Home Assistant server's time zone. ---

const partsIn = (timeZone, ms) => {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  return Object.fromEntries(dtf.formatToParts(new Date(ms)).map((p) => [p.type, p.value]));
};

const offsetMs = (timeZone, ms) => {
  const p = partsIn(timeZone, ms);
  const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute, +p.second);
  return asUtc - Math.floor(ms / 1000) * 1000;
};

const startOfDayMs = (day, timeZone) => {
  const [y, m, d] = day.split("-").map(Number);
  const guess = Date.UTC(y, m - 1, d);
  let ms = guess - offsetMs(timeZone, guess);
  const corrected = guess - offsetMs(timeZone, ms); // DST edge
  if (corrected !== ms) ms = corrected;
  return ms;
};

const shiftDay = (day, delta) => {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + delta)).toISOString().slice(0, 10);
};

const todayIn = (timeZone) => {
  const p = partsIn(timeZone, Date.now());
  return `${p.year}-${p.month}-${p.day}`;
};

class CartrackRouteCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hidden = new Set();
    this._data = {}; // entity_id -> { points, trips, tripsError }
    this._selectedTrip = null; // { entity, start, end }
    this._loadToken = 0;
    this._lastSeen = {};
    this._lastLoad = 0;
  }

  static getStubConfig(hass) {
    const trackers = Object.values(hass.entities || {})
      .filter((e) => e.platform === "cartrack" && e.entity_id.startsWith("device_tracker."))
      .map((e) => e.entity_id);
    return { entities: trackers.slice(0, 4) };
  }

  setConfig(config) {
    if (!config || !Array.isArray(config.entities) || config.entities.length === 0) {
      throw new Error("Add at least one Cartrack device tracker under 'entities'");
    }
    this._config = {
      title: "Routes",
      height: 420,
      show_trips: true,
      ...config,
    };
    this._entities = config.entities.map((item, index) => {
      const entry = typeof item === "string" ? { entity: item } : { ...item };
      if (!entry.entity || !entry.entity.startsWith("device_tracker.")) {
        throw new Error(`Not a device tracker: ${entry.entity}`);
      }
      entry.color = entry.color || PALETTE[index % PALETTE.length];
      return entry;
    });
    // A new config (e.g. from the dashboard editor) rebuilds the card.
    this._built = false;
    this._map = null;
    this._mapPromise = null;
    this._data = {};
    this._selectedTrip = null;
    this._hidden = new Set();
    if (this._hass) this._start();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._map) this._map.hass = hass; // older frontends read hass directly
    if (!this._config) return;
    if (first || !this._built) {
      this._start();
      return;
    }
    this._maybeLiveRefresh();
  }

  _start() {
    if (!this._day) this._day = todayIn(this._tz());
    this._buildSkeleton();
    this._ensureMap().then(() => this._load(true));
  }

  getCardSize() {
    return 9;
  }

  getGridOptions() {
    return { columns: 12, rows: "auto", min_columns: 6 };
  }

  // --- Helpers -------------------------------------------------------------

  _tz() {
    return this._hass?.config?.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  }

  _name(entry) {
    return (
      entry.name ||
      this._hass?.states[entry.entity]?.attributes?.friendly_name ||
      entry.entity.split(".")[1]
    );
  }

  _formatTime(ms) {
    const locale = this._hass?.locale?.language || navigator.language;
    const h12 = this._hass?.locale?.time_format === "12";
    return new Intl.DateTimeFormat(locale, {
      timeZone: this._tz(),
      hour: "2-digit",
      minute: "2-digit",
      hour12: h12,
    }).format(new Date(ms));
  }

  _formatDuration(seconds) {
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} min`;
    return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
  }

  _formatKm(km) {
    return `${km.toFixed(km < 10 ? 1 : 0)} km`;
  }

  // --- Skeleton --------------------------------------------------------------

  _buildSkeleton() {
    if (!this._config || !this._hass || this._built) return;
    this._built = true;
    const height = Number(this._config.height) || 420;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }
        .header { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px;
          padding: 16px 16px 8px; }
        .title { font-size: 1.25rem; font-weight: 500; color: var(--primary-text-color);
          flex: 1 1 auto; }
        .day { display: flex; align-items: center; gap: 4px; }
        .day button, .chip, .trip { font: inherit; cursor: pointer; }
        .icon-btn { background: none; border: none; color: var(--primary-text-color);
          width: 36px; height: 36px; border-radius: 50%; display: inline-flex;
          align-items: center; justify-content: center; }
        .icon-btn:hover:not([disabled]) { background: var(--secondary-background-color); }
        .icon-btn[disabled] { opacity: .35; cursor: default; }
        input[type=date] { font: inherit; color: var(--primary-text-color);
          background: var(--secondary-background-color); border: 1px solid var(--divider-color);
          border-radius: 8px; padding: 6px 8px; color-scheme: light dark; }
        .today { background: none; border: 1px solid var(--divider-color); border-radius: 16px;
          color: var(--primary-text-color); padding: 4px 12px; margin-left: 4px; }
        .chips { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 16px 12px; }
        .chip { display: inline-flex; align-items: center; gap: 6px; border-radius: 16px;
          border: 1px solid var(--divider-color); background: none; padding: 4px 12px;
          color: var(--primary-text-color); }
        .chip .dot, .row .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
        .chip.off { opacity: .45; }
        .chip.off .dot { background: transparent !important; border: 2px solid currentColor; }
        .map { position: relative; height: ${height}px; border-top: 1px solid var(--divider-color);
          border-bottom: 1px solid var(--divider-color); }
        ha-map { position: absolute; inset: 0; }
        .overlay { position: absolute; inset: 0; display: flex; align-items: center;
          justify-content: center; text-align: center; padding: 24px; pointer-events: none;
          color: var(--secondary-text-color); z-index: 1; }
        .overlay span { background: var(--card-background-color); padding: 8px 14px;
          border-radius: 8px; box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.2)); }
        .section { padding: 12px 16px; }
        .section + .section { border-top: 1px solid var(--divider-color); }
        .label { color: var(--secondary-text-color); font-size: .85rem; margin-bottom: 6px; }
        .row { display: flex; align-items: center; gap: 10px; padding: 6px 0;
          color: var(--primary-text-color); }
        .row .name { font-weight: 500; min-width: 90px; }
        .row .stats { color: var(--secondary-text-color); display: flex; flex-wrap: wrap;
          gap: 4px 14px; }
        .row .stats b { color: var(--primary-text-color); font-weight: 500; }
        .trips { display: flex; flex-direction: column; gap: 4px; margin: 2px 0 10px 20px; }
        .trip { text-align: left; background: none; border: 1px solid transparent;
          border-radius: 8px; padding: 6px 8px; color: var(--primary-text-color);
          display: grid; grid-template-columns: auto 1fr auto; gap: 2px 12px; }
        .trip:hover { background: var(--secondary-background-color); }
        .trip.selected { border-color: var(--primary-color);
          background: var(--secondary-background-color); }
        .trip .when { font-variant-numeric: tabular-nums; white-space: nowrap; }
        .trip .where { color: var(--secondary-text-color); overflow: hidden;
          text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
        .trip .dist { white-space: nowrap; font-variant-numeric: tabular-nums; }
        .note { color: var(--secondary-text-color); font-size: .85rem; margin: 2px 0 8px 20px; }
        @media (max-width: 500px) {
          .trip { grid-template-columns: auto auto; }
          .trip .where { grid-column: 1 / -1; grid-row: 2; }
        }
      </style>
      <ha-card>
        <div class="header">
          <div class="title"></div>
          <div class="day">
            <button class="icon-btn prev" title="Previous day" aria-label="Previous day">
              <ha-icon icon="mdi:chevron-left"></ha-icon></button>
            <input type="date" aria-label="Day">
            <button class="icon-btn next" title="Next day" aria-label="Next day">
              <ha-icon icon="mdi:chevron-right"></ha-icon></button>
            <button class="today">Today</button>
          </div>
        </div>
        <div class="chips"></div>
        <div class="map"><div class="overlay"></div></div>
        <div class="summary section"></div>
      </ha-card>`;

    const root = this.shadowRoot;
    root.querySelector(".title").textContent = this._config.title || "";
    if (!this._config.title) root.querySelector(".title").style.display = "none";
    this._input = root.querySelector("input[type=date]");
    this._input.addEventListener("change", () => {
      if (this._input.value) this._setDay(this._input.value);
    });
    root.querySelector(".prev").addEventListener("click", () => this._setDay(shiftDay(this._day, -1)));
    root.querySelector(".next").addEventListener("click", () => this._setDay(shiftDay(this._day, 1)));
    root.querySelector(".today").addEventListener("click", () => this._setDay(todayIn(this._tz())));
    this._overlay = root.querySelector(".overlay");
    this._summary = root.querySelector(".summary");
    this._summary.addEventListener("click", (ev) => this._onTripClick(ev));
    this._renderChips();
    this._renderDay();
  }

  _renderChips() {
    const chips = this.shadowRoot.querySelector(".chips");
    if (!chips) return;
    chips.innerHTML = this._entities
      .map(
        (e) => `<button class="chip ${this._hidden.has(e.entity) ? "off" : ""}"
          data-entity="${escapeHtml(e.entity)}" aria-pressed="${!this._hidden.has(e.entity)}">
          <span class="dot" style="background:${escapeHtml(e.color)}"></span>${escapeHtml(this._name(e))}
        </button>`
      )
      .join("");
    chips.querySelectorAll(".chip").forEach((chip) =>
      chip.addEventListener("click", () => {
        const id = chip.dataset.entity;
        if (this._hidden.has(id)) this._hidden.delete(id);
        else this._hidden.add(id);
        if (this._selectedTrip?.entity === id) this._selectedTrip = null;
        this._renderChips();
        this._renderAll(true);
      })
    );
  }

  _renderDay() {
    if (!this._input || !this._day) return;
    const today = todayIn(this._tz());
    this._input.value = this._day;
    this._input.max = today;
    this.shadowRoot.querySelector(".next").disabled = this._day >= today;
    this.shadowRoot.querySelector(".today").style.visibility =
      this._day === today ? "hidden" : "visible";
  }

  _setDay(day) {
    const today = todayIn(this._tz());
    if (day > today) day = today;
    if (day === this._day) return;
    this._day = day;
    this._selectedTrip = null;
    this._renderDay();
    this._load(true);
  }

  // --- Map -------------------------------------------------------------------

  _ensureMap() {
    if (!this._mapPromise) this._mapPromise = this._createMap();
    return this._mapPromise;
  }

  async _createMap() {
    if (this._map) return;
    if (!customElements.get("ha-map")) {
      // ha-map ships with the built-in map card; creating one loads it.
      try {
        const helpers = await window.loadCardHelpers();
        helpers.createCardElement({ type: "map", entities: ["zone.home"] });
      } catch (err) {
        // fall through to the timeout below
      }
      await Promise.race([
        customElements.whenDefined("ha-map"),
        new Promise((resolve) => setTimeout(resolve, 10000)),
      ]);
    }
    if (!customElements.get("ha-map")) {
      this._setOverlay("The map component could not be loaded.");
      return;
    }
    if (this._map || !this._built) return;
    const map = document.createElement("ha-map");
    map.hass = this._hass;
    map.themeMode = "auto";
    map.zoom = 16;
    map.autoFit = false;
    map.clusterMarkers = false;
    this.shadowRoot.querySelector(".map").prepend(map);
    this._map = map;
  }

  _setOverlay(text) {
    if (!this._overlay) return;
    this._overlay.innerHTML = text ? `<span>${escapeHtml(text)}</span>` : "";
  }

  _fitWhenReady(latLngs, attempt = 0) {
    const map = this._map;
    if (!map) return;
    // Wait until the map has loaded and finished its own initial fit, or ours
    // would be overwritten by it.
    const ready =
      map._loaded === undefined ? Boolean(map.leafletMap || map._engine) : map._loaded === true;
    if (!ready) {
      if (attempt < 60) setTimeout(() => this._fitWhenReady(latLngs, attempt + 1), 150);
      return;
    }
    Promise.resolve(map.updateComplete).then(() => {
      if (map !== this._map) return;
      if (latLngs.length && typeof map.fitBounds === "function") {
        map.fitBounds(latLngs, { zoom: 16, pad: 0.15 });
      } else if (typeof map.fitMap === "function") {
        map.fitMap();
      }
    });
  }

  // --- Data ------------------------------------------------------------------

  _maybeLiveRefresh() {
    if (!this._day || this._day !== todayIn(this._tz())) return;
    let changed = false;
    for (const e of this._entities) {
      const stamp = this._hass.states[e.entity]?.last_updated;
      if (stamp && stamp !== this._lastSeen[e.entity]) {
        this._lastSeen[e.entity] = stamp;
        changed = true;
      }
    }
    if (changed && Date.now() - this._lastLoad > LIVE_REFRESH_MS) this._load(false);
  }

  async _load(refit) {
    if (!this._hass || !this._day || !this._built) return;
    const token = ++this._loadToken;
    this._lastLoad = Date.now();
    const tz = this._tz();
    const start = startOfDayMs(this._day, tz);
    const end = startOfDayMs(shiftDay(this._day, 1), tz);
    const ids = this._entities.map((e) => e.entity);
    if (refit) this._setOverlay("Loading…");

    const historyPromise = this._hass
      .callWS({
        type: "history/history_during_period",
        start_time: new Date(start).toISOString(),
        end_time: new Date(Math.min(end, Date.now())).toISOString(),
        entity_ids: ids,
        include_start_time_state: false,
        significant_changes_only: false,
        minimal_response: false,
        no_attributes: false,
      })
      .catch((err) => ({ __error: err }));

    const day = this._day;
    // Long-term history from VictoriaMetrics, when the account has it set up.
    const longTermPromises = ids.map((id) =>
      this._hass
        .callWS({ type: "cartrack/route", entity_id: id, date: day })
        .then((res) => res.points || [])
        .catch(() => null)
    );
    const tripPromises = this._config.show_trips
      ? ids.map((id) =>
          this._hass
            .callWS({ type: "cartrack/trips", entity_id: id, date: day })
            .then((res) => ({ trips: res.trips || [] }))
            .catch((err) => ({ error: err?.code === "forbidden" ? "forbidden" : err?.message || "error" }))
        )
      : ids.map(() => Promise.resolve({ trips: [] }));

    const [history, longTerm, trips] = await Promise.all([
      historyPromise,
      Promise.all(longTermPromises),
      Promise.all(tripPromises),
    ]);
    if (token !== this._loadToken) return; // a newer request won

    const usable = longTerm.some((points) => points && points.length);
    if (history?.__error && !usable) {
      this._data = {};
      this._setOverlay(`Could not read history: ${history.__error.message || history.__error}`);
      this._renderSummary();
      return;
    }

    const data = {};
    ids.forEach((id, i) => {
      const fromRecorder = history?.__error ? [] : this._pointsFromHistory(history?.[id] || []);
      const fromLongTerm = this._pointsFromRows(longTerm[i]);
      // Prefer whichever source has more of the day (VictoriaMetrics, except
      // for the last minute or two it may not have received yet).
      const useLongTerm = fromLongTerm.length && fromLongTerm.length >= fromRecorder.length * 0.8;
      data[id] = {
        points: useLongTerm ? fromLongTerm : fromRecorder,
        source: useLongTerm ? "VictoriaMetrics" : "recorder",
        trips: trips[i].trips || [],
        tripsError: trips[i].error,
      };
    });
    this._data = data;
    this._renderAll(refit);
  }

  _pointsFromRows(rows) {
    if (!Array.isArray(rows)) return [];
    const points = [];
    for (const row of rows) {
      const [t, lat, lon, speed, odometer] = row;
      if (!Number.isFinite(t) || !Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      points.push({
        t,
        lat,
        lon,
        speed: Number.isFinite(speed) ? speed : null,
        odometer: Number.isFinite(odometer) ? odometer : null,
      });
    }
    return points.sort((x, y) => x.t - y.t);
  }

  _pointsFromHistory(states) {
    const points = [];
    for (const s of states) {
      const a = s.a || s.attributes || {};
      const lat = Number(a.latitude);
      const lon = Number(a.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      let t = Date.parse(a.last_update);
      if (!Number.isFinite(t)) t = ((s.lu ?? s.lc) || 0) * 1000;
      const prev = points[points.length - 1];
      if (prev && prev.lat === lat && prev.lon === lon) continue;
      points.push({
        t,
        lat,
        lon,
        speed: Number.isFinite(Number(a.speed)) ? Number(a.speed) : null,
        odometer: Number.isFinite(Number(a.odometer)) ? Number(a.odometer) : null,
      });
    }
    return points.sort((x, y) => x.t - y.t);
  }

  _summarize(points) {
    let gpsKm = 0;
    let driving = 0;
    let first = null;
    let last = null;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1];
      const b = points[i];
      const step = haversineKm(a, b);
      const moved = step > MIN_POINT_SPACING_KM || (a.speed || 0) > 0 || (b.speed || 0) > 0;
      if (!moved) continue;
      gpsKm += step;
      const gap = (b.t - a.t) / 1000;
      if (gap > 0 && gap <= MAX_DRIVING_GAP_S) driving += gap;
      if (first === null) first = a.t;
      last = b.t;
    }
    const odos = points.map((p) => p.odometer).filter((v) => v !== null);
    let km = gpsKm;
    if (odos.length >= 2) {
      const odoKm = (Math.max(...odos) - Math.min(...odos)) / 1000;
      if (odoKm >= 0 && odoKm < 3000) km = odoKm;
    }
    return { km, driving, first, last };
  }

  _thin(points) {
    if (points.length <= 2) return points;
    const kept = [points[0]];
    for (let i = 1; i < points.length - 1; i++) {
      if (haversineKm(kept[kept.length - 1], points[i]) >= MIN_POINT_SPACING_KM) kept.push(points[i]);
    }
    kept.push(points[points.length - 1]);
    if (kept.length <= MAX_PATH_POINTS) return kept;
    const step = Math.ceil(kept.length / MAX_PATH_POINTS);
    return kept.filter((_, i) => i % step === 0 || i === kept.length - 1);
  }

  // --- Rendering -------------------------------------------------------------

  _visiblePoints(entry) {
    const points = this._data[entry.entity]?.points || [];
    const sel = this._selectedTrip;
    if (!sel) return points;
    if (sel.entity !== entry.entity) return [];
    return points.filter((p) => p.t >= sel.start - 60000 && p.t <= sel.end + 60000);
  }

  _renderAll(refit) {
    this._renderMap(refit);
    this._renderSummary();
  }

  _renderMap(refit) {
    if (!this._map) return;
    const visible = this._entities.filter((e) => !this._hidden.has(e.entity));
    const paths = [];
    const latLngs = [];
    for (const e of visible) {
      const points = this._thin(this._visiblePoints(e));
      if (points.length < 2) continue;
      paths.push({
        points: points.map((p) => ({ point: [p.lat, p.lon], timestamp: new Date(p.t) })),
        color: e.color,
        name: this._name(e),
      });
      points.forEach((p) => latLngs.push([p.lat, p.lon]));
    }
    const isToday = this._day === todayIn(this._tz());
    this._map.entities =
      isToday && !this._selectedTrip
        ? visible
            .filter((e) => this._hass.states[e.entity])
            .map((e) => ({ entity_id: e.entity, color: e.color, name: this._name(e) }))
        : [];
    this._map.paths = paths;

    if (!paths.length) {
      const anyData = Object.values(this._data).some((d) => d.points.length);
      this._setOverlay(
        this._selectedTrip
          ? "No recorded positions for this trip."
          : anyData
            ? "No movement for the selected vehicles on this day."
            : "No positions recorded for this day."
      );
    } else {
      this._setOverlay("");
    }
    if (refit) {
      if (!latLngs.length && isToday) {
        visible.forEach((e) => {
          const a = this._hass.states[e.entity]?.attributes;
          if (a && Number.isFinite(a.latitude)) latLngs.push([a.latitude, a.longitude]);
        });
      }
      this._fitWhenReady(latLngs);
    }
  }

  _renderSummary() {
    if (!this._summary) return;
    const rows = this._entities
      .filter((e) => !this._hidden.has(e.entity))
      .map((e) => {
        const d = this._data[e.entity];
        const name = escapeHtml(this._name(e));
        const dot = `<span class="dot" style="background:${escapeHtml(e.color)}"></span>`;
        if (!d) return `<div class="row">${dot}<span class="name">${name}</span></div>`;
        const s = this._summarize(d.points);
        const stats = s.first
          ? `<span><b>${this._formatKm(s.km)}</b></span>
             <span>driving <b>${this._formatDuration(s.driving)}</b></span>
             <span>${this._formatTime(s.first)} – ${this._formatTime(s.last)}</span>`
          : `<span>${d.points.length ? "Parked all day" : "No positions recorded"}</span>`;
        const source = d.source ? ` title="Positions from ${escapeHtml(d.source)}"` : "";
        return `<div class="row">${dot}<span class="name"${source}>${name}</span>
            <span class="stats">${stats}</span></div>${this._renderTrips(e, d)}`;
      })
      .join("");
    this._summary.innerHTML = `<div class="label">${escapeHtml(this._dayLabel())}</div>${rows}`;
  }

  _renderTrips(entry, d) {
    if (!this._config.show_trips) return "";
    if (d.tripsError === "forbidden") {
      return `<div class="note">Trips are not available with these Cartrack API credentials.</div>`;
    }
    if (d.tripsError) return `<div class="note">Trips could not be loaded.</div>`;
    if (!d.trips.length) return "";
    const items = d.trips
      .map((trip) => {
        const start = Date.parse(trip.start);
        const end = Date.parse(trip.end);
        if (!Number.isFinite(start)) return "";
        const selected =
          this._selectedTrip?.entity === entry.entity && this._selectedTrip.start === start;
        const where = [trip.start_location, trip.end_location]
          .filter(Boolean)
          .map(escapeHtml)
          .join(" → ");
        let km = trip.distance_km;
        const hours = Number.isFinite(end) ? (end - start) / 3600000 : 0;
        // Some Cartrack accounts send metres; a km reading that implies
        // more than 250 km/h on average must be metres.
        if (Number.isFinite(km) && hours > 0 && km / hours > 250) km /= 1000;
        const dist = Number.isFinite(km) ? this._formatKm(km) : "";
        return `<button class="trip ${selected ? "selected" : ""}" data-entity="${escapeHtml(entry.entity)}"
            data-start="${start}" data-end="${Number.isFinite(end) ? end : start}"
            title="${selected ? "Show the whole day" : "Show this trip on the map"}">
            <span class="when">${this._formatTime(start)}${Number.isFinite(end) ? ` – ${this._formatTime(end)}` : ""}</span>
            <span class="where">${where}</span>
            <span class="dist">${dist}</span>
          </button>`;
      })
      .join("");
    return `<div class="trips">${items}</div>`;
  }

  _dayLabel() {
    const [y, m, d] = this._day.split("-").map(Number);
    const locale = this._hass?.locale?.language || navigator.language;
    const label = new Intl.DateTimeFormat(locale, {
      weekday: "long",
      day: "numeric",
      month: "long",
      timeZone: "UTC",
    }).format(new Date(Date.UTC(y, m - 1, d)));
    return this._selectedTrip ? `${label} · one trip shown (tap it again for the whole day)` : label;
  }

  _onTripClick(ev) {
    const button = ev.target.closest?.(".trip");
    if (!button) return;
    const start = Number(button.dataset.start);
    const entity = button.dataset.entity;
    const same = this._selectedTrip?.entity === entity && this._selectedTrip.start === start;
    this._selectedTrip = same ? null : { entity, start, end: Number(button.dataset.end) };
    this._renderAll(true);
  }
}

if (!customElements.get("cartrack-route-card")) {
  customElements.define("cartrack-route-card", CartrackRouteCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "cartrack-route-card",
    name: "Cartrack routes",
    description: "Daily vehicle routes from Home Assistant history, with Cartrack trips.",
    documentationURL: "https://github.com/britsmarius/ha-cartrack",
  });
  console.info(`%c CARTRACK-ROUTE-CARD %c ${CARD_VERSION} `, "color:#fff;background:#4285f4", "");
}
