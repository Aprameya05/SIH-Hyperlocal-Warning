// M0 frontend integrity tests: assets/forecast_integrity.js (pure helpers) and static guarantees about index.html.
// Run with: node tests/test_m0_frontend_integrity.mjs
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');
const require = createRequire(import.meta.url);
const F = require(path.join(ROOT, 'frontend', 'assets', 'forecast_integrity.js'));
const html = fs.readFileSync(path.join(ROOT, 'frontend', 'index.html'), 'utf8');

const failures = [];
function check(name, cond, detail) {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${name}${!cond && detail ? ' -- ' + detail : ''}`);
  if (!cond) failures.push(name);
}

console.log('operational lead semantics');
check('lead = valid - ISSUED (live grid numbers: 03:00Z valid, 01:54:18Z issued -> ~1.1h)',
  Math.abs(F.operationalLeadHours('2026-10-31T03:00:00+00:00', '2026-10-31T01:54:18.290085+00:00') - 1.095) < 0.01);
check('unknown times -> null, never a made-up lead', F.operationalLeadHours(null, '2026-10-31T01:00:00Z') === null);
check('leadLabel(null) is UNAVAILABLE, not +4.5h', F.leadLabel(null) === 'UNAVAILABLE');
check('leadLabel(undefined) is UNAVAILABLE', F.leadLabel(undefined) === 'UNAVAILABLE');
check('leadLabel(<=0) says it is not a forecast', /NOT A FORECAST/.test(F.leadLabel(-5)) && /NOT A FORECAST/.test(F.leadLabel(0)));
check('leadLabel(4.5) formats +4.5h', F.leadLabel(4.5) === '+4.5h');
check('1.1h does not meet the 2-6h target; 4.5h does; 7h does not',
  !F.meetsTargetLead(1.1) && F.meetsTargetLead(4.5) && !F.meetsTargetLead(7) && !F.meetsTargetLead(null));
check('resolveOperationalLead prefers the backend field', F.resolveOperationalLead({ operational_lead_hours: 3.2, time_until_valid_hours: 9 }) === 3.2);
check('resolveOperationalLead computes from valid/issued when the backend field is absent (legacy grid)',
  Math.abs(F.resolveOperationalLead({ forecast_valid_at_utc: '2026-10-31T03:00:00+00:00', generated_at_utc: '2026-10-31T01:54:18+00:00' }) - 1.095) < 0.01);
check('resolveOperationalLead(null) is null', F.resolveOperationalLead(null) === null);

console.log('hazard block display state');
check('CURRENT block with probability is available', F.blockState({ temporal_status: 'CURRENT', probability: 0.3, presentable_as_current_forecast: true }).available === true);
check('HISTORICAL_REPLAY block is unavailable even if a number is present',
  F.blockState({ temporal_status: 'HISTORICAL_REPLAY', probability: 0.9 }).available === false);
check('NOT_LEAD_SPECIFIC says lead time not supported', /LEAD TIME NOT SUPPORTED/.test(F.blockState({ temporal_status: 'NOT_LEAD_SPECIFIC', probability: null }).headline));
check('a block with no temporal provenance is unavailable (legacy)', F.blockState({ probability: 0.5 }).available === false);
check('presentable_as_current_forecast=false overrides', F.isCurrentBlock({ temporal_status: 'CURRENT', probability: 0.5, presentable_as_current_forecast: false }) === false);
check('a missing block is unavailable', F.blockState(undefined).available === false);

console.log('legacy artifact guard (pre-M0 2024-replay artifact sample, tests/fixtures)');
const legacy = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests', 'fixtures', 'legacy_unified_sample.json'), 'utf8'));
const guarded = F.guardUnifiedArtifact(legacy);
let presented = 0, total = 0;
for (const r of guarded.records) for (const hz of ['TS', 'CB', 'FF']) {
  total++;
  const b = r[hz];
  if (b.probability != null || (b.extra && b.extra.pu_ranking_score != null && b.temporal_status === 'CURRENT')) presented++;
}
check(`no hazard value in the legacy artifact survives the client guard as a forecast (${presented}/${total})`, presented === 0);
check('guard flags the artifact', guarded.client_guard === 'LEGACY_ARTIFACT_WITHHELD');
const stillLive = guarded.records.filter(r => ['TS', 'CB', 'FF'].some(hz => /LIVE|TRAINED/.test(String(r[hz].source_status) + String(r[hz].status)) && r[hz].temporal_status !== 'CURRENT')).length;
check('withheld legacy blocks no longer claim TRAINED / MIXED_LIVE_SOURCES provenance (' + stillLive + ' do)', stillLive === 0);
check('guard preserves the original value for research', guarded.records.find(r => r.CB.historical_reference && r.CB.historical_reference.probability != null) !== undefined);
check('guard keeps the original timestamp with the preserved value', guarded.records.every(r => !r.CB.historical_reference || r.CB.historical_reference.model_init_time_utc));
check('guard does not mutate the input', legacy.records[0].CB.probability !== null || legacy.records[0].CB.temporal_status === undefined);
const v3 = { schema_version: 3, integrity: { applied: true }, records: [{ CB: { temporal_status: 'CURRENT', probability: 0.2, model_init_time_utc: '2026-10-31T00:00:00Z', valid_time_utc: '2026-10-31T04:00:00Z', generated_at_utc: '2026-10-31T02:00:00Z' } }] };
check('a fully-provenanced v3 artifact passes through untouched', F.guardUnifiedArtifact(v3) === v3);
const forged = { schema_version: 3, integrity: { applied: true }, records: [{ CB: { temporal_status: 'CURRENT', probability: 0.9 }, TS: { temporal_status: 'HISTORICAL_REPLAY', probability: 0.4, extra: { pu_ranking_score: 0.8 } } }] };
const fg = F.guardUnifiedArtifact(forged);
check('a self-declared integrity.applied flag is NOT trusted: a CURRENT block without provenance is withheld', fg.records[0].CB.probability === null && fg.records[0].TS.probability === null);
check('the client guard strips PU scores from withheld blocks', !(fg.records[0].TS.extra && fg.records[0].TS.extra.pu_ranking_score != null));

console.log('predictors');
check('null predictor -> UNAVAILABLE, never 0', F.formatPredictor(null) === 'UNAVAILABLE' && F.formatPredictor(undefined, 1, 'mm') === 'UNAVAILABLE');
check('a real 0 is shown as 0.0 (zero is a value, null is not)', F.formatPredictor(0, 1) === '0.0');
const grid = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'pan_india_grid.json'), 'utf8'));
const primary = grid.forecasts.find(f => f.is_primary);
const unavail = F.unavailablePredictors({}, primary.grid_cells);
check('the seven audited predictors are reported unavailable from the real grid',
  JSON.stringify(unavail) === JSON.stringify(['convergence_s', 'ctt_c', 'ctt_drop_rate_c_hr', 'k_index', 'pwat_mm', 'totals_totals', 'wind_shear_ms']), JSON.stringify(unavail));
check('backend list is preferred when present', JSON.stringify(F.unavailablePredictors({ unavailable_predictors: ['b', 'a'] }, [])) === '["a","b"]');
check('cape/cin/qpe are not reported unavailable', !unavail.includes('cape') && !unavail.includes('cin') && !unavail.includes('qpe_mm'));

console.log('alert-log classification');
const hit = F.classifyAlertEvent({ outcome: 'HIT', verified: true });
check('a recorded hit is labelled "as recorded", never VERIFIED', hit.label === 'HIT (as recorded)' && !/VERIFIED/i.test(hit.label));
check('a historical row is never official or a verified warning', hit.isOfficialWarning === false && hit.isVerifiedWarning === false);
check('PENDING is a model-generated pending alert', F.classifyAlertEvent({ outcome: 'PENDING', verified: false }).recordClass === 'MODEL_GENERATED_PENDING');
check('distinct classes for pending vs recorded', F.classifyAlertEvent({ outcome: 'PENDING' }).recordClass !== hit.recordClass);

console.log('index.html static guarantees');
const banned = [
  ['hard-coded "+4.5h Lead"', "'+4.5h Lead'"],
  ['hard-coded "+4.5h" fallback', "'+4.5h'"],
  ['hard-coded lead default 4.5', '?? 4.5'],
  ['fabricated CAPE default', 'cape_jkg ?? 1420'],
  ['fabricated CIN default', 'cin_jkg ?? -42'],
  ['fabricated PWAT default', 'pwat_mm ?? 44.8'],
  ['fabricated CTT default', 'vobl_bt_celsius ?? -58.2'],
  ['fabricated shear literal', 'wind_shear_ms: 18.4'],
  ['fabricated K-index default', 'k_index ?? 36.2'],
  ['fabricated convergence constant', '0.000042'],
  ['fabricated terrain literal', 'elevation_m: 915'],
  ['fabricated TS default 0.15', 'peak_probability ?? 0.15'],
  ['"VERIFIED HIT" label', '✓ VERIFIED HIT'],
  ['"verified events logged"', 'verified events logged'],
  ['unsupported "next 2-6 hours" hero claim', 'over the next 2-6 hours'],
  ['"No active severe-weather warning" (reads as official)', 'No active severe-weather warning'],
  ['stale static "(+4.5h) is the production primary" note', 'is the production primary'],
  ['invented valid time = generated time', 'forecast_valid_at_utc: usable ? usable.forecast_valid_at_utc : d.generated_at_utc'],
];
for (const [name, needle] of banned) check(`removed: ${name}`, !html.includes(needle));
check('helper script is loaded', html.includes('./assets/forecast_integrity.js'));
check('advisory disclaimer is present', html.includes('data-testid="advisory-disclaimer"') && html.includes('NOT an official IMD/NDMA warning'));
check('unavailable-predictors notice is present', html.includes('data-testid="unavailable-predictors"'));
check('unified page applies the client guard on both load paths', (html.match(/guardUnifiedArtifact\(/g) || []).length >= 2);
check('unified page shows the no-lead-forecast banner', html.includes('data-testid="no-lead-forecast-banner"'));
check('withheld hazards render an explicit unavailable state', (html.match(/data-testid="hazard-unavailable"/g) || []).length >= 2);
check('cloudburst is shown as unavailable (a heavy-rain-day proxy is not a cloudburst)', html.includes('no validated cloudburst model') && html.includes('is not a cloudburst'));
check('satellite temperature is labelled brightness temperature (observed), never a retrieved CTT or a GFS proxy', html.includes('CLOUD-TOP BRIGHTNESS TEMP (Himawari-9 B13, observed)') && html.includes('not a retrieved CTT') && !html.includes('CTT PROXY (GFS saturated-layer temp)'));
check('shear label matches the computation (850-500 hPa bulk difference; 850-200 hPa and 0-6 km are not ingested)', html.includes('WIND SHEAR (850–500 hPa bulk)') && html.includes('850–200 hPa and 0–6 km shear are not ingested') && !html.includes('850-200 hPa wind shear') && !html.includes('0-6 km bulk shear layer'));

if (failures.length) { console.log(`\n${failures.length} FAILED:`, failures); process.exit(1); }
console.log('\nALL TESTS PASSED');
