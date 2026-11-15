# DRIFT national data report

Generated 2026-11-15 15:25Z from the committed artifacts by `scripts/utilities/generate_national_report.py`. GFS cycle **2026-11-15T00:00:00Z**; 4643 India land cells at 0.25 deg; forecast hours [3, 6, 9, 12, 15, 18, 21, 24].

## 1. Source-to-output trace

Every value in a location panel comes from one of the rows below. **Where**: the selected 0.25 deg cell (cell centre = one GFS grid point, exact extraction, no interpolation). **Times** (per forecast hour, in the artifact and the panel): model init, valid time, S3 publication time of the NWP file (observed), acquisition time. Statuses (CURRENT / PAST_VALID_TIME / STALE) are recomputed from these times and the clock, never read from the producer.

| Variable | Unit | Kind | Definition / source message | Resolution | Safe as current? |
|---|---|---|---|---|---|
| `cape` | J/kg | RAW_MODEL_OUTPUT | GFS CAPE, surface-based parcel | 0.25 deg GFS point | only while status = CURRENT |
| `cin` | J/kg | RAW_MODEL_OUTPUT | GFS CIN, surface-based parcel (negative = inhibition) | 0.25 deg GFS point | only while status = CURRENT |
| `lftx` | K | RAW_MODEL_OUTPUT | GFS surface lifted index (negative = unstable) | 0.25 deg GFS point | only while status = CURRENT |
| `pwat` | mm | RAW_MODEL_OUTPUT | GFS precipitable water, entire atmosphere | 0.25 deg GFS point | only while status = CURRENT |
| `t2m_c` | degC | RAW_MODEL_OUTPUT | GFS 2 m temperature | 0.25 deg GFS point | only while status = CURRENT |
| `d2m_c` | degC | RAW_MODEL_OUTPUT | GFS 2 m dew point | 0.25 deg GFS point | only while status = CURRENT |
| `rh2m` | % | RAW_MODEL_OUTPUT | GFS 2 m relative humidity | 0.25 deg GFS point | only while status = CURRENT |
| `ws10` | m/s | DERIVED_PHYSICAL | sqrt(u10^2+v10^2) of GFS 10 m wind | 0.25 deg GFS point | only while status = CURRENT |
| `wd10` | deg | DERIVED_PHYSICAL | meteorological direction the 10 m wind blows FROM | 0.25 deg GFS point | only while status = CURRENT |
| `mslp_hpa` | hPa | RAW_MODEL_OUTPUT | GFS mean sea-level pressure (reduced; not station pressure) | 0.25 deg GFS point | only while status = CURRENT |
| `k_index` | degC | DERIVED_PHYSICAL | George K-index from GFS T and RH at 850/700/500 hPa | 0.25 deg GFS point | only while status = CURRENT |
| `total_totals` | degC | DERIVED_PHYSICAL | Total Totals from GFS T and RH at 850/500 hPa | 0.25 deg GFS point | only while status = CURRENT |
| `lapse_850_500` | K/km | DERIVED_PHYSICAL | (T850-T500)/3.5 km; approximate layer depth | 0.25 deg GFS point | only while status = CURRENT |
| `shear_850_500` | m/s | DERIVED_PHYSICAL | |V500-V850| bulk wind difference. NOT 0-6 km shear | 0.25 deg GFS point | only while status = CURRENT |
| `w850` | Pa/s | RAW_MODEL_OUTPUT | GFS vertical velocity 850 hPa (negative = ascent) | 0.25 deg GFS point | only while status = CURRENT |
| `w700` | Pa/s | RAW_MODEL_OUTPUT | GFS vertical velocity 700 hPa (negative = ascent) | 0.25 deg GFS point | only while status = CURRENT |
| `w500` | Pa/s | RAW_MODEL_OUTPUT | GFS vertical velocity 500 hPa (negative = ascent) | 0.25 deg GFS point | only while status = CURRENT |
| `rh850` | % | RAW_MODEL_OUTPUT | GFS RH 850 hPa | 0.25 deg GFS point | only while status = CURRENT |
| `rh700` | % | RAW_MODEL_OUTPUT | GFS RH 700 hPa | 0.25 deg GFS point | only while status = CURRENT |
| `rh500` | % | RAW_MODEL_OUTPUT | GFS RH 500 hPa | 0.25 deg GFS point | only while status = CURRENT |
| `soilw0_10` | m3/m3 | MODEL_DIAGNOSTIC | GFS volumetric soil moisture 0-10 cm (land-surface model state, not observed) | 0.25 deg GFS point | only while status = CURRENT |
| `apcp_cum` | mm | RAW_MODEL_OUTPUT | GFS accumulated precipitation from init to valid time (window verified from GRIB: start step 0) | 0.25 deg GFS point | only while status = CURRENT |
| `conv850` | 1e-5 1/s | DERIVED_PHYSICAL | 850 hPa horizontal convergence = -div(V) from GFS winds by centred differences on the 0.25 deg grid (positive = convergence) | 0.25 deg GFS point | only while status = CURRENT |
| `conv10` | 1e-5 1/s | DERIVED_PHYSICAL | 10 m horizontal convergence = -div(V10) by centred differences (positive = convergence) | 0.25 deg GFS point | only while status = CURRENT |
| `pwat_grad` | mm/100 km | DERIVED_PHYSICAL | magnitude of the horizontal gradient of GFS precipitable water (moisture contrast) | 0.25 deg GFS point | only while status = CURRENT |
| `q850` | g/kg | DERIVED_PHYSICAL | specific humidity at 850 hPa from GFS T and RH (Bolton saturation vapour pressure) | 0.25 deg GFS point | only while status = CURRENT |
| `q700` | g/kg | DERIVED_PHYSICAL | specific humidity at 700 hPa from GFS T and RH | 0.25 deg GFS point | only while status = CURRENT |
| `precip_3h` | mm | DERIVED_PHYSICAL | apcp_cum(f) - apcp_cum(f-3); the first hour equals apcp_cum | 0.25 deg GFS point | only while status = CURRENT |

