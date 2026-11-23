/* Bridge between the legacy station dashboard (legacy.html) and the validated 0.25 deg fields pipeline.
 *
 * The legacy page used to read diagnostics from the old 1 deg grid and the station forecast.json, where CAPE, CIN, PWAT, shear,
 * convergence, K-index and Total Totals were null. The validated store (drift_location.js: same artifacts, same record logic, parity-tested
 * against the Python service) already holds all of them with provenance. This module only SELECTS and LABELS what the store returns:
 *   - a value is shown only if the store marks it safe_as_current (status recomputed from timestamps); otherwise it is WITHHELD
 *   - nothing is defaulted: a missing value stays null and the reason is returned
 *   - every value carries unit, kind (model output / calculated), definition and the forecast-hour provenance
 * Pure functions (no DOM) so they are unit-tested in Node. Browser: DriftFieldsBridge.load() builds window.__driftStore. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DriftFieldsBridge = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var ARTIFACTS = { grid: 'data/india_grid_025.json', places: 'data/india_places.json', fields: 'data/india_fields_latest.json',
                    statics: 'data/india_static_025.json', tsmodel: 'data/india_ts_model_latest.json', rainmodel: 'data/india_rain_model_latest.json',
                    satellite: 'data/india_satellite_ctt_latest.json' };

  // Diagnostics shown on the legacy card, in display order: [key, label, decimals]
  var ROWS = [['cape', 'CAPE', 0], ['cin', 'CIN', 0], ['pwat', 'Precipitable water', 1], ['shear_850_500', '850–500 hPa bulk wind shear', 1],
              ['conv850', 'Convergence, 850 hPa', 2], ['conv10', 'Convergence, 10 m', 2], ['k_index', 'K-index', 1], ['total_totals', 'Total Totals', 1],
              ['lftx', 'Lifted index', 1], ['lapse_850_500', '850–500 hPa lapse rate', 2]];

  function load(fetchFn, DriftLocation, base) {
    var f = fetchFn || (typeof fetch === 'function' ? fetch.bind(null) : null);
    var get = function (u) { return f((base || '') + u + '?_=' + Date.now(), { cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).catch(function () { return null; }); };
    var keys = Object.keys(ARTIFACTS);
    return Promise.all(keys.map(function (k) { return get(ARTIFACTS[k]); })).then(function (docs) {
      var a = {}; keys.forEach(function (k, i) { a[k] = docs[i]; });
      if (!a.grid || !a.fields) return { store: null, problems: ['grid or fields artifact could not be loaded'] };
      return { store: (DriftLocation || root.DriftLocation).create({ grid: a.grid, places: a.places, fields: a.fields, statics: a.statics, tsmodel: a.tsmodel,
                                                                   rainmodel: a.rainmodel, satellite: a.satellite }), problems: [] };
    });
  }

  /** Everything the legacy card needs for ONE location and ONE forecast hour. `hour` null = the store's default (next valid) hour. */
  function diagnostics(store, lat, lon, hour, now) {
    now = now == null ? Date.now() : now;
    var out = { status: 'UNAVAILABLE', reason: null, now_utc: new Date(now).toISOString().replace(/\.\d+Z$/, 'Z'), values: {}, hours: [], selected_hour: null,
                cycle: null, cell: null, satellite: null, withheld: [] };
    if (!store) { out.reason = 'field artifacts could not be loaded'; return out; }
    if (!store.fieldsOk) { out.reason = 'atmospheric fields failed verification: ' + (store.problems[0] || 'unknown'); return out; }
    var r = store.resolve(lat, lon);
    if (!r.covered) { out.reason = 'location is not covered by the national grid'; return out; }
    var rec = store.record(r.cell_id, hour, now);
    if (!rec.cycle) { out.reason = 'no forecast cycle available'; return out; }
    out.cell = { id: r.cell_id, lat: r.cell_centre.lat, lon: r.cell_centre.lon, query: r.query };
    out.cycle = rec.cycle;
    out.hours = rec.cycle.hours;
    out.selected_hour = rec.selected_hour;
    out.satellite = rec.satellite;
    var cur = rec.selected_hour.status === 'CURRENT';
    out.status = cur ? 'OK' : (rec.selected_hour.status === 'STALE' ? 'STALE' : rec.selected_hour.status);
    ROWS.forEach(function (row) {
      var f = rec.fields[row[0]];
      if (!f) { out.values[row[0]] = { label: row[1], value: null, status: 'UNAVAILABLE', reason: 'not in the fields artifact' }; out.withheld.push(row[0]); return; }
      var safe = f.safe_as_current === true && f.value != null && isFinite(f.value);
      var v = { label: row[1], value: safe ? f.value : null, decimals: row[2], unit: f.unit, kind: f.kind, definition: f.definition, status: f.status,
                is_observation: f.is_observation === true, series: f.series, safe_as_current: safe };
      if (!safe) {
        v.reason = f.value == null ? 'no value at this cell and hour' : 'forecast ' + f.status.toLowerCase().replace(/_/g, ' ') + ': value withheld, not shown as current';
        out.withheld.push(row[0]);
      }
      out.values[row[0]] = v;
    });
    // horizontal divergence is the negative of convergence (same field, sign convention stated)
    var c = out.values.conv850;
    out.values.div850 = { label: 'Horizontal divergence, 850 hPa', value: c && c.value != null ? -c.value : null, decimals: 2, unit: c ? c.unit : null,
                          kind: 'DERIVED_PHYSICAL', definition: 'div(V) = -convergence at 850 hPa (positive = divergence)', status: c ? c.status : 'UNAVAILABLE',
                          is_observation: false, safe_as_current: !!(c && c.safe_as_current), reason: c && c.safe_as_current ? undefined : 'convergence unavailable' };
    return out;
  }

  /** Text for a value: number with unit, or the reason it is not shown. Never a default. */
  function fmt(v) {
    if (!v || v.value == null) return null;
    return Number(v.value).toFixed(v.decimals == null ? 1 : v.decimals);
  }

  return { ARTIFACTS: ARTIFACTS, ROWS: ROWS, load: load, diagnostics: diagnostics, fmt: fmt };
});
