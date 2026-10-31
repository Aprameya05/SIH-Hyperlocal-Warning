/* DRIFT command centre UI.
 * LIVE mode renders only the integrity-checked feed (data/drift_live.json): NWP guidance fields, terrain, station
 * evidence, a daily heavy-rain-day outlook. Unsupported predictions stay "unavailable".
 * SIMULATION mode renders the synthetic engine (assets/drift_sim.js). The two never share data. Nothing here sends a
 * notification or contacts any recipient. */
(function () {
  'use strict';
  var S = window.DriftSim;
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; }); };
  var STALE_H = 12;
  var HZ_ORDER = ['thunderstorm', 'extreme_rainfall', 'flash_flood'];

  var st = {
    mode: 'live', hazard: 'thunderstorm', layer: 'cape', hour: 1, onlyIndia: true, sel: null, panel: 'detail',
    thr: { cape: 1000, cinLimit: 100, rain: 10, rainTrig: 10, susc: 0.6 }, research: false,
    loading: true, live: null, ev: null, outline: null, cols: {}, cells: [],
    sc: null, t: 3, frame: null, narr: [], playing: false, timer: null, speed: 1100, lcShown: {}, inIndia: {}
  };
  var map = null, mapReady = false;

  /* ------------------------------------------------------------------ colour ramps */
  var RAMPS = {
    seq: [[0, '#1b2a4a'], [.25, '#2b6cb0'], [.5, '#f6d860'], [.75, '#f08a3c'], [1, '#d62839']],
    rain: [[0, '#14304d'], [.3, '#1f78b4'], [.6, '#6ec3c9'], [.8, '#f6d860'], [1, '#d62839']],
    terrain: [[0, '#1d3b2a'], [.4, '#5b8c3a'], [.7, '#c9a14a'], [1, '#f1e9d2']],
    susc: [[0, '#1b2a4a'], [.5, '#5aa6c9'], [1, '#e8f6ff']],
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

  /* ------------------------------------------------------------------ helpers */
  function ageH(iso) { var d = iso ? Date.parse(iso) : NaN; return isNaN(d) ? null : (Date.now() - d) / 36e5; }
  function srcState(s) {
    if (s.state === 'STATIC') return 'STATIC';
    var a = ageH(s.generated_at_utc); if (s.state === 'UNAVAILABLE' || s.integrity_ok === false || a == null) return 'UNAVAILABLE';
    return a <= STALE_H ? 'FRESH' : 'STALE';
  }
  function sourceById(id) { return (st.live && st.live.sources || []).filter(function (s) { return s.id === id; })[0]; }
  function fmt(v, d, u) { return v == null ? '<span class="na">unavailable</span>' : (+v).toFixed(d == null ? 0 : d) + (u ? ' ' + u : ''); }
  function agoText(iso) { var a = ageH(iso); if (a == null) return 'time unknown'; var f = a < 0 ? 'in ' + (-a).toFixed(1) + ' h' : a.toFixed(1) + ' h ago'; return f; }
  function chip(cls, text) { return '<span class="chip ' + esc(cls) + '"><i></i>' + esc(text) + '</span>'; }
  function col(name) { var i = st.cols[name]; return i == null ? -1 : i; }
  function val(cell, name) { var i = col(name); return i < 0 ? null : cell[i]; }
  function cellArea(lat) { return 111 * 111 * Math.cos(lat * Math.PI / 180); }

  /* point-in-India (ray casting on the simplified outline); a cell counts if its centre or any corner is inside */
  function inRing(lon, lat, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if (((yi > lat) !== (yj > lat)) && (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi)) inside = !inside;
    }
    return inside;
  }
  function inIndiaPoint(lat, lon) {
    if (!st.outline) return true;
    var g = st.outline.features[0].geometry, polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
    for (var p = 0; p < polys.length; p++) {
      if (!inRing(lon, lat, polys[p][0])) continue;
      var hole = false; for (var h = 1; h < polys[p].length; h++) if (inRing(lon, lat, polys[p][h])) hole = true;
      if (!hole) return true;
    }
    return false;
  }
  function cellInIndia(lat, lon) {
    var o = [[0, 0], [.5, .5], [.5, -.5], [-.5, .5], [-.5, -.5]];
    for (var k = 0; k < o.length; k++) if (inIndiaPoint(lat + o[k][0], lon + o[k][1])) return true;
    return false;
  }
  function visible(lat, lon, key) {
    if (!st.onlyIndia) return true;
    if (st.inIndia[key] === undefined) st.inIndia[key] = cellInIndia(lat, lon);
    return st.inIndia[key];
  }

  /* ------------------------------------------------------------------ live layer catalogue (honest semantics only) */
  function hourName() { var h = st.live && st.live.grid.nwp_hours[st.hour]; return h ? 'f' + h.forecast_hour : ''; }
  function layerDefs() {
    var H = st.hour;
    return {
      thunderstorm: [
        { id: 'cape', label: 'CAPE (GFS guidance)', f: function (c) { return val(c, 'cape_' + H); }, max: 3000, ramp: 'seq', unit: 'J/kg', hl: function (v) { return v >= st.thr.cape; },
          kind: 'NWP instability guidance at one model hour. Not a thunderstorm probability.' },
        { id: 'cin', label: 'CIN (GFS guidance)', f: function (c) { var v = val(c, 'cin_' + H); return v == null ? null : Math.abs(v); }, max: 300, ramp: 'seq', unit: 'J/kg (magnitude)',
          kind: 'Convective inhibition: larger magnitude = stronger cap that suppresses storms.' },
        { id: 'screen', label: 'Instability screening (CAPE high, CIN weak)', f: function (c) { var a = val(c, 'cape_' + H), b = val(c, 'cin_' + H); return (a == null || b == null) ? null : a; },
          max: 3000, ramp: 'seq', unit: 'J/kg', hl: function (v, c) { var b = val(c, 'cin_' + H); return v >= st.thr.cape && b != null && b >= -st.thr.cinLimit; },
          kind: 'Cells where YOUR thresholds are met: CAPE >= ' + st.thr.cape + ' J/kg and CIN weaker than -' + st.thr.cinLimit + ' J/kg. A screening aid, not a forecast.' }],
      extreme_rainfall: [
        { id: 'qpe', label: 'GFS precipitation intensity', f: function (c) { return val(c, 'qpe_' + H); }, max: 40, ramp: 'rain', unit: 'mm', hl: function (v) { return v >= st.thr.rain; },
          kind: 'GFS precipitation at one model hour (accumulation window as supplied by GFS). Model guidance, not observed rain.' },
        { id: 'cb', label: 'Heavy-rain-day outlook (daily)', f: function (c) { return val(c, 'cb_daily_outlook'); }, max: 0.4, ramp: 'rain', unit: 'score',
          kind: 'Uncalibrated model score for the whole forecast day (>= 64.5 mm/24 h proxy). Not a cloudburst; not a probability.' },
        { id: 'extent', label: 'Spatial extent above your rainfall threshold', f: function (c) { return val(c, 'qpe_' + H); }, max: 40, ramp: 'rain', unit: 'mm', hl: function (v) { return v >= st.thr.rain; },
          kind: 'Only cells at or above ' + st.thr.rain + ' mm are coloured; statistics below summarise their extent.' }],
      flash_flood: [
        { id: 'trigger', label: 'Rainfall trigger on susceptible terrain', f: function (c) { return val(c, 'qpe_' + H); }, max: 40, ramp: 'rain', unit: 'mm',
          hl: function (v, c) { var s = val(c, 'ff_susceptibility_proxy'); return v >= st.thr.rainTrig && s != null && s >= st.thr.susc; },
          kind: 'GFS rain >= ' + st.thr.rainTrig + ' mm on cells whose DEM flood-susceptibility proxy >= ' + st.thr.susc + '. Context screening only, NOT a flood forecast.' },
        { id: 'susc', label: 'DEM flood-susceptibility proxy', f: function (c) { return val(c, 'ff_susceptibility_proxy'); }, max: 1, ramp: 'susc', unit: 'index',
          kind: 'Derived index from SRTM elevation/slope. Static terrain context, not a prediction.' },
        { id: 'elev', label: 'Elevation (SRTM)', f: function (c) { return val(c, 'elevation_m'); }, max: 3000, ramp: 'terrain', unit: 'm', kind: 'Static terrain from SRTM tiles.' },
        { id: 'hydro', label: 'Gauge hydrology coverage', f: function (c) { return val(c, 'has_hydrology') ? 1 : null; }, max: 1, ramp: 'susc', unit: '',
          kind: 'Only cells with real INDOFLOODS gauge catchment data are coloured: the rest have NO hydrology.' }]
    };
  }
  function layerDef() { var l = layerDefs()[st.hazard]; return l.filter(function (x) { return x.id === st.layer; })[0] || l[0]; }

  /* ------------------------------------------------------------------ data load */
  function load() {
    var opts = { cache: 'no-store' };
    var j = function (u) { return fetch(u, opts).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).catch(function () { return null; }); };
    return Promise.all([j('data/drift_live.json'), j('data/hazard_evaluation.json'), j('assets/india_outline.geojson')]).then(function (a) {
      st.live = a[0]; st.ev = a[1]; st.outline = a[2];
      if (st.live && st.live.mode === 'LIVE_OPERATIONAL' && st.live.grid && st.live.grid.cells.length) {
        st.cells = st.live.grid.cells; st.cols = {}; st.live.grid.columns.forEach(function (n, i) { st.cols[n] = i; });
        st.hour = Math.max(0, st.live.grid.nwp_hours.length - 1);
      } else { st.cells = []; if (st.live) st.live = Object.assign({}, st.live); }
    });
  }

  /* ------------------------------------------------------------------ map */
  function poly(la, lo, h) { h = h || 0.5; return [[[lo - h, la - h], [lo + h, la - h], [lo + h, la + h], [lo - h, la + h], [lo - h, la - h]]]; }
  function features(cellsLL) {
    return { type: 'FeatureCollection', features: cellsLL.map(function (c, i) { return { type: 'Feature', id: i, properties: { i: i, color: '#000', op: 0 }, geometry: { type: 'Polygon', coordinates: poly(c[0], c[1]) } }; }) };
  }
  function layerStats(L, rows) {
    var n = 0, area = 0, mx = null, mxc = null, tot = 0;
    rows.forEach(function (r) {
      if (r.v == null) return; tot++;
      if (mx == null || r.v > mx) { mx = r.v; mxc = r.c; }
      if (L.hl && L.hl(r.v, r.c)) { n++; area += cellArea(r.c[0]); }
    });
    return { n: n, area: area, max: mx, maxCell: mxc, total: tot };
  }
  function paint() {
    if (!mapReady) return;
    var simMode = st.mode === 'sim', cells = simMode ? S.CELLS : st.cells, src = map.getSource('cells');
    var ov = '', legend = '', stats = '', data = features(cells);
    if (simMode) {
      var f = st.frame, h = S.HAZARDS[st.hazard];
      data.features.forEach(function (ft, i) {
        var lv = f.levels[i], x = f.values[i] / h.max;
        ft.properties.color = ramp('sim', x);
        ft.properties.op = !visible(cells[i][0], cells[i][1], 's' + i) ? 0 : (lv === 0 ? 0.22 : 0.45 + 0.18 * lv);
      });
      var cnt = f.counts;
      legend = '<b>SIMULATED ' + esc(h.label) + '</b><div class="bar" style="background:' + gradient('sim') + '"></div><span>0 → ' + h.max + ' ' + esc(h.unit) + '</span><br><span class="mute">Synthetic. Cells at WATCH / ALERT / WARNING: ' + cnt[1] + ' / ' + cnt[2] + ' / ' + cnt[3] + '</span>';
      map.getSource('centre').setData({ type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [f.centre[1], f.centre[0]] } }] });
    } else if (!st.live || !st.cells.length) {
      ov = st.loading ? 'Loading live feed…' : 'LIVE feed unavailable. No values are shown.';
      map.getSource('centre').setData({ type: 'FeatureCollection', features: [] });
    } else {
      map.getSource('centre').setData({ type: 'FeatureCollection', features: [] });
      var L = layerDef(), gs = sourceById('gfs_grid'), cs = sourceById('cb_daily_outlook');
      var needed = L.id === 'cb' ? cs : (st.hazard === 'flash_flood' && (L.id === 'susc' || L.id === 'elev' || L.id === 'hydro')) ? sourceById('terrain_hydrology') : gs;
      var state = needed ? srcState(needed) : 'UNAVAILABLE';
      if (state !== 'FRESH' && state !== 'STATIC') ov = 'Source is ' + state + ' — values are withheld, not shown as current.';
      var rs = st.hazard === 'flash_flood' && st.research, rows = [];
      data.features.forEach(function (ft, i) {
        var c = cells[i], v = null;
        if (!visible(c[0], c[1], 'l' + i)) { ft.properties.op = 0; return; }
        if (rs) v = val(c, 'ff_research_pu_rank'); else v = L.f(c);
        if (ov || v == null) { ft.properties.op = 0.0; return; }
        rows.push({ v: v, c: c });
        var x = Math.abs(v) / L.max, on = L.hl ? L.hl(v, c) : true;
        ft.properties.color = ramp(rs ? 'res' : L.ramp, x);
        ft.properties.op = on ? 0.78 : 0.16;
        if (!on) ft.properties.color = '#33456b';
      });
      if (!ov) {
        legend = rs ? '<b>RESEARCH ONLY</b> — PU-learning ranking (not a probability)<div class="bar" style="background:' + gradient('res') + '"></div><span>low → high rank</span>' :
          '<b>' + esc(L.label) + (L.id.match(/^(cape|cin|screen|qpe|extent|trigger)$/) ? ' · ' + esc(hourName()) : '') + '</b><div class="bar" style="background:' + gradient(L.ramp) + '"></div><span>0 → ' + L.max + ' ' + esc(L.unit) + '</span><details><summary>About this layer</summary><div class="small mute">' + esc(L.kind) + '</div>@@COV@@</details>';
        var ls = layerStats(L, rows);
        if (L.hl) stats = '<b>' + ls.n + '</b> of ' + ls.total + ' cells meet the highlight rule (≈ ' + Math.round(ls.area / 1000).toLocaleString('en-IN') + ' thousand km²)' + (ls.max != null ? ' · max ' + (+ls.max).toFixed(L.max <= 1 ? 2 : 0) + ' ' + esc(L.unit) + ' at ' + ls.maxCell[0] + '°N ' + ls.maxCell[1] + '°E' : '');
        else if (ls.max != null) stats = 'Max ' + (+ls.max).toFixed(L.max <= 1 ? 2 : 0) + ' ' + esc(L.unit) + ' at ' + ls.maxCell[0] + '°N ' + ls.maxCell[1] + '°E · ' + ls.total + ' cells with data';
      }
    }
    src.setData(data);
    var cov = '<div class="small mute" style="margin-top:4px">Grid: 1°×1° cells (~111 km) over 6–37°N, 68–98°E. Cell-level guidance, not point forecasts.' + (st.onlyIndia ? ' Cells outside the India outline are hidden.' : ' Cells outside India show model guidance only.') + '</div>';
    legend = legend.indexOf('@@COV@@') >= 0 ? legend.replace('@@COV@@', cov) : (legend ? legend + '<details><summary>About this view</summary>' + cov + '</details>' : legend);
    $('legend').innerHTML = legend + (stats ? '<div class="small" style="margin-top:4px">' + stats + '</div>' : ''); $('legend').style.display = (legend || stats) ? 'block' : 'none';
    $('overlay').style.display = ov ? 'flex' : 'none'; $('overlay').innerHTML = ov;
    if (map.getLayer('sel')) map.setFilter('sel', ['==', ['get', 'i'], st.sel == null ? -1 : st.sel]);
  }

  function initMap() {
    if (!window.maplibregl) { $('overlay').style.display = 'flex'; $('overlay').textContent = 'Map library could not be loaded.'; return; }
    map = new maplibregl.Map({
      container: 'map', center: [82.5, 22.5], zoom: window.innerWidth < 760 ? 3 : 3.8, minZoom: 2.5, maxZoom: 9, attributionControl: false,
      style: { version: 8, sources: { base: { type: 'raster', tileSize: 256, attribution: '© OpenStreetMap contributors; outline: Natural Earth (public domain)', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'] } },
               layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b1220' } }, { id: 'base', type: 'raster', source: 'base', paint: { 'raster-opacity': 0.5, 'raster-saturation': -0.85, 'raster-brightness-max': 0.4, 'raster-contrast': 0.2 } }] }
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
    if (window.ResizeObserver) new ResizeObserver(function () { map.resize(); }).observe($('mapwrap'));
    window.addEventListener('resize', function () { map.resize(); });
    // External basemap tiles are optional decoration. If they fail, hide that layer and say so: the India outline,
    // land fill and every grid cell are local and keep working.
    var tileFailures = 0;
    map.on('error', function (e) {
      var src = e && (e.sourceId || (e.source && e.source.id));
      if (src !== 'base') return;
      if (++tileFailures === 3 && map.getLayer('base')) {
        map.setLayoutProperty('base', 'visibility', 'none'); st.basemap = 'UNAVAILABLE';
        var n = $('maplabel'); if (n) n.insertAdjacentHTML('beforeend', '<span class="chip STALE" id="tilenote"><i></i>Basemap tiles unavailable · grid and outline unaffected</span>');
      }
    });
    var inited = false;
    function onReady() {
      if (inited) return; inited = true;
      map.addSource('cells', { type: 'geojson', data: features([]) });
      map.addSource('india', { type: 'geojson', data: st.outline || { type: 'FeatureCollection', features: [] } });
      map.addSource('centre', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'india-fill', type: 'fill', source: 'india', paint: { 'fill-color': '#16243f', 'fill-opacity': 0.85 } });
      map.addLayer({ id: 'fill', type: 'fill', source: 'cells', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['get', 'op'] } });
      map.addLayer({ id: 'grid', type: 'line', source: 'cells', paint: { 'line-color': '#9fb3d8', 'line-opacity': 0.1, 'line-width': 0.5 } });
      map.addLayer({ id: 'india-line', type: 'line', source: 'india', paint: { 'line-color': '#e6f1ff', 'line-width': 1.4, 'line-opacity': 0.9 } });
      map.addLayer({ id: 'centre-halo', type: 'circle', source: 'centre', paint: { 'circle-radius': 16, 'circle-color': '#ffffff', 'circle-opacity': 0.18, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } });
      map.addLayer({ id: 'sel', type: 'line', source: 'cells', filter: ['==', ['get', 'i'], -1], paint: { 'line-color': '#ffffff', 'line-width': 2.5 } });
      map.on('click', 'fill', function (e) { if (e.features && e.features[0]) select(e.features[0].properties.i); });
      map.on('mouseenter', 'fill', function () { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'fill', function () { map.getCanvas().style.cursor = ''; });
      mapReady = true; refresh();
    }
    // The style event can fire before or after this point depending on timing/network; accept any of them once.
    if (map.isStyleLoaded()) onReady();
    map.on('style.load', onReady); map.on('load', onReady);
    setTimeout(function () { if (!inited && map.isStyleLoaded()) onReady(); }, 1200);
  }
  function setOutlineSource() { if (mapReady && st.outline) map.getSource('india').setData(st.outline); }

  /* ------------------------------------------------------------------ panels */
  function renderHazardTabs() {
    $('hz').innerHTML = HZ_ORDER.map(function (h) {
      var label = S.HAZARDS[h].label, sub;
      if (st.mode === 'sim') sub = 'Simulated scenario';
      else { var s2 = st.live ? st.live.hazards[h].status : 'UNAVAILABLE'; sub = ({ PARTIAL: 'Station model + NWP guidance', AVAILABLE_DAILY_OUTLOOK_ONLY: 'Daily outlook + GFS rain', UNAVAILABLE: h === 'flash_flood' ? 'Prediction unavailable · terrain context' : 'Unavailable' })[s2] || s2; }
      return '<button data-h="' + h + '" aria-pressed="' + (st.hazard === h) + '">' + esc(label) + '<small>' + esc(sub) + '</small></button>';
    }).join('');
    Array.prototype.forEach.call($('hz').querySelectorAll('button'), function (b) { b.onclick = function () { st.hazard = b.dataset.h; st.sel = null; st.research = false; st.layer = layerDefs()[st.hazard][0].id; if (st.mode === 'sim') newScenario(); refresh(); }; });
  }

  function slider(id, label, min, max, step, v, suffix) {
    return '<label for="' + id + '">' + label + ': <b id="' + id + '-v">' + v + '</b>' + (suffix || '') + '</label><input id="' + id + '" type="range" min="' + min + '" max="' + max + '" step="' + step + '" value="' + v + '">';
  }
  function bindSlider(id, fn) { var el = $(id); if (el) el.oninput = function () { $(id + '-v').textContent = this.value; fn(+this.value); }; }

  function renderControls() {
    var h = '<h2>' + (st.mode === 'sim' ? 'Scenario controls <span class="chip" style="color:var(--sim)">SIMULATION</span>' : 'Layers &amp; thresholds') + '</h2>';
    if (st.mode === 'live') {
      var ls = layerDefs()[st.hazard];
      h += '<label for="lyr">Map layer</label><select id="lyr">' + ls.map(function (l) { return '<option value="' + l.id + '"' + (l.id === st.layer ? ' selected' : '') + '>' + esc(l.label) + '</option>'; }).join('') + '</select>';
      if (st.live && st.live.grid.nwp_hours.length > 1 && st.layer !== 'cb' && st.layer !== 'susc' && st.layer !== 'elev' && st.layer !== 'hydro')
        h += '<label>GFS model hour</label><div class="seg2">' + st.live.grid.nwp_hours.map(function (n, i) { return '<button data-hr="' + i + '" aria-pressed="' + (i === st.hour) + '">f' + n.forecast_hour + '<small>' + esc((n.valid_time_utc || '').slice(11, 16)) + 'Z</small></button>'; }).join('') + '</div>';
      if (st.hazard === 'thunderstorm' && (st.layer === 'cape' || st.layer === 'screen')) h += slider('t-cape', 'Highlight CAPE ≥', 0, 3000, 100, st.thr.cape, ' J/kg');
      if (st.hazard === 'thunderstorm' && st.layer === 'screen') h += slider('t-cin', 'CIN weaker than −', 0, 300, 10, st.thr.cinLimit, ' J/kg');
      if (st.hazard === 'extreme_rainfall' && (st.layer === 'qpe' || st.layer === 'extent')) h += slider('t-rain', 'Highlight rain ≥', 1, 40, 1, st.thr.rain, ' mm');
      if (st.hazard === 'flash_flood' && st.layer === 'trigger') h += slider('t-rtrig', 'Rain trigger ≥', 1, 40, 1, st.thr.rainTrig, ' mm') + slider('t-susc', 'Susceptibility ≥', 0, 1, 0.05, st.thr.susc);
      if (st.hazard === 'flash_flood') h += '<label><input type="checkbox" id="res"' + (st.research ? ' checked' : '') + '> Show research-only PU ranking</label><div class="small mute">Research view. Not a probability; never used for alerts.</div>';
    } else {
      var p = st.sc.params;
      h += '<div class="row" style="margin-top:6px"><button class="btn" data-ps="mild">Mild</button><button class="btn" data-ps="severe">Severe</button><button class="btn" data-ps="extreme">Extreme</button></div>' +
        slider('s-intensity', 'Intensity', 0.1, 1, 0.05, p.intensity) + slider('s-radius', 'Storm radius (°)', 0.6, 3, 0.1, p.radius) + slider('s-speed', 'Speed (°/h)', 0, 0.8, 0.05, p.speed) +
        slider('s-bearing', 'Heading (° from N)', 0, 359, 5, p.bearing) + (st.hazard !== 'thunderstorm' ? slider('s-moisture', 'Antecedent soil moisture', 0, 1, 0.05, p.moisture) : '') +
        '<div class="row" style="margin-top:10px"><button class="btn" id="rst">Reset</button></div><div class="small mute" style="margin-top:6px">' + esc(S.HAZARDS[st.hazard].story) + '</div>';
    }
    h += '<label style="margin-top:10px"><input type="checkbox" id="india"' + (st.onlyIndia ? ' checked' : '') + '> Show India only (hide cells outside the outline)</label>';
    $('controls').innerHTML = h;
    if ($('lyr')) $('lyr').onchange = function () { st.layer = this.value; refresh(); };
    Array.prototype.forEach.call($('controls').querySelectorAll('[data-hr]'), function (b) { b.onclick = function () { st.hour = +b.dataset.hr; refresh(); }; });
    if ($('india')) $('india').onchange = function () { st.onlyIndia = this.checked; paint(); };
    if ($('res')) $('res').onchange = function () { st.research = this.checked; refresh(); };
    bindSlider('t-cape', function (v) { st.thr.cape = v; paint(); renderHazardInfo(); });
    bindSlider('t-cin', function (v) { st.thr.cinLimit = v; paint(); });
    bindSlider('t-rain', function (v) { st.thr.rain = v; paint(); });
    bindSlider('t-rtrig', function (v) { st.thr.rainTrig = v; paint(); });
    bindSlider('t-susc', function (v) { st.thr.susc = v; paint(); });
    ['intensity', 'radius', 'speed', 'bearing', 'moisture'].forEach(function (k) { bindSlider('s-' + k, function (v) { st.sc.params[k] = v; recompute(); paint(); renderAlerts(); renderDetail(); renderTimeline(); }); });
    Array.prototype.forEach.call($('controls').querySelectorAll('[data-ps]'), function (b) { b.onclick = function () { Object.assign(st.sc.params, S.PRESETS[b.dataset.ps]); resetRun(); refresh(); }; });
    if ($('rst')) $('rst').onclick = function () { newScenario(); refresh(); };
  }

  function evidenceRow(label, value, iso) {
    var a = iso ? ageH(iso) : null, stale = a != null && a > 6;
    return '<tr><th>' + esc(label) + '</th><td>' + value + (iso ? ' <span class="small ' + (stale ? 'na' : 'mute') + '">(' + esc(iso.slice(0, 16).replace('T', ' ')) + 'Z · ' + agoText(iso) + (stale ? ' · STALE' : '') + ')</span>' : '') + '</td></tr>';
  }
  function renderStation(x) {
    var s = sourceById('vobl_station'), state = s ? srcState(s) : 'UNAVAILABLE', ev = x.station_evidence || {}, out = '';
    out += '<h2 style="margin-top:12px">Station evidence · VOBL (Bengaluru)</h2>';
    var rows = '';
    if (ev.metar) rows += evidenceRow('METAR', esc(ev.metar.raw || ''), ev.metar.obs_time) + evidenceRow('Temp / dewpoint', fmt(ev.metar.temp_c, 0, '°C') + ' / ' + fmt(ev.metar.dewpoint_c, 0, '°C')) + evidenceRow('Wind', esc(ev.metar.wind_dir) + ' ' + fmt(ev.metar.wind_speed_kt, 0, 'kt')) + evidenceRow('Thunderstorm reported', ev.metar.thunderstorm_present ? 'YES' : 'no');
    else rows += '<tr><th>METAR</th><td><span class="na">unavailable</span></td></tr>';
    if (ev.satellite) rows += evidenceRow('Himawari-9 B13 min BT', fmt(ev.satellite.min_bt_50km_c, 1, '°C') + ' (cold threshold ' + ev.satellite.cold_threshold_c + '°C; ' + (ev.satellite.cold_pixels_count || 0) + ' cold pixels)', ev.satellite.timestamp_utc);
    if (ev.precipitable_water_mm != null) rows += '<tr><th>Precipitable water</th><td>' + fmt(ev.precipitable_water_mm, 1, 'mm') + '</td></tr>';
    if (ev.model_winds) rows += '<tr><th>Model winds (u/v)</th><td class="small">500 hPa ' + fmt(ev.model_winds.ERA5_u_500hPa, 1) + ' / ' + fmt(ev.model_winds.ERA5_v_500hPa, 1) + ' · 850 hPa ' + fmt(ev.model_winds.ERA5_u_850hPa, 1) + ' / ' + fmt(ev.model_winds.ERA5_v_850hPa, 1) + ' m/s</td></tr>';
    out += '<table>' + rows + '</table><div class="small mute">' + esc(ev.model_winds_note || '') + '</div>';
    out += '<h2 style="margin-top:12px">Station model · 6-h slots</h2>';
    if (state !== 'FRESH') return out + '<p class="na small">Station forecast is ' + state + ' — withheld.</p>';
    out += '<p class="small mute">Uncalibrated model scores for IST 6-hour windows — not probabilities and not 2–6 h lead forecasts.</p>';
    return out + x.station_slots.map(function (sl) {
      var mx = Math.max.apply(null, sl.drivers.map(function (d) { return Math.abs(d.shap); }).concat([0.01]));
      return '<div class="slot"><div class="row"><b>' + esc(sl.time) + '</b><span class="spacer"></span><span>score ' + fmt(sl.score, 3) + ' / thr ' + fmt(sl.threshold, 2) + '</span></div>' +
        '<div class="small ' + (sl.fallback ? 'na' : 'mute') + '">' + (sl.fallback ? 'FALLBACK (inputs are defaults) — not eligible · ' : '') + esc(sl.source || '') + (sl.predicted ? ' · above threshold' : ' · below threshold') + '</div>' +
        sl.drivers.map(function (d) { var w = Math.round(Math.abs(d.shap) / mx * 100), neg = d.shap < 0;
          return '<div class="small" style="margin-top:4px">' + esc(d.feature) + ' = ' + (+d.value).toFixed(1) + ' <span class="mute">(' + (neg ? 'lowers' : 'raises') + ' score)</span><div class="bar2"><b style="left:0;width:' + w + '%;background:' + (neg ? 'var(--accent)' : 'var(--bad)') + '"></b></div></div>'; }).join('') + '</div>';
    }).join('');
  }

  function renderHazardInfo() {
    var hz = st.hazard, h = '<h2>Hazard info</h2>';
    if (st.mode === 'sim') {
      h += '<p class="small"><b>' + esc(S.HAZARDS[hz].label) + '</b> — simulated field: ' + esc(S.HAZARDS[hz].unit) + '. Level thresholds are illustrative, not IMD/NDMA criteria.</p><p class="small mute">No real inputs, trained models or verification are involved in this mode.</p>';
    } else if (!st.live) { h += '<p class="na">Live feed unavailable.</p>'; }
    else {
      var x = st.live.hazards[hz];
      h += '<p>' + chip(x.status, x.status.replace(/_/g, ' ')) + '</p><p class="small">' + esc(x.summary) + '</p><p class="small"><b>Target:</b> ' + esc(x.target || '') + '</p>';
      if (x.distinctions) h += '<table>' + x.distinctions.map(function (d) { return '<tr><th>' + esc(d.term) + '</th><td class="small">' + esc(d.what) + '<br>' + chip(/UNAVAIL/.test(d.status) ? 'UNAVAILABLE' : 'AVAILABLE', d.status) + '</td></tr>'; }).join('') + '</table>';
      if (x.supported_predictors) h += '<p class="small"><b>Supported:</b> ' + x.supported_predictors.map(esc).join(', ') + '<br><b>Not available:</b> <span class="na">' + x.unsupported_predictors.map(esc).join(', ') + '</span></p>';
      if (x.available_context) h += '<p class="small"><b>Available context:</b></p><ul class="small">' + x.available_context.map(function (m) { return '<li>' + esc(m) + '</li>'; }).join('') + '</ul>';
      if (x.missing_for_live) h += '<p class="small"><b>Live operation still needs:</b></p><ul class="small na">' + x.missing_for_live.map(function (m) { return '<li>' + esc(m) + '</li>'; }).join('') + '</ul>';
      if (hz === 'thunderstorm') h += renderStation(x);
    }
    $('hzinfo').innerHTML = h;
  }

  function select(i) { st.sel = i; if (map && map.getLayer('sel')) map.setFilter('sel', ['==', ['get', 'i'], i]); setPanel('detail'); renderDetail(); }
  function renderDetail() {
    var el = $('p-detail');
    if (st.sel == null) { el.innerHTML = '<p class="mute">Select a grid cell on the map to drill down.</p>'; return; }
    if (st.mode === 'sim') {
      var c = S.CELLS[st.sel], f = st.frame, hz = S.HAZARDS[st.hazard];
      var series = []; for (var t = 0; t <= S.HOURS; t++) series.push(S.frame(st.sc, t));
      var mx = Math.max.apply(null, series.map(function (fr) { return fr.values[st.sel]; }).concat([hz.max * 0.05]));
      el.innerHTML = '<span class="chip" style="color:var(--sim)">SIMULATION</span><h3>Cell ' + c[0] + '°N ' + c[1] + '°E</h3><table><tr><th>Simulated level</th><td class="lvl' + f.levels[st.sel] + '">' + S.LEVELS[f.levels[st.sel]] + '</td></tr><tr><th>Simulated value</th><td>' + f.values[st.sel].toFixed(hz.max <= 1 ? 2 : 0) + ' ' + esc(hz.unit) + '</td></tr><tr><th>Hour</th><td>T+' + st.t + ' h (' + (f.inTargetBand ? 'inside' : 'outside') + ' the 2–6 h band)</td></tr></table><h2 style="margin-top:10px">Simulated evolution</h2><div style="display:flex;align-items:flex-end;gap:4px;height:64px">' +
        series.map(function (fr, k) { var v = fr.values[st.sel]; return '<div title="T+' + k + ' h: ' + S.LEVELS[fr.levels[st.sel]] + '" style="flex:1;background:' + (k === st.t ? 'var(--sim)' : 'var(--accent)') + ';height:' + Math.max(3, v / mx * 100) + '%;border-radius:3px 3px 0 0"></div>'; }).join('') + '</div><div class="ticks"><span>T+0</span><span>T+6</span></div><p class="small mute">All values are synthetic.</p>';
      return;
    }
    var cell = st.cells[st.sel]; if (!cell) { el.innerHTML = ''; return; }
    var gs = sourceById('gfs_grid'), cs = sourceById('cb_daily_outlook'), hrs = st.live.grid.nwp_hours;
    var hdr = '<tr><th></th>' + hrs.map(function (n) { return '<td><b>f' + n.forecast_hour + '</b><div class="small mute">' + esc((n.valid_time_utc || '').slice(5, 16).replace('T', ' ')) + 'Z</div></td>'; }).join('') + '</tr>';
    var row = function (name, key, d, u) { return '<tr><th>' + name + '</th>' + hrs.map(function (n, i) { return '<td>' + fmt(val(cell, key + '_' + i), d, u) + '</td>'; }).join('') + '</tr>'; };
    var out = '<h3>Cell ' + cell[0] + '°N ' + cell[1] + '°E</h3><div class="small mute">GFS cycle ' + esc(gs && gs.model_init_utc || '?') + ' · ' + (gs ? agoText(gs.generated_at_utc) : '') + '</div><table>' + hdr + row('CAPE', 'cape', 0, 'J/kg') + row('CIN', 'cin', 0, 'J/kg') + row('GFS precip', 'qpe', 1, 'mm') + '</table>' +
      '<h2 style="margin-top:10px">Outlooks &amp; predictions</h2><table><tr><th>Heavy-rain-day score (daily)</th><td>' + fmt(val(cell, 'cb_daily_outlook'), 3) + '</td></tr><tr><th>Thunderstorm prediction</th><td><span class="na">unavailable (pan-India)</span></td></tr><tr><th>Cloudburst prediction</th><td><span class="na">unavailable</span></td></tr><tr><th>Flash-flood prediction</th><td><span class="na">unavailable</span></td></tr>' +
      (st.research && st.hazard === 'flash_flood' ? '<tr><th>Research PU rank</th><td>' + fmt(val(cell, 'ff_research_pu_rank'), 2) + ' <span class="mute small">(research only)</span></td></tr>' : '') + '</table>' +
      '<h2 style="margin-top:10px">Terrain &amp; hydrology context</h2><table><tr><th>Elevation</th><td>' + fmt(val(cell, 'elevation_m'), 0, 'm') + '</td></tr><tr><th>Slope</th><td>' + fmt(val(cell, 'slope_deg'), 1, '°') + '</td></tr><tr><th>Susceptibility proxy</th><td>' + fmt(val(cell, 'ff_susceptibility_proxy'), 2) + ' <span class="small mute">(derived index)</span></td></tr>';
    var hyd = st.live.grid.hydrology && st.live.grid.hydrology[String(st.sel)];
    if (hyd) out += '<tr><th>Gauge hydrology</th><td class="small">' + Object.keys(hyd).slice(0, 6).map(function (k) { return esc(k) + ': ' + (+hyd[k]).toPrecision(3); }).join('<br>') + '</td></tr>';
    else out += '<tr><th>Gauge hydrology</th><td><span class="na">no gauge in this cell</span></td></tr>';
    el.innerHTML = out + '</table><p class="small mute">Daily window: ' + esc(cs && cs.window_start_utc || '?') + ' → ' + esc(cs && cs.window_end_utc || '?') + '. Predictors null in every cell: ' + esc((gs && gs.unavailable_predictors || []).join(', ') || 'none') + '.</p>';
  }

  /* severity legend: what each level would mean (defined for the simulation; live DRIFT issues none) */
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
    el.innerHTML = '<div class="warn"><b>No DRIFT warning is active</b><div class="small mute">Automated alerting: ' + esc(a ? a.automated_dispatch : 'UNKNOWN') + ' · eligible ' + (a ? a.eligible : '–') + ' of ' + (a ? a.n_evaluated : '–') + ' evaluated slots</div></div>' +
      '<h2>Why alerts are withheld</h2><p class="small">' + esc(a ? a.withheld_explanation : 'Live feed unavailable.') + '</p>' +
      (a && a.withheld_reason_codes ? '<div class="small mute">' + a.withheld_reason_codes.map(esc).join(' · ') + '</div>' : '') +
      '<p class="small" style="margin-top:8px">No hazard model has been approved as alertable, and no forecast availability time is recorded, so a DRIFT warning cannot be issued from live data. Use SIMULATION to see the full warning → recommended action → notification flow.</p>' + severityLegend();
  }
  function lifecycleHtml(n) {
    var shown = st.lcShown[n.t] == null ? 5 : st.lcShown[n.t], lc = n.issued.lifecycle.slice(0, shown);
    return '<div class="log">' + lc.map(function (e) { return '<div><span class="s">[SIM +' + e.atMinute + 'm]</span> ' + esc(e.stage) + ' — ' + esc(e.note) + ' (not sent)</div>'; }).join('') + (shown < 5 ? '<div class="mute">…</div>' : '') + '</div>';
  }
  function animateLifecycle(t) {
    var n = st.narr[t]; if (!n || !n.issued) return; st.lcShown[t] = 0;
    (function step() { if (st.lcShown[t] >= 5) return; st.lcShown[t]++; if (st.mode === 'sim') renderAlerts(); setTimeout(step, 380); })();
  }

  function renderSources() {
    var el = $('p-sources');
    if (st.mode === 'sim') { el.innerHTML = '<span class="chip" style="color:var(--sim)">SIMULATION</span><p class="small">Simulation mode has no data sources. Every value is generated in the browser from the controls.</p>'; return; }
    if (!st.live) { el.innerHTML = '<p class="na">Live feed unavailable.</p>'; return; }
    el.innerHTML = st.live.sources.map(function (s) {
      var state = srcState(s), a = ageH(s.generated_at_utc);
      return '<div class="slot"><div class="row"><b>' + esc(s.label) + '</b><span class="spacer"></span>' + chip(state, state) + '</div><div class="small mute">' + (s.generated_at_utc ? 'Generated ' + esc(s.generated_at_utc) + (a == null ? '' : ' · ' + a.toFixed(1) + ' h ago') : '') + (s.model_init_utc ? '<br>Model init ' + esc(s.model_init_utc) : '') + (s.forecast_hour != null ? ' · primary f' + s.forecast_hour : '') + (s.note ? '<br>' + esc(s.note) : '') + '</div></div>';
    }).join('') + '<p class="small mute">Feed built ' + esc(st.live.generated_at_utc) + '. Ages are recomputed in your browser; sources older than ' + STALE_H + ' h are marked STALE and their values withheld. ' + esc(st.live.disclaimer) + '</p>';
  }

  function renderEval() {
    var el = $('p-eval'), e = st.ev;
    if (st.mode === 'sim') { el.innerHTML = '<p class="small mute">Evaluation applies to LIVE models only. A simulation has no skill to measure.</p>'; return; }
    if (!e) { el.innerHTML = '<p class="na">Evaluation file unavailable.</p>'; return; }
    var t = e.hazards.thunderstorm, r = e.hazards.extreme_rainfall, f = e.hazards.flash_flood, u = r.variants.uncalibrated, c = r.variants.calibrated;
    el.innerHTML = '<p class="small mute">' + esc(e.claims_policy) + '</p>' +
      '<div class="slot"><b>Thunderstorm (VOBL station)</b><div class="small">' + esc(t.validation) + '</div><table><tr><th>Mean AUROC</th><td>' + t.summary.mean_auroc + '</td></tr><tr><th>Brier skill vs climatology</th><td>' + t.summary.mean_bss_vs_climatology + ' (positive in ' + t.summary.years_with_positive_bss + ' of ' + t.folds.length + ' years)</td></tr><tr><th>Mean POD / FAR</th><td>' + t.summary.mean_pod + ' / ' + t.summary.mean_far + '</td></tr></table><div class="small mute">' + t.limits.map(esc).join(' ') + '</div></div>' +
      '<div class="slot"><b>Extreme rainfall (heavy-rain day, pan-India)</b><div class="small">' + esc(r.scope) + '</div><table><tr><th>AUROC / PR-AUC (uncal.)</th><td>' + u.auroc + ' / ' + u.pr_auc + ' (no-skill PR ' + u.baseline_pr_auc_no_skill + ')</td></tr><tr><th>Brier skill vs climatology</th><td>uncal. ' + u.bss_vs_climatology + ' · cal. ' + c.bss_vs_climatology + '</td></tr><tr><th>POD / FAR (uncal.)</th><td>' + u.pod + ' / ' + u.far + '</td></tr><tr><th>POD / FAR (cal.)</th><td>' + c.pod + ' / ' + c.far + '</td></tr></table><div class="small mute">' + esc(r.summary.note) + '</div></div>' +
      '<div class="slot"><b>Flash flood</b><div class="small na">Not validated — no evaluation exists.</div><div class="small mute">' + f.limits.map(esc).join(' ') + '</div></div>';
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
    var gs = sourceById('gfs_grid'), hrs = st.live && st.live.grid ? st.live.grid.nwp_hours : [];
    el.innerHTML = '<div class="row"><b>Forecast timeline</b>' + chip('UNAVAILABLE', 'LEAD-SPECIFIC 2–6 h FORECAST UNAVAILABLE') + '</div>' +
      '<div class="small mute" style="margin:6px 0">What DRIFT genuinely has: discrete GFS model hours (below) and a daily outlook window. Select a model hour to drive the map.</div>' +
      '<div class="row">' + hrs.map(function (n, i) { return '<button class="btn" data-hr2="' + i + '" aria-pressed="' + (i === st.hour) + '">GFS f' + n.forecast_hour + ' · valid ' + esc((n.valid_time_utc || '').slice(5, 16).replace('T', ' ')) + 'Z <span class="small mute">(' + agoText(n.valid_time_utc) + ')</span></button>'; }).join('') + '</div>' +
      '<div class="track" style="margin-top:10px"><div class="band" style="left:' + left + ';width:' + width + ';opacity:.45"><span>2–6 h band · not populated</span></div></div>' + ticks +
      '<p class="small mute">No forecast availability time is recorded, so no operational lead time can be claimed. Switch to SIMULATION to explore the 2–6 h warning interface with synthetic data.</p>';
    Array.prototype.forEach.call(el.querySelectorAll('[data-hr2]'), function (b2) { b2.onclick = function () { st.hour = +b2.dataset.hr2; refresh(); }; });
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
  function bestCell(fn) { var bi = -1, bv = -1e9; st.cells.forEach(function (c, i) { if (!visible(c[0], c[1], 'l' + i)) return; var v = fn(c); if (v != null && v > bv) { bv = v; bi = i; } }); return bi; }
  function cellAt(lat, lon) { var bi = -1, bd = 1e9; st.cells.forEach(function (c, i) { var d = Math.abs(c[0] - lat) + Math.abs(c[1] - lon); if (d < bd) { bd = d; bi = i; } }); return bi; }
  function go(hz, layer) { if (st.mode !== 'live') setMode('live'); st.hazard = hz; st.layer = layer; st.research = false; refresh(); }
  var TOUR = [
    { t: 'DRIFT at national scale', b: 'DRIFT screens severe-weather hazards across India on a <b>992-cell grid</b> (1° cells, 6–37°N, 68–98°E). LIVE mode shows only integrity-checked data; anything DRIFT cannot support stays <b>unavailable</b>. Colours here are GFS model guidance, not warnings.',
      a: function () { go('thunderstorm', 'cape'); st.sel = null; refresh(); if (map) map.fitBounds([[68.5, 6.5], [97.8, 37.2]], { padding: 20, duration: 600 }); } },
    { t: 'Thunderstorm: pick a region', b: '<b>CAPE</b> (J/kg) is stored convective energy: how strongly a rising air parcel can accelerate. <b>CIN</b> is the "cap": energy needed to get parcels rising at all. High CAPE with weak CIN is the ingredient list for storms. These are <b>model inputs</b>, not a thunderstorm probability. Use the CAPE slider to see where YOUR threshold is met.',
      a: function () { go('thunderstorm', 'screen'); var i = bestCell(function (c) { return val(c, 'cape_' + st.hour); }); if (i >= 0) { select(i); if (map) map.flyTo({ center: [st.cells[i][1], st.cells[i][0]], zoom: 5, duration: 700 }); } } },
    { t: 'Thunderstorm: station evidence vs national coverage', b: 'Only <b>one station (VOBL, Bengaluru)</b> has a trained thunderstorm model, METAR and satellite evidence, with explanations of which inputs raised or lowered its score. Everywhere else DRIFT has <b>guidance fields only</b>; it does not claim a pan-India thunderstorm forecast. Model score here is uncalibrated, not a probability.',
      a: function () { go('thunderstorm', 'cape'); var i = cellAt(13, 78); select(i); setPanel('detail'); if (map) map.flyTo({ center: [78, 13], zoom: 5.5, duration: 700 }); var h = $('hzinfo'); if (h && h.scrollIntoView) h.scrollIntoView({ block: 'start', behavior: 'smooth' }); } },
    { t: 'Extreme rainfall: intensity and extent', b: 'GFS precipitation at two model hours (f6, f9) with a threshold you control; the legend states how many cells and roughly how many km² exceed it. This is <b>model guidance</b>, not observed rain, and the two hours are not summed because their accumulation windows are not verified.',
      a: function () { go('extreme_rainfall', 'extent'); st.thr.rain = 5; refresh(); var i = bestCell(function (c) { return val(c, 'qpe_' + st.hour); }); if (i >= 0) select(i); if (map) map.fitBounds([[68.5, 6.5], [97.8, 37.2]], { padding: 20, duration: 600 }); } },
    { t: 'Heavy-rain outlook is not a cloudburst forecast', b: 'The <b>daily heavy-rain-day score</b> ranks cells for rainfall ≥ 64.5 mm/24 h across the whole day. A <b>cloudburst</b> is far more intense and localised (often quoted ≥ 100 mm/h over a few tens of km²). DRIFT has no verified cloudburst labels or sub-hourly rainfall input, so cloudburst prediction is <b>unavailable</b>.',
      a: function () { go('extreme_rainfall', 'cb'); setPanel('detail'); var h = $('hzinfo'); if (h && h.scrollIntoView) h.scrollIntoView({ block: 'start', behavior: 'smooth' }); } },
    { t: 'Flash flood: triggers and terrain, no prediction', b: 'DRIFT shows <b>context</b>: GFS rain trigger on cells with high SRTM-derived susceptibility. It does not predict floods. Missing for live operation: observed flood labels, gauge/soil-moisture inputs (gauge hydrology exists for only 75 of 992 cells) and a validated model.',
      a: function () { go('flash_flood', 'trigger'); st.thr.rainTrig = 3; refresh(); var i = bestCell(function (c) { var q = val(c, 'qpe_' + st.hour), s = val(c, 'ff_susceptibility_proxy'); return (q != null && s != null && s >= st.thr.susc) ? q : null; }); if (i >= 0) select(i); setPanel('detail'); } },
    { t: 'Simulation: an extreme event', b: '<b>SIMULATION mode uses synthetic data only.</b> The amber banner, SIMULATION badges and striped header cannot be mistaken for live output. Press <b>Run event</b>: conditions intensify, cells change level, and WATCH → ALERT → WARNING are issued.',
      a: function () { setMode('sim'); Object.assign(st.sc.params, S.PRESETS.extreme); resetRun(); refresh(); setPanel('alerts'); if (map) map.fitBounds([[68.5, 6.5], [97.8, 37.2]], { padding: 20, duration: 600 }); } },
    { t: 'Simulation: warning and notification lifecycle', b: 'Each issued level lists affected cells and recommended actions, then a <b>simulated</b> lifecycle (eligible → recorded → dispatch attempted → provider accepted → delivery confirmed). No network request is made and no recipient exists. Real DRIFT alerting stays disabled until a hazard model passes the alert-eligibility policy.',
      a: function () { setMode('sim'); Object.assign(st.sc.params, S.PRESETS.extreme); resetRun(); refresh(); setPanel('alerts'); st.speed = 700; togglePlay(); } }
  ];
  var tourI = -1;
  function tourGo(i) {
    var el = $('tour'); if (!el) return;
    if (i < 0 || i >= TOUR.length) { tourI = -1; stopPlay(); el.style.display = 'none'; return; }
    tourI = i; var s = TOUR[i]; el.style.display = 'block';
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
    if (!st.live) return 'UNAVAILABLE';
    var ss = st.live.sources.map(srcState).filter(function (x) { return x !== 'STATIC'; });
    return ss.indexOf('UNAVAILABLE') >= 0 ? 'UNAVAILABLE' : ss.indexOf('STALE') >= 0 ? 'STALE' : 'FRESH';
  }
  function banner() {
    var b = $('banner');
    if (st.mode === 'sim') { b.className = 'sim'; b.textContent = 'SIMULATION MODE — every map value, warning and notification here is synthetic. It is not a forecast and nothing is ever sent.'; }
    else { b.className = 'live'; b.textContent = 'LIVE MODE — only integrity-checked data is shown; unavailable data stays unavailable. Advisory model outputs, not IMD/NDMA warnings.'; }
    $('simmark').style.display = st.mode === 'sim' ? 'block' : 'none';
    $('maplabel').innerHTML = st.mode === 'sim' ? '<span class="chip" style="color:var(--sim)"><i style="background:var(--sim)"></i>SIMULATION</span>' : '<span class="chip ' + worst() + '"><i></i>LIVE · ' + worst() + '</span>';
    var f = $('fresh'); f.className = 'chip ' + (st.mode === 'sim' ? 'STALE' : worst()); f.querySelector('span').textContent = st.mode === 'sim' ? 'Synthetic data' : 'Data: ' + worst();
  }
  function setMode(m) {
    st.mode = m; st.sel = null; stopPlay();
    $('m-live').setAttribute('aria-pressed', m === 'live'); $('m-sim').setAttribute('aria-pressed', m === 'sim');
    st.layer = layerDefs()[st.hazard][0].id; st.research = false;
    if (m === 'sim') newScenario(); history.replaceState(null, '', '#' + m); refresh();
  }
  function setPanel(p) {
    st.panel = p;
    Array.prototype.forEach.call(document.querySelectorAll('.rtabs button'), function (b) { b.setAttribute('aria-pressed', b.dataset.p === p); });
    ['detail', 'alerts', 'sources', 'eval'].forEach(function (k) { $('p-' + k).classList.toggle('on', k === p); });
  }
  function refresh() { banner(); renderHazardTabs(); renderControls(); renderHazardInfo(); renderDetail(); renderAlerts(); renderSources(); renderEval(); renderTimeline(); paint(); }

  function boot() {
    if ($('tour-start')) $('tour-start').onclick = function () { tourGo(0); };
    $('m-live').onclick = function () { setMode('live'); }; $('m-sim').onclick = function () { setMode('sim'); };
    Array.prototype.forEach.call(document.querySelectorAll('.rtabs button'), function (b) { b.onclick = function () { setPanel(b.dataset.p); }; });
    initMap();
    load().then(function () { st.loading = false; st.layer = layerDefs()[st.hazard][0].id; setOutlineSource(); if (location.hash === '#sim') setMode('sim'); else refresh(); });
    window.__drift = { inIndiaPoint: inIndiaPoint, cellInIndia: cellInIndia, tour: function (i) { tourGo(i); }, getMap: function () { return map; }, ready: function () { return mapReady; }, state: st, setMode: setMode, select: select, refresh: refresh, setT: setT };
  }
  boot();
})();
