// node --test tests/test_cell_inspector.mjs : the inspector view-model equals the published artifacts for EVERY cell and hour, with honest statuses.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
const req = createRequire(import.meta.url);
const L = req('../frontend/assets/drift_location.js'), I = req('../frontend/assets/cell_inspector.js');
const j = f => (fs.existsSync(`data/${f}`) ? JSON.parse(fs.readFileSync(`data/${f}`, 'utf8')) : null);
const docs = { grid: j('india_grid_025.json'), places: j('india_places.json'), fields: j('india_fields_latest.json'), statics: j('india_static_025.json'), satellite: j('india_satellite_ctt_latest.json'),
  tsmodel: j('india_ts_model_latest.json'), rainmodel: j('india_rain_model_latest.json'), live: j('drift_live.json') };
const mk = mut => { const d = JSON.parse(JSON.stringify(docs)); if (mut) mut(d); return L.create(d); };
const S = mk(), meta = docs.fields.meta, files = meta.files, init = Date.parse(meta.cycle_init_utc), reg = docs.fields.registry;
const NOW = init + 9 * 3600000;

test('EVERY cell x EVERY hour x EVERY displayed variable equals the artifact (value, unit) and carries its own status, source and timestamps', () => {
  let n = 0;
  const keys = I.SECTIONS.flatMap(s => s[2].map(r => r[0]));
  for (let ci = 0; ci < docs.fields.cells.length; ci++) {
    const cid = docs.fields.cells[ci];
    for (let h = 0; h < files.length; h++) {
      const m = I.buildModel(S, cid, h, NOW);
      assert.equal(m.status, 'OK'); assert.equal(m.cell.id, cid); assert.equal(m.time.forecast_hour, files[h].fhour);
      const rows = Object.fromEntries(m.sections.flatMap(s => s.rows).map(r => [r.key, r]));
      for (const k of keys) {
        const r = rows[k], raw = docs.fields.fields[k][h][ci]; n++;
        assert.equal(r.unit, reg[k].unit);
        assert.ok(r.source && r.valid_time_utc === files[h].valid_time_utc && r.acquired_at_utc === files[h].acquired_at_utc);
        if (raw == null) { assert.equal(r.value, null); assert.equal(r.status, 'MISSING'); assert.ok(r.reason && r.required_source); }
        else {
          assert.ok(Math.abs(r.value - raw) <= 0.5 * Math.pow(10, -reg[k].decimals) + 1e-9, `${cid} f${files[h].fhour} ${k}`);
          assert.ok(['FRESH', 'STALE', 'INVALID'].includes(r.status)); assert.equal(typeof r.age_hours, 'number');
        }
      }
      assert.equal(m.charts.length, I.CHARTS.length);
      for (const ch of m.charts) assert.deepEqual(ch.values.map(v => (v === 0 ? 0 : v)), docs.fields.fields[ch.key].map(a => (a[ci] === 0 ? 0 : a[ci])), `${cid} chart ${ch.key} must be this cell's series over all hours`);
    }
  }
  console.log(`coverage: ${docs.fields.cells.length} cells x ${files.length} hours x ${keys.length} variables = ${n} checked, plus chart series per cell`);
  assert.equal(n, docs.fields.cells.length * files.length * keys.length);
});

test('three time concepts stay separate and a warning lead is never claimed', () => {
  for (let h = 0; h < files.length; h++) {
    const t = I.buildModel(S, 'IN025_1325_07775', h, NOW).time, f = files[h];
    assert.equal(t.horizon_hours, Math.round((Date.parse(f.valid_time_utc) - init) / 36000) / 100);
    assert.equal(t.acquisition_age_hours, Math.round((NOW - Date.parse(f.acquired_at_utc)) / 36000) / 100);
    const lead = (Date.parse(f.valid_time_utc) - Date.parse(f.acquired_at_utc)) / 3600000;
    assert.equal(t.data_availability_lead_hours, lead > 0 ? Math.round(lead * 1000) / 1000 : null);
    assert.equal(t.warning_lead.hours, null); assert.equal(t.warning_lead.status, 'NO_ALERT_PUBLISHED');
  }
});

