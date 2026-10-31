// node --test tests/e2e/drift.spec.mjs   (needs system Chrome; uses the BUILT bundle, not the dev tree)
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { buildDist, serve, launch, watch, ROOT } from './harness.mjs';

let browser, S, dist;
const SHOTS = process.env.DRIFT_SHOTS ? path.join(ROOT, 'docs/demo_screenshots') : null;
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
const DESKTOP = { width: 1440, height: 900 }, MOBILE = { width: 390, height: 844 };
const TILE = /tile\.openstreetmap\.org/;

before(async () => { dist = buildDist(); S = await serve(dist); browser = await launch(); });
after(async () => { await browser?.close(); S?.srv.close(); });

async function open(viewport, hash = '', { blockTiles = false, shiftDays = 0 } = {}) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1, isMobile: viewport.width < 500, hasTouch: viewport.width < 500 });
  const page = await ctx.newPage();
  const w = watch(page);
  if (blockTiles) await page.route(TILE, r => r.abort());
  else await page.route(TILE, r => r.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64') }));
  if (shiftDays) await page.addInitScript(d => { const r = Date.now; Date.now = () => r() + d * 864e5; }, shiftDays);
  await page.goto(`${S.base}/drift.html${hash}`);
  await page.waitForFunction(() => window.__drift && window.__drift.ready() && window.__drift.state.live, null, { timeout: 20000 });
  return { ctx, page, w };
}
const shot = (page, name) => SHOTS ? page.screenshot({ path: path.join(SHOTS, name + '.png') }) : null;
const clean = w => { assert.deepEqual(w.pageErrors, [], 'page errors'); assert.deepEqual(w.errors.filter(e => !/tile\.openstreetmap|ERR_FAILED|net::/.test(e)), [], 'console errors'); assert.deepEqual(w.nonGet, [], 'non-GET requests'); };

test('every referenced resource exists in the BUILT bundle and nothing 404s (homepage + command centre)', async () => {
  for (const v of [DESKTOP, MOBILE]) {
    const { ctx, page, w } = await open(v);
    await page.goto(`${S.base}/index.html`); await page.waitForTimeout(2500);
    await page.goto(`${S.base}/drift.html`); await page.waitForTimeout(1500);
    await ctx.close();
    const bad = S.log.filter(l => l.status === 404 && !/subscribers/.test(l.url));
    assert.deepEqual(bad, [], JSON.stringify(bad));
  }
});

test('homepage entry banner opens the command centre; command centre links back', async () => {
  const ctx = await browser.newContext({ viewport: DESKTOP }); const page = await ctx.newPage();
  await page.route(TILE, r => r.abort());
  await page.goto(`${S.base}/index.html`);
  await page.waitForSelector('#drift-entry');
  await shot(page, '01_homepage_entry');
  await page.click('#drift-entry');
  await page.waitForURL(/drift\.html/);
  await page.waitForSelector('#home-link');
  await page.click('#home-link'); await page.waitForURL(/index\.html/);
  await ctx.close();
});

test('992 cells have valid, unique, in-bounds geographic coordinates; map does not claim coverage outside the grid', async () => {
  const { ctx, page } = await open(DESKTOP);
  const r = await page.evaluate(() => { const s = window.__drift.state; const k = new Set(); let bad = 0;
    s.cells.forEach(c => { if (!(c[0] >= 6 && c[0] <= 37 && c[1] >= 68 && c[1] <= 98) || !Number.isInteger(c[0]) || !Number.isInteger(c[1])) bad++; k.add(c[0] + ',' + c[1]); });
    return { n: s.cells.length, unique: k.size, bad, bounds: s.live.grid.bounds }; });
  assert.equal(r.n, 992); assert.equal(r.unique, 992); assert.equal(r.bad, 0);
  assert.deepEqual(r.bounds, { S: 6, N: 37, W: 68, E: 98 });
  const legend = await page.textContent('#legend');
  assert.match(legend, /1°×1° cells/); assert.match(legend, /6–37°N, 68–98°E/);   // textContent includes the collapsed details
  await ctx.close();
});

