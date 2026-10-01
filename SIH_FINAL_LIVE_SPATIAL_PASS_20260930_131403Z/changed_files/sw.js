// CSIR Thunderstorm Nowcast (DRIFT) — Service Worker
// Version: 2.1.0 — Offline-first with background sync, location-aware live data

const CACHE_NAME = 'csir-ts-v2';
const FORECAST_URL = './forecast.json';
const DATA_URLS = [
  './',
  './index.html',
  './manifest.json',
  './forecast.json',
  './assets/location_engine.js',
  'https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@500;600;700;800;900&display=swap',
];

// LIVE DATA — always network-first: freshness matters more than offline
// availability for these. This list previously only covered the VOBL
// forecast; the location-aware pass added pan_india_grid.json (the source
// every non-VOBL location's prediction is read from) and blr_terrain.json,
// so a map click on a new location during a flaky connection still gets
// the freshest data it can, with the same offline-fallback behavior below.
const LIVE_DATA_SUFFIXES = [
  'forecast.json',
  'gfs_multiday_43295.json',
  'himawari_realtime.json',
  'pan_india_grid.json',
  'blr_terrain.json',
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
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== CACHE_NAME)
          .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: network-first for forecast, cache-first for assets ─────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Always network-first for live forecast/grid/terrain/satellite data —
  // data freshness is critical, and (since the location-aware pass) this
  // now also covers the pan-India grid and terrain files a map click reads.
  if (LIVE_DATA_SUFFIXES.some(suffix => url.pathname.endsWith(suffix))) {
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
  const title = data.title || '⚡ CSIR Thunderstorm Alert — VOBL';
  const options = {
    body: data.body || 'Thunderstorm probability elevated. Check dashboard.',
    icon: './manifest.json',
    badge: './manifest.json',
    tag: 'ts-alert',
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
