// node --test tests/e2e/drift.spec.mjs   (system Chrome; the BUILT bundle is served, not the dev tree)
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { buildDist, serve, launch, watch, ROOT } from './harness.mjs';

let browser, S;
const SHOTS = process.env.DRIFT_SHOTS ? path.join(ROOT, 'docs/demo/screenshots') : null;
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
const DESKTOP = { width: 1440, height: 900 }, MOBILE = { width: 390, height: 844 };
const TILE = /tile\.openstreetmap\.org/;
const PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');

before(async () => { S = await serve(buildDist()); browser = await launch(); });
after(async () => { await browser?.close(); S?.srv.close(); });

async function open(viewport, { hash = '', blockTiles = false, shiftDays = 0, mutate = null, abort = [] } = {}) {
  const ctx = await browser.newContext({ viewport, isMobile: viewport.width < 500, hasTouch: viewport.width < 500 });
  const page = await ctx.newPage(); const w = watch(page);
  await page.route(TILE, r => blockTiles ? r.abort() : r.fulfill({ status: 200, contentType: 'image/png', body: PIXEL }));
  for (const pat of abort) await page.route(pat, r => r.fulfill({ status: 404, body: 'gone' }));
  if (mutate) await page.route(mutate.pattern, async r => { const j = await (await r.fetch()).json(); mutate.fn(j); r.fulfill({ contentType: 'application/json', body: JSON.stringify(j) }); });
  if (shiftDays) await page.addInitScript(d => { const r = Date.now; Date.now = () => r() + d * 864e5; }, shiftDays);
  await page.goto(`${S.base}/drift.html${hash}`);
  await page.waitForFunction(() => window.__drift && window.__drift.ready() && !window.__drift.state.loading, null, { timeout: 30000 });
  return { ctx, page, w };
}
const shot = (page, name) => SHOTS ? page.screenshot({ path: path.join(SHOTS, name + '.png') }) : null;
const noise = e => /tile\.openstreetmap|ERR_FAILED|net::|404 \(Not Found\)|status of 404|\[BABEL\] Note/.test(e);
const clean = (w, allow404 = false) => { assert.deepEqual(w.pageErrors, [], 'page errors'); assert.deepEqual(w.errors.filter(e => !noise(e)), [], 'console errors'); assert.deepEqual(w.nonGet, [], 'non-GET requests'); };
async function pick(page, name) {
  await page.fill('#q', name); await page.waitForSelector('#sug li[data-i]'); await page.keyboard.press('Enter');
  await page.waitForFunction(n => window.__drift.state.sel && window.__drift.labelOf(window.__drift.state.sel) === n, name);
  return page.evaluate(() => window.__drift.state.sel);
}
const detail = page => page.textContent('#p-detail');
const row = async (page, label) => (await page.$$eval('#p-detail tr', (tr, l) => tr.map(t => t.innerText.replace(/\s+/g, ' ').trim()).find(t => t.startsWith(l)) || null, label));
const num = (s, label) => { const t = (s || '').slice(label ? label.length : 0); const m = /(-?\d+(\.\d+)?)/.exec(t); return m ? +m[1] : null; };

test('built bundle: every resource loads, nothing 404s, national artifacts verified', async () => {
  for (const v of [DESKTOP, MOBILE]) {
    const { ctx, page, w } = await open(v);
    const r = await page.evaluate(() => ({ n: window.__drift.state.store.cells.length, ok: window.__drift.state.store.fieldsOk, problems: window.__drift.state.store.problems, ts: window.__drift.state.store.tsOk }));
    assert.equal(r.ok, true, JSON.stringify(r.problems)); assert.ok(r.n > 4000);
    await page.goto(`${S.base}/index.html`); await page.waitForTimeout(1500);
    await ctx.close(); clean(w);
  }
  assert.deepEqual(S.log.filter(l => l.status === 404 && !/subscribers/.test(l.url)), [], JSON.stringify(S.log.filter(l => l.status === 404)));
});

test('homepage entry opens the command centre and links back', async () => {
  const ctx = await browser.newContext({ viewport: DESKTOP }); const page = await ctx.newPage(); await page.route(TILE, r => r.abort());
  await page.goto(`${S.base}/index.html`); await page.waitForSelector('#drift-entry'); await shot(page, '01_homepage_entry');
  await page.click('#drift-entry'); await page.waitForURL(/drift\.html/); await page.click('#home-link'); await page.waitForURL(/index\.html/); await ctx.close();
});

