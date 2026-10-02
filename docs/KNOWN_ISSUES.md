# Known Issues

## MapLibre GL JS `getLayer` internal exception (PARKED, not fixed)

**MAPLIBRE_COMPATIBILITY_STATUS:** `maplibre-gl@3.6.2` is the current stable pinned version for this application.
**MAPLIBRE_4_STATUS:** `maplibre-gl@4.7.1` (previously loaded via a floating, unpinned `@4` CDN tag) is known incompatible/problematic with this application's current configuration. Do not move back to a floating `@4` tag.
**RESIDUAL_STATUS:** Known, unresolved, low-frequency third-party/runtime exception on 3.6.2. Not acceptable, not fixed, parked.

### Symptom

`Uncaught TypeError: Cannot read properties of undefined (reading 'getLayer')`, thrown from inside the maplibre-gl bundle itself (`maplibre-gl.js`, minified, stack collapses to `<anonymous> (:45:60)` because the app's Babel-standalone in-browser transpilation produces unmapped stack frames). Every one of this application's own `getLayer()` call sites is already guarded (`if (!map.getLayer(id)) return` / try-catch) and does not appear in the throwing stack.

### Controlled experiment summary

All counts below are from clean, single-shot console reads (`clear:true` before and after each reload — an earlier round of this investigation was invalidated by an un-cleared console buffer producing bogus numbers like 816; those are void and superseded by the table below).

| Test | Config | Error count (clean load, no interaction) |
|---|---|---|
| Baseline | `maplibre-gl@4.7.1`, full app, CARTO dark-matter style | 99–132 (noisy, always > 0) |
| Swap to a different vector style | @4.7.1 | 99 (unchanged) |
| Swap to a pure raster style (zero vector tiles/symbols) | @4.7.1 | 132 (unchanged) |
| All 16 custom `addLayer` calls disabled | @4.7.1 | 66 (still fires on a near-bare map) |
| + `NavigationControl` removed | @4.7.1 | 99 (still fires) |
| Wait 10s instead of 3s | @4.7.1 | 99 (bounded one-time burst, not a per-frame leak) |
| `trackResize: false` | @4.7.1 | 23 (still fires; resize-observer theory rejected) |
| **Pin to `maplibre-gl@3.6.2`, full app, clean load** | @3.6.2 | **0–14** (dramatically reduced, not fully eliminated) |

### What was ruled out

- Our own application `getLayer()` call sites (all already guarded; none appear in the throwing stack).
- The external base style choice (vector CartoDB dark-matter, a different vector style, and a pure raster style all behave the same).
- Every one of our 16 custom layers/sources (disabling all of them does not eliminate it).
- `NavigationControl`.
- `trackResize` / internal ResizeObserver-driven resize handling.
- A Dashboard component unmount/remount lifecycle leak: a full lifecycle-ownership guard was implemented (`aliveRef` flag flipped synchronously at the top of the map-teardown cleanup, checked by the `'load'`/`'idle'` handlers, the 5s fallback timer, `window._showCellPopup`, `window._setSelectedLocationMarker`, and the CB canvas-overlay animation frame callback; the VOBL `Popup` is now explicitly `.remove()`d on unmount). This is real, correct hardening and should be kept, but it did not change the residual count — confirmed because the residual (12 errors) still occurs on a **first mount with no prior unmount ever having happened**.

### Correlation pass (timestamps vs. network/lifecycle events)

Using `performance.getEntriesByType('resource')` against the exact session where residual errors occurred: the base style's glyph requests (five separate font-family `.pbf` ranges, needed for the vector style's place-name text labels) resolve asynchronously between roughly **+4.9s and +5.9s** after navigation start, in non-deterministic arrival order (sprite.png at +4.7s, then four separate glyph-range fetches landing out of order between +4.9s and +5.9s). The observed residual exceptions cluster inside this exact window. They do not correlate with: the initial `style.json` fetch (+3.8s), the `tiles.json`/`sprite.json` fetch (+4.7s), the `'idle'` event, or any resize/interaction. This points to a race inside maplibre-gl's own internal symbol-placement/collision-index bookkeeping as glyph tiles arrive out of order for the base style's text labels — a maplibre-internal timing issue, not something reachable from this application's component lifecycle or layer configuration.

### Why parked

Going further would require stepping through maplibre-gl's minified bundle with source maps to find the exact internal line, which is a materially larger undertaking than anything justified by a handful of non-fatal console exceptions that do not affect map rendering, interaction, or any visible functionality (verified: Dashboard, hazard pills, VOBL marker/popup, zoom, pan, and tab navigation all work correctly despite these exceptions).

### How to reproduce

1. Load `http://localhost:8731/index.html#dashboard` with devtools console open and the service worker unregistered / caches cleared (`navigator.serviceWorker.getRegistrations()` → unregister each; `caches.keys()` → delete each) to avoid stale-cache false negatives.
2. Wait 5–10 seconds with no interaction.
3. Count `Uncaught TypeError: Cannot read properties of undefined (reading 'getLayer')` in the console. Expect 0–14 on `maplibre-gl@3.6.2`.

### What would be required to investigate further

- Fetch an unminified/sourcemapped build of `maplibre-gl@3.6.2` and reproduce locally with source maps enabled, to get a real stack trace instead of the collapsed `<anonymous>` frame.
- Instrument maplibre's `Placement`/`CollisionIndex` internals (likely candidates given the glyph-timing correlation) with temporary logging, which requires a local maplibre-gl source build rather than the CDN bundle.
- File/search the maplibre-gl GitHub issue tracker for this exact message against 3.6.2 specifically (a 4.7.1-specific search this session found no exact match worth citing).

### Regression protection added

- `maplibre-gl` CDN tags are pinned to the exact version `3.6.2` (both the CSS and JS `<script>`/`<link>` tags) — no floating `@4` or `@3` tag remains anywhere in `index.html`.
- See `scripts/check_maplibre_pin.py` — a lightweight static check (no new test framework) that fails if `index.html` ever references a floating major-version-only maplibre-gl CDN URL instead of an exact `x.y.z` version.

## NOMADS GFS fetch blocked (externally blocked, not a code defect) — corrected scope

Previously documented as a "sandbox-specific" limitation. Re-verified this session directly on the user's own Windows device (not the cloud container): `curl https://nomads.ncep.noaa.gov` and a direct `requests.get()` to the NOMADS filter endpoint both fail with `403 from proxy after CONNECT` / `ProxyError` — this is the device's own egress proxy actively blocking this host, not a cloud-sandbox-only restriction. Correction: this blocks live GFS fetch on the user's real machine too, not just inside Cowork's cloud container. `backend/pipeline.py`'s 992-cell/`forecasts[]` contract cannot be exercised against live data from either environment until `nomads.ncep.noaa.gov` is allowlisted at the network/proxy level (or an alternate GFS mirror is used). The on-disk historical GRIB2 files under `data/external/historical_gfs/raw/` are 2015–2021 training-data snapshots and must not be substituted for a live forecast run (doing so would fabricate "live" data from years-old archives).
