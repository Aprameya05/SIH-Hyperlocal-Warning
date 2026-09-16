// CSIR Thunderstorm Nowcast — Service Worker
// Version: 2.0.0 — Offline-first with background sync

const CACHE_NAME = 'csir-ts-v2';
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

  // Always network-first for forecast.json — data freshness is critical
  if (url.pathname.endsWith('forecast.json') || url.pathname.endsWith('gfs_multiday_43295.json') || url.pathname.endsWith('himawari_realtime.json')) {
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