Static layers (not forecasts): elevation / relief / slope (AWS Terrain Tiles, SRTM-derived, ~1.2 km pixels, cell statistics); nearest INDOFLOODS gauge, its distance and recorded flood events (106 gauges, historical, nulled beyond 100 km). Daily heavy-rain-day outlook: `panindia_cb_v1`, **1 deg** cells, uncalibrated score. Model outputs: see section 4. None of the values is an observation; none except the model outputs is a probability; no heuristic is presented as one.

**Provenance of each artifact**

| Time | Per forecast hour |
|---|---|
| f3 valid 2026-11-15T03:00:00Z | published (S3 Last-Modified) 2026-11-15T03:46:26Z · acquired 2026-11-15T14:26:20Z · APCP windows [[0, 3]] |
| f6 valid 2026-11-15T06:00:00Z | published (S3 Last-Modified) 2026-11-15T03:32:47Z · acquired 2026-11-15T14:26:30Z · APCP windows [[0, 6]] |
| f9 valid 2026-11-15T09:00:00Z | published (S3 Last-Modified) 2026-11-15T03:33:28Z · acquired 2026-11-15T14:26:40Z · APCP windows [[6, 9], [0, 9]] |
| f12 valid 2026-11-15T12:00:00Z | published (S3 Last-Modified) 2026-11-15T03:34:30Z · acquired 2026-11-15T14:26:53Z · APCP windows [[6, 12], [0, 12]] |
| f15 valid 2026-11-15T15:00:00Z | published (S3 Last-Modified) 2026-11-15T03:35:32Z · acquired 2026-11-15T14:27:03Z · APCP windows [[12, 15], [0, 15]] |
| f18 valid 2026-11-15T18:00:00Z | published (S3 Last-Modified) 2026-11-15T03:50:32Z · acquired 2026-11-15T14:27:13Z · APCP windows [[12, 18], [0, 18]] |
| f21 valid 2026-11-15T21:00:00Z | published (S3 Last-Modified) 2026-11-15T03:36:36Z · acquired 2026-11-15T14:27:25Z · APCP windows [[18, 21], [0, 21]] |
| f24 valid 2026-11-16T00:00:00Z | published (S3 Last-Modified) 2026-11-15T03:37:37Z · acquired 2026-11-15T14:27:35Z · APCP windows [[18, 24], [0, 24]] |

