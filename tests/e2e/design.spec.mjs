// node --test tests/e2e/design.spec.mjs : the premium command-centre interface: backdrop, expandable inspector cards with real values, motion preferences, controls.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { buildDist, serve, launch, watch } from './harness.mjs';

let browser, S, FIELDS;
const DESKTOP = { width: 1440, height: 900 }, MOBILE = { width: 390, height: 844 };
const CELL = 'IN025_1975_07600';
before(async () => {
  S = await serve(buildDist()); browser = await launch(); FIELDS = await (await fetch(`${S.base}/data/india_fields_latest.json`)).json();
  const nc = browser.newContext.bind(browser);   // hermetic basemap style
  browser.newContext = async (o) => { const c = await nc(o); await c.route(/basemaps\.cartocdn\.com\/gl\/dark-matter-gl-style\/style\.json/, r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ version: 8, sources: {}, layers: [] }) })); return c; };
});
after(async () => { await browser?.close(); S?.srv.close(); });

async function open(viewport = DESKTOP, opts = {}) {
  const ctx = await browser.newContext({ viewport, hasTouch: viewport.width < 700, ...opts }); const page = await ctx.newPage(); const w = watch(page); await page.route(/tile\.openstreetmap\.org/, r => r.abort());
  await page.goto(S.base + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForFunction(() => window.__drift && window.__drift.ready && window.__drift.ready() && window.__drift.state.store && window.__drift.state.store.fieldsOk, null, { timeout: 60000 });
  return { ctx, page, w };
}
const ci = FIELDS_ => FIELDS_.cells.indexOf(CELL);

test('the Earth backdrop loads and is the page background; panels are glass over it', async () => {
  const { ctx, page } = await open();
  const r = await page.evaluate(async () => { const u = new URL('earth_sunrise.webp', location.href).href; const res = await fetch(u); return { ok: res.ok, type: res.headers.get('content-type'), bg: getComputedStyle(document.body).backgroundImage }; });
  assert.equal(r.ok, true); assert.match(r.type, /image\/webp/); assert.match(r.bg, /earth_sunrise\.webp/);
  assert.match(await page.evaluate(() => getComputedStyle(document.querySelector('.card')).backdropFilter), /blur/);
  await ctx.close();
});

test('inspector cards: all eight categories, collapsed by default with real summaries; expanding animates a gauge to the real value and shows every metric for the selected cell and hour', async () => {
  const { ctx, page, w } = await open();
  await page.evaluate(id => { window.__drift.selectCell(id); }, CELL);
  await page.waitForSelector('.acc[data-acc=inst]');
  const keys = await page.$$eval('.acc', e => e.map(x => x.dataset.acc)); assert.deepEqual(keys, ['prov', 'inst', 'moist', 'wind', 'temp', 'precip', 'sat', 'ser', 'ter']);
  assert.equal(await page.$$eval('.acc.open', e => e.length), 0, 'nothing is expanded until the user asks');
  const hour = await page.evaluate(() => window.__drift.state.hour), idx = FIELDS.cells.indexOf(CELL), f = FIELDS.fields;
  const sum = await page.$eval('.acc[data-acc=inst] .sum', e => e.innerText.replace(/\s+/g, ' '));
  assert.ok(sum.includes(Math.round(f.cape[hour][idx]) + ' J/kg') && sum.includes(f.lftx[hour][idx].toFixed(1)) && sum.includes(f.total_totals[hour][idx].toFixed(1)), 'collapsed summary shows real CAPE, LI and Total Totals: ' + sum);
  await page.click('.acc[data-acc=inst] > button');
  assert.equal(await page.getAttribute('.acc[data-acc=inst] > button', 'aria-expanded'), 'true');
  const target = await page.$eval('.acc[data-acc=inst] .gauge .val', e => +e.dataset.target), ratio = Math.max(0, Math.min(1, f.cape[hour][idx] / 3000));
  assert.ok(Math.abs(target - 157.08 * (1 - ratio)) < 0.05, `gauge arc encodes the real CAPE (${target} vs ${157.08 * (1 - ratio)})`);
  await page.waitForFunction(t => { const e = document.querySelector('.acc[data-acc=inst] .gauge .val'); return e && Math.abs(parseFloat(getComputedStyle(e).strokeDashoffset) - t) < 1; }, target, { timeout: 5000 });
  const rows = await page.$$eval('.acc[data-acc=inst] .rows .r', e => e.map(x => x.innerText.replace(/\s+/g, ' ').trim()));
  for (const [label, key, dec] of [['CAPE', 'cape', 0], ['CIN', 'cin', 0], ['Lifted index', 'lftx', 1], ['K-index', 'k_index', 1], ['Total Totals', 'total_totals', 1], ['850–500 hPa lapse rate', 'lapse_850_500', 2]]) {
    const r = rows.find(x => x.startsWith(label)); assert.ok(r && r.includes(f[key][hour][idx].toFixed(dec)), `${label}: ${r} vs ${f[key][hour][idx]}`);
  }
  // another forecast hour: the card stays open, values follow the hour
  const next = (hour + 1) % FIELDS.meta.files.length;
  await page.click(`#timeline [data-hr="${next}"]`); await page.waitForSelector('.acc[data-acc=inst].open');
  const r2 = (await page.$$eval('.acc[data-acc=inst] .rows .r', e => e.map(x => x.innerText.replace(/\s+/g, ' ').trim()))).find(x => x.startsWith('CAPE'));
  assert.ok(r2.includes(Math.round(f.cape[next][idx]) + ' J/kg'), `hour ${next}: ${r2}`);
  await page.click('.acc[data-acc=inst] > button'); assert.equal(await page.$$eval('.acc.open', e => e.length), 0);   // collapse
  assert.equal(w.pageErrors.length, 0, JSON.stringify(w.pageErrors)); await ctx.close();
});

test('every expanded category shows its metrics from the artifact; satellite, terrain and the forecast-hour series are present with provenance', async () => {
  const { ctx, page } = await open(); await page.evaluate(id => { window.__drift.selectCell(id); }, CELL); await page.waitForSelector('.acc[data-acc=ter]');
  const hour = await page.evaluate(() => window.__drift.state.hour), idx = FIELDS.cells.indexOf(CELL), f = FIELDS.fields;
  for (const k of ['moist', 'wind', 'temp', 'precip', 'sat', 'ser', 'ter', 'prov']) await page.click(`.acc[data-acc=${k}] > button`);
  const grab = async k => page.$eval(`.acc[data-acc=${k}]`, e => e.innerText.replace(/\s+/g, ' '));
  assert.ok((await grab('moist')).includes(f.pwat[hour][idx].toFixed(1)));
  assert.ok((await grab('wind')).includes(f.shear_850_500[hour][idx].toFixed(1)) && (await grab('wind')).includes(f.ws10[hour][idx].toFixed(1)));
  assert.ok((await grab('temp')).includes(f.t2m_c[hour][idx].toFixed(1)));
  assert.ok((await grab('precip')).includes(f.precip_3h[hour][idx].toFixed(1)));
  assert.match(await grab('sat'), /Himawari|observed|UNAVAILABLE|STALE|FRESH|unavailable|scan|withheld/i);
  assert.equal(await page.$$eval('.acc[data-acc=ser] .spark', e => e.length), 6, 'six series charts');
  assert.match(await grab('ter'), /Elevation|unavailable/i);
  assert.match(await grab('prov'), /Acquired by DRIFT/); assert.match(await grab('prov'), /Operational lead/);
  await ctx.close();
});

test('prefers-reduced-motion: transitions and animations are effectively off', async () => {
  const { ctx, page } = await open(DESKTOP, { reducedMotion: 'reduce' });
  const d = await page.evaluate(() => { const b = document.querySelector('.btn'); return parseFloat(getComputedStyle(b).transitionDuration) * 1000; }); assert.ok(d <= 1, `transition ${d} ms`);
  await ctx.close();
});

test('controls do real things: notices popover lists live status and closes with Escape; layer button focuses the layer select; fullscreen toggles; left-rail cards collapse; zoom changes the map', async () => {
  const { ctx, page } = await open();
  await page.click('#bell'); assert.equal(await page.evaluate(() => document.getElementById('notif').classList.contains('on')), true);
  const t = await page.$eval('#notif', e => e.innerText); assert.match(t, /GFS cycle/); assert.match(t, /Operational alerting is off/); assert.match(t, /Himawari-9|satellite/i);
  await page.keyboard.press('Escape'); assert.equal(await page.evaluate(() => document.getElementById('notif').classList.contains('on')), false);
  await page.click('#layerbtn'); assert.equal(await page.evaluate(() => document.activeElement.id), 'lyr');
  await page.click('#fullbtn'); assert.equal(await page.evaluate(() => document.getElementById('mapwrap').classList.contains('fs')), true); await page.keyboard.press('Escape'); assert.equal(await page.evaluate(() => document.getElementById('mapwrap').classList.contains('fs')), false);
  const z0 = await page.evaluate(() => window.__drift.getMap().getZoom()); await page.click('#zin'); await page.waitForFunction(z => window.__drift.getMap().getZoom() > z + 0.3, z0, { timeout: 5000 });
  await page.click('#controls h2'); assert.equal(await page.evaluate(() => document.getElementById('controls').classList.contains('collapsed')), true);
  await page.click('#controls h2'); assert.equal(await page.evaluate(() => document.getElementById('controls').classList.contains('collapsed')), false);
  await ctx.close();
});

test('mobile: the inspector is a draggable bottom sheet (peek, half, full) over a full-width map; nothing overflows horizontally', async () => {
  const { ctx, page } = await open(MOBILE);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.equal(await page.evaluate(() => document.querySelector('section.right').dataset.sheet), 'peek');
  await page.evaluate(id => { window.__drift.selectCell(id); }, CELL); await page.waitForFunction(() => document.querySelector('section.right').dataset.sheet === 'half');
  await page.focus('#sheetgrab'); await page.keyboard.press('Enter'); assert.equal(await page.evaluate(() => document.querySelector('section.right').dataset.sheet), 'full');
  await page.keyboard.press('Enter'); assert.equal(await page.evaluate(() => document.querySelector('section.right').dataset.sheet), 'peek');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  await ctx.close();
});
