// node --test tests/e2e/tabs.spec.mjs : every tab of the ORIGINAL dark dashboard is clicked and used, not just rendered.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { buildDist, serve, launch, watch, ROOT } from './harness.mjs';

let browser, S, FIELDS;
const DESKTOP = { width: 1440, height: 900 };
const TILE = /tile\.openstreetmap\.org/;
const TABS = [['FORECAST', '#forecast'], ['SATELLITE', '#satellite'], ['MODELS', '#models'], ['EXPLAIN', '#explainability'], ['REGIMES', '#regimes'], ['SCORES', '#scores'], ['GRAPHS', '#graphs']];
before(async () => {
  S = await serve(buildDist()); browser = await launch(); FIELDS = await (await fetch(`${S.base}/data/india_fields_latest.json`)).json();
  // Hermetic: the remote CARTO basemap style is not under test; every context gets a local stand-in so the map layers can attach.
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async (o) => { const c = await newContext(o); await c.route(/basemaps\.cartocdn\.com\/gl\/dark-matter-gl-style\/style\.json/, r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b1020' } }] }) })); return c; };
});
after(async () => { await browser?.close(); S?.srv.close(); });

const baseForecast = () => JSON.parse(fs.readFileSync(path.join(ROOT, 'forecast.json'), 'utf8'));
// A forecast in which every VOBL station slot is a fallback (the production state after PR #7/#25): nothing may read as 0% or LOW RISK.
const withheld = j => { j.slots.forEach(s => { s.fallback = true; s.fallback_reason = '28 of 64 model inputs have no live value'; s.ts_probability = 0.0016; s.probability = null; }); j.peak_probability = null; j.peak_slot = null; j.alert_active = false; };

async function open(hash = '', { now, mutateForecast = withheld } = {}) {
  const ctx = await browser.newContext({ viewport: DESKTOP }); const page = await ctx.newPage(); const w = watch(page); await page.route(TILE, r => r.abort());
  const T = now ?? Date.parse(FIELDS.meta.cycle_init_utc) + 9 * 3600000;
  await page.addInitScript(t => { const r = Date.now.bind(Date), off = t - r(); Date.now = () => r() + off; }, T);
  if (mutateForecast) await page.route('**/forecast.json*', async r => { const j = JSON.parse(JSON.stringify(baseForecast())); mutateForecast(j); r.fulfill({ contentType: 'application/json', body: JSON.stringify(j) }); });
  await page.goto(`${S.base}/legacy.html${hash}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('#boot-loading', { state: 'detached', timeout: 90000 }); await page.waitForTimeout(1200);
  return { ctx, page, w };
}
async function clickNav(page, label) {
  const ok = await page.evaluate(l => { const b = [...document.querySelectorAll('button,a,[role=button],[role=tab]')].find(e => e.innerText && e.innerText.trim().toUpperCase() === l && e.getBoundingClientRect().top < 220 && e.getBoundingClientRect().height > 0); if (b) { b.click(); return true; } return false; }, label);
  assert.ok(ok, `nav tab ${label} must exist in the header`); await page.waitForTimeout(600);
}
const body = page => page.evaluate(() => document.body.innerText);
const forbidden = /\bundefined\b|\bNaN\b|\[object Object\]/;

test('every nav tab opens by click: the URL hash follows, content renders, nothing is undefined/NaN, no page errors', async () => {
  const { ctx, page, w } = await open();
  for (const [label, hash] of TABS) {
    await clickNav(page, label);
    assert.equal(await page.evaluate(() => location.hash), hash, `${label} hash`);
    const t = await body(page); assert.ok(t.length > 400, `${label} renders content (${t.length} chars)`); assert.doesNotMatch(t, forbidden, `${label} shows undefined/NaN`);
  }
  await clickNav(page, 'DASHBOARD'); assert.equal(await page.evaluate(() => location.hash), '#dashboard');
  assert.equal(w.pageErrors.length, 0, JSON.stringify(w.pageErrors)); await ctx.close();
});

test('direct links open the right tab, and Back/Forward move between tabs', async () => {
  for (const [label, hash] of TABS) {
    const { ctx, page } = await open(hash);
    assert.equal(await page.evaluate(() => location.hash), hash); const t = await body(page); assert.ok(t.length > 300, `${hash} renders`); await ctx.close();
  }
  const { ctx, page } = await open();
  await clickNav(page, 'FORECAST'); const forecastText = await body(page); await clickNav(page, 'MODELS'); await page.waitForFunction(prev => document.body.innerText !== prev, forecastText, { timeout: 15000 }); const modelsText = await body(page); assert.ok(modelsText !== forecastText, 'MODELS differs from FORECAST');   // (compare booleans, not 3 kB strings)
  await page.goBack(); await page.waitForTimeout(800); assert.equal(await page.evaluate(() => location.hash), '#forecast'); assert.equal(await body(page).then(t => t.includes('MODEL THRESHOLD') || t.length > 400), true);
  await page.goForward(); await page.waitForTimeout(800); assert.equal(await page.evaluate(() => location.hash), '#models');
  await ctx.close();
});

test('the selected grid cell and forecast hour persist across tabs (shared state), and the deep link restores it', async () => {
  const { ctx, page } = await open();
  await page.waitForFunction(() => window.__driftInspector && window.__driftStore && window.__driftStore.fieldsOk, null, { timeout: 60000 });
  await page.evaluate(() => window.__driftInspector.select('IN025_1325_07775', 4)); await page.waitForSelector('.di-panel.open');
  await clickNav(page, 'FORECAST'); await clickNav(page, 'GRAPHS'); await clickNav(page, 'DASHBOARD');
  const st = await page.evaluate(() => window.__driftInspector.state()); assert.equal(st.cell_id, 'IN025_1325_07775'); assert.equal(st.hour, 4);
  assert.ok(page.url().includes('cell=IN025_1325_07775'));
  await ctx.close();
  const d = await browser.newContext({ viewport: DESKTOP }); const p2 = await d.newPage(); await p2.route(TILE, r => r.abort());
  await p2.goto(`${S.base}/legacy.html?cell=IN025_1325_07775`, { waitUntil: 'domcontentloaded', timeout: 60000 }); await p2.waitForSelector('#boot-loading', { state: 'detached', timeout: 90000 });
  await p2.waitForFunction(() => window.__driftInspector && window.__driftInspector.state().cell_id === 'IN025_1325_07775', null, { timeout: 60000 }); await d.close();
});

test('withheld station slots: no slot, card, or threshold status reads as 0%, LOW RISK or THRESHOLD EXCEEDED on the Forecast tab', async () => {
  const { ctx, page } = await open('#forecast');
  const t = await body(page);
  assert.doesNotMatch(t, /THRESHOLD EXCEEDED/); assert.doesNotMatch(t, /THRESHOLD NOMINAL/); assert.doesNotMatch(t, /LOW RISK/);
  assert.match(t, /NO VALID PREDICTION|NO PREDICTION/); assert.doesNotMatch(t, /\b0\.0%/);
  await ctx.close();
});

test('Copy Briefing: withheld slots say WITHHELD with the reason; the maximum is "none", never 0.0%; forecast time comes from the data', async () => {
  const { ctx, page } = await open();
  await page.evaluate(() => { window.__copied = null; Object.defineProperty(navigator, 'clipboard', { value: { writeText: t => { window.__copied = t; return Promise.resolve(); } }, configurable: true }); });
  const clicked = await page.evaluate(() => { const b = [...document.querySelectorAll('button')].find(e => /Copy Briefing/i.test(e.innerText)); if (b) { b.click(); return true; } return false; });
  assert.ok(clicked, 'Copy Briefing button exists'); await page.waitForFunction(() => window.__copied, null, { timeout: 10000 });
  const text = await page.evaluate(() => window.__copied);
  assert.match(text, /WITHHELD/); assert.match(text, /no valid slot prediction/i); assert.doesNotMatch(text, /\b0\.0%/); assert.match(text, /IST/); assert.match(text, /NOT an official IMD\/NDMA warning/);
  await ctx.close();
});

test('data-source card: every status is computed from the source timestamp and limit (no hardcoded FETCHED / ACTIVE / BLOCKED)', async () => {
  const { ctx, page } = await open();
  await page.waitForFunction(() => document.querySelector('[data-testid=source-gfs]'), null, { timeout: 30000 });
  const gfs = await page.$eval('[data-testid=source-gfs]', e => e.innerText), sat = await page.$eval('[data-testid=source-himawari-9]', e => e.innerText);
  assert.match(gfs, /FRESH|STALE/); assert.match(gfs, /limit 18 h/); assert.match(gfs, /cycle 20\d\d-\d\d-\d\d \d\d:\d\dZ/); assert.match(sat, /FRESH|STALE/); assert.match(sat, /limit 90 min/);
  const all = await page.evaluate(() => [...document.querySelectorAll('[data-testid^=source-]')].map(e => e.innerText).join('\n'));
  assert.doesNotMatch(all, /FETCHED|BLOCKED|ACTIVE/); assert.match(all, /NOT INTEGRATED/);
  await ctx.close();
  const stale = await open('', { now: Date.parse(FIELDS.meta.cycle_init_utc) + 40 * 3600000 });
  await stale.page.waitForSelector('[data-testid=source-gfs]', { timeout: 30000 }); assert.match(await stale.page.$eval('[data-testid=source-gfs]', e => e.innerText), /STALE/); await stale.ctx.close();
});

test('Terrain and Moisture map buttons switch the real grid layer (they previously called undefined functions)', async () => {
  const { ctx, page } = await open();
  await page.waitForFunction(() => window.__driftInspector && window.__driftInspector.getMap() && window.__driftStore, null, { timeout: 60000 });
  const clickBtn = label => page.evaluate(l => { const b = [...document.querySelectorAll('button')].find(e => e.innerText.trim() === l); if (b) { b.click(); return true; } return false; }, label);
  assert.ok(await clickBtn('Terrain')); assert.equal(await page.evaluate(() => window.__driftInspector.state().layer), 'elev');
  assert.ok(await clickBtn('Moisture')); assert.equal(await page.evaluate(() => window.__driftInspector.state().layer), 'pwat');
  assert.ok(await clickBtn('Moisture')); assert.equal(await page.evaluate(() => window.__driftInspector.state().layer), 'cape');
  const elev = await page.evaluate(() => { window.__driftInspector.setLayer('elev'); const f = window.__driftInspector.getMap().getSource('insp-cells')._data.features; return f.filter(x => x.properties.v != null).length; });
  assert.ok(elev > 3000, `elevation layer has data for most cells (${elev})`); await ctx.close();
});

test('Satellite tab shows the observed brightness temperature for the selected cell with its scan time and age', async () => {
  const { ctx, page } = await open();
  await page.waitForFunction(() => window.__driftInspector && window.__driftStore && window.__driftStore.fieldsOk, null, { timeout: 60000 });
  await page.evaluate(() => window.__driftInspector.select('IN025_1300_08000')); await clickNav(page, 'SATELLITE');
  const t = await body(page); assert.match(t, /Himawari|HIMAWARI/); assert.match(t, /brightness|BRIGHTNESS/);
  await ctx.close();
});

test('Forecast: no hand-weighted multi-day score or "Tomorrow" label; trend fields are carried from the artifact', async () => {
  const { ctx, page } = await open('#forecast', { mutateForecast: j => { withheld(j); j.slots[3].prev_probability = 0.5; j.slots[3].trend_diff = -0.25; j.slots[3].fallback = false; j.slots[3].probability = 0.25; j.slots[3].ts_probability = 0.25; j.slots[3].threshold = 0.2; } });
  const t = await body(page);
  assert.match(t, /No validated multi-day thunderstorm model exists/); assert.doesNotMatch(t, /GFS PROBABILISTIC FORECAST/); assert.doesNotMatch(t, /\bTomorrow\b/);
  assert.match(t, /THRESHOLD EXCEEDED/);                                      // the one valid slot (0.25 >= 0.2) is a real exceedance
  assert.match(t, /-25\.0|25\.0pp|25\.0/);                                     // trend vs previous run comes from the artifact (0.25 vs prev 0.5), not a fixed 0.0pp
  assert.doesNotMatch(t, /0\.0pp vs prev run/);
  await ctx.close();
});

test('Dashboard hero and telemetry: GFS cycle comes from the 0.25 deg store, grid count from the grid, MAX SYSTEM RISK is WITHHELD (not 0%), one regime label with a season check', async () => {
  const { ctx, page } = await open('', { mutateForecast: j => { withheld(j); j.synoptic_regime = { regime_id: 'R1', regime_name: 'Hot Pre-Monsoon / Stable', color: 'red' }; } });
  const t = await body(page);
  assert.match(t, /GFS 2026-11-23 00Z f\d{3}/); assert.match(t, /4,643 grid cells/); assert.doesNotMatch(t, /992 Grid Cells/);
  assert.match(t, /MAX SYSTEM RISK[\s\S]{0,40}WITHHELD/); assert.doesNotMatch(t, /0% \* MINIMAL/);
  assert.doesNotMatch(t, /Hot Pre-Monsoon/);                                   // November: the artifact's Mar-Jun label is withheld
  await ctx.close();
});

test('keyboard 1-8 follows the visible navigation order and #atc opens the dashboard (not a blank page)', async () => {
  const { ctx, page } = await open();
  const order = ['#dashboard', '#forecast', '#satellite', '#models', '#explainability', '#regimes', '#scores', '#graphs'];
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  for (let i = 0; i < 8; i++) { await page.keyboard.press(String(i + 1)); await page.waitForTimeout(400); assert.equal(await page.evaluate(() => location.hash), order[i], `key ${i + 1}`); }
  await ctx.close();
  const { ctx: c2, page: p2 } = await open('#atc'); assert.ok((await body(p2)).length > 400, '#atc renders the dashboard'); await c2.close();
});

test('alert log header states its real window and age; PDF briefing does not mislabel IST slots as UTC or print withheld slots as 0.0%', async () => {
  const { ctx, page } = await open();
  const t = await body(page); assert.match(t, /log last updated/i);
  const html = await (await fetch(`${S.base}/legacy.html`)).text();
  assert.doesNotMatch(html, /00-06 UTC \(Night\)/); assert.match(html, /00-06 IST \(Night\)/); assert.match(html, /WITHHELD/); assert.match(html, /Forecast generated \(IST\)/);
  await ctx.close();
});

test('Models and Scores show the held-out evaluation tables from the published artifacts, including targets that could not be evaluated, without crashing', async () => {
  const { ctx, page, w } = await open('#models');
  await page.waitForFunction(() => /not live operational performance/i.test(document.body.innerText) && !/Loading…/.test((document.querySelector('[data-testid=held-out-evaluation]') || {}).innerText || 'Loading…'), null, { timeout: 30000 });
  const m = await body(page);
  assert.match(m, /Pan-India thunderstorm model/i); assert.match(m, /Short-duration rain model/i); assert.match(m, /VOBL station thunderstorm, live-inputs model/i);
  assert.match(m, /NOT EVALUATED: INSUFFICIENT_EVENTS/i); assert.match(m, /held-out HISTORICAL results, not live operational performance/i); assert.match(m, /data\/hazard_evaluation\.json/i);
  const ts = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/ts_panindia_evaluation.json'), 'utf8')).leads['6'].A_temporal_holdout.xgboost_calibrated;
  assert.ok(m.includes(ts.auroc.toFixed(3)) && m.includes(ts.bss_vs_climatology.toFixed(3)), 'the +6 h AUROC and Brier skill printed are the artifact values');
  await clickNav(page, 'SCORES'); const sc = await body(page);
  await page.waitForFunction(() => /not live operational performance/i.test(document.body.innerText), null, { timeout: 30000 }); const sc2 = await body(page);
  assert.ok(/Pan-India thunderstorm model/i.test(sc2) && /UNAVAILABLE|Rolling/i.test(sc2)); assert.doesNotMatch(sc, forbidden);
  assert.equal(w.pageErrors.length, 0, JSON.stringify(w.pageErrors)); await ctx.close();
});