test('national grid: land-only, unique stable ids, valid coordinates on the GFS lattice, all regions represented, honest resolution wording', async () => {
  const { ctx, page } = await open(DESKTOP);
  const r = await page.evaluate(() => { const s = window.__drift.state.store, ids = new Set(), reg = {}; let bad = 0, off = 0, badId = 0;
    s.cells.forEach(c => { ids.add(c[0]); if (!(c[1] >= 6.5 && c[1] <= 37.5 && c[2] >= 68 && c[2] <= 97.5)) bad++; if (Math.abs(c[1] * 4 - Math.round(c[1] * 4)) > 1e-9 || Math.abs(c[2] * 4 - Math.round(c[2] * 4)) > 1e-9) off++;
      if (c[0] !== 'IN025_' + String(Math.round(c[1] * 100)).padStart(4, '0') + '_' + String(Math.round(c[2] * 100)).padStart(5, '0')) badId++; if (c[3] < 0.5) bad++; const k = c[4] == null ? 'none' : s.states[c[4]]; reg[k] = (reg[k] || 0) + 1; });
    return { n: s.cells.length, unique: ids.size, bad, off, badId, states: Object.keys(reg).length, none: reg.none || 0, sea: [s.resolve(15, 65).covered, s.resolve(10, 85).covered, s.resolve(27.7, 85.3).covered, s.resolve(6.9, 79.9).covered, s.resolve(1, 103).covered],
             land: [s.resolve(12.97, 77.59).covered, s.resolve(28.6, 77.2).covered, s.resolve(34.08, 74.8).covered, s.resolve(11.65, 92.74).covered] }; });
  assert.equal(r.n, r.unique); assert.equal(r.bad, 0); assert.equal(r.off, 0); assert.equal(r.badId, 0); assert.equal(r.none, 0); assert.ok(r.states >= 33, 'states ' + r.states);
  assert.deepEqual(r.sea, [false, false, false, false, false]); assert.deepEqual(r.land, [true, true, true, true]);
  assert.match(await page.textContent('#p-detail'), /same resolution as the GFS source/);
  await ctx.close();
});

const REGIONS = { north: ['Delhi', 'Srinagar'], south: ['Bengaluru', 'Chennai'], east: ['Kolkata', 'Bhubaneswar'], west: ['Mumbai', 'Ahmedabad'], central: ['Bhopal', 'Nagpur'], northeast: ['Guwahati', 'Imphal'] };
test('twelve locations across all six regions: each shows its own underlying data; nothing from the previous selection survives', async () => {
  const { ctx, page, w } = await open(DESKTOP);
  const seen = {}; let prev = null;
  for (const [reg, names] of Object.entries(REGIONS)) for (const name of names) {
    const id = await pick(page, name);
    const txt = await detail(page);
    assert.ok(txt.includes(name) && txt.includes(id), name + ' panel names itself and its cell');
    if (prev) { assert.ok(!txt.includes(prev.id), `${name} panel still shows previous cell ${prev.id}`); }
    const cape = num(await row(page, 'CAPE'), 'CAPE'), pwat = num(await row(page, 'Precipitable water'), 'Precipitable water'), t2 = num(await row(page, '2 m temperature'), '2 m temperature'), elev = num(await row(page, 'Elevation'), 'Elevation (mean / min–max)');
    seen[name] = { id, cape, pwat, t2, elev };
    // every other panel is also about THIS location
    await page.click('[data-p=hazards]'); const hz = await page.textContent('#p-hazards'); assert.ok(hz.includes(name) && (!prev || !hz.includes(prev.name + ' ·')), 'hazards panel of ' + name);
    await page.click('[data-p=detail]'); prev = { id, name };
  }
  const uniq = k => new Set(Object.values(seen).map(v => v[k]).filter(x => x != null)).size;
  assert.ok(uniq('pwat') >= 9 && uniq('t2') >= 9 && uniq('elev') >= 10 && uniq('cape') >= 6, JSON.stringify(seen));
  assert.ok(seen.Srinagar.elev > 1000 && seen.Chennai.elev < 50 && seen.Delhi.pwat !== seen.Mumbai.pwat);
  assert.equal(new Set(Object.values(seen).map(v => v.id)).size, 12);
  await shot(page, '02_location_selected'); clean(w); await ctx.close();
});

