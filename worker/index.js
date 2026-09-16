/**
 * CSIR Thunderstorm Alert — Cloudflare Worker
 *
 * Endpoints:
 *   POST /subscribe        — add a subscriber { name, email, phone, whatsapp_apikey, threshold, daily_digest }
 *   GET  /unsubscribe?token=xxx  — one-click unsubscribe from email links
 *   POST /unsubscribe      — programmatic unsubscribe { token }
 *   GET  /subscribers      — list all subscribers (requires X-Admin-Key header)
 *   GET  /health           — uptime check
 *
 * KV namespace: SUBSCRIBERS
 *   Key format:  sub:<token>
 *   Value:       JSON subscriber object
 *
 * Env vars (set in Cloudflare dashboard → Worker → Settings → Variables):
 *   ADMIN_KEY     — secret for /subscribers endpoint (used by GitHub Actions)
 *   TOKEN_SECRET  — salt for generating unsubscribe tokens
 */

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-Admin-Key',
};

// ── Token ──────────────────────────────────────────────────────────────────

async function makeToken(email, secret) {
  const data = new TextEncoder().encode(email + '|' + secret);
  const hashBuf = await crypto.subtle.digest('SHA-256', data);
  const hashArr = Array.from(new Uint8Array(hashBuf));
  return hashArr.map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
}

// ── Helpers ────────────────────────────────────────────────────────────────

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS_HEADERS },
  });
}

function html(body, status = 200) {
  return new Response(body, {
    status,
    headers: { 'Content-Type': 'text/html;charset=UTF-8', ...CORS_HEADERS },
  });
}

// ── Route handlers ─────────────────────────────────────────────────────────

async function handleSubscribe(request, env) {
  let body;
  try { body = await request.json(); } catch {
    return json({ error: 'Invalid JSON body' }, 400);
  }

  const email = (body.email || '').trim().toLowerCase();
  const name  = (body.name  || '').trim() || 'Researcher';
  const phone = (body.phone || '').trim();
  const wa_key = (body.whatsapp_apikey || '').trim();
  const threshold = Math.min(Math.max(Number(body.threshold) || 30, 5), 95);
  const daily_digest = body.daily_digest !== false;

  if (!email && !phone) {
    return json({ error: 'Provide at least an email or phone number.' }, 400);
  }
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return json({ error: 'Invalid email address.' }, 400);
  }

  const token = await makeToken((email || phone), env.TOKEN_SECRET || 'csir-ts-default');
  const key   = `sub:${token}`;

  // Check for duplicate
  const existing = await env.SUBSCRIBERS.get(key);
  if (existing) {
    return json({ ok: true, message: 'Already subscribed. Settings updated.', token });
  }

  const record = {
    token,
    name,
    email,
    phone,
    whatsapp_apikey: wa_key,
    threshold,
    daily_digest,
    subscribed_at: new Date().toISOString(),
  };

  await env.SUBSCRIBERS.put(key, JSON.stringify(record));
  return json({ ok: true, message: `Subscribed! You'll receive alerts when thunderstorm probability exceeds ${threshold}%.`, token });
}

async function handleUnsubscribe(request, env) {
  const url   = new URL(request.url);
  const token = url.searchParams.get('token') ||
    (request.method === 'POST' ? (await request.json().catch(() => ({}))).token : null);

  if (!token) {
    return json({ error: 'Missing token.' }, 400);
  }

  const key   = `sub:${token}`;
  const value = await env.SUBSCRIBERS.get(key);
  if (!value) {
    return html(`<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Unsubscribe — CSIR TS</title>
<style>body{font-family:Inter,sans-serif;background:#040711;color:#cfd8dc;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
.card{background:#0d1117;border:1px solid rgba(56,189,248,0.15);border-radius:16px;padding:40px;max-width:400px;text-align:center;}
h2{color:#38bdf8;} p{color:#607d8b;font-size:13px;}</style></head>
<body><div class="card"><h2>Already Unsubscribed</h2><p>This link has already been used or the subscription was not found. You will not receive further alerts.</p></div></body></html>`);
  }

  const sub = JSON.parse(value);
  await env.SUBSCRIBERS.delete(key);

  return html(`<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Unsubscribed — CSIR TS</title>
<style>body{font-family:Inter,sans-serif;background:#040711;color:#cfd8dc;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
.card{background:#0d1117;border:1px solid rgba(76,175,80,0.2);border-radius:16px;padding:40px;max-width:400px;text-align:center;}
h2{color:#4caf50;} p{color:#607d8b;font-size:13px;} a{color:#38bdf8;}</style></head>
<body><div class="card">
  <div style="font-size:48px;margin-bottom:16px;">✅</div>
  <h2>Unsubscribed Successfully</h2>
  <p>${sub.name || 'You'} (${sub.email || sub.phone}) will no longer receive CSIR Thunderstorm alerts.</p>
  <p style="margin-top:20px;"><a href="https://csir-thunderstorm-bengaluru.pages.dev">← Back to Dashboard</a></p>
</div></body></html>`);
}

async function handleListSubscribers(request, env) {
  const adminKey = request.headers.get('X-Admin-Key') || '';
  if (!env.ADMIN_KEY || adminKey !== env.ADMIN_KEY) {
    return json({ error: 'Unauthorized' }, 401);
  }

  const list = await env.SUBSCRIBERS.list({ prefix: 'sub:' });
  const subscribers = [];
  for (const key of list.keys) {
    const val = await env.SUBSCRIBERS.get(key.name);
    if (val) {
      try { subscribers.push(JSON.parse(val)); } catch {}
    }
  }
  return json({ ok: true, count: subscribers.length, subscribers });
}

// ── Main handler ───────────────────────────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS_HEADERS });
    }

    if (path === '/health' || path === '/') {
      return json({ ok: true, service: 'CSIR Thunderstorm Alert Worker', ts: new Date().toISOString() });
    }

    if (path === '/subscribe' && request.method === 'POST') {
      return handleSubscribe(request, env);
    }

    if (path === '/unsubscribe') {
      return handleUnsubscribe(request, env);
    }

    if (path === '/subscribers' && request.method === 'GET') {
      return handleListSubscribers(request, env);
    }

    return json({ error: 'Not found' }, 404);
  },
};
