# MASTER Data Inventory — 2026-10-01

Verified by `find`/`ls`/`wc -l`/`head`/pandas against the actual files on disk in this repo, not
by trusting prior doc text. Tags: **OBSERVED** / **DERIVED** / **PROXY** / **UNKNOWN** / **UNAVAILABLE**,
and lifecycle tags: historical / live / proxy / research-only / blocked / unused.

## Station-level (VOBL, WMO 43295) — Bengaluru

| File | Source | Resolution | Coverage | Tag | Usage |
|---|---|---|---|---|---|
| `data/gfs_realtime_43295.csv` | NOAA GFS 0.25° via NOMADS | 0.25°, hourly fetch cadence | VOBL point, live | OBSERVED (model analysis, not station obs) | Production, live |
| `data/upperair_realtime_43295.csv` | GFS upper-air levels | 0.25°, per-cycle | VOBL point | DERIVED | Production |
| `data/era5_6hrly_bengaluru_2015_2025.csv`, `..._with_indices.csv` | ECMWF ERA5 reanalysis | 6-hourly, ~0.25° | 2015–2025, Bengaluru | OBSERVED (reanalysis) | Historical training |
| `data/bengaluru_6hr_training_dataset*.csv` (v3, v4, cb_ff) | Derived from ERA5 + IMD obs | 6-hourly | 2015–2025 | DERIVED | Training, historical — **4 near-duplicate versions on disk (v3, v4, cb_ff, base) — stale/duplicate risk, unclear which is canonical for current training** |
| `data/himawari_realtime.json`, `himawari_history.json`, `himawari_historical_bt.csv` | Himawari-9 AHI, MOSDAC-free mirror | ~2km, 10-min | Bengaluru crop only | OBSERVED | Live (satellite override) + historical |
| `data/blr_terrain.json` | SRTM/CartoDEM | 30m-class | Bengaluru only | OBSERVED | Production (VOBL terrain wetness only) |
| `data/cape_training_baseline.npy`, `k_index_training_baseline.npy`, `totals_totals_training_baseline.npy`, `precip_water_training_baseline.npy`, `lifted_index_training_baseline.npy` | Derived climatology baselines | — | historical | DERIVED | Used for anomaly/percentile scoring, historical |
| `data/cape_climatology.json` | Derived | — | historical | DERIVED | historical/reference |
| `data/actual_log.csv`, `data/alert_history.json`, `data/skill_scores.json` | Pipeline's own output log | — | live, rolling | DERIVED | live verification/skill-score tracking |

## Pan-India

| File | Source | Resolution | Coverage | Tag | Usage |
|---|---|---|---|---|---|
| `data/pan_india_grid.json` | GFS 0.25° regridded to 1.0° canonical application grid | 1.0°, 992 cells | Mainland India, live | OBSERVED (GFS fields) + PROXY (TS/CB/FF scores) | Production, live — **the hazard scores on this file are a hand-weighted heuristic formula, not a trained model's output; GFS raw fields (CAPE, shear, PWAT) feeding it are real** |
| `data/canonical_forecast.json` | `canonical_forecast_writer.py`, merges VOBL ML output + pan-India heuristic grid | 1.0° + VOBL point override | Live | mixed OBSERVED/DERIVED/PROXY | Production artifact served to frontend |

## Flash-flood research (Phase 5–5.7, INDOFLOODS)

| File | Rows (verified) | Tag | Status |
|---|---|---|---|
| `processed/ff_pu/ff_pu_training_table.csv` | 144,486 data rows (`wc -l` = 144,487 incl. header); 620 `label_status=POSITIVE`, 143,866 `UNLABELED` (pandas-verified) | OBSERVED (positives, IMD rainfall + INDOFLOODS overlap) / UNKNOWN (unlabeled rest — not confirmed negatives) | research-only, not in production |
| `processed/indofloods/indofloods_grid_events.csv`, `indofloods_grid_mapping.csv` | not re-counted this pass beyond the 620/69/131 figures already cross-checked by the Phase 5.7 report, which this pass independently re-derived the 144,486/620 split for | OBSERVED | research-only |
| `data/floodevents_indofloods.csv`, `data/catchment_characteristics_indofloods.csv` (note: this exact filename exists under `data/`, contrary to one dev-script's expectation of a differently-scoped gauge-coordinate file — see MASTER_SIH_REQUIREMENT_MATRIX #17 for the specific missing file) | OBSERVED | mixed research-only/blocked depending on exact field needed |
| `processed/ff_pu/RESEARCH_ONLY_model_c_xgboost.json`, `RESEARCH_ONLY_model_c_logistic.pkl` | n/a (models) | DERIVED | explicitly filename-flagged research-only — good practice, confirmed not referenced by any production path (`grep -rl "RESEARCH_ONLY" --include=*.py .` returns only the training/eval scripts, never `forecast_action.py`, `backend/pipeline.py`, or `canonical_forecast_writer.py`) |
| `processed/labels/ff_labels_proxy.csv`, `ff_labels_summary.json` | — | PROXY | **this is the one actually used in the production FF XGBoost path** — rainfall-threshold proxy, not an observed flood event |

## Blocked / unavailable

| Location | Status | Evidence |
|---|---|---|
| `raw/imdaa/` | **empty, blocked** | `ls` confirms zero data files, only a README explaining it needs NCMRWF registration |
| `processed/imdaa/` | **empty, blocked** | same — downstream of raw being empty |
| INSAT-3D/3DR live feed | **unavailable in this sandbox and in production per README** | `MOSDAC_USER`/`PASS` env vars unset per prior-pass grep; could not independently test live connectivity from this sandbox (no egress to MOSDAC) |

## Duplicate / stale artifacts flagged

- Four Bengaluru training CSVs (`bengaluru_6hr_training_dataset.csv`, `_v3.csv`, `_v4.csv`,
  `_cb_ff.csv`) coexist with no in-repo note on which is authoritative for current model training —
  recommend the remediation plan resolve this (see MASTER_REMEDIATION_PLAN P4).
- `models/` directory holds many versioned XGBoost pickles per slot (`v2`, `v3`, `v4`, `v4_ensemble`,
  `v4_calibrated`, `v5_temporal`, `v6_temporal`, plus `_calibrator.pkl` siblings) — could not confirm
  from file listing alone which exact file `forecast_action.py` loads at runtime for each slot without
  deeper code trace; flagged as a possible source of "wrong model loaded" risk, not confirmed a bug.
- `data/gfs_multiday_43295.json` (7-day outlook) exists but its production consumption path was not
  traced this pass — UNKNOWN usage, flag for remediation triage.
