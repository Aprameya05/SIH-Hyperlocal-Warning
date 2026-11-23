// node --test tests/e2e/shell.spec.mjs : the command centre keeps its own panels and adds the classic dashboard's sections as tabs, in the old dark look.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { buildDist, serve, launch, watch } from './harness.mjs';

let browser, S;
const DESKTOP = { width: 1440, height: 900 }, MOBILE = { width: 390, height: 844 };
const TILE = /tile\.openstreetmap\.org/;
const ADDED = [['forecast', 'Forecast'], ['satellite', 'Satellite'], ['models', 'Models'], ['explain', 'Explain'], ['regimes', 'Regimes'], ['scores', 'Scores'], ['graphs', 'Graphs']];
before(async () => {
  S = await serve(buildDist()); browser = await launch();
  const newContext = browser.newContext.bind(browser);   // hermetic: the remote basemap style is not under test
  browser.newContext = async (o) => { const c = await newContext(o); await c.route(/basemaps\.cartocdn\.com\/gl\/dark-matter-gl-style\/style\.json/, r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b1020' } }] }) })); return c; };
});
after(async () => { await browser?.close(); S?.srv.close(); });

async function open(url = '/', viewport = DESKTOP) {
  const ctx = await browser.newContext({ viewport, colorScheme: 'light' }); const page = await ctx.newPage(); const w = watch(page); await page.route(TILE, r => r.abort());
  await page.goto(S.base + url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForFunction(() => window.__drift && window.__drift.ready && window.__driftShell, null, { timeout: 60000 });
  return { ctx, page, w };
}
const frameBody = page => page.frameLocator('#legacy-frame').locator('body').innerText();
async function frameReady(page) {
  await page.waitForFunction(() => { const f = document.getElementById('legacy-frame'); try { return f.contentDocument && f.contentDocument.readyState !== 'loading' && f.contentDocument.getElementById('root') && !f.contentDocument.getElementById('boot-loading') && f.contentDocument.body.innerText.length > 300; } catch (e) { return false; } }, null, { timeout: 90000 });
}

test('dark in any colour scheme; the command centre keeps its own tabs; the classic sections are added; the page fits the screen', async () => {
  const { ctx, page, w } = await open();
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor); const m = bg.match(/\d+/g).map(Number); assert.ok(m[0] < 40 && m[1] < 40 && m[2] < 60, 'dark background even when the OS prefers light: ' + bg);
  const nav = await page.$$eval('#nav [role=tab]', e => e.map(b => b.textContent)); assert.deepEqual(nav, ['Command centre', ...ADDED.map(a => a[1])]);
  const panels = await page.$$eval('.rtabs button', e => e.map(b => b.textContent)); assert.deepEqual(panels, ['Location', 'Hazards', 'Compare', 'Alerts & actions', 'Sources', 'Evaluation']);   // existing tabs unchanged
  assert.ok(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight + 2), 'no page-level scrolling on desktop');
  assert.equal(await page.evaluate(() => document.getElementById('home-link').getAttribute('href')), 'legacy.html'); assert.match(await page.innerText('#home-link'), /Classic dashboard/);
  assert.equal(w.pageErrors.length, 0, JSON.stringify(w.pageErrors)); await ctx.close();
});

test('every added tab opens, shows real content for its section, follows ?tab=, and Back/Forward work', async () => {
  const { ctx, page, w } = await open();
  for (const [id, label] of ADDED) {
    await page.click(`#tab-${id}`); await frameReady(page);
    assert.equal(await page.evaluate(() => new URL(location.href).searchParams.get('tab')), id);
    assert.equal(await page.evaluate(i => document.getElementById('tab-' + i).getAttribute('aria-selected'), id), 'true');
    const t = await frameBody(page); assert.ok(t.length > 300, `${label} has content`); assert.doesNotMatch(t, /REACT RUNTIME ERROR|\bundefined\b|\bNaN\b|\[object Object\]/, `${label} clean`);
    assert.equal(await page.evaluate(() => !!document.querySelector('#view-main.off')), true); assert.equal(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight + 2), true, `${label}: the page itself does not scroll`);
  }
  await page.goBack(); await page.waitForFunction(() => window.__driftShell.current() === 'scores'); await page.goBack(); await page.waitForFunction(() => window.__driftShell.current() === 'regimes');
  await page.goForward(); await page.waitForFunction(() => window.__driftShell.current() === 'scores');
  await page.click('#tab-main'); assert.equal(await page.evaluate(() => !document.getElementById('view-main').classList.contains('off')), true);
  assert.equal(w.pageErrors.length, 0, JSON.stringify(w.pageErrors)); await ctx.close();
});

test('direct links open the right tab, and the selected grid cell follows you between the command centre and the added tabs', async () => {
  const { ctx, page } = await open('/?tab=graphs'); await frameReady(page);
  assert.equal(await page.evaluate(() => window.__driftShell.current()), 'graphs');
  await page.click('#tab-main');
  await page.evaluate(() => { window.__drift.selectCell('IN025_1300_08000'); }); await page.click('#tab-graphs');
  await page.waitForFunction(() => { const f = document.getElementById('legacy-frame').contentWindow; return f.__driftInspector && f.__driftInspector.state().cell_id === 'IN025_1300_08000'; }, null, { timeout: 60000 });
  await page.waitForFunction(() => /IN025_1300_08000/.test(document.getElementById('legacy-frame').contentDocument.body.innerText), null, { timeout: 30000 });
  await page.evaluate(() => { document.getElementById('legacy-frame').contentWindow.__driftInspector.select('IN025_1325_07775'); });
  await page.click('#tab-main'); assert.equal(await page.evaluate(() => window.__drift.state.sel), 'IN025_1325_07775');
  await ctx.close();
});

test('keyboard: arrow keys move between tabs; mobile: the tab bar scrolls sideways and the page never overflows', async () => {
  const { ctx, page } = await open();
  await page.focus('#tab-main'); await page.keyboard.press('ArrowRight'); await page.waitForFunction(() => window.__driftShell.current() === 'forecast');
  await page.keyboard.press('End'); await page.waitForFunction(() => window.__driftShell.current() === 'graphs'); await ctx.close();
  const m = await open('/', MOBILE);
  assert.equal(await m.page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, 'no horizontal overflow on mobile');
  await m.page.click('#tab-scores'); await frameReady(m.page); assert.equal(await m.page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true);
  await m.ctx.close();
});