Operational lead time: **unavailable** - DRIFT records no time at which a forecast was made available to a user; the S3 time is the availability of the model input only.

## 2. Coverage matrix

| State / UT | cells | GFS fields | soil moisture (model) | terrain | gauge < 100 km | heavy-rain-day (1 deg) | TS model | short-duration rain model |
|---|---|---|---|---|---|---|---|---|
| Andaman & Nicobar Island | 8 | 8 | 3 | 8 | 0 | 8 | 8 | 8 |
| Andhra Pradesh | 220 | 220 | 217 | 220 | 116 | 220 | 220 | 220 |
| Arunanchal Pradesh | 120 | 120 | 120 | 120 | 0 | 120 | 120 | 120 |
| Assam | 116 | 116 | 116 | 116 | 17 | 116 | 116 | 116 |
| Bihar | 134 | 134 | 134 | 134 | 99 | 134 | 134 | 134 |
| Chandigarh | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Chhattisgarh | 189 | 189 | 189 | 189 | 34 | 189 | 189 | 189 |
| Dadara & Nagar Havelli | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Goa | 5 | 5 | 5 | 5 | 3 | 5 | 5 | 5 |
| Gujarat | 264 | 264 | 231 | 264 | 85 | 264 | 264 | 264 |
| Haryana | 65 | 65 | 65 | 65 | 42 | 65 | 65 | 65 |
| Himachal Pradesh | 84 | 84 | 83 | 84 | 13 | 84 | 84 | 84 |
| Jammu & Kashmir | 352 | 352 | 352 | 352 | 0 | 352 | 352 | 352 |
| Jharkhand | 115 | 115 | 115 | 115 | 35 | 115 | 115 | 115 |
| Karnataka | 253 | 253 | 253 | 253 | 232 | 253 | 253 | 253 |
| Kerala | 49 | 49 | 49 | 49 | 49 | 49 | 49 | 49 |
| Madhya Pradesh | 438 | 438 | 438 | 438 | 329 | 438 | 438 | 438 |
| Maharashtra | 421 | 421 | 420 | 421 | 338 | 421 | 421 | 421 |
| Manipur | 32 | 32 | 32 | 32 | 0 | 32 | 32 | 32 |
| Meghalaya | 33 | 33 | 33 | 33 | 4 | 33 | 33 | 33 |
| Mizoram | 28 | 28 | 28 | 28 | 2 | 28 | 28 | 28 |
| NCT of Delhi | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Nagaland | 23 | 23 | 23 | 23 | 0 | 23 | 23 | 23 |
| Odisha | 214 | 214 | 212 | 214 | 156 | 214 | 214 | 214 |
| Puducherry | 2 | 2 | 2 | 2 | 0 | 2 | 2 | 2 |
| Punjab | 76 | 76 | 76 | 76 | 2 | 76 | 76 | 76 |
| Rajasthan | 499 | 499 | 499 | 499 | 159 | 499 | 499 | 499 |
| Sikkim | 11 | 11 | 11 | 11 | 3 | 11 | 11 | 11 |
| Tamil Nadu | 174 | 174 | 169 | 174 | 140 | 174 | 174 | 174 |
| Telangana | 157 | 157 | 157 | 157 | 130 | 157 | 157 | 157 |
| Tripura | 15 | 15 | 15 | 15 | 15 | 15 | 15 | 15 |
| Uttar Pradesh | 341 | 341 | 341 | 341 | 248 | 341 | 341 | 341 |
| Uttarakhand | 79 | 79 | 79 | 79 | 71 | 79 | 79 | 79 |
| West Bengal | 121 | 121 | 118 | 121 | 110 | 121 | 121 | 121 |

