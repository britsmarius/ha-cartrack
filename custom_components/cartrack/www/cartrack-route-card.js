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
 * The same file also defines custom:cartrack-fleet-card (see below).
 * Served by the Cartrack integration; no dashboard resource is needed.
 */

const CARD_VERSION = "0.4.0";
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

// Shared by both cards: entity config, formatting, the map, history and trips.
class CartrackBase extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  static getStubConfig(hass) {
    const trackers = Object.values(hass.entities || {})
      .filter((e) => e.platform === "cartrack" && e.entity_id.startsWith("device_tracker."))
      .map((e) => e.entity_id);
    return { entities: trackers.slice(0, 6) };
  }

  _parseEntities(config) {
    if (!config || !Array.isArray(config.entities) || config.entities.length === 0) {
      throw new Error("Add at least one Cartrack device tracker under 'entities'");
    }
    return config.entities.map((item, index) => {
      const entry = typeof item === "string" ? { entity: item } : { ...item };
      if (!entry.entity || !entry.entity.startsWith("device_tracker.")) {
        throw new Error(`Not a device tracker: ${entry.entity}`);
      }
      entry.color = entry.color || PALETTE[index % PALETTE.length];
      return entry;
    });
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

  // --- History and trips -----------------------------------------------------

  async _fetchDay(day, ids, withTrips = true) {
    // A day's points (VictoriaMetrics when configured, else the recorder)
    // and Cartrack trips for each tracker.
    const tz = this._tz();
    const start = startOfDayMs(day, tz);
    const end = startOfDayMs(shiftDay(day, 1), tz);
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
    const longTermPromises = ids.map((id) =>
      this._hass
        .callWS({ type: "cartrack/route", entity_id: id, date: day })
        .then((res) => res.points || [])
        .catch(() => null)
    );
    const tripPromises = withTrips
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
    const usable = longTerm.some((points) => points && points.length);
    if (history?.__error && !usable) {
      return { error: history.__error.message || String(history.__error) };
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
    return { data };
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
    return this._collapseStops(points.sort((x, y) => x.t - y.t));
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
      const point = {
        t,
        lat,
        lon,
        speed: Number.isFinite(Number(a.speed)) ? Number(a.speed) : null,
        odometer: Number.isFinite(Number(a.odometer)) ? Number(a.odometer) : null,
      };
      points.push(point);
    }
    return this._collapseStops(points.sort((x, y) => x.t - y.t));
  }

  _collapseStops(points) {
    // While parked, keep only the first and last reading of each stay, so the
    // time a car left is known without drawing hundreds of identical points.
    const out = [];
    for (const p of points) {
      const last = out[out.length - 1];
      const before = out[out.length - 2];
      if (last && last.lat === p.lat && last.lon === p.lon) {
        if (before && before.lat === p.lat && before.lon === p.lon) out[out.length - 1] = p;
        else out.push(p);
        continue;
      }
      out.push(p);
    }
    return out;
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
      // After a long silence the departure time is unknown; start at b.
      if (first === null) first = gap > MAX_DRIVING_GAP_S ? b.t : a.t;
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

  _drivesFromPoints(points) {
    // Group movement into drives, split by stops longer than MAX_DRIVING_GAP_S.
    const drives = [];
    let current = null;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1];
      const b = points[i];
      const step = haversineKm(a, b);
      const moved = step > MIN_POINT_SPACING_KM || (b.speed || 0) > 0;
      if (!moved) continue;
      if (current && (a.t - current.end) / 1000 <= MAX_DRIVING_GAP_S) {
        current.end = b.t;
        current.gpsKm += step;
        current.points.push(b);
      } else {
        const start = (b.t - a.t) / 1000 > MAX_DRIVING_GAP_S ? b.t : a.t;
        current = { start, end: b.t, gpsKm: step, points: [a, b] };
        drives.push(current);
      }
    }
    return drives
      .map((drive) => {
        const odos = drive.points.map((p) => p.odometer).filter((v) => v !== null);
        let km = drive.gpsKm;
        if (odos.length >= 2) {
          const odoKm = (Math.max(...odos) - Math.min(...odos)) / 1000;
          if (odoKm > 0 && odoKm < 2000) km = odoKm;
        }
        return { start: drive.start, end: drive.end, km };
      })
      .filter((drive) => drive.km >= 0.2);
  }

  _tripItems(entry, d) {
    const items = [];
    for (const trip of d.trips || []) {
      const start = Date.parse(trip.start);
      const end = Date.parse(trip.end);
      if (!Number.isFinite(start)) continue;
      let km = trip.distance_km;
      const hours = Number.isFinite(end) ? (end - start) / 3600000 : 0;
      // Some Cartrack accounts send metres; a km reading that implies
      // more than 250 km/h on average must be metres.
      if (Number.isFinite(km) && hours > 0 && km / hours > 250) km /= 1000;
      items.push({
        start,
        end: Number.isFinite(end) ? end : start,
        km,
        where: [trip.start_location, trip.end_location].filter(Boolean),
        pending: false,
      });
    }
    // Drives in the recorded route that Cartrack has not listed (yet): a trip
    // only appears there once the tracker reports the ignition off.
    const margin = 120000;
    for (const drive of this._drivesFromPoints(d.points || [])) {
      const covered = items.some(
        (trip) => !trip.pending && drive.start <= trip.end + margin && drive.end >= trip.start - margin
      );
      if (!covered) items.push({ ...drive, where: [], pending: true });
    }
    return items.sort((x, y) => x.start - y.start);
  }

}

