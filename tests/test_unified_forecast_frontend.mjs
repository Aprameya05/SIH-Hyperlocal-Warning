// Phase 36 Requirement 17: frontend tests for the Unified Forecast tab
// wired into index.html (GET /forecast/all, /forecast, /forecast/sources).
// Follows the exact pattern tests/test_location_engine_js.mjs already
// uses for assets/location_engine.js: extract the real source text and
// execute it directly via `new Function`, rather than mocking it out.
//
// Run with: node tests/test_unified_forecast_frontend.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

const failures = [];
function check(name, cond, detail) {
  const status = cond ? 'PASS' : 'FAIL';
  console.log(`  [${status}] ${name}${!cond && detail ? ' -- ' + detail : ''}`);
  if (!cond) failures.push(name);
}

// ---------------------------------------------------------------------
// 1. Nav wiring: the new tab exists and is consistently mapped in both
//    directions, and the page-render branch exists.
// ---------------------------------------------------------------------
check('NAV_TAB_LIST includes UNIFIED FORECAST',
  html.includes("'DASHBOARD', 'FORECAST', 'UNIFIED FORECAST', 'RADAR MAP'"));
check('hashToTab maps #unified -> UNIFIED FORECAST',
  html.includes("'#unified':'UNIFIED FORECAST'"));
check('tabToHash maps UNIFIED FORECAST -> #unified',
  html.includes("'UNIFIED FORECAST':'#unified'"));
check('render branch for UNIFIED FORECAST exists',
  html.includes("activeNavTab === 'UNIFIED FORECAST'") && html.includes('<UnifiedForecastPage />'));
check('existing FORECAST/RADAR MAP branches untouched',
  html.includes("activeNavTab === 'FORECAST' &&") && html.includes("activeNavTab === 'RADAR MAP' &&"));

// ---------------------------------------------------------------------
// 2. Extract and execute the pure helper functions added for Phase 36
//    (same extraction technique as test_location_engine_js.mjs).
// ---------------------------------------------------------------------
function extractBetween(src, startMarker, endMarker) {
  const i = src.indexOf(startMarker);
  if (i === -1) throw new Error(`startMarker not found: ${startMarker}`);
  const j = src.indexOf(endMarker, i + startMarker.length);
  if (j === -1) throw new Error(`endMarker not found: ${endMarker}`);
  return src.slice(i, j + endMarker.length);
}

const helpersSrc = extractBetween(
  html,
  "const UNIFIED_RISK_COLORS = {",
  "function unifiedProjectLatLon(lat, lon, w, h) {\n      const x = ((lon - UNIFIED_BBOX.lonMin) / (UNIFIED_BBOX.lonMax - UNIFIED_BBOX.lonMin)) * w;\n      const y = h - ((lat - UNIFIED_BBOX.latMin) / (UNIFIED_BBOX.latMax - UNIFIED_BBOX.latMin)) * h;\n      return [x, y];\n    }"
);

const sandbox = {};
// Wrap as a function body that assigns everything onto `sandbox`.
const wrapped = `
  const UNIFIED_RISK_COLORS = {${helpersSrc.slice(helpersSrc.indexOf('{') + 1)}
  sandbox.unifiedRiskColor = unifiedRiskColor;
  sandbox.unifiedIsHazardUnavailable = unifiedIsHazardUnavailable;
  sandbox.unifiedFilterRecordsByLead = unifiedFilterRecordsByLead;
  sandbox.unifiedProjectLatLon = unifiedProjectLatLon;
  sandbox.UNIFIED_BBOX = UNIFIED_BBOX;
`;
new Function('sandbox', wrapped)(sandbox);

// ---------------------------------------------------------------------
// 3. Risk color mapping -- never silently maps an unknown/NOT_AVAILABLE
//    risk string to a "real" risk color.
// ---------------------------------------------------------------------
check('unifiedRiskColor(LOW) is the LOW color', sandbox.unifiedRiskColor('LOW') === '#22c55e');
check('unifiedRiskColor(SEVERE) is the SEVERE color', sandbox.unifiedRiskColor('SEVERE') === '#ef4444');
check('unifiedRiskColor(NOT_AVAILABLE) is the muted color', sandbox.unifiedRiskColor('NOT_AVAILABLE') === '#475569');
check('unifiedRiskColor(undefined) falls back to NOT_AVAILABLE color, not LOW',
  sandbox.unifiedRiskColor(undefined) === sandbox.unifiedRiskColor('NOT_AVAILABLE'));
check('unifiedRiskColor("garbage") falls back to NOT_AVAILABLE color',
  sandbox.unifiedRiskColor('garbage') === sandbox.unifiedRiskColor('NOT_AVAILABLE'));

// ---------------------------------------------------------------------
// 4. Null/unavailable-state truthfulness (Requirements 5/6)
// ---------------------------------------------------------------------
check('null probability is unavailable', sandbox.unifiedIsHazardUnavailable({ probability: null, risk_category: 'NOT_AVAILABLE' }) === true);
check('undefined hazard block is unavailable', sandbox.unifiedIsHazardUnavailable(undefined) === true);
check('a real 0.0 probability is NOT treated as unavailable (0 is a legitimate low score, not missing)',
  sandbox.unifiedIsHazardUnavailable({ probability: 0.0, risk_category: 'LOW' }) === false);