| Hazard | Inputs | Model / prediction status | Lead-specific | Validated for alerting |
|---|---|---|---|---|
| Thunderstorm | GFS CAPE/CIN/LI/K/TT/PWAT/shear/omega at 0.25 deg | pan-India METAR-trained model, leads [6, 12, 18, 24] h (evaluated: temporal + leave-region-out) | True | no |
| Extreme rainfall | GFS precipitation (verified accumulation windows), 3 h amounts, neighbourhood extent | heavy-rain-day: panindia_cb_v1 daily outlook (1 deg, uncalibrated); short-duration heavy: CMORPH-labelled 3 h model, targets ['20mm_f9'] (evaluated); extreme short-duration: UNAVAILABLE; cloudburst: UNAVAILABLE | True | no |
| Flash flood | GFS precipitation + GFS soil-moisture model state + SRTM slope/relief + 106 gauges (historical events) | prediction UNAVAILABLE; heuristic screening index (unvalidated) | no | no |

Cells with no gauge within 100 km have no hydrological context; soil moisture is a GFS land-surface model state (missing over water pixels); wind shear is the 850-500 hPa bulk difference, not 0-6 km shear.

## 3. Location acceptance table (different locations -> different underlying data)

Selected forecast hour f15 (valid 2026-11-15T15:00:00Z), clock 15:25Z.

