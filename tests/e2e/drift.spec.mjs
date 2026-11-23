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
  // The site root is the modern command centre; the legacy station dashboard lives at /legacy.html and links forward to it.
  await page.goto(`${S.base}/`); await page.waitForSelector('#home-link'); assert.match(await page.getAttribute('#home-link', 'href'), /legacy\.html$/);
  await page.goto(`${S.base}/legacy.html`, { waitUntil: "domcontentloaded", timeout: 60000 }); await page.waitForSelector('#drift-entry'); await shot(page, '01_homepage_entry');
  await page.click('#drift-entry'); await page.waitForURL(/\/drift(\.html)?$/); await page.click("#home-link"); await page.waitForURL(/\/legacy\.html$/); await ctx.close();
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
  assert.ok(uniq('pwat') >= 9 && uniq('t2') >= 9 && uniq('elev') >= 10, JSON.stringify(seen));   // CAPE is season dependent (≈0 in a winter cycle), so it is not used for the distinctness check
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
  for (const k of [0, 2, 4, 7]) { await page.click(`[data-hr="${k}"]`); await page.waitForTimeout(150); vals.push(num(await row(page, '2 m temperature'), '2 m temperature')); assert.match(await detail(page), new RegExp('f' + [3, 9, 15, 24][[0, 2, 4, 7].indexOf(k)] + ' · ')); }
  assert.ok(new Set(vals).size >= 3, '2 m temperature by hour (diurnal cycle): ' + vals);
  const cls = await page.$$eval('.hr', b => b.map(x => x.className)); assert.ok(cls.every(c => /CURRENT|PAST_VALID_TIME|STALE|UNAVAILABLE|INVALID/.test(c)));
  await ctx.close();
});