class CartrackRouteCard extends CartrackBase {
  constructor() {
    super();
    this._hidden = new Set();
    this._data = {}; // entity_id -> { points, trips, tripsError }
    this._selectedTrip = null; // { entity, start, end }
    this._loadToken = 0;
    this._lastSeen = {};
    this._lastLoad = 0;
  }

  setConfig(config) {
    const entities = this._parseEntities(config);
    this._config = {
      title: "Routes",
      height: 420,
      show_trips: true,
      ...config,
    };
    this._entities = entities;
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
        .trip.pending .where em { font-style: italic; }
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
    if (refit) this._setOverlay("Loading…");
    const result = await this._fetchDay(
      this._day,
      this._entities.map((e) => e.entity),
      this._config.show_trips
    );
    if (token !== this._loadToken) return; // a newer request won
    if (result.error) {
      this._data = {};
      this._setOverlay(`Could not read history: ${result.error}`);
      this._renderSummary();
      return;
    }
    this._data = result.data;
    this._renderAll(refit);
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
    let note = "";
    if (d.tripsError === "forbidden") {
      note = `<div class="note">Cartrack trips are not available with these API credentials; drives below come from the recorded route.</div>`;
    } else if (d.tripsError) {
      note = `<div class="note">Cartrack trips could not be loaded; drives below come from the recorded route.</div>`;
    }
    const items = this._tripItems(entry, d);
    if (!items.length) return note;
    const rows = items
      .map((trip) => {
        const selected =
          this._selectedTrip?.entity === entry.entity && this._selectedTrip.start === trip.start;
        const where = trip.pending
          ? `<em>Not yet reported by Cartrack</em>`
          : trip.where.map(escapeHtml).join(" → ");
        const dist = Number.isFinite(trip.km) ? this._formatKm(trip.km) : "";
        return `<button class="trip ${selected ? "selected" : ""} ${trip.pending ? "pending" : ""}"
            data-entity="${escapeHtml(entry.entity)}" data-start="${trip.start}" data-end="${trip.end}"
            title="${selected ? "Show the whole day" : "Show this trip on the map"}">
            <span class="when">${this._formatTime(trip.start)} – ${this._formatTime(trip.end)}</span>
            <span class="where">${where}</span>
            <span class="dist">${dist}</span>
          </button>`;
      })
      .join("");
    return `${note}<div class="trips">${rows}</div>`;
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


// ---------------------------------------------------------------------------
// Fleet card: vehicle list beside a full map, with a details popup and a
// trips tab, in the style of a fleet tracking app.
//
//   type: custom:cartrack-fleet-card
//   height: 760          # px, optional; fills the screen when left out
//   offline_hours: 168   # optional; no report for this long shows "Offline"
//   entities:
//     - device_tracker.ranger
//     - entity: device_tracker.mini
//       name: Mini
//       color: "#ea4335"
//       icon: mdi:car-hatchback
// ---------------------------------------------------------------------------

const STALE_AFTER_S = 180;
const SIBLING_SUFFIX = {
  supply_voltage: "_battery_voltage",
  moving: "_moving",
};

class CartrackFleetCard extends CartrackBase {
  constructor() {
    super();
    this._tab = "map";
    this._selected = null;
    this._follow = false;
    this._filter = "";
    this._dayData = {};
    this._selectedTrip = null;
    this._loadToken = 0;
    this._watched = {};
    this._lastLoad = 0;
  }

  setConfig(config) {
    const entities = this._parseEntities(config);
    this._config = { offline_hours: 168, ...config };
    this._entities = entities;
    this._built = false;
    this._map = null;
    this._mapPromise = null;
    this._dayData = {};
    this._selected = null;
    this._selectedTrip = null;
    this._siblings = null;
    if (this._hass) this._start();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._map) this._map.hass = hass;
    if (!this._config) return;
    if (first || !this._built) {
      this._start();
      return;
    }
    this._onStates();
  }

  getCardSize() {
    return 12;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  _start() {
    if (!this._day) this._day = todayIn(this._tz());
    this._buildSkeleton();
    this._snapshotWatched();
    this._renderList();
    this._ensureMap().then(() => this._renderMap(true));
  }

  // --- Vehicle state ---------------------------------------------------------

  _sibling(entry, key) {
    if (!this._siblings) this._siblings = {};
    const cacheKey = `${entry.entity}|${key}`;
    if (cacheKey in this._siblings) return this._siblings[cacheKey];
    const registry = this._hass.entities || {};
    const device = registry[entry.entity]?.device_id;
    let found = null;
    if (device) {
      const mates = Object.values(registry).filter(
        (e) => e.device_id === device && e.entity_id !== entry.entity
      );
      found =
        mates.find((e) => e.translation_key === key)?.entity_id ||
        mates.find((e) => SIBLING_SUFFIX[key] && e.entity_id.endsWith(SIBLING_SUFFIX[key]))
          ?.entity_id ||
        null;
    }
    this._siblings[cacheKey] = found;
    return found;
  }

  _info(entry) {
    const state = this._hass.states[entry.entity];
    if (!state) return null;
    const a = state.attributes || {};
    let updated = Date.parse(a.last_update);
    if (!Number.isFinite(updated)) updated = Date.parse(state.last_updated);
    const voltageId = this._sibling(entry, "supply_voltage");
    const voltage = voltageId ? Number(this._hass.states[voltageId]?.state) : NaN;
    const movingId = this._sibling(entry, "moving");
    const moving = movingId
      ? this._hass.states[movingId]?.state === "on"
      : Number(a.speed) > 0;
    let zone = null;
    if (state.state === "home") zone = "Home";
    else if (state.state && !["not_home", "unknown", "unavailable"].includes(state.state)) {
      zone = state.state;
    }
    return {
      lat: Number(a.latitude),
      lon: Number(a.longitude),
      address: a.address || null,
      speed: Number(a.speed),
      ignition: a.ignition === true || a.ignition === "on",
      odometer: Number(a.odometer),
      updated,
      voltage: Number.isFinite(voltage) ? voltage : null,
      moving,
      zone,
      available: state.state !== "unavailable",
    };
  }

  _relative(ms) {
    const seconds = Math.max(0, (Date.now() - ms) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400 * 2) return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} d ago`;
  }

  _status(info) {
    if (!info || !info.available) return { text: "Unavailable", cls: "bad" };
    if (!Number.isFinite(info.updated)) return { text: "No data", cls: "muted" };
    const age = (Date.now() - info.updated) / 1000;
    const ago = this._relative(info.updated);
    if (age > this._config.offline_hours * 3600) return { text: `Offline · ${ago}`, cls: "bad" };
    if (info.moving) {
      const speed = Number.isFinite(info.speed) ? ` · ${Math.round(info.speed)} km/h` : "";
      return { text: `Driving${speed}`, cls: "good" };
    }
    if (info.ignition) {
      return age > STALE_AFTER_S
        ? { text: `Ignition on · no signal ${ago}`, cls: "warn" }
        : { text: "Ignition on", cls: "good" };
    }
    return { text: `Parked${info.zone ? ` at ${info.zone}` : ""} · ${ago}`, cls: "muted" };
  }

  _batteryClass(volts) {
    if (volts === null) return "muted";
    if (volts >= 12.4) return "good";
    if (volts >= 12.0) return "warn";
    return "bad";
  }

  _visibleEntities() {
    const filter = this._filter.trim().toLowerCase();
    if (!filter) return this._entities;
    return this._entities.filter(
      (e) => this._name(e).toLowerCase().includes(filter) || e.entity.includes(filter)
    );
  }

  _entry(entityId) {
    return this._entities.find((e) => e.entity === entityId) || null;
  }

  _snapshotWatched() {
    const ids = [];
    for (const e of this._entities) {
      ids.push(e.entity, this._sibling(e, "supply_voltage"), this._sibling(e, "moving"));
    }
    const watched = {};
    let changed = false;
    for (const id of ids.filter(Boolean)) {
      watched[id] = this._hass.states[id];
      if (watched[id] !== this._watched[id]) changed = true;
    }
    this._watched = watched;
    return changed;
  }

  _onStates() {
    const oldPosition = this._selected ? this._watched[this._selected]?.attributes : null;
    if (!this._snapshotWatched()) return;
    this._renderList();
    this._renderPopup();
    if (this._selected && this._follow) {
      const a = this._hass.states[this._selected]?.attributes;
      if (a && (a.latitude !== oldPosition?.latitude || a.longitude !== oldPosition?.longitude)) {
        this._fitWhenReady([[a.latitude, a.longitude]]);
      }
    }
    // Keep today's trips view current while cars drive.
    if (
      this._tab === "trips" &&
      this._day === todayIn(this._tz()) &&
      Date.now() - this._lastLoad > LIVE_REFRESH_MS
    ) {
      this._loadDay(false);
    }
  }

  // --- Skeleton --------------------------------------------------------------

  _buildSkeleton() {
    if (!this._config || !this._hass || this._built) return;
    this._built = true;
    const height = Number(this._config.height)
      ? `${Number(this._config.height)}px`
      : "calc(100vh - var(--header-height, 56px) - 16px)";
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }
        button { font: inherit; cursor: pointer; }
        .fleet { display: grid; grid-template-columns: minmax(260px, 340px) 1fr;
          height: ${height}; min-height: 480px; }
        .side { display: flex; flex-direction: column; min-height: 0; min-width: 0;
          border-right: 1px solid var(--divider-color); background: var(--card-background-color); }
        .search { display: flex; align-items: center; gap: 8px; margin: 12px; padding: 0 12px;
          border-radius: 8px; background: var(--secondary-background-color);
          color: var(--secondary-text-color); }
        .search input { flex: 1; min-width: 0; border: none; background: none; font: inherit;
          color: var(--primary-text-color); padding: 10px 0; outline: none; }
        .panel { flex: 1; overflow: auto; min-height: 0; }
        .panel[hidden] { display: none; }
        .vrow { display: flex; align-items: center; gap: 12px; width: 100%; padding: 12px 16px;
          border: none; border-left: 3px solid transparent; background: none; text-align: left;
          color: var(--primary-text-color); }
        .vrow:hover { background: var(--secondary-background-color); }
        .vrow.sel { background: var(--secondary-background-color);
          border-left-color: var(--primary-color); }
        .avatar { width: 40px; height: 40px; border-radius: 50%; flex: none; display: flex;
          align-items: center; justify-content: center; color: #fff; }
        .vtext { min-width: 0; flex: 1; }
        .vname { font-weight: 500; }
        .vstatus { font-size: .85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .vicons { display: flex; gap: 6px; align-items: center; flex: none; }
        .vicons ha-icon { --mdc-icon-size: 20px; }
        .good { color: var(--success-color, #43a047); }
        .warn { color: var(--warning-color, #ffa600); }
        .bad { color: var(--error-color, #db4437); }
        .muted { color: var(--secondary-text-color); }
        .empty { padding: 16px; color: var(--secondary-text-color); }
        .tabs { display: flex; border-top: 1px solid var(--divider-color); }
        .tabs button { flex: 1; display: flex; flex-direction: column; align-items: center;
          gap: 2px; padding: 8px 0; background: none; border: none; font-size: .8rem;
          color: var(--secondary-text-color); }
        .tabs button.active { color: var(--primary-color); }
        .map { position: relative; min-height: 0; min-width: 0; }
        ha-map { position: absolute; inset: 0; }
        .overlay { position: absolute; inset: 0; display: flex; align-items: center;
          justify-content: center; text-align: center; padding: 24px; pointer-events: none;
          color: var(--secondary-text-color); z-index: 1; }
        .overlay span { background: var(--card-background-color); padding: 8px 14px;
          border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.25); }
        .popup { position: absolute; right: 16px; bottom: 16px; width: 300px; z-index: 2;
          background: var(--card-background-color); color: var(--primary-text-color);
          border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,.35); padding: 12px 16px 8px; }
        .popup[hidden] { display: none; }
        .pop-head { display: flex; align-items: center; gap: 8px; font-weight: 500; }
        .pop-head .x { margin-left: auto; }
        .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
        .popup dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px;
          margin: 10px 0; font-size: .9rem; }
        .popup dt { color: var(--secondary-text-color); }
        .popup dd { margin: 0; overflow-wrap: anywhere; }
        .actions { display: flex; justify-content: space-around;
          border-top: 1px solid var(--divider-color); padding-top: 6px; }
        .icon-btn { background: none; border: none; color: var(--primary-text-color);
          width: 40px; height: 40px; border-radius: 50%; display: inline-flex;
          align-items: center; justify-content: center; }
        .icon-btn:hover:not([disabled]) { background: var(--secondary-background-color); }
        .icon-btn[disabled] { opacity: .35; cursor: default; }
        .icon-btn.on { color: var(--primary-color); }
        .day { display: flex; align-items: center; gap: 4px; padding: 0 12px 8px; }
        input[type=date] { flex: 1; min-width: 0; font: inherit; color: var(--primary-text-color);
          background: var(--secondary-background-color); border: 1px solid var(--divider-color);
          border-radius: 8px; padding: 6px 8px; color-scheme: light dark; }
        .vsum { display: flex; align-items: center; gap: 8px; padding: 10px 16px 4px;
          font-weight: 500; }
        .vsum .stats { margin-left: auto; font-weight: 400; color: var(--secondary-text-color);
          font-size: .85rem; }
        .trips { display: flex; flex-direction: column; gap: 2px; padding: 0 8px 8px; }
        .trip { text-align: left; background: none; border: 1px solid transparent;
          border-radius: 8px; padding: 6px 8px; color: var(--primary-text-color);
          display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; }
        .trip:hover { background: var(--secondary-background-color); }
        .trip.selected { border-color: var(--primary-color);
          background: var(--secondary-background-color); }
        .trip .when { font-variant-numeric: tabular-nums; white-space: nowrap; }
        .trip .dist { text-align: right; font-variant-numeric: tabular-nums; }
        .trip .where { grid-column: 1 / -1; color: var(--secondary-text-color); font-size: .85rem;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .note { color: var(--secondary-text-color); font-size: .85rem; padding: 0 16px 6px; }
        @media (max-width: 700px) {
          .fleet { grid-template-columns: 1fr; grid-template-rows: minmax(0, 45%) 1fr; }
          .side { border-right: none; border-bottom: 1px solid var(--divider-color); }
          .popup { left: 8px; right: 8px; bottom: 8px; width: auto; }
        }
      </style>
      <ha-card>
        <div class="fleet">
          <aside class="side">
            <label class="search"><ha-icon icon="mdi:magnify"></ha-icon>
              <input type="search" placeholder="Search vehicles" aria-label="Search vehicles"></label>
            <div class="panel list" role="list"></div>
            <div class="panel trips-panel" hidden>
              <div class="day">
                <button class="icon-btn prev" title="Previous day" aria-label="Previous day">
                  <ha-icon icon="mdi:chevron-left"></ha-icon></button>
                <input type="date" aria-label="Day">
                <button class="icon-btn next" title="Next day" aria-label="Next day">
                  <ha-icon icon="mdi:chevron-right"></ha-icon></button>
              </div>
              <div class="trips-body"></div>
            </div>
            <nav class="tabs">
              <button data-tab="map" class="active"><ha-icon icon="mdi:map"></ha-icon><span>Map</span></button>
              <button data-tab="trips"><ha-icon icon="mdi:map-marker-path"></ha-icon><span>Trips</span></button>
            </nav>
          </aside>
          <div class="map"><div class="overlay"></div><div class="popup" hidden></div></div>
        </div>
      </ha-card>`;

    const root = this.shadowRoot;
    this._overlay = root.querySelector(".overlay");
    this._list = root.querySelector(".list");
    this._tripsPanel = root.querySelector(".trips-panel");
    this._tripsBody = root.querySelector(".trips-body");
    this._popup = root.querySelector(".popup");
    this._input = root.querySelector("input[type=date]");

    root.querySelector(".search input").addEventListener("input", (ev) => {
      this._filter = ev.target.value;
      this._renderList();
      if (this._tab === "trips") this._renderTrips();
    });
    this._list.addEventListener("click", (ev) => {
      const row = ev.target.closest?.(".vrow");
      if (row) this._select(row.dataset.entity === this._selected ? null : row.dataset.entity);
    });
    root.querySelectorAll(".tabs button").forEach((button) =>
      button.addEventListener("click", () => this._setTab(button.dataset.tab))
    );
    this._input.addEventListener("change", () => {
      if (this._input.value) this._setDay(this._input.value);
    });
    root.querySelector(".prev").addEventListener("click", () => this._setDay(shiftDay(this._day, -1)));
    root.querySelector(".next").addEventListener("click", () => this._setDay(shiftDay(this._day, 1)));
    this._tripsBody.addEventListener("click", (ev) => this._onTripClick(ev));
    this._popup.addEventListener("click", (ev) => this._onPopupClick(ev));
    this._renderDay();
  }

  // --- Interaction -----------------------------------------------------------

  _select(entityId) {
    this._selected = entityId;
    this._follow = false;
    this._selectedTrip = null;
    this._renderList();
    this._renderPopup();
    if (this._tab === "trips") this._renderTrips();
    this._renderMap(true);
  }

  _setTab(tab) {
    if (tab === this._tab) return;
    this._tab = tab;
    this._selectedTrip = null;
    this.shadowRoot.querySelectorAll(".tabs button").forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === tab)
    );
    this._list.hidden = tab !== "map";
    this._tripsPanel.hidden = tab !== "trips";
    if (tab === "trips") this._loadDay(true);
    else this._renderMap(true);
  }

  _setDay(day) {
    const today = todayIn(this._tz());
    if (day > today) day = today;
    if (day === this._day) return;
    this._day = day;
    this._selectedTrip = null;
    this._renderDay();
    this._loadDay(true);
  }

  _renderDay() {
    if (!this._input) return;
    const today = todayIn(this._tz());
    this._input.value = this._day;
    this._input.max = today;
    this.shadowRoot.querySelector(".next").disabled = this._day >= today;
  }

  _onPopupClick(ev) {
    const button = ev.target.closest?.("button");
    if (!button || !this._selected) return;
    const action = button.dataset.act;
    const info = this._info(this._entry(this._selected));
    if (action === "close") {
      this._select(null);
    } else if (action === "route") {
      this._day = todayIn(this._tz());
      this._renderDay();
      if (this._tab === "trips") this._loadDay(true);
      else this._setTab("trips");
    } else if (action === "follow") {
      this._follow = !this._follow;
      this._renderPopup();
      if (this._follow && info) this._fitWhenReady([[info.lat, info.lon]]);
    } else if (action === "directions" && info && Number.isFinite(info.lat)) {
      window.open(
        `https://www.google.com/maps/search/?api=1&query=${info.lat},${info.lon}`,
        "_blank",
        "noopener"
      );
    } else if (action === "info") {
      this.dispatchEvent(
        new CustomEvent("hass-more-info", {
          detail: { entityId: this._selected },
          bubbles: true,
          composed: true,
        })
      );
    }
  }

  _onTripClick(ev) {
    const button = ev.target.closest?.(".trip");
    if (!button) return;
    const start = Number(button.dataset.start);
    const entity = button.dataset.entity;
    const same = this._selectedTrip?.entity === entity && this._selectedTrip.start === start;
    this._selectedTrip = same ? null : { entity, start, end: Number(button.dataset.end) };
    this._renderTrips();
    this._renderMap(true);
  }

  // --- Data ------------------------------------------------------------------

  async _loadDay(refit) {
    const day = this._day;
    const token = ++this._loadToken;
    this._lastLoad = Date.now();
    if (refit && !this._dayData[day]) {
      this._tripsBody.innerHTML = `<div class="empty">Loading…</div>`;
    }
    const result = await this._fetchDay(
      day,
      this._entities.map((e) => e.entity),
      true
    );
    if (token !== this._loadToken) return;
    if (result.error) {
      this._tripsBody.innerHTML = `<div class="empty">Could not read history: ${escapeHtml(result.error)}</div>`;
      return;
    }
    this._dayData[day] = result.data;
    this._renderTrips();
    this._renderMap(refit);
  }

  // --- Rendering -------------------------------------------------------------

  _renderList() {
    if (!this._list) return;
    const rows = this._visibleEntities().map((e) => {
      const info = this._info(e);
      const status = this._status(info);
      const volts = info?.voltage ?? null;
      const battery = this._batteryClass(volts);
      const batteryTitle = volts === null ? "Battery voltage unknown" : `Battery ${volts.toFixed(2)} V`;
      const ignition = info?.ignition;
      return `<button class="vrow ${e.entity === this._selected ? "sel" : ""}" role="listitem"
          data-entity="${escapeHtml(e.entity)}">
          <span class="avatar" style="background:${escapeHtml(e.color)}">
            <ha-icon icon="${escapeHtml(e.icon || "mdi:car")}"></ha-icon></span>
          <span class="vtext">
            <div class="vname">${escapeHtml(this._name(e))}</div>
            <div class="vstatus ${status.cls}">${escapeHtml(status.text)}</div>
          </span>
          <span class="vicons">
            <ha-icon class="${ignition ? "good" : "muted"}" icon="${ignition ? "mdi:key-variant" : "mdi:key-remove"}"
              title="${ignition ? "Ignition on" : "Ignition off"}"></ha-icon>
            <ha-icon class="${battery}" icon="mdi:car-battery" title="${escapeHtml(batteryTitle)}"></ha-icon>
          </span>
        </button>`;
    });
    this._list.innerHTML = rows.length
      ? rows.join("")
      : `<div class="empty">No vehicles match “${escapeHtml(this._filter)}”.</div>`;
  }

  _formatDateTime(ms) {
    const locale = this._hass?.locale?.language || navigator.language;
    return new Intl.DateTimeFormat(locale, {
      timeZone: this._tz(),
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: this._hass?.locale?.time_format === "12",
    }).format(new Date(ms));
  }

  _renderPopup() {
    if (!this._popup) return;
    const entry = this._selected ? this._entry(this._selected) : null;
    const info = entry ? this._info(entry) : null;
    if (!entry || !info) {
      this._popup.hidden = true;
      return;
    }
    const status = this._status(info);
    const rows = [
      [
        "Last update",
        Number.isFinite(info.updated)
          ? `${this._formatDateTime(info.updated)} · ${this._relative(info.updated)}`
          : "—",
      ],
      ["Status", `<span class="${status.cls}">${escapeHtml(status.text)}</span>`, true],
      ["Address", info.address || "—"],
      ["Speed", Number.isFinite(info.speed) ? `${Math.round(info.speed)} km/h` : "—"],
      [
        "Odometer",
        Number.isFinite(info.odometer)
          ? `${Math.round(info.odometer / 1000).toLocaleString(this._hass?.locale?.language)} km`
          : "—",
      ],
      [
        "Battery",
        info.voltage === null
          ? "—"
          : `<span class="${this._batteryClass(info.voltage)}">${info.voltage.toFixed(2)} V</span>`,
        true,
      ],
    ];
    const hasPosition = Number.isFinite(info.lat) && Number.isFinite(info.lon);
    this._popup.innerHTML = `
      <div class="pop-head"><span class="dot" style="background:${escapeHtml(entry.color)}"></span>
        <span>${escapeHtml(this._name(entry))}</span>
        <button class="icon-btn x" data-act="close" title="Close" aria-label="Close">
          <ha-icon icon="mdi:close"></ha-icon></button></div>
      <dl>${rows
        .map(([label, value, html]) => `<dt>${label}</dt><dd>${html ? value : escapeHtml(value)}</dd>`)
        .join("")}</dl>
      <div class="actions">
        <button class="icon-btn" data-act="route" title="Today's route" aria-label="Today's route">
          <ha-icon icon="mdi:map-marker-path"></ha-icon></button>
        <button class="icon-btn ${this._follow ? "on" : ""}" data-act="follow" title="Follow on the map"
          aria-label="Follow on the map" aria-pressed="${this._follow}" ${hasPosition ? "" : "disabled"}>
          <ha-icon icon="mdi:crosshairs-gps"></ha-icon></button>
        <button class="icon-btn" data-act="directions" title="Directions" aria-label="Directions"
          ${hasPosition ? "" : "disabled"}><ha-icon icon="mdi:directions"></ha-icon></button>
        <button class="icon-btn" data-act="info" title="Details and history" aria-label="Details">
          <ha-icon icon="mdi:information-outline"></ha-icon></button>
      </div>`;
    this._popup.hidden = false;
  }

  _tripEntities() {
    if (this._selected) return [this._entry(this._selected)].filter(Boolean);
    return this._visibleEntities();
  }

  _renderTrips() {
    if (!this._tripsBody) return;
    const data = this._dayData[this._day];
    if (!data) return;
    const sections = this._tripEntities().map((e) => {
      const d = data[e.entity];
      if (!d) return "";
      const s = this._summarize(d.points);
      const stats = s.first
        ? `${this._formatKm(s.km)} · ${this._formatDuration(s.driving)}`
        : d.points.length
          ? "Parked all day"
          : "No positions";
      let note = "";
      if (d.tripsError === "forbidden") {
        note = `<div class="note">Cartrack trips aren't available for this account; drives come from the route.</div>`;
      } else if (d.tripsError) {
        note = `<div class="note">Cartrack trips could not be loaded; drives come from the route.</div>`;
      }
      const items = this._tripItems(e, d)
        .map((trip) => {
          const selected =
            this._selectedTrip?.entity === e.entity && this._selectedTrip.start === trip.start;
          const where = trip.pending
            ? "<em>Not yet reported by Cartrack</em>"
            : trip.where.map(escapeHtml).join(" → ");
          return `<button class="trip ${selected ? "selected" : ""}" data-entity="${escapeHtml(e.entity)}"
              data-start="${trip.start}" data-end="${trip.end}">
              <span class="when">${this._formatTime(trip.start)} – ${this._formatTime(trip.end)}</span>
              <span class="dist">${Number.isFinite(trip.km) ? this._formatKm(trip.km) : ""}</span>
              ${where ? `<span class="where">${where}</span>` : ""}
            </button>`;
        })
        .join("");
      return `<div class="vsum"><span class="dot" style="background:${escapeHtml(e.color)}"></span>
          ${escapeHtml(this._name(e))}<span class="stats">${stats}</span></div>
        ${note}${items ? `<div class="trips">${items}</div>` : ""}`;
    });
    this._tripsBody.innerHTML = sections.join("") || `<div class="empty">No vehicles.</div>`;
  }

  _renderMap(refit) {
    if (!this._map) return;
    const isToday = this._day === todayIn(this._tz());
    const markers = (list) =>
      list
        .filter((e) => this._hass.states[e.entity])
        .map((e) => ({ entity_id: e.entity, color: e.color, name: this._name(e) }));
    const latLngs = [];

    if (this._tab === "trips") {
      const data = this._dayData[this._day] || {};
      const paths = [];
      for (const e of this._tripEntities()) {
        let points = data[e.entity]?.points || [];
        const sel = this._selectedTrip;
        if (sel) {
          if (sel.entity !== e.entity) continue;
          points = points.filter((p) => p.t >= sel.start - 60000 && p.t <= sel.end + 60000);
        }
        points = this._thin(points);
        if (points.length < 2) continue;
        paths.push({
          points: points.map((p) => ({ point: [p.lat, p.lon], timestamp: new Date(p.t) })),
          color: e.color,
          name: this._name(e),
        });
        points.forEach((p) => latLngs.push([p.lat, p.lon]));
      }
      this._map.paths = paths;
      this._map.entities = isToday && !this._selectedTrip ? markers(this._tripEntities()) : [];
      this._setOverlay(
        this._dayData[this._day] && !paths.length
          ? this._selectedTrip
            ? "No recorded positions for this trip."
            : "No movement on this day."
          : ""
      );
    } else {
      this._map.paths = [];
      this._map.entities = markers(this._entities);
      this._setOverlay("");
    }

    if (!refit) return;
    if (!latLngs.length) {
      const focus = this._selected ? [this._entry(this._selected)] : this._entities;
      for (const e of focus.filter(Boolean)) {
        const info = this._info(e);
        if (info && Number.isFinite(info.lat)) latLngs.push([info.lat, info.lon]);
      }
    }
    this._fitWhenReady(latLngs);
  }
}

if (!customElements.get("cartrack-fleet-card")) {
  customElements.define("cartrack-fleet-card", CartrackFleetCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "cartrack-fleet-card",
    name: "Cartrack fleet",
    description: "Vehicle list, live map, details and trips in one fleet-tracking view.",
    documentationURL: "https://github.com/britsmarius/ha-cartrack",
  });
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
