/* DRIFT grid-cell inspector for the original (legacy) dashboard.
 *
 * Click or tap any 0.25 deg GFS cell on the map -> highlight it, open a panel (desktop side panel / mobile bottom sheet) with the cell's real
 * values, per-variable freshness and provenance, forecast-hour charts, satellite observation, terrain/hydrology context and hazard status.
 * Everything comes from the validated store (assets/drift_location.js: same artifacts and record logic as the Python service, parity-tested).
 *
 * Part 1 (buildModel, pure, Node-testable): no value is invented. A missing value is MISSING with its reason; a stale value is RETAINED and
 * labelled STALE with its own timestamp; a model that does not exist is UNAVAILABLE, never 0%.
 * Part 2 (DOM): fast O(1) hit-testing through store.resolve, one geojson source for the selectable grid, rAF-coalesced rapid switching,
 * keyboard (arrows move to the adjacent cell, Enter selects, Escape closes), touch bottom sheet with drag. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DriftInspector = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var SAT_MAX_AGE_MIN = 90, GFS_MAX_AGE_H = 18;
  var HOUR_STATUS = { CURRENT: 'FRESH', STALE: 'STALE', PAST_VALID_TIME: 'STALE', INVALID_PROVENANCE: 'INVALID' };
  var KIND = { RAW_MODEL_OUTPUT: 'model output', DERIVED_PHYSICAL: 'calculated from GFS', MODEL_DIAGNOSTIC: 'model state' };

  // [id, title, [[key, label, decimals], ...]]
  var SECTIONS = [
    ['instability', 'Instability', [['cape', 'CAPE', 0], ['cin', 'CIN', 0], ['lftx', 'Lifted index', 1], ['k_index', 'K-index', 1], ['total_totals', 'Total Totals', 1], ['lapse_850_500', '850–500 hPa lapse rate', 2]]],
    ['moisture', 'Moisture', [['pwat', 'Precipitable water', 1], ['rh2m', '2 m relative humidity', 0], ['d2m_c', '2 m dew point', 1], ['rh850', 'RH 850 hPa', 0], ['rh700', 'RH 700 hPa', 0], ['rh500', 'RH 500 hPa', 0],
                                 ['q850', 'Specific humidity 850 hPa', 2], ['q700', 'Specific humidity 700 hPa', 2], ['pwat_grad', 'PWAT gradient', 2]]],
    ['wind', 'Wind & dynamics', [['ws10', '10 m wind speed', 1], ['wd10', '10 m wind from', 0], ['shear_850_500', '850–500 hPa bulk shear', 1], ['w850', 'ω 850 hPa', 2], ['w700', 'ω 700 hPa', 2], ['w500', 'ω 500 hPa', 2],
                                 ['conv850', 'Convergence 850 hPa', 2], ['conv10', 'Convergence 10 m', 2]]],
    ['temperature', 'Temperature & pressure', [['t2m_c', '2 m temperature', 1], ['mslp_hpa', 'Mean sea-level pressure', 1]]],
    ['precip', 'Precipitation & soil', [['precip_3h', 'Rain, last 3 h (amount)', 1], ['apcp_cum', 'Rain accumulated since init', 1], ['soilw0_10', 'Soil moisture 0–10 cm (model)', 3]]]
  ];
  var CHARTS = [['cape', 'CAPE', 0], ['precip_3h', 'Rain, 3 h amount', 1], ['pwat', 'Precipitable water', 1], ['t2m_c', '2 m temperature', 1], ['ws10', '10 m wind speed', 1], ['shear_850_500', '850–500 hPa shear', 1]];
  var NOT_INGESTED = ['0–6 km wind shear (no model-level winds are ingested)', 'temperatures at individual pressure levels (only the derived K-index, Total Totals and lapse rate are published)',
                      'soil moisture layers other than 0–10 cm', 'observed rainfall'];

  var round = function (x, n) { var m = Math.pow(10, n); return Math.round(x * m) / m; };
  var T = function (s) { return s ? Date.parse(s) : NaN; };
  var iso = function (ms) { return new Date(ms).toISOString().replace(/\.\d+Z$/, 'Z'); };
  var hoursBetween = function (a, b) { return (a - b) / 3600000; };

  /** Retained satellite block (the store withholds stale values; the inspector keeps the last valid measurement and labels it). */
  function satelliteModel(store, cid, now) {
    var d = store.sat, base = { source: 'Himawari-9 AHI L1b band 13 (10.4 µm), NOAA AWS', kind: 'OBSERVATION', is_observation: true, is_retrieved_cloud_top_temperature: false,
      coverage: '2 km at nadir, aggregated to this 0.25° cell; the satellite views India at 55–83° zenith, so pixels are coarse and parallax shifts cold tops',
      interpretation: 'Brightness temperature, not a retrieved cloud-top temperature: it equals cloud-top temperature only for opaque cloud (clear sky shows the surface; thin cirrus reads warm).' };
    if (!store.satOk || store.satIdx[cid] == null) return Object.assign(base, { status: 'MISSING', reason: (store.satProblems && store.satProblems[0]) || 'no satellite artifact for this cell',
                                                                                 required_source: 'Himawari-9 B13 scan (ingest every 30 min by satellite_ctt.yml)' });
    var i = store.satIdx[cid], scene = T(d.meta.scene_nominal_utc), ageMin = round((now - scene) / 60000, 1);
    var cur = ageMin >= 0 && ageMin <= SAT_MAX_AGE_MIN, p10 = d.ctt_p10_k[i];
    var status = p10 == null ? 'MISSING' : cur ? 'FRESH' : 'STALE';
    return Object.assign(base, { status: status, scene_nominal_utc: d.meta.scene_nominal_utc, age_minutes: ageMin, max_age_minutes: SAT_MAX_AGE_MIN,
      bt_p10_k: p10, bt_min_k: d.ctt_min_k[i], cold_frac_235k: d.cold_frac_235k[i], n_pixels: d.n_pixels[i], dtdt_30_k_per_h: d.dtdt_30min_k_per_h[i], dtdt_60_k_per_h: d.dtdt_60min_k_per_h[i],
      view_zenith_deg: d.view_zenith_deg[i], quality: d.view_zenith_deg[i] > 70 ? 'COARSE_VIEW_ANGLE' : 'OK',
      frames: Object.keys((d.meta.frames || {})).map(function (k) { return { id: k, scene_nominal_utc: d.meta.frames[k].scene_nominal_utc }; }),
      reason: p10 == null ? 'no valid pixel in this cell in the latest scan' : null });
  }

  function hazardsModel(rec) {
    var H = rec.hazards || {}, out = {};
    var ts = H.thunderstorm || {};
    if (ts.model && ts.model.status === 'AVAILABLE_EVALUATED') {
      out.thunderstorm = { status: 'AVAILABLE_EVALUATED', model_version: ts.model.model_version, value_type: ts.model.value_type, calibrated: ts.model.calibrated, evaluation_ref: ts.model.evaluation_ref,
        leads: Object.keys(ts.model.leads).map(function (L) { return { lead_h: +L, probability: ts.model.leads[L].value, valid_time_utc: ts.model.leads[L].valid_time_utc, drivers: (ts.model.explanation_top3_treeshap || {})[L] || [] }; }),
        note: 'Calibrated to METAR-reported thunderstorms at airport stations; validated on Apr–Jun 2024/2025 only; NWP forecast hours, not a nowcast. Alerts stay disabled.' };
    } else out.thunderstorm = { status: 'UNAVAILABLE', reason: (ts.model && ts.model.reason) || 'no evaluated thunderstorm model for this cycle', indicators: ts.indicators || null };
    var ex = H.extreme_rainfall || {};
    out.extreme_rainfall = { heavy_rain_day: ex.heavy_rain_day_outlook || null, short_duration: ex.short_duration_extreme_rainfall || { status: 'UNAVAILABLE' }, guidance: ex.model_guidance || null,
      cloudburst: { status: 'UNAVAILABLE', reason: 'No validated, event-based cloudburst model exists. The daily heavy-rain score (>= 64.5 mm/24 h) and GFS rainfall guidance are not cloudburst detection.' } };
    var ff = H.flash_flood || {};
    out.flash_flood = { prediction: { status: 'UNAVAILABLE', reason: 'No validated flash-flood labels or hydrological model exist.' }, screening: ff.screening_index || null, missing_inputs: ff.missing_inputs || [] };
    return out;
  }

  /** Pure view-model for one cell and one forecast hour. */
  function buildModel(store, cid, hour, now) {
    now = now == null ? Date.now() : now;
    var m = { status: 'UNAVAILABLE', reason: null, now_utc: iso(now), sections: [], charts: [], hours: [] };
    if (!store) { m.reason = 'field artifacts could not be loaded'; return m; }
    if (!store.fieldsOk) { m.reason = 'atmospheric fields failed verification: ' + ((store.problems || [])[0] || 'unknown'); return m; }
    if (store.idx[cid] == null) { m.reason = 'cell is not in the national grid'; return m; }
    var rec = store.record(cid, hour, now);
    if (!rec.cycle) { m.reason = 'no forecast cycle available'; return m; }
    var loc = rec.location, cyc = rec.cycle, sel = rec.selected_hour, fm = store.fields.meta;
    var valid = T(sel.valid_time_utc), init = T(cyc.model_init_utc), acq = T(sel.acquired_at_utc);
    m.status = 'OK';
    m.cell = { id: cid, lat: loc.lat, lon: loc.lon, state: loc.state || null, district: loc.district || null, admin_method: loc.admin_method || null, admin_note: loc.admin_note || null,
               land_fraction: loc.land_fraction, land_class: loc.land_fraction >= 0.5 ? 'land' : (loc.land_fraction > 0 ? 'coastal (mostly ocean)' : 'ocean'), resolution_deg: loc.resolution_deg,
               extraction: cyc.extraction, source: cyc.source, definition: loc.cell_definition };
    m.time = {
      model_init_utc: cyc.model_init_utc, forecast_hour: sel.fhour, valid_time_utc: sel.valid_time_utc, published_at_utc: sel.published_at_utc, acquired_at_utc: sel.acquired_at_utc,
      last_update_utc: fm.generated_at_utc,
      horizon_hours: round(hoursBetween(valid, init), 2),                                   // 1. forecast horizon = valid - model init
      acquisition_age_hours: round(hoursBetween(now, acq), 2),                              // 2. acquisition age = now - DRIFT acquisition
      data_age_hours: cyc.age_hours,                                                        //    model-cycle age
      data_availability_lead_hours: sel.operational_lead_hours,                             //    valid - acquired (NOT a warning lead)
      warning_lead: { hours: null, status: 'NO_ALERT_PUBLISHED',                            // 3. operational warning lead = valid - alert publication
                      note: 'Automated alerts are disabled and no DRIFT alert has been published, so no warning lead exists. Never inferred from the model run.' },
      cycle_status: sel.status, max_age_hours: GFS_MAX_AGE_H };
    m.hours = cyc.hours.map(function (h) { return { index: h.index, fhour: h.fhour, valid_time_utc: h.valid_time_utc, status: HOUR_STATUS[h.status] || 'INVALID', raw_status: h.status, lead_hours: h.operational_lead_hours }; });
    m.selected = sel.index;
    var rowFor = function (key, label, dec) {
      var f = rec.fields[key], r = { key: key, label: label, decimals: dec, source: cyc.source, valid_time_utc: sel.valid_time_utc, acquired_at_utc: sel.acquired_at_utc };
      if (!f) { r.value = null; r.status = 'MISSING'; r.reason = 'not published in the fields artifact'; r.required_source = 'GFS 0.25° ' + key; return r; }
      var ok = f.value != null && isFinite(f.value);
      r.value = ok ? f.value : null; r.unit = f.unit; r.kind = f.kind; r.kind_label = KIND[f.kind] || f.kind; r.definition = f.definition; r.is_observation = f.is_observation === true;
      r.age_hours = round(hoursBetween(now, acq), 1);
      if (!ok) { r.status = 'MISSING'; r.reason = 'no value at this cell and forecast hour'; r.required_source = 'GFS 0.25° field behind ' + key; }
      else { r.status = HOUR_STATUS[f.status] || 'INVALID'; if (r.status !== 'FRESH') r.reason = f.status === 'PAST_VALID_TIME' ? 'valid time has passed; last model value for that hour' : f.status === 'STALE' ? 'forecast cycle older than ' + GFS_MAX_AGE_H + ' h' : 'provenance check failed'; }
      return r;
    };
    m.sections = SECTIONS.map(function (s) { return { id: s[0], title: s[1], rows: s[2].map(function (r) { return rowFor(r[0], r[1], r[2]); }) }; });
    var conv = m.sections[2].rows.filter(function (r) { return r.key === 'conv850'; })[0];
    if (conv) m.sections[2].rows.push(Object.assign({}, conv, { key: 'div850', label: 'Horizontal divergence 850 hPa', value: conv.value == null ? null : -conv.value, kind: 'DERIVED_PHYSICAL', kind_label: 'calculated from GFS', definition: 'div(V) = -convergence at 850 hPa (positive = divergence)' }));
    m.not_ingested = NOT_INGESTED;
    m.charts = CHARTS.map(function (c) { var f = rec.fields[c[0]]; return { key: c[0], label: c[1], decimals: c[2], unit: f ? f.unit : '', values: f ? f.series : [], fhours: cyc.hours.map(function (h) { return h.fhour; }) }; });
    m.satellite = satelliteModel(store, cid, now);
    var st = rec.static || {};
    m.terrain = st.status === 'AVAILABLE' ? { status: 'STATIC', source: st.source, gauge_source: st.gauge_source, limits: st.limits || [],
      rows: [['Elevation, mean', st.elev_mean_m, 'm'], ['Elevation, min', st.elev_min_m, 'm'], ['Elevation, max', st.elev_max_m, 'm'], ['Relief', st.relief_m, 'm'], ['Slope, mean', st.slope_mean_deg, '°'], ['Slope, 90th percentile', st.slope_p90_deg, '°'],
             ['Flood gauge in cell', st.gauge_in_cell ? 'yes' : 'no', ''], ['Nearest gauge', st.nearest_gauge_id ? st.nearest_gauge_id + (st.nearest_gauge_km != null ? ' (' + st.nearest_gauge_km + ' km)' : '') : 'none within 100 km', ''],
             ['Recorded gauge events', st.nearest_gauge_events, ''], ['of which rapid-onset', st.nearest_gauge_rapid_events, '']],
      note: 'Static terrain and a historical gauge record. Past gauge events are not current flooding; no live river levels, drainage network or soil-moisture observations are available.' }
      : { status: 'MISSING', reason: 'terrain/gauge context unavailable for this cell' };
    m.hazards = hazardsModel(rec);
    return m;
  }

  // ------------------------------------------------------------------------------------------------------------------------------
  // Part 2: DOM. Only runs in a browser with MapLibre.
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var utc = function (t) { return t ? String(t).slice(0, 16).replace('T', ' ') + 'Z' : '—'; };
  var num = function (v, d) { return v == null ? '—' : Number(v).toFixed(d == null ? 1 : d); };
  var LAYERS = [['cape', 'CAPE', 'J/kg', 3000], ['pwat', 'Precipitable water', 'mm', 70], ['k_index', 'K-index', '°C', 40], ['shear_850_500', '850–500 hPa shear', 'm/s', 25], ['t2m_c', '2 m temperature', '°C', 45],
                ['ws10', '10 m wind speed', 'm/s', 15], ['precip_3h', 'Rain, 3 h', 'mm', 20], ['elev', 'Elevation, SRTM mean (static)', 'm', 4000], ['sat_bt', 'Himawari-9 B13 BT (observed)', '°C', null]];
  var RAMP = ['#0b1a33', '#12437a', '#1f7ea8', '#38bdf8', '#7dd3a8', '#e6c64a', '#f59e0b', '#ef4444'];
  var rampColor = function (t) { var i = Math.max(0, Math.min(RAMP.length - 1, Math.round(t * (RAMP.length - 1)))); return RAMP[i]; };

  function create(getStore, opts) {
    opts = opts || {};
    var map = null, host = null, panel = null, sheetState = 'half', sel = { cid: null, hour: null }, layerKey = 'cape', raf = 0, lastModel = null, features = null, openCb = opts.onSelect || null;
    var now = function () { return opts.now ? opts.now() : Date.now(); };
    var STORE = function () { return getStore(); };

    function cellPoly(lat, lon, h) { return [[[lon - h, lat - h], [lon + h, lat - h], [lon + h, lat + h], [lon - h, lat + h], [lon - h, lat - h]]]; }
    function buildFeatures() {
      var S = STORE(); if (!S || !S.cells) return null;
      return { type: 'FeatureCollection', features: S.cells.map(function (c, i) { return { type: 'Feature', id: i, properties: { id: c[0], v: null, c: 'rgba(0,0,0,0)' }, geometry: { type: 'Polygon', coordinates: cellPoly(c[1], c[2], 0.125) } }; }) };
    }
    function layerValue(S, key, i, hour, nowMs) {
      if (key === 'elev') { var sr = S.staticRecord ? S.staticRecord(S.cells[i][0]) : null; return sr && sr.status === 'AVAILABLE' && sr.elev_mean_m != null ? sr.elev_mean_m : null; }
      if (key === 'sat_bt') { var b = satelliteModel(S, S.cells[i][0], nowMs); return b.status === 'FRESH' ? b.bt_p10_k - 273.15 : null; }
      var col = S.fcol[S.cells[i][0]]; if (col == null) return null;
      var arr = S.fields.fields[key] && S.fields.fields[key][hour]; var v = arr ? arr[col] : null; return v == null || !isFinite(v) ? null : v;
    }
    function paintLayer() {
      var S = STORE(); if (!map || !S || !S.fieldsOk || !map.getSource('insp-cells')) return;
      if (!features) features = buildFeatures(); if (!features) return;
      var def = LAYERS.filter(function (l) { return l[0] === layerKey; })[0] || LAYERS[0], nowMs = now();
      var hr = sel.hour == null ? S.defaultHour(nowMs) : sel.hour, st = S.record(S.cells[0][0], hr, nowMs).selected_hour.status;
      var fresh = def[0] === 'sat_bt' || def[0] === 'elev' || st === 'CURRENT';
      for (var i = 0; i < features.features.length; i++) {
        var v = layerValue(S, def[0], i, hr, nowMs), p = features.features[i].properties;
        p.v = v; p.c = v == null ? 'rgba(0,0,0,0)' : rampColor(def[3] ? Math.max(0, Math.min(1, v / def[3])) : Math.max(0, Math.min(1, (320 - (v + 273.15)) / 110)));
        p.o = v == null ? 0 : (fresh ? 0.55 : 0.22);
      }
      map.getSource('insp-cells').setData(features);
      var lg = host && host.querySelector('.di-legend');
      if (lg) lg.innerHTML = '<b>' + esc(def[1]) + '</b> · ' + (def[0] === 'sat_bt' ? 'latest scan' : def[0] === 'elev' ? 'static terrain' : 'f' + S.fields.meta.fhours[hr] + ' valid ' + utc(S.fields.meta.files[hr].valid_time_utc)) +
        (fresh ? '' : ' · <span class="di-chip di-STALE">STALE</span>') + '<div class="di-ramp"></div><span>' + (def[3] ? '0 → ' + def[3] : 'warm → cold') + ' ' + esc(def[2]) + '</span>';
    }

    function highlight(cid) {
      var S = STORE(); if (!map || !map.getSource('insp-sel')) return;
      var c = cid && S && S.idx[cid] != null ? S.cells[S.idx[cid]] : null;
      map.getSource('insp-sel').setData(c ? { type: 'Feature', properties: { id: cid }, geometry: { type: 'Polygon', coordinates: cellPoly(c[1], c[2], 0.125) } } : { type: 'FeatureCollection', features: [] });
    }

    function chartSvg(ch, selIdx) {
      var vals = ch.values || [], fin = vals.filter(function (v) { return v != null && isFinite(v); });
      if (!fin.length) return '<div class="di-muted">no data for this variable</div>';
      var lo = Math.min.apply(null, fin.concat([0])), hi = Math.max.apply(null, fin.concat([lo + 1e-9])), W = 232, H = 44, bw = W / vals.length;
      var bars = vals.map(function (v, i) {
        if (v == null || !isFinite(v)) return '<rect x="' + (i * bw + 2) + '" y="' + (H - 3) + '" width="' + (bw - 4) + '" height="3" class="di-bar-missing"><title>f' + ch.fhours[i] + ': missing</title></rect>';
        var h = Math.max(2, (v - lo) / (hi - lo) * (H - 6));
        return '<rect x="' + (i * bw + 2) + '" y="' + (H - h) + '" width="' + (bw - 4) + '" height="' + h + '" class="' + (i === selIdx ? 'di-bar-sel' : 'di-bar') + '" data-hour="' + i + '"><title>f' + ch.fhours[i] + ': ' + num(v, ch.decimals) + ' ' + esc(ch.unit) + '</title></rect>';
      }).join('');
      return '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(ch.label) + ' by forecast hour" class="di-svg">' + bars + '</svg>' +
        '<div class="di-axis"><span>f' + ch.fhours[0] + '</span><span>f' + ch.fhours[ch.fhours.length - 1] + '</span></div>';
    }

    function chip(status) { return '<span class="di-chip di-' + esc(status) + '">' + esc(status) + '</span>'; }
    function rowHtml(r) {
      var shown = r.value == null ? '<span class="di-miss">' + (r.reason ? 'MISSING' : '—') + '</span>' : '<b>' + num(r.value, r.decimals) + '</b> <span class="di-unit">' + esc(r.unit) + '</span>';
      var sub = r.value == null ? esc(r.reason || '') + (r.required_source ? ' · needs ' + esc(r.required_source) : '')
        : esc(r.kind_label) + ' · valid ' + utc(r.valid_time_utc) + ' · age ' + num(r.age_hours, 1) + ' h' + (r.reason ? ' · ' + esc(r.reason) : '');
      return '<div class="di-row" title="' + esc(r.definition || '') + '"><div class="di-l">' + esc(r.label) + '</div><div class="di-v">' + shown + '</div><div class="di-c">' + chip(r.status) + '</div><div class="di-sub">' + sub + '</div></div>';
    }
    function haz(m) {
      var H = m.hazards, h = '';
      var ts = H.thunderstorm;
      h += '<div class="di-haz"><div class="di-ht">Thunderstorm</div>' + (ts.status === 'AVAILABLE_EVALUATED'
        ? ts.leads.map(function (l) { return '<div class="di-row"><div class="di-l">+' + l.lead_h + ' h · valid ' + utc(l.valid_time_utc) + '</div><div class="di-v"><b>' + (l.probability == null ? '—' : Math.round(l.probability * 100) + '%') + '</b></div><div class="di-c">' + chip('UNVALIDATED').replace('UNVALIDATED', 'EVALUATED') + '</div></div>'; }).join('') +
          '<div class="di-sub">' + esc(ts.model_version) + ' · ' + esc(ts.value_type) + ' · ' + esc(ts.note) + '</div>'
        : '<div class="di-na">UNAVAILABLE</div><div class="di-sub">' + esc(ts.reason) + '</div>' + (ts.indicators ? '<div class="di-sub">Physical indicator only: instability band <b>' + esc(ts.indicators.instability_band || '—') + '</b> (conventional CAPE class, not a probability).</div>' : '')) + '</div>';
      var ex = H.extreme_rainfall;
      h += '<div class="di-haz"><div class="di-ht">Extreme rainfall</div><div class="di-na">' + (ex.short_duration && ex.short_duration.status === 'AVAILABLE_EVALUATED' ? 'EVALUATED' : 'SHORT-DURATION MODEL UNAVAILABLE') + '</div><div class="di-sub">' + esc((ex.short_duration && ex.short_duration.reason) || '') + '</div>' +
        (ex.heavy_rain_day ? '<div class="di-row"><div class="di-l">Heavy-rain-day score (1°, uncalibrated)</div><div class="di-v"><b>' + num(ex.heavy_rain_day.value, 3) + '</b></div><div class="di-c">' + chip('UNVALIDATED') + '</div><div class="di-sub">' + esc(ex.heavy_rain_day.event_definition || '') + '</div></div>' : '') +
        (ex.guidance ? '<div class="di-sub">GFS rainfall guidance (model, not observed): max 3 h ' + num(ex.guidance.max_3h_mm, 1) + ' mm at f' + ex.guidance.max_3h_fhour + ', ' + num(ex.guidance.accumulated_24h_mm, 1) + ' mm over the window.</div>' : '') + '</div>';
      h += '<div class="di-haz"><div class="di-ht">Cloudburst</div><div class="di-na">UNAVAILABLE</div><div class="di-sub">' + esc(ex.cloudburst.reason) + '</div></div>';
      var ff = H.flash_flood;
      h += '<div class="di-haz"><div class="di-ht">Flash flood</div><div class="di-na">UNAVAILABLE</div><div class="di-sub">' + esc(ff.prediction.reason) + '</div>' +
        (ff.screening ? '<div class="di-row"><div class="di-l">Terrain/rain screening index (not a probability)</div><div class="di-v"><b>' + num(ff.screening.value, 3) + '</b></div><div class="di-c">' + chip('UNVALIDATED') + '</div><div class="di-sub">Kept separate from any flood forecast.</div></div>' : '') + '</div>';
      return h;
    }
    function satHtml(s) {
      var head = '<div class="di-sat-h">' + chip(s.status) + ' <span>' + esc(s.source) + '</span></div>';
      if (s.status === 'MISSING') return head + '<div class="di-sub">' + esc(s.reason) + ' · needs ' + esc(s.required_source) + '</div>';
      var age = s.age_minutes >= 120 ? num(s.age_minutes / 60, 1) + ' h' : Math.round(s.age_minutes) + ' min';
      var rate = function (r) { return r == null ? 'n/a (earlier frame missing)' : (r > 0 ? '+' : '') + r.toFixed(1) + ' K/h ' + (r < -1 ? 'cooling' : r > 1 ? 'warming' : 'steady'); };
      return head + '<div class="di-row"><div class="di-l">Brightness temperature (p10 of cell pixels)</div><div class="di-v"><b>' + num(s.bt_p10_k - 273.15, 1) + '</b> <span class="di-unit">°C (' + num(s.bt_p10_k, 1) + ' K)</span></div><div class="di-c">' + chip(s.status) + '</div><div class="di-sub">observed · scan ' + utc(s.scene_nominal_utc) + ' · age ' + age + (s.status === 'STALE' ? ' · older than ' + s.max_age_minutes + ' min: last valid measurement, not current' : '') + '</div></div>' +
        '<div class="di-row"><div class="di-l">Coldest pixel</div><div class="di-v"><b>' + num(s.bt_min_k - 273.15, 1) + '</b> <span class="di-unit">°C</span></div><div class="di-c">' + chip(s.status) + '</div></div>' +
        '<div class="di-row"><div class="di-l">Pixels colder than 235 K</div><div class="di-v"><b>' + Math.round(s.cold_frac_235k * 100) + '</b> <span class="di-unit">% of ' + s.n_pixels + '</span></div><div class="di-c">' + chip(s.status) + '</div></div>' +
        '<div class="di-row"><div class="di-l">BT change, 30 min</div><div class="di-v">' + rate(s.dtdt_30_k_per_h) + '</div><div class="di-c">' + chip(s.dtdt_30_k_per_h == null ? 'MISSING' : s.status) + '</div></div>' +
        '<div class="di-row"><div class="di-l">BT change, 60 min</div><div class="di-v">' + rate(s.dtdt_60_k_per_h) + '</div><div class="di-c">' + chip(s.dtdt_60_k_per_h == null ? 'MISSING' : s.status) + '</div><div class="di-sub">between successive scans; negative = cooling (rising cloud top)</div></div>' +
        '<div class="di-sub">' + esc(s.coverage) + (s.quality === 'COARSE_VIEW_ANGLE' ? ' · view zenith ' + s.view_zenith_deg + '° here' : '') + '</div><div class="di-sub di-warn">' + esc(s.interpretation) + '</div>';
    }
    function html(m, mobile) {
      if (m.status !== 'OK') return '<div class="di-head"><div class="di-title">Grid cell</div><button class="di-x" data-act="close" aria-label="Close cell inspector">✕</button></div><div class="di-na">UNAVAILABLE</div><div class="di-sub">' + esc(m.reason) + '</div>';
      var c = m.cell, t = m.time, selH = m.hours[m.selected];
      var chips = m.hours.map(function (h) { return '<button class="di-hr' + (h.index === m.selected ? ' on' : '') + '" data-hour="' + h.index + '" aria-pressed="' + (h.index === m.selected) + '" title="valid ' + utc(h.valid_time_utc) + ' · ' + h.status + '">f' + h.fhour + '</button>'; }).join('');
      var sec = function (id, title, body, open) { return '<details class="di-sec" ' + (open ? 'open' : '') + '><summary>' + esc(title) + '</summary>' + body + '</details>'; };
      var prov = [['Cell ID', c.id], ['Coordinates', num(c.lat, 2) + '°N ' + num(c.lon, 2) + '°E'], ['Region', [c.district, c.state].filter(Boolean).join(', ') + (c.admin_method === 'nearest' ? ' (nearest)' : '') || '—'], ['Land / ocean', c.land_class + ' (' + Math.round(c.land_fraction * 100) + '% land)'],
        ['Source', c.source], ['Resolution', c.resolution_deg + '° (~28 km)'], ['Grid match', c.extraction], ['Model init', utc(t.model_init_utc)], ['Forecast hour', 'f' + String(t.forecast_hour).padStart(3, '0')], ['Valid time', utc(t.valid_time_utc) + ' ' + chip(HOUR_STATUS[t.cycle_status] || 'INVALID')],
        ['NOAA published', utc(t.published_at_utc) + ' (S3 Last-Modified)'], ['Acquired by DRIFT', utc(t.acquired_at_utc)], ['Last successful update', utc(t.last_update_utc)]];
      var times = '<div class="di-times"><div><span>Forecast horizon</span><b>+' + num(t.horizon_hours, 0) + ' h</b><i>valid − model init</i></div><div><span>Acquisition age</span><b>' + num(t.acquisition_age_hours, 1) + ' h</b><i>now − DRIFT acquisition</i></div>' +
        '<div><span>Warning lead</span><b class="di-miss">NONE</b><i>' + esc(t.warning_lead.note) + '</i></div></div><div class="di-sub">Data-availability lead (valid − acquired): ' + (t.data_availability_lead_hours != null ? '+' + num(t.data_availability_lead_hours, 1) + ' h' : 'none, valid time already past at acquisition') + ' — not a warning lead.</div>';
      var body = sec('prov', 'Location & provenance', '<div class="di-kv">' + prov.map(function (p) { return '<div><span>' + esc(p[0]) + '</span><b>' + (p[0] === 'Valid time' ? p[1] : esc(p[1])) + '</b></div>'; }).join('') + '</div>' + times, !mobile) +
        m.sections.map(function (s, i) { return sec(s.id, s.title, s.rows.map(rowHtml).join(''), !mobile || i < 1); }).join('') +
        sec('sat', 'Satellite observation (Himawari-9)', satHtml(m.satellite), true) +
        sec('series', 'Forecast-hour series (this cell)', m.charts.map(function (ch) { return '<div class="di-chart"><div class="di-ct">' + esc(ch.label) + ' <span class="di-unit">' + esc(ch.unit) + '</span></div>' + chartSvg(ch, m.selected) + '</div>'; }).join(''), !mobile) +
        sec('terrain', 'Terrain & hydrological context', m.terrain.status === 'STATIC'
          ? '<div class="di-sub">' + chip('STATIC') + ' ' + esc(m.terrain.source) + '</div>' + m.terrain.rows.map(function (r) { return '<div class="di-row di-slim"><div class="di-l">' + esc(r[0]) + '</div><div class="di-v"><b>' + esc(r[1] == null ? '—' : r[1]) + '</b> <span class="di-unit">' + esc(r[2]) + '</span></div></div>'; }).join('') +
            '<div class="di-sub">' + esc(m.terrain.gauge_source) + '</div><div class="di-sub di-warn">' + esc(m.terrain.note) + '</div>' + m.terrain.limits.map(function (l) { return '<div class="di-sub">• ' + esc(l) + '</div>'; }).join('')
          : '<div class="di-na">MISSING</div><div class="di-sub">' + esc(m.terrain.reason) + '</div>', false) +
        sec('hazard', 'Hazard status', haz(m), true) +
        sec('gaps', 'Not available', '<div class="di-sub">' + m.not_ingested.map(esc).join('<br>• ').replace(/^/, '• ') + '</div>', false);
      var layers = '<label class="di-lay">Map layer <select data-act="layer">' + LAYERS.map(function (l) { return '<option value="' + l[0] + '"' + (l[0] === layerKey ? ' selected' : '') + '>' + esc(l[1]) + '</option>'; }).join('') + '</select></label>';
      return (mobile ? '<div class="di-grab" data-act="grab" role="separator" aria-label="Drag to resize"><i></i></div>' : '') +
        '<div class="di-head"><div><div class="di-kicker">GRID CELL INSPECTOR</div><div class="di-title">' + num(c.lat, 2) + '°N ' + num(c.lon, 2) + '°E</div><div class="di-sub">' + esc(c.id) + ' · ' + esc([c.district, c.state].filter(Boolean).join(', ')) + ' · ' + esc(c.land_class) + '</div></div>' +
        '<button class="di-x" data-act="close" aria-label="Close cell inspector">✕</button></div><div class="di-hrs" role="group" aria-label="Forecast hour">' + chips + '</div>' + layers + '<div class="di-body">' + body + '</div>';
    }

    function render() {
      raf = 0; if (!panel) return;
      var S = STORE(), mobile = window.innerWidth < 700;
      if (!sel.cid) { panel.hidden = true; panel.classList.remove('open'); return; }
      lastModel = buildModel(S, sel.cid, sel.hour, now());
      if (lastModel.status === 'OK' && sel.hour == null) sel.hour = lastModel.selected;
      var scroll = panel.querySelector('.di-body'), top = scroll ? scroll.scrollTop : 0;
      panel.className = 'di-panel open ' + (mobile ? 'di-mobile di-' + sheetState : 'di-desktop'); panel.hidden = false;
      panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Grid cell inspector'); panel.setAttribute('data-cell', sel.cid); panel.setAttribute('data-hour', sel.hour == null ? '' : sel.hour);
      panel.innerHTML = html(lastModel, mobile);
      var b = panel.querySelector('.di-body'); if (b) b.scrollTop = top;
      highlight(sel.cid); paintLayer();
    }
    function schedule() {                                    // coalesce rapid switching: only the last selection renders
      if (raf) return;
      raf = 1;
      var run = function () { if (raf) render(); };           // render() resets raf; whichever of rAF / timer fires first renders, the other is a no-op
      if (typeof window.requestAnimationFrame === 'function') window.requestAnimationFrame(run);
      setTimeout(run, 120);                                   // background/throttled tabs do not run rAF
    }

    function announceHour() { try { window.dispatchEvent(new CustomEvent('drift-hour-changed', { detail: { hour: sel.hour } })); } catch (e) { /* ignore */ } }
    function select(cid, hour) {
      var S = STORE(); if (!S || !S.fieldsOk || S.idx[cid] == null) return false;
      var changed = sel.cid !== cid; sel.cid = cid; if (hour != null) sel.hour = hour; schedule();
      if (changed && openCb) try { openCb(cid); } catch (e) { /* host callback must not break the inspector */ }
      return true;
    }
    function selectAt(lat, lon) {
      var S = STORE(); if (!S || !S.fieldsOk) return false;
      var r = S.resolve(lat, lon); if (!r.covered) { toast('Not covered by the national grid (land cells only)'); return false; }
      return select(r.cell_id);
    }
    function toast(msg) { if (!host) return; var t = host.querySelector('.di-toast'); if (!t) return; t.textContent = msg; t.classList.add('on'); setTimeout(function () { t.classList.remove('on'); }, 2200); }
    function close() { sel.cid = null; highlight(null); schedule(); }
    function move(dLat, dLon) {
      var S = STORE(); if (!S || !sel.cid) return; var c = S.cells[S.idx[sel.cid]]; var r = S.resolve(c[1] + dLat, c[2] + dLon);
      if (r.covered) select(r.cell_id); else toast('No grid cell in that direction');
    }

    function attach(m, mapHost) {
      map = m; host = mapHost || m.getContainer().parentElement;
      if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
      panel = document.createElement('aside'); panel.className = 'di-panel'; panel.hidden = true; host.appendChild(panel);
      var ctl = document.createElement('div'); ctl.className = 'di-legend'; host.appendChild(ctl);
      var t = document.createElement('div'); t.className = 'di-toast'; t.setAttribute('role', 'status'); host.appendChild(t);
      var add = function () {
        if (map.getSource('insp-cells')) return;
        map.addSource('insp-cells', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addSource('insp-sel', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addLayer({ id: 'insp-fill', type: 'fill', source: 'insp-cells', paint: { 'fill-color': ['get', 'c'], 'fill-opacity': ['coalesce', ['get', 'o'], 0.5] } });
        map.addLayer({ id: 'insp-grid', type: 'line', source: 'insp-cells', minzoom: 5, paint: { 'line-color': 'rgba(148,163,184,0.18)', 'line-width': 0.6 } });
        map.addLayer({ id: 'insp-sel-fill', type: 'fill', source: 'insp-sel', paint: { 'fill-color': '#38bdf8', 'fill-opacity': 0.14 } });
        map.addLayer({ id: 'insp-sel-line', type: 'line', source: 'insp-sel', paint: { 'line-color': '#f8fafc', 'line-width': 2.4 } });
        paintLayer(); if (sel.cid) highlight(sel.cid);
      };
      if (map.isStyleLoaded()) add(); else map.once('load', add);
      map.on('styledata', function () { if (!map.getSource('insp-cells') && map.isStyleLoaded()) add(); });
      map.on('click', function (e) { selectAt(e.lngLat.lat, e.lngLat.lng); });
      map.on('mousemove', function (e) { var S = STORE(); if (S && S.fieldsOk) map.getCanvas().style.cursor = S.resolve(e.lngLat.lat, e.lngLat.lng).covered ? 'pointer' : ''; });
      var cv = map.getCanvas(); cv.setAttribute('tabindex', '0'); cv.setAttribute('aria-label', 'Interactive grid map. Arrow keys move the selected cell, Enter selects the map centre cell, Escape closes the inspector.');
      cv.addEventListener('keydown', function (e) {
        var k = e.key;
        if (k === 'ArrowUp') { move(0.25, 0); e.preventDefault(); } else if (k === 'ArrowDown') { move(-0.25, 0); e.preventDefault(); }
        else if (k === 'ArrowLeft') { move(0, -0.25); e.preventDefault(); } else if (k === 'ArrowRight') { move(0, 0.25); e.preventDefault(); }
        else if (k === 'Enter' || k === ' ') { if (!sel.cid) { var c = map.getCenter(); selectAt(c.lat, c.lng); } e.preventDefault(); }
        else if (k === 'Escape') { close(); }
      });
      panel.addEventListener('click', function (e) {
        var b = e.target.closest('[data-act],[data-hour]'); if (!b) return;
        if (b.dataset.act === 'close') { close(); cv.focus(); return; }
        if (b.dataset.hour != null) { sel.hour = +b.dataset.hour; announceHour(); schedule(); }
      });
      panel.addEventListener('change', function (e) { if (e.target.dataset.act === 'layer') { layerKey = e.target.value; paintLayer(); schedule(); } });
      panel.addEventListener('keydown', function (e) { if (e.key === 'Escape') { close(); cv.focus(); } });
      // mobile bottom sheet: drag the handle between peek / half / full; the map above stays interactive
      var y0 = null, s0 = null;
      panel.addEventListener('pointerdown', function (e) { var g = e.target.closest('[data-act=grab]'); if (g) { y0 = e.clientY; s0 = sheetState; g.setPointerCapture(e.pointerId); } });
      panel.addEventListener('pointerup', function (e) {
        if (y0 == null) return; var dy = e.clientY - y0, order = ['peek', 'half', 'full'], i = order.indexOf(s0); y0 = null;
        if (dy < -30) i = Math.min(2, i + 1); else if (dy > 30) { if (i === 0) { close(); return; } i = Math.max(0, i - 1); }
        sheetState = order[i]; schedule();
      });
      window.addEventListener('resize', schedule);
      if (sel.cid) schedule();            // re-attached after a tab switch recreated the map: restore panel, highlight and layer
    }

    return { getMap: function () { return map; }, attach: attach, select: select, selectAt: selectAt, close: close, setHour: function (h) { if (sel.hour === h) return; sel.hour = h; schedule(); }, setLayer: function (k) { layerKey = k; paintLayer(); schedule(); },
             refresh: function () { features = null; paintLayer(); schedule(); }, state: function () { return { cell_id: sel.cid, hour: sel.hour, layer: layerKey, sheet: sheetState, model_status: lastModel ? lastModel.status : null }; }, renderNow: render };
  }

  return { buildModel: buildModel, satelliteModel: satelliteModel, create: create, SECTIONS: SECTIONS, CHARTS: CHARTS, LAYERS: LAYERS };
});
