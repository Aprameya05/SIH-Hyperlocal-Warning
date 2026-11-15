# Hazard model validation

Generated 2026-11-15 16:13 UTC by `scripts/utilities/generate_validation_docs.py` from `data/*_evaluation.json`. Nothing below is typed by hand.

Status per hazard (what the product actually serves):

| Hazard | Prediction product | Status |
|---|---|---|
| Severe thunderstorm | Pan-India lead-specific model, +6, +12, +18, +24 h, calibrated to airport METAR thunderstorm reports | AVAILABLE, EVALUATED |
| Extreme rainfall | Short-duration heavy rainfall (>= X mm / 3 h, CMORPH-labelled); targets published: ['20mm_f9'] | AVAILABLE, EVALUATED (those targets only) |
| Extreme rainfall | Daily heavy-rain-day outlook (>= 64.5 mm/24 h, 1 deg, existing `panindia_cb_v1`) | AVAILABLE as an UNCALIBRATED ranking score; negative Brier skill uncalibrated |
| Cloudburst | any | **UNAVAILABLE** (no sub-hourly labels or observations; 0.25 deg / 3 h data cannot verify >= 100 mm/h events) |
| Flash flood | prediction | **UNAVAILABLE** (no flood-event labels with time/location, no observed soil moisture or river levels); a heuristic screening index is shown and labelled unvalidated |

## 1. Thunderstorm

- **Target.** Thunderstorm (METAR `TS` or `VCTS`) reported by an Indian airport within +-1.5 h of the forecast valid time; a window with fewer than 2 reports is unlabelled (never counted as negative). This is an ORDINARY-thunderstorm target; a documented *severe*-thunderstorm label (hail/gust/lightning counts) is not available from METAR and is not claimed.
- **Data.** 19,994 station-windows, 113 stations, 43 init dates, years [2024, 2025], event rate 0.0654. Cycles 00Z and 12Z, leads 6/12/18/24 h. Stations by region: {'CENTRAL': 10, 'EAST': 12, 'NORTH': 27, 'NORTHEAST': 12, 'SOUTH': 34, 'WEST': 18}.
- **Predictors.** GFS 0.25 deg at the nearest grid point (24 features: cape, cin, lftx, pwat, rh2m, rh850, rh700, rh500, w850, w700, w500, soilw0_10, k_index, total_totals, lapse_850_500, ws10, shear_850_500, t2m_c, dewpoint_depression, mslp_hpa, apcp_6h, apcp_total, valid_hour_sin, valid_hour_cos).
- **Validation.** train 2024 -> test 2025, all regions; leave-one-region-out within 2024 training -> test the held-out region in 2025. Leakage controls: test year and test region never enter fitting; calibration and threshold from date-grouped out-of-fold TRAIN predictions; fixed hyperparameters, no tuning on test; one model per lead.
- **Gate.** calibrated model: Brier skill >= 0.01 vs climatology AND AUROC >= 0.65 in BOTH validation A and pooled B.

Temporal holdout (train 2024, test 2025), calibrated XGBoost; 95% CI = block bootstrap over forecast dates (200 resamples):

| Lead | n | events | AUROC (95% CI) | PR-AUC (no skill) | Brier | Brier skill (95% CI) | ECE | POD | FAR | CSI | HSS | BIAS | false alarms /100 forecasts | persistence AUROC | leave-region-out AUROC / BSS | published |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| +6 h | 2290 | 127 | 0.817 [0.777, 0.863] | 0.180 (0.056) | 0.0479 | 0.090 [0.054, 0.123] | 0.0126 | 0.496 | 0.793 | 0.171 | 0.232 | 2.40 | 10.6 | 0.621 | 0.805 / 0.090 | yes |
| +12 h | 2435 | 241 | 0.741 [0.704, 0.782] | 0.231 (0.099) | 0.0833 | 0.072 [0.039, 0.104] | 0.0193 | 0.444 | 0.740 | 0.196 | 0.232 | 1.71 | 12.5 | 0.486 | 0.738 / 0.065 | yes |
| +18 h | 2360 | 124 | 0.843 [0.803, 0.888] | 0.181 (0.052) | 0.0455 | 0.093 [0.067, 0.116] | 0.0152 | 0.573 | 0.787 | 0.183 | 0.253 | 2.69 | 11.1 | 0.568 | 0.834 / 0.077 | yes |
| +24 h | 2438 | 231 | 0.742 [0.693, 0.782] | 0.210 (0.095) | 0.0801 | 0.070 [0.036, 0.096] | 0.0197 | 0.407 | 0.760 | 0.178 | 0.208 | 1.69 | 12.2 | 0.586 | 0.705 / 0.045 | yes |

BIAS is the frequency bias (hits + false alarms) / (hits + misses) = POD / (1 - FAR), derived from the stored POD and FAR. **Reading the categorical scores honestly:** the probabilities are calibrated (low ECE, positive Brier skill), but at the stored decision thresholds the yes/no forecasts over-forecast (BIAS > 1) and most flagged cases are false alarms (FAR about 0.74-0.79 for thunderstorms, 0.83 for the 20 mm / 3 h rainfall target). Use the probability, not the yes/no flag; these scores do not support a warning-grade claim.


Baselines on the same test rows (lead +12 h shown; all leads are in the JSON): climatology (training base rate), CAPE-threshold rule, logistic regression on 6 predictors, persistence (thunderstorm in the 3 h before issue).

