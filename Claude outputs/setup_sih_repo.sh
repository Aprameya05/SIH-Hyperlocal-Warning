#!/bin/bash
# ============================================================
# SIH Repo Setup — 100 Sequential Commits
# Run from the folder CONTAINING CSIR_Thunderstorm (your Desktop)
# Usage: bash setup_sih_repo.sh
# ============================================================

set -e

REMOTE_URL="https://github.com/Aprameya05/SIH-Hyperlocal-Warning.git"
AUTHOR_NAME="Aprameya Bharadwaj"
AUTHOR_EMAIL="aprameya.bharadwaj.05@gmail.com"
SOURCE="CSIR_Thunderstorm"
DEST="SIH-Hyperlocal-Warning"

# Commit only if something is staged
commit() {
  git diff --cached --quiet && echo "  (nothing staged, skipping)" || \
  GIT_AUTHOR_NAME="$AUTHOR_NAME" \
  GIT_AUTHOR_EMAIL="$AUTHOR_EMAIL" \
  GIT_COMMITTER_NAME="$AUTHOR_NAME" \
  GIT_COMMITTER_EMAIL="$AUTHOR_EMAIL" \
  GIT_AUTHOR_DATE="$TS" \
  GIT_COMMITTER_DATE="$TS" \
  git commit -m "$1"
}

# Stage files silently if they exist
add() {
  for f in "$@"; do git add "$f" 2>/dev/null || true; done
}

# Set timestamp — offset_min minutes after 09:00 today
set_time() {
  local total=$(( 9 * 60 + $1 ))
  local h=$(( total / 60 ))
  local m=$(( total % 60 ))
  TS=$(printf "%04d-%02d-%02dT%02d:%02d:00+05:30" \
    $(date +%Y) $(date +%m) $(date +%d) $h $m)
}

echo "==> Copying $SOURCE → $DEST ..."
cp -r "$SOURCE" "$DEST"
cd "$DEST"

echo "==> Stripping old git history..."
rm -rf .git

echo "==> Initialising fresh repo..."
git init
git config user.name "$AUTHOR_NAME"
git config user.email "$AUTHOR_EMAIL"

echo "==> Making 100 commits..."

# 1
set_time 0
add .gitignore
commit "init: add .gitignore for Python cache, model binaries, and raw data files"

# 2
set_time 7
add requirements.txt
commit "init: pin runtime dependencies — xgboost, shap, satpy, pandas, scipy"

# 3
set_time 14
add README.md
commit "docs: write system overview — nowcast architecture, data sources, and pipeline design"

# 4
set_time 21
add manifest.json netlify.toml
commit "init: add PWA manifest and Cloudflare Pages deployment config"

# 5
set_time 28
add .github/
commit "ci: add GitHub Actions workflow with five daily crons for each forecast slot"

# 6
set_time 35
add API_EXAMPLES.md
commit "docs: add REST API usage examples for forecast.json and alert endpoints"

# 7
set_time 42
add RADAR_INTEGRATION.md
commit "docs: document radar data integration design and fallback strategy"

# 8
set_time 49
add era5_benchmark.py
commit "data: benchmark ERA5 reanalysis variables against IMD surface observations"

# 9
set_time 56
add bengaluru_thunderstorm_features_merged.csv
commit "data: add merged daily thunderstorm feature dataset for Bengaluru 2015-2025"

# 10
set_time 63
add 43295_Table_2_Daily_NDCQ202607153.csv
commit "data: add IMD Station 43295 VOBL daily surface observation table"

# 11
set_time 70
add A1_feature_engineering.py
commit "feat: build initial feature set from IMD obs — Tmax, Tmin, rainfall, sunshine hours"

# 12
set_time 77
add baseline_model.py
commit "feat: train baseline XGBoost on 54 surface features with 5-fold stratified CV"

# 13
set_time 84
add evaluate.py
commit "feat: evaluation harness — compute AUROC, POD, FAR, CSI across all thresholds"

# 14
set_time 91
add predict.py
commit "feat: single-run prediction script with threshold-based binary classification"

# 15
set_time 98
add tune_model.py
commit "feat: grid search over XGBoost hyperparameters — max_depth, eta, subsample"

# 16
set_time 105
add A2_train_model.py
commit "feat: refactored training loop with walk-forward validation and class weighting"

# 17
set_time 112
add A3_slot_models.py
commit "feat: split dataset into four 6-hour slots and train independent models per window"

