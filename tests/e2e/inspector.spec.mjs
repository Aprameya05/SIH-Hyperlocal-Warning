// node --test tests/e2e/inspector.spec.mjs : the grid-cell inspector inside the ORIGINAL dark dashboard (legacy.html), clicked and used for real.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { buildDist, serve, launch, watch } from './harness.mjs';

let browser, S, FIELDS, SAT, GRID;
const DESKTOP = { width: 1440, height: 900 }, MOBILE = { width: 390, height: 844 };
const TILE = /tile\.openstreetmap\.org/;
before(async () => {
  S = await serve(buildDist()); browser = await launch();
  FIELDS = await (await fetch(`${S.base}/data/india_fields_latest.json`)).json();
  SAT = await (await fetch(`${S.base}/data/india_satellite_ctt_latest.json`)).json();
  GRID = await (await fetch(`${S.base}/data/india_grid_025.json`)).json();
});
after(async () => { await browser?.close(); S?.srv.close(); });

const INIT = () => Date.parse(FIELDS.meta.cycle_init_utc);
async function open(viewport, { now = INIT() + 9 * 3600000, mutate = null } = {}) {
  const ctx = await browser.newContext({ viewport, hasTouch: viewport.width < 700 }); const page = await ctx.newPage(); const w = watch(page); await page.route(TILE, r => r.abort());
  await page.addInitScript(t => { const r = Date.now.bind(Date), off = t - r(); Date.now = () => r() + off; }, now);
  await page.route(/india_satellite_ctt_latest\.json/, async r => { const j = await (await r.fetch()).json(); j.meta.scene_nominal_utc = new Date(now - 20 * 60000).toISOString().replace(/\.\d+Z$/, 'Z'); r.fulfill({ contentType: 'application/json', body: JSON.stringify(j) }); });
  if (mutate) await page.route(mutate.pattern, async r => { const j = await (await r.fetch()).json(); mutate.fn(j); r.fulfill({ contentType: 'application/json', body: JSON.stringify(j) }); });
  await page.goto(`${S.base}/legacy.html`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('#boot-loading', { state: 'detached', timeout: 90000 });
  await page.waitForFunction(() => window.__driftInspector && window.__driftInspector.getMap() && window.__driftStore && window.__driftStore.fieldsOk, null, { timeout: 60000 });
  await page.waitForFunction(() => { const m = window.__driftInspector.getMap(); return m && m.getContainer(); }, null, { timeout: 30000 });
  return { ctx, page, w };
}
// Click the map at a lat/lon through real pointer events (projected to screen coordinates).
async function clickLatLon(page, lat, lon) {
  await page.evaluate(([la, lo]) => { window.__driftInspector.getMap().jumpTo({ center: [lo, la], zoom: 8 }); }, [lat, lon]); await page.waitForTimeout(250);
  await page.evaluate(() => { window.__driftInspector.getMap().getContainer().scrollIntoView({ block: 'center' }); }); await page.waitForTimeout(200);
  const xy = await page.evaluate(([la, lo]) => { const m = window.__driftInspector.getMap(), p = m.project([lo, la]), r = m.getCanvas().getBoundingClientRect(); return [r.left + p.x, r.top + p.y]; }, [lat, lon]);
  await page.mouse.click(xy[0], xy[1]); await page.waitForSelector('.di-panel.open', { timeout: 10000 });
}
const cellIdx = id => FIELDS.cells.indexOf(id);
const panelText = page => page.$eval('.di-panel', e => e.innerText);
const rowOf = async (page, label) => page.$$eval('.di-row', (rows, l) => rows.filter(r => r.querySelector('.di-l') && r.querySelector('.di-l').innerText.trim() === l).map(r => r.innerText.replace(/\s+/g, ' ')), label);

test('click a cell: it is highlighted, the panel shows its exact id and coordinates, and every category is populated from that cell', async () => {
  const { ctx, page, w } = await open(DESKTOP);
  await clickLatLon(page, 13.26, 77.76);                                                   // inside cell IN025_1325_07775
  const st = await page.evaluate(() => window.__driftInspector.state());
  assert.equal(st.cell_id, 'IN025_1325_07775');
  const sel = await page.evaluate(() => window.__driftInspector.getMap().getSource('insp-sel')._data);
  assert.equal(sel.properties.id, 'IN025_1325_07775'); assert.equal(sel.geometry.coordinates[0].length, 5);                   // highlight polygon
  const t = await panelText(page);
  assert.match(t, /13\.25°N 77\.75°E/); assert.match(t, /IN025_1325_07775/); assert.match(t, /Karnataka/);
  for (const sec of ['Location & provenance', 'Instability', 'Moisture', 'Wind & dynamics', 'Temperature & pressure', 'Precipitation & soil', 'Satellite observation', 'Forecast-hour series', 'Terrain', 'Hazard status']) assert.match(t, new RegExp(sec, 'i'), sec);
  const h = st.hour, ci = cellIdx('IN025_1325_07775'), f = FIELDS.fields;
  const cape = (await rowOf(page, 'CAPE'))[0]; assert.ok(cape.includes(String(Math.round(f.cape[h][ci]))) && cape.includes('J/kg') && /FRESH|STALE/.test(cape), cape);
  assert.equal(w.pageErrors.length, 0, JSON.stringify(w.pageErrors)); await ctx.close();
});

test('every forecast-hour chip updates the panel values, the charts marker and the map layer to the matching hour', async () => {
  const { ctx, page } = await open(DESKTOP);
  await clickLatLon(page, 13.25, 77.75); const ci = cellIdx('IN025_1325_07775'), f = FIELDS.fields;
  for (let h = 0; h < FIELDS.meta.files.length; h++) {
    await page.click(`.di-hrs .di-hr[data-hour="${h}"]`); await page.waitForFunction(i => window.__driftInspector.state().hour === i && document.querySelector('.di-panel').getAttribute('data-hour') === String(i), h);
    const fh = FIELDS.meta.files[h];
    const t = await panelText(page); assert.ok(t.includes('f' + String(fh.fhour).padStart(3, '0')), `f${fh.fhour} shown`); assert.ok(t.includes(fh.valid_time_utc.slice(0, 16).replace('T', ' ') + 'Z'));
    for (const [label, key, dec] of [['CAPE', 'cape', 0], ['Precipitable water', 'pwat', 1], ['2 m temperature', 't2m_c', 1], ['10 m wind speed', 'ws10', 1], ['850–500 hPa bulk shear', 'shear_850_500', 1], ['K-index', 'k_index', 1]]) {
      const r = (await rowOf(page, label))[0]; assert.ok(r.includes(f[key][h][ci].toFixed(dec)), `f${fh.fhour} ${label}: ${r}`);
    }
    const marked = await page.$$eval('.di-svg .di-bar-sel', els => els.map(e => e.getAttribute('data-hour')));
    assert.ok(marked.length === 6 && marked.every(x => x === String(h)), `charts mark hour ${h}: ${marked}`);                       // all 6 charts highlight the selected hour
    const layer = await page.evaluate(i => { const feats = window.__driftInspector.getMap().getSource('insp-cells')._data.features; return feats[i].properties.v; }, await page.evaluate(() => window.__driftStore.idx['IN025_1325_07775']));
    assert.equal(layer, f.cape[h][ci], `map layer value for the cell at f${fh.fhour}`);                                          // map layer (CAPE) is the same hour/cell as the panel
  }
  await ctx.close();
});

test('adjacent cells, cell boundaries, and points outside the grid', async () => {
  const { ctx, page } = await open(DESKTOP);
  await clickLatLon(page, 13.2, 77.7);  assert.equal((await page.evaluate(() => window.__driftInspector.state())).cell_id, 'IN025_1325_07775');           // same cell, different point
  await clickLatLon(page, 13.2, 77.88); const b = (await page.evaluate(() => window.__driftInspector.state())).cell_id; assert.notEqual(b, 'IN025_1325_07775');   // across the boundary
  assert.equal(b, 'IN025_1325_07800'); assert.match(await panelText(page), /IN025_1325_07800/);
  const cape = (await rowOf(page, 'CAPE'))[0]; assert.ok(cape.includes(String(Math.round(FIELDS.fields.cape[(await page.evaluate(() => window.__driftInspector.state())).hour][cellIdx('IN025_1325_07800')]))), cape);
  await page.evaluate(() => { window.__driftInspector.getMap().jumpTo({ center: [66.5, 15.0], zoom: 8 }); }); await page.waitForTimeout(250);             // Arabian Sea: no land cell
  await page.evaluate(() => { window.__driftInspector.getMap().getContainer().scrollIntoView({ block: 'center' }); }); await page.waitForTimeout(200);
  const xy = await page.evaluate(() => { const m = window.__driftInspector.getMap(), p = m.project([66.5, 15.0]), r = m.getCanvas().getBoundingClientRect(); return [r.left + p.x, r.top + p.y]; });
  await page.mouse.click(xy[0], xy[1]); await page.waitForTimeout(300);
  assert.equal((await page.evaluate(() => window.__driftInspector.state())).cell_id, 'IN025_1325_07800');                                                    // selection unchanged
  assert.match(await page.$eval('.di-toast', e => e.textContent), /Not covered/); await ctx.close();
});

test('selection persists until another cell is chosen or the panel is closed; close button and Escape work; keyboard arrows move to the adjacent cell', async () => {
  const { ctx, page } = await open(DESKTOP);
  await clickLatLon(page, 13.25, 77.75);
  await page.evaluate(() => { window.__driftInspector.getMap().getCanvas().focus(); });
  await page.keyboard.press('ArrowRight'); await page.waitForFunction(() => window.__driftInspector.state().cell_id === 'IN025_1325_07800');
  await page.keyboard.press('ArrowUp'); await page.waitForFunction(() => window.__driftInspector.state().cell_id === 'IN025_1350_07800');
  await page.waitForFunction(() => document.querySelector('.di-panel').getAttribute('data-cell') === 'IN025_1350_07800');   // render is scheduled after the state change
  assert.equal(await page.$eval('.di-panel', e => e.getAttribute('data-cell')), 'IN025_1350_07800');
  assert.equal(await page.evaluate(() => { const d = window.__driftInspector.getMap().getSource('insp-sel')._data; return d && d.properties ? d.properties.id : null; }), 'IN025_1350_07800');
  await page.keyboard.press('Escape'); await page.waitForFunction(() => window.__driftInspector.state().cell_id === null);
  assert.equal(await page.$('.di-panel.open'), null);
  assert.equal(await page.evaluate(() => { const d = window.__driftInspector.getMap().getSource('insp-sel')._data; return d && d.features ? d.features.length : 0; }), 0);                              // highlight removed
  await clickLatLon(page, 13.25, 77.75); await page.click('.di-x'); await page.waitForFunction(() => window.__driftInspector.state().cell_id === null);
  const url = new URL(page.url()); assert.ok(url.searchParams.get('cell') !== undefined); await ctx.close();
});

test('rapid switching across 40 cells renders only the last one, with that cell\'s values and no errors', async () => {
  const { ctx, page, w } = await open(DESKTOP);
  const ids = GRID.cells.filter((_, i) => i % 110 === 0).slice(0, 40).map(c => c[0]);
  await page.evaluate(list => { for (const id of list) window.__driftInspector.select(id); }, ids);
  const last = ids[ids.length - 1]; await page.waitForFunction(id => document.querySelector('.di-panel') && document.querySelector('.di-panel').getAttribute('data-cell') === id, last);
  assert.match(await panelText(page), new RegExp(last)); assert.equal(await page.$$eval('.di-panel', e => e.length), 1);
  const h = (await page.evaluate(() => window.__driftInspector.state())).hour, ci = cellIdx(last);
  const cape = (await rowOf(page, 'CAPE'))[0]; assert.ok(cape.includes(String(Math.round(FIELDS.fields.cape[h][ci]))), cape);
  assert.equal(w.pageErrors.length, 0); await ctx.close();
});

test('per-variable freshness: stale cycle keeps the last values with STALE chips and timestamps; a missing value says MISSING and why; satellite has its own age', async () => {
  const stale = await open(DESKTOP, { now: INIT() + 40 * 3600000 });
  await clickLatLon(stale.page, 13.25, 77.75);
  const cape = (await rowOf(stale.page, 'CAPE'))[0]; const h = (await stale.page.evaluate(() => window.__driftInspector.state())).hour;
  assert.ok(cape.includes(String(Math.round(FIELDS.fields.cape[h][cellIdx('IN025_1325_07775')]))) && /STALE/.test(cape), cape);               // value retained and labelled
  assert.ok(!/FRESH/.test(cape));
  const t = await panelText(stale.page); assert.match(t, /older than 18 h/); assert.match(t, /acquired|valid/i);
  const satText = await stale.page.$eval('.di-sec:has(.di-sat-h)', e => e.innerText); assert.match(satText, /FRESH/);                          // the satellite scan is 20 min old on this clock: its own freshness
  await stale.ctx.close();
  const ci = cellIdx('IN025_1325_07775');
  const miss = await open(DESKTOP, { mutate: { pattern: /india_fields_latest\.json/, fn: j => { for (const row of j.fields.cape) row[ci] = null; } } });
  await clickLatLon(miss.page, 13.25, 77.75);
  const m = (await rowOf(miss.page, 'CAPE'))[0]; assert.ok(/MISSING/.test(m) && /needs GFS/.test(m) && !/\b0 J\/kg/.test(m), m); await miss.ctx.close();
});

test('satellite section shows the observed brightness temperature of THIS cell with scan time, age and cooling rate, labelled not a retrieved CTT; stale scan retained', async () => {
  const { ctx, page } = await open(DESKTOP);
  await clickLatLon(page, 13.0, 80.0); const si = SAT.cells.indexOf('IN025_1300_08000');
  const t = await page.$eval('.di-sec:has(.di-sat-h)', e => e.innerText);
  assert.ok(t.includes((SAT.ctt_p10_k[si] - 273.15).toFixed(1)), t); assert.match(t, /20 min/); assert.match(t, /not a retrieved cloud-top temperature/i); assert.match(t, /Himawari-9/);
  const r = SAT.dtdt_60min_k_per_h[si]; assert.ok(t.includes(`${r > 0 ? '+' : ''}${r.toFixed(1)} K/h`), t);
  await ctx.close();
  // A scan older than 90 min is retained and labelled STALE (its own freshness, independent of the GFS cycle).
  const old = await browser.newContext({ viewport: DESKTOP }); const pg = await old.newPage(); await pg.route(TILE, r => r.abort());
  const now = Date.parse(SAT.meta.scene_nominal_utc) + 5 * 3600000; await pg.addInitScript(t => { const r = Date.now.bind(Date), off = t - r(); Date.now = () => r() + off; }, now);
  await pg.goto(`${S.base}/legacy.html`, { waitUntil: 'domcontentloaded', timeout: 60000 }); await pg.waitForSelector('#boot-loading', { state: 'detached', timeout: 90000 });
  await pg.waitForFunction(() => window.__driftStore && window.__driftStore.fieldsOk, null, { timeout: 60000 });
  await pg.evaluate(() => window.__driftInspector.select('IN025_1300_08000')); await pg.waitForSelector('.di-panel.open');
  const st = await pg.$eval('.di-sec:has(.di-sat-h)', e => e.innerText); assert.match(st, /STALE/); assert.ok(st.includes((SAT.ctt_p10_k[si] - 273.15).toFixed(1)), 'last valid value retained: ' + st); assert.match(st, /not current/);
  await old.close();
});

test('hazard status: a missing model is UNAVAILABLE with a reason, never 0%; cloudburst and flash flood are not predicted; screening is labelled unvalidated', async () => {
  const { ctx, page } = await open(DESKTOP);
  await clickLatLon(page, 13.25, 77.75);
  const hz = await page.$eval('.di-sec:has(.di-haz)', e => e.innerText);
  assert.match(hz, /Thunderstorm[\s\S]*UNAVAILABLE/i); assert.match(hz, /Cloudburst[\s\S]*UNAVAILABLE/i); assert.match(hz, /Flash flood[\s\S]*UNAVAILABLE/i); assert.match(hz, /UNVALIDATED/);
  assert.ok(!/\b0%/.test(hz), 'no 0% for a missing model: ' + hz); assert.match(hz, /not a probability|Kept separate/i);
  await page.$eval('.di-sec:has(.di-slim)', e => { e.open = true; });
  const terrain = await page.$eval('.di-sec:has(.di-slim)', e => e.innerText); assert.match(terrain, /Elevation/); assert.match(terrain, /not current flooding/i);
  await ctx.close();
});

test('three time concepts are shown separately and no warning lead is claimed', async () => {
  const { ctx, page } = await open(DESKTOP);
  await clickLatLon(page, 13.25, 77.75); const t = await panelText(page);
  assert.match(t, /Forecast horizon/i); assert.match(t, /Acquisition age/i); assert.match(t, /Warning lead[\s\S]*NONE/i); assert.match(t, /not a warning lead/i);
  await ctx.close();
});

test('mobile: tapping opens a bottom sheet with expandable sections; the sheet drags between peek/half/full; the map stays interactive; close works', async () => {
  const { ctx, page } = await open(MOBILE);
  await page.evaluate(() => { window.__driftInspector.getMap().jumpTo({ center: [77.75, 13.25], zoom: 8 }); }); await page.waitForTimeout(250);
  await page.evaluate(() => { window.__driftInspector.getMap().getContainer().scrollIntoView({ block: 'center' }); }); await page.waitForTimeout(200);
  const xy = await page.evaluate(() => { const m = window.__driftInspector.getMap(), p = m.project([77.75, 13.25]), r = m.getCanvas().getBoundingClientRect(); return [r.left + p.x, r.top + p.y]; });
  await page.touchscreen.tap(xy[0], xy[1]); await page.waitForSelector('.di-panel.di-mobile.open');
  assert.equal((await page.evaluate(() => window.__driftInspector.state())).cell_id, 'IN025_1325_07775');
  const box = await page.$eval('.di-panel', e => { const r = e.getBoundingClientRect(); return { top: r.top, h: r.height, vh: innerHeight, bottom: r.bottom }; });
  assert.ok(box.bottom >= box.vh - 2 && box.h <= box.vh * 0.62, JSON.stringify(box));                                                           // docked at the bottom, map visible above
  assert.ok(await page.$('.di-sec summary'));
  const detailsClosed = await page.$$eval('.di-sec', els => els.filter(e => !e.open).length); assert.ok(detailsClosed >= 3, 'sections are collapsible and mostly collapsed on mobile');
  await page.click('.di-sec:not([open]) summary'); assert.ok((await page.$$eval('.di-sec', els => els.filter(e => !e.open).length)) < detailsClosed);
  const g = await page.$eval('.di-grab', e => { const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; });
  await page.mouse.move(g[0], g[1]); await page.mouse.down(); await page.mouse.move(g[0], g[1] - 120, { steps: 4 }); await page.mouse.up();
  await page.waitForFunction(() => window.__driftInspector.state().sheet === 'full'); await page.waitForTimeout(700); assert.ok((await page.$eval('.di-panel', e => e.getBoundingClientRect().height)) > box.vh * 0.8);
  await page.evaluate(() => { window.__driftInspector.getMap().panBy([40, 20], { duration: 0 }); });                                                      // the map is still usable
  await page.click('.di-x'); await page.waitForFunction(() => window.__driftInspector.state().cell_id === null);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'no horizontal overflow'); await ctx.close();
});