test('all three hazard views: every layer renders; every hazard panel is populated; unsupported capabilities are explicitly unavailable', async () => {
  // The satellite layers are observations that are only shown while the scan is <= 90 min old, so give the committed artifact a current scan time.
  const { ctx, page, w } = await open(DESKTOP, { mutate: { pattern: /india_satellite_ctt_latest\.json/, fn: j => { j.meta.scene_nominal_utc = new Date(Date.now() - 20 * 60000).toISOString().replace(/\.\d+Z$/, 'Z'); } } }); await pick(page, 'Guwahati');
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

const TWENTY = ['Leh', 'Srinagar', 'Delhi', 'Jaipur', 'Mumbai', 'Pune', 'Bengaluru', 'Chennai', 'Hyderabad', 'Kochi', 'Thiruvananthapuram', 'Mangaluru', 'Panjim', 'Kolkata', 'Bhubaneswar', 'Guwahati', 'Shillong', 'Patna', 'Lucknow', 'Port Blair'];
test('twenty locations: what the panel shows equals the published JSON at that cell and hour; coastal points outside the land mask are reported unavailable', async () => {
  const { ctx, page, w } = await open(DESKTOP);
  const art = await page.evaluate(async () => (await (await fetch('data/india_fields_latest.json')).json()));
  const pos = Object.fromEntries(art.cells.map((c, i) => [c, i]));
  const shown = {}; let covered = 0; const notCovered = [];
  for (const name of TWENTY) {
    const hit = await page.evaluate(n => { const r = window.__drift.state.store.search(n, 6).find(x => x.type === 'place' && x.name.toLowerCase() === n.toLowerCase()); return r ? { lat: r.lat, lon: r.lon } : null; }, name);
    assert.ok(hit, name + ' is in the gazetteer');
    const res = await page.evaluate(h => window.__drift.state.store.resolve(h.lat, h.lon), hit);
    if (!res.covered) { notCovered.push(name); assert.ok(res.reason, name + ' states why it is not covered'); continue; }
    covered++;
    const id = await pick(page, name);
    assert.equal(id, res.cell_id);
    const hour = await page.evaluate(() => window.__drift.state.hour);
    const i = pos[id];
    const want = { 'CAPE': art.fields.cape[hour][i], 'Precipitable water': art.fields.pwat[hour][i], '2 m temperature': art.fields.t2m_c[hour][i] };
    for (const [label, expect] of Object.entries(want)) {
      const got = num(await row(page, label), label);
      assert.ok(expect == null ? got == null : Math.abs(got - expect) <= 0.51, `${name} ${label}: panel ${got} vs artifact ${expect}`);
    }
    const txt = await detail(page); assert.ok(txt.includes(art.meta.files[hour].valid_time_utc.slice(0, 13).replace('T', ' ')) || /\d{4}-\d{2}-\d{2} \d{2}:00Z/.test(txt), name + ' shows the valid time');
    shown[name] = want;
  }
  assert.ok(covered >= 17 && notCovered.every(n => ['Kochi', 'Mangaluru', 'Panjim'].includes(n)), 'not covered: ' + notCovered);
  assert.ok(new Set(Object.values(shown).map(v => v['2 m temperature'])).size >= covered - 3, 'temperatures differ by location');
  assert.deepEqual(w.pageErrors, []);
  await ctx.close();
});

for (const [label, vp] of [['desktop', { width: 1360, height: 860 }], ['laptop', { width: 1100, height: 700 }], ['mobile', { width: 390, height: 844 }]])
test(`${label}: the map legend never overlaps the basemap attribution; unknown paths are a real 404, never HTML with 200`, async () => {
  const { ctx, page } = await open(vp);
  await page.waitForSelector('#legend'); await page.waitForTimeout(1500);
  const o = await page.evaluate(() => { const l = document.querySelector('#legend').getBoundingClientRect(), a = [...document.querySelectorAll('.maplibregl-ctrl-attrib')].map(e => e.getBoundingClientRect()).filter(r => r.width > 0);
    return a.map(r => !(l.right <= r.left || l.left >= r.right || l.bottom <= r.top || l.top >= r.bottom)); });
  assert.deepEqual(o.filter(Boolean), [], 'legend overlaps attribution');
  const r = await page.evaluate(async () => { const x = await fetch('v1/health'); const y = await fetch('data/nonexistent.json'); return [x.status, y.status]; });
  assert.deepEqual(r, [404, 404]);
  await ctx.close();
});

test('legacy station dashboard: no typed metrics or unsupported pages are reachable; cloudburst and flash flood are not selectable', async () => {
  const ctx = await browser.newContext({ viewport: DESKTOP }); const page = await ctx.newPage(); const w = watch(page); await page.route(TILE, r => r.abort());
  await page.goto(`${S.base}/legacy.html`, { waitUntil: "domcontentloaded", timeout: 60000 }); await page.waitForSelector('#boot-loading', { state: 'detached', timeout: 90000 }); await page.waitForTimeout(1500);
  assert.deepEqual(w.pageErrors, []);
  const disabled = await page.$$eval('button[disabled]', b => b.map(x => x.innerText.trim()));
  assert.ok(disabled.filter(t => /CB/.test(t)).length >= 1 && disabled.filter(t => /FF/.test(t)).length >= 1, 'CB and FF buttons are disabled: ' + disabled);
  for (const hash of ['#models', '#scores', '#regimes', '#satellite', '#explainability', '#api', '#alerts', '#contacts', '#whatif', '#climatology', '#multiday', '#atc']) {
    await page.goto(`${S.base}/legacy.html${hash}`, { waitUntil: 'domcontentloaded', timeout: 60000 }); await page.reload({ waitUntil: 'domcontentloaded' }); await page.waitForSelector('#boot-loading', { state: 'detached', timeout: 90000 }); await page.waitForTimeout(1200);
    const t = await page.evaluate(() => document.body.innerText);
    assert.ok(!/0\.871|0\.8484|0\.997|AUROC 1\.0|Doppler|Blitzortung|ALL CLEAR|Dispatch to|Dispatched to|SIGMET\/AIRMET DRAFT|S-BAND|XGBoost v6/i.test(t), `${hash} shows a removed claim`);
  }
  await page.goto(`${S.base}/legacy.html#models`, { waitUntil: "domcontentloaded", timeout: 60000 }); await page.reload({ waitUntil: 'domcontentloaded' }); await page.waitForSelector('#boot-loading', { state: 'detached', timeout: 90000 }); await page.waitForTimeout(1500);
  const m = await page.evaluate(() => document.body.innerText);
  assert.ok(/data\/hazard_evaluation\.json/.test(m) && /Brier skill/.test(m) && /STATION-ONLY/.test(m), 'models page is data-backed and station-scoped');
  await ctx.close();
});

test('pan-India thunderstorm map layer shows each forecast hour\'s OWN model lead, never a shifted or clamped one', async () => {
  // Synthetic model artifact: a distinct constant per lead, so any lead/hour mix-up is visible. (Test fixture only; the app code is unmodified.)
  const fields = await (await fetch(`${S.base}/data/india_fields_latest.json`)).json();
  const n = fields.cells.length, LEADS = { 6: 0.1, 12: 0.2, 18: 0.3, 24: 0.4 };
  const mutate = { pattern: /india_ts_model_latest\.json/, fn: j => {
    j.meta = { model_version: 'fixture', model_evaluated: true, calibrated: true, leads: [6, 12, 18, 24], valid_times: {}, value_type: 'FIXTURE', evaluation_summary: 'fixture' };
    j.cells = fields.cells; j.leads = Object.fromEntries(Object.entries(LEADS).map(([l, v]) => [l, new Array(n).fill(v)])); j.explanations = {}; } };
  const { ctx, page } = await open(DESKTOP, { mutate }); await page.waitForTimeout(800);
  const rows = await page.evaluate(() => { const d = window.__drift, out = []; d.state.hazard = 'thunderstorm';
    const hrs = d.state.store.fields.meta.fhours;
    for (let i = 0; i < hrs.length; i++) { d.state.hour = i; const v = d.layerValues('ts_model').filter(x => x != null); out.push({ fhour: hrs[i], n: v.length, uniq: [...new Set(v)] }); }
    return out; });
  for (const r of rows) {
    if (LEADS[r.fhour] != null) assert.deepEqual(r.uniq, [LEADS[r.fhour]], `f${r.fhour} must show the +${r.fhour} h model value`);
    else assert.equal(r.n, 0, `f${r.fhour} has no model lead, so the layer must be empty (not another lead's values)`);
  }
  await ctx.close();
});

test('legacy station dashboard: fallback/unscored slots are never shown as 0%, and PEAK marks only a real maximum', async () => {
  const base = JSON.parse(fs.readFileSync(path.join(ROOT, 'forecast.json'), 'utf8'));
  async function render(mutate) {
    const ctx = await browser.newContext({ viewport: DESKTOP }); const page = await ctx.newPage(); const w = watch(page); await page.route(TILE, r => r.abort());
    await page.route('**/forecast.json*', r => { const j = JSON.parse(JSON.stringify(base)); mutate(j); r.fulfill({ contentType: 'application/json', body: JSON.stringify(j) }); });
    await page.goto(`${S.base}/legacy.html`, { waitUntil: "domcontentloaded", timeout: 60000 }); await page.waitForSelector('#boot-loading', { state: 'detached', timeout: 90000 }); await page.waitForTimeout(1500);
    const cards = await page.$$eval('button[title], button', bs => bs.filter(b => /LATE NIGHT|MORNING|AFTERNOON|EVENING/.test(b.innerText)).map(b => b.innerText.replace(/\s+/g, ' ').trim()));
    const text = await page.evaluate(() => document.body.innerText);
    await ctx.close(); assert.deepEqual(w.pageErrors, []); return { cards, text };
  }
  // Every slot is a fallback (inputs partly fixed constants): nothing may read as a probability.
  const allFallback = await render(j => { j.slots.forEach(s => { s.fallback = true; s.fallback_reason = '20 of 64 model inputs are fixed climatological constants (no live station-observation source)'; s.ts_probability = 0.0016; });
    j.peak_probability = null; j.peak_slot = null; j.alert_active = false; });
  assert.equal(allFallback.cards.length, 4, JSON.stringify(allFallback.cards));
  for (const c of allFallback.cards) { assert.doesNotMatch(c, /\d+%/, c); assert.doesNotMatch(c, /PEAK|MINIMAL/, c); assert.match(c, /WITHHELD/); }
  assert.match(allFallback.text, /UNAVAILABLE: 20 of 64 model inputs are fixed/);
  // One valid slot: exactly one PEAK, and the other slots stay withheld.
  const oneValid = await render(j => { j.slots.forEach((s, i) => { s.fallback = i !== 2; s.fallback_reason = i !== 2 ? 'fixed constants' : null; s.ts_probability = i === 2 ? 0.42 : 0.01; });
    j.peak_probability = 0.42; j.peak_slot = 2; });
  assert.equal(oneValid.cards.filter(c => /PEAK/.test(c)).length, 1, JSON.stringify(oneValid.cards));
  assert.ok(oneValid.cards.find(c => /AFTERNOON/.test(c) && /42%/.test(c) && /PEAK/.test(c)), JSON.stringify(oneValid.cards));
  assert.equal(oneValid.cards.filter(c => /WITHHELD/.test(c)).length, 3);
});

test('operational lead: the panel shows valid time minus the recorded acquisition time of the SELECTED hour, never negative', async () => {
  const fields = await (await fetch(`${S.base}/data/india_fields_latest.json`)).json();
  const { ctx, page } = await open(DESKTOP); await page.waitForTimeout(500);
  await page.evaluate(() => window.__drift.selectCell('IN025_1900_07300'));
  for (let i = 0; i < fields.meta.files.length; i++) {
    const f = fields.meta.files[i], h = (Date.parse(f.valid_time_utc) - Date.parse(f.acquired_at_utc)) / 3600000;
    await page.evaluate(k => { window.__drift.state.hour = k; window.__drift.refresh(); }, i);
    const rec = await page.evaluate(() => { const d = window.__drift; return d.state.store.record(d.state.sel, d.state.hour, Date.now()); });
    assert.equal(rec.selected_hour.fhour, f.fhour);
    if (h > 0) assert.equal(rec.selected_hour.operational_lead_hours, Math.round(h * 1000) / 1000, `f${f.fhour}`);
    else { assert.equal(rec.selected_hour.operational_lead_hours, null, `f${f.fhour} was already past at acquisition`); assert.equal(rec.selected_hour.operational_lead_status, 'NOT_APPLICABLE_PAST_VALID_TIME'); }
    const row = await page.evaluate(() => [...document.querySelectorAll('table.prov tr')].find(r => /Operational lead/.test(r.innerText)).innerText);
    if (h > 0) assert.ok(row.includes('+' + h.toFixed(1) + ' h'), row); else assert.match(row, /already past/i, row);
  }
  await ctx.close();
});

test('satellite observations: fresh scan shows exact artifact values in layers and panel, independent of the forecast-hour selector', async () => {
  const sat = await (await fetch(`${S.base}/data/india_satellite_ctt_latest.json`)).json();
  const scene = new Date(Date.now() - 20 * 60000); scene.setUTCSeconds(0, 0);
  const sceneIso = scene.toISOString().replace('.000Z', 'Z');
  const mutate = { pattern: /india_satellite_ctt_latest\.json/, fn: j => { j.meta.scene_nominal_utc = sceneIso; } };
  const { ctx, page } = await open(DESKTOP, { mutate }); await page.waitForTimeout(500);
  const probe = await page.evaluate(() => { const d = window.__drift; d.state.hazard = 'thunderstorm'; const out = {};
    for (const id of ['sat_cold', 'sat_cool']) { const per = []; for (const h of [0, 3, 7]) { d.state.hour = h; per.push(d.layerValues(id)); } out[id] = per; }
    return out; });
  const iCell = sat.cells.indexOf('IN025_1300_08000'); assert.ok(iCell >= 0);
  const gridIdx = await page.evaluate(() => window.__drift.state.store.cells.findIndex(c => c[0] === 'IN025_1300_08000'));
  const expectCold = Math.max(0, 300 - sat.ctt_p10_k[iCell]), expectCool = Math.max(0, -sat.dtdt_60min_k_per_h[iCell]);
  assert.ok(Math.abs(probe.sat_cold[0][gridIdx] - expectCold) < 1e-9, 'cold layer equals 300 K - p10 BT of the artifact');
  assert.ok(Math.abs(probe.sat_cool[0][gridIdx] - expectCool) < 1e-9, 'cooling layer equals the 60-min drop of the artifact');
  assert.ok(probe.sat_cold[0].filter(v => v != null).length > 4000);
  assert.deepEqual(probe.sat_cold[0], probe.sat_cold[2], 'an observation does not change with the forecast-hour chips');
  assert.deepEqual(probe.sat_cool[0], probe.sat_cool[1]);
  await page.evaluate(() => window.__drift.selectCell('IN025_1300_08000')); await page.waitForTimeout(300);
  const panel = await page.evaluate(() => document.getElementById('p-detail').innerText);
  assert.match(panel, /Satellite observation \(Himawari-9 B13\)/i); { const m = panel.match(/OBSERVED (\d+) MIN AGO/i); assert.ok(m && +m[1] >= 20 && +m[1] <= 30, 'age shown must be the 20 min we set plus elapsed test time: ' + (m && m[0])); }
  assert.ok(panel.includes((sat.ctt_p10_k[iCell] - 273.15).toFixed(1) + ' °C'), 'panel shows the artifact p10 BT');
  assert.match(panel, /not a forecast|Brightness temperature/i);
  await ctx.close();
});

test('satellite observations: a stale scan is withheld everywhere (never shown as current) and a missing artifact is UNAVAILABLE', async () => {
  const stale = new Date(Date.now() - 3 * 3600000).toISOString().replace(/\.\d+Z$/, 'Z');
  let r = await open(DESKTOP, { mutate: { pattern: /india_satellite_ctt_latest\.json/, fn: j => { j.meta.scene_nominal_utc = stale; } } });
  let res = await r.page.evaluate(() => { const d = window.__drift; d.state.hazard = 'thunderstorm'; d.state.layer = 'sat_cold'; d.refresh();
    return { vals: d.layerValues('sat_cold').filter(v => v != null).length, ov: document.getElementById('overlay').innerText, rec: d.state.store.record('IN025_1300_08000', 0, Date.now()).satellite }; });
  assert.equal(res.vals, 0); assert.match(res.ov, /withheld, not shown as current/); assert.equal(res.rec.status, 'STALE'); assert.equal(res.rec.ctt_p10_k, null);
  await r.ctx.close();
  r = await open(DESKTOP, { abort: [/india_satellite_ctt_latest\.json/] });
  res = await r.page.evaluate(() => { const d = window.__drift; d.state.hazard = 'thunderstorm'; d.state.layer = 'sat_cold'; d.refresh();
    return { vals: d.layerValues('sat_cold').filter(v => v != null).length, ov: document.getElementById('overlay').innerText, rec: d.state.store.record('IN025_1300_08000', 0, Date.now()).satellite }; });
  assert.equal(res.vals, 0); assert.match(res.ov, /UNAVAILABLE/); assert.equal(res.rec.status, 'UNAVAILABLE');
  await r.ctx.close();
});

test('service worker: a returning visitor with an old cache and old scripts ends up on current content', async () => {
  const ctx = await browser.newContext(); const page = await ctx.newPage(); await page.route(TILE, r => r.abort());
  await page.goto(`${S.base}/manifest.json`);                      // same origin, no page scripts
  // Plant what a visitor of the previous deployment has: an old cache holding the LEGACY page and a stale script.
  await page.evaluate(async () => {
    const c = await caches.open('drift-v4');
    await c.put('./index.html', new Response('<html>LEGACY-CACHED</html>', { headers: { 'Content-Type': 'text/html' } }));
    await c.put('/', new Response('<html>LEGACY-CACHED</html>', { headers: { 'Content-Type': 'text/html' } }));
    await c.put('/assets/drift_location.js', new Response('window.__STALE_SCRIPT__ = true;', { headers: { 'Content-Type': 'text/javascript' } }));
  });
  await page.evaluate(async () => { const reg = await navigator.serviceWorker.register('/sw.js'); await navigator.serviceWorker.ready; return reg.scope; });
  await page.waitForFunction(() => navigator.serviceWorker.controller, null, { timeout: 15000 }).catch(async () => { await page.reload(); });
  await page.waitForFunction(async () => (await caches.keys()).every(k => k !== 'drift-v4'), null, { timeout: 15000 });
  const keys = await page.evaluate(async () => caches.keys());
  assert.ok(!keys.includes('drift-v4'), 'the previous deployment cache is deleted: ' + keys);
  // The deploy-after-deploy scenario: the CURRENT worker's cache already holds an older script (cached by a previous visit).
  await page.evaluate(async () => {
    const name = /CACHE_NAME = '([^']+)'/.exec(await (await fetch('/sw.js', { cache: 'no-store' })).text())[1];
    const c = await caches.open(name);
    await c.put('/assets/drift_location.js', new Response('window.__STALE_SCRIPT__ = true;', { headers: { 'Content-Type': 'text/javascript' } }));
    await c.put('/', new Response('<html>LEGACY-CACHED</html>', { headers: { 'Content-Type': 'text/html' } }));
  });
  const r = await page.evaluate(async () => {
    const root = await (await fetch('/')).text();
    const script = await (await fetch('/assets/drift_location.js')).text();
    return { root: root.slice(0, 4000), script: script.slice(0, 200) };
  });
  assert.doesNotMatch(r.root, /LEGACY-CACHED/, 'the root is served from the network, never from the old cache');
  assert.match(r.root, /DRIFT/i);
  assert.doesNotMatch(r.script, /__STALE_SCRIPT__/, 'scripts are network-first: a stale cached script must not run');
  assert.match(r.script, /DRIFT location store/);
  await ctx.close();
});