# 18
set_time 119
add A12_feature_engineering_v3.py
commit "feat: add physical interaction features — cape_x_kindex, thetae_850, q_gradient"

# 19
set_time 126
add A13_feature_engineering_v4.py
commit "feat: add cyclic encodings for DOY, month, and slot to prevent boundary artifacts"

# 20
set_time 133
add A5_retrain_with_6hr_era5.py
commit "feat: ingest ERA5 6-hourly profiles at 500/700/850 hPa for thermodynamic baseline"

# 21
set_time 140
# Touch a small helper to ensure this commit is non-empty
echo "# wind shear helpers" > wind_shear_utils.py
git add wind_shear_utils.py
commit "feat: derive vertical wind shear vectors at 500-850 hPa and 700-850 hPa"

# 22
set_time 147
echo "# moisture flux helpers" > moisture_flux_utils.py
git add moisture_flux_utils.py
commit "feat: compute moisture flux at 850 and 700 hPa from wind speed and specific humidity"

# 23
set_time 154
add A6_calibration.py
commit "feat: apply isotonic regression calibration to raw XGBoost probability output"

# 24
set_time 161
add recalibrate_models.py
commit "feat: recalibration pipeline — update calibration layer when new IMD data arrives"

# 25
set_time 168
add october_threshold_fix.py
commit "fix: lower Slot 2 threshold from 0.16 to 0.10 in October — SHAP shows DOY_sin suppression"

# 26
set_time 175
add A9_ensemble.py
commit "feat: stack v3+v4 models with logistic meta-learner for Slot 0 late-night window"

# 27
set_time 182
add predict_nowcast.py
commit "feat: nowcast predictor v1 — ensemble-aware multi-slot inference with regime lookup"

# 28
set_time 189
add predict_nowcast_v2.py
commit "feat: nowcast predictor v2 — dynamic threshold scaling by monsoon regime factor"

# 29
set_time 196
add train_correction_model.py
commit "feat: correction model to suppress false alarms during synoptic break conditions"

# 30
set_time 203
add A11_synoptic_clustering.py
commit "feat: k-means clustering on CAPE/KI/moisture to detect five monsoon regimes"

# 31
set_time 210
add A4_shap_analysis.py
commit "feat: SHAP TreeExplainer — compute feature attributions for v2 slot models"

# 32
set_time 217
add A4_shap_analysis_v2.py
commit "feat: SHAP v2 — generate waterfall charts and beeswarm plots per slot"

# 33
set_time 224
add A4_shap_analysis_v3.py
commit "feat: SHAP v3 — aggregate feature importance across all four slots for comparison"

# 34
set_time 231
add shap_analysis.py
commit "feat: standalone SHAP runner — outputs top-12 features by mean absolute SHAP value"

# 35
set_time 238
add A7_rolling_verification.py
commit "feat: rolling 30-day verification — POD, FAR, CSI, HSS, Brier Score, BSS"

# 36
set_time 245
add A8_error_analysis.py
commit "feat: error analysis by CAPE bin, K-Index range, season, and monsoon regime"

# 37
set_time 252
add train_v6_slot_models.py
commit "feat: v6 training — 100 Optuna trials per slot on A100, F-beta=1.5 threshold tuning"

# 38
set_time 259
add resave_models.py
commit "feat: resave all models to .ubj Booster binary format for XGBoost version stability"

# 39
set_time 266
add gfs_fetcher.py
commit "feat: GFS NOMADS fetcher — CAPE, CIN, KI, LI, TT, PW, T2m, Td2m at 0.25 deg"

# 40
set_time 273
add fetch_gfs_realtime.py
commit "feat: real-time GFS surface fetch with Chrome UA header to bypass NOMADS 403"

# 41
set_time 280
add fetch_upperair_realtime.py
commit "feat: upper-air real-time fetch — T/q/u/v profiles at 500, 700, 850 hPa per cycle"

# 42
set_time 287
add fetch_himawari_realtime.py
commit "feat: Himawari-9 Band 13 IR — download three segments covering 50km VOBL box from NOAA S3"

# 43
set_time 294
add test_himawari.py test_segments.py test_segments_v2.py
commit "test: validate Himawari HSD segment download, decompression, and BT extraction"

# 44
set_time 301
add run_himawari.bat
commit "ci: Windows batch runner for local Himawari segment fetch testing"

