# SIH Implementation Patch — 2026-09-30

Scope: implementation of the 5 approved highest-value priorities from the PS-to-code audit. No commit, no push, no PR created. Everything here is for manual review and commit by Aprameya.

## 1. Files Changed

New files:
- `lead_time.py` — explicit lead-time metadata module (Priority 1)
- `terrain_lookup.py` — DEM/slope/drainage nearest-neighbor lookup + bounded FF modifier (Priority 2)
- `regrid.py` — shared nearest-neighbor/bilinear regridding abstraction (Priority 3)
- `alert_delivery.py` — explicit SUCCESS/FAILED/NOT_CONFIGURED/SKIPPED_NO_ALERT delivery tracking + persisted log (Priority 4)
- `test_lead_time.py`, `test_terrain_lookup.py`, `test_regrid.py`, `test_alert_delivery.py` — new regression tests, all passing

Modified files:
- `forecast_action.py` — added `lead_time` field (via `lead_time.compute_lead_time()`) to every slot's output, in all three code paths (model success, no-model-found climatology fallback, model-load-error fallback)
- `backend/pipeline.py` — wired `terrain_lookup.py` into `hazard_probabilities()`'s call site; added `flash_flood_probability_terrain_adjusted` and `terrain` provenance fields per cell, without changing the existing `flash_flood_probability` field's meaning
- `send_alerts.py` — replaced the boolean `send_whatsapp()` return value with `alert_delivery.send_whatsapp_tracked()`, added `SKIPPED_NO_ALERT` classification, and persists every run to `data/alert_delivery_log.json`
- `test_skill_scores.py` — fixed the 2 failing assertions (test's own `1e-6` tolerance was stricter than `compute_metrics()`'s documented 4-decimal rounding contract; production code was already correct)
- Carried forward from this turn's earlier, not-yet-delivered work: `fetch_metar.py`, `metar_ground_truth.py`, `populate_skill_scores.py` (METAR ground-truth automation and skill-score `INSUFFICIENT_DATA` handling — see prior turn's summary)

## 2. Files Saved to PC

All files above are in this patch folder's `changed_files/` and `tests/` subdirectories, with a unified diff in `diff/diff_tracked_files.patch` for the 5 already-tracked files that were modified in place. See the exact local path given at the end of the chat response.

Workflow YAMLs are included in `workflows/` as corrected copies (protected destination — cannot be written directly). `forecast_update.yml`/`drift_check.yml`/`retrain_trigger.yml` here reflect this turn's carried-forward xgboost/sklearn pin fix from the prior turn; they do not yet include any change for this turn's 5 priorities (none of the 5 priorities required a workflow change).

## 3. Lead-Time Implementation

`lead_time.py::compute_lead_time(date_str, slot, reference_time_utc, gfs_cycle, gfs_fhour)` returns a `LeadTime` dataclass with `hours`, `valid_from`, `valid_to`, `reference_time`, `source_cycle`, `source_fhour`, all derived from real timestamps:
- `reference_time` = the GFS row's `fetched_at_utc` (from `gfs_row_select.GFSSelection`, the same value `forecast_action.py` already uses to select its "latest" row)
- `valid_from`/`valid_to` = the slot's 6-hour IST window, converted to UTC
- `hours` = `valid_from - reference_time`, clipped to 0 (never negative) when the run is late or the slot window has already opened, with `clipped: true` to disclose that explicitly

Wired into all three `slots_output.append(...)` sites in `forecast_action.py` (successful-model path, no-model-found fallback, model-load-error fallback) as a `lead_time` object. 7 tests covering normal forecast, slot boundary, stale/late input, malformed timestamp, missing reference time, unknown slot, and a structural check that all 4 slot windows are ~6 hours — all passing.

## 4. Terrain Implementation

`terrain_lookup.py::TerrainGrid` loads `data/blr_terrain.json` (the existing SRTM-derived Bengaluru/VOBL-only DEM, `fetch_dem_terrain.py`) and does nearest-neighbor lookup with an explicit snap radius (0.02 deg / ~2.2km); reports `available: false` with a stated reason for any pan-India cell outside that bbox, rather than fabricating pan-India terrain coverage. `apply_terrain_to_ff()` applies a documented, bounded 0.70x–1.30x multiplier to flash-flood probability based on the existing `flood_susceptibility` field — terrain never manufactures risk where the model output was 0, and the result is always clipped to [0,1].