| Lead | XGBoost AUROC | Logistic AUROC | CAPE-rule CSI | XGBoost CSI | Persistence CSI (rows with an observation) |
|---|---|---|---|---|---|
| +6 h | 0.817 | 0.749 | 0.057 | 0.171 | 0.156 |
| +12 h | 0.741 | 0.701 | 0.115 | 0.196 | 0.037 |
| +18 h | 0.843 | 0.766 | 0.074 | 0.183 | 0.082 |
| +24 h | 0.742 | 0.711 | 0.119 | 0.178 | 0.153 |

Leave-one-region-out (train 2024 without the region, test 2025 in the region), AUROC per held-out region:

| Lead | CENTRAL | EAST | NORTH | NORTHEAST | SOUTH | WEST |
|---|---|---|---|---|---|---|
| +6 h | 0.766 | 0.781 | n/a | 0.787 | 0.797 | 0.881 |
| +12 h | 0.781 | 0.712 | 0.781 | 0.637 | 0.730 | 0.784 |
| +18 h | 0.746 | 0.851 | 0.824 | 0.804 | 0.814 | 0.923 |
| +24 h | 0.795 | 0.695 | 0.725 | 0.514 | 0.706 | 0.798 |

By month of the valid time (2025 test, lead +12 h): {"4": {"n": 1093, "auroc": 0.7928}, "5": {"n": 680, "auroc": 0.6886}, "6": {"n": 612, "auroc": 0.7397}}

**Limits.** labels are airport METAR reports: urban/plain-biased, not point-in-cell truth for rural or mountain cells; pilot sample (every 12th-3rd day of Apr-Jun, two seasons): wide uncertainty; pre-monsoon season only; lead and time-of-day are confounded because only 00Z/12Z cycles are used; probabilities are calibrated to station reports, not to a generic 0.25 deg cell.

## 2. Short-duration heavy rainfall (3 h)

- **Target.** 3 h CMORPH rainfall at the 0.25 deg cell >= threshold (short-duration heavy rainfall; NOT cloudburst)
- **Labels.** NOAA CDR CMORPH V1.0 hourly 0.25 deg, 3 h accumulation ending at the valid time (satellite estimate, gauge-adjusted over land). **Predictors.** GFS 0.25 deg init 00Z, leads 9/15/21 h, cell centre = GFS grid point. 445,728 cell-windows, 32 init dates, monsoon, 15 Jun - 15 Sep, every 6th day.
- **Gate.** calibrated model: Brier skill >= 0.01, AUROC >= 0.65 in temporal AND pooled leave-region-out holdout, and AUROC >= raw GFS rainfall rule.

| Target | events (test) | AUROC (95% CI) | raw GFS rainfall AUROC | PR-AUC (no skill) | Brier skill (95% CI) | ECE | POD | FAR | CSI | BIAS | false alarms /100 | leave-region-out AUROC / BSS | published |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20mm_f9 | 650 | 0.894 [0.861, 0.919] | 0.859 | 0.0944 (0.0087) | 0.052 [0.023, 0.075] | 0.0027 | 0.220 | 0.833 | 0.105 | 1.32 | 0.96 | 0.866 / 0.027 | yes |
| 20mm_f15 | 256 | 0.812 [0.743, 0.873] | 0.728 | 0.0170 (0.0034) | 0.005 [-0.006, 0.016] | 0.0010 | 0.031 | 0.928 | 0.022 | 0.43 | 0.14 | 0.793 / 0.002 | no |
| 20mm_f21 | 888 | 0.819 [0.777, 0.857] | 0.797 | 0.0510 (0.0120) | 0.024 [0.006, 0.037] | 0.0016 | 0.096 | 0.912 | 0.048 | 1.09 | 1.19 | 0.802 / 0.009 | no |
| 40mm_f9 | insufficient events | | | | | | | | | | | no |
| 40mm_f15 | insufficient events | | | | | | | | | | | no |
| 40mm_f21 | 156 | 0.812 [0.732, 0.884] | 0.845 | 0.0091 (0.0021) | 0.003 [-0.007, 0.012] | 0.0013 | 0.070 | 0.965 | 0.024 | 2.01 | 0.41 | 0.776 / -0.003 | no |

**Limits.** CMORPH is a satellite estimate; heavy convective rainfall in mountains is under-estimated; monsoon only, two seasons, every 6th day: wide uncertainty; leads 9/15/21 h from 00Z only; 0.25 deg / 3 h cannot represent cloudbursts (>= 100 mm/h over ~10 km).

## 3. Existing models (not retrained here)

- VOBL station thunderstorm model (Bengaluru airport only): walk-forward by year (train on earlier years only); mean AUROC 0.9002, Brier skill positive in 3 of 5 years. This is a single-station model and is not a pan-India prediction.
- Heavy-rain-day model `panindia_cb_v1` (pan-India grid, 10 held-out dates (leave-one-date-out), 6 informative): uncalibrated Brier skill -0.1448 (worse than climatology; a ranking score, not a probability).
- Flash flood: no model is evaluated.

## 4. Reproduce

```bash
python pipelines/feature_engineering/build_thunderstorm_training_dataset.py   # METAR labels x GFS (resumable; ~hours, network)
python models/thunderstorm/train_and_evaluate_thunderstorm_model.py
python pipelines/feature_engineering/build_rainfall_training_dataset.py       # CMORPH labels x GFS (resumable)
python models/extreme_rainfall/train_and_evaluate_extreme_rainfall_model.py
python models/thunderstorm/predict_thunderstorm.py && python models/extreme_rainfall/predict_extreme_rainfall.py
python scripts/utilities/generate_validation_docs.py
```

Seeds are fixed; `tests/test_national_pipeline.py` re-runs both predictors and requires bit-identical output to the committed artifacts.