check('a real nonzero probability is not unavailable',
  sandbox.unifiedIsHazardUnavailable({ probability: 0.42, risk_category: 'MODERATE' }) === false);

// ---------------------------------------------------------------------
// 5. Lead-time filtering / switching (Requirement 3)
// ---------------------------------------------------------------------
const fakeRecords = [2, 3, 4, 5, 6].flatMap(lh => (
  [{ cell_id: 'A', lead_hours: lh }, { cell_id: 'B', lead_hours: lh }]
));
for (const lh of [2, 3, 4, 5, 6]) {
  const filtered = sandbox.unifiedFilterRecordsByLead(fakeRecords, lh);
  check(`unifiedFilterRecordsByLead returns exactly 2 records for lead ${lh}h`, filtered.length === 2 && filtered.every(r => r.lead_hours === lh));
}
check('unifiedFilterRecordsByLead handles a missing/undefined records array without throwing',
  (() => { try { return Array.isArray(sandbox.unifiedFilterRecordsByLead(undefined, 3)); } catch { return false; } })());

// ---------------------------------------------------------------------
// 6. 992-cell grid projection (Requirement 2) -- bbox corners and a known
//    real cell project inside the SVG canvas, never out of bounds.
// ---------------------------------------------------------------------
const W = 560, H = 620;
const [xMin, yMax] = sandbox.unifiedProjectLatLon(sandbox.UNIFIED_BBOX.latMin, sandbox.UNIFIED_BBOX.lonMin, W, H);
const [xMax, yMin] = sandbox.unifiedProjectLatLon(sandbox.UNIFIED_BBOX.latMax, sandbox.UNIFIED_BBOX.lonMax, W, H);
check('southwest corner (latMin,lonMin) projects to (x=0, y=H)', xMin === 0 && yMax === H);
check('northeast corner (latMax,lonMax) projects to (x=W, y=0)', xMax === W && yMin === 0);
const [xVobl, yVobl] = sandbox.unifiedProjectLatLon(13.0, 77.0, W, H); // IND_13.0_77.0, the real VOBL canonical cell
check('VOBL cell (13.0N, 77.0E) projects within the SVG canvas bounds',
  xVobl >= 0 && xVobl <= W && yVobl >= 0 && yVobl <= H);

// ---------------------------------------------------------------------
// 7. data/unified_forecast.json contract -- the offline fallback artifact
//    the frontend reads when the live API is unreachable (Requirement 12).
// ---------------------------------------------------------------------
const artifactPath = path.join(ROOT, 'data', 'unified_forecast.json');
if (fs.existsSync(artifactPath)) {
  const artifact = JSON.parse(fs.readFileSync(artifactPath, 'utf8'));
  check('offline artifact has exactly 4960 records', artifact.n_records === 4960 && artifact.records.length === 4960);
  check('offline artifact records expose TS/CB/FF with risk_category/probability/status',
    ['TS', 'CB', 'FF'].every(hz => {
      const r = artifact.records[0][hz];
      return r && 'probability' in r && 'risk_category' in r && 'status' in r;
    }));
  const ffRecord = artifact.records.find(r => r.FF.probability !== null);
  check('no FF record in the offline artifact ever carries a non-null probability',
    ffRecord === undefined);
  const unavailableSample = artifact.records.find(r => r.TS.status === 'OUT_OF_DOMAIN_STATION_ONLY');
  check('an OUT_OF_DOMAIN_STATION_ONLY TS record has risk_category NOT_AVAILABLE, never LOW',
    unavailableSample && unavailableSample.TS.risk_category === 'NOT_AVAILABLE');
} else {
  check('offline artifact data/unified_forecast.json exists (run scripts/phase34_build_unified_forecast.py)', false);
}

// ---------------------------------------------------------------------
// 8. Error/fallback handling: the component source itself must attempt
//    the live API first and fall back to the offline artifact, labeling
//    forecast_source honestly, rather than fabricating a live claim.
// ---------------------------------------------------------------------
check('component attempts the live /forecast/all endpoint first',
  html.includes("await fetch('/forecast/all'"));
check('component falls back to the offline artifact path on live failure',
  html.includes('UNIFIED_OFFLINE_ARTIFACT_PATH'));
check("component labels the offline path forecast_source as 'OFFLINE_ARTIFACT', never claiming it is live",
  html.includes("setForecastSource('OFFLINE_ARTIFACT')"));
check('UI text distinguishes OFFLINE ARTIFACT from LIVE API rather than hiding the distinction',
  html.includes('OFFLINE ARTIFACT (deterministic fallback -- not a live API call)'));

// ---------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------
console.log(`\n${failures.length === 0 ? 'ALL TESTS PASSED' : failures.length + ' TEST(S) FAILED'}`);
if (failures.length > 0) {
  console.log('Failures:', failures.join(', '));
  process.exit(1);
}
