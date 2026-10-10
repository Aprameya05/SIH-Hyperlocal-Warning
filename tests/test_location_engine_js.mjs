// Regression test for assets/location_engine.js, run with: node tests/test_location_engine_js.mjs
// Ported from the unapplied patch bundle's test_location_engine.py checks
// (that file tested a location_engine.py reference module which never
// existed in this repo -- only the JS port does -- so this test exercises
// the real, restored assets/location_engine.js directly via Node, against
// the actual field names backend/pipeline.py writes).

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');

const src = fs.readFileSync(path.join(ROOT, 'assets', 'location_engine.js'), 'utf8');
const sandbox = { window: {} };
new Function('window', src)(sandbox.window);
const E = sandbox.window.DriftLocationEngine;

const failures = [];
function check(name, cond, detail) {
  const status = cond ? 'PASS' : 'FAIL';
  console.log(`  [${status}] ${name}${!cond && detail ? ' -- ' + detail : ''}`);
  if (!cond) failures.push(name);
}

function makeGrid() {
  return {
    grid_step_deg: 1.0,
    generated_at_utc: '2026-09-30T12:00:00Z',
    gfs_cycle: '2026-09-30 00Z', gfs_fhour: 9,
    grid_cells: [
      { lat: 13.0, lon: 77.0, thunderstorm_probability: 0.43,
        cloudburst_probability: 0.18, flash_flood_probability: 0.27,
        flash_flood_probability_terrain_adjusted: 0.34,
        terrain: { available: true, elevation_m: 800, slope_deg: 1.0, flood_susceptibility: 0.6 },
        cape: 1500, cin: -20, k_index: 34, totals_totals: 46,
        wind_shear_ms: 12, pwat_mm: 42, ctt_c: -55,
        convergence_s: 1e-4, ctt_drop_rate_c_hr: 3.2, qpe_mm: 5.0 },
      { lat: 28.6, lon: 77.2, thunderstorm_probability: 0.10,
        cloudburst_probability: 0.02, flash_flood_probability: 0.01,
        flash_flood_probability_terrain_adjusted: 0.01,
        terrain: { available: false, reason: 'outside DEM coverage bbox' },
        cape: 300, cin: -5, k_index: 20, totals_totals: 38,
        wind_shear_ms: 5, pwat_mm: 20, ctt_c: -20,
        convergence_s: null, ctt_drop_rate_c_hr: null, qpe_mm: 0.0 },
    ],
  };
}

function makeForecast() {
  return {
    generated_at_ist: '2026-09-30 18:08 IST',
    gfs_cycle: '2026-09-30 00Z f009',
    peak_probability: 0.62, peak_slot: 2,
    slots: [
      { slot: 0, ts_probability: 0.05, primary: false },
      { slot: 2, ts_probability: 0.62, primary: true,
        lead_time: { available: true, hours: 4.5, valid_from: 'x', valid_to: 'y' } },
    ],
  };
}

function makeHimawari() {
  return { vobl_bt_celsius: -60.2, min_bt_50km: -70.1, bt_trend_1h: -4.5,
           storm_detected: true, fetched_at_utc: '2026-09-30T12:30:00Z' };
}

console.log('test_nearest_cell_exact_and_snap');
{
  const grid = makeGrid();
  const m = E.findNearestCell(grid, 13.0, 77.0);
  check('exact cell match found', m.available && m.cell.lat === 13.0);
  check('distance_km near 0', m.distance_km < 1.0, m.distance_km);
  const m2 = E.findNearestCell(grid, 0.0, 0.0);
  check('far point rejected (beyond snap radius)', m2.available === false);
}

console.log('test_vobl_domain_boundary');
{
  const aIn = E.voblApplicability(E.VOBL_LAT, E.VOBL_LON);
  check('VOBL itself is in-domain', aIn.in_domain, aIn.distance_km);
  const aOut = E.voblApplicability(12.2958, 76.6394); // Mysuru, ~130km away
  check('Mysuru is NOT in VOBL domain', aOut.in_domain === false, aOut.distance_km);
  check('reason explains station-specific / not applicable', aOut.reason.includes('station-specific'));
}

console.log('test_bundle_at_vobl_shows_station_forecast_applicable');
{
  const bundle = E.buildLocationBundle(E.VOBL_LAT, E.VOBL_LON, makeGrid(), makeForecast(), makeHimawari());
  check('VOBL in_domain True at VOBL coordinates', bundle.vobl.in_domain === true);
  check('station_forecast present', bundle.vobl.station_forecast !== null);
  check('Himawari available near VOBL', bundle.himawari.available === true);
}

console.log('test_bundle_far_from_vobl_marks_station_not_applicable');
{
  const bundle = E.buildLocationBundle(28.6, 77.2, makeGrid(), makeForecast(), makeHimawari());
  check('VOBL in_domain False far away', bundle.vobl.in_domain === false);
  check('station_forecast still retrievable for context', bundle.vobl.station_forecast !== null && bundle.vobl.in_domain === false);
  check('Himawari unavailable far from VOBL', bundle.himawari.available === false);
  check('pan_india still available for a matched cell', bundle.pan_india.available === true);
}

console.log('test_bundle_never_reuses_bengaluru_terrain_for_delhi_cell');
{
  const bundle = E.buildLocationBundle(28.6, 77.2, makeGrid(), null, null);
  check('Delhi-area cell terrain explicitly unavailable, not Bengaluru values', bundle.pan_india.terrain.available === false);
}

console.log('test_missing_data_sources_never_crash');
{
  const bundle = E.buildLocationBundle(13.0, 77.0, null, null, null);
  check('pan_india explicitly unavailable when grid not loaded', bundle.pan_india.available === false);
  check('vobl block still present with an honest reason', 'reason' in bundle.vobl);
  check('himawari explicitly unavailable when not loaded', bundle.himawari.available === false);
  check('gfs explicitly unavailable when pan_india unmatched', bundle.gfs.available === false);
}

