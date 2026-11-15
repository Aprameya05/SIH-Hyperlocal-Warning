# DRIFT / SIH-Hyperlocal-Warning — Final Frontend Wiring Pass — 2026-09-30

## 1. Exact Active Frontend File

Verified, not assumed: `find . -maxdepth 1 -iname "index*.html"` returns only `index.html` — no `index-1.html` or archived variant exists in the repo. `.github/workflows/forecast_update.yml` deploys with `wrangler pages deploy . --project-name=sih-hyperlocal-warning`, i.e. the whole repo root, so `index.html` is confirmed to be the file actually served in production. This is the file modified below. (Side note, not touched: `.github/workflows/drift_check.yml` still deploys to a differently-named Cloudflare project, `csir-thunderstorm-bengaluru` — a pre-existing inconsistency, flagged but out of scope for this pass.)

## 2. Exact Lines/Hooks Modified

All hook points named in the prior pass's report were re-opened and verified against the current file (not assumed from stale line numbers) before editing:
- Script loading (`<head>`, ~line 86): added `<script src="./assets/location_engine.js"></script>` as a plain script before the Babel block, so `window.DriftLocationEngine` exists before the React app runs.
- `DashboardOverviewPage` component state block (~line 2097): added `gridMetaRef`, `activeLocation` state, `selectedArchitecture` state, `geoStatus` state, `setActiveLocationFromCoords()`, a `liveData`-driven refresh effect, and `useMyLocation()`.
- Pan-India grid fetch handler (~line 2718, `.then(d => { ... gridCellsRef.current = d.grid_cells ...})`): now also populates `gridMetaRef.current` and triggers one initial `setActiveLocationFromCoords` call once the grid has actually loaded.
- `vobl-clickzone` click handler (~line 2246): now also calls `setActiveLocationFromCoords(VOBL.lat, VOBL.lng, ..., 'MAP')`.
- Generic map click handler (~line 2329, the one already doing nearest-cell popup lookup): now also calls `setActiveLocationFromCoords(lat, lng, null, 'MAP')` immediately after the existing `showCellPopup()` call — the existing popup behavior is untouched, this is additive.
- New `selected-location-src`/`selected-location-ring`/`selected-location-dot` map source/layers (added right after the existing `vobl-src`/`window._showCellPopup` block) plus a `window._setSelectedLocationMarker()` helper and a React effect that calls it whenever `activeLocation` changes.
- `handleSearch()` (~line 2921): now calls `setActiveLocationFromCoords(latN, lngN, shortName, 'SEARCH')` immediately after the existing `flyTo()`, before the existing popup-retry logic. The two existing early-return paths (no geocode results; caught exception) are unchanged, so search failure already preserved the previous location before this pass and still does.
- New "Use my location" button + status messages, and a new "Selected Location + Multi-Architecture" panel, inserted as two new sibling blocks right after the existing search-box JSX (before the existing gradient-vignette divs) — fully additive, no existing JSX removed.
- Mobile peak chip (~line 3211): added one new compact location bar above the existing chip (unmodified), showing `activeLocation.displayName` and a "Locate" button.
- `sw.js`: unchanged from the prior pass's fix (`pan_india_grid.json`/`blr_terrain.json` in the network-first live-data list) — re-verified still present and correct, not touched further.

## 3. `activeLocation` Implementation

One `React.useState` object: `{latitude, longitude, displayName, selectionSource, selectedGridCell, architectureResults, timestamp, freshness}`. `selectionSource` is one of `'DEFAULT' | 'DEVICE' | 'MAP' | 'SEARCH'`. Every panel described below reads from this single state via `activeLocation.*` — no panel keeps an independent copy. The only computation is `setActiveLocationFromCoords()`, which calls `window.DriftLocationEngine.buildLocationBundle()` (the already-tested engine from the prior pass) and nothing else — no probability math happens in `index.html` itself.

## 4. Map-Click Implementation

The existing click handler (which already did its own nearest-cell popup lookup) was extended, not replaced: after its existing `showCellPopup()` call, it now also calls `setActiveLocationFromCoords(lat, lng, null, 'MAP')`. This reuses the exact same tested engine the popup itself doesn't use (the popup's own nearest-cell math is a separate, pre-existing, slightly different heuristic with CB/FF proxy fallbacks for zero-valued cells — left untouched per "don't rewrite what already works"). A new `selected-location-dot`/`selected-location-ring` map layer, driven by a `window._setSelectedLocationMarker()` helper, makes the active point visually distinct from the permanent VOBL marker — visible only once the user has made an explicit selection (`selectionSource !== 'DEFAULT'`).

