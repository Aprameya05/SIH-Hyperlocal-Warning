/* DRIFT command centre UI.
 * LIVE mode: a location-aware view over the published national artifacts (0.25 deg India land grid + GFS fields +
 * terrain/gauge context), through assets/drift_location.js. Every value shown is derived from the selected cell and the
 * selected forecast hour; nothing is carried over from a previous selection.
 * SIMULATION mode: a synthetic engine (assets/drift_sim.js) on its own coarse grid. The modes share no data.
 * Nothing in this file sends a notification or contacts a recipient. */
(function () {
  'use strict';
  var S = window.DriftSim, DL = window.DriftLocation;
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; }); };
  var HZ_ORDER = ['thunderstorm', 'extreme_rainfall', 'flash_flood'];
  var HZ_LABEL = { thunderstorm: 'Severe thunderstorm', extreme_rainfall: 'Extreme rainfall', flash_flood: 'Flash flood' };

  var st = {
    mode: 'live', hazard: 'thunderstorm', layer: 'cape', hour: 0, sel: null, label: {}, compare: [], region: null, panel: 'detail',
    loading: true, store: null, live: null, ev: null, outline: null, tsm: null, hourCache: {},
    sc: null, t: 0, frame: null, narr: [], playing: false, timer: null, speed: 1100, lcShown: {}, thr: { cape: 1000, rain: 10 }
  };
  var map = null, mapReady = false;

  /* ------------------------------------------------------------------ colour ramps */
  var RAMPS = {
    seq: [[0, '#1b2a4a'], [.25, '#2b6cb0'], [.5, '#f6d860'], [.75, '#f08a3c'], [1, '#d62839']],
    rain: [[0, '#14304d'], [.3, '#1f78b4'], [.6, '#6ec3c9'], [.8, '#f6d860'], [1, '#d62839']],
    terrain: [[0, '#1d3b2a'], [.4, '#5b8c3a'], [.7, '#c9a14a'], [1, '#f1e9d2']],
    susc: [[0, '#1b2a4a'], [.5, '#5aa6c9'], [1, '#e8f6ff']],
    flood: [[0, '#1b2a4a'], [.35, '#4b6cb7'], [.7, '#9b59b6'], [1, '#ff6fb5']],
    sim: [[0, '#2b3350'], [.3, '#e8c15a'], [.55, '#f08a3c'], [1, '#e11d48']]
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

  /* ------------------------------------------------------------------ helpers */
  function chip(cls, text) { return '<span class="chip ' + esc(cls) + '"><i></i>' + esc(text) + '</span>'; }
  function fmt(v, d, u) { return v == null ? '<span class="na">unavailable</span>' : (+v).toFixed(d == null ? 0 : d) + (u ? ' ' + u : ''); }
  function ageH(iso) { var d = iso ? Date.parse(iso) : NaN; return isNaN(d) ? null : (Date.now() - d) / 36e5; }
  function agoText(iso) { var a = ageH(iso); if (a == null) return 'time unknown'; return a < 0 ? 'in ' + (-a).toFixed(1) + ' h' : a.toFixed(1) + ' h ago'; }
  function ist(iso) { var d = Date.parse(iso); if (isNaN(d)) return ''; var x = new Date(d + 330 * 60000); return ('0' + x.getUTCHours()).slice(-2) + ':' + ('0' + x.getUTCMinutes()).slice(-2) + ' IST'; }
  function fld() { return st.store && st.store.fieldsOk ? st.store.fields : null; }
  function hours() { return fld() ? st.store.hourMeta().files : []; }
  function hourStatus(k) {
    var f = fld(); if (!f) return 'UNAVAILABLE';
    var hm = st.store.hourMeta(), h = hm.files[k];
    return DL.hourStatus(Date.now(), hm.init, Date.parse(h.valid_time_utc), Date.parse(h.published_at_utc));
  }
  function cellIdx(cid) { return st.store.idx[cid]; }
  function labelOf(cid) { if (st.label[cid]) return st.label[cid]; var c = st.store.cells[cellIdx(cid)]; return c[1].toFixed(2) + '°N ' + c[2].toFixed(2) + '°E'; }
  function staticOf(i) { var s = st.store.staticsDoc; if (!s) return null; var j = st.store.sidx[st.store.cells[i][0]]; return j == null ? null : s.values[j]; }
  function scol(n) { return st.store.staticsDoc.columns.indexOf(n); }

  /* ------------------------------------------------------------------ live layers: each maps cell index -> value or null */
  function F(name) { return fld().fields[name][st.hour]; }
  function ffIndex(i) {   // identical formula to location_service.ff_hazard (parity-tested through the record API)
    var f = fld().fields, p3 = f.precip_3h, r6 = null, k;
    for (k = 0; k < p3.length - 1; k++) { var a = p3[k][i], b = p3[k + 1][i]; if (a != null && b != null && (r6 == null || a + b > r6)) r6 = a + b; }
    var row = staticOf(i); if (!row || r6 == null) return null;
    var s90 = row[scol('slope_p90_deg')], rel = row[scol('relief_m')]; if (s90 == null || rel == null) return null;
    var soil = f.soilw0_10[st.hour][i], c = function (x) { return Math.max(0, Math.min(1, x)); };
    var wet = soil == null ? 0.5 : c((soil - 0.15) / 0.25);
    return c(r6 / 50) * (0.4 + 0.3 * c(0.5 * s90 / 10 + 0.5 * rel / 1500) + 0.3 * wet);
  }
  function layerDefs() {
    var fieldLayer = function (id, label, name, max, rampName, unit, kind, tf) { return { id: id, label: label, max: max, ramp: rampName, unit: unit, kind: kind, field: name,
      get: function (i) { var v = F(name)[i]; return v == null ? null : (tf ? tf(v) : v); } }; };
    var stat = function (id, label, col, max, rampName, unit, kind) { return { id: id, label: label, max: max, ramp: rampName, unit: unit, kind: kind, isStatic: true,
      get: function (i) { var r = staticOf(i); return r ? r[scol(col)] : null; } }; };
    var cb = { id: 'cb', label: 'Heavy-rain-day outlook (1° score)', max: 0.4, ramp: 'rain', unit: 'score', kind: 'Uncalibrated daily score for the 1° cell containing each 0.25° cell (>= 64.5 mm/24 h proxy). Not a cloudburst; not a probability.', daily: true,
      get: function (i) { var c = st.store.cells[i], v = st.store.cb[(Math.floor(c[1] + 0.5)).toFixed(2) + ',' + (Math.floor(c[2] + 0.5)).toFixed(2)]; return v == null ? null : v; } };
    var tsm = { id: 'ts_model', label: 'Pan-India thunderstorm model', max: 1, ramp: 'seq', unit: 'score', kind: 'Model output (see its evaluation).', model: true,
      get: function (i) { var m = st.tsm; if (!m || !st.store.tsOk) return null; var j = st.store.tsIdx[st.store.cells[i][0]]; var L = m.meta.leads[Math.min(st.hour, m.meta.leads.length - 1)]; return j == null ? null : m.leads[String(L)][j]; } };
    var L = {
      thunderstorm: [
        fieldLayer('cape', 'CAPE', 'cape', 3000, 'seq', 'J/kg', 'GFS surface-based CAPE: instability guidance, not a probability.'),
        fieldLayer('cin', 'CIN (cap strength)', 'cin', 300, 'seq', 'J/kg (magnitude)', 'Convective inhibition: larger magnitude = stronger cap.', Math.abs),
        fieldLayer('lftx', 'Lifted index (instability)', 'lftx', 8, 'seq', 'K (−LI)', 'Negative lifted index = unstable; shown as −LI (0 = stable).', function (v) { return Math.max(0, -v); }),
        fieldLayer('k_index', 'K-index', 'k_index', 40, 'seq', '°C', 'Derived from GFS T/RH at 850/700/500 hPa. Convective-potential index.', function (v) { return Math.max(0, v); }),
        fieldLayer('total_totals', 'Total Totals', 'total_totals', 60, 'seq', '°C', 'Derived from GFS 850/500 hPa temperature and dew point.', function (v) { return Math.max(0, v); }),
        fieldLayer('pwat', 'Precipitable water', 'pwat', 70, 'rain', 'mm', 'GFS column water vapour.'),
        fieldLayer('shear_850_500', '850–500 hPa bulk shear', 'shear_850_500', 25, 'seq', 'm/s', 'Bulk wind difference 850–500 hPa. NOT 0–6 km shear.'),
        fieldLayer('w700', 'Ascent at 700 hPa', 'w700', 1.5, 'seq', 'Pa/s (−ω)', 'GFS vertical velocity; ascent shown positive.', function (v) { return Math.max(0, -v); }),
        fieldLayer('ws10', '10 m wind speed', 'ws10', 15, 'seq', 'm/s', 'GFS 10 m wind.'), fieldLayer('rh2m', '2 m relative humidity', 'rh2m', 100, 'rain', '%', 'GFS 2 m RH.'),
        fieldLayer('t2m_c', '2 m temperature', 't2m_c', 45, 'seq', '°C', 'GFS 2 m temperature.'), tsm],
      extreme_rainfall: [
        fieldLayer('precip_3h', 'GFS precipitation, 3 h amount', 'precip_3h', 40, 'rain', 'mm / 3 h', '3-hourly amount = difference of verified 0–f accumulations. Model guidance, not observed rain.'),
        fieldLayer('apcp_cum', 'GFS accumulated since init', 'apcp_cum', 100, 'rain', 'mm', 'Accumulated rain from model init to the selected hour (verified 0–f window).'), cb,
        fieldLayer('pwat', 'Precipitable water', 'pwat', 70, 'rain', 'mm', 'Moisture available for heavy rain.')],
      flash_flood: [
        { id: 'ff_screen', label: 'Flash-flood screening index (heuristic)', max: 1, ramp: 'flood', unit: 'index 0–1', kind: 'Heuristic screening: rain trigger x (terrain + soil wetness). NOT a probability and NOT validated.', get: ffIndex },
        fieldLayer('soilw0_10', 'Soil moisture 0–10 cm (model)', 'soilw0_10', 0.5, 'susc', 'm³/m³', 'GFS land-surface model state, not an observation.'),
        stat('slope_p90_deg', 'Slope (90th percentile)', 'slope_p90_deg', 20, 'terrain', '°', 'SRTM-derived at ~1.2 km; catchment-scale index.'),
        stat('relief_m', 'Terrain relief', 'relief_m', 2000, 'terrain', 'm', 'Max − min elevation inside the cell.'),
        stat('elev_mean_m', 'Elevation', 'elev_mean_m', 4000, 'terrain', 'm', 'SRTM-derived mean elevation.'),
        { id: 'gauge', label: 'Distance to nearest flood gauge', max: 100, ramp: 'susc', unit: 'km (nearer = brighter)', kind: 'INDOFLOODS gauges (106, historical events). Cells beyond 100 km have no hydrological context.', isStatic: true,
          get: function (i) { var r = staticOf(i); var d = r ? r[scol('nearest_gauge_km')] : null; return d == null ? null : 100 - d; } }]
    };
    return L;
  }
  function layerDef() { var l = layerDefs()[st.hazard]; return l.filter(function (x) { return x.id === st.layer; })[0] || l[0]; }

  /* ------------------------------------------------------------------ data load */
  function load() {
    var opts = { cache: 'no-store' };
    var j = function (u) { return fetch(u, opts).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).catch(function () { return null; }); };
    return Promise.all([j('data/india_grid_025.json'), j('data/india_places.json'), j('data/india_fields_latest.json'), j('data/india_static_025.json'), j('data/drift_live.json'),
                        j('data/hazard_evaluation.json'), j('assets/india_outline.geojson'), j('data/india_ts_model_latest.json')]).then(function (a) {
      st.live = a[4]; st.ev = a[5]; st.outline = a[6]; st.tsm = a[7];
      if (a[0]) {
        st.store = DL.create({ grid: a[0], places: a[1], fields: a[2], statics: a[3], live: a[4], tsmodel: a[7] });
        st.store.staticsDoc = a[3];
        if (st.store.fieldsOk) st.hour = st.store.defaultHour(Date.now());
      }
    });
  }

  /* ------------------------------------------------------------------ map */
  function poly(la, lo, h) { return [[[lo - h, la - h], [lo + h, la - h], [lo + h, la + h], [lo - h, la + h], [lo - h, la - h]]]; }
  function features(cellsLL, half) {
    return { type: 'FeatureCollection', features: cellsLL.map(function (c, i) { return { type: 'Feature', id: i, properties: { i: i, color: '#000', op: 0, reg: 0 }, geometry: { type: 'Polygon', coordinates: poly(c[1], c[2], half) } }; }) };
  }
  var geomMode = null;
  function paint() {
    if (!mapReady) return;
    var sim = st.mode === 'sim', src = map.getSource('cells'), ov = '', legend = '', stats = '';
    var cells = sim ? S.CELLS.map(function (c) { return [null, c[0], c[1]]; }) : (st.store ? st.store.cells : []);
    var data = features(cells, sim ? 0.5 : 0.125);
    if (sim) {
      var f = st.frame, h = S.HAZARDS[st.hazard];
      data.features.forEach(function (ft, i) { var lv = f.levels[i], x = f.values[i] / h.max; ft.properties.color = ramp('sim', x); ft.properties.op = lv === 0 ? 0.22 : 0.45 + 0.18 * lv; });
      legend = '<b>SIMULATED ' + esc(h.label) + '</b><div class="bar" style="background:' + gradient('sim') + '"></div><span>0 → ' + h.max + ' ' + esc(h.unit) + '</span><details><summary>About this view</summary><div class="small mute">Synthetic. Cells at WATCH / ALERT / WARNING: ' + f.counts[1] + ' / ' + f.counts[2] + ' / ' + f.counts[3] + '. The simulation grid is coarse (1°) and unrelated to the live 0.25° grid.</div></details>';
      map.getSource('centre').setData({ type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [f.centre[1], f.centre[0]] } }] });
    } else {
      map.getSource('centre').setData({ type: 'FeatureCollection', features: [] });
      if (st.loading) ov = 'Loading national data…';
      else if (!st.store) ov = 'The national grid could not be loaded. No values are shown.';
      else if (!st.store.fieldsOk) ov = 'Atmospheric fields are UNAVAILABLE: ' + esc((st.store.problems[0] || 'artifact failed verification')) + '. No values are shown.';
      else {
        var L = layerDef(), status = L.isStatic ? 'CURRENT' : L.daily ? 'CURRENT' : hourStatus(st.hour);
        if (L.model && !st.store.tsOk) ov = 'The pan-India thunderstorm model has no evaluated artifact yet: UNAVAILABLE.';
        else if (status === 'STALE' || status === 'INVALID_PROVENANCE' || status === 'UNAVAILABLE') ov = 'Model cycle is ' + status + ' — values are withheld, not shown as current.';
        if (!ov) {
          var rows = [], n = 0, area = 0, mx = null, mxc = null;
          data.features.forEach(function (ft, i) {
            var v = L.get(i);
            if (v == null) { ft.properties.op = 0; return; }
            var on = L.id === 'cape' ? v >= st.thr.cape : L.id === 'precip_3h' ? v >= st.thr.rain : true;
            ft.properties.color = ramp(L.ramp, Math.abs(v) / L.max); ft.properties.op = on ? 0.82 : 0.16; if (!on) ft.properties.color = '#33456b';
            rows.push(v);
            if (mx == null || v > mx) { mx = v; mxc = i; }
            if ((L.id === 'cape' || L.id === 'precip_3h') && on) { n++; area += 111 * 111 * 0.0625 * Math.cos(cells[i][1] * Math.PI / 180); }
          });
          if (st.region) data.features.forEach(function (ft, i) { ft.properties.reg = st.region.set[i] ? 1 : 0; });
          var unitNote = (L.isStatic || L.daily) ? '' : ' · ' + esc(hours()[st.hour] ? 'f' + hours()[st.hour].fhour + ' valid ' + hours()[st.hour].valid_time_utc.slice(5, 16).replace('T', ' ') + 'Z' : '');
          legend = '<b>' + esc(L.label) + unitNote + '</b><div class="bar" style="background:' + gradient(L.ramp) + '"></div><span>0 → ' + L.max + ' ' + esc(L.unit) + '</span>' +
            (status === 'PAST_VALID_TIME' ? '<div class="small na">Valid time has passed: model value for a past hour, not current.</div>' : '') +
            '<details><summary>About this layer</summary><div class="small mute">' + esc(L.kind) + '</div><div class="small mute" style="margin-top:4px">Grid: 0.25° cells (~28 km), one GFS grid point each, India land cells only (' + cells.length + '). Cell-level guidance, not point forecasts.</div></details>';
          stats = (n ? '<b>' + n + '</b> cells above your threshold (≈ ' + Math.round(area / 1000) + ' thousand km²) · ' : '') + (mx != null ? 'max ' + (+mx).toFixed(L.max <= 1 ? 2 : 0) + ' ' + esc(L.unit) + ' near ' + esc(labelOf(cells[mxc][0])) : '');
        }
      }
    }
    if (sim && st.region) st.region = null;
    src.setData(data);
    $('legend').innerHTML = legend + (stats ? '<div class="small" style="margin-top:4px">' + stats + '</div>' : ''); $('legend').style.display = (legend || stats) ? 'block' : 'none';
    $('overlay').style.display = ov ? 'flex' : 'none'; $('overlay').innerHTML = ov;
    var sidx = (!sim && st.sel && st.store) ? cellIdx(st.sel) : -1;
    if (map.getLayer('sel')) map.setFilter('sel', ['==', ['get', 'i'], sidx == null ? -1 : sidx]);
  }
  function initMap() {
    if (!window.maplibregl) { $('overlay').style.display = 'flex'; $('overlay').textContent = 'Map library could not be loaded.'; return; }
    map = new maplibregl.Map({
      container: 'map', center: [82.5, 22.5], zoom: window.innerWidth < 760 ? 3 : 3.8, minZoom: 2.5, maxZoom: 10, attributionControl: false,
      style: { version: 8, sources: { base: { type: 'raster', tileSize: 256, attribution: '© OpenStreetMap contributors; outline: Natural Earth (public domain)', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'] } },
               layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b1220' } }, { id: 'base', type: 'raster', source: 'base', paint: { 'raster-opacity': 0.5, 'raster-saturation': -0.85, 'raster-brightness-max': 0.4, 'raster-contrast': 0.2 } }] }
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
    if (window.ResizeObserver) new ResizeObserver(function () { map.resize(); }).observe($('mapwrap'));
    window.addEventListener('resize', function () { map.resize(); });
    // External basemap tiles are optional decoration; on failure hide them and say so (outline, land fill and cells are local).
    var tileFailures = 0;
    map.on('error', function (e) {
      var s = e && (e.sourceId || (e.source && e.source.id)); if (s !== 'base') return;
      if (++tileFailures === 3 && map.getLayer('base')) {
        map.setLayoutProperty('base', 'visibility', 'none');
        var n = $('maplabel'); if (n) n.insertAdjacentHTML('beforeend', '<span class="chip STALE" id="tilenote"><i></i>Basemap tiles unavailable · grid and outline unaffected</span>');
      }
    });
    var inited = false;
    function onReady() {
      if (inited) return; inited = true;
      map.addSource('cells', { type: 'geojson', data: features([], 0.125) });
      map.addSource('india', { type: 'geojson', data: st.outline || { type: 'FeatureCollection', features: [] } });
      map.addSource('centre', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'india-fill', type: 'fill', source: 'india', paint: { 'fill-color': '#16243f', 'fill-opacity': 0.85 } });
      map.addLayer({ id: 'fill', type: 'fill', source: 'cells', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['get', 'op'] } });
      map.addLayer({ id: 'region-line', type: 'line', source: 'cells', filter: ['==', ['get', 'reg'], 1], paint: { 'line-color': '#fff', 'line-width': 1, 'line-opacity': 0.9 } });
      map.addLayer({ id: 'india-line', type: 'line', source: 'india', paint: { 'line-color': '#e6f1ff', 'line-width': 1.4, 'line-opacity': 0.9 } });
      map.addLayer({ id: 'centre-halo', type: 'circle', source: 'centre', paint: { 'circle-radius': 16, 'circle-color': '#ffffff', 'circle-opacity': 0.18, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } });
      map.addLayer({ id: 'sel', type: 'line', source: 'cells', filter: ['==', ['get', 'i'], -1], paint: { 'line-color': '#ffffff', 'line-width': 2.5 } });
      map.on('click', function (e) {
        if (st.mode !== 'live' || !st.store) return;
        var r = st.store.resolve(e.lngLat.lat, e.lngLat.lng);
        if (r.covered) { selectCell(r.cell_id); return; }
        showNotCovered(r);
      });
      map.on('mousemove', function (e) { map.getCanvas().style.cursor = (st.mode === 'live' && st.store && st.store.resolve(e.lngLat.lat, e.lngLat.lng).covered) ? 'pointer' : ''; });
      mapReady = true; refresh();
    }
    if (map.isStyleLoaded()) onReady();
    map.on('style.load', onReady); map.on('load', onReady);
    setTimeout(function () { if (!inited && map.isStyleLoaded()) onReady(); }, 1200);
  }
  function setOutlineSource() { if (mapReady && st.outline) map.getSource('india').setData(st.outline); }

  /* ------------------------------------------------------------------ selection (location, region, compare) */
  function selectCell(cid, opts) {
    opts = opts || {};
    if (!st.store || st.store.idx[cid] == null) return;
    st.sel = cid; st.region = null;
    if (opts.label) st.label[cid] = opts.label;
    var c = st.store.cells[cellIdx(cid)];
    if (opts.fly !== false && map && mapReady) map.flyTo({ center: [c[2], c[1]], zoom: Math.max(map.getZoom(), 5.2), duration: 600 });
    setPanel(opts.panel || 'detail');
    paint(); renderDetail(); renderHazards(); renderCompare(); renderAlerts(); renderSources();
  }
  function showNotCovered(r) {
    st.sel = null; setPanel('detail');
    var n = r.nearest_covered_cell;
    $('p-detail').innerHTML = '<div class="warn l2"><b>Not covered by DRIFT</b><div class="small">' + esc(r.reason) + ' (' + r.query.lat.toFixed(2) + '°N ' + r.query.lon.toFixed(2) + '°E).</div>' +
      (n ? '<div class="row" style="margin-top:6px"><button class="btn" id="nc-go">Use nearest covered cell ' + n.lat + '°N ' + n.lon + '°E</button></div>' : '') + '</div><p class="small mute">DRIFT only represents cells that are at least 50% Indian land. It never shows another cell\'s values for an uncovered point.</p>';
    if (n) $('nc-go').onclick = function () { selectCell(n.cell_id); };
    ['hazards'].forEach(function (k) { $('p-' + k).innerHTML = '<p class="mute">Select a covered location.</p>'; });
    paint();
  }
  function selectRegion(kind, name) {
    var S0 = st.store, set = {}, ids = [], lat0 = 99, lat1 = -99, lon0 = 999, lon1 = -999;
    S0.cells.forEach(function (c, i) {
      var s = c[4] != null ? S0.states[c[4]] : null, d = c[5] != null ? S0.districts[c[5]] : null;
      if ((kind === 'state' && s === name) || (kind === 'district' && d && d.name === name)) { set[i] = true; ids.push(i); lat0 = Math.min(lat0, c[1]); lat1 = Math.max(lat1, c[1]); lon0 = Math.min(lon0, c[2]); lon1 = Math.max(lon1, c[2]); }
    });
    if (!ids.length) return;
    st.region = { kind: kind, name: name, set: set, ids: ids }; st.sel = null; setPanel('detail');
    if (map && mapReady) map.fitBounds([[lon0 - 0.3, lat0 - 0.3], [lon1 + 0.3, lat1 + 0.3]], { padding: 30, duration: 600 });
    paint(); renderDetail(); renderHazards();
  }
  function addCompare(cid) { if (st.compare.indexOf(cid) < 0 && st.compare.length < 4) st.compare.push(cid); renderCompare(); renderDetail(); }
  function removeCompare(cid) { st.compare = st.compare.filter(function (x) { return x !== cid; }); renderCompare(); renderDetail(); }

  /* ------------------------------------------------------------------ search */
  function bindSearch() {
    var q = $('q'), box = $('sug'), items = [], cur = -1;
    function close() { box.style.display = 'none'; cur = -1; }
    function pick(it) {
      close(); q.value = it.name;
      if (it.type === 'place') { st.label[it.cell_id] = it.name; setMode('live', true); selectCell(it.cell_id, { label: it.name }); }
      else { setMode('live', true); selectRegion(it.type, it.name); }
    }
    function show() {
      if (!st.store) return;
      items = st.store.search(q.value, 8);
      if (!items.length) { box.innerHTML = q.value.length >= 2 ? '<li class="na" role="option">No matching place, district or state</li>' : ''; box.style.display = q.value.length >= 2 ? 'block' : 'none'; return; }
      box.innerHTML = items.map(function (it, i) { return '<li role="option" data-i="' + i + '"><b>' + esc(it.name) + '</b> <span class="mute small">' + esc(it.type + (it.state ? ' · ' + it.state : '') + (it.population ? ' · pop ' + it.population.toLocaleString('en-IN') : '')) + '</span></li>'; }).join('');
      box.style.display = 'block';
      Array.prototype.forEach.call(box.querySelectorAll('li[data-i]'), function (li) { li.onmousedown = function (e) { e.preventDefault(); pick(items[+li.dataset.i]); }; });
    }
    q.oninput = show;
    q.onkeydown = function (e) {
      if (e.key === 'ArrowDown') { cur = Math.min(items.length - 1, cur + 1); hl(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { cur = Math.max(0, cur - 1); hl(); e.preventDefault(); }
      else if (e.key === 'Enter') { if (items[Math.max(cur, 0)]) pick(items[Math.max(cur, 0)]); }
      else if (e.key === 'Escape') close();
    };
    q.onblur = function () { setTimeout(close, 150); };
    function hl() { Array.prototype.forEach.call(box.querySelectorAll('li'), function (li, i) { li.setAttribute('aria-selected', i === cur); }); }
  }

  /* ------------------------------------------------------------------ panels: left */
  function renderHazardTabs() {
    var st2 = function (h) {
      if (st.mode === 'sim') return 'Simulated scenario';
      if (!st.store || !st.store.fieldsOk) return 'Unavailable';
      return { thunderstorm: st.store.tsOk ? 'Indicators + evaluated model' : 'Indicators · model unavailable', extreme_rainfall: 'GFS rain + daily outlook', flash_flood: 'Screening only · prediction unavailable' }[h];
    };
    $('hz').innerHTML = HZ_ORDER.map(function (h) { return '<button data-h="' + h + '" aria-pressed="' + (st.hazard === h) + '">' + esc(HZ_LABEL[h]) + '<small>' + esc(st2(h)) + '</small></button>'; }).join('');
    Array.prototype.forEach.call($('hz').querySelectorAll('button'), function (b) { b.onclick = function () { st.hazard = b.dataset.h; st.layer = layerDefs()[st.hazard][0].id; if (st.mode === 'sim') newScenario(); refresh(); }; });
  }
  function slider(id, label, min, max, step, v, suffix) { return '<label for="' + id + '">' + label + ': <b id="' + id + '-v">' + v + '</b>' + (suffix || '') + '</label><input id="' + id + '" type="range" min="' + min + '" max="' + max + '" step="' + step + '" value="' + v + '">'; }
  function bindSlider(id, fn) { var el = $(id); if (el) el.oninput = function () { $(id + '-v').textContent = this.value; fn(+this.value); }; }
  function renderControls() {
    var h = '<h2>' + (st.mode === 'sim' ? 'Scenario controls <span class="chip" style="color:var(--sim)">SIMULATION</span>' : 'Layers &amp; forecast hour') + '</h2>';
    if (st.mode === 'live') {
      var ls = layerDefs()[st.hazard];
      h += '<label for="lyr">Map layer</label><select id="lyr">' + ls.map(function (l) { return '<option value="' + l.id + '"' + (l.id === st.layer ? ' selected' : '') + '>' + esc(l.label) + '</option>'; }).join('') + '</select>';
      if (st.layer === 'cape') h += slider('t-cape', 'Highlight CAPE ≥', 0, 3000, 100, st.thr.cape, ' J/kg');
      if (st.layer === 'precip_3h') h += slider('t-rain', 'Highlight rain ≥', 1, 40, 1, st.thr.rain, ' mm/3 h');
      if (st.region) h += '<div class="small" style="margin-top:8px">Region: <b>' + esc(st.region.name) + '</b> (' + st.region.ids.length + ' cells) <button class="btn" id="reg-clear">Clear</button></div>';
    } else {
      var p = st.sc.params;
      h += '<div class="row" style="margin-top:6px"><button class="btn" data-ps="mild">Mild</button><button class="btn" data-ps="severe">Severe</button><button class="btn" data-ps="extreme">Extreme</button></div>' +
        slider('s-intensity', 'Intensity', 0.1, 1, 0.05, p.intensity) + slider('s-radius', 'Storm radius (°)', 0.6, 3, 0.1, p.radius) + slider('s-speed', 'Speed (°/h)', 0, 0.8, 0.05, p.speed) +
        slider('s-bearing', 'Heading (° from N)', 0, 359, 5, p.bearing) + (st.hazard !== 'thunderstorm' ? slider('s-moisture', 'Antecedent soil moisture', 0, 1, 0.05, p.moisture) : '') +
        '<div class="row" style="margin-top:10px"><button class="btn" id="rst">Reset</button></div><div class="small mute" style="margin-top:6px">' + esc(S.HAZARDS[st.hazard].story) + '</div>';
    }
    $('controls').innerHTML = h;
    if ($('lyr')) $('lyr').onchange = function () { st.layer = this.value; refresh(); };
    bindSlider('t-cape', function (v) { st.thr.cape = v; paint(); }); bindSlider('t-rain', function (v) { st.thr.rain = v; paint(); });
    if ($('reg-clear')) $('reg-clear').onclick = function () { st.region = null; refresh(); };
    ['intensity', 'radius', 'speed', 'bearing', 'moisture'].forEach(function (k) { bindSlider('s-' + k, function (v) { st.sc.params[k] = v; recompute(); paint(); renderAlerts(); renderDetail(); renderTimeline(); }); });
    Array.prototype.forEach.call($('controls').querySelectorAll('[data-ps]'), function (b) { b.onclick = function () { Object.assign(st.sc.params, S.PRESETS[b.dataset.ps]); resetRun(); refresh(); }; });
    if ($('rst')) $('rst').onclick = function () { newScenario(); refresh(); };
  }
  function renderHazardInfo() {
    var hz = st.hazard, h = '<h2>Pathway status</h2>';
    if (st.mode === 'sim') { $('hzinfo').innerHTML = h + '<p class="small"><b>' + esc(S.HAZARDS[hz].label) + '</b> — simulated field: ' + esc(S.HAZARDS[hz].unit) + '. Level thresholds are illustrative, not IMD/NDMA criteria.</p><p class="small mute">No real inputs, models or verification are involved in this mode.</p>'; return; }
    var ok = st.store && st.store.fieldsOk;
    var rows = {
      thunderstorm: [['Instability indicators (CAPE, CIN, LI, K, TT, PWAT, shear, ω)', ok ? 'AVAILABLE' : 'UNAVAILABLE'], ['Pan-India model', st.store && st.store.tsOk ? 'AVAILABLE_EVALUATED' : 'UNAVAILABLE'], ['Operational alerting', 'UNAVAILABLE']],
      extreme_rainfall: [['GFS rainfall: 3 h, accumulation, spatial extent', ok ? 'AVAILABLE' : 'UNAVAILABLE'], ['Heavy-rain-day outlook (1°, uncalibrated)', st.store && Object.keys(st.store.cb).length ? 'AVAILABLE' : 'UNAVAILABLE'], ['Short-duration extreme rainfall', 'UNAVAILABLE'], ['Cloudburst', 'UNAVAILABLE']],
      flash_flood: [['Rainfall trigger, soil wetness (model), terrain, gauge history', ok ? 'AVAILABLE' : 'UNAVAILABLE'], ['Screening index (heuristic)', ok ? 'UNVALIDATED' : 'UNAVAILABLE'], ['Flash-flood prediction', 'UNAVAILABLE']]
    }[hz];
    h += '<table>' + rows.map(function (r) { return '<tr><th>' + esc(r[0]) + '</th><td>' + chip(/UNAVAIL/.test(r[1]) ? 'UNAVAILABLE' : /UNVALID/.test(r[1]) ? 'STALE' : 'FRESH', r[1].replace(/_/g, ' ')) + '</td></tr>'; }).join('') + '</table>';
    if (hz === 'flash_flood') h += '<p class="small"><b>Live operation still needs:</b></p><ul class="small na"><li>observed flash-flood labels with time and place</li><li>observed soil moisture and live river levels</li><li>drainage / catchment hydrology beyond 106 gauges</li><li>a validated model and evaluation</li></ul>';
    if (hz === 'extreme_rainfall') h += '<p class="small mute">A heavy-rain day (≥ 64.5 mm/24 h) is not a cloudburst. Short-duration extreme rainfall needs hourly or sub-hourly rainfall labels that DRIFT does not have.</p>';
    $('hzinfo').innerHTML = h;
  }

  /* ------------------------------------------------------------------ panels: location */
  function spark(series, hourIdx, color, unit) {
    var vals = series.map(function (v) { return v == null ? 0 : v; }), mx = Math.max.apply(null, vals.concat([1e-9])), w = 100 / series.length;
    return '<svg viewBox="0 0 100 30" preserveAspectRatio="none" class="spark" role="img" aria-label="series">' + vals.map(function (v, i) {
      var h = Math.max(0.6, v / mx * 28); return '<rect x="' + (i * w + 1) + '" y="' + (30 - h) + '" width="' + (w - 2) + '" height="' + h + '" fill="' + (i === hourIdx ? 'var(--sim)' : color) + '" ' + (series[i] == null ? 'opacity=".25"' : '') + '><title>' + (series[i] == null ? 'unavailable' : series[i] + ' ' + unit) + '</title></rect>'; }).join('') + '</svg>';
  }
  function provBlock(rec) {
    var c = rec.cycle, h = rec.selected_hour;
    return '<table class="prov"><tr><th>Source</th><td>' + esc(c.source) + '</td></tr><tr><th>Spatial</th><td>0.25° cell · ' + esc(c.extraction) + '</td></tr>' +
      '<tr><th>Model init</th><td>' + esc(c.model_init_utc.slice(0, 16).replace('T', ' ')) + 'Z · age ' + c.age_hours + ' h</td></tr>' +
      '<tr><th>Valid time</th><td>f' + h.fhour + ' · ' + esc(h.valid_time_utc.slice(0, 16).replace('T', ' ')) + 'Z (' + esc(ist(h.valid_time_utc)) + ') ' + chip(h.status === 'CURRENT' ? 'FRESH' : h.status === 'PAST_VALID_TIME' ? 'STALE' : 'UNAVAILABLE', h.status.replace(/_/g, ' ')) + '</td></tr>' +
      '<tr><th>NWP input published</th><td>' + esc(h.published_at_utc.slice(0, 16).replace('T', ' ')) + 'Z <span class="small mute">(S3 Last-Modified, observed)</span></td></tr>' +
      '<tr><th>Acquired by DRIFT</th><td>' + esc(h.acquired_at_utc.slice(0, 16).replace('T', ' ')) + 'Z</td></tr>' +
      '<tr><th>Operational lead</th><td><span class="na">unavailable</span> <span class="small mute">(no DRIFT publication time recorded)</span></td></tr></table>';
  }
  var GROUPS = [
    ['Instability', [['cape', 'CAPE', 0], ['cin', 'CIN', 0], ['lftx', 'Lifted index', 1], ['k_index', 'K-index', 1], ['total_totals', 'Total Totals', 1], ['lapse_850_500', '850–500 hPa lapse rate', 2]]],
    ['Moisture', [['pwat', 'Precipitable water', 1], ['rh2m', '2 m RH', 0], ['d2m_c', '2 m dew point', 1], ['rh850', 'RH 850 hPa', 0], ['rh700', 'RH 700 hPa', 0], ['rh500', 'RH 500 hPa', 0]]],
    ['Wind & dynamics', [['ws10', '10 m wind speed', 1], ['wd10', '10 m wind from', 0], ['shear_850_500', '850–500 hPa bulk shear', 1], ['w850', 'ω 850 hPa', 2], ['w700', 'ω 700 hPa', 2], ['w500', 'ω 500 hPa', 2]]],
    ['Temperature & pressure', [['t2m_c', '2 m temperature', 1], ['mslp_hpa', 'MSLP', 1]]],
    ['Precipitation & soil', [['precip_3h', 'Rain, 3 h amount', 1], ['apcp_cum', 'Rain accumulated since init', 1], ['soilw0_10', 'Soil moisture 0–10 cm (model)', 3]]]
  ];
  var KIND = { RAW_MODEL_OUTPUT: 'raw model', DERIVED_PHYSICAL: 'derived', MODEL_DIAGNOSTIC: 'model state' };
  function renderDetail() {
    var el = $('p-detail');
    if (st.mode === 'sim') { renderSimDetail(el); return; }
    if (!st.store) { el.innerHTML = '<p class="na">National grid unavailable.</p>'; return; }
    if (st.region) { renderRegion(el); return; }
    if (!st.sel) { el.innerHTML = '<p class="mute">Search for a city, district or state, or click anywhere on the map, to see that location\'s data.</p><p class="small mute">' + st.store.cells.length + ' land cells cover India at 0.25° (≈28 km): the same resolution as the GFS source, so no cell implies finer detail than the model has.</p>'; return; }
    var rec = st.store.record(st.sel, st.hour, Date.now()), loc = rec.location, inCmp = st.compare.indexOf(st.sel) >= 0;
    var out = '<div class="row"><h3 style="margin:0">' + esc(labelOf(st.sel)) + '</h3><span class="spacer"></span><button class="btn" id="cmp-add"' + (inCmp || st.compare.length >= 4 ? ' disabled' : '') + '>' + (inCmp ? 'In compare' : 'Compare +') + '</button></div>' +
      '<div class="small mute">' + esc(loc.cell_id) + ' · ' + loc.lat + '°N ' + loc.lon + '°E · ' + esc([loc.district, loc.state].filter(Boolean).join(', ')) + (loc.admin_method === 'nearest' ? ' (nearest polygon)' : '') + ' · land ' + Math.round(loc.land_fraction * 100) + '%</div>';
    if (!st.store.fieldsOk) { el.innerHTML = out + '<p class="na">Atmospheric fields unavailable: ' + esc(rec.availability.problems.join('; ')) + '</p>' + staticTable(rec); bindDetail(); return; }
    out += provBlock(rec);
    GROUPS.forEach(function (g) {
      out += '<h2 style="margin-top:10px">' + g[0] + '</h2><table>' + g[1].map(function (r) {
        var f = rec.fields[r[0]]; return '<tr><th>' + esc(r[1]) + '</th><td>' + fmt(f.value, r[2], f.unit) + ' <span class="small mute">' + esc(KIND[f.kind] || f.kind) + '</span>' + (f.value != null && f.status !== 'CURRENT' ? ' ' + chip('STALE', f.status.replace(/_/g, ' ')) : '') + '</td></tr>'; }).join('') + '</table>';
    });
    out += '<div class="small mute" style="margin-top:6px">Unavailable everywhere: 0–6 km wind shear, observed rainfall, soil-moisture observations, radar/satellite fields at grid scale.</div>';
    out += '<h2 style="margin-top:10px">Series by forecast hour</h2><div class="small">CAPE</div>' + spark(rec.fields.cape.series, st.hour, 'var(--accent)', 'J/kg') + '<div class="small">Rain (3 h)</div>' + spark(rec.fields.precip_3h.series, st.hour, '#6ec3c9', 'mm') + '<div class="ticks">' + rec.cycle.hours.map(function (h) { return '<span>f' + h.fhour + '</span>'; }).join('') + '</div>';
    out += staticTable(rec);
    el.innerHTML = out; bindDetail();
  }
  function staticTable(rec) {
    var s = rec.static; if (s.status !== 'AVAILABLE') return '<p class="na small">Terrain/gauge context unavailable.</p>';
    return '<h2 style="margin-top:10px">Terrain &amp; hydrological context</h2><table>' +
      '<tr><th>Elevation (mean / min–max)</th><td>' + fmt(s.elev_mean_m, 0, 'm') + ' <span class="small mute">' + fmt(s.elev_min_m, 0) + '–' + fmt(s.elev_max_m, 0) + '</span></td></tr>' +
      '<tr><th>Relief</th><td>' + fmt(s.relief_m, 0, 'm') + '</td></tr><tr><th>Slope (mean / 90th pct)</th><td>' + fmt(s.slope_mean_deg, 1, '°') + ' / ' + fmt(s.slope_p90_deg, 1, '°') + '</td></tr>' +
      '<tr><th>Flood gauge in cell</th><td>' + (s.gauge_in_cell ? 'yes' : 'no') + '</td></tr>' +
      '<tr><th>Nearest gauge</th><td>' + (s.nearest_gauge_km == null ? '<span class="na">none within 100 km</span>' : s.nearest_gauge_km + ' km' + (s.nearest_gauge_id ? ' · ' + (s.nearest_gauge_events || 0) + ' recorded events (' + (s.nearest_gauge_rapid_events || 0) + ' rapid-onset)' : '')) + '</td></tr></table>' +
      '<div class="small mute">Source: SRTM-derived terrain (~1.2 km), INDOFLOODS historical events at 106 gauges. Static; not live. No river network or observed soil moisture.</div>';
  }
  function bindDetail() { if ($('cmp-add')) $('cmp-add').onclick = function () { addCompare(st.sel); }; }
  function renderRegion(el) {
    var R = st.region, f = fld(), ids = R.ids, hr = st.hour;
    var stat = function (name) { var v = ids.map(function (i) { return f.fields[name][hr][i]; }).filter(function (x) { return x != null; }); return v.length ? { n: v.length, min: Math.min.apply(null, v), max: Math.max.apply(null, v), mean: v.reduce(function (a, b) { return a + b; }, 0) / v.length } : null; };
    var rows = [['cape', 'CAPE', 'J/kg', 0], ['cin', 'CIN', 'J/kg', 0], ['pwat', 'Precipitable water', 'mm', 1], ['rh2m', '2 m RH', '%', 0], ['t2m_c', '2 m temperature', '°C', 1], ['k_index', 'K-index', '°C', 1], ['precip_3h', 'Rain 3 h', 'mm', 1], ['soilw0_10', 'Soil moisture (model)', 'm³/m³', 3]];
    var cape = ids.map(function (i) { return f.fields.cape[hr][i]; }), p3 = ids.map(function (i) { return f.fields.precip_3h[hr][i]; });
    el.innerHTML = '<h3>' + esc(R.name) + ' <span class="small mute">(' + R.kind + ', ' + ids.length + ' cells)</span></h3><div class="small mute">Regional summary of the selected forecast hour f' + hours()[hr].fhour + '. Cell-level values are in the Location tab after you click a cell.</div>' +
      '<table><tr><th></th><td><b>min</b></td><td><b>mean</b></td><td><b>max</b></td></tr>' + rows.map(function (r) { var s = stat(r[0]); return s ? '<tr><th>' + esc(r[1]) + ' <span class="mute small">' + r[2] + '</span></th><td>' + s.min.toFixed(r[3]) + '</td><td>' + s.mean.toFixed(r[3]) + '</td><td>' + s.max.toFixed(r[3]) + '</td></tr>' : '<tr><th>' + esc(r[1]) + '</th><td colspan="3"><span class="na">unavailable</span></td></tr>'; }).join('') + '</table>' +
      '<div class="small" style="margin-top:6px">Cells with CAPE ≥ 1000 J/kg: <b>' + cape.filter(function (v) { return v != null && v >= 1000; }).length + '</b> · cells with ≥ 10 mm rain in 3 h: <b>' + p3.filter(function (v) { return v != null && v >= 10; }).length + '</b></div>';
  }

  /* ------------------------------------------------------------------ panels: hazards, compare */
  function kv(rows) { return '<table>' + rows.map(function (r) { return '<tr><th>' + r[0] + '</th><td>' + r[1] + '</td></tr>'; }).join('') + '</table>'; }
  function renderHazards() {
    var el = $('p-hazards');
    if (st.mode === 'sim') { el.innerHTML = '<span class="chip" style="color:var(--sim)">SIMULATION</span><p class="small">Hazard panels describe real locations and are not shown in simulation mode.</p>'; return; }
    if (!st.store || !st.store.fieldsOk) { el.innerHTML = '<p class="na">Atmospheric fields unavailable.</p>'; return; }
    if (!st.sel) { el.innerHTML = '<p class="mute">Select a location to see its three hazard analyses.</p>'; return; }
    var rec = st.store.record(st.sel, st.hour, Date.now()), H = rec.hazards, ts = H.thunderstorm, rn = H.extreme_rainfall, ff = H.flash_flood, ind = ts.indicators;
    var card = function (id, title, body) { return '<details class="hz" ' + (st.hazard === id ? 'open' : '') + '><summary><b>' + title + '</b></summary>' + body + '</details>'; };
    var tsBody = '<div class="small mute">Target: thunderstorm reported at/near a METAR station within ±1.5 h of the valid time.</div>' + kv([
      ['Instability band <span class="mute small">(conventional CAPE classes)</span>', esc(ind.instability_band || 'unavailable') + ' ' + chip('STALE', 'not a probability')],
      ['CAPE / CIN', fmt(ind.cape, 0, 'J/kg') + ' / ' + fmt(ind.cin, 0, 'J/kg') + ' <span class="mute small">' + esc((ind.cap_strength || '').replace('_', ' ').toLowerCase()) + '</span>'],
      ['Lifted index / K-index / TT', fmt(ind.lifted_index, 1) + ' / ' + fmt(ind.k_index, 1) + ' / ' + fmt(ind.total_totals, 1)],
      ['Precipitable water', fmt(ind.pwat_mm, 1, 'mm')], ['850–500 hPa shear', fmt(ind.shear_850_500_ms, 1, 'm/s')], ['Ascent ω700', fmt(ind.vertical_velocity_700_pa_s, 2, 'Pa/s')]]) +
      '<div class="small">CAPE by forecast hour</div>' + spark(ind.cape_series, st.hour, 'var(--accent)', 'J/kg') +
      '<p class="small mute">CAPE: energy available to a rising parcel. CIN: the cap that must be overcome. Storms need CAPE, a weak or eroded cap, moisture and a trigger; shear organises them. These are inputs, not an outcome forecast.</p>';
    if (ts.model.status === 'AVAILABLE_EVALUATED') {
      var m = ts.model; tsBody += '<h2>Pan-India model (' + esc(m.model_version) + ')</h2>' + kv(Object.keys(m.leads).map(function (L) { return ['+' + L + ' h (valid ' + esc(m.leads[L].valid_time_utc.slice(5, 16).replace('T', ' ')) + 'Z)', m.leads[L].value == null ? '<span class="na">unavailable</span>' : (+m.leads[L].value).toFixed(3) + ' <span class="mute small">' + esc(m.value_type) + '</span>']; })) +
        '<div class="small mute">' + esc(m.calibrated ? 'Calibrated on the training data.' : 'Not calibrated.') + ' Evaluation: ' + esc(m.evaluation_ref) + '. Automated alerts stay disabled.</div>' + (function () { var best = Object.keys(m.leads).sort(function (a, b) { return (m.leads[b].value || 0) - (m.leads[a].value || 0); })[0], ex = m.explanation_top3_treeshap && m.explanation_top3_treeshap[best]; return ex ? '<div class="small"><b>Why (+' + best + ' h, top inputs, TreeSHAP):</b> ' + ex.map(function (x) { return esc(x.feature) + ' <span class="mute">(' + (x.contribution > 0 ? 'raises' : 'lowers') + ' ' + Math.abs(x.contribution).toFixed(2) + ')</span>'; }).join(', ') + '</div>' : ''; })();
    } else tsBody += '<h2>Pan-India model</h2><p class="small">' + chip('UNAVAILABLE', 'UNAVAILABLE') + ' ' + esc(ts.model.reason) + '</p>';
    var station = (st.live && st.live.hazards && st.live.hazards.thunderstorm && st.live.hazards.thunderstorm.station_evidence);
    var c = rec.location;
    if (station && station.metar && Math.abs(c.lat - 13.14) <= 0.3 && Math.abs(c.lon - 77.68) <= 0.3)
      tsBody += '<h2>Station evidence · VOBL</h2><div class="small">METAR ' + esc(station.metar.raw || '') + '<br><span class="mute">observed ' + esc(station.metar.obs_time || '') + ' · ' + agoText(station.metar.obs_time) + '</span></div>';
    var mg = rn.model_guidance, rnBody = kv([
      ['Heavy-rain-day outlook <span class="mute small">(1° cell, daily, uncalibrated)</span>', rn.heavy_rain_day_outlook.value == null ? '<span class="na">unavailable</span>' : (+rn.heavy_rain_day_outlook.value).toFixed(3) + ' ' + chip('STALE', 'score, not a probability')],
      ['Short-duration extreme rainfall', chip('UNAVAILABLE', 'UNAVAILABLE')], ['Cloudburst prediction', chip('UNAVAILABLE', 'UNAVAILABLE')]]) +
      '<div class="small mute">A heavy-rain day (≥ 64.5 mm/24 h) is a daily total. A cloudburst is a very intense, localised burst (commonly ≥ 100 mm/h). DRIFT has no hourly/sub-hourly rainfall labels or inputs, so it does not predict either short-duration extremes or cloudbursts.</div>';
    if (mg) rnBody += '<h2>GFS rainfall guidance (model, not observed)</h2>' + kv([['Max 3 h amount', fmt(mg.max_3h_mm, 1, 'mm') + (mg.max_3h_fhour != null ? ' <span class="mute small">ending f' + mg.max_3h_fhour + '</span>' : '')], ['Accumulated by last hour', fmt(mg.accumulated_24h_mm, 1, 'mm')], ['IMD-class of that total <span class="mute small">(applied to model rain)</span>', esc(mg.imd_24h_category || 'unavailable')],
      ['Spatial extent (5×5 cells ≈ ±0.5°)', mg.spatial_extent_within_half_degree[st.hour].cells_ge_10mm_3h + ' of ' + mg.spatial_extent_within_half_degree[st.hour].n_cells + ' cells ≥ 10 mm/3 h · ' + mg.spatial_extent_within_half_degree[st.hour].cells_ge_25mm_3h + ' ≥ 25 mm/3 h']]) +
      '<div class="small">3 h amounts by forecast hour</div>' + spark(mg.precip_3h_series_mm, st.hour, '#6ec3c9', 'mm');
    var si = ff.screening_index, cp = si && si.components, ffBody = '<p class="small">' + chip('UNAVAILABLE', 'Flash-flood prediction UNAVAILABLE') + '</p><div class="small mute">DRIFT has no validated flash-flood target, labels or hydrological inputs. It shows context and a heuristic screening index only.</div>';
    if (si) ffBody += kv([['Screening index <span class="mute small">(0–1, heuristic, unvalidated)</span>', (+si.value).toFixed(3)], ['Rain trigger (max 6 h ' + fmt(cp.rain_6h_max_mm, 1, 'mm') + ')', cp.rain_trigger], ['Terrain factor', cp.terrain_factor], ['Soil wetness (model state)', cp.soil_wetness == null ? '<span class="na">unavailable (held neutral)</span>' : cp.soil_wetness]]) + '<div class="small mute">' + esc(si.kind) + ' · ' + esc(si.formula) + '</div>';
    else ffBody += '<p class="na small">Screening index unavailable for this cell.</p>';
    ffBody += '<h2>Missing for live flash-flood prediction</h2><ul class="small na">' + ff.missing_inputs.map(function (m2) { return '<li>' + esc(m2) + '</li>'; }).join('') + '</ul>';
    el.innerHTML = '<div class="small mute" style="margin-bottom:6px">' + esc(labelOf(st.sel)) + ' · f' + rec.selected_hour.fhour + ' (valid ' + esc(rec.selected_hour.valid_time_utc.slice(5, 16).replace('T', ' ')) + 'Z) · ' + chip(rec.selected_hour.status === 'CURRENT' ? 'FRESH' : 'STALE', rec.selected_hour.status.replace(/_/g, ' ')) + '</div>' +
      card('thunderstorm', 'Severe thunderstorm', tsBody) + card('extreme_rainfall', 'Extreme rainfall', rnBody) + card('flash_flood', 'Flash flood', ffBody);
  }
  function renderCompare() {
    var el = $('p-compare');
    if (st.mode === 'sim') { el.innerHTML = '<p class="small mute">Compare works on real locations (LIVE mode).</p>'; return; }
    if (!st.store || !st.store.fieldsOk) { el.innerHTML = '<p class="na">Fields unavailable.</p>'; return; }
    if (!st.compare.length) { el.innerHTML = '<p class="mute">Select a location and press “Compare +” (up to 4) to see different regions side by side at the same forecast hour.</p>'; return; }
    var recs = st.compare.map(function (c) { return st.store.record(c, st.hour, Date.now()); });
    var rows = [['cape', 'CAPE', 0, 'J/kg'], ['cin', 'CIN', 0, 'J/kg'], ['k_index', 'K-index', 1, ''], ['pwat', 'PWAT', 1, 'mm'], ['rh2m', 'RH 2 m', 0, '%'], ['t2m_c', 'T 2 m', 1, '°C'], ['shear_850_500', 'Shear 850–500', 1, 'm/s'], ['precip_3h', 'Rain 3 h', 1, 'mm'], ['apcp_cum', 'Rain since init', 1, 'mm'], ['soilw0_10', 'Soil moisture', 3, '']];
    el.innerHTML = '<div class="small mute">Same forecast hour for all: f' + hours()[st.hour].fhour + ' · ' + esc(hours()[st.hour].valid_time_utc.slice(5, 16).replace('T', ' ')) + 'Z</div><div class="tscroll"><table class="cmp"><tr><th></th>' + recs.map(function (r) { return '<td><b>' + esc(labelOf(r.location.cell_id)) + '</b><div class="small mute">' + esc(r.location.state || '') + '</div><button class="btn" data-rm="' + esc(r.location.cell_id) + '" aria-label="Remove">✕</button></td>'; }).join('') + '</tr>' +
      rows.map(function (r) { return '<tr><th>' + r[1] + '</th>' + recs.map(function (x) { return '<td>' + fmt(x.fields[r[0]].value, r[2], r[3]) + '</td>'; }).join('') + '</tr>'; }).join('') +
      '<tr><th>Elevation</th>' + recs.map(function (x) { return '<td>' + fmt(x.static.elev_mean_m, 0, 'm') + '</td>'; }).join('') + '</tr><tr><th>Slope p90</th>' + recs.map(function (x) { return '<td>' + fmt(x.static.slope_p90_deg, 1, '°') + '</td>'; }).join('') + '</tr>' +
      '<tr><th>FF screening</th>' + recs.map(function (x) { var s = x.hazards.flash_flood.screening_index; return '<td>' + (s ? s.value : '<span class="na">n/a</span>') + '</td>'; }).join('') + '</tr></table></div>';
    Array.prototype.forEach.call(el.querySelectorAll('[data-rm]'), function (b) { b.onclick = function () { removeCompare(b.dataset.rm); }; });
  }

  /* ------------------------------------------------------------------ sources / evaluation / alerts */
  function renderSources() {
    var el = $('p-sources');
    if (st.mode === 'sim') { el.innerHTML = '<span class="chip" style="color:var(--sim)">SIMULATION</span><p class="small">Simulation mode has no data sources. Every value is generated in the browser from the controls.</p>'; return; }
    if (!st.store) { el.innerHTML = '<p class="na">Unavailable.</p>'; return; }
    var out = '';
    if (st.store.fieldsOk) {
      var m = st.store.fields.meta, state = hourStatus(0);
      out += '<div class="slot"><div class="row"><b>GFS 0.25° fields (' + st.store.cells.length + ' land cells)</b><span class="spacer"></span>' + chip(state === 'STALE' ? 'STALE' : 'FRESH', state === 'STALE' ? 'STALE' : 'VERIFIED') + '</div><div class="small mute">Cycle ' + esc(m.cycle_init_utc) + ' · age ' + ((Date.now() - Date.parse(m.cycle_init_utc)) / 36e5).toFixed(1) + ' h · built ' + esc(m.generated_at_utc) + '<br>' + esc(m.source) + '<br>' + esc(m.availability_note) + '</div>' +
        '<table class="small">' + m.files.map(function (f) { return '<tr><th>f' + f.fhour + ' · valid ' + esc(f.valid_time_utc.slice(11, 16)) + 'Z</th><td>published ' + esc(f.published_at_utc.slice(11, 16)) + 'Z · acquired ' + esc(f.acquired_at_utc.slice(11, 16)) + 'Z</td></tr>'; }).join('') + '</table></div>';
    } else out += '<div class="slot"><b>GFS fields</b> ' + chip('UNAVAILABLE', 'UNAVAILABLE') + '<div class="small na">' + esc(st.store.problems.join('; ')) + '</div></div>';
    out += '<div class="slot"><div class="row"><b>Terrain &amp; gauges</b><span class="spacer"></span>' + chip('STATIC', 'STATIC') + '</div><div class="small mute">' + esc(st.store.staticsDoc ? st.store.staticsDoc.meta.terrain_source : 'unavailable') + '<br>' + esc(st.store.staticsDoc ? st.store.staticsDoc.meta.gauge_source : '') + '</div></div>';
    var g = st.store.grid.meta;
    out += '<div class="slot"><b>Geography</b><div class="small mute">' + esc(g.cell_definition) + '<br>Boundary: ' + esc(g.boundary_source) + '<br>States: ' + esc(g.state_source) + '<br>Districts: ' + esc(g.district_source) + '<br>' + esc(g.admin_assignment) + '</div></div>';
    if (st.live) (st.live.sources || []).filter(function (s) { return s.id === 'cb_daily_outlook'; }).forEach(function (s) { var a = ageH(s.generated_at_utc); out += '<div class="slot"><div class="row"><b>' + esc(s.label) + '</b><span class="spacer"></span>' + chip(a != null && a <= 12 ? 'FRESH' : 'STALE', a != null && a <= 12 ? 'FRESH' : 'STALE') + '</div><div class="small mute">Generated ' + esc(s.generated_at_utc) + ' (' + agoText(s.generated_at_utc) + ') · 1° cells</div></div>'; });
    el.innerHTML = out + '<p class="small mute">' + esc(st.live ? st.live.disclaimer : '') + '</p>';
  }
  function renderEval() {
    var el = $('p-eval'), e = st.ev;
    if (st.mode === 'sim') { el.innerHTML = '<p class="small mute">Evaluation applies to LIVE models only. A simulation has no skill to measure.</p>'; return; }
    var out = '';
    if (st.tsm && st.tsm.meta && st.tsm.meta.evaluation_summary) out += '<div class="slot"><b>Pan-India thunderstorm model</b><div class="small">' + esc(st.tsm.meta.evaluation_summary) + '</div></div>';
    else out += '<div class="slot"><b>Pan-India thunderstorm model</b> ' + chip('UNAVAILABLE', 'UNAVAILABLE') + '<div class="small mute">No evaluated model artifact is published.</div></div>';
    if (!e) { el.innerHTML = out + '<p class="na">Evaluation file unavailable.</p>'; return; }
    var t = e.hazards.thunderstorm, r = e.hazards.extreme_rainfall, f = e.hazards.flash_flood, u = r.variants.uncalibrated, c = r.variants.calibrated;
    el.innerHTML = '<p class="small mute">' + esc(e.claims_policy) + '</p>' + out +
      '<div class="slot"><b>Thunderstorm (VOBL station only)</b><div class="small">' + esc(t.validation) + '</div><table><tr><th>Mean AUROC</th><td>' + t.summary.mean_auroc + '</td></tr><tr><th>Brier skill vs climatology</th><td>' + t.summary.mean_bss_vs_climatology + ' (positive in ' + t.summary.years_with_positive_bss + ' of ' + t.folds.length + ' years)</td></tr><tr><th>Mean POD / FAR</th><td>' + t.summary.mean_pod + ' / ' + t.summary.mean_far + '</td></tr></table><div class="small mute">' + t.limits.map(esc).join(' ') + '</div></div>' +
      '<div class="slot"><b>Heavy-rain day (pan-India, 1°)</b><div class="small">' + esc(r.scope) + '</div><table><tr><th>AUROC / PR-AUC (uncal.)</th><td>' + u.auroc + ' / ' + u.pr_auc + ' (no-skill PR ' + u.baseline_pr_auc_no_skill + ')</td></tr><tr><th>Brier skill vs climatology</th><td>uncal. ' + u.bss_vs_climatology + ' · cal. ' + c.bss_vs_climatology + '</td></tr><tr><th>POD / FAR (uncal.)</th><td>' + u.pod + ' / ' + u.far + '</td></tr><tr><th>POD / FAR (cal.)</th><td>' + c.pod + ' / ' + c.far + '</td></tr></table><div class="small mute">' + esc(r.summary.note) + '</div></div>' +
      '<div class="slot"><b>Flash flood</b><div class="small na">Not validated — no evaluation exists.</div><div class="small mute">' + f.limits.map(esc).join(' ') + '</div></div>';
  }
  function severityLegend() {
    var hz = st.hazard, A = S.ACTIONS[hz];
    return '<h2 style="margin-top:12px">Severity levels &amp; recommended actions</h2>' + [1, 2, 3].map(function (l) {
      return '<div class="warn l' + l + '"><b class="lvl' + l + '">' + S.LEVELS[l] + '</b><ul class="small">' + A[l].map(function (a) { return '<li>' + esc(a) + '</li>'; }).join('') + '</ul></div>';
    }).join('') + '<div class="small mute">Illustrative guidance. DRIFT live mode issues no levels; follow IMD / NDMA / district authority advisories.</div>';
  }
  function renderAlerts() {
    var el = $('p-alerts');
    if (st.mode === 'sim') {
      var upto = st.narr.slice(0, st.t + 1), cur = st.narr[st.t];
      var feed = upto.map(function (n) {
        var iss = n.issued ? '<div class="warn l' + n.issued.level + '"><b>' + esc(n.issued.headline) + '</b><div class="small">' + n.issued.cells + ' cells affected · issued at T+' + n.issued.atHour + ' h' + (n.inTargetBand ? ' (simulated lead inside the 2–6 h band)' : '') + '</div><ul class="small">' + n.issued.actions.map(function (a) { return '<li>' + esc(a) + '</li>'; }).join('') + '</ul>' + lifecycleHtml(n) + '</div>' : '';
        return '<div class="ev"><div class="row"><b>T+' + n.t + ' h</b>' + chip(n.peakLevel === 3 ? 'UNAVAILABLE' : n.peakLevel === 2 ? 'STALE' : n.peakLevel === 1 ? 'PARTIAL' : 'FRESH', n.stage) + '</div><div class="small mute">' + esc(n.headline) + ' · ' + n.cellsNewlyAffected + ' newly affected</div>' + iss + '</div>';
      }).join('');
      el.innerHTML = '<span class="chip" style="color:var(--sim)">SIMULATION</span> <span class="small">Event feed — nothing is sent; every stage below is simulated.</span>' +
        '<div class="small" style="margin:6px 0">Now: <b class="lvl' + cur.peakLevel + '">' + cur.peakName + '</b> · ' + (cur.counts[1] + cur.counts[2] + cur.counts[3]) + ' cells at WATCH or above</div>' + feed + severityLegend();
      return;
    }
    var a = st.live && st.live.alerts;
    el.innerHTML = '<div class="warn"><b>No DRIFT warning is active' + (st.sel ? ' for ' + esc(labelOf(st.sel)) : '') + '</b><div class="small mute">Automated alerting: ' + esc(a ? a.automated_dispatch : 'UNKNOWN') + ' · eligible ' + (a ? a.eligible : '–') + '</div></div>' +
      '<h2>Why alerts are withheld</h2><p class="small">No hazard model has been approved as alertable, and DRIFT records no forecast publication time, so no operational lead time exists. ' + esc(a ? a.withheld_explanation : '') + '</p>' +
      (a && a.withheld_reason_codes ? '<div class="small mute">' + a.withheld_reason_codes.map(esc).join(' · ') + '</div>' : '') +
      '<p class="small" style="margin-top:8px">Use SIMULATION to see the warning → recommended action → notification flow with synthetic data.</p>' + severityLegend();
  }
  function lifecycleHtml(n) {
    var shown = st.lcShown[n.t] == null ? 5 : st.lcShown[n.t], lc = n.issued.lifecycle.slice(0, shown);
    return '<div class="log">' + lc.map(function (e) { return '<div><span class="s">[SIM +' + e.atMinute + 'm]</span> ' + esc(e.stage) + ' — ' + esc(e.note) + ' (not sent)</div>'; }).join('') + (shown < 5 ? '<div class="mute">…</div>' : '') + '</div>';
  }
  function animateLifecycle(t) {
    var n = st.narr[t]; if (!n || !n.issued) return; st.lcShown[t] = 0;
    (function step() { if (st.lcShown[t] >= 5) return; st.lcShown[t]++; if (st.mode === 'sim') renderAlerts(); setTimeout(step, 380); })();
  }
  function renderSimDetail(el) {
    if (!st.frame) { el.innerHTML = ''; return; }
    var f = st.frame, hz = S.HAZARDS[st.hazard], c = f.centre;
    el.innerHTML = '<span class="chip" style="color:var(--sim)">SIMULATION</span><h3>Simulated storm</h3><table><tr><th>Hour</th><td>T+' + st.t + ' h (' + (f.inTargetBand ? 'inside' : 'outside') + ' the 2–6 h band)</td></tr><tr><th>Centre</th><td>' + c[0].toFixed(1) + '°N ' + c[1].toFixed(1) + '°E</td></tr><tr><th>Peak simulated value</th><td>' + Math.max.apply(null, Array.prototype.slice.call(f.values)).toFixed(hz.max <= 1 ? 2 : 0) + ' ' + esc(hz.unit) + '</td></tr><tr><th>Peak level</th><td class="lvl' + f.peakLevel + '">' + S.LEVELS[f.peakLevel] + '</td></tr></table><p class="small mute">All values are synthetic. Real locations are not shown in simulation mode.</p>';
  }

  /* ------------------------------------------------------------------ timeline */
  function renderTimeline() {
    var el = $('timeline'), W = S.HOURS, b = S.TARGET_BAND, left = (b[0] / W * 100) + '%', width = ((b[1] - b[0]) / W * 100) + '%';
    var ticks = '<div class="ticks">' + [0, 1, 2, 3, 4, 5, 6].map(function (i) { return '<span>T+' + i + 'h</span>'; }).join('') + '</div>';
    if (st.mode === 'sim') {
      var n = st.narr[st.t];
      var marks = st.narr.map(function (x) { return x.issued ? '<div class="mark l' + x.issued.level + '" style="left:' + (x.t / W * 100) + '%" title="' + esc(x.issued.kind) + ' issued"></div>' : ''; }).join('');
      el.innerHTML = '<div class="row"><b>Simulated event timeline</b>' + chip('STALE', 'SIMULATION') + '<span class="spacer"></span>' +
        '<button class="btn" id="stepb">Step</button><button class="btn simb" id="play">' + (st.playing ? 'Pause' : (st.t >= W ? 'Replay' : 'Run event')) + '</button>' +
        '<select id="spd" aria-label="Speed" style="width:auto"><option value="1800"' + (st.speed === 1800 ? ' selected' : '') + '>slow</option><option value="1100"' + (st.speed === 1100 ? ' selected' : '') + '>normal</option><option value="500"' + (st.speed === 500 ? ' selected' : '') + '>fast</option></select></div>' +
        '<div class="track"><div class="band" style="left:' + left + ';width:' + width + '"><span>2–6 h target band</span></div>' + marks + '<div class="cursor" style="left:' + (st.t / W * 100) + '%"></div></div>' +
        '<input type="range" id="tl" min="0" max="' + W + '" step="1" value="' + st.t + '" aria-label="Simulated hour" style="margin-top:6px">' + ticks +
        '<div class="small"><b>T+' + st.t + ' h</b> · stage <b>' + esc(n.stage) + '</b> · peak <b class="lvl' + n.peakLevel + '">' + n.peakName + '</b> · cells WATCH/ALERT/WARNING: ' + n.counts.slice(1).join(' / ') + ' · ' + n.cellsChanged + ' cells changed level this hour</div>';
      $('tl').oninput = function () { setT(+this.value, false); };
      $('play').onclick = togglePlay; $('stepb').onclick = function () { stopPlay(); setT(st.t >= W ? 0 : st.t + 1, true); };
      $('spd').onchange = function () { st.speed = +this.value; if (st.playing) { stopPlay(); togglePlay(); } };
      return;
    }
    var hs = hours();
    el.innerHTML = '<div class="row"><b>Forecast hour</b>' + chip('UNAVAILABLE', 'LEAD-SPECIFIC 2–6 h WARNING UNAVAILABLE') + '</div>' +
      '<div class="small mute" style="margin:6px 0">GFS model hours from cycle ' + (st.store && st.store.fieldsOk ? esc(st.store.fields.meta.cycle_init_utc.slice(0, 16).replace('T', ' ')) + 'Z' : '—') + '. Each chip drives the map and every panel. Status is recomputed from the clock.</div>' +
      '<div class="hours" role="group" aria-label="Forecast hour">' + hs.map(function (h, k) { var s = hourStatus(k); return '<button class="hr ' + s + '" data-hr="' + k + '" aria-pressed="' + (k === st.hour) + '"><b>f' + h.fhour + '</b><small>' + esc(h.valid_time_utc.slice(11, 16)) + 'Z</small><small>' + esc(ist(h.valid_time_utc)) + '</small><i></i></button>'; }).join('') + '</div>' +
      '<div class="small mute" style="margin-top:6px">● current &nbsp; ● valid time already passed (model value for a past hour) &nbsp; ● stale/invalid (withheld). DRIFT records no forecast publication time, so no operational lead time is claimed.</div>';
    Array.prototype.forEach.call(el.querySelectorAll('[data-hr]'), function (b2) { b2.onclick = function () { st.hour = +b2.dataset.hr; paint(); renderDetail(); renderHazards(); renderCompare(); renderTimeline(); }; });
  }
  function stopPlay() { st.playing = false; clearInterval(st.timer); }
  function togglePlay() {
    if (st.playing) { stopPlay(); renderTimeline(); return; }
    if (st.t >= S.HOURS) setT(0, false);
    st.playing = true;
    st.timer = setInterval(function () { if (st.t >= S.HOURS) { stopPlay(); renderTimeline(); return; } setT(st.t + 1, true); }, st.speed);
    renderTimeline();
  }
  function setT(t, animate) {
    st.t = t; recompute();
    if (animate && st.narr[t] && st.narr[t].issued && st.lcShown[t] == null) animateLifecycle(t);
    paint(); renderAlerts(); renderDetail(); renderTimeline();
  }

  /* ------------------------------------------------------------------ guided tour (drives the real UI) */
  function go(hz, layer) { if (st.mode !== 'live') setMode('live'); st.hazard = hz; st.layer = layer; refresh(); }
  function place(name) { var r = st.store.search(name, 3).filter(function (x) { return x.type === 'place'; })[0]; if (r) selectCell(r.cell_id, { label: r.name }); return r; }
  var TOUR = [
    { t: 'DRIFT at national scale', b: 'DRIFT covers India\'s land area with <b>0.25° cells (~28 km)</b>, the same resolution as the GFS source, so no cell implies finer detail than the model has. LIVE mode shows only integrity-checked data; anything DRIFT cannot support stays <b>unavailable</b>.',
      a: function () { go('thunderstorm', 'cape'); st.sel = null; st.region = null; refresh(); if (map) map.fitBounds([[68.5, 6.5], [97.8, 37.2]], { padding: 20, duration: 600 }); } },
    { t: 'Every location has its own data', b: 'Select <b>Mumbai</b>: values, units, kind (raw model / derived / model state), valid time, model init and the time the NWP file was published are shown for <i>that cell</i>. Now compare with Srinagar or Chennai: CAPE, humidity, terrain and rainfall change because the underlying data changes.',
      a: function () { go('thunderstorm', 'cape'); place('Mumbai'); } },
    { t: 'Thunderstorm indicators and what they mean', b: '<b>CAPE</b> is energy available to a rising parcel; <b>CIN</b> is the cap that must be overcome; K-index and Total Totals summarise lapse rate and moisture; <b>shear</b> organises storms. These are inputs, not an outcome forecast. The pan-India thunderstorm model is shown only if an evaluated artifact exists.',
      a: function () { go('thunderstorm', 'k_index'); setPanel('hazards'); } },
    { t: 'Extreme rainfall is not cloudburst prediction', b: 'GFS 3-hourly rainfall (differences of verified accumulations), accumulation and spatial extent are model guidance. The daily <b>heavy-rain-day</b> score (≥ 64.5 mm/24 h, 1° cells) is <b>not</b> a cloudburst forecast; short-duration extremes and cloudbursts are <b>unavailable</b> (no hourly rainfall labels).',
      a: function () { go('extreme_rainfall', 'precip_3h'); place('Guwahati'); setPanel('hazards'); } },
    { t: 'Flash floods: context, not prediction', b: 'DRIFT shows rainfall trigger, model soil wetness, SRTM slope/relief and gauge history, plus a <b>heuristic screening index</b> that is not a probability. Prediction is unavailable: no flood labels, no observed soil moisture or river levels, hydrology only near 106 gauges.',
      a: function () { go('flash_flood', 'ff_screen'); place('Shillong'); setPanel('hazards'); } },
    { t: 'Compare regions', b: 'Add several places to <b>Compare</b> to see different Indian regions side by side at the same forecast hour.',
      a: function () { go('thunderstorm', 'cape'); ['Delhi', 'Chennai', 'Jaipur'].forEach(function (n) { var r = st.store.search(n, 3).filter(function (x) { return x.type === 'place'; })[0]; if (r) { st.label[r.cell_id] = r.name; addCompare(r.cell_id); } }); setPanel('compare'); } },
    { t: 'Simulation: an extreme event', b: '<b>SIMULATION mode uses synthetic data only.</b> The amber banner and SIMULATION badges cannot be mistaken for live output. Press <b>Run event</b>: conditions intensify, cells change level, and WATCH → ALERT → WARNING are issued.',
      a: function () { setMode('sim'); Object.assign(st.sc.params, S.PRESETS.extreme); resetRun(); refresh(); setPanel('alerts'); if (map) map.fitBounds([[68.5, 6.5], [97.8, 37.2]], { padding: 20, duration: 600 }); } },
    { t: 'Simulation: warning and notification lifecycle', b: 'Each issued level lists affected cells and recommended actions, then a <b>simulated</b> lifecycle (eligible → recorded → dispatch attempted → provider accepted → delivery confirmed). No network request is made and no recipient exists. Real DRIFT alerting stays disabled.',
      a: function () { setMode('sim'); Object.assign(st.sc.params, S.PRESETS.extreme); resetRun(); refresh(); setPanel('alerts'); st.speed = 700; togglePlay(); } }
  ];
  function tourGo(i) {
    var el = $('tour'); if (!el) return;
    if (i < 0 || i >= TOUR.length) { stopPlay(); el.style.display = 'none'; return; }
    var s = TOUR[i]; el.style.display = 'block';
    el.innerHTML = '<div class="row"><b>Guided tour · ' + (i + 1) + ' / ' + TOUR.length + '</b><span class="spacer"></span><button class="btn" id="tour-x" aria-label="Close tour">✕</button></div><h3>' + esc(s.t) + '</h3><p class="small">' + s.b + '</p><div class="row"><button class="btn" id="tour-b"' + (i === 0 ? ' disabled' : '') + '>Back</button><button class="btn primary" id="tour-n">' + (i === TOUR.length - 1 ? 'Finish' : 'Next') + '</button></div>';
    $('tour-x').onclick = function () { tourGo(-1); }; $('tour-b').onclick = function () { tourGo(i - 1); }; $('tour-n').onclick = function () { tourGo(i + 1); };
    stopPlay(); try { s.a(); } catch (e) { /* a tour step must never break the app */ }
  }

  /* ------------------------------------------------------------------ mode / refresh */
  function resetRun() { stopPlay(); st.t = 0; st.lcShown = {}; recompute(); }
  function newScenario() { st.sc = S.createScenario(st.hazard); resetRun(); }
  function recompute() { st.frame = S.frame(st.sc, st.t); st.narr = S.narrative(st.sc); }
  function worst() {
    if (st.loading) return 'LOADING';
    if (!st.store || !st.store.fieldsOk) return 'UNAVAILABLE';
    var s = hourStatus(0); return s === 'STALE' || s === 'INVALID_PROVENANCE' ? 'STALE' : 'FRESH';
  }
  function banner() {
    var b = $('banner');
    if (st.mode === 'sim') { b.className = 'sim'; b.textContent = 'SIMULATION MODE — every map value, warning and notification here is synthetic. It is not a forecast and nothing is ever sent.'; }
    else { b.className = 'live'; b.textContent = 'LIVE MODE — only integrity-checked data is shown; unavailable data stays unavailable. Advisory model guidance, not IMD/NDMA warnings.'; }
    $('simmark').style.display = st.mode === 'sim' ? 'block' : 'none';
    var keep = $('tilenote') ? $('tilenote').outerHTML : '';
    $('maplabel').innerHTML = (st.mode === 'sim' ? '<span class="chip" style="color:var(--sim)"><i style="background:var(--sim)"></i>SIMULATION</span>' : '<span class="chip ' + worst() + '"><i></i>LIVE · ' + worst() + '</span>') + keep;
    var f = $('fresh'); f.className = 'chip ' + (st.mode === 'sim' ? 'STALE' : worst()); f.querySelector('span').textContent = st.mode === 'sim' ? 'Synthetic data' : 'Data: ' + worst();
  }
  function setMode(m, quiet) {
    if (st.mode === m && quiet) return;
    st.mode = m; stopPlay();
    $('m-live').setAttribute('aria-pressed', m === 'live'); $('m-sim').setAttribute('aria-pressed', m === 'sim');
    if (m === 'sim') { st.region = null; newScenario(); }
    else st.layer = layerDefs()[st.hazard].some(function (l) { return l.id === st.layer; }) ? st.layer : layerDefs()[st.hazard][0].id;
    history.replaceState(null, '', '#' + m); refresh();
  }
  function setPanel(p) {
    st.panel = p;
    Array.prototype.forEach.call(document.querySelectorAll('.rtabs button'), function (b) { b.setAttribute('aria-pressed', b.dataset.p === p); });
    ['detail', 'hazards', 'compare', 'alerts', 'sources', 'eval'].forEach(function (k) { $('p-' + k).classList.toggle('on', k === p); });
  }
  function refresh() { banner(); renderHazardTabs(); renderControls(); renderHazardInfo(); renderDetail(); renderHazards(); renderCompare(); renderAlerts(); renderSources(); renderEval(); renderTimeline(); paint(); }

  function boot() {
    if ($('tour-start')) $('tour-start').onclick = function () { tourGo(0); };
    $('m-live').onclick = function () { setMode('live'); }; $('m-sim').onclick = function () { setMode('sim'); };
    Array.prototype.forEach.call(document.querySelectorAll('.rtabs button'), function (b) { b.onclick = function () { setPanel(b.dataset.p); }; });
    bindSearch(); initMap();
    load().then(function () { st.loading = false; st.layer = layerDefs()[st.hazard][0].id; setOutlineSource(); if (location.hash === '#sim') setMode('sim'); else refresh(); });
    window.__drift = { getMap: function () { return map; }, ready: function () { return mapReady; }, state: st, setMode: setMode, selectCell: selectCell, selectRegion: selectRegion, refresh: refresh, setT: setT, tour: tourGo, labelOf: labelOf, layerValues: function (id) { var L = layerDefs()[st.hazard].filter(function (x) { return x.id === id; })[0]; return st.store.cells.map(function (c, i) { return L.get(i); }); } };
  }
  boot();
})();