test('map click selects the enclosing cell (including a rural cell), sea click selects nothing, zoom and pan keep working', async () => {
  const { ctx, page, w } = await open(DESKTOP); await page.waitForTimeout(800);
  async function click(lat, lon) { const xy = await page.evaluate(([la, lo]) => { const m = window.__drift.getMap(); m.jumpTo({ center: [lo, la], zoom: 6 }); const p = m.project([lo, la]); const b = document.getElementById('map').getBoundingClientRect(); return [b.left + p.x, b.top + p.y]; }, [lat, lon]); await page.waitForTimeout(300); await page.mouse.click(xy[0], xy[1]); await page.waitForTimeout(200); }
  await click(26.1, 74.1);                                   // rural Rajasthan
  assert.equal(await page.evaluate(() => window.__drift.state.sel), 'IN025_2600_07400'); assert.match(await detail(page), /Rajasthan/);
  await page.evaluate(() => window.__drift.getMap().panBy([80, -40], { duration: 0 })); await page.waitForTimeout(200);
  await click(15, 66);                                       // Arabian Sea
  assert.match(await detail(page), /Not covered by DRIFT/); assert.equal(await page.evaluate(() => window.__drift.state.sel), null);
  assert.doesNotMatch(await detail(page), /CAPE/);
  await click(27.7, 85.3);                                   // Kathmandu: another country
  assert.match(await detail(page), /Not covered/); assert.doesNotMatch(await detail(page), /CAPE/);
  await click(19.0, 72.5);                                   // sea just off Mumbai: nearest covered cell offered, never auto-selected
  assert.match(await detail(page), /Not covered/); assert.equal(await page.evaluate(() => window.__drift.state.sel), null);
  await page.click('#nc-go'); assert.ok(await page.evaluate(() => window.__drift.state.sel));
  const drawn = await page.evaluate(() => window.__drift.getMap().queryRenderedFeatures({ layers: ['fill'] }).filter(f => f.properties.op > 0).length); assert.ok(drawn > 10);
  clean(w); await ctx.close();
});

test('forecast hour changes the underlying values, status chips are recomputed, and rain/CAPE series differ by hour', async () => {
  const { ctx, page } = await open(DESKTOP); await pick(page, 'Mumbai');
  const vals = [];
  for (const k of [0, 2, 4, 7]) { await page.click(`[data-hr="${k}"]`); await page.waitForTimeout(150); vals.push(num(await row(page, 'CAPE'), 'CAPE')); assert.match(await detail(page), new RegExp('f' + [3, 9, 15, 24][[0, 2, 4, 7].indexOf(k)] + ' · ')); }
  assert.ok(new Set(vals).size >= 3, 'CAPE by hour: ' + vals);
  const cls = await page.$$eval('.hr', b => b.map(x => x.className)); assert.ok(cls.every(c => /CURRENT|PAST_VALID_TIME|STALE|UNAVAILABLE|INVALID/.test(c)));
  await ctx.close();
});

test('all three hazard views: every layer renders; every hazard panel is populated; unsupported capabilities are explicitly unavailable', async () => {
  const { ctx, page, w } = await open(DESKTOP); await pick(page, 'Guwahati');
  for (const h of ['thunderstorm', 'extreme_rainfall', 'flash_flood']) {
    await page.click(`[data-h=${h}]`);
    for (const o of await page.$$eval('#lyr option', o => o.map(x => x.value))) {
      await page.selectOption('#lyr', o);
      const info = await page.evaluate(() => ({ legend: document.getElementById('legend').innerText, ov: getComputedStyle(document.getElementById('overlay')).display, ovt: document.getElementById('overlay').innerText }));
      if (o === 'ts_model') assert.ok(info.legend.length > 10 || /pan-India thunderstorm model/.test(info.ovt)); else { assert.ok(info.legend.length > 20, `${h}/${o} legend`); assert.equal(info.ov, 'none', `${h}/${o}: ${info.ovt}`); }
      if (o !== 'ts_model') { const vals = await page.evaluate(id => window.__drift.layerValues(id), o); const nz = vals.filter(v => v != null); if (o === 'gauge') assert.ok(nz.length > 800 && nz.length < vals.length, 'gauge context exists only near gauges; the rest stays null (' + nz.length + ')'); else assert.ok(nz.length > 4000 && new Set(nz).size > (o === 'cb' ? 5 : 20), `${h}/${o} has real spatial variation (${new Set(nz).size} distinct)`); }
    }
    await shot(page, '03_layer_' + h);
  }
  await page.click('[data-p=hazards]'); const t = await page.textContent('#p-hazards');
  assert.match(t, /not a probability/i); assert.match(t, /Cloudburst prediction\s*UNAVAILABLE/); assert.match(t, /Short-duration heavy rainfall \(3 h\)/); assert.match(t, /does not predict cloudbursts/);
  assert.match(t, /Flash-flood prediction UNAVAILABLE/); assert.match(t, /heuristic, unvalidated/); assert.match(t, /Missing for live flash-flood prediction/);
  assert.match(t, /Pan-India model|UNAVAILABLE/); await shot(page, '04_hazard_panels');
  clean(w); await ctx.close();
});