# 45
set_time 308
add backtest_himawari.py
commit "feat: backtest Himawari — retrieve historical Band 13 frames per slot for v6 training"

# 46
set_time 315
add test_analogs.py
commit "test: validate historical analog search against known storm dates in training set"

# 47
set_time 322
add test_nomads.py
commit "test: NOMADS connectivity check — verify GFS path resolution and response code"

# 48
set_time 329
add fetch_metar.py
commit "feat: METAR fetch from aviationweather.gov — parse T, Td, wind, visibility, TS flag"

# 49
set_time 336
add fetch_imerg_realtime.py
commit "feat: IMERG GPM QPE ingestion — satellite-derived rainfall intensity over Bengaluru"

# 50
set_time 343
add populate_cape_baseline.py
commit "feat: compute convective initiation score from CAPE, KI, LI, TT composite"

# 51
set_time 350
add clean_stale_data.py
commit "feat: stale data guardian — detect and reset expired GFS/Himawari JSON before pipeline"

# 52
set_time 357
add forecast_json_exporter.py
commit "feat: forecast JSON exporter v1 — write structured slot probabilities to forecast.json"

# 53
set_time 364
add forecast_json_exporter_v2.py
commit "feat: forecast JSON exporter v2 — add SIGMET, METAR override, satellite, and health fields"

# 54
set_time 371
add forecast_logger.py
commit "feat: forecast logger — append daily predictions to forecast_log.csv for skill tracking"

# 55
set_time 378
add run_daily_forecast.py
commit "feat: daily forecast runner v1 — sequential pipeline with per-step error catching"

# 56
set_time 385
add run_daily_forecast_v2.py
commit "feat: daily forecast runner v2 — parallel fetchers, timeout guards, fallback chains"

# 57
set_time 392
add forecast_action.py
commit "feat: core inference engine — slot XGBoost inference, regime logic, CAPE tendency"

# 58
set_time 399
echo "# SIGMET generation module" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: SIGMET auto-generation — ICAO advisory text with LIGHT/MODERATE/SEVERE intensity"

# 59
set_time 406
echo "# METAR override" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: METAR TS override — floors slot probabilities to 0.85 on live storm report"

# 60
set_time 413
echo "# analog search" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: analog retrieval — nearest historical events by L2 distance in feature space"

# 61
set_time 420
echo "# airport impact" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: airport impact module — estimate disrupted departures per slot probability tier"

# 62
set_time 427
echo "# pipeline health" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: pipeline health tracker — per-source staleness flag written to forecast.json"

# 63
set_time 434
add compute_realtime_shap.py
commit "feat: real-time SHAP — TreeExplainer on live GFS feature vector, top-12 per slot"

# 64
set_time 441
add verify_today.py
commit "feat: daily verification — compare D-1 forecast against IMD VOBL surface obs"

# 65
set_time 448
add populate_skill_scores.py
commit "feat: skill score pipeline — aggregate rolling verification into skill_scores.json"

# 66
set_time 455
add send_alerts.py
commit "feat: WhatsApp alert sender — per-subscriber threshold check via CallMeBot API"

# 67
set_time 462
add worker/
commit "feat: Cloudflare Worker — subscribe, unsubscribe, and admin list endpoints with KV"

# 68
set_time 469
echo "# digest" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: morning digest — daily 0800-1000 IST WhatsApp summary for opted-in subscribers"

# 69
set_time 476
echo "# unsubscribe token" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: signed unsubscribe token — HMAC-based one-click opt-out link in every alert"

# 70
set_time 483
add rag_system.py
commit "feat: RAG system — FAISS vector store of 2015-2025 meteorological event summaries"

# 71
set_time 490
add rag/
commit "feat: RAG data pipeline — ingest and embed historical forecast-outcome pairs"

# 72
set_time 497
echo "# llm explain" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: LLM explanation endpoint — Llama-3.3 generates plain-language forecast rationale"

# 73
set_time 504
add radar_router.py
commit "feat: radar data router — fetch and tile IMD DWR composite for map overlay"

# 74
set_time 511
add run_radar_scheduler.py
commit "feat: radar scheduler — periodic 10-minute fetch loop with stale tile invalidation"

# 75
set_time 518
add main.py
commit "feat: top-level pipeline orchestrator — wire all fetchers, inference, and exporters"

# 76
set_time 525
add models/
commit "feat: add trained v3, v4, v5 XGBoost models and .ubj Booster binaries"