Wired into `backend/pipeline.py`'s per-cell loop: `flash_flood_probability` (original model-derived value) is left unchanged for backward compatibility; a new `flash_flood_probability_terrain_adjusted` field and a `terrain` provenance object (elevation, slope, susceptibility, distance, availability, reason) are added per cell. 6 tests (flat/high-susceptibility, steep/low-susceptibility, missing terrain file, outside-bbox, high-precip+high-susceptibility stacking, low-precip+high-susceptibility staying near 0) — all passing.

This remains a heuristic post-processing layer, explicitly documented as such in the module docstring — not a retrained, terrain-aware model.

## 5. Regridding Implementation

`regrid.py::regrid_point()` is a shared nearest-neighbor/bilinear lookup for reconciling a source field (GFS, Himawari, DEM, and — once real data exists — INSAT/IMDAA) against an arbitrary target lat/lon. It never extrapolates outside the source's coordinate range, falls back from bilinear to nearest-neighbor with an explicit `fallback_reason` when neighbors are missing or the source isn't a sorted regular grid, and supports a max-snap-radius rejection. It does not replace the three existing ad hoc lookups already in the codebase (`backend/pipeline.py::lat_lon_to_idx`, Himawari's haversine argmin, `terrain_lookup.py`'s own loop) — that would be a larger refactor than this pass covers — it provides the shared abstraction for future work (regridding GFS/Himawari/DEM onto one common grid, and later INSAT/IMDAA) to build on instead of writing a fourth ad hoc lookup. 8 tests including hand-computed bilinear values (exact midpoint and quarter-point interpolation of a known linear field) — all passing.

## 6. Alert Delivery Implementation

`alert_delivery.py::send_whatsapp_tracked()` replaces the bare-bool return of `send_alerts.py`'s original `send_whatsapp()` with a `DeliveryResult` carrying an explicit status (`SUCCESS` / `FAILED` / `NOT_CONFIGURED`), the actual HTTP status code when one was received, and a human-readable, secret-free reason distinguishing HTTP failure from timeout from missing credentials. `skipped_no_alert()` gives a fourth explicit state, `SKIPPED_NO_ALERT`, for subscribers who correctly received nothing this run (below threshold, no digest due) — never conflated with a failure. `persist_delivery_log()` appends every run to `data/alert_delivery_log.json` (rolling 500-run history, atomic write via temp-file replace), with phone numbers masked to their last 4 digits and API keys never included. `send_alerts.py::main()` now calls this path and prints per-run SUCCESS/FAILED/NOT_CONFIGURED/SKIPPED_NO_ALERT counts. 9 tests (all mocked — no live alerts sent), covering: alert inactive, credentials missing, HTTP 2xx, HTTP 500, timeout, dry-run never touching the network, no secrets/full-phone leaking into the persisted result, log accumulation across multiple runs, and phone masking — all passing.

## 7. Skill-Test Fix

Root cause: `test_skill_scores.py`'s own assertions used a `1e-6` tolerance against the unrounded float (e.g. `2/3 = 0.6666666...`), while `populate_skill_scores.py::compute_metrics()` intentionally rounds every metric to 4 decimal places for the persisted record (documented `r4()` helper). This was a test bug, not a production defect — production's `POD=0.6667` is the correct, documented output. Fixed by asserting against `round(2/3, 4)` etc., with the rationale recorded in the test file itself. All 17 checks now pass.

## 8. Tests Passed/Failed

9/9 test files pass, 0 failures, after this turn's changes:
`test_gfs_row_select.py`, `test_cape_tendency.py`, `test_forecast_log_migrate.py`, `test_metar_ground_truth.py`, `test_skill_scores.py`, `test_lead_time.py`, `test_terrain_lookup.py`, `test_regrid.py`, `test_alert_delivery.py`.

All touched production files pass `ast.parse()` syntax validation: `forecast_action.py`, `compute_realtime_shap.py`, `backend/pipeline.py`, `send_alerts.py`, `populate_skill_scores.py`, `fetch_metar.py`, `metar_ground_truth.py`, plus the 4 new modules.

