# Dashboard implementation matrix (what each indicator is backed by, and what is missing)

Written 2026-11-23 after PRs #2-#27. There are two dashboards: the modern command centre (`drift.html`, the site root once the next
deploy lands) and the legacy Bengaluru station dashboard (`legacy.html`), which is the one in the reviewed screenshot. Code-level behaviour described as 'withheld' reaches the live site when `forecast.json` is next regenerated and deployed by the scheduled forecast run. Statuses are
verified against code, artifacts and tests, not inferred from the UI. "Available" means the value reaches the screen from a named
source; it does not mean the underlying model is validated for that purpose.

## A. Hazard cards

| Component | Source today | Status | Gap | What it takes (see section F) |
|---|---|---|---|---|
| Thunderstorm, station (VOBL, legacy cards) | `forecast_action.py`. **Slot 3 (18-24 IST): `v7_live_inputs`**, trained only on inputs with a live value, passed a pre-set gate on label-valid test years 2022-2023 (AUROC 0.897 [0.860, 0.928]; Brier skill vs slot-month climatology +0.176 [+0.088, +0.251]; POD 0.54, FAR 0.56, CSI 0.32, HSS 0.43). Slots 0-2: v6, **28 of 64 inputs are not real live values** (20 station observations + 8 never computed, silently 0.0 until PR #25) | Slot 3: **available** (calibrated probability, real SHAP drivers). Slots 0-2: **withheld** (`fallback`, dash + WITHHELD, never 0%; PRs #7, #21, #25) | slots 0-2 failed the same gate (slot 2: AUROC 0.81 but no Brier skill); 2019 and 2024-25 (slots 0, 1, 3) station labels are missing, not negative, and were excluded; train/serve skew (ERA5-like training predictors vs GFS) unquantified; no live verification | WP4 for slots 0-2: more label-valid years or a different predictor set |
| Thunderstorm, pan-India (modern dashboard) | `models/thunderstorm` XGBoost on 24 GFS predictors, calibrated, TreeSHAP | **Available in season only** (Apr-Jun cycles; `unavailable` otherwise, PR #15); validated on 113 METAR stations, Apr-Jun 2024/2025 | no live verification; one season; leads are NWP hours (+6/12/18/24), not 2-6 h warnings | WP1 + WP4 |
| Cloudburst | none | **Unavailable** by design | no cloudburst definition or labels in the repo; the only related output is a daily heavy-rain-day score (>= 64.5 mm/24 h, 10 training dates) which is labelled as a proxy | WP2 |
| Flash flood | none (a heuristic screening index and DEM context are shown, labelled unvalidated) | **Unavailable** by design | no flash-flood event labels, no hydrological model; IndoFloods are riverine gauge events, not flash-flood labels | WP3 |

## B. Cell diagnostics and threat attribution (physical diagnostics, all from GFS unless stated)

Every row below is a *model-derived or model-output* quantity, none is an observation. Common properties: 0.25 deg grid; 3-hourly f003-f024 of the 00Z/12Z cycles.

| Indicator | Source | Variable | Units | Kind and definition | Resolution | Timestamp | Missing data | Validation |
|---|---|---|---|---|---|---|---|---|
| CAPE | GFS 0.25 deg via NOAA AWS mirror | `cape` | J/kg | model output (GFS): GFS CAPE, surface-based parcel | 0.25 deg grid; 3-hourly f003-f024 of the 00Z/12Z cycles | per file: valid, S3 published, DRIFT acquired time (artifact `meta.files`) | null where a cell/variable is missing; never defaulted | national-fields validator re-derives provenance, units/ranges, accumulation windows and grid hash; stale cycles withheld |
| CIN | GFS 0.25 deg via NOAA AWS mirror | `cin` | J/kg | model output (GFS): GFS CIN, surface-based parcel (negative = inhibition) | 0.25 deg grid; 3-hourly f003-f024 of the 00Z/12Z cycles | per file: valid, S3 published, DRIFT acquired time (artifact `meta.files`) | null where a cell/variable is missing; never defaulted | national-fields validator re-derives provenance, units/ranges, accumulation windows and grid hash; stale cycles withheld |
| PWAT / integrated water vapour | GFS 0.25 deg via NOAA AWS mirror | `pwat` | mm | model output (GFS): GFS precipitable water, entire atmosphere | 0.25 deg grid; 3-hourly f003-f024 of the 00Z/12Z cycles | per file: valid, S3 published, DRIFT acquired time (artifact `meta.files`) | null where a cell/variable is missing; never defaulted | national-fields validator re-derives provenance, units/ranges, accumulation windows and grid hash; stale cycles withheld |
| Wind shear (850-500 hPa vector difference) | GFS 0.25 deg via NOAA AWS mirror | `shear_850_500` | m/s | calculated from GFS fields: |V500-V850| bulk wind difference. NOT 0-6 km shear | 0.25 deg grid; 3-hourly f003-f024 of the 00Z/12Z cycles | per file: valid, S3 published, DRIFT acquired time (artifact `meta.files`) | null where a cell/variable is missing; never defaulted | national-fields validator re-derives provenance, units/ranges, accumulation windows and grid hash; stale cycles withheld |
| Convergence (850 hPa) | GFS 0.25 deg via NOAA AWS mirror | `conv850` | 1e-5 1/s | calculated from GFS fields: 850 hPa horizontal convergence = -div(V) from GFS winds by centred differences on the 0.25 deg grid (positive = convergence) | 0.25 deg grid; 3-hourly f003-f024 of the 00Z/12Z cycles | per file: valid, S3 published, DRIFT acquired time (artifact `meta.files`) | null where a cell/variable is missing; never defaulted | national-fields validator re-derives provenance, units/ranges, accumulation windows and grid hash; stale cycles withheld |
| Convergence (10 m) | GFS 0.25 deg via NOAA AWS mirror | `conv10` | 1e-5 1/s | calculated from GFS fields: 10 m horizontal convergence = -div(V10) by centred differences (positive = convergence) | 0.25 deg grid; 3-hourly f003-f024 of the 00Z/12Z cycles | per file: valid, S3 published, DRIFT acquired time (artifact `meta.files`) | null where a cell/variable is missing; never defaulted | national-fields validator re-derives provenance, units/ranges, accumulation windows and grid hash; stale cycles withheld |
| K-index | GFS 0.25 deg via NOAA AWS mirror | `k_index` | degC | calculated from GFS fields: George K-index from GFS T and RH at 850/700/500 hPa | 0.25 deg grid; 3-hourly f003-f024 of the 00Z/12Z cycles | per file: valid, S3 published, DRIFT acquired time (artifact `meta.files`) | null where a cell/variable is missing; never defaulted | national-fields validator re-derives provenance, units/ranges, accumulation windows and grid hash; stale cycles withheld |
| Total Totals | GFS 0.25 deg via NOAA AWS mirror | `total_totals` | degC | calculated from GFS fields: Total Totals from GFS T and RH at 850/500 hPa | 0.25 deg grid; 3-hourly f003-f024 of the 00Z/12Z cycles | per file: valid, S3 published, DRIFT acquired time (artifact `meta.files`) | null where a cell/variable is missing; never defaulted | national-fields validator re-derives provenance, units/ranges, accumulation windows and grid hash; stale cycles withheld |
| Lifted index | GFS 0.25 deg via NOAA AWS mirror | `lftx` | K | model output (GFS): GFS surface lifted index (negative = unstable) | 0.25 deg grid; 3-hourly f003-f024 of the 00Z/12Z cycles | per file: valid, S3 published, DRIFT acquired time (artifact `meta.files`) | null where a cell/variable is missing; never defaulted | national-fields validator re-derives provenance, units/ranges, accumulation windows and grid hash; stale cycles withheld |
| CTT (satellite) | Himawari-9 AHI L1b band 13 (10.4 um), anonymous AWS bucket `noaa-himawari9` | `ctt_p10_k`, `ctt_min_k`, `cold_frac_235k` | K | **observation** (brightness temperature; equals cloud-top temperature only for opaque cloud; **not a retrieved CTT**) | 2 km at nadir, aggregated to 0.25 deg cells; 10-minute scans, ingest every 30 min | scene nominal time + S3 last-modified per file | null where a cell has no valid pixel | `validate_satellite_ctt.py` (alignment, 150-340 K, coverage >= 90%, age, honesty flags); shown only while <= 90 min old (PR #23) |
| CTT drop rate | same | `dtdt_30min_k_per_h`, `dtdt_60min_k_per_h` | K/h | **calculated** from two observations: p10 BT now minus p10 BT 30/60 min earlier (negative = cooling) | as above | as above | null if the earlier frame is missing | implausibility bound 120 K/h; rate requires a current BT |
| Operational lead | recorded `acquired_at_utc` of each GFS file | `operational_lead_hours` | h | **calculated**: valid time minus the time DRIFT held the data; never negative; excludes dissemination to users (PR #22) | per forecast hour | per file | null + status when valid time already passed | Python/JS parity test; per-hour e2e against the published JSON |

Not available anywhere: 0-6 km shear (no model-level winds are ingested), soil-moisture observations, radar, lightning, INSAT (MOSDAC login required), IMDAA (no access).

## C. Explainability and threat attribution

| Component | Source | Status |
|---|---|---|
| Pan-India TS and rain: per-cell top-3 drivers | XGBoost `pred_contribs` (TreeSHAP) on the same booster used for inference; contributions are in log-odds of the **uncalibrated** margin | Real; unavailable out of season |
| Station (VOBL) slot drivers | `compute_realtime_shap.py` on the real model | **Constants excluded** from the ranking and listed under `defaulted_features_excluded` (PR #7); the slots themselves are withheld |
| Physical diagnostics (section B) | deterministic physics, not ML | Kept separate from ML attribution in the UI |

## D. Map layers

| Layer | Data | Matches selector? | Evidence |
|---|---|---|---|
| GFS fields (CAPE, CIN, LI, K, TT, PWAT, shear, omega, wind, RH, T) | `india_fields_latest.json` | Yes, forecast-hour chip drives the layer | e2e: values change by hour; stale cycle withheld |
| Pan-India TS model | `india_ts_model_latest.json` | **Fixed in PR #18**: it showed the wrong lead for most chips; now only a model lead's own hour has data | e2e fixture fails on old code |
| Satellite BT coldness / cooling rate | `india_satellite_ctt_latest.json` | Observation, deliberately independent of the chips; withheld when stale | e2e: exact artifact values, hour-independence, stale and missing cases |
| Heavy-rain-day outlook | `drift_live.json` (1 deg score) | Daily, labelled not a cloudburst | existing tests |
| Terrain / flash-flood screening | static SRTM-derived terrain + heuristic | Static; labelled unvalidated | existing tests |

## E. Consistency checks that now exist in CI

Station slots withheld never show 0% and never carry PEAK (legacy e2e); model marked evaluated only in its validated months; map lead equals chip hour; operational lead equals valid minus recorded acquisition, per hour; stale satellite scan withheld; Python and JS records identical.

## F. Work packages that cannot be completed by editing code (each needs data, access or training that does not exist here)

| WP | Goal | Missing | Concrete next steps | Acceptance (never claimed before it is met) |
|---|---|---|---|---|
| WP1 | Genuine 2-6 h lead for the pan-India thunderstorm model | Training/validation data for new cycles and hours; compute | Ingest hourly f004-f016 of all four cycles; rebuild the training table per (cycle, forecast hour); define lead as valid minus issue time; retrain per lead | Year-ahead and leave-region-out holdouts; POD, FAR, CSI, HSS, PR-AUC, Brier, reliability reported per real lead; effective lead computed from recorded acquisition times |
| WP2 | Cloudburst prediction | An event definition and **hourly** rainfall labels (IMD AWS/ARG, IMD cloudburst reports, GSMaP/IMERG at sub-daily) | Adopt a definition (e.g. >= 100 mm/h over ~10 km); build a labelled event set with matched negatives; hourly predictors | Independent test events in several regions; calibrated probability; until then the output stays unavailable |
| WP3 | Flash-flood forecast | Observed flash-flood event labels with time and place; discharge/gauge series; antecedent rainfall and soil-moisture observations | Separate flash from riverine events; ERA5/IMERG-based rainfall and wetness for event dates plus matched negatives; hydrologic model per catchment | Event-based validation; terrain susceptibility stays a separate, labelled context layer |
| WP4 | Station (VOBL) thunderstorm without constants | Live station observations (IMD daily max/min/rain) or a model trained only on live-available predictors | Retrain XGBoost on GFS/upper-air/calendar features; chronological split; document the ERA5-vs-GFS train/serve skew | Held-out years, BSS > 0 with intervals; slots stop being `fallback` only when no model input is a constant |
| WP5 | Satellite inside models | A historical Himawari archive joined to labels (anonymous, 2015+) and INSAT access for better geometry over western India | Backfill B13 features for the training dates; add as predictors; compare against the GFS-only model | Improvement on a held-out season, not on the training window |