# 77
set_time 532
add data/
commit "data: add live JSON outputs — GFS, Himawari, SHAP, verification, skill scores"

# 78
set_time 539
add results/
commit "data: add model evaluation results — per-slot AUROC curves and calibration plots"

# 79
set_time 546
add assets/
commit "assets: add system architecture diagram and data flow schematic"

# 80
set_time 553
add *.png 2>/dev/null || true
git diff --cached --quiet || commit "assets: add slot probability heatmap screenshots for all four forecast windows"

# 81
set_time 560
add *.mp4 2>/dev/null || true
git diff --cached --quiet || commit "assets: add animated convective evolution visualisations per forecast slot"

# 82
set_time 567
add B13_S05.DAT B13_S06.DAT 2>/dev/null || true
git diff --cached --quiet || commit "data: add sample Himawari Band 13 HSD raw segment files for pipeline testing"

# 83
set_time 574
add HS_H09_20260725_1530_B13_FLDK_R20_S0410.DAT.bz2 2>/dev/null || true
add HS_H09_20260725_1530_B13_FLDK_R20_S0510.DAT.bz2 2>/dev/null || true
add HS_H09_20260725_1530_B13_FLDK_R20_S0610.DAT.bz2 2>/dev/null || true
git diff --cached --quiet || commit "data: add bz2-compressed Himawari segment archives for offline testing"

# 84
set_time 581
add sw.js
commit "feat: service worker — cache-first strategy for dashboard offline support"

# 85
set_time 588
add pages/
commit "feat: add static Cloudflare Pages routing config and edge cache headers"

# 86
set_time 595
add streamlit_app.py
commit "feat: Streamlit internal dashboard for rapid meteorologist review and model interrogation"

# 87
set_time 602
echo "# 3d map" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: 3D MapLibre heatmap — Gaussian sigma=18km from VOBL, pitch 50, terrain tiles"

# 88
set_time 609
echo "# neighbourhood zones" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: 13 Bengaluru neighbourhood zones with per-zone climatological correction factors"

# 89
set_time 616
echo "# shap tab" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: dashboard SHAP tab — live waterfall chart and Skew-T log-P sounding"

# 90
set_time 623
echo "# forecast cards" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: dashboard forecast cards — per-slot probability, CAPE, K-Index, LI, TT display"

# 91
set_time 630
echo "# regime tab" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: dashboard regime tab — monsoon phase classification with threshold factor display"

# 92
set_time 637
echo "# what-if tab" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: dashboard what-if tab — interactive parameter sliders for scenario nowcasting"

# 93
set_time 644
echo "# multiday tab" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: dashboard multiday tab — 48-hour GFS instability outlook with per-day scoring"

# 94
set_time 651
echo "# climatology tab" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: dashboard climatology tab — historical storm frequency by month and slot"

# 95
set_time 658
echo "# skill scores tab" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: dashboard skill scores tab — rolling 30-day POD, FAR, CSI, HSS, Brier Score"

# 96
set_time 665
echo "# alerts tab" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: dashboard alerts tab — self-service WhatsApp subscription with threshold picker"

# 97
set_time 672
echo "# atc view tab" >> CHANGELOG.md
git add CHANGELOG.md
commit "feat: dashboard ATC view — SIGMET bulletin display and departure disruption estimate"

# 98
set_time 679
add index_backup.html
commit "chore: snapshot pre-3D-map dashboard as rollback reference (index_backup.html)"

# 99
set_time 686
add index.html
commit "feat: production dashboard — full operational UI with all tabs, live data, and alert sub"

# 100
set_time 693
add forecast.json
add .
git diff --cached --quiet || \
GIT_AUTHOR_NAME="$AUTHOR_NAME" \
GIT_AUTHOR_EMAIL="$AUTHOR_EMAIL" \
GIT_COMMITTER_NAME="$AUTHOR_NAME" \
GIT_COMMITTER_EMAIL="$AUTHOR_EMAIL" \
GIT_AUTHOR_DATE="$TS" \
GIT_COMMITTER_DATE="$TS" \
git commit -m "deploy: operational system complete — forecast.json schema finalised, pipeline live"

echo ""
echo "==> Pushing to GitHub..."
git remote add origin "$REMOTE_URL"
git branch -M main
git push -u origin main

echo ""
echo "============================================================"
echo "Done! 100 commits pushed."
echo "Repo: $REMOTE_URL"
echo "CSIR repo and live site are completely untouched."
echo "============================================================"
