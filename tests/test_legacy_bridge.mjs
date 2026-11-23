// node --test tests/test_legacy_bridge.mjs : the legacy dashboard bridge shows exactly the validated artifact values, for EVERY cell and hour.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
const req = createRequire(import.meta.url);
const L = req('../frontend/assets/drift_location.js'), B = req('../frontend/assets/legacy_fields_bridge.js');
const j = f => (fs.existsSync(`data/${f}`) ? JSON.parse(fs.readFileSync(`data/${f}`, 'utf8')) : null);
const docs = { grid: j('india_grid_025.json'), places: j('india_places.json'), fields: j('india_fields_latest.json'), statics: j('india_static_025.json'), satellite: j('india_satellite_ctt_latest.json') };
const mk = (mut) => { const d = JSON.parse(JSON.stringify(docs)); if (mut) mut(d); return L.create(d); };
const meta = docs.fields.meta, files = meta.files, init = Date.parse(meta.cycle_init_utc);
const nowAfterAcquire = Math.max(...files.map(f => Date.parse(f.acquired_at_utc))) + 60000;   // every file acquired; hours already past are not current

test('every cell x every forecast hour x every diagnostic equals the published artifact value (or is withheld, never invented)', () => {
  const S = mk(), cells = docs.fields.cells, F = docs.fields.fields;
  let checked = 0, shown = 0, withheld = 0;
  const reg = docs.fields.registry;
  for (let ci = 0; ci < cells.length; ci++) {
    const [lat, lon] = [S.cells[S.idx[cells[ci]]][1], S.cells[S.idx[cells[ci]]][2]];
    for (let h = 0; h < files.length; h++) {
      const d = B.diagnostics(S, lat, lon, h, nowAfterAcquire);
      assert.equal(d.cell.id, cells[ci]); assert.equal(d.selected_hour.fhour, files[h].fhour);
      for (const [k] of B.ROWS) {
        const raw = F[k] ? F[k][h][ci] : undefined, v = d.values[k];
        checked++;
        if (v.value != null) { shown++; assert.ok(Math.abs(v.value - raw) <= 0.5 * Math.pow(10, -(reg[k].decimals)) + 1e-9, `${cells[ci]} f${files[h].fhour} ${k}: ${v.value} vs ${raw}`); assert.equal(d.selected_hour.status, 'CURRENT'); assert.equal(v.unit, reg[k].unit); }
        else { withheld++; assert.ok(v.reason, `${k} withheld without a reason`); assert.ok(d.selected_hour.status !== 'CURRENT' || raw == null, `${cells[ci]} f${files[h].fhour} ${k} has a value (${raw}) but was withheld at a CURRENT hour`); }
      }
    }
  }
  console.log(`# coverage: ${cells.length} cells x ${files.length} hours x ${B.ROWS.length} diagnostics = ${checked} checks; shown ${shown}, withheld ${withheld}`);
  assert.ok(shown > 0 && checked === cells.length * files.length * B.ROWS.length);
});

test('a stale cycle is withheld everywhere and says so; the timestamp is still reported', () => {
  const S = mk();
  const d = B.diagnostics(S, 13.2, 77.71, 3, init + 40 * 3600000);
  assert.equal(d.status, 'STALE');
  for (const [k] of B.ROWS) { assert.equal(d.values[k].value, null); assert.match(d.values[k].reason, /withheld/); }
  assert.equal(d.values.div850.value, null);
  assert.ok(d.cycle.model_init_utc && d.cycle.age_hours > 18);
});

test('a missing value is null with a reason, never zero', () => {
  const S = mk(d => { d.fields.fields.cape[3][d.fields.cells.indexOf('IN025_1325_07775')] = null; });
  const d = B.diagnostics(S, 13.2, 77.71, 3, nowAfterAcquire - 0 + 3 * 3600000);
  if (d.selected_hour.status === 'CURRENT') { assert.equal(d.values.cape.value, null); assert.ok(d.values.cape.reason); assert.notEqual(B.fmt(d.values.cape), '0'); }
});

test('divergence is the negative of convergence; units and kinds come from the registry', () => {
  const S = mk(), now = init + 12 * 3600000;
  const d = B.diagnostics(S, 13.2, 77.71, 3, now);
  assert.equal(d.status, 'OK');
  assert.ok(Math.abs(d.values.div850.value + d.values.conv850.value) < 1e-12);
  assert.equal(d.values.cape.unit, 'J/kg'); assert.equal(d.values.pwat.unit, 'mm'); assert.equal(d.values.shear_850_500.kind, 'DERIVED_PHYSICAL');
  assert.equal(d.values.cape.kind, 'RAW_MODEL_OUTPUT'); assert.ok(d.values.cape.is_observation === false);
});

test('locations outside the grid and a missing store are unavailable with a reason', () => {
  const S = mk();
  assert.equal(B.diagnostics(S, 27.7, 85.3, null, init + 12 * 3600000).status, 'UNAVAILABLE');      // Kathmandu
  assert.equal(B.diagnostics(null, 13.2, 77.71, null).status, 'UNAVAILABLE');
});

test('operational lead per hour is valid minus acquisition and the satellite block is passed through unchanged', () => {
  const S = mk(), now = init + 12 * 3600000;
  for (let h = 0; h < files.length; h++) {
    const d = B.diagnostics(S, 13.2, 77.71, h, now), f = files[h], lead = (Date.parse(f.valid_time_utc) - Date.parse(f.acquired_at_utc)) / 3600000;
    assert.equal(d.selected_hour.operational_lead_hours, lead > 0 ? Math.round(lead * 1000) / 1000 : null);
    assert.equal(d.selected_hour.acquired_at_utc, f.acquired_at_utc); assert.equal(d.selected_hour.published_at_utc, f.published_at_utc);
  }
  const d = B.diagnostics(S, 13.2, 77.71, 3, now);
  assert.equal(d.satellite.kind, 'SATELLITE_OBSERVATION'); assert.equal(d.satellite.is_retrieved_cloud_top_temperature, false);
});