test('India land mask classifies known places correctly and hides outside cells by default', async () => {
  const { ctx, page } = await open(DESKTOP);
  const r = await page.evaluate(() => { const d = window.__drift; const P = d.inIndiaPoint;
    return { delhi: P(28.6, 77.2), bengaluru: P(12.97, 77.59), mumbai: P(19.07, 72.88), kolkata: P(22.57, 88.36), guwahati: P(26.14, 91.74), srinagar: P(34.08, 74.8),
             arabianSea: P(15, 65), bayOfBengal: P(14, 88), colombo: P(6.93, 79.86), kathmandu: P(27.7, 85.3), beijing: P(39.9, 116.4),
             inside: d.state.cells.filter(c => d.cellInIndia(c[0], c[1])).length }; });
  for (const k of ['delhi', 'bengaluru', 'mumbai', 'kolkata', 'guwahati', 'srinagar']) assert.equal(r[k], true, k + ' should be inside India');
  for (const k of ['arabianSea', 'bayOfBengal', 'colombo', 'kathmandu', 'beijing']) assert.equal(r[k], false, k + ' should be outside India');
  assert.ok(r.inside > 250 && r.inside < 520, 'cells touching India: ' + r.inside);
  await page.waitForTimeout(1200);
  const leak = await page.evaluate(() => { const d = window.__drift; const f = d.getMap().queryRenderedFeatures({ layers: ['fill'] }).filter(x => x.properties.op > 0);
    return { drawn: f.length, outside: f.filter(x => { const c = d.state.cells[x.properties.i]; return !d.cellInIndia(c[0], c[1]); }).length }; });
  assert.ok(leak.drawn > 20, 'cells drawn: ' + leak.drawn); assert.equal(leak.outside, 0, 'coloured cells outside the India outline');
  await page.uncheck('#india'); await page.waitForTimeout(800);
  const shown = await page.evaluate(() => { const d = window.__drift; return d.getMap().queryRenderedFeatures({ layers: ['fill'] }).filter(x => x.properties.op > 0).some(x => { const c = d.state.cells[x.properties.i]; return !d.cellInIndia(c[0], c[1]); }); });
  assert.equal(shown, true, 'turning the India-only mask off reveals outside cells');
  await ctx.close();
});

test('map actually renders (non-blank), cell click selects the right cell, zoom and pan keep working', async () => {
  const { ctx, page, w } = await open(DESKTOP);
  await page.waitForTimeout(1500);
  const drawn = await page.evaluate(() => window.__drift.getMap().queryRenderedFeatures({ layers: ['fill'] }).filter(f => f.properties.op > 0).length);
  assert.ok(drawn > 20, 'rendered coloured cells: ' + drawn);
  async function clickCell(lat, lon) {
    const xy = await page.evaluate(([la, lo]) => { const p = window.__drift.getMap().project([lo, la]); const b = document.getElementById('map').getBoundingClientRect(); return [b.left + p.x, b.top + p.y]; }, [lat, lon]);
    await page.mouse.click(xy[0], xy[1]);
  }
  await clickCell(28, 77);
  assert.match(await page.textContent('#p-detail'), /Cell 28°N 77°E/);
  await page.evaluate(() => window.__drift.getMap().zoomTo(6.5, { duration: 0 })); await page.waitForTimeout(400);
  await page.evaluate(() => window.__drift.getMap().jumpTo({ center: [78, 13] })); await page.waitForTimeout(400);
  await clickCell(13, 78);
  assert.match(await page.textContent('#p-detail'), /Cell 13°N 78°E/);
  await page.evaluate(() => window.__drift.getMap().panBy([120, -60], { duration: 0 })); await page.waitForTimeout(300);
  const sel = await page.evaluate(() => window.__drift.state.sel); assert.ok(sel != null);
  await shot(page, '02_live_thunderstorm_zoomed_cell');
  clean(w); await ctx.close();
});

test('graceful fallback when external basemap tiles fail: grid, outline and land fill still render, user is told', async () => {
  const { ctx, page, w } = await open(DESKTOP, '', { blockTiles: true });
  await page.waitForTimeout(2500);
  assert.equal(await page.evaluate(() => window.__drift.ready()), true);
  const drawn = await page.evaluate(() => window.__drift.getMap().queryRenderedFeatures({ layers: ['fill', 'india-fill'] }).length);
  assert.ok(drawn > 20);
  assert.match(await page.textContent('#maplabel'), /Basemap tiles unavailable/);
  await shot(page, '03_tiles_failed_fallback');
  await ctx.close();
});

test('all three hazard views: every layer renders a legend, nothing unsupported is shown as a prediction', async () => {
  const { ctx, page, w } = await open(DESKTOP);
  for (const h of ['thunderstorm', 'extreme_rainfall', 'flash_flood']) {
    await page.click(`[data-h=${h}]`);
    const opts = await page.$$eval('#lyr option', o => o.map(x => x.value));
    for (const o of opts) {
      await page.selectOption('#lyr', o);
      const legend = await page.textContent('#legend'); assert.ok(legend.length > 20, `${h}/${o} legend`);
      assert.equal(await page.$eval('#overlay', e => getComputedStyle(e).display), 'none', `${h}/${o} overlay`);
    }
    await page.evaluate(() => window.__drift.select(400));
    const detail = await page.textContent('#p-detail');
    assert.match(detail, /Thunderstorm prediction\s*unavailable/); assert.match(detail, /Cloudburst prediction\s*unavailable/); assert.match(detail, /Flash-flood prediction\s*unavailable/);
    await shot(page, '04_live_' + h);
  }
  assert.match(await page.textContent('#hzinfo'), /Live operation still needs/);
  await page.click('[data-h=extreme_rainfall]'); assert.match(await page.textContent('#hzinfo'), /Cloudburst[\s\S]*UNAVAILABLE/);
  clean(w); await ctx.close();
});