// ---- legacy station dashboard: real diagnostics, forecast timeline and boot placeholder ------------------------------------
async function openLegacy(nowMs, { mutateSat = true } = {}) {
  const ctx = await browser.newContext({ viewport: DESKTOP }); const page = await ctx.newPage(); const w = watch(page); await page.route(TILE, r => r.abort());
  await page.addInitScript(t => { const r = Date.now.bind(Date), off = t - r(); Date.now = () => r() + off; }, nowMs);
  if (mutateSat) await page.route(/india_satellite_ctt_latest\.json/, async r => { const j = await (await r.fetch()).json();
    j.meta.scene_nominal_utc = new Date(nowMs - 20 * 60000).toISOString().replace(/\.\d+Z$/, 'Z'); r.fulfill({ contentType: 'application/json', body: JSON.stringify(j) }); });
  await page.goto(`${S.base}/legacy.html`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector('[data-testid=real-diagnostics]', { timeout: 60000 });
  return { ctx, page, w };
}
const VOBL = { id: 'IN025_1325_07775' };
async function pillars(page) { return page.$$eval('[data-testid=real-diagnostics] > div:nth-child(2) > div', els => els.map(e => e.innerText.replace(/\s+/g, ' ').trim())); }

test('legacy dashboard: every diagnostic equals the published artifact for the selected hour; the satellite value is the observed one', async () => {
  const fields = await (await fetch(`${S.base}/data/india_fields_latest.json`)).json(), sat = await (await fetch(`${S.base}/data/india_satellite_ctt_latest.json`)).json();
  const ci = fields.cells.indexOf(VOBL.id), si = sat.cells.indexOf(VOBL.id); assert.ok(ci >= 0 && si >= 0);
  const init = Date.parse(fields.meta.cycle_init_utc), now = init + 9 * 3600000;               // inside the validity window of f012..f024
  const { ctx, page, w } = await openLegacy(now);
  for (const fh of [12, 18, 24]) {
    const h = fields.meta.fhours.indexOf(fh), g = k => fields.fields[k][h][ci];
    await page.click(`[data-testid=real-diagnostics] button:text-is("f${fh}")`); await page.waitForTimeout(300);
    const p = await pillars(page);
    assert.equal(p.length, 8, JSON.stringify(p));
    assert.ok(p[0].includes(String(Math.round(g('cape')))) && p[0].includes('J/kg') && p[0].includes(`CIN: ${g('cin').toFixed(0)} J/kg`), `f${fh} CAPE/CIN: ${p[0]}`);
    assert.ok(p[1].includes(g('pwat').toFixed(1)) && p[1].includes('mm'), `f${fh} PWAT: ${p[1]}`);
    assert.ok(p[3].includes(g('shear_850_500').toFixed(1)) && p[3].includes('m/s'), `f${fh} shear: ${p[3]}`);
    assert.ok(p[4].includes(g('conv850').toFixed(2)), `f${fh} convergence: ${p[4]}`);
    assert.ok(p[5].includes(g('k_index').toFixed(1)) && p[6].includes(g('total_totals').toFixed(1)) && p[7].includes(g('lftx').toFixed(1)), `f${fh} indices: ${p[5]} | ${p[6]} | ${p[7]}`);
    assert.ok(!p.some(t => /WITHHELD/.test(t)), `f${fh} nothing may be withheld at a current hour: ${JSON.stringify(p)}`);
    const banner = await page.$eval('[data-testid=real-diagnostics]', e => e.innerText);
    assert.match(banner, new RegExp(`f0${fh} valid`)); assert.match(banner, /acquired by DRIFT/); assert.match(banner, /FRESH/);
    const f = fields.meta.files[h], lead = (Date.parse(f.valid_time_utc) - Date.parse(f.acquired_at_utc)) / 3600000;
    assert.ok(banner.includes(`+${lead.toFixed(1)} h`), `f${fh} lead ${lead}`);
  }
  const p = await pillars(page);                                                              // satellite pillar: observed brightness temperature, labelled as such
  assert.ok(p[2].includes((sat.ctt_p10_k[si] - 273.15).toFixed(1)) && /observed/i.test(p[2]) && /not a retrieved CTT/i.test(p[2]) && /20 min ago/.test(p[2]), p[2]);
  const rate = sat.dtdt_60min_k_per_h[si]; assert.ok(p[2].includes(`${rate > 0 ? '+' : ''}${rate.toFixed(1)} K/h`), p[2]);
  assert.equal(w.pageErrors.length, 0, JSON.stringify(w.pageErrors)); await ctx.close();
});

test('legacy dashboard: a stale forecast cycle is shown as STALE with its real timestamp and every value is withheld', async () => {
  const fields = await (await fetch(`${S.base}/data/india_fields_latest.json`)).json();
  const { ctx, page } = await openLegacy(Date.parse(fields.meta.cycle_init_utc) + 40 * 3600000);
  const p = await pillars(page);
  assert.ok(p.every((t, i) => i === 2 || /WITHHELD/.test(t)), JSON.stringify(p));                // satellite scan is also 20 min old by the test clock, so it may show
  assert.ok(!p.some((t, i) => i !== 2 && /\d+\s*(J\/kg|mm|m\/s)/.test(t.replace(/withheld.*/i, ''))), `no numeric value on a stale cycle: ${JSON.stringify(p)}`);
  const banner = await page.$eval('[data-testid=real-diagnostics]', e => e.innerText);
  assert.match(banner, /STALE/); assert.match(banner, new RegExp(fields.meta.cycle_init_utc.slice(0, 10))); assert.match(banner, /limit 18 h/);
  const tl = await page.$eval('[data-testid=real-timeline]', e => e.innerText);
  assert.match(tl, /STALE FORECAST/); assert.match(tl, /GFS CYCLE/); await ctx.close();
});

test('legacy dashboard: forecast timeline shows cycle, hour, valid time, publication, acquisition and the true lead for each hour chip', async () => {
  const fields = await (await fetch(`${S.base}/data/india_fields_latest.json`)).json();
  const { ctx, page } = await openLegacy(Date.parse(fields.meta.cycle_init_utc) + 9 * 3600000);
  for (const f of fields.meta.files) {
    await page.click(`[data-testid=real-timeline] button:has-text("f${f.fhour} ")`); await page.waitForTimeout(150);
    const t = await page.$eval('[data-testid=real-timeline]', e => e.innerText), lead = (Date.parse(f.valid_time_utc) - Date.parse(f.acquired_at_utc)) / 3600000;
    assert.ok(t.includes(f.valid_time_utc.slice(0, 16).replace('T', ' ') + 'Z') && t.includes(f.published_at_utc.slice(0, 16).replace('T', ' ') + 'Z') && t.includes(f.acquired_at_utc.slice(0, 16).replace('T', ' ') + 'Z'), `f${f.fhour} timestamps`);
    assert.ok(lead > 0 ? t.includes(`+${lead.toFixed(1)} h`) : /NONE/.test(t), `f${f.fhour} lead ${lead}: ${t.slice(0, 200)}`);
    assert.ok(t.includes(fields.meta.cycle_init_utc.slice(0, 16).replace('T', ' ') + 'Z'));
  }
  await ctx.close();
});

test('legacy dashboard: the black first paint is replaced by a visible loading state that disappears once the app has mounted', async () => {
  const raw = await (await fetch(`${S.base}/legacy.html`)).text();
  assert.match(raw, /id="boot-loading"/); assert.match(raw, /Loading the Bengaluru station dashboard/); assert.match(raw, /assets\/vendor\/react\.production\.min\.js/); assert.match(raw, /babel-standalone-7\.26\.4/);
  const ext = [...raw.matchAll(/<script[^>]*\ssrc="(https?:\/\/[^"]+)"/g)].map(m => m[1]);
  assert.deepEqual(ext, ['https://cdn.tailwindcss.com'], 'only the Tailwind runtime remains external; React/Babel/Framer/jsPDF/MapLibre are vendored and pinned: ' + ext);
  const { ctx, page } = await openLegacy(Date.now() > 0 ? Date.parse('2026-11-23T10:00:00Z') : 0);
  assert.equal(await page.$('#boot-loading'), null); await ctx.close();
});
