import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../../worker/index.js';

function kv() {
  const m = new Map();
  return {
    get: async k => m.get(k) ?? null,
    put: async (k, v) => { m.set(k, v); },
    delete: async k => { m.delete(k); },
    list: async () => ({ keys: [...m.keys()].map(name => ({ name })) }),
    _m: m,
  };
}
const req = (path, method = 'GET', body, headers = {}) =>
  new Request('https://w.test' + path, {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
const mkEnv = (o = {}) => ({ SUBSCRIBERS: kv(), TOKEN_SECRET: 's3cret', ADMIN_KEY: 'adm', ...o });

test('subscribe fails closed without TOKEN_SECRET (no predictable default)', async () => {
  const r = await worker.fetch(req('/subscribe', 'POST', { email: 'a@b.co' }), mkEnv({ TOKEN_SECRET: undefined }));
  assert.equal(r.status, 503);
});

test('subscribe never returns the unsubscribe token, new or existing', async () => {
  const env = mkEnv();
  const a = await (await worker.fetch(req('/subscribe', 'POST', { email: 'a@b.co' }), env)).json();
  const b = await (await worker.fetch(req('/subscribe', 'POST', { email: 'a@b.co' }), env)).json();
  assert.equal(a.token, undefined);
  assert.equal(b.token, undefined);
  assert.equal(a.message, b.message); // no enumeration signal
});

test('unsubscribe page escapes user-controlled name (no stored XSS)', async () => {
  const env = mkEnv();
  await worker.fetch(req('/subscribe', 'POST', { email: 'x@y.co', name: '<script>alert(1)</script>' }), env);
  const key = [...env.SUBSCRIBERS._m.keys()][0];
  const html = await (await worker.fetch(req('/unsubscribe?token=' + key.slice(4)), env)).text();
  assert.ok(!html.includes('<script>alert(1)</script>'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('input limits and validation', async () => {
  const env = mkEnv();
  assert.equal((await worker.fetch(req('/subscribe', 'POST', { email: 'a'.repeat(300) + '@b.co' }), env)).status, 400);
  assert.equal((await worker.fetch(req('/subscribe', 'POST', { phone: 'abc' }), env)).status, 400);
  assert.equal(
    (await worker.fetch(req('/subscribe', 'POST', { email: 'a@b.co', whatsapp_apikey: 'k'.repeat(200) }), env)).status, 400);
});

test('subscriber list requires the admin key', async () => {
  assert.equal((await worker.fetch(req('/subscribers'), mkEnv())).status, 401);
  assert.equal((await worker.fetch(req('/subscribers', 'GET', undefined, { 'X-Admin-Key': 'wrong' }), mkEnv())).status, 401);
  assert.equal((await worker.fetch(req('/subscribers', 'GET', undefined, { 'X-Admin-Key': 'adm' }), mkEnv())).status, 200);
  assert.equal((await worker.fetch(req('/subscribers', 'GET', undefined, { 'X-Admin-Key': '' }), mkEnv({ ADMIN_KEY: undefined }))).status, 401);
});

test('CORS does not advertise the admin header', async () => {
  const r = await worker.fetch(req('/health', 'OPTIONS'), mkEnv());
  assert.ok(!(r.headers.get('access-control-allow-headers') || '').includes('X-Admin-Key'));
});