test('LIVE withholds values when sources are stale; alerts remain disabled', async () => {
  const { ctx, page } = await open(DESKTOP, '', { shiftDays: 3 });
  assert.match(await page.textContent('#overlay'), /STALE/);
  assert.match(await page.textContent('#fresh'), /STALE/);
  await page.click('[data-p=alerts]'); assert.match(await page.textContent('#p-alerts'), /DISABLED/);
  await ctx.close();
});

for (const [name, vp] of [['desktop', DESKTOP], ['mobile', MOBILE]]) {
  test(`simulation end-to-end (${name}): WATCH -> ALERT -> WARNING, affected cells, actions, simulated lifecycle, nothing sent, LIVE/SIM separated`, async () => {
    const { ctx, page, w } = await open(vp);
    const liveLegend = await page.textContent('#legend');
    await page.click('#m-sim');
    assert.match(await page.textContent('#banner'), /SIMULATION MODE/);
    assert.equal(await page.$eval('#simmark', e => getComputedStyle(e).display), 'block');
    await page.click('[data-ps=extreme]');
    await page.selectOption('#spd', '500');
    await shot(page, `05_simulation_start_${name}`);
    await page.click('#play');
    await page.waitForFunction(() => window.__drift.state.t === 6, null, { timeout: 15000 });
    await page.waitForTimeout(2500);
    await page.click('[data-p=alerts]');
    const feed = await page.textContent('#p-alerts');
    for (const k of ['WATCH', 'ALERT', 'WARNING']) assert.match(feed, new RegExp(`SIMULATED ${k} issued`));
    assert.match(feed, /cells affected/); assert.match(feed, /DELIVERY_CONFIRMED/); assert.match(feed, /not sent/); assert.match(feed, /Warning: |Alert: |Watch: /);
    assert.doesNotMatch(feed, /Live DRIFT|official warning issued/i);
    await shot(page, `06_simulation_warning_${name}`);
    // separation: nothing from LIVE leaks into SIM and vice versa
    assert.match(await page.textContent('#legend'), /SIMULATED/); assert.doesNotMatch(await page.textContent('#legend'), /GFS/);
    await page.click('#m-live'); await page.waitForTimeout(300);
    const back = await page.textContent('#legend'); assert.doesNotMatch(back, /SIMULATED/); assert.equal(back.includes('GFS'), liveLegend.includes('GFS'));
    assert.match(await page.textContent('#banner'), /LIVE MODE/);
    assert.equal(await page.$eval('#simmark', e => getComputedStyle(e).display), 'none');
    assert.deepEqual(w.nonGet, [], 'no POST/PUT etc. was made'); assert.deepEqual([...new Set(w.external)].filter(h => !/openstreetmap/.test(h)), [], 'no external host contacted except basemap');
    clean(w); await ctx.close();
  });
}

test('guided tour drives every step without errors on desktop and mobile', async () => {
  for (const [name, vp] of [['desktop', DESKTOP], ['mobile', MOBILE]]) {
    const { ctx, page, w } = await open(vp);
    await page.click('#tour-start');
    for (let i = 0; i < 8; i++) {
      await page.waitForSelector('#tour h3'); await page.waitForTimeout(500);
      if (i === 1 || i === 3 || i === 5 || i === 7) await shot(page, `07_tour_step${i + 1}_${name}`);
      if (i < 7) await page.click('#tour-n');
    }
    await page.click('#tour-n'); assert.equal(await page.$eval('#tour', e => getComputedStyle(e).display), 'none');
    clean(w); await ctx.close();
  }
});

test('mobile layout: map first, no horizontal overflow, controls reachable', async () => {
  const { ctx, page } = await open(MOBILE);
  const m = await page.evaluate(() => ({ over: document.documentElement.scrollWidth > innerWidth, mapTop: document.getElementById('mapwrap').getBoundingClientRect().top, ctrlTop: document.getElementById('controls').getBoundingClientRect().top }));
  assert.equal(m.over, false); assert.ok(m.mapTop < m.ctrlTop);
  await shot(page, '08_mobile_live');
  await ctx.close();
});