| Region | Location | Cell | State / district | Elev m | CAPE | CIN | PWAT mm | T2m C | RH % | K-index | Shear m/s | Rain 3h mm | Soil m3/m3 | TS p (+6h) | FF screen |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| North | Delhi | `IN025_2875_07725` | NCT of Delhi / North East | 214 | 0 | 0 | 16.7 | 26.6 | 27 | 19.3 | 16.6 | 0.0 | 0.239 | 0.072 | 0.001 |
| North | Srinagar | `IN025_3400_07475` | Jammu & Kashmir / Srinagar | 1715 | 0 | 0 | 7.6 | 8.1 | 51 | 16.9 | 5.2 | 0.0 | 0.193 | 0.022 | 0.004 |
| North | Chandigarh | `IN025_3075_07675` | Chandigarh / Chandigarh | 352 | 0 | 0 | 12.6 | 20.2 | 27 | 20.7 | 9.6 | 0.0 | 0.065 | 0.022 | 0.001 |
| South | Bengaluru | `IN025_1300_07750` | Karnataka / Bangalore | 871 | 0 | 0 | 20.8 | 20.8 | 64 | 12.6 | 5.6 | 0.0 | 0.239 | 0.000 | 0.0 |
| South | Chennai | `IN025_1300_08025` | Tamil Nadu / Chennai | 1 | 123 | -35 | 57.7 | 26.6 | 76 | 36.4 | 5.2 | 0.1 | n/a | 0.003 | 0.002 |
| South | Thiruvananthapuram | `IN025_0850_07700` | Kerala / Thiruvananthapuram | 52 | 258 | -6 | 52.6 | 24.6 | 88 | 24.7 | 8.0 | 0.6 | 0.282 | 0.022 | 0.019 |
| East | Kolkata | `IN025_2250_08825` | West Bengal / South Twenty Four Parganas | 7 | 0 | 0 | 23.5 | 26.4 | 47 | -3.2 | 16.2 | 0.0 | 0.240 | 0.000 | 0.0 |
| East | Bhubaneswar | `IN025_2025_08575` | Odisha / Khordha | 47 | 0 | 0 | 22.3 | 21.9 | 80 | -12.3 | 12.7 | 0.0 | 0.198 | 0.000 | 0.0 |
| East | Patna | `IN025_2550_08525` | Bihar / Patna | 52 | 0 | 0 | 25.9 | 24.7 | 50 | 13.0 | 20.6 | 0.0 | 0.188 | 0.000 | 0.0 |
| West | Mumbai | `IN025_1900_07300` | Maharashtra / Thane | 23 | 1 | -3 | 21.1 | 26.6 | 60 | -12.7 | 11.7 | 0.0 | 0.078 | 0.000 | 0.0 |
| West | Ahmedabad | `IN025_2300_07250` | Gujarat / Ahmadabad | 43 | 0 | 0 | 28.1 | 28.2 | 33 | 18.0 | 19.7 | 0.0 | 0.178 | 0.000 | 0.0 |
| West | Jaisalmer | `IN025_2700_07100` | Rajasthan / Jaisalmer | 203 | 0 | 0 | 11.7 | 26.2 | 23 | -13.8 | 13.3 | 0.0 | 0.106 | 0.000 | 0.0 |
| Central | Bhopal | `IN025_2325_07750` | Madhya Pradesh / Bhopal | 482 | 0 | 0 | 21.2 | 22.1 | 40 | 20.0 | 20.2 | 0.0 | 0.120 | 0.000 | 0.0 |
| Central | Nagpur | `IN025_2125_07900` | Maharashtra / Nagpur | 320 | 0 | 0 | 16.9 | 22.5 | 41 | -12.5 | 13.9 | 0.0 | 0.132 | 0.000 | 0.0 |
| Central | Raipur | `IN025_2125_08175` | Chhattisgarh / Raipur | 290 | 0 | 0 | 17.9 | 22.7 | 52 | -12.7 | 15.9 | 0.0 | 0.177 | 0.000 | 0.0 |
| Northeast | Guwahati | `IN025_2625_09175` | Assam / Kamrup | 67 | 40 | -34 | 35.3 | 22.2 | 71 | 27.4 | 31.2 | 0.3 | 0.136 | 0.009 | 0.012 |
| Northeast | Imphal | `IN025_2475_09400` | Manipur / Kangpokpi | 825 | 0 | -2 | 20.5 | 13.6 | 91 | 17.5 | 27.0 | 0.0 | 0.197 | 0.002 | 0.0 |
| Northeast | Shillong | `IN025_2550_09200` | Meghalaya / East Khasi Hills | 1358 | 2 | -7 | 17.0 | 12.9 | 91 | 21.6 | 27.6 | 0.1 | 0.228 | 0.002 | 0.01 |
| rural | Rajasthan desert | `IN025_2600_07400` | Rajasthan / Pali | 364 | 0 | 0 | 15.8 | 22.5 | 34 | 11.1 | 13.1 | 0.0 | 0.081 | 0.000 | 0.0 |
| rural | Western Ghats | `IN025_1100_07650` | Kerala / Palakkad | 446 | 9 | -30 | 41.6 | 22.0 | 90 | 26.7 | 8.9 | 0.1 | 0.257 | 0.002 | 0.007 |
| rural | Himalaya | `IN025_3050_07900` | Uttarakhand / Rudraprayag | 2031 | 0 | 0 | 10.6 | 4.7 | 78 | 25.9 | 10.0 | 0.5 | 0.210 | 0.108 | 0.048 |
| rural | Chhattisgarh forest | `IN025_2050_08200` | Chhattisgarh / Dhamtari | 406 | 0 | 0 | 14.6 | 18.7 | 74 | -15.0 | 12.6 | 0.0 | 0.211 | 0.000 | 0.0 |
| rural | Sundarbans edge | `IN025_2200_08900` | West Bengal / South Twenty Four Parganas | 4 | 0 | 0 | 24.6 | 23.3 | 69 | -5.1 | 15.5 | 0.0 | 0.198 | 0.000 | 0.0 |
| rural | Deccan plateau | `IN025_1650_07700` | Karnataka / Raichur | 378 | 0 | 0 | 15.7 | 25.7 | 30 | -12.6 | 4.7 | 0.0 | 0.129 | 0.000 | 0.0 |