## 5. Device-Location Implementation

`useMyLocation()` calls `navigator.geolocation.getCurrentPosition()` exactly once per button press (never `watchPosition`, never polled), with `enableHighAccuracy: false, timeout: 10000, maximumAge: 300000`. On success: `setActiveLocationFromCoords(lat, lon, 'My Location', 'DEVICE')` + `map.flyTo()`. On failure: `geoStatus` is set to `'denied'` (permission) or `'unavailable'` (timeout/position-unavailable), each rendering a short inline message; the rest of the dashboard is untouched either way. No coordinate is ever sent anywhere — this is a pure client-side lookup against already-loaded JSON.

## 6. Search Implementation

The existing Nominatim-based `handleSearch()` was extended in place (not duplicated): the one new line calling `setActiveLocationFromCoords()` sits right after the existing `flyTo()`. Both of the function's existing failure paths (empty geocode result; caught fetch exception) return/fall through before that line is reached, so a failed search already left the previous `activeLocation` untouched before this pass, and still does — verified by reading the control flow, not assumed.

## 7. Architecture Selector

Five buttons — PAN-INDIA / VOBL XGBOOST / TERRAIN / HIMAWARI / GFS — in the new Selected Location panel, backed by `selectedArchitecture` state. Each renders only its own real data from `activeLocation.architectureResults`; there is no combined score, no averaging, no ensemble anywhere in this code. An unavailable architecture renders `{reason}` in italic muted text (e.g. `"nearest pan-India cell is 2.10 deg away -- outside grid coverage"`, `"153.1 km from IMD VOBL 43295 -- outside the 25 km station representativeness radius..."`) — the exact honest strings the engine itself produces, not paraphrased.

## 8. Map-Layer Implementation

