/* DRIFT location store (browser + Node). JavaScript twin of backend/location_service.py: the static site has no server,
 * so the same record logic runs here over the same published artifacts. tests/test_location_parity.py asserts parity.
 * Statuses are RECOMPUTED from timestamps; producer labels are never trusted. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DriftLocation = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var RES = 0.25, MODEL_MAX_AGE_H = 18, PAST_TOL_H = 1.5;
  var CAPE_BANDS = [[300, 'WEAK'], [1000, 'MARGINAL'], [2500, 'MODERATE'], [1e9, 'STRONG']];
  var IMD_BANDS = [[15.5, 'NO_HEAVY_RAIN'], [64.5, 'MODERATE'], [115.6, 'HEAVY'], [204.5, 'VERY_HEAVY'], [1e9, 'EXTREMELY_HEAVY']];
  var SOURCE = 'NOAA GFS 0.25 deg (AWS Open Data mirror)';
  var r2 = function (x, n) { var m = Math.pow(10, n); return Math.round(x * m) / m; };
  var clip = function (x) { return Math.max(0, Math.min(1, x)); };
  var T = function (s) { var d = Date.parse(s); return isNaN(d) ? null : d; };
  var key = function (a, b) { return a.toFixed(2) + ',' + b.toFixed(2); };
  var band = function (v, bands) { for (var i = 0; i < bands.length; i++) if (v < bands[i][0]) return bands[i][1]; };

  function hourStatus(now, init, valid, pub) {
    if (valid == null || pub == null) return 'UNAVAILABLE';
    if (pub < init || valid <= init || pub > now + 5 * 60000) return 'INVALID_PROVENANCE';
    if ((now - init) / 3600000 > MODEL_MAX_AGE_H) return 'STALE';
    if (valid < now - PAST_TOL_H * 3600000) return 'PAST_VALID_TIME';
    return 'CURRENT';
  }

  /* independent verification of the fields artifact (what can be recomputed in the browser) */
  function verifyFields(doc, grid) {
    var p = [];
    try {
      var m = doc.meta, init = T(m.cycle_init_utc);
      if (init == null || [0, 12].indexOf(new Date(init).getUTCHours()) < 0) p.push('cycle init missing or not 00/12Z');
      if (!grid) { p.push('grid missing'); return p; }
      if (doc.cells.length !== grid.cells.length || doc.cells[0] !== grid.cells[0][0] || doc.cells[doc.cells.length - 1] !== grid.cells[grid.cells.length - 1][0]) p.push('cell order differs from the grid');
      m.files.forEach(function (f, i) {
        var v = T(f.valid_time_utc), pub = T(f.published_at_utc), acq = T(f.acquired_at_utc);
        if (m.fhours[i] !== f.fhour) p.push('fhours/files mismatch');
        if (v !== init + f.fhour * 3600000) p.push('f' + f.fhour + ': valid time != init + fhour');
        if (pub == null || pub <= init + 30 * 60000) p.push('f' + f.fhour + ': publication time missing or implausibly early');
        else if (acq != null && acq < pub - 60000) p.push('f' + f.fhour + ': acquired before it was published');
        if (!f.messages_verified) p.push('f' + f.fhour + ': GRIB messages not verified');
        if (!(f.apcp_windows || []).some(function (w) { return w[0] === 0 && w[1] === f.fhour; })) p.push('f' + f.fhour + ': no verified 0-' + f.fhour + ' accumulation window');
      });
      Object.keys(doc.fields).forEach(function (k) {
        var v = doc.fields[k];
        if (v.length !== m.files.length || v.some(function (h) { return h.length !== doc.cells.length; })) p.push('field ' + k + ': shape mismatch');
      });
      var mx = 0, mn = 0; doc.fields.cape.forEach(function (h) { h.forEach(function (x) { if (x != null) { if (x > mx) mx = x; if (x < mn) mn = x; } }); });
      if (mn < 0 || mx > 10000) p.push('CAPE outside physical range');
      var sm = 0; doc.fields.soilw0_10.forEach(function (h) { h.forEach(function (x) { if (x != null && x > sm) sm = x; }); });
      if (sm > 1) p.push('soil moisture sentinel leaked into values');
    } catch (e) { p.push('malformed artifact: ' + e); }
    return p;
  }

  function create(a) {
    var grid = a.grid, places = a.places, fields = a.fields, statics = a.statics, live = a.live || null, ts = a.tsmodel || null, rain = a.rainmodel || null;
    var S = { grid: grid, cells: grid.cells, idx: {}, byLL: {}, states: grid.states, districts: grid.districts, fcol: {}, sidx: {}, cb: {}, ts: ts };
    grid.cells.forEach(function (c, i) { S.idx[c[0]] = i; S.byLL[key(c[1], c[2])] = i; });
    S.problems = fields ? verifyFields(fields, grid) : ['fields artifact missing'];
    S.fieldsOk = S.problems.length === 0; S.fields = S.fieldsOk ? fields : null;
    if (S.fieldsOk) fields.cells.forEach(function (c, i) { S.fcol[c] = i; });
    if (statics) statics.cells.forEach(function (c, i) { S.sidx[c] = i; });
    if (live && live.grid) {
      var cols = {}; live.grid.columns.forEach(function (n, i) { cols[n] = i; });
      live.grid.cells.forEach(function (r) { var v = r[cols.cb_daily_outlook]; if (v != null) S.cb[key(+r[0], +r[1])] = v; });
    }
    S.tsOk = !!(ts && ts.meta && ts.meta.model_evaluated); S.tsIdx = {};
    if (ts) ts.cells.forEach(function (c, i) { S.tsIdx[c] = i; });
    S.rain = rain; S.rainOk = !!(rain && rain.meta && rain.meta.model_evaluated); S.rainIdx = {};
    if (rain) rain.cells.forEach(function (c, i) { S.rainIdx[c] = i; });
    S.places = (places && places.places) || [];

    S.resolve = function (lat, lon) {
      var la = r2(Math.round(lat / RES) * RES, 2), lo = r2(Math.round(lon / RES) * RES, 2), i = S.byLL[key(la, lo)];
      if (i != null) return { covered: true, cell_id: S.cells[i][0], query: { lat: lat, lon: lon }, cell_centre: { lat: la, lon: lo } };
      var best = null, d = [-0.5, -0.25, 0, 0.25, 0.5];
      d.forEach(function (dy) { d.forEach(function (dx) { var j = S.byLL[key(r2(la + dy, 2), r2(lo + dx, 2))]; if (j != null) { var dist = Math.hypot(S.cells[j][1] - lat, S.cells[j][2] - lon); if (!best || dist < best[0]) best = [dist, j]; } }); });
      var out = { covered: false, query: { lat: lat, lon: lon }, cell_centre: { lat: la, lon: lo }, reason: 'the point lies in a 0.25 deg cell that is less than 50% Indian land (sea or another country)' };
      if (best) out.nearest_covered_cell = { cell_id: S.cells[best[1]][0], lat: S.cells[best[1]][1], lon: S.cells[best[1]][2], distance_deg: r2(best[0], 3) };
      return out;
    };
    S.locationInfo = function (cid) {
      var c = S.cells[S.idx[cid]], d = c[5] != null ? S.districts[c[5]] : null;
      return { cell_id: cid, lat: c[1], lon: c[2], land_fraction: c[3], state: c[4] != null ? S.states[c[4]] : null, district: d ? d.name : null,
               admin_method: c[6], resolution_deg: RES, cell_definition: grid.meta.cell_definition, admin_note: 'state/district describe the cell CENTRE point' };
    };
    S.search = function (q, limit) {
      q = (q || '').trim().toLowerCase(); if (q.length < 2) return [];
      var out = [];
      S.places.forEach(function (p) { var n = p.ascii.toLowerCase(); if (n.indexOf(q) === 0 || n.indexOf(q) >= 0) out.push({ type: 'place', name: p.name, cell_id: p.cell, lat: p.lat, lon: p.lon, population: p.pop, _r: [n === q ? 0 : n.indexOf(q) === 0 ? 1 : 2, -p.pop] }); });
      S.states.forEach(function (s) { if (s.toLowerCase().indexOf(q) >= 0) out.push({ type: 'state', name: s, _r: [s.toLowerCase() === q ? 0 : s.toLowerCase().indexOf(q) === 0 ? 1 : 2, 1e9] }); });
      var seen = {};
      S.districts.forEach(function (d) { var k = d.name + '|' + d.state; if (d.name.toLowerCase().indexOf(q) >= 0 && !seen[k]) { seen[k] = 1; out.push({ type: 'district', name: d.name, state: d.state, _r: [d.name.toLowerCase() === q ? 0 : d.name.toLowerCase().indexOf(q) === 0 ? 1 : 2, 1e9 - 1] }); } });
      out.sort(function (a, b) { return a._r[0] - b._r[0] || a._r[1] - b._r[1]; });
      return out.slice(0, limit || 10).map(function (r) { delete r._r; return r; });
    };
    S.hourMeta = function () { var m = S.fields.meta; return { init: T(m.cycle_init_utc), files: m.files }; };
    S.defaultHour = function (now) {
      var hm = S.hourMeta();
      for (var k = 0; k < hm.files.length; k++) { var v = T(hm.files[k].valid_time_utc); if (v != null && v >= now - PAST_TOL_H * 3600000) return k; }
      return hm.files.length - 1;
    };
    S.staticRecord = function (cid) {
      if (!statics || S.sidx[cid] == null) return { status: 'UNAVAILABLE' };
      var out = { status: 'AVAILABLE', kind: 'STATIC_GEOGRAPHY', source: statics.meta.terrain_source, gauge_source: statics.meta.gauge_source, limits: statics.meta.limits };
      var row = statics.values[S.sidx[cid]]; statics.columns.forEach(function (c, i) { out[c] = row[i]; });
      return out;
    };
    S.neighbours = function (cid, r) {
      var c = S.cells[S.idx[cid]], out = [];
      for (var dy = -r; dy <= r; dy++) for (var dx = -r; dx <= r; dx++) { var j = S.byLL[key(r2(c[1] + dy * RES, 2), r2(c[2] + dx * RES, 2))]; if (j != null) out.push(S.cells[j][0]); }
      return out;
    };
    S.record = function (cid, hour, now) {
      now = now == null ? Date.now() : now;
      if (S.idx[cid] == null) throw new Error('unknown cell ' + cid);
      var rec = { location: S.locationInfo(cid), now_utc: new Date(now).toISOString().replace(/\.\d+Z$/, 'Z') };
      rec.availability = { fields: S.fieldsOk ? 'AVAILABLE' : 'UNAVAILABLE', problems: S.fieldsOk ? [] : S.problems.slice(0, 5) };
      rec.static = S.staticRecord(cid);
      if (!S.fieldsOk) { rec.fields = {}; rec.hazards = S.hazards(cid, null, null, now); return rec; }
      var hm = S.hourMeta(), init = hm.init, ageH = (now - init) / 3600000;
      hour = hour == null ? S.defaultHour(now) : hour;
      var hours = hm.files.map(function (f, k) {
        // Operational lead = valid - the time DRIFT held the data (twin of backend/location_service.py; never negative).
        var leadH = (T(f.valid_time_utc) - T(f.acquired_at_utc)) / 3600000, lead = leadH > 0 ? Math.round(leadH * 1000) / 1000 : null;
        return { index: k, fhour: f.fhour, valid_time_utc: f.valid_time_utc, published_at_utc: f.published_at_utc, acquired_at_utc: f.acquired_at_utc,
                 status: hourStatus(now, init, T(f.valid_time_utc), T(f.published_at_utc)),
                 forecast_available_time_utc: f.acquired_at_utc, operational_lead_hours: lead, operational_lead_status: lead !== null ? 'OK' : 'NOT_APPLICABLE_PAST_VALID_TIME' };
      });
      rec.cycle = { model_init_utc: S.fields.meta.cycle_init_utc, age_hours: r2(ageH, 2), source: SOURCE, resolution_deg: RES, extraction: S.fields.meta.extraction, hours: hours,
                    availability_note: S.fields.meta.availability_note, operational_lead_hours: hours[hour].operational_lead_hours, operational_lead_status: hours[hour].operational_lead_status,
                    operational_lead_basis: 'valid time minus the time DRIFT acquired the NWP file; excludes dissemination to users' };
      rec.selected_hour = hours[hour];
      var col = S.fcol[cid], fl = {};
      Object.keys(S.fields.registry).forEach(function (name) {
        var reg = S.fields.registry[name], series = S.fields.fields[name].map(function (h) { return h[col]; });
        fl[name] = { value: series[hour], series: series, unit: reg.unit, kind: reg.kind, definition: reg.definition, is_observation: false, is_calibrated_probability: false,
                     status: series[hour] != null ? hours[hour].status : 'MISSING', safe_as_current: hours[hour].status === 'CURRENT' && series[hour] != null };
      });
      rec.fields = fl; rec.hazards = S.hazards(cid, fl, hours, now, hour);
      return rec;
    };
    S.hazards = function (cid, fl, hours, now, hour) {
      var st = S.staticRecord(cid);
      return { thunderstorm: S.tsHazard(cid, fl), extreme_rainfall: S.rainHazard(cid, fl, hours), flash_flood: S.ffHazard(cid, fl, st) };
    };
    S.tsHazard = function (cid, fl) {
      var h = { pathway: 'thunderstorm', indicators: null, model: { status: 'UNAVAILABLE', reason: 'no evaluated pan-India thunderstorm model artifact' } };
      if (!fl) return h;
      var cape = fl.cape.value, cin = fl.cin.value;
      h.indicators = { kind: 'DERIVED_PHYSICAL (conventional bands; NOT a probability)', instability_band: cape == null ? null : band(cape, CAPE_BANDS), cape: cape, cin: cin,
        lifted_index: fl.lftx.value, k_index: fl.k_index.value, total_totals: fl.total_totals.value, pwat_mm: fl.pwat.value, shear_850_500_ms: fl.shear_850_500.value,
        vertical_velocity_700_pa_s: fl.w700.value, cape_series: fl.cape.series,
        cap_strength: cin == null ? null : (cin <= -100 ? 'STRONG_CAP' : cin <= -25 ? 'MODERATE_CAP' : 'WEAK_CAP') };
      if (S.tsOk && S.tsIdx[cid] != null) {
        var m = S.ts, leads = {}, exp = {}, feats = m.meta.features, ix = S.tsIdx[cid];
        m.meta.leads.forEach(function (L) { leads[String(L)] = { value: m.leads[String(L)][ix], valid_time_utc: m.meta.valid_times[String(L)] }; var a = m.explanations[String(L)][ix]; exp[String(L)] = [0, 2, 4].map(function (k) { return { feature: feats[a[k]], contribution: a[k + 1] }; }); });
        h.model = { status: 'AVAILABLE_EVALUATED', model_version: m.meta.model_version, model_init_utc: m.meta.cycle_init_utc, value_type: m.meta.value_type, calibrated: m.meta.calibrated,
                    leads: leads, evaluation_ref: m.meta.evaluation_ref, explanation_top3_treeshap: exp, alert_eligible: false,
                    alert_note: 'automated alerts stay disabled: no recorded DRIFT publication time' };
      }
      return h;
    };
    S.shortDuration = function (cid) {
      var target = '3 h CMORPH rainfall at the 0.25 deg cell >= threshold (short-duration heavy rainfall; NOT cloudburst)';
      var out = { status: 'UNAVAILABLE', target: target, reason: (rain && rain.meta && rain.meta.reason) || 'no evaluated short-duration rainfall model artifact' };
      if (!(S.rainOk && S.rainIdx[cid] != null)) return out;
      var m = rain.meta, i = S.rainIdx[cid], feats = m.features, tg = {};
      m.targets.forEach(function (k) {
        var a = rain.explanations[k][i];
        tg[k] = { value: rain.targets[k][i], threshold_mm_per_3h: m.target_info[k].threshold_mm_per_3h, lead_h: m.target_info[k].lead_h, valid_time_utc: m.target_info[k].valid_time_utc, evaluation: m.target_info[k],
                  explanation_top3_treeshap: [0, 2, 4].map(function (j) { return { feature: feats[a[j]], contribution: a[j + 1] }; }) };
      });
      return { status: 'AVAILABLE_EVALUATED', target: target, model_version: m.model_version, model_init_utc: m.cycle_init_utc, value_type: m.value_type, calibrated: m.calibrated, not_cloudburst: true, evaluation_ref: m.evaluation_ref, limits: m.limits, targets: tg };
    };
    S.rainHazard = function (cid, fl, hours) {
      var c = S.cells[S.idx[cid]], cb = S.cb[key(Math.floor(c[1] + 0.5), Math.floor(c[2] + 0.5))];
      var h = { pathway: 'extreme_rainfall',
        heavy_rain_day_outlook: { value: cb == null ? null : cb, kind: 'MODEL_SCORE_UNCALIBRATED', status: cb == null ? 'UNAVAILABLE' : 'AVAILABLE', event_definition: '>=64.5 mm/24 h daily rainfall proxy; not a cloudburst' },
        short_duration_extreme_rainfall: S.shortDuration(cid), cloudburst: { status: 'UNAVAILABLE' }, model_guidance: null };
      if (!fl) return h;
      var p3 = fl.precip_3h.series, cum = fl.apcp_cum.series, nb = S.neighbours(cid, 2), P3 = S.fields.fields.precip_3h, cols = [];
      nb.forEach(function (n) { if (S.fcol[n] != null) cols.push(S.fcol[n]); });
      var extent = p3.map(function (_, k) { var v = cols.map(function (j) { return P3[k][j]; }).filter(function (x) { return x != null; }); return { cells_ge_10mm_3h: v.filter(function (x) { return x >= 10; }).length, cells_ge_25mm_3h: v.filter(function (x) { return x >= 25; }).length, n_cells: v.length }; });
      var valid = p3.filter(function (x) { return x != null; }), total = cum[cum.length - 1], mx = valid.length ? Math.max.apply(null, valid) : null;
      h.model_guidance = { kind: 'RAW_MODEL_OUTPUT / DERIVED_PHYSICAL (GFS guidance; not observed rain)', imd_category_note: 'IMD daily-rainfall classes applied to MODEL rainfall over the available window; not an observation', neighbourhood: '5x5 cells (+-0.5 deg), land cells only', precip_3h_series_mm: p3, accumulated_since_init_mm: cum, max_3h_mm: mx, max_3h_fhour: mx == null ? null : hours[p3.indexOf(mx)].fhour,
        accumulated_24h_mm: total, imd_24h_category: total == null ? null : band(total, IMD_BANDS), spatial_extent_within_half_degree: extent };
      return h;
    };
    S.ffHazard = function (cid, fl, st) {
      var h = { pathway: 'flash_flood', status: 'SCREENING_ONLY_UNVALIDATED', prediction: { status: 'UNAVAILABLE' }, screening_index: null,
        missing_inputs: ['observed flash-flood event labels with time and location', 'river network / catchment hydrology beyond 106 gauges', 'observed soil moisture', 'live gauge levels', 'validated model and evaluation'] };
      if (!fl || st.status !== 'AVAILABLE') return h;
      var p3 = fl.precip_3h.series, r6 = null;
      for (var i = 0; i < p3.length - 1; i++) if (p3[i] != null && p3[i + 1] != null) { var s = p3[i] + p3[i + 1]; if (r6 == null || s > r6) r6 = s; }
      var soil = fl.soilw0_10.value, s90 = st.slope_p90_deg, relief = st.relief_m;
      if (r6 != null && s90 != null && relief != null) {
        var trig = clip(r6 / 50), terr = clip(0.5 * s90 / 10 + 0.5 * relief / 1500), wet = soil == null ? null : clip((soil - 0.15) / 0.25), weff = wet == null ? 0.5 : wet;
        h.screening_index = { value: r2(trig * (0.4 + 0.3 * terr + 0.3 * weff), 3), kind: 'HEURISTIC_SCREENING_INDEX (0-1; NOT a probability; not validated)', formula: 'trigger * (0.4 + 0.3*terrain + 0.3*wetness)',
          notes: ['soil moisture is a GFS land-surface model state, not an observation', 'if soil moisture is missing the wetness term is held at a neutral 0.5 and this is stated', 'terrain uses slope/relief at ~1.2 km; hydrology beyond the nearest gauge is absent'], components: { rain_6h_max_mm: r6, rain_trigger: r2(trig, 3), terrain_factor: r2(terr, 3), soil_wetness: wet == null ? null : r2(wet, 3), soil_moisture_m3m3: soil } };
      }
      return h;
    };
    return S;
  }
  return { create: create, hourStatus: hourStatus, verifyFields: verifyFields };
});