## 4. Model evaluation (reproducible: `pipelines/feature_engineering/build_thunderstorm_training_dataset.py` -> `models/thunderstorm/train_and_evaluate_thunderstorm_model.py`; `pipelines/feature_engineering/build_rainfall_training_dataset.py` -> `models/extreme_rainfall/train_and_evaluate_extreme_rainfall_model.py`)

### Thunderstorm (pan-India, lead-specific)

Data: {"source_labels": "IEM METAR present weather (TS or VCTS within +-1.5 h of valid time; windows with <2 reports are unlabelled)", "source_predictors": "GFS 0.25 deg, NOAA AWS archive, nearest grid point to the station", "cycles": "00Z and 12Z", "n_rows": 19994, "n_stations": 113, "n_init_dates": 43, "years": [2024, 2025], "stations_by_region": {"CENTRAL": 10, "EAST": 12, "NORTH": 27, "NORTHEAST": 12, "SOUTH": 34, "WEST": 18}, "event_rate": 0.0654}

Validation: {"A": "train 2024 -> test 2025, all regions", "B": "leave-one-region-out within 2024 training -> test the held-out region in 2025", "leakage_controls": ["test year and test region never enter fitting", "calibration and threshold from date-grouped out-of-fold TRAIN predictions", "fixed hyperparameters, no tuning on test", "one model per lead"]}

| Target | n | events | AUROC | PR-AUC (no skill) | Brier | Brier skill vs clim. | POD | FAR | CSI | HSS | leave-region-out AUROC / BSS | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 6 | 2290 | 127 | 0.8173 | 0.1797 (0.0555) | 0.0479 | 0.0905 | 0.4961 | 0.7934 | 0.1707 | 0.2315 | 0.8052 / 0.0905 | PUBLISHED |
| 12 | 2435 | 241 | 0.7412 | 0.231 (0.099) | 0.0833 | 0.0724 | 0.444 | 0.7397 | 0.1963 | 0.2324 | 0.7381 / 0.0645 | PUBLISHED |
| 18 | 2360 | 124 | 0.8429 | 0.1807 (0.0525) | 0.0455 | 0.0926 | 0.5726 | 0.7874 | 0.1835 | 0.2528 | 0.8343 / 0.0773 | PUBLISHED |
| 24 | 2438 | 231 | 0.7424 | 0.2101 (0.0947) | 0.0801 | 0.07 | 0.4069 | 0.7596 | 0.178 | 0.2079 | 0.7054 / 0.0451 | PUBLISHED |

Limits: labels are airport METAR reports: urban/plain-biased, not point-in-cell truth for rural or mountain cells; pilot sample (every 12th-3rd day of Apr-Jun, two seasons): wide uncertainty; pre-monsoon season only; lead and time-of-day are confounded because only 00Z/12Z cycles are used; probabilities are calibrated to station reports, not to a generic 0.25 deg cell

Reliability (first published target, 2025 holdout; mean forecast vs observed frequency): [{"mean_forecast": 0.0, "observed_frequency": 0.0, "n": 349}, {"mean_forecast": 0.0042, "observed_frequency": 0.0103, "n": 387}, {"mean_forecast": 0.0083, "observed_frequency": 0.014, "n": 285}, {"mean_forecast": 0.0178, "observed_frequency": 0.0315, "n": 381}, {"mean_forecast": 0.0379, "observed_frequency": 0.0608, "n": 263}, {"mean_forecast": 0.1001, "observed_frequency": 0.0862, "n": 325}, {"mean_forecast": 0.18, "observed_frequency": 0.21, "n": 300}]