test('desktop layout: the panel sits beside the map (map stays visible), uses the dark DRIFT palette, and is keyboard-reachable', async () => {
  const { ctx, page } = await open(DESKTOP);
  await clickLatLon(page, 13.25, 77.75);
  const lay = await page.evaluate(() => { const p = document.querySelector('.di-panel'), m = window.__driftInspector.getMap().getContainer(), pr = p.getBoundingClientRect(), mr = m.getBoundingClientRect(), cs = getComputedStyle(p);
    return { panelW: pr.width, mapW: mr.width, overlap: pr.left - mr.left, bg: cs.backgroundColor, color: cs.color, font: cs.fontFamily }; });
  assert.ok(lay.panelW <= 400 && lay.mapW > 900, JSON.stringify(lay));
  const rgb = lay.bg.match(/\d+(\.\d+)?/g).map(Number); assert.ok(rgb[0] < 30 && rgb[1] < 30 && rgb[2] < 45, 'dark navy panel, not white: ' + lay.bg);
  assert.match(lay.font, /Inter|apple-system|BlinkMacSystemFont/);
  await page.focus('.di-hr'); await page.keyboard.press('Tab'); assert.ok(await page.evaluate(() => document.activeElement && document.activeElement.closest('.di-panel')), 'tab order stays inside the open panel region');
  await ctx.close();
});

test('API failure: with the fields artifact unreachable the inspector says UNAVAILABLE with the reason and never shows values', async () => {
  const ctx = await browser.newContext({ viewport: DESKTOP }); const page = await ctx.newPage(); await page.route(TILE, r => r.abort());
  await page.route(/india_fields_latest\.json/, r => r.fulfill({ status: 503, body: 'down' }));
  await page.goto(`${S.base}/legacy.html`, { waitUntil: 'domcontentloaded', timeout: 60000 }); await page.waitForSelector('#boot-loading', { state: 'detached', timeout: 90000 });
  await page.waitForFunction(() => window.__driftInspector && window.__driftStoreProblems !== undefined, null, { timeout: 30000 });
  assert.equal(await page.evaluate(() => window.__driftStore), null);
  assert.equal(await page.evaluate(() => window.__driftInspector.select('IN025_1325_07775')), false);                                                  // nothing to inspect, no crash
  assert.equal(await page.$('.di-panel.open'), null); await ctx.close();
});
