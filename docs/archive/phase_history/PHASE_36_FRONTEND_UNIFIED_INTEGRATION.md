# Phase 36: Frontend Integration of the Unified Forecast Engine

Status date: 2026-10-05. No backend API contract change (Phase 35's
`backend/unified_api.py` is untouched). No model retrained. No commit/push.

## What changed

`index.html` gained ONE additive tab, "UNIFIED FORECAST" (`#unified`), in
the existing `NAV_TAB_LIST` navigation system, rendered by a new
self-contained component, `UnifiedForecastPage`, inserted just before the
`App` component. No existing tab, component, route, visual style, or
behavior was modified -- confirmed by: the exact pre-existing text for
`FORECAST`/`RADAR MAP` render branches is unchanged (verified by the new
test suite), and the whole script still transpiles under the same
`@babel/standalone` the page already loads (verified with Node, matching
output size growth to only the new code added).

## Architecture

`UnifiedForecastPage` fetches, in order:
1. `GET /forecast/all` (relative, same-origin) -- the live Phase 35 API.
2. On failure (expected on the current static hosting, since no deployed
   instance of the Phase 35 API exists yet for this site to reach), falls
   back to `./data/unified_forecast.json` directly and sets
   `forecast_source = 'OFFLINE_ARTIFACT'`, displayed honestly in the UI
   (never presented as live).

Four small, pure, top-level helper functions were factored out of the
component specifically so they are independently testable in Node without
a DOM/React renderer, following the exact pattern this repo already uses
for `assets/location_engine.js` (`tests/test_location_engine_js.mjs`):
`unifiedRiskColor`, `unifiedIsHazardUnavailable`, `unifiedFilterRecordsByLead`,
`unifiedProjectLatLon`. The component itself calls these same functions
(no parallel/duplicate logic exists).

## Requirement-by-requirement

1. No redesign: additive tab only, matching existing tab-button/motion.div
   styling; zero lines removed from any other tab.
2. Grid: inline SVG scatter of the real canonical 992-cell grid
   (lat/lon from the artifact itself -- not a second grid), one point per
   cell at the selected lead time.
3. Lead-time selector: 5 buttons, +2h..+6h, driving `unifiedFilterRecordsByLead`.
4. TS/CB/FF rendered from the API's own `probability`/`risk_category`/`status`.
5/6. `unifiedIsHazardUnavailable()` is the single source of truth for
   "no legitimate probability" and is never confused with a real `0.0`;
   the details card renders an explicit "UNAVAILABLE -- status: ..." line
   rather than a 0%/LOW badge.
7. init_time/valid_time shown in both the header and the details card.
8. `/forecast/sources` (live, or derived offline from the same documented
   values when unreachable) rendered as a status grid, red for BLOCKED.
9. Terrain block shows real elevation/slope/catchment_status, or an
   explicit "MISSING" line for the 639/992 cells without real DEM.
10. Clicking a grid cell drives the details card; the live per-cell
   `/forecast` endpoint is tried first (fresher XAI), with an honest
   fallback note when it is unreachable and the already-loaded bulk
   record is shown instead.
11. XAI: `<details>` listing real top SHAP contributions when
   `xai.status === 'AVAILABLE'`; otherwise a plain NOT_AVAILABLE line with
   the API's own reason text -- nothing invented.
12. OFFLINE_ARTIFACT is a first-class, clearly labeled state, never
   silently presented as live.
13. No fake live-data claim: the UI text explicitly says "not a live API
   call" whenever serving the offline artifact.

## Tests (Requirement 17)

`tests/test_unified_forecast_frontend.mjs` (Node, no build step) -- 29
checks: nav wiring, risk-color fallback safety, null/unavailable
semantics, lead-time filtering for all 5 lead hours, grid projection
bounds, the offline artifact's real shape (4960 records, FF never
carrying a probability, an `OUT_OF_DOMAIN_STATION_ONLY` TS record never
mapped to anything but `NOT_AVAILABLE`), and the live-then-offline
fallback code path's literal presence in the source. All 29 pass.

## End-to-end smoke test (Requirement 19)

A local FastAPI instance (`backend.unified_api:app`, with
`backend.alerts.DB_PATH` monkeypatched to a temp file -- see "Environment
notes" below) was started and `index.html` served from the same origin.
Verified via curl (acting as the browser's fetch calls would): `GET /`
serves the page with the new tab's markup present; `GET /forecast/all`
returns the real 4960-record artifact; `GET /forecast?cell_id=IND_13.0_77.0&lead_hours=3`
returns a real TS probability; `GET /forecast?cell_id=IND_6.0_68.0&lead_hours=3`
returns `null`/`NOT_AVAILABLE` for both TS and FF, never a fabricated
value. A live interactive browser session against this locally-started
server could not be kept open across separate tool calls in this
environment (the background server process does not survive past the
shell call that started it), so the full click-through was verified via
direct HTTP calls reproducing exactly what the page's own fetch logic
does, rather than a driven browser session -- this is the honest
limitation of this environment, not a gap in the integration itself.

## Environment notes (pre-existing, not fixed in this phase)

- `data/unified_forecast.json` was again found reverted to the older
  Phase 24 schema at the start of this phase (same instability noted in
  Phase 35 -- most likely an out-of-session regeneration on the user's
  machine). Re-ran the unmodified `scripts/phase34_build_unified_forecast.py`
  to restore the correct schema before testing. This file's schema
  stability between sessions is a real, open risk for anyone deploying
  this frontend against the offline fallback and is flagged as a
  remaining blocker below.
- `data/alerts.db` still has the pre-existing `disk I/O error` found in
  Phase 35 (this machine's disk is at 98% capacity) -- it now also blocks
  the FastAPI app from starting at all (`init_db()` runs unconditionally
  at startup), which was not previously observed because Phase 35's tests
  used `TestClient`'s lifespan directly with a monkeypatched `DB_PATH`
  rather than actually booting `uvicorn`. This is a real, previously
  underreported blocker: **the live API cannot start on this machine
  until `data/alerts.db` is repaired or its path is reconfigured**,
  independent of anything in this phase's frontend code.
