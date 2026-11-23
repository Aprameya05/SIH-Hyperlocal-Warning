// node tests/e2e/shots.mjs [outDir] : screenshots of the command centre at the review sizes (not a test; used for the visual comparison against the design references).
import fs from 'node:fs';
import path from 'node:path';
import { buildDist, serve, launch } from './harness.mjs';

const out = process.argv[2] || path.join(process.env.TEMP || '/tmp', 'drift_shots');
fs.mkdirSync(out, { recursive: true });
const SIZES = [['1920x1080', 1920, 1080], ['1600x900', 1600, 900], ['1440x900', 1440, 900], ['1366x768', 1366, 768], ['tablet', 820, 1180], ['mobile', 390, 844]];
const only = process.argv[3] ? process.argv[3].split(',') : null;
const S = await serve(buildDist()); const b = await launch();
const STYLE = { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b1a36' } }] };
for (const [name, w, h] of SIZES) {
  if (only && !only.includes(name)) continue;
  const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1, hasTouch: w < 700 }); const p = await ctx.newPage();
  await p.route(/tile\.openstreetmap/, r => r.abort());
  await p.route(/basemaps\.cartocdn\.com.*style\.json/, r => r.fulfill({ contentType: 'application/json', body: JSON.stringify(STYLE) }));
  await p.goto(S.base + '/', { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(() => window.__drift && window.__drift.ready && window.__drift.ready(), null, { timeout: 60000 }); await p.waitForTimeout(1500);
  await p.evaluate(() => { window.__drift.selectCell('IN025_1975_07600'); });
  await p.waitForTimeout(900);
  await p.evaluate(() => { const c = document.querySelector('.acc[data-acc=inst] > button'); if (c && !c.parentNode.classList.contains('open')) c.click(); });
  await p.waitForTimeout(1400);
  await p.screenshot({ path: path.join(out, `${name}.png`) });
  await ctx.close();
}
await b.close(); S.srv.close(); console.log('saved to', out); process.exit(0);