Not changed this pass. The existing `panLayer` state (thunderstorm/cloudburst/flash_flood fill-layer toggle) and the existing canvas-based hazard overlays were already real, working, gradient-based visualizations from before this pass (confirmed in the prior pass's map audit) — they were left alone. Extending the map's fill layers to also expose a "terrain-adjusted FF" and "Himawari CTT" toggle, and the broader visual/basemap improvement (§18 of the request — roads, water, labels, hillshade prominence), was not attempted this pass: it's a real visual-design change to a map I cannot render, and a wrong basemap swap risks silently breaking the existing fallback/retry/cellular behavior the request explicitly said to preserve. Flagged as the top remaining item, not silently dropped.

## 9. Forecast-Card Integration

Not modified this pass. The existing bottom forecast-slot cards remain driven by the original `liveData`/`slots` props (VOBL-only), unchanged. The new Selected Location panel is additive and sits alongside them, showing the location-aware pan-India/VOBL/terrain/Himawari/GFS data independently — it does not yet feed back into the existing slot cards' own rendering. Making the *existing* cards themselves become location-aware (rather than adding a new panel beside them) is real, further UI-restructuring work not done this pass.

## 10. VOBL Isolation

`R.vobl.in_domain` (from the tested engine, 25km radius) gates everything: in-domain shows `"IMD VOBL STATION 43295 — station-specific forecast"` with the real peak probability/slot/timestamp; out-of-domain shows `"VOBL XGBoost — station-specific — NOT APPLICABLE TO SELECTED LOCATION"` plus the engine's own distance-based reason string. At no point is a VOBL number rendered without one of these two explicit labels — verified by reading the one `renderArchitecture()` branch that handles it, there is no other code path that touches `R.vobl`.

## 11. Terrain Behavior

Reads `R.pan_india.terrain` (nested inside the pan-India cell result, from the prior pass's `terrain_lookup.py` integration). If `null`/`unavailable` (true for every cell on the current stale on-disk grid file, verified in the prior pass), renders "Terrain data unavailable for selected cell" — no fabricated elevation/slope value is ever shown. Shows both `FF BASE` and `FF TERRAIN-ADJUSTED` side by side when available, matching the request's explicit "show both" instruction.

## 12. Himawari Behavior

Reads `R.himawari.available` (the engine's real ~50km-radius check around VOBL). Available: shows CTT, trend, and the real timestamp, labeled `"HIMAWARI-9 (fetch_himawari_realtime.py) -- NOT INSAT"` verbatim from the engine. Unavailable: shows the engine's own distance-based reason. Never calls it INSAT anywhere in this new code.

## 13. GFS Behavior

Reads `R.gfs.available`/fields straight from the matched pan-India cell (no separate fetch). Field is literally named `pwat_mm` and labeled `"GFS (NOAA NOMADS, backend/pipeline.py)"` — never "IWV" anywhere in this new code.

## 14. PWA Compatibility

No PWA files were rebuilt. `sw.js`'s prior-pass fix (network-first for `pan_india_grid.json`/`blr_terrain.json`) was re-verified present and untouched. The new `assets/location_engine.js` script tag was added to `sw.js`'s static-asset precache list (`DATA_URLS`) in the *prior* pass already — re-confirmed still there — so it's available offline like the rest of the app shell.

## 15. Tests

- All 10 existing Python test suites re-run after this pass's edits: `test_location_engine.py`, `test_lead_time.py`, `test_terrain_lookup.py`, `test_regrid.py`, `test_alert_delivery.py`, `test_skill_scores.py`, `test_metar_ground_truth.py`, `test_gfs_row_select.py`, `test_cape_tendency.py`, `test_forecast_log_migrate.py` — **all passing, 0 failures**.
- `node --check sw.js` and `node --check assets/location_engine.js` — both clean.
- The entire modified `index.html` inline script was extracted and run through a real `@babel/core` + `@babel/preset-react` parse (installed fresh in this sandbox specifically for this pass) after every edit, not just at the end — **parses clean**, meaning the JSX is syntactically valid React, which a plain grep/read-through cannot confirm on a 9,600-line file.
- Manually traced (not executed, since no browser exists here) every new code path against the actual control flow: search failure preserves prior location, geolocation denial doesn't touch dashboard state, VOBL out-of-domain never renders a bare probability, terrain/Himawari/GFS unavailability all render their real `reason` strings.
- Scanned the diff itself for newly introduced hardcoded coordinates/probabilities — none found; every VOBL-coordinate reference in the diff is either a comment, the pre-existing `VOBL` constant, or a labeled VOBL-domain UI string.

## 16. Browser Limitations

**BROWSER TESTING NOT PERFORMED.** This environment has no browser, no DOM, no way to render `index.html` or click anything in it. Every claim above is backed by a real automated check (Python tests, Node syntax checks, a real Babel/JSX parse, and manual code-path tracing) — none of that is a substitute for actually opening the page. Screenshots were not captured because no rendering environment exists to capture them from; `screenshots/` in this patch folder contains only a note explaining why.

## 17. Remaining Limitations

- Map visual redesign (basemap brightness, roads/water/labels, hillshade prominence, per-architecture map fill layers beyond the existing TS/CB/FF toggle) — not attempted, flagged as the top remaining item.
- Existing bottom forecast-slot cards are not yet location-aware; only the new side panel is.
- The "WHY THIS PREDICTION" explanation panel (VOBL SHAP / pan-India physical inputs / terrain adjustment / Himawari context, per-architecture) was not added this pass.
- Full mobile information-hierarchy redesign (expandable technical sections, etc.) not attempted — only the two targeted mobile additions described above (location bar, existing chip untouched).
- No live deployment fetch/Lighthouse/installability check was performed (no browser).

## Manual Test Checklist (run this on your PC)

1. **Open DRIFT** → confirm it opens at the default VOBL/Bengaluru view with the Selected Location panel showing "VOBL / Bengaluru Airport" / SOURCE: DEFAULT.
2. **Click another point on the map** (e.g. somewhere near Mysuru or Chennai) → confirm the Selected Location panel's name/coordinates/SOURCE change to MAP, a yellow marker appears at the clicked point, and switching the architecture selector to PAN-INDIA shows that cell's real TS/CB/FF numbers (different from VOBL's).
3. **Search "Mysuru"** → confirm the map flies there, the Selected Location panel updates to SOURCE: SEARCH, and switching to VOBL XGBOOST shows "NOT APPLICABLE TO SELECTED LOCATION" rather than a number.
4. **Click "Use my location"** → grant permission → confirm SOURCE: DEVICE and the panel updates to your real coordinates; deny permission on a second try → confirm the app doesn't break and shows the denial message.
5. **Switch through PAN-INDIA / VOBL / TERRAIN / HIMAWARI / GFS** at a couple of different selected locations → confirm each shows either real data or an explicit "unavailable" reason, never a blank or fabricated value.
6. **Search something invalid** (e.g. "xyzzyplace123") → confirm the previously selected location is NOT lost.
7. **Resize to a phone width / open on an actual phone** → confirm the new location bar above the mobile peak chip is legible and the "Locate" button is tappable.
8. Everything else already in the app (hazard TS/CB/FF map toggle, existing VOBL popup on tapping the airport marker, PWA install/offline behavior) should behave exactly as before this pass — nothing there was intentionally changed.
