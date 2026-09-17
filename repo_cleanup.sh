#!/usr/bin/env bash
# Run this from the repo root in Git Bash (Windows) or terminal (Mac/Linux)
# It moves all the loose development files out of root into a dev/ folder
# so judges see a clean repo when they land on GitHub.

set -e

mkdir -p dev

# All the numbered feature engineering / training scripts
git mv A1_feature_engineering.py dev/ 2>/dev/null || true
git mv A2_train_model.py dev/ 2>/dev/null || true
git mv A3_slot_models.py dev/ 2>/dev/null || true
git mv A4_shap_analysis.py dev/ 2>/dev/null || true
git mv "A4_shap_analysis_v2.py" dev/ 2>/dev/null || true
git mv "A4_shap_analysis_v3.py" dev/ 2>/dev/null || true
git mv A5_retrain_with_6hr_era5.py dev/ 2>/dev/null || true
git mv A6_calibration.py dev/ 2>/dev/null || true
git mv A7_rolling_verification.py dev/ 2>/dev/null || true
git mv A8_error_analysis.py dev/ 2>/dev/null || true
git mv A9_ensemble.py dev/ 2>/dev/null || true
git mv A10_lstm.py dev/ 2>/dev/null || true
git mv A11_synoptic_clustering.py dev/ 2>/dev/null || true
git mv A12_feature_engineering_v3.py dev/ 2>/dev/null || true
git mv A13_feature_engineering_v4.py dev/ 2>/dev/null || true

# Standalone Python scripts that live in root
git mv backtest_himawari.py dev/ 2>/dev/null || true
git mv baseline_model.py dev/ 2>/dev/null || true
git mv clean_stale_data.py dev/ 2>/dev/null || true
git mv compute_realtime_shap.py dev/ 2>/dev/null || true
git mv convlstm_train.py dev/ 2>/dev/null || true
git mv era5_benchmark.py dev/ 2>/dev/null || true
git mv evaluate.py dev/ 2>/dev/null || true
git mv "Fetch cb ff labels.py" dev/ 2>/dev/null || true
git mv fetch_dem_terrain.py dev/ 2>/dev/null || true
git mv fetch_gfs_realtime.py dev/ 2>/dev/null || true
git mv fetch_himawari_realtime.py dev/ 2>/dev/null || true
git mv fetch_imerg_realtime.py dev/ 2>/dev/null || true
git mv fetch_metar.py dev/ 2>/dev/null || true
git mv fetch_upperair_realtime.py dev/ 2>/dev/null || true
git mv forecast_action.py dev/ 2>/dev/null || true
git mv "forecast_action-1.py" dev/ 2>/dev/null || true
git mv forecast_json_exporter.py dev/ 2>/dev/null || true
git mv forecast_json_exporter_v2.py dev/ 2>/dev/null || true
git mv forecast_logger.py dev/ 2>/dev/null || true
git mv gfs_fetcher.py dev/ 2>/dev/null || true
git mv main.py dev/ 2>/dev/null || true
git mv moisture_flux_utils.py dev/ 2>/dev/null || true
git mv october_threshold_fix.py dev/ 2>/dev/null || true
git mv pan_india_gfs_fetcher.py dev/ 2>/dev/null || true
git mv populate_cape_baseline.py dev/ 2>/dev/null || true
git mv populate_skill_scores.py dev/ 2>/dev/null || true
git mv predict.py dev/ 2>/dev/null || true
git mv predict_nowcast.py dev/ 2>/dev/null || true
git mv predict_nowcast_v2.py dev/ 2>/dev/null || true
git mv radar_router.py dev/ 2>/dev/null || true
git mv rag_system.py dev/ 2>/dev/null || true
git mv recalibrate_models.py dev/ 2>/dev/null || true
git mv requirements.txt dev/ 2>/dev/null || true
git mv resave_models.py dev/ 2>/dev/null || true
git mv run_daily_forecast.py dev/ 2>/dev/null || true
git mv run_daily_forecast_v2.py dev/ 2>/dev/null || true
git mv run_himawari.bat dev/ 2>/dev/null || true
git mv run_radar_scheduler.py dev/ 2>/dev/null || true
git mv send_alerts.py dev/ 2>/dev/null || true
git mv shap_analysis.py dev/ 2>/dev/null || true
git mv streamlit_app.py dev/ 2>/dev/null || true

# Data / model output files
git mv "43295_Table_2_Daily_NDCQ202607153.csv" dev/ 2>/dev/null || true
git mv bengaluru_thunderstorm_features_merged.csv dev/ 2>/dev/null || true
git mv forecast.json dev/ 2>/dev/null || true
git mv B13_S05.DAT dev/ 2>/dev/null || true
git mv B13_S06.DAT dev/ 2>/dev/null || true
git mv HS_H09_20260725_1530_B13_FLDK_R20_S0410.DAT.bz2 dev/ 2>/dev/null || true
git mv HS_H09_20260725_1530_B13_FLDK_R20_S0510.DAT.bz2 dev/ 2>/dev/null || true
git mv HS_H09_20260725_1530_B13_FLDK_R20_S0610.DAT.bz2 dev/ 2>/dev/null || true

# Media files
git mv afternoon.mp4 dev/ 2>/dev/null || true
git mv afternoon.png dev/ 2>/dev/null || true
git mv afternoon1.png dev/ 2>/dev/null || true
git mv evening.mp4 dev/ 2>/dev/null || true
git mv evening.png dev/ 2>/dev/null || true
git mv evening1.png dev/ 2>/dev/null || true
git mv morning.mp4 dev/ 2>/dev/null || true
git mv morning.png dev/ 2>/dev/null || true
git mv morning1.png dev/ 2>/dev/null || true
git mv night.mp4 dev/ 2>/dev/null || true
git mv night.png dev/ 2>/dev/null || true
git mv night1.png dev/ 2>/dev/null || true
git mv hero.png dev/ 2>/dev/null || true
git mv assetssystem_architecture.png dev/ 2>/dev/null || true

# Docs that clutter root
git mv CHANGELOG.md dev/ 2>/dev/null || true
git mv RADAR_INTEGRATION.md dev/ 2>/dev/null || true
git mv API_EXAMPLES.md dev/ 2>/dev/null || true

# Misc
git mv index_backup.html dev/ 2>/dev/null || true
git mv .gitkeep dev/ 2>/dev/null || true
git mv .wslconfig dev/ 2>/dev/null || true

echo ""
echo "All files moved. Now commit:"
echo "  git commit -m 'Clean repo root: move dev files to dev/ folder'"
echo "  git push origin main"
