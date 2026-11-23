// Shared harness: builds the REAL publish bundle, serves it, launches the system Chrome via playwright-core.
import { chromium } from 'playwright-core';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const DEFAULT_CHROME = { darwin: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  win32: 'C:/Program Files/Google/Chrome/Application/chrome.exe', linux: '/usr/bin/google-chrome' }[process.platform];
export const CHROME = process.env.CHROME_PATH || DEFAULT_CHROME;
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.geojson': 'application/json', '.png': 'image/png', '.ico': 'image/x-icon', '.css': 'text/css' };

// DRIFT_BASE_URL=https://<preview>.pages.dev runs the suite against a deployed site instead of a locally built bundle.
export const REMOTE = process.env.DRIFT_BASE_URL ? process.env.DRIFT_BASE_URL.replace(/\/$/, '') : null;
const remoteLog = [];
export function buildDist() {
  if (REMOTE) return null;
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'drift-dist-'));
  const dist = path.join(out, 'pages_dist');
  execFileSync(process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3'), [path.join(ROOT, 'pipelines/publishing/build_pages_bundle.py'), dist], { stdio: 'pipe' });
  return dist;
}
export function serve(dist) {
  if (REMOTE) return Promise.resolve({ srv: { close() {} }, log: remoteLog, base: REMOTE });
  const log = [];
  const srv = http.createServer((req, res) => {
    const u = decodeURIComponent(req.url.split('?')[0]);
    const f = path.join(dist, u === '/' ? 'index.html' : u);
    log.push({ method: req.method, url: u, status: 0 });
    const rec = log[log.length - 1];
    if (!f.startsWith(dist) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { rec.status = 404; res.writeHead(404); return res.end('nf'); }
    rec.status = 200;
    res.writeHead(200, { 'Content-Type': MIME[path.extname(f)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    fs.createReadStream(f).pipe(res);
  });
  return new Promise(r => srv.listen(0, '127.0.0.1', () => r({ srv, log, base: `http://127.0.0.1:${srv.address().port}` })));
}
export async function launch() {
  const b = await chromium.launch({ executablePath: CHROME, headless: true,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-first-run'] });
  if (REMOTE) {   // record every response of every page so the "nothing 404s" assertions work against a deployed site
    const attach = c => c.on('response', r => { const u = new URL(r.url()); if (u.origin === new URL(REMOTE).origin) remoteLog.push({ method: r.request().method(), url: u.pathname, status: r.status() }); });
    const nc = b.newContext.bind(b); b.newContext = async (...a) => { const c = await nc(...a); attach(c); return c; };
    const np = b.newPage.bind(b); b.newPage = async (...a) => { const pg = await np(...a); attach(pg.context()); return pg; };
  }
  return b;
}
// Collects everything a judge's browser would object to.
export function watch(page, { allowHosts = [] } = {}) {
  const w = { errors: [], pageErrors: [], failed: [], nonGet: [], external: [] };
  page.on('console', m => { if (m.type() === 'error') w.errors.push(m.text()); });
  page.on('pageerror', e => w.pageErrors.push(String(e)));
  page.on('requestfailed', r => w.failed.push(r.url() + ' ' + (r.failure() && r.failure().errorText)));
  page.on('request', r => {
    const u = new URL(r.url());
    if (r.method() !== 'GET' && r.method() !== 'HEAD') w.nonGet.push(r.method() + ' ' + r.url());
    if (!/^(127\.0\.0\.1|localhost)$/.test(u.hostname) && !(REMOTE && u.origin === new URL(REMOTE).origin) && u.protocol.startsWith('http')) w.external.push(u.hostname);
  });
  return w;
}
