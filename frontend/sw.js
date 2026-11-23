// CSIR Thunderstorm Nowcast — Service Worker
// Version: 2.0.0 — Offline-first with background sync

const CACHE_NAME = 'drift-v6';   // bumped: index.html is now the modern dashboard; old clients must drop the cached legacy page
const FORECAST_URL = './forecast.json';
const DATA_URLS = [
  './',
  './index.html',
  './manifest.json',
  './forecast.json',
  'https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@500;600;700;800;900&display=swap',
];

// ── Install: pre-cache shell ──────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(DATA_URLS).catch(() => {
        // Non-fatal: cache what we can
      });
    }).then(() => self.skipWaiting())
  );
});

// ── Activate: clean old caches ────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      const stale = keys.filter(k => k !== CACHE_NAME);
      return Promise.all(stale.map(k => caches.delete(k))).then(() => stale.length > 0);
    }).then(hadOldCache => self.clients.claim().then(() => {
      // A returning visitor whose previous worker/caches were replaced may be looking at a page served from the old cache:
      // reload open tabs once so they show the current deployment (runs once per worker version, so no reload loop).
      if (hadOldCache) {
        return self.clients.matchAll({ type: 'window' }).then(cs => cs.forEach(c => { try { c.navigate(c.url); } catch (e) { /* ignore */ } }));
      }
    }))
  );
});

// ── Fetch: network-first for forecast, cache-first for assets ─────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Always network-first for forecast.json — data freshness is critical
  // Data feeds and the command-centre page/scripts are network-first: a cached copy must never be shown as current.
  // Every same-origin page, script, stylesheet and data file is network-first: a cached copy must never be shown as current, and a
  // new drift_app.js must never run against a cached older drift_location.js. Only fonts and images stay cache-first.
  const networkFirst = url.origin === self.location.origin && (url.pathname === '/' || url.pathname.includes('/data/') || /\.(html|js|css|json|geojson)$/.test(url.pathname));
  if (networkFirst || url.pathname.endsWith('forecast.json') || url.pathname.endsWith('gfs_multiday_43295.json') || url.pathname.endsWith('himawari_realtime.json')) {
    event.respondWith(
      fetch(event.request, { cache: 'no-cache' })
        .then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => caches.match(event.request).then(r => r || offlineForecastResponse()))
    );
    return;
  }

  // Cache-first for everything else (fonts, static assets)
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        if (response.ok && event.request.method === 'GET') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      }).catch(() => {
        // For navigation requests, serve the app shell
        if (event.request.mode === 'navigate') {
          return caches.match('./index.html');
        }
      });
    })
  );
});

// ── Offline forecast placeholder ──────────────────────────────────
function offlineForecastResponse() {
  const now = new Date();
  const body = JSON.stringify({
    generated_at_utc: now.toISOString(),
    generated_at_ist: 'OFFLINE — cached data',
    pipeline_status: 'OFFLINE',
    alert_active: false,
    peak_slot: null,
    peak_probability: 0.0,
    slots: [],
    offline: true,
    note: 'No network connection. Showing cached forecast. Reconnect to refresh.'
  });
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'application/json' }
  });
}

// ── Background sync: refresh forecast when back online ───────────
self.addEventListener('sync', event => {
  if (event.tag === 'refresh-forecast') {
    event.waitUntil(
      fetch(FORECAST_URL, { cache: 'no-cache' })
        .then(r => {
          if (r.ok) {
            return caches.open(CACHE_NAME).then(cache => cache.put(FORECAST_URL, r));
          }
        })
        .then(() => {
          self.clients.matchAll().then(clients =>
            clients.forEach(c => c.postMessage({ type: 'FORECAST_UPDATED' }))
          );
        })
    );
  }
});

// ── Push notifications ────────────────────────────────────────────
self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || 'DRIFT notification';
  const options = {
    body: data.body || 'Open DRIFT for details. Not an official IMD/NDMA warning.',
    icon: './manifest.json',
    badge: './manifest.json',
    tag: 'drift-notice',
    requireInteraction: true,
    data: { url: data.url || '/' },
    actions: [
      { action: 'view', title: 'View Forecast' },
      { action: 'dismiss', title: 'Dismiss' }
    ]
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  if (event.action !== 'dismiss') {
    event.waitUntil(
      clients.openWindow(event.notification.data.url || '/')
    );
  }
});