test('search finds places, districts and states; region summary highlights the region; compare shows different regions side by side', async () => {
  const { ctx, page } = await open(DESKTOP);
  await page.fill('#q', 'ker'); await page.waitForSelector('#sug li[data-i]'); assert.match(await page.textContent('#sug'), /state/);
  await page.fill('#q', 'Kerala'); await page.keyboard.press('Enter'); await page.waitForFunction(() => window.__drift.state.region && window.__drift.state.region.name === 'Kerala');
  const reg = await page.evaluate(() => window.__drift.state.region.ids.length); assert.ok(reg > 40 && reg < 70);
  assert.match(await detail(page), /Kerala[\s\S]*cells/); assert.match(await detail(page), /CAPE/);
  await page.fill('#q', 'thane'); await page.waitForSelector('#sug li[data-i]'); assert.match(await page.textContent('#sug'), /district/);
  for (const n of ['Delhi', 'Chennai', 'Guwahati']) { await pick(page, n); await page.click('#cmp-add'); }
  await page.click('[data-p=compare]'); const cols = await page.$$eval('table.cmp tr:first-child td', t => t.length); assert.equal(cols, 3);
  const caps = await page.$$eval('table.cmp tr', trs => trs.find(t => t.innerText.startsWith('PWAT')).innerText.match(/[\d.]+/g)); assert.equal(new Set(caps.slice(0, 3)).size, 3);
  await shot(page, '05_compare'); await ctx.close();
});

test('stale data is withheld, never shown as current; alerts stay disabled', async () => {
  const { ctx, page } = await open(DESKTOP, { shiftDays: 3 });
  assert.match(await page.textContent('#overlay'), /STALE/); assert.match(await page.textContent('#fresh'), /STALE/);
  await pick(page, 'Delhi'); const t = await detail(page); assert.match(t, /STALE/); assert.doesNotMatch(t, /CURRENT/);
  await page.click('[data-p=alerts]'); assert.match(await page.textContent('#p-alerts'), /DISABLED/); await ctx.close();
});

test('missing or forged sources degrade independently and safely', async () => {
  // fields artifact missing
  let r = await open(DESKTOP, { abort: [/india_fields_latest\.json/] });
  assert.match(await r.page.textContent('#overlay'), /UNAVAILABLE/); await r.page.fill('#q', 'Delhi'); await r.page.waitForSelector('#sug li[data-i]'); await r.page.keyboard.press('Enter');
  assert.match(await detail(r.page), /unavailable/i); assert.doesNotMatch(await detail(r.page), /CAPE\s*\d/); assert.deepEqual(r.w.pageErrors, []); await shot(r.page, '06_fields_missing'); await r.ctx.close();
  // forged: a valid-time label that disagrees with init + fhour
  r = await open(DESKTOP, { mutate: { pattern: /india_fields_latest\.json/, fn: j => { j.meta.files[2].valid_time_utc = '2020-01-01T00:00:00Z'; } } });
  assert.equal(await r.page.evaluate(() => window.__drift.state.store.fieldsOk), false); assert.match(await r.page.textContent('#overlay'), /valid time/); assert.deepEqual(r.w.pageErrors, []); await r.ctx.close();
  // forged: a plausible but impossible value
  r = await open(DESKTOP, { mutate: { pattern: /india_fields_latest\.json/, fn: j => { j.fields.cape[0][10] = 99999; } } });
  assert.equal(await r.page.evaluate(() => window.__drift.state.store.fieldsOk), false); await r.ctx.close();
  // static terrain layer missing: atmospheric data remain, terrain says unavailable
  r = await open(DESKTOP, { abort: [/india_static_025\.json/] }); await pick(r.page, 'Pune');
  assert.match(await detail(r.page), /CAPE/); assert.match(await detail(r.page), /Terrain\/gauge context unavailable/); assert.deepEqual(r.w.pageErrors, []); await r.ctx.close();
  // thunderstorm model artifact missing
  r = await open(DESKTOP, { abort: [/india_ts_model_latest\.json/] }); await pick(r.page, 'Pune'); await r.page.click('[data-p=hazards]');
  assert.match(await r.page.textContent('#p-hazards'), /Pan-India model[\s\S]*UNAVAILABLE/); assert.deepEqual(r.w.pageErrors, []); await r.ctx.close();
});