### Short-duration heavy rainfall (3 h, CMORPH-labelled)

Data: {"labels": "NOAA CDR CMORPH V1.0 hourly 0.25 deg, 3 h accumulation ending at the valid time (satellite estimate, gauge-adjusted over land)", "predictors": "GFS 0.25 deg init 00Z, leads 9/15/21 h, cell centre = GFS grid point", "n_rows": 445728, "n_init_dates": 32, "years": [2024, 2025], "season": "monsoon, 15 Jun - 15 Sep, every 6th day"}

Validation: "3 h CMORPH rainfall at the 0.25 deg cell >= threshold (short-duration heavy rainfall; NOT cloudburst)"

| Target | n | events | AUROC | PR-AUC (no skill) | Brier | Brier skill vs clim. | POD | FAR | CSI | HSS | leave-region-out AUROC / BSS | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20mm_f9 | 74288 | 650 | 0.8936 | 0.0944 (0.0087) | 0.0082 | 0.0519 | 0.22 | 0.8333 | 0.1048 | 0.1815 | 0.8663 / 0.0272 | PUBLISHED |
| 20mm_f15 | 74288 | 256 | 0.8116 | 0.017 (0.0034) | 0.0034 | 0.0051 | 0.0312 | 0.9279 | 0.0223 | 0.0416 | 0.793 / 0.0023 | WITHHELD |
| 20mm_f21 | 74288 | 888 | 0.819 | 0.051 (0.012) | 0.0115 | 0.0237 | 0.0957 | 0.9124 | 0.0479 | 0.08 | 0.8019 / 0.0088 | WITHHELD |
| 40mm_f9 | - | - | insufficient events | | | | | | | | | withheld |
| 40mm_f15 | - | - | insufficient events | | | | | | | | | withheld |
| 40mm_f21 | 74288 | 156 | 0.8122 | 0.0091 (0.0021) | 0.0021 | 0.0032 | 0.0705 | 0.965 | 0.024 | 0.0441 | 0.7759 / -0.0033 | WITHHELD |

Limits: CMORPH is a satellite estimate; heavy convective rainfall in mountains is under-estimated; monsoon only, two seasons, every 6th day: wide uncertainty; leads 9/15/21 h from 00Z only; 0.25 deg / 3 h cannot represent cloudbursts (>= 100 mm/h over ~10 km)

Reliability (first published target, 2025 holdout; mean forecast vs observed frequency): [{"mean_forecast": 0.0, "observed_frequency": 0.0, "n": 10619}, {"mean_forecast": 0.0001, "observed_frequency": 0.0005, "n": 16838}, {"mean_forecast": 0.0002, "observed_frequency": 0.0013, "n": 4632}, {"mean_forecast": 0.001, "observed_frequency": 0.0017, "n": 13803}, {"mean_forecast": 0.0055, "observed_frequency": 0.0047, "n": 7583}, {"mean_forecast": 0.0069, "observed_frequency": 0.0089, "n": 7524}, {"mean_forecast": 0.0266, "observed_frequency": 0.0383, "n": 13289}]

## 5. What is not available (and what it needs)

- **Cloudburst / sub-hourly extreme rainfall**: needs observed >=100 mm/h events and sub-hourly rainfall at ~10 km (e.g. dense AWS/ARG gauge or radar archives). CMORPH 0.25 deg / 3 h cannot verify them.
- **Flash-flood prediction**: needs time-stamped observed flash-flood events (location, onset), observed soil moisture and live river levels, drainage/catchment hydrology beyond 106 gauges, then a validated model.
- **Operational 2-6 h warnings**: needs a recorded publication time for DRIFT's own forecasts and an approved alert policy (registries are empty by decision).
- **0-6 km wind shear, observed radar/satellite grid fields, per-cell observations**: not integrated.
