/* DRIFT command centre UI. LIVE mode renders only the integrity-checked feed (data/drift_live.json);
 * SIMULATION mode renders the synthetic engine (assets/drift_sim.js). The two never share data or colours' meaning:
 * simulation is always marked, and nothing here sends a notification. */
(function () {
  'use strict';
  var S = window.DriftSim;
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; }); };
  var STALE_H = 12;

  var st = { mode: 'live', hazard: 'thunderstorm', layer: 'cape', thr: 1000, t: 3, sel: null, playing: false, timer: null,
             live: null, ev: null, sc: null, frame: null, warns: [], log: [], research: false, clock: 0, panel: 'detail' };
  var map = null, mapReady = false;

  /* ------------------------------------------------------------------ colour ramps */
  var RAMPS = {
    seq: [[0, '#1b2a4a'], [.25, '#2b6cb0'], [.5, '#f6d860'], [.75, '#f08a3c'], [1, '#d62839']],
    rain: [[0, '#14304d'], [.3, '#1f78b4'], [.6, '#6ec3c9'], [.8, '#f6d860'], [1, '#d62839']],
    sim: [[0, '#2b3350'], [.3, '#e8c15a'], [.55, '#f08a3c'], [1, '#e11d48']],
    res: [[0, '#2c2540'], [.5, '#8e6bd0'], [1, '#f1c9ff']]
  };
  function hex(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
  function ramp(name, x) {
    var r = RAMPS[name]; x = Math.max(0, Math.min(1, x));
    for (var i = 1; i < r.length; i++) if (x <= r[i][0]) {
      var a = hex(r[i - 1][1]), b = hex(r[i][1]), f = (x - r[i - 1][0]) / (r[i][0] - r[i - 1][0]);
      return 'rgb(' + a.map(function (v, k) { return Math.round(v + (b[k] - v) * f); }).join(',') + ')';
    }
    return r[r.length - 1][1];
  }
  function gradient(name) { return 'linear-gradient(90deg,' + RAMPS[name].map(function (s) { return s[1] + ' ' + (s[0] * 100) + '%'; }).join(',') + ')'; }

  /* ------------------------------------------------------------------ live layer catalogue (honest semantics only) */
  var LIVE_LAYERS = {
    thunderstorm: [
      { id: 'cape', label: 'CAPE (GFS guidance)', col: 2, unit: 'J/kg', max: 3000, ramp: 'seq', kind: 'NWP guidance field - not a thunderstorm probability', threshold: true },
      { id: 'cin', label: 'CIN (GFS guidance)', col: 3, unit: 'J/kg', max: 300, ramp: 'seq', neg: true, kind: 'NWP guidance field - inhibition (stronger = capped)' }],
    extreme_rainfall: [
      { id: 'cb', label: 'Heavy-rain-day outlook score', col: 5, unit: 'score', max: 0.4, ramp: 'rain', kind: 'Uncalibrated model score for the whole forecast day (>=64.5 mm/24 h proxy). Not a cloudburst, not a probability.' },
      { id: 'qpe', label: 'GFS precipitation', col: 4, unit: 'mm', max: 40, ramp: 'rain', kind: 'NWP precipitation at the single model hour - guidance, not observed' }],
    flash_flood: [
      { id: 'none', label: 'No validated layer', col: -1, unit: '', max: 1, ramp: 'seq', kind: 'Flash-flood prediction is unavailable pan-India' }]
  };
  function layerDef() { return (LIVE_LAYERS[st.hazard].filter(function (l) { return l.id === st.layer; })[0]) || LIVE_LAYERS[st.hazard][0]; }

  /* ------------------------------------------------------------------ geometry */
  var LIVE_CELLS = []; // [lat, lon, ...] from the feed
  function poly(la, lo, h) { h = h || 0.5; return [[[lo - h, la - h], [lo + h, la - h], [lo + h, la + h], [lo - h, la + h], [lo - h, la - h]]]; }
  function features(cellsLL) {
    return { type: 'FeatureCollection', features: cellsLL.map(function (c, i) { return { type: 'Feature', id: i, properties: { i: i, color: '#000', op: 0 }, geometry: { type: 'Polygon', coordinates: poly(c[0], c[1]) } }; }) };
  }

  /* ------------------------------------------------------------------ data */
  function ageH(iso) { var d = iso ? Date.parse(iso) : NaN; return isNaN(d) ? null : (Date.now() - d) / 36e5; }
  function srcState(s) { var a = ageH(s.generated_at_utc); if (s.state === 'UNAVAILABLE' || s.integrity_ok === false || a == null) return 'UNAVAILABLE'; return a <= STALE_H ? 'FRESH' : 'STALE'; }
  function sourceById(id) { return (st.live && st.live.sources || []).filter(function (s) { return s.id === id; })[0]; }
  function fmt(v, d, u) { return v == null ? '<span class="na">unavailable</span>' : (+v).toFixed(d == null ? 0 : d) + (u ? ' ' + u : ''); }

  function load() {
    var opts = { cache: 'no-store' };
    return Promise.all([
      fetch('data/drift_live.json', opts).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).catch(function () { return null; }),
      fetch('data/hazard_evaluation.json', opts).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
    ]).then(function (a) {
      st.live = a[0]; st.ev = a[1];
      if (st.live && st.live.mode === 'LIVE_OPERATIONAL' && st.live.grid) LIVE_CELLS = st.live.grid.cells; else { st.live = null; LIVE_CELLS = []; }
    });
  }

  /* ------------------------------------------------------------------ rendering: map */
  function simCells() { return S.CELLS; }
  function paint() {
    if (!mapReady) return;
    var cells = st.mode === 'sim' ? simCells() : LIVE_CELLS, src = map.getSource('cells');
    var ov = '', legend = '', data = features(cells);
    if (st.mode === 'sim') {
      var f = st.frame, h = S.HAZARDS[st.hazard];
      data.features.forEach(function (ft, i) { var lv = f.levels[i], x = f.values[i] / h.max; ft.properties.color = ramp('sim', x); ft.properties.op = lv === 0 ? 0.28 : 0.4 + 0.18 * lv; });
      legend = '<b>SIMULATED ' + esc(h.label) + '</b><div class="bar" style="background:' + gradient('sim') + '"></div><span>0 → ' + h.max + ' ' + esc(h.unit) + '</span><br><span class="mute">Synthetic data. Not a forecast.</span>';
    } else if (!st.live) {
      ov = 'LIVE feed unavailable. No values are shown.';
    } else {
      var L = layerDef(), gs = sourceById('gfs_grid'), cs = sourceById('cb_daily_outlook');
      var needed = (L.id === 'cb') ? cs : gs, state = needed ? srcState(needed) : 'UNAVAILABLE';
      if (st.hazard === 'flash_flood' && !st.research) ov = 'Flash-flood prediction is UNAVAILABLE.<br>No validated model or observed-flood target exists. See “Hazard info” for what live operation needs.';
      else if (state !== 'FRESH') ov = 'Source is ' + state + ' — values are withheld, not shown as current.';
      var rs = st.hazard === 'flash_flood' && st.research;
      data.features.forEach(function (ft, i) {
        var c = LIVE_CELLS[i], v = null, ok = !ov;
        if (rs) { v = c[6]; ok = v != null && !ov.length; }
        else if (L.col >= 0) v = c[L.col];
        if (!ok || v == null) { ft.properties.op = 0.0; return; }
        var x = Math.abs(v) / L.max;
        if (L.threshold && !rs) { ft.properties.color = v >= st.thr ? ramp(L.ramp, x) : '#243252'; ft.properties.op = v >= st.thr ? 0.78 : 0.22; }
        else { ft.properties.color = ramp(rs ? 'res' : L.ramp, x); ft.properties.op = 0.72; }
      });
      if (rs) legend = '<b>RESEARCH ONLY</b> — PU-learning ranking (not a probability)<div class="bar" style="background:' + gradient('res') + '"></div><span>low → high rank</span>';
      else if (st.hazard !== 'flash_flood' && !ov) legend = '<b>' + esc(L.label) + '</b><div class="bar" style="background:' + gradient(L.ramp) + '"></div><span>0 → ' + L.max + ' ' + esc(L.unit) + (L.threshold ? ' · highlighted ≥ ' + st.thr : '') + '</span><br><span class="mute">' + esc(L.kind) + '</span>';
    }
    src.setData(data);
    $('legend').innerHTML = legend; $('legend').style.display = legend ? 'block' : 'none';
    $('overlay').style.display = ov ? 'flex' : 'none'; $('overlay').innerHTML = ov;
    if (map.getLayer('sel')) map.setFilter('sel', ['==', ['get', 'i'], st.sel == null ? -1 : st.sel]);
  }

  function initMap() {
    if (!window.maplibregl) { $('overlay').style.display = 'flex'; $('overlay').textContent = 'Map library could not be loaded.'; return; }
    map = new maplibregl.Map({
      container: 'map', center: [82.5, 22], zoom: window.innerWidth < 760 ? 3 : 3.8, minZoom: 2.5, maxZoom: 9, attributionControl: true,
      style: { version: 8, sources: { base: { type: 'raster', tileSize: 256, attribution: '© OpenStreetMap contributors', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'] } },
               layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b1220' } }, { id: 'base', type: 'raster', source: 'base', paint: { 'raster-opacity': 0.5, 'raster-saturation': -0.85, 'raster-brightness-max': 0.4, 'raster-contrast': 0.2 } }] }
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.once('style.load', function () {
      map.addSource('cells', { type: 'geojson', data: features([]) });
      map.addLayer({ id: 'fill', type: 'fill', source: 'cells', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['get', 'op'] } });
      map.addLayer({ id: 'grid', type: 'line', source: 'cells', paint: { 'line-color': '#9fb3d8', 'line-opacity': 0.12, 'line-width': 0.5 } });
      map.addLayer({ id: 'sel', type: 'line', source: 'cells', filter: ['==', ['get', 'i'], -1], paint: { 'line-color': '#ffffff', 'line-width': 2.5 } });
      map.on('click', 'fill', function (e) { if (e.features && e.features[0]) select(e.features[0].properties.i); });
      map.on('mouseenter', 'fill', function () { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'fill', function () { map.getCanvas().style.cursor = ''; });
      mapReady = true; refresh();
    });
  }

  /* ------------------------------------------------------------------ rendering: panels */
  var HZ_ORDER = ['thunderstorm', 'extreme_rainfall', 'flash_flood'];
  function renderHazardTabs() {
    $('hz').innerHTML = HZ_ORDER.map(function (h) {
      var label = S.HAZARDS[h].label, sub;
      if (st.mode === 'sim') sub = 'Simulated scenario';
      else { var status = st.live ? st.live.hazards[h].status : 'UNAVAILABLE'; sub = ({ PARTIAL: 'Station model + NWP guidance', AVAILABLE_DAILY_OUTLOOK_ONLY: 'Daily outlook only', UNAVAILABLE: 'Unavailable' })[status] || status; }
      return '<button data-h="' + h + '" aria-pressed="' + (st.hazard === h) + '">' + esc(label) + '<small>' + esc(sub) + '</small></button>';
    }).join('');
    Array.prototype.forEach.call($('hz').querySelectorAll('button'), function (b) { b.onclick = function () { st.hazard = b.dataset.h; st.sel = null; defaultLayer(); if (st.mode === 'sim') newScenario(); refresh(); }; });
  }
  function defaultLayer() { st.layer = LIVE_LAYERS[st.hazard][0].id; st.research = false; }

  function renderControls() {
    var h = '';
    if (st.mode === 'live') {
      var ls = LIVE_LAYERS[st.hazard];
      h = '<h2>Layer</h2><label for="lyr">Map layer</label><select id="lyr">' + ls.map(function (l) { return '<option value="' + l.id + '"' + (l.id === st.layer ? ' selected' : '') + '>' + esc(l.label) + '</option>'; }).join('') + '</select>';
      if (layerDef().threshold && st.hazard !== 'flash_flood') h += '<label for="thr">Highlight cells with CAPE ≥ <b id="thrv">' + st.thr + '</b> J/kg</label><input id="thr" type="range" min="0" max="3000" step="100" value="' + st.thr + '"><div class="small mute">A user-chosen display threshold on a model input — not a risk level.</div>';
      if (st.hazard === 'flash_flood') h += '<label><input type="checkbox" id="res"' + (st.research ? ' checked' : '') + '> Show research-only PU ranking</label><div class="small mute">Research view. Not a probability, never used for alerts.</div>';
    } else {
      var p = st.sc.params;
      h = '<h2>Scenario controls <span class="chip" style="color:var(--sim)">SIMULATION</span></h2>' +
        sl('intensity', 'Intensity', 0.1, 1, 0.05, p.intensity) + sl('radius', 'Storm radius (°)', 0.6, 3, 0.1, p.radius) + sl('speed', 'Speed (°/h)', 0, 0.8, 0.05, p.speed) +
        sl('bearing', 'Heading (° from N)', 0, 359, 5, p.bearing) + (st.hazard !== 'thunderstorm' ? sl('moisture', 'Antecedent soil moisture', 0, 1, 0.05, p.moisture) : '') +
        '<div class="row" style="margin-top:10px"><button class="btn" id="rst">Reset scenario</button></div><div class="small mute" style="margin-top:6px">' + esc(S.HAZARDS[st.hazard].story) + '</div>';
    }
    $('controls').innerHTML = h;
    var q = function (id) { return $(id); };
    if (q('lyr')) q('lyr').onchange = function () { st.layer = this.value; refresh(); };
    if (q('thr')) q('thr').oninput = function () { st.thr = +this.value; q('thrv').textContent = st.thr; paint(); };
    if (q('res')) q('res').onchange = function () { st.research = this.checked; paint(); renderTimeline(); };
    if (q('rst')) q('rst').onclick = function () { newScenario(); refresh(); };
    ['intensity', 'radius', 'speed', 'bearing', 'moisture'].forEach(function (k) {
      var el = q('s-' + k); if (el) el.oninput = function () { st.sc.params[k] = +this.value; q('v-' + k).textContent = this.value; recompute(); paint(); renderAlerts(); renderDetail(); renderTimeline(); };
    });
  }
  function sl(k, label, min, max, step, v) { return '<label for="s-' + k + '">' + label + ': <b id="v-' + k + '">' + v + '</b></label><input id="s-' + k + '" type="range" min="' + min + '" max="' + max + '" step="' + step + '" value="' + v + '">'; }

  function renderHazardInfo() {
    var hz = st.hazard, h = '';
    if (st.mode === 'sim') {
      h = '<h2>Hazard info</h2><p class="small"><b>' + esc(S.HAZARDS[hz].label) + '</b> — simulated field: ' + esc(S.HAZARDS[hz].unit) + '. Level thresholds are illustrative, not IMD/NDMA criteria.</p><p class="small mute">Real inputs, trained models and verification are not involved in this mode.</p>';
    } else if (!st.live) { h = '<h2>Hazard info</h2><p class="na">Live feed unavailable.</p>'; }
    else {
      var x = st.live.hazards[hz];
      h = '<h2>Hazard info</h2><p><span class="chip ' + esc(x.status) + '"><i></i>' + esc(x.status.replace(/_/g, ' ')) + '</span></p><p class="small">' + esc(x.summary) + '</p><p class="small"><b>Target:</b> ' + esc(x.target || '') + '</p>';
      if (x.missing_for_live) h += '<p class="small"><b>Live operation still needs:</b></p><ul class="small">' + x.missing_for_live.map(function (m) { return '<li>' + esc(m) + '</li>'; }).join('') + '</ul>';
      if (hz === 'thunderstorm') h += renderStation(x);
    }
    $('hzinfo').innerHTML = h;
  }
  function renderStation(x) {
    var s = sourceById('vobl_station'), state = s ? srcState(s) : 'UNAVAILABLE';
    if (state !== 'FRESH') return '<h2 style="margin-top:10px">VOBL station slots</h2><p class="na small">Station forecast is ' + state + ' — withheld.</p>';
    return '<h2 style="margin-top:10px">VOBL station slots</h2><p class="small mute">Bengaluru airport only. Uncalibrated model scores for IST 6-hour windows — not probabilities and not 2–6 h lead forecasts.</p>' +
      x.station_slots.map(function (sl) {
        var mx = Math.max.apply(null, sl.drivers.map(function (d) { return Math.abs(d.shap); }).concat([0.01]));
        return '<div class="slot"><div class="row"><b>' + esc(sl.time) + '</b><span class="spacer"></span><span>score ' + fmt(sl.score, 3) + ' / thr ' + fmt(sl.threshold, 2) + '</span></div>' +
          '<div class="small mute">' + (sl.fallback ? 'FALLBACK input · ' : '') + esc(sl.source || '') + (sl.predicted ? ' · above threshold' : ' · below threshold') + '</div>' +
          sl.drivers.map(function (d) { var w = Math.round(Math.abs(d.shap) / mx * 100); var neg = d.shap < 0;
            return '<div class="small" style="margin-top:4px">' + esc(d.feature) + ' = ' + (+d.value).toFixed(1) + ' <span class="mute">(' + (neg ? 'lowers' : 'raises') + ' score)</span><div class="bar2"><b style="left:0;width:' + w + '%;background:' + (neg ? 'var(--accent)' : 'var(--bad)') + '"></b></div></div>'; }).join('') + '</div>';
      }).join('');
  }

  function select(i) { st.sel = i; if (map && map.getLayer('sel')) map.setFilter('sel', ['==', ['get', 'i'], i]); setPanel('detail'); renderDetail(); }
  function renderDetail() {
    var el = $('p-detail');
    if (st.sel == null) { el.innerHTML = '<p class="mute">Select a grid cell on the map to see its values and provenance.</p>'; return; }
    if (st.mode === 'sim') {
      var c = S.CELLS[st.sel], f = st.frame, hz = S.HAZARDS[st.hazard];
      var series = []; for (var t = 0; t <= S.HOURS; t++) series.push(S.frame(st.sc, t).values[st.sel]);
      var mx = Math.max.apply(null, series.concat([hz.max * 0.05]));
      el.innerHTML = '<span class="chip" style="color:var(--sim)">SIMULATION</span><h3>Cell ' + c[0] + '°N ' + c[1] + '°E</h3><table><tr><th>Simulated level</th><td class="lvl' + f.levels[st.sel] + '">' + S.LEVELS[f.levels[st.sel]] + '</td></tr><tr><th>Simulated value</th><td>' + f.values[st.sel].toFixed(hz.max <= 1 ? 2 : 0) + ' ' + esc(hz.unit) + '</td></tr><tr><th>Timeline</th><td>T+' + st.t + ' h (' + (f.inTargetBand ? 'inside' : 'outside') + ' the 2–6 h band)</td></tr></table><h2 style="margin-top:10px">Simulated evolution</h2><div style="display:flex;align-items:flex-end;gap:4px;height:60px">' +
        series.map(function (v, k) { return '<div title="T+' + k + ' h" style="flex:1;background:' + (k === st.t ? 'var(--sim)' : 'var(--accent)') + ';height:' + Math.max(3, v / mx * 100) + '%;border-radius:3px 3px 0 0"></div>'; }).join('') + '</div><div class="ticks"><span>T+0</span><span>T+6</span></div><p class="small mute">All values are synthetic.</p>';
      return;
    }
    var cell = LIVE_CELLS[st.sel]; if (!cell) { el.innerHTML = ''; return; }
    var gs = sourceById('gfs_grid'), cs = sourceById('cb_daily_outlook');
    el.innerHTML = '<h3>Cell ' + cell[0] + '°N ' + cell[1] + '°E</h3><table>' +
      '<tr><th>CAPE</th><td>' + fmt(cell[2], 0, 'J/kg') + '</td></tr><tr><th>CIN</th><td>' + fmt(cell[3], 0, 'J/kg') + '</td></tr><tr><th>GFS precipitation</th><td>' + fmt(cell[4], 1, 'mm') + '</td></tr>' +
      '<tr><th>Heavy-rain-day score</th><td>' + fmt(cell[5], 3) + '</td></tr><tr><th>Flash-flood prediction</th><td><span class="na">unavailable</span></td></tr>' +
      (st.research && st.hazard === 'flash_flood' ? '<tr><th>Research PU rank</th><td>' + fmt(cell[6], 2) + ' <span class="mute small">(research only)</span></td></tr>' : '') + '</table>' +
      '<p class="small mute">NWP fields: GFS cycle ' + esc(gs && gs.model_init_utc || '?') + ', model hour f' + esc(gs && gs.forecast_hour) + '. Score: ' + esc(cs && cs.window_start_utc || '?') + ' → ' + esc(cs && cs.window_end_utc || '?') + ' (daily window). Unavailable predictors for every cell: ' + esc((gs && gs.unavailable_predictors || []).join(', ') || 'none') + '.</p>';
  }

  function renderAlerts() {
    var el = $('p-alerts');
    if (st.mode === 'sim') {
      var hdr = '<span class="chip" style="color:var(--sim)">SIMULATION</span><p class="small">Simulated warnings from the scenario. Nothing is sent; the lifecycle below is a demonstration of the stages.</p>';
      var body = st.warns.length ? st.warns.map(function (w, k) {
        return '<div class="warn l' + w.level + '"><b>' + esc(w.headline) + '</b><div class="small mute">Valid T+' + w.validHour + ' h · simulated lead ' + w.simulatedLeadHours + ' h' + (w.inTargetBand ? ' (in 2–6 h band)' : '') + '</div><ul class="small">' + w.actions.map(function (a) { return '<li>' + esc(a) + '</li>'; }).join('') + '</ul><div class="row" style="margin-top:6px"><button class="btn" data-z="' + k + '">Show on map</button><button class="btn simb" data-n="' + k + '">Simulate notification</button></div></div>';
      }).join('') : '<p class="mute">No simulated warnings at this intensity. Raise the intensity slider.</p>';
      el.innerHTML = hdr + body + '<h2 style="margin-top:10px">Simulated alert log</h2><div class="log" id="log">' + (st.log.length ? st.log.map(logLine).join('') : '<span class="mute">empty</span>') + '</div>';
      Array.prototype.forEach.call(el.querySelectorAll('[data-z]'), function (b) { b.onclick = function () { var w = st.warns[+b.dataset.z]; st.t = w.validHour; recompute(); select(S.CELLS.findIndex(function (c) { return c[0] === w.lat && c[1] === w.lon; })); map.flyTo({ center: [w.lon, w.lat], zoom: 5.5 }); refresh(true); }; });
      Array.prototype.forEach.call(el.querySelectorAll('[data-n]'), function (b) { b.onclick = function () { simNotify(st.warns[+b.dataset.n]); }; });
      return;
    }
    var a = st.live && st.live.alerts;
    el.innerHTML = '<div class="warn"><b>No DRIFT warning is active</b><div class="small mute">Automated alerting: ' + esc(a ? a.automated_dispatch : 'UNKNOWN') + ' · eligible ' + (a ? a.eligible : '–') + ' of ' + (a ? a.n_evaluated : '–') + ' evaluated</div></div>' +
      '<h2>Why alerts are withheld</h2><p class="small">' + esc(a ? a.withheld_explanation : 'Live feed unavailable.') + '</p>' +
      (a && a.withheld_reason_codes ? '<div class="small mute">' + a.withheld_reason_codes.map(esc).join(' · ') + '</div>' : '') +
      '<h2 style="margin-top:12px">Recommended action</h2><p class="small">DRIFT is not an official warning source. Follow IMD, NDMA and district authority advisories. Use SIMULATION mode to see how DRIFT warnings, actions and notifications would work once a hazard model passes the alert-eligibility policy.</p>';
  }
  function logLine(e) { return '<div><span class="s">[SIM T+' + e.atMinute + 'm]</span> ' + esc(e.stage) + ' — ' + esc(e.note) + ' (not sent)</div>'; }
  function simNotify(w) {
    var lc = S.lifecycle(w, st.clock); st.clock += 3; var i = 0;
    (function step() { if (i >= lc.length) return; st.log.push(lc[i++]); renderAlerts(); var l = $('log'); if (l) l.scrollTop = l.scrollHeight; setTimeout(step, 350); })();
  }

  function renderSources() {
    var el = $('p-sources');
    if (st.mode === 'sim') { el.innerHTML = '<span class="chip" style="color:var(--sim)">SIMULATION</span><p class="small">Simulation mode has no data sources. Every value is generated in the browser from the sliders.</p>'; return; }
    if (!st.live) { el.innerHTML = '<p class="na">Live feed unavailable.</p>'; return; }
    el.innerHTML = st.live.sources.map(function (s) {
      var state = srcState(s), a = ageH(s.generated_at_utc);
      return '<div class="slot"><div class="row"><b>' + esc(s.label) + '</b><span class="spacer"></span><span class="chip ' + state + '"><i></i>' + state + '</span></div><div class="small mute">Generated ' + esc(s.generated_at_utc || 'unknown') + (a == null ? '' : ' · ' + a.toFixed(1) + ' h ago') + (s.model_init_utc ? '<br>Model init ' + esc(s.model_init_utc) : '') + (s.forecast_hour != null ? ' · f' + s.forecast_hour : '') + '</div></div>';
    }).join('') + '<p class="small mute">Feed built ' + esc(st.live.generated_at_utc) + '. Ages are recomputed in your browser; sources older than ' + STALE_H + ' h are marked STALE and their values are withheld. ' + esc(st.live.disclaimer) + '</p>';
  }

  function renderEval() {
    var el = $('p-eval'), e = st.ev;
    if (st.mode === 'sim') { el.innerHTML = '<p class="small mute">Evaluation applies to LIVE models only. Simulation has no skill to measure.</p>'; return; }
    if (!e) { el.innerHTML = '<p class="na">Evaluation file unavailable.</p>'; return; }
    var t = e.hazards.thunderstorm, r = e.hazards.extreme_rainfall, f = e.hazards.flash_flood, u = r.variants.uncalibrated, c = r.variants.calibrated;
    el.innerHTML = '<p class="small mute">' + esc(e.claims_policy) + '</p>' +
      '<div class="slot"><b>Thunderstorm (VOBL station)</b><div class="small">' + esc(t.validation) + '</div><table><tr><th>Mean AUROC</th><td>' + t.summary.mean_auroc + '</td></tr><tr><th>Mean Brier skill vs climatology</th><td>' + t.summary.mean_bss_vs_climatology + ' (positive in ' + t.summary.years_with_positive_bss + ' of ' + t.folds.length + ' years)</td></tr><tr><th>Mean POD / FAR</th><td>' + t.summary.mean_pod + ' / ' + t.summary.mean_far + '</td></tr></table><div class="small mute">' + t.limits.map(esc).join(' ') + '</div></div>' +
      '<div class="slot"><b>Extreme rainfall (heavy-rain day, pan-India)</b><div class="small">' + esc(r.scope) + '</div><table><tr><th>AUROC / PR-AUC (uncal.)</th><td>' + u.auroc + ' / ' + u.pr_auc + ' (no-skill PR ' + u.baseline_pr_auc_no_skill + ')</td></tr><tr><th>Brier skill vs climatology</th><td>uncal. ' + u.bss_vs_climatology + ' · cal. ' + c.bss_vs_climatology + '</td></tr><tr><th>POD / FAR (uncal.)</th><td>' + u.pod + ' / ' + u.far + '</td></tr><tr><th>POD / FAR (cal.)</th><td>' + c.pod + ' / ' + c.far + '</td></tr></table><div class="small mute">' + esc(r.summary.note) + '</div></div>' +
      '<div class="slot"><b>Flash flood</b><div class="small na">Not validated — no evaluation exists.</div><div class="small mute">' + f.limits.map(esc).join(' ') + '</div></div>';
  }

  /* ------------------------------------------------------------------ timeline */
  function renderTimeline() {
    var el = $('timeline'), W = S.HOURS, b = S.TARGET_BAND, left = (b[0] / W * 100) + '%', width = ((b[1] - b[0]) / W * 100) + '%';
    var ticks = '<div class="ticks">' + [0, 1, 2, 3, 4, 5, 6].map(function (i) { return '<span>T+' + i + 'h</span>'; }).join('') + '</div>';
    if (st.mode === 'sim') {
      el.innerHTML = '<div class="row"><b>Simulated timeline</b><span class="chip" style="color:var(--sim)">SIMULATION</span><span class="spacer"></span><button class="btn simb" id="play">' + (st.playing ? 'Pause' : 'Play') + '</button></div>' +
        '<div class="track"><div class="band" style="left:' + left + ';width:' + width + '"><span>2–6 h target band</span></div></div>' +
        '<input type="range" id="tl" min="0" max="' + W + '" step="1" value="' + st.t + '" aria-label="Simulated hour" style="margin-top:6px">' + ticks +
        '<div class="small">Showing <b>T+' + st.t + ' h</b> after the simulated issue time. Peak simulated level: <b class="lvl' + st.frame.peakLevel + '">' + S.LEVELS[st.frame.peakLevel] + '</b> · cells at WATCH/ALERT/WARNING: ' + st.frame.counts.slice(1).join(' / ') + '</div>';
      $('tl').oninput = function () { st.t = +this.value; recompute(); paint(); renderAlerts(); renderDetail(); renderTimeline(); };
      $('play').onclick = togglePlay;
      return;
    }
    var gs = sourceById('gfs_grid');
    el.innerHTML = '<div class="row"><b>2–6 h timeline</b><span class="chip UNAVAILABLE"><i></i>LEAD-SPECIFIC FORECAST UNAVAILABLE</span></div>' +
      '<div class="track"><div class="band" style="left:' + left + ';width:' + width + ';opacity:.45"><span>2–6 h band · not populated</span></div></div>' + ticks +
      '<p class="small mute">DRIFT does not record when a forecast becomes available to users, so no operational lead time can be claimed and no lead-specific 2–6 h value exists. ' +
      'The map shows a single GFS model hour' + (gs && gs.forecast_hour != null ? ' (f' + esc(gs.forecast_hour) + ')' : '') + ' and a whole-day outlook window. Switch to SIMULATION to explore the 2–6 h interface with synthetic data.</p>';
  }
  function togglePlay() {
    st.playing = !st.playing; clearInterval(st.timer);
    if (st.playing) st.timer = setInterval(function () { st.t = st.t >= S.HOURS ? 0 : st.t + 1; recompute(); paint(); renderAlerts(); renderDetail(); renderTimeline(); }, 1100);
    renderTimeline();
  }

  /* ------------------------------------------------------------------ mode / refresh */
  function newScenario() { st.sc = S.createScenario(st.hazard); st.t = 3; st.log = []; recompute(); }
  function recompute() { st.frame = S.frame(st.sc, st.t); st.warns = S.warnings(st.sc); }
  function banner() {
    var b = $('banner');
    if (st.mode === 'sim') { b.className = 'sim'; b.textContent = 'SIMULATION MODE — every map value, warning and notification here is synthetic. It is not a forecast and nothing is ever sent.'; }
    else { b.className = 'live'; b.textContent = 'LIVE MODE — only integrity-checked data is shown; unavailable data stays unavailable. Advisory model outputs, not IMD/NDMA warnings.'; }
    $('simmark').style.display = st.mode === 'sim' ? 'block' : 'none';
    $('maplabel').innerHTML = st.mode === 'sim' ? '<span class="chip" style="color:var(--sim)"><i style="background:var(--sim)"></i>SIMULATION</span>' : '<span class="chip ' + worst() + '"><i></i>LIVE · ' + worst() + '</span>';
    var f = $('fresh'); f.className = 'chip ' + (st.mode === 'sim' ? 'STALE' : worst()); f.querySelector('span').textContent = st.mode === 'sim' ? 'Synthetic data' : 'Data: ' + worst();
  }
  function worst() {
    if (!st.live) return 'UNAVAILABLE';
    var ss = st.live.sources.map(srcState);
    return ss.indexOf('UNAVAILABLE') >= 0 ? 'UNAVAILABLE' : ss.indexOf('STALE') >= 0 ? 'STALE' : 'FRESH';
  }
  function setMode(m) {
    st.mode = m; st.sel = null; clearInterval(st.timer); st.playing = false;
    $('m-live').setAttribute('aria-pressed', m === 'live'); $('m-sim').setAttribute('aria-pressed', m === 'sim');
    defaultLayer(); if (m === 'sim') newScenario(); history.replaceState(null, '', '#' + m); refresh();
  }
  function setPanel(p) {
    st.panel = p;
    Array.prototype.forEach.call(document.querySelectorAll('.rtabs button'), function (b) { b.setAttribute('aria-pressed', b.dataset.p === p); });
    ['detail', 'alerts', 'sources', 'eval'].forEach(function (k) { $('p-' + k).classList.toggle('on', k === p); });
  }
  function refresh() {
    banner(); renderHazardTabs(); renderControls(); renderHazardInfo(); renderDetail(); renderAlerts(); renderSources(); renderEval(); renderTimeline(); paint();
  }

  function boot() {
    $('m-live').onclick = function () { setMode('live'); }; $('m-sim').onclick = function () { setMode('sim'); };
    Array.prototype.forEach.call(document.querySelectorAll('.rtabs button'), function (b) { b.onclick = function () { setPanel(b.dataset.p); }; });
    initMap();
    load().then(function () { if (location.hash === '#sim') setMode('sim'); else refresh(); });
    window.__drift = { state: st, setMode: setMode, select: select, refresh: refresh };
  }
  boot();
})();