`backend/pipeline.py` was additionally smoke-loaded as a module (import chain, not the full GFS-fetching `main()`) to confirm `terrain_grid.loaded == True` against the real `data/blr_terrain.json` on disk — confirmed.

Not run this turn: `forecast_action.py`'s full `main()` end-to-end (requires live GFS/model artifacts and network access not available in this audit environment) and `compute_realtime_shap.py`'s full run. Only unit/syntax-level validation was performed for those — this is stated explicitly, not claimed as a live/integration pass.

## 9. Current Map Technical Audit (read-only, no UI changes made)

- Dashboard renders `data/pan_india_grid.json` (the canonical ~15,000-cell grid), fetched directly in `index.html` (`fetch('data/pan_india_grid.json?' + Date.now())`) — confirms the canonical grid, not the 992-cell slotrun grid, is what users see.
- Hazard probabilities are already rendered as **continuous gradient fill layers** (`pan-india-ts`/`pan-india-cb`/`pan-india-ff`, MapLibre `fill` type with `interpolate`/`linear` color ramps), not opaque square cells — this part of the PS's visual expectation is already implemented.
- Terrain **is already represented on the map**: a `raster-dem` hillshade layer (AWS Terrain Tiles, terrarium encoding) shown in Flash Flood mode, plus a canvas-rendered flood-susceptibility overlay built directly from `data/blr_terrain.json`.
- Basemap is CARTO `dark-matter-gl-style` (no API key) — a minimal-label dark basemap; place/road labels exist but are sparse by design, no dedicated river layer beyond what CARTO's basemap includes.
- Location search/drill-down uses OpenStreetMap Nominatim geocoding, scoped to India (`countrycodes=in`).
- Mobile layout is handled via a `window.innerWidth < 640` breakpoint with explicit component hide/reflow rules (left panel hidden, bottom grid switches from 4 to 2 columns).
- Hazard switching and a 4-slot timeline exist in the UI (`selectedSlotIndex`, per-slot opacity toggling, TS/CB/FF layer visibility toggles) but weren't traced to a single named "switching mechanism" function this pass — worth a closer look in the dedicated UX pass.
- Constraint for the future UX pass: the pan-India layer, terrain hillshade, and BBOX-limited canvas overlays (Bengaluru-only, hardcoded `BBOX_W/E/S/N`) are three separately-scoped visual systems (pan-India 1°, Bengaluru DEM 0.01°, canvas raster 512×512) — a future redesign should treat the Bengaluru-local layers explicitly as a "zoomed-in" mode rather than trying to extend them pan-India without new DEM coverage.

## 10. Remaining PS Gaps (unchanged by this pass — none of the 5 priorities addressed these)

IMDAA (no code), INSAT-3D/MOSDAC live data (credential-blocked, unwired downstream), real satellite IWV (currently GFS PWAT relabeled), real QPE (currently GFS APCP relabeled), a trained multi-task/transformer model (architecture exists in `backend/mtl_backbone.py`, never trained — blocked on labeled pan-India data, not compute), and a fully unified spatiotemporal grid (this pass built the abstraction in `regrid.py` but did not migrate the three existing ad hoc lookups onto it).

## 11. Is A100 Work Now Justified?

No, not yet. Nothing in this pass changed the underlying blocker identified in the audit: `mtl_backbone.py` has no labeled pan-India training dataset. An A100 makes training fast once that dataset exists; it doesn't create the dataset, and using it now would not move any PS requirement forward.

## 12. Recommended Next Phase

1. Finish the remaining audit phases not yet executed this turn: CB/FF training-data audit (sample size, positive-class balance, leakage risk) before any retraining decision, and live deployment verification (Cloudflare Pages URL fetch, actual deployed file timestamps).
2. Decide whether to fold DEM/terrain into the FF model's actual training features (not just the post-hoc multiplier added this pass) — requires the training-data audit first.
3. Build a real labeled pan-India spatiotemporal dataset — this is the actual prerequisite for the transformer work, not GPU access.
4. Begin migrating `backend/pipeline.py`'s GFS lookup and Himawari's haversine lookup onto the new `regrid.py` abstraction, so there is one interpolation code path instead of three, ahead of any future INSAT/IMDAA integration.
5. The dedicated UX/map pass, using the "MAP/UX PREPARATION" findings in section 9 above as its starting inventory.