test('a stale cycle RETAINS the last values with STALE status and their timestamps (never shown as current, never dropped)', () => {
  const m = I.buildModel(S, 'IN025_1325_07775', 3, init + 40 * 3600000);
  const cape = m.sections[0].rows[0];
  assert.equal(cape.value, docs.fields.fields.cape[3][docs.fields.cells.indexOf('IN025_1325_07775')]);
  assert.equal(cape.status, 'STALE'); assert.ok(cape.valid_time_utc && cape.acquired_at_utc && cape.reason);
  assert.ok(m.sections.flatMap(s => s.rows).every(r => r.value == null || r.status === 'STALE'));
  assert.equal(m.time.cycle_status, 'STALE'); assert.ok(m.time.data_age_hours > 18);
});

test('missing values are MISSING with the reason and the required source, never zero', () => {
  const ci = docs.fields.cells.indexOf('IN025_1325_07775');
  const M = mk(d => { d.fields.fields.cape[3][ci] = null; });
  const r = I.buildModel(M, 'IN025_1325_07775', 3, NOW).sections[0].rows[0];
  assert.equal(r.value, null); assert.equal(r.status, 'MISSING'); assert.match(r.reason, /no value/); assert.match(r.required_source, /GFS/);
});

test('satellite block: observed brightness temperature with its own age/status; stale scans are retained and labelled; never a retrieved CTT', () => {
  const sat = docs.satellite, si = sat.cells.indexOf('IN025_1325_07775'), scene = Date.parse(sat.meta.scene_nominal_utc);
  const cur = I.buildModel(S, 'IN025_1325_07775', 3, scene + 20 * 60000).satellite, old = I.buildModel(S, 'IN025_1325_07775', 3, scene + 5 * 3600000).satellite;
  assert.equal(cur.status, 'FRESH'); assert.equal(cur.bt_p10_k, sat.ctt_p10_k[si]); assert.equal(cur.dtdt_60_k_per_h, sat.dtdt_60min_k_per_h[si]); assert.equal(cur.age_minutes, 20);
  assert.equal(old.status, 'STALE'); assert.equal(old.bt_p10_k, sat.ctt_p10_k[si]); assert.ok(old.age_minutes > 90);
  assert.equal(cur.is_retrieved_cloud_top_temperature, false); assert.match(cur.interpretation, /not a retrieved cloud-top temperature/i);
  const none = mk(d => { d.satellite = null; });
  assert.equal(I.buildModel(none, 'IN025_1325_07775', 3, NOW).satellite.status, 'MISSING');
});

test('hazards: a missing model is UNAVAILABLE with a reason; cloudburst and flash flood are never a probability; screening stays separate', () => {
  const h = I.buildModel(S, 'IN025_1325_07775', 3, NOW).hazards;
  assert.equal(h.thunderstorm.status, 'UNAVAILABLE'); assert.ok(h.thunderstorm.reason);
  assert.equal(h.extreme_rainfall.cloudburst.status, 'UNAVAILABLE'); assert.equal(h.flash_flood.prediction.status, 'UNAVAILABLE');
  assert.match(h.flash_flood.prediction.reason, /No validated/);
});

test('terrain and hydrology are STATIC context with source and the "historical, not live" caveat; unknown cells and a missing store are unavailable with a reason', () => {
  const m = I.buildModel(S, 'IN025_1325_07775', 3, NOW);
  assert.equal(m.terrain.status, 'STATIC'); assert.match(m.terrain.note, /not current flooding/); assert.ok(m.terrain.source && m.terrain.gauge_source);
  assert.equal(I.buildModel(S, 'NOPE', 3, NOW).status, 'UNAVAILABLE'); assert.equal(I.buildModel(null, 'x', 0).status, 'UNAVAILABLE');
});

test('hit-testing: every cell centre and a point near every cell corner resolve to that exact cell; adjacent cells differ; outside the grid is not covered', () => {
  let bad = 0;
  for (const c of S.cells) {
    const r = S.resolve(c[1], c[2]); if (!r.covered || r.cell_id !== c[0]) bad++;
    const e = S.resolve(c[1] + 0.12, c[2] - 0.12); if (!e.covered || e.cell_id !== c[0]) bad++;
  }
  assert.equal(bad, 0);
  const a = S.resolve(13.25, 77.75), b = S.resolve(13.25, 78.0), c = S.resolve(13.5, 77.75);
  assert.ok(a.cell_id !== b.cell_id && a.cell_id !== c.cell_id);
  assert.equal(S.resolve(27.7, 85.3).covered, false); assert.equal(S.resolve(-50, 10).covered, false);
});