test('external basemap tiles failing does not break the grid, outline or land fill, and the user is told', async () => {
  const { ctx, page } = await open(DESKTOP, { blockTiles: true }); await page.waitForTimeout(2500);
  assert.match(await page.textContent('#maplabel'), /Basemap tiles unavailable/);
  assert.ok(await page.evaluate(() => window.__drift.getMap().queryRenderedFeatures({ layers: ['fill', 'india-fill'] }).length) > 10);
  await shot(page, '07_tiles_failed'); await ctx.close();
});

for (const [name, vp] of [['desktop', DESKTOP], ['mobile', MOBILE]]) {
  test(`simulation end-to-end (${name}): WATCH -> ALERT -> WARNING, nothing sent, LIVE and SIMULATION never mix`, async () => {
    const { ctx, page, w } = await open(vp); await pick(page, 'Mumbai');
    const liveSel = await page.evaluate(() => window.__drift.state.sel);
    await page.click('#m-sim'); assert.match(await page.textContent('#banner'), /SIMULATION MODE/);
    assert.equal(await page.$eval('#simmark', e => getComputedStyle(e).display), 'block');
    assert.doesNotMatch(await page.textContent('#p-detail'), /Mumbai|CAPE \d/);           // no live location in simulation
    await page.click('[data-ps=extreme]'); await page.selectOption('#spd', '500'); await shot(page, `08_simulation_start_${name}`);
    await page.click('#play'); await page.waitForFunction(() => window.__drift.state.t === 6, null, { timeout: 15000 }); await page.waitForTimeout(2500);
    await page.click('[data-p=alerts]'); const feed = await page.textContent('#p-alerts');
    for (const k of ['WATCH', 'ALERT', 'WARNING']) assert.match(feed, new RegExp(`SIMULATED ${k} issued`));
    assert.match(feed, /cells affected/); assert.match(feed, /DELIVERY_CONFIRMED/); assert.match(feed, /not sent/);
    await shot(page, `09_simulation_warning_${name}`);
    assert.match(await page.textContent('#legend'), /SIMULATED/);
    await page.click('#m-live'); await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.__drift.state.sel), liveSel); assert.doesNotMatch(await page.textContent('#legend'), /SIMULATED/);
    assert.match(await page.textContent('#banner'), /LIVE MODE/);
    assert.deepEqual(w.nonGet, []); assert.deepEqual([...new Set(w.external)].filter(h => !/openstreetmap/.test(h)), []);
    clean(w); await ctx.close();
  });
}

test('guided tour drives every step on desktop and mobile without errors', async () => {
  for (const [name, vp] of [['desktop', DESKTOP], ['mobile', MOBILE]]) {
    const { ctx, page, w } = await open(vp); await page.click('#tour-start');
    for (let i = 0; i < 8; i++) { await page.waitForSelector('#tour h3'); await page.waitForTimeout(500); if ([1, 3, 5, 7].includes(i)) await shot(page, `10_tour_step${i + 1}_${name}`); if (i < 7) await page.click('#tour-n'); }
    await page.click('#tour-n'); assert.equal(await page.$eval('#tour', e => getComputedStyle(e).display), 'none'); clean(w); await ctx.close();
  }
});

test('mobile: search reachable, map first, no horizontal overflow, panel updates on selection', async () => {
  const { ctx, page } = await open(MOBILE);
  const m = await page.evaluate(() => ({ over: document.documentElement.scrollWidth > innerWidth, q: document.getElementById('q').getBoundingClientRect().width, mapTop: document.getElementById('mapwrap').getBoundingClientRect().top, ctrlTop: document.getElementById('controls').getBoundingClientRect().top }));
  assert.equal(m.over, false); assert.ok(m.q > 150); assert.ok(m.mapTop < m.ctrlTop);
  await pick(page, 'Chennai'); assert.match(await detail(page), /Tamil Nadu/); await shot(page, '11_mobile_location'); await ctx.close();
});
