# DRIFT — Pan-India Production Baseline (Phase 0 Audit) — 2026-09-30

Read-only inspection. Nothing in production was changed to produce this document.

## A. Current Production Inference Path

`forecast_action.py` (VOBL/station-level, runs on schedule via `forecast_update.yml`) and `backend/pipeline.py` (pan-India 992-cell grid, runs on schedule via `update_grid.yml`) are two separate, independently-scheduled inference paths writing two separate outputs: `forecast.json` (station) and `data/pan_india_grid.json` (grid). They are not the same pipeline and do not share a model.

## B. Current 992-Cell Grid Definition

`backend/pipeline.py`: `BOUNDS = {"S": 6, "N": 37, "W": 68, "E": 98}` (mainland India bounding box), `GRID_STEP = 0.25` degrees (~27km, matches native GFS resolution — this is a deliberate choice, not an approximation error). Cells generated via `np.arange` over lat/lon at that step; `n_cells` recorded per-run in the output file's own metadata (992 as of the last verified run). No separate elevation/slope/drainage/land-sea-mask fields exist per cell today — terrain is looked up separately (see E) and only for the Bengaluru subregion.

## C. Current GFS Ingestion

NOAA NOMADS, fetched by `backend/pipeline.py`, request built from `BOUNDS` above. Runs on the `update_grid.yml` schedule (04:30/10:30/16:30/22:30 UTC, right after each GFS cycle posts). Variables: CAPE, CIN, PWAT, K-index, Totals Totals, winds at 850/500hPa, APCP (precip), T2M. This is the only pan-India-wide live data source currently in production.

## D. Current Himawari Ingestion

JMA Himawari-9, `~50km` crop centered on VOBL only (not pan-India). Feeds `data/himawari_realtime.json`, consumed by `forecast_action.py` (station-level) and by `location_engine.py`'s `himawari` bundle section (availability-gated to the 50km radius). Not part of `backend/pipeline.py`'s pan-India grid computation.

## E. Current DEM/Terrain Ingestion

`terrain_lookup.py` + `data/blr_terrain.json` — SRTM-derived, **Bengaluru bounding box only**, 0.01° resolution. Wired into `backend/pipeline.py`'s per-cell loop (added two passes ago) but only produces non-null values for cells inside the Bengaluru terrain file's bounds; every other cell in the 992-cell grid gets `terrain: null` today, confirmed against the live on-disk `pan_india_grid.json` in an earlier pass of this session.

## F. Current VOBL XGBoost Path

`forecast_action.py`, per-slot (0-3) joblib artifacts under `models/nowcast_slot{0-3}_xgb_v6_temporal.pkl` (each self-describing: embeds `model`, `feature_cols`, `threshold`, `auroc`). Station-level only (single point, VOBL/IMD 43295), not gridded.

## G. Current CB/FF Models

Two genuinely different things exist and must not be conflated:
- **Station-level (VOBL)**: real trained XGBoost classifiers + isotonic calibrators per slot, `models/{cb,ff}_slot_{0-3}_model.json` + `_calibrator.pkl`, feature list `models/cb_ff_feature_list.json`. Verified loadable and scoreable in the prior pass of this session (real AUROC/CSI/HSS numbers obtained on 2024-2025 held-out VOBL data).
- **Pan-India (992-cell grid)**: `backend/pipeline.py::hazard_probabilities()` — a **physics-based proxy**, not a trained classifier. Computes CB/FF probability from CAPE/K-index/PWAT/terrain via documented heuristic thresholds, not from any ML model fit to pan-India labels (none exist — confirmed in the prior data audit).

## H. Current SHAP Path

`compute_realtime_shap.py` — VOBL station-level only, computed against the production TS slot XGBoost artifacts, written to `data/realtime_shap.json`, read (not recomputed) by `forecast_action.py`. No SHAP or other explainability exists for the pan-India grid or for CB/FF at any granularity.

## I. Current Alert Path

`send_alerts.py`, using `alert_delivery.py` (added two passes ago): `send_whatsapp_tracked()` returns a real `DeliveryResult` (SENT/FAILED/SKIPPED, not a bare boolean), `persist_delivery_log()` writes `data/alert_delivery_log.json`. Station-level (VOBL) only — no pan-India cell has ever triggered an alert; the alert path is not wired to `backend/pipeline.py`'s output.

## J. Current Location-Aware Frontend

`index.html` + `assets/location_engine.js` (added in the two most recent passes): canonical `activeLocation` React state, map click / search / device-geolocation all wired to `DriftLocationEngine.buildLocationBundle()`, a 5-way architecture selector (Pan-India / VOBL / Terrain / Himawari / GFS) with VOBL gated by `in_domain`. Map visual redesign and forecast-card location-awareness are explicitly **not** done (documented in the pass-2 report as deferred).

## K. Current PWA

`manifest.json` (real icons/branding, standalone display) and `sw.js` (network-first for live data including `pan_india_grid.json`/`blr_terrain.json` since the pass-2 fix, cache-first for static assets, explicit `offlineForecastResponse()` marker) — both pre-existing and functional, not rebuilt by any pass in this session.

## L. Current Deployment Workflow

Five GitHub Actions workflows: `forecast_update.yml` (station forecast + frontend deploy via `wrangler pages deploy . --project-name=sih-hyperlocal-warning`), `update_grid.yml` (pan-India grid, 4x/day on GFS cycle schedule), `drift_check.yml` (deploys to a **differently-named** Cloudflare project `csir-thunderstorm-bengaluru` — flagged as a pre-existing naming inconsistency in an earlier pass, not fixed, not in scope here), `retrain_trigger.yml`, `weekly_digest.yml`.

## M. Current Forecast Metadata/Provenance

Station-level (`forecast_action.py`): each slot output carries `model_used`, `model_version`, `source`, `lead_time` (added in an earlier pass via `lead_time.py`), `fallback`/`fallback_reason` when no model artifact is found. Pan-India (`pan_india_grid.json`): top-level `generated_at_utc`, `gfs_cycle`, `gfs_fhour`, `grid_step_deg`, `bounds`, `n_cells` — but **no per-cell provenance** (no per-cell source timestamp, no per-cell missing-data flag). This is a real gap Phase 3/5 of the current request would need to close.

## N. Current Tests

15 test files at repo root: `test_alert_delivery.py`, `test_analogs.py`, `test_cape_tendency.py`, `test_forecast_log_migrate.py`, `test_gfs_row_select.py`, `test_himawari.py`, `test_lead_time.py`, `test_location_engine.py`, `test_metar_ground_truth.py`, `test_nomads.py`, `test_regrid.py`, `test_segments.py`, `test_segments_v2.py`, `test_skill_scores.py`, `test_terrain_lookup.py`. No end-to-end test exists that exercises ingestion → grid → labels → model → API → frontend as one path — each test covers one module in isolation.

## Summary — What "Pan-India" Actually Means Today

Only three things are genuinely pan-India right now: the 992-cell grid definition itself, GFS ingestion, and the physics-proxy CB/FF/TS hazard scoring on that grid. Everything else that is real and validated (trained CB/FF/TS classifiers, SHAP, terrain, Himawari, alerts) is VOBL/Bengaluru-only. This is the honest starting line for the rest of this master pass.