console.log('test_lead_time_surfaced_from_primary_slot');
{
  const bundle = E.buildLocationBundle(E.VOBL_LAT, E.VOBL_LON, makeGrid(), makeForecast(), null);
  check('lead_time pulled from primary slot', bundle.lead_time !== null && bundle.lead_time.hours === 4.5);
}

console.log('test_haversine_known_distance');
{
  const d = E.haversineKm(E.VOBL_LAT, E.VOBL_LON, 12.2958, 76.6394);
  check('VOBL-Mysuru haversine distance in plausible 100-160km band', d > 100 && d < 160, d);
}

console.log('test_cb_probability_source_defaults_to_physics_baseline_when_sync_has_not_run');
{
  const grid = makeGrid(); // cells here have NO cloudburst_probability_source field
  const b = E.buildLocationBundle(13.0, 77.0, grid, null, null);
  check('cb_probability_source defaults to the honest physics-baseline label',
        b.pan_india.cb_probability_source === 'backend/pipeline.py physics-baseline formula (not a trained model)',
        b.pan_india.cb_probability_source);
  check('cb_source_status is null when the sync script has not populated it', b.pan_india.cb_source_status === null);
}

console.log('test_cb_probability_source_passes_through_the_real_trained_model_label');
{
  const grid = makeGrid();
  // 2026-10-10: scripts/sync_unified_cb_into_pan_india_grid.py adds
  // these three fields when a real panindia_cb_v1 value exists for a
  // cell -- verify the JS engine surfaces them, not just the number.
  grid.grid_cells[0].cloudburst_probability_source = 'panindia_cb_v1 (Phase 21, LODO-validated XGBoost, REAL trained model)';
  grid.grid_cells[0].cloudburst_source_status = 'LIVE_AWS_GFS';
  const b = E.buildLocationBundle(13.0, 77.0, grid, null, null);
  check('cb_probability_source surfaces the real trained-model label when present',
        b.pan_india.cb_probability_source.includes('panindia_cb_v1'), b.pan_india.cb_probability_source);
  check('cb_source_status surfaces the real live source status', b.pan_india.cb_source_status === 'LIVE_AWS_GFS');
}

console.log('test_ff_research_status_defaults_to_no_coverage_when_sync_has_not_run');
{
  const grid = makeGrid(); // cells here have NO flash_flood_research_status field
  const b = E.buildLocationBundle(13.0, 77.0, grid, null, null);
  check('ff_research_status defaults to NO_RESEARCH_COVERAGE', b.pan_india.ff_research_status === 'NO_RESEARCH_COVERAGE');
  check('ff_research_pu_score is null when no real score exists', b.pan_india.ff_research_pu_score === null);
}

console.log('test_ff_research_status_passes_through_a_real_pu_score');
{
  const grid = makeGrid();
  grid.grid_cells[0].flash_flood_research_status = 'RESEARCH_ONLY_PU_SCORE_AVAILABLE';
  grid.grid_cells[0].flash_flood_research_pu_score = 1.0;
  const b = E.buildLocationBundle(13.0, 77.0, grid, null, null);
  check('ff_research_status surfaces real research coverage', b.pan_india.ff_research_status === 'RESEARCH_ONLY_PU_SCORE_AVAILABLE');
  check('ff_research_pu_score surfaces the real score', b.pan_india.ff_research_pu_score === 1.0);
  check('ff_probability (physics-baseline) is untouched', b.pan_india.ff_probability === grid.grid_cells[0].flash_flood_probability);
}

console.log('test_against_real_on_disk_grid_file (catches key-name mismatches against the real file)');
{
  const gridPath = path.join(ROOT, 'data', 'pan_india_grid.json');
  if (!fs.existsSync(gridPath)) {
    console.log('  [SKIP] data/pan_india_grid.json not present in this environment');
  } else {
    const realGrid = JSON.parse(fs.readFileSync(gridPath, 'utf8'));
    const m = E.findNearestCell(realGrid, E.VOBL_LAT, E.VOBL_LON);
    check('real on-disk grid_cells key is read correctly (not silently empty)', m.available === true, m.reason);
    if (m.available) check('matched cell has a thunderstorm_probability field', 'thunderstorm_probability' in m.cell);

    // 2026-10-10: backend/pipeline.py's "forecasts[]" is the real source
    // of truth (added in a later phase than the legacy flat-field
    // mirror); confirm the honesty fields (model_type, value_type,
    // is_calibrated_probability) genuinely exist on a real on-disk cell
    // -- not just asserted to exist in a synthetic test fixture.
    if (realGrid.forecasts && realGrid.forecasts.length) {
      const primary = realGrid.forecasts.find(f => f.is_primary) || realGrid.forecasts[0];
      const b = E.buildLocationBundle(E.VOBL_LAT, E.VOBL_LON, realGrid, null, null);
      check('real forecasts[] exists and has a primary entry', !!primary, 'no forecasts[] or no primary entry');
      check('pan_india.model_type surfaces the real on-disk value',
            b.pan_india.model_type === 'physics_baseline', b.pan_india.model_type);
      check('pan_india.is_calibrated_probability surfaces the real on-disk value (false for physics-baseline)',
            b.pan_india.is_calibrated_probability === false);
    } else {
      console.log('  [SKIP] real on-disk grid has no forecasts[] yet (stale/legacy-schema artifact)');
    }
  }
}

console.log();
if (failures.length) {
  console.log(`FAILED: ${failures.length} check(s) failed -> ${failures}`);
  process.exit(1);
} else {
  console.log('ALL CHECKS PASSED');
  process.exit(0);
}
