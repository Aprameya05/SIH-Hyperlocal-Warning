<div align="center">
<img src="https://img.shields.io/badge/Smart%20India%20Hackathon-2024-orange?style=for-the-badge" />

# Hyperlocal Weather Warning System

**Operational AI Nowcasting for Bengaluru Airport (VOBL)**

Station 43295 · Kempegowda International Airport · Bengaluru, India

<p>
  <img src="https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/XGBoost-v6%20Temporal%20%2B%20Himawari-success" />
  <img src="https://img.shields.io/badge/Himawari--9-Band%2013%20IR-blueviolet" />
  <img src="https://img.shields.io/badge/Cloudflare%20Pages-Live-F38020?logo=cloudflare&logoColor=white" />
  <img src="https://img.shields.io/badge/GitHub%20Actions-5--cron%20CI%2FCD-2088FF?logo=githubactions&logoColor=white" />
  <img src="https://img.shields.io/badge/SHAP-Real--Time%20Explainability-red" />
  <img src="https://img.shields.io/badge/Cloudburst%20%2B%20Flash%20Flood-Hazard%20Models-orange" />
  <img src="https://img.shields.io/badge/Status-Operational%20%E2%9C%85-brightgreen" />
</p>

**[Live Dashboard](https://csir-thunderstorm-bengaluru.pages.dev)** &nbsp;·&nbsp; **[Live API](https://csir-thunderstorm-api.onrender.com)** &nbsp;·&nbsp; **[API Docs](https://csir-thunderstorm-api.onrender.com/docs)**

</div>

---

## What This Is

This is a fully operational AI system that predicts thunderstorm, cloudburst, and flash flood probability at Bengaluru Airport (VOBL) across four 6-hour windows every day. A 5-cron GitHub Actions pipeline pulls real-time atmospheric data, runs XGBoost inference, computes SHAP explanations, sends WhatsApp alerts to subscribers, and deploys a public dashboard on Cloudflare Pages, all without any human intervention.

The goal is a hyperlocal nowcasting product that airport operations and disaster management authorities can use to act early on severe weather events.

What makes this different from a typical ML project: it runs in production today, not just in a notebook. It ingests four live data sources on every pipeline run. The model explains every prediction in real-time using SHAP. It handles missing data, stale sources, and fallback chains gracefully. Verification metrics are computed daily against actual surface observations. And anyone can subscribe to WhatsApp alerts directly from the dashboard.

The system models three distinct hazards: Thunderstorm (TS), Cloudburst (CB), and Flash Flood (FF). All three run in a single pipeline execution. CB and FF probabilities are appended to every slot in forecast.json alongside the existing TS probability.

---

## Forecast Slots

The day is split into four 6-hour windows. Each slot has its own XGBoost model trained on that window's historical data.

| Slot | Window (IST) | Period | Production Model | CV AUROC | Threshold | Notes |
|:----:|:------------:|:------:|:----------------:|:--------:|:---------:|:-----:|
| 0 | 00:01 - 06:00 | Late Night | v6+v4 Ensemble | 0.8484 | 0.24 | Low event rate |
| 1 | 06:01 - 12:00 | Morning | v6 Temporal | 0.8317 | 0.15 | 30 lag features |
| **2** | **12:01 - 18:00** | **Afternoon** | **v6 Temporal** | **0.8710** | **0.16** | **Primary operational slot** |
| 3 | 18:01 - 24:00 | Evening | v6 Temporal | 0.8710 | 0.39 | High base threshold |

Slot 2 is the primary operational slot. The 1300-1800 IST window captures Bengaluru's dominant thunderstorm mechanism: solar heating of the Deccan Plateau driving afternoon convection, often triggered by orographic uplift on the eastern slopes of the Western Ghats.

**October threshold fix:** Slot 2 threshold is automatically lowered from 0.16 to 0.10 in October. SHAP analysis showed the DOY_sin feature suppresses output probabilities during the post-monsoon transition, causing systematic under-prediction. The fix restores POD from 0.379 to 0.621 on the 2015-2025 test set.

**Monsoon regime adjustment:** All thresholds are scaled dynamically by a monsoon phase factor. BREAK conditions raise thresholds by 30% to suppress false alarms during stratiform clouding periods. ACTIVE and CONVECTIVE_BURST regimes lower thresholds to catch more events. The regime is detected from real-time CAPE and K-Index each run.

---

## System Architecture

```
GitHub Actions (5 crons/day)
        |
        +-- clean_stale_data.py
        |       Guardian script. Runs first. Resets any JSON files older
        |       than their expected update window to safe placeholder values,
        |       preventing stale GFS or satellite data from silently carrying
        |       forward into a new forecast.
        |
        +-- gfs_fetcher.py
        |       NOAA NOMADS GFS 0.25 deg (anonymous, Chrome User-Agent required)
        |       - Surface: CAPE, CIN, K-Index, LI, TT, PW, T2m, Td2m
        |       - Profile: T/q/u/v at 500/700/850 hPa
        |       - Multi-hour TMP: f006, f012, f018, f024 (Tmax/Tmin)
        |       - 48h outlook: f024, f048 per-day aggregation
        |       - History: gfs_history_43295.json (CAPE tendency source)
        |       Output: gfs_realtime_43295.csv, upperair_realtime_43295.csv,
        |               gfs_multiday_43295.json (plain list, one entry per day)
        |
        +-- fetch_himawari_realtime.py
        |       Himawari-9 Band 13 (10.4 um IR) via NOAA S3 (anonymous)
        |       Falls back to JAXA P-Tree if S3 unavailable.
        |       - Downloads 3 segments covering VOBL's 50km radius box
        |       - Parsed with satpy (ahi_hsd reader) into lat/lon BT grid
        |       - Computes: min_bt_50km, cold_pixels_count, storm_detected,
        |                   nearest_pixel_dist_km, vobl_bt_celsius
        |       - bt_trend_1h: compares current frame to ~60 min prior frame
        |         from 6-frame rolling history (negative = anvil cooling)
        |       Output: himawari_realtime.json, himawari_history.json
        |
        +-- forecast_action.py
        |       XGBoost inference and all downstream computation.
        |       - Monsoon regime detection (R1-R5 rule-based on CAPE/KI)
        |       - Regime-aware threshold adjustment per slot
        |       - CAPE tendency from gfs_history_43295.json (dCAPE/dt J/kg/h)
        |       - 84-feature vector: base obs + 30 temporal lags + derived fields
        |       - Historical analog search across 2015-2025 training data
        |       - Convective initiation score (CAPE + KI + LI + TT composite)
        |       - 48h multi-day outlook with per-day instability scores
        |       - Airport impact: disrupted departures estimate per slot
        |       - Pipeline health tracker (data freshness per source)
        |       - METAR TS override: live METAR confirms storm -> forces alert
        |         and floors slot probabilities to 0.85
        |       - SIGMET bulletin: auto-generates ICAO-format advisory text
        |         when any slot exceeds threshold
        |       - CB/FF scoring: reuses per-slot feature vectors from the TS
        |         loop to score cloudburst and flash flood models without
        |         rebuilding the feature vector a second time
        |       Output: forecast.json
        |
        +-- fetch_metar.py
        |       aviationweather.gov JSON API (VOBL with VOBG fallback)
        |       Parses: T, Td, RH, wind, visibility, sky cover, TS flag
        |       Injected into forecast.json under "metar" key
        |
        +-- compute_realtime_shap.py
        |       SHAP TreeExplainer on production model for each slot
        |       Top 12 features by absolute SHAP value
        |       Output: data/realtime_shap.json
        |
        +-- verify_today.py
        |       Compares previous-day forecast against surface observations
        |       Rolling 30-day WMO metrics: POD, FAR, CSI, HSS, Brier
        |       Output: data/verification_today.json
        |
        +-- populate_skill_scores.py
        |       Aggregates verification history into skill_scores.json
        |       Fallback chain: forecast_log.csv -> verification_report.json
        |       -> verification_today.json
        |       Output: data/skill_scores.json
        |
        +-- send_alerts.py
        |       Reads subscriber list from Cloudflare Worker API.
        |       Falls back to data/subscribers.json if Worker unreachable.
        |       Sends WhatsApp via CallMeBot for each subscriber whose
        |       threshold is exceeded. Also fires a morning digest (08-10 IST)
        |       to subscribers with daily_digest enabled.
        |
        +-- forecast.json  (committed back to main)
                Cloudflare Pages auto-deploys on every commit.
                Dashboard fetches this file directly. No backend needed.
```

Pipeline run time is approximately 90 seconds end-to-end. The Himawari step takes the longest due to downloading three satellite segments from NOAA S3.

---

## Alert System

Anyone can subscribe to WhatsApp alerts directly from the dashboard. There is no admin step required.

**How it works:**

1. Visitor enters their name, phone number, WhatsApp API key (from CallMeBot), alert threshold, and whether they want a daily morning digest
2. The dashboard posts to a Cloudflare Worker, which stores the subscriber record in Cloudflare KV
3. On each pipeline run, `send_alerts.py` fetches the subscriber list from the Worker and sends WhatsApp messages to anyone whose threshold is exceeded
4. Every alert message includes a one-click unsubscribe link that deletes the subscriber record from KV

The WhatsApp API key is a per-user key from CallMeBot, not a shared system key. Each subscriber generates their own by messaging the CallMeBot WhatsApp bot once. This keeps the system free and means no one's number is at risk if the key leaks.

**Getting a CallMeBot API key:** Send "I allow callmebot to send me messages" to +34 644 01 10 98 on WhatsApp. CallMeBot replies with your API key in a few minutes.

**Cloudflare Worker:** `https://csir-ts-alerts.aprameya-bharadwaj-05.workers.dev`

Subscriber endpoints: `POST /subscribe`, `GET /unsubscribe?token=xxx`, `GET /subscribers` (admin key required).

---

## Cron Schedule

Five GitHub Actions crons per day. GFS cycle selection follows the t+12 rule: the cycle fetched is 12 hours before the slot valid time, giving roughly a 6.5 hour posting buffer before the window opens.

| UTC Cron | IST Time | Purpose | GFS Cycle |
|:--------:|:--------:|:-------:|:---------:|
| `45 17 * * *` | 23:15 IST | Slot 0 forecast | prev-day 06Z |
| `45 23 * * *` | 05:15 IST | Slot 1 forecast | prev-day 12Z |
| `45  5 * * *` | 11:15 IST | Slot 2 forecast (primary) | prev-day 18Z |
| `15 10 * * *` | 15:45 IST | Slot 3 forecast | same-day 00Z |
| `15 16 * * *` | 21:45 IST | Dashboard refresh | same-day 00Z |

---

## Features and Model Versions

### Feature Engineering

The v5/v6 models use 84 features from four categories.

**Base meteorological features (54):** GFS surface fields (CAPE, CIN, K-Index, LI, Total Totals, PW, T2m, Td2m), ERA5-derived multi-level fields (T/q/u/v at 500/700/850 hPa), and daily surface obs (Tmax, Tmin, rainfall, sunshine hours, evaporation).

**Temporal lag features (30):** 1-day and 3-day lags on Tmax, Tmin, rainfall, and storm label. Rolling means: RF_3d, RF_7d, MAX_3d_avg, DTR_3d_avg. LABEL_lag1: whether a storm occurred in the same slot yesterday.

**Derived thermodynamic features (10):** `cape_x_kindex` (product of CAPE and K-Index, best single predictor in SHAP), `thetae_850` (equivalent potential temperature at 850 hPa), `wind_shear_500_850` and `wind_shear_700_850` (vertical shear vectors), `moisture_flux_850/700` (|wind| * specific humidity), `q_gradient_500_850` (moisture availability at mid-levels), `thickness_500_850` (thermal thickness proxy for lapse rate).

**Cyclic time encodings:** `MONTH_sin/cos`, `DOY_sin/cos`, `slot_sin/cos` to prevent discontinuities at month/year boundaries. `slot_month_clim`: per-slot monthly climatological storm rate as a base rate anchor.

### Model Version History

| Version | Training Data | Key Features | Notes |
|---------|:-------------:|:------------:|-------|
| v2 | 2015-2022 | 54 base features | Baseline calibrated |
| v3 | 2015-2022 | 54 + calibration | Isotonic calibration |
| v4 Ensemble | 2015-2023 | 54 + ensemble stacking | 3-model ensemble, Slot 0 only |
| **v5 Temporal** | **2015-2023** | **84 (+ 30 lag features)** | **Slots 1-3** |
| **v6 Temporal** | **2015-2024** | **84 + 10 derived** | **Current production, A100 trained** |
| **CB/FF v1** | **2015-2023** | **78 (shared feature list)** | **New. Cloudburst + Flash Flood slot models** |

v6 training details: 100 Optuna trials per slot on A100 GPU (`tree_method="hist", device="cuda"`). Walk-forward cross-validation (train on years before test year). F-beta=1.5 threshold tuning (recall weighted 1.5x over precision, missed storm is a safety event). October-specific class weight x2 on positive samples for Slot 2. Models saved as both `.pkl` (joblib) and `.ubj` (XGBoost Booster binary, version-stable).

Model fallback chain per slot:
```
v6 Himawari > v6 Temporal > v5 Temporal > v4 Ensemble > v3 Calibrated > v2 Calibrated > Climatology
```

CB/FF models do not have a fallback chain. If model files are missing, the pipeline logs a skip message and continues. TS forecasting is unaffected.

---

## Cloudburst and Flash Flood Models

These are new hazard models added to the pipeline. They score independently of the TS models and append `cb_probability` and `ff_probability` to every slot in forecast.json.

### What they model

**Cloudburst (CB):** Probability that rainfall in a 6-hour slot reaches or exceeds 64.5 mm at the VOBL grid cell. 64.5 mm is the 6-hourly equivalent of the 100 mm/3-hour cloudburst classification threshold used operationally in India. Using exactly 100 mm/day as the label threshold produced only 3 positive events in the 2015-2023 training window, which is not enough to train on. The 64.5 mm threshold gives 15 positive event days (60 positive rows across 4 slots).

**Flash Flood (FF):** Probability that current and antecedent rainfall conditions will cause flash flooding. The label uses a proxy because the INDOFLOODS gauge database, the only publicly available flood event dataset for India, has no stations within 200 km of VOBL. A day is labeled FF if 3-day cumulative rainfall >= 100 mm AND same-day rainfall >= 40 mm. This captures the physical mechanism: saturated antecedent conditions plus a heavy daily pulse. The 2015-2023 training set had 12 FF event days (48 positive rows). When gauge data for the VOBL region becomes available, `fetch_cb_ff_labels.py` will use it automatically; the proxy only activates when no gauges fall within the search radius.

### Architecture

Both hazards use the same architecture as the TS slot models: XGBoost binary classifier, one model per slot, trained on the same 78-feature vector. Key differences:

- Labels come from gridded daily rainfall binary files (CB) and a rainfall proxy (FF), not from METAR storm observations (TS)
- `scale_pos_weight` is computed per slot from n_neg/n_pos (~241x for CB, ~314x for FF) to handle extreme class imbalance
- Models are saved as XGBoost native JSON rather than joblib PKL, because JSON format is stable across XGBoost versions and does not embed numpy/sklearn ABI state
- Calibration is isotonic regression fitted on a held-out 20% chronological split, saved as a separate PKL per slot
- Temporal split: train on 2015-2023, test on 2024+
- GPU training attempted automatically, falls back to CPU

Alert thresholds: CB >= 0.30, FF >= 0.25. These are conservative given the small positive sample size. A higher threshold pushes recall to near zero on the 2024 test set at current class sizes.

### How CB/FF scoring fits into the pipeline

The `forecast_action.py` TS slot loop builds an `obs` feature dict for each slot during its existing run. After finishing all four TS slots, those `obs` dicts are reused by `score_hazard_slots()` to score the CB and FF models. This avoids rebuilding the 78-feature vector from scratch and adds eight model loads plus eight predict calls to each pipeline run.

```python
# Simplified view of what was added to forecast_action.py

obs_by_slot = {}   # populated during the TS slot loop

# After compute_derived() in each slot iteration:
obs_by_slot[slot_id] = obs.copy()

# After the slot loop:
cb_ff_features = json.load(open("models/cb_ff_feature_list.json"))
cb_probs = score_hazard_slots(obs_by_slot, cb_ff_features, "cb", MODELS)
ff_probs = score_hazard_slots(obs_by_slot, cb_ff_features, "ff", MODELS)

for s in slots_output:
    s["cb_probability"] = cb_probs.get(s["slot"], 0.0)
    s["ff_probability"] = ff_probs.get(s["slot"], 0.0)
```

### Training the CB/FF models

**Step 1: build labeled training data**

```bash
# Requires gridded daily rainfall .grd files in imd_rain/rain/ (2015.grd ... 2024.grd)
python fetch_cb_ff_labels.py
# Output: data/bengaluru_6hr_training_dataset_cb_ff.csv
```

This reads the rainfall binary grid files directly (no external library dependency), extracts the VOBL grid cell time series, applies CB and FF thresholds, and left-joins the labels onto the existing v4 TS training dataset. It handles the 4-byte header some year files include and skips files whose size does not match either expected format.

**Step 2: train the slot models**

```bash
python train_cb_ff_models.py
# Outputs written to models/
```

A checkpoint JSON is written after each slot completes, so a disconnection does not lose finished work.

### Gridded rainfall file format

The rainfall dataset is distributed as raw float32 binary. The grid covers 6.5-38.5 N, 66.5-100.0 E at 0.25-degree resolution (129 lat x 135 lon). Each file has shape (n_days, 129, 135), row-major. Missing values are -999.0. Some year files include a 4-byte header before the data block.

To extract the VOBL grid cell:

```python
lats = np.arange(6.5, 38.75, 0.25)
lons = np.arange(66.5, 100.25, 0.25)
lat_idx = int(np.argmin(np.abs(lats - 12.97)))  # -> 26
lon_idx = int(np.argmin(np.abs(lons - 77.58)))  # -> 44
arr = np.fromfile("2020.grd", dtype=np.float32).reshape(366, 129, 135)
vobl_series = arr[:, lat_idx, lon_idx]
vobl_series[vobl_series <= -900] = np.nan
```

---

## Real-Time Intelligence

Beyond the raw model prediction, each pipeline run generates several operational intelligence layers.

**Brightness Temperature Trend (`bt_trend_1h`):** Compares the current Himawari frame's minimum BT within 50km of VOBL against the frame from approximately 60 minutes prior. A negative trend (cooling) indicates an anvil growing toward the airport, typically a 30-60 minute precursor to surface activity. Available under `satellite.himawari9.bt_trend_1h` in forecast.json.

**CAPE Tendency:** Rate of change of CAPE between the last two GFS cycles stored in `gfs_history_43295.json`. Units are J/kg/h. A positive tendency above +50 J/kg/h is flagged as BUILDING and sets `INTSF` in the SIGMET bulletin.

**Monsoon Regime Detection:** Classifies the synoptic environment into one of five regimes using rule-based logic on real-time CAPE, K-Index, T2m, and month.

| Regime | Condition | Threshold Factor |
|:------:|:---------:|:----------------:|
| CONVECTIVE_BURST | KI >= 38, CAPE >= 800 | 0.80 |
| ACTIVE | KI >= 35, CAPE >= 300, monsoon months | 0.88 |
| ACTIVE_MODERATE | KI >= 32, CAPE >= 100, T2m >= 28 | 0.95 |
| NEUTRAL | Default | 1.00 |
| BREAK | KI < 30, CAPE < 100, monsoon months | 1.30 |

**METAR Thunderstorm Override:** If the live METAR for VOBL reports an active thunderstorm (`TS` in wx_string), the pipeline forces `alert_active=True` regardless of model output and sets all slot probabilities to a minimum of 0.85. This handles the case where a storm has already initiated but the model has not yet updated.

**SIGMET Bulletin:** When any slot exceeds its threshold, the pipeline auto-generates an ICAO-format SIGMET advisory text in `forecast.json["sigmet_bulletin"]`. Intensity is LIGHT/MODERATE/SEVERE based on peak probability. Clearly marked as advisory-only and not for operational use without meteorologist review.

---

## Verification and Skill Scores

Verification runs daily via `verify_today.py`, comparing the previous day's forecasts against surface observations. Rolling 30-day metrics are written to `forecast.json` and shown on the dashboard.

| Metric | What it measures | Target |
|--------|:----------------:|:------:|
| POD | Fraction of actual storms that were predicted | >= 0.70 |
| FAR | Fraction of predicted storms that did not occur | <= 0.35 |
| CSI | Combined hit/miss/false-alarm score | >= 0.40 |
| HSS | Skill relative to random chance | >= 0.45 |
| Brier Score | Probabilistic accuracy | <= 0.08 |
| BSS | Skill relative to climatological base rate | >= 0.10 |

---

## Data Sources

| Source | Variables | Update Lag | Auth |
|--------|-----------|:----------:|:----:|
| NOAA NOMADS (GFS 0.25 deg) | CAPE, CIN, KI, LI, TT, PW, T/q/u/v profile | ~4h after cycle | None (Chrome UA required) |
| Himawari-9 via NOAA S3 | Band 13 BT, 3 segments, 50km box | ~10 min | None |
| JAXA P-Tree | Same as above | ~15 min | None |
| aviationweather.gov | METAR: T, Td, wind, visibility, TS flag | ~1h | None |
| Gridded daily rainfall | Daily RF .grd binary (CB/FF label source) | Annual | Institutional |
| INDOFLOODS (IIT Gandhinagar) | Gauge flood event records | Annual | Public |
| Cloudflare KV | Subscriber list | Real-time | Admin key |

GFS fetch note: NOAA NOMADS returns 403 on bare Python requests. A Chrome User-Agent header is required. All GFS paths use UTC dates; IST conversion happens only at display time.

---

## Key Files

```
forecast_action.py              Main pipeline: inference and all downstream sections
gfs_fetcher.py                  GFS NOMADS fetcher (surface + profile + multiday outlook)
fetch_himawari_realtime.py      Himawari-9 BT via satpy (NOAA S3 -> JAXA fallback)
fetch_metar.py                  METAR from aviationweather.gov (VOBL + VOBG fallback)
compute_realtime_shap.py        SHAP TreeExplainer, top 12 features per slot
verify_today.py                 Daily verification (WMO metrics, 30-day rolling)
populate_skill_scores.py        Aggregates skill scores into skill_scores.json
clean_stale_data.py             Guardian: resets stale JSON files before pipeline runs
send_alerts.py                  WhatsApp alert delivery via CallMeBot + Cloudflare Worker
resave_models.py                XGBoost version-agnostic model resave (.ubj format)
train_v6_slot_models.py         v6 training script (A100/GPU, Optuna, walk-forward CV)
backtest_himawari.py            Historical Himawari BT retrieval for v6 training data
fetch_cb_ff_labels.py           Derives CB/FF labels from gridded daily rainfall files
train_cb_ff_models.py           Trains CB and FF XGBoost slot models (4 slots each)
index.html                      Dashboard (React + Tailwind, fetches forecast.json)
index_backup.html               Pre-3D-map rollback snapshot of the dashboard

worker/
  index.js                      Cloudflare Worker: subscribe/unsubscribe/list API
  wrangler.toml                 Worker config (KV namespace binding)

models/
  nowcast_slot*_xgb_v6_temporal.pkl      v6 production TS models (4 slots)
  nowcast_slot*_xgb_v5_temporal.pkl      v5 temporal, 30 lag features
  nowcast_slot0_xgb_v4_ensemble.pkl      v4 ensemble (Slot 0 only)
  nowcast_slot*_xgb_v3_calibrated.pkl    v3 fallback
  boosters/nowcast_slot*_v6.ubj          XGBoost Booster binary (version-stable)
  cb_slot_{0..3}_model.json              CB slot classifiers (XGBoost native JSON)
  cb_slot_{0..3}_calibrator.pkl          CB isotonic calibrators (pickle)
  ff_slot_{0..3}_model.json              FF slot classifiers (XGBoost native JSON)
  ff_slot_{0..3}_calibrator.pkl          FF isotonic calibrators (pickle)
  cb_ff_feature_list.json                Shared CB/FF feature column order (78 cols)
  cb_ff_training_summary.json            Metrics and metadata from last CB/FF training run
  cb_checkpoint.json                     Per-slot CB training progress
  ff_checkpoint.json                     Per-slot FF training progress

data/
  gfs_realtime_43295.csv                 Today's GFS surface + upper-air
  gfs_history_43295.json                 Per-cycle GFS history (CAPE tendency source)
  gfs_multiday_43295.json                48h outlook (plain list, one entry per day)
  himawari_realtime.json                 Latest Himawari frame + bt_trend_1h
  himawari_history.json                  Last 6 Himawari frames
  realtime_shap.json                     SHAP values per slot
  verification_today.json                Daily WMO metrics
  skill_scores.json                      Aggregated skill scores
  pipeline_health.json                   Data freshness per source
  subscribers.json                       Fallback subscriber list (if Worker unreachable)
  forecast_log.csv                       Historical forecast log
  bengaluru_6hr_training_dataset_v4.csv  TS training data
  bengaluru_6hr_training_dataset_cb_ff.csv  Full training data with CB/FF labels
  floodevents_indofloods.csv             INDOFLOODS flood event records
  catchment_characteristics_indofloods.csv  Gauge lat/lon (no gauges near VOBL)

forecast.json                            Live output committed by GitHub Actions
.github/workflows/forecast_update.yml   5-cron CI/CD pipeline
imd_rain/rain/                           Gridded daily rainfall binaries (not in repo)
```

---

## forecast.json Schema

```json
{
  "date": "2026-08-11",
  "generated_at": "2026-08-11 11:15 IST",
  "alert_active": true,
  "peak_slot": 2,
  "peak_probability": 0.41,
  "model_version": "v6_temporal_v5_temporal_v4_ensemble",
  "slots": [
    {
      "slot": 2,
      "label": "Afternoon",
      "time": "1201-1800 IST",
      "ts_probability": 0.41,
      "ts_predicted": true,
      "cb_probability": 0.09,
      "ff_probability": 0.04,
      "threshold": 0.14,
      "primary": true,
      "source": "gfs+upperair",
      "model_used": "nowcast_slot2_xgb_v6_temporal.pkl",
      "model_version": "v6_temporal",
      "raw_probability": 0.39,
      "cape": 1240.5,
      "k_index": 36.2,
      "lifted_index": -2.4,
      "totals_totals": 48.1,
      "trend": "up",
      "trend_diff": 0.045,
      "regime_adjustment": 0.88
    }
  ],
  "hazard_summary": {
    "cloudburst": {
      "max_prob": 0.09,
      "peak_slot": 2,
      "alert": false,
      "slots": {"0": 0.02, "1": 0.05, "2": 0.09, "3": 0.03},
      "threshold": 0.30
    },
    "flash_flood": {
      "max_prob": 0.04,
      "peak_slot": 2,
      "alert": false,
      "slots": {"0": 0.01, "1": 0.02, "2": 0.04, "3": 0.01},
      "threshold": 0.25
    }
  },
  "convective_initiation": {
    "instability_score": 67.4,
    "initiation_risk": "MODERATE",
    "cape_tendency_jkgh": 120.5,
    "cape_trend": "BUILDING",
    "monsoon_regime": "ACTIVE",
    "regime_thresh_factor": 0.88
  },
  "satellite": {
    "himawari9": {
      "min_bt_50km": -52.3,
      "cold_pixels_count": 47,
      "storm_detected": true,
      "bt_trend_1h": -8.4,
      "alert_level": "ORANGE",
      "available": true
    }
  },
  "sigmet_bulletin": "VCBB SIGMET X01 VALID 1115/1715 UTC ...",
  "metar_ts_override": false,
  "verification": {
    "pod": 0.714,
    "far": 0.286,
    "hss": 0.523,
    "brier": 0.0812,
    "window": "30-day"
  },
  "pipeline_health": {
    "components": {
      "gfs": {"status": "OK", "staleness": "FRESH"},
      "himawari9": {"status": "OK", "storm_detected": true},
      "metar": {"status": "OK"}
    }
  }
}
```

`cb_probability` and `ff_probability` are isotonic-calibrated values in [0, 1] and are present on every slot object. `hazard_summary` is a new top-level key. It is absent if model files are missing (the pipeline skips gracefully).

---

## Dashboard

The dashboard is a single-file React app served as a static page. No build step: Babel standalone + Tailwind CDN. The page fetches `forecast.json` from the raw GitHub URL on load, so the dashboard reflects whatever the last pipeline run committed.

**Home screen (3D map):** MapLibre GL JS with OpenFreeMap tiles renders a 3D interactive map centred on VOBL at pitch 50. A thunderstorm probability heatmap overlays the map, computed using Gaussian spatial falloff (sigma=18km from VOBL) with climatological correction factors for 13 Bengaluru neighbourhoods. The heatmap intensity is derived from the current peak probability, so it reflects the actual forecast, not a static display.

**Tabs:** Forecast, Radar Map, Models, Explainability, Regimes, Live API, Multiday, Climatology, Skill Scores, Alerts, What-If.

**Alerts tab:** Self-service subscribe form. Visitors enter their name, phone, CallMeBot API key, threshold, and digest preference. The form calls the Cloudflare Worker directly. No admin step required.

**Visual system:** Aurora background (3 CSS gradient blobs that shift to red/orange on alert), canvas particle field (70 drifting particles that turn red on alert), glassmorphism cards with backdrop blur, Framer Motion tab transitions.

**Rollback:** `index_backup.html` is the pre-3D-map snapshot. Rename it to `index.html` to revert the UI.

---

## Deployment

### Normal push

```bash
git pull origin main
git add <files>
git commit -m "type: description"
git push origin main --force-with-lease
```

### forecast.json merge conflict

```bash
git pull origin main --no-rebase
git checkout --theirs forecast.json   # auto-generated, never edit manually
git add forecast.json
git commit -m "merge: resolve forecast.json conflict"
git push origin main --force-with-lease
```

Never use bare `--force`. Always `--force-with-lease`.

### Trigger a manual pipeline run

GitHub repo -> Actions -> "Update Forecast JSON" -> Run workflow

### Cloudflare Pages

Project name: `csir-thunderstorm-bengaluru`. Deploys automatically on every commit to `main` via `cloudflare/wrangler-action@v3`. The dashboard is a static HTML file; the pipeline injects the Cloudflare Worker URL via `sed` at deploy time so the subscribe form points to the live Worker.

### Cloudflare Worker

Deployed separately from the `worker/` subfolder using `npx wrangler deploy`. Stores subscribers in Cloudflare KV (binding: `SUBSCRIBERS`). Secrets set via `wrangler secret put`: `ADMIN_KEY` (for the `/subscribers` endpoint) and `TOKEN_SECRET` (for generating unsubscribe tokens).

Required GitHub secrets: `WORKER_URL`, `WORKER_ADMIN_KEY`, `CLOUDFLARE_API_TOKEN`.

### Model resave after retraining

```bash
python resave_models.py --all
```

Saves each TS model using `Booster.save_model()` into `.ubj` format, stable across XGBoost versions. Old `.pkl` files remain for the fallback chain. CB/FF models do not need resaving; they are already in XGBoost native JSON format.

---

## Local Development

```bash
git clone https://github.com/Aprameya05/CSIR-Thunderstorm-Bengaluru.git
cd CSIR-Thunderstorm-Bengaluru
pip install -r requirements.txt

python gfs_fetcher.py
python fetch_himawari_realtime.py
python forecast_action.py
python compute_realtime_shap.py
python populate_skill_scores.py

# Open index.html in a browser
```

To also generate CB/FF labels and retrain:

```bash
# Requires imd_rain/rain/ with .grd files
python fetch_cb_ff_labels.py
python train_cb_ff_models.py
```

---

## Free-Tier Stack

The entire system runs at zero cost.

| Service | Use | Cost |
|---------|-----|:----:|
| NOAA NOMADS | GFS 0.25 deg GRIB2 | Free |
| NOAA AWS S3 | Himawari-9 HSD files (anonymous) | Free |
| JAXA P-Tree | Himawari fallback | Free |
| aviationweather.gov | METAR API | Free |
| CallMeBot | WhatsApp delivery | Free |
| Cloudflare Pages | Dashboard hosting | Free |
| Cloudflare Workers + KV | Alert subscriber backend | Free (100k req/day) |
| GitHub Actions | CI/CD (public repo) | Free |
| Render | FastAPI backend | Free (cold start ~30s) |

---

## Key Engineering Decisions

**Why XGBoost over deep learning?** LSTM and CNN architectures were tested and hit around 0.79 AUROC. XGBoost at 0.871 outperformed them across all seasons. The dataset has roughly 3,800 days of training data at a 6-8% positive rate, which is too small for deep models to generalize well.

**Why walk-forward validation?** Random k-fold leaks future information through lag features (RF_lag1, LABEL_lag1), inflating apparent AUROC by 4-6 points. Walk-forward CV trains on all years before the test year and evaluates forward, which matches how the model is actually used.

**Why F-beta=1.5 threshold tuning?** A missed thunderstorm at an airport is a safety event. A false alarm costs delay time and fuel. F-beta=1.5 weights recall 1.5x over precision in threshold selection, deliberately accepting higher FAR to improve POD.

**Why not train on ERA5 reanalysis?** ERA5 is higher quality than real-time GFS, which creates a train/serve skew. GFS-based training means the model has seen the same biases (CAPE underestimation, coarser profile resolution) it encounters at inference time.

**Why a static dashboard with no backend?** The entire dashboard state lives in `forecast.json`, committed after every pipeline run. Cloudflare Pages serves it as a static file. No database, no backend required. The Render FastAPI is optional, for programmatic access only.

**Why Cloudflare Worker for alerts?** The dashboard is a static site hosted on Cloudflare Pages. Static sites cannot write data from the browser. A Cloudflare Worker gives a lightweight API backend (free tier: 100k requests/day) that can read and write KV, without running any server.

**Why 64.5 mm as the CB threshold?** The standard cloudburst classification in India is >= 100 mm in 3 hours. At the 6-hourly temporal resolution of the training data, 64.5 mm is the closest defensible daily equivalent. Using 100 mm/day as the threshold produced only 3 positive events in the 2015-2023 window, too few to train on.

**Why XGBoost native JSON for CB/FF models and not joblib?** joblib PKL files embed the sklearn and numpy ABI versions present at training time. Running inference on a different machine or after a library upgrade can silently corrupt the loaded object. XGBoost's native JSON format serialises only the tree structure and parameters, not Python object state. It is stable across XGBoost versions.

**Why isotonic regression calibration for CB/FF?** With very few positive events, raw XGBoost probabilities tend to cluster near zero rather than reflecting actual event frequency. Isotonic regression is monotone and non-parametric, which means it does not assume a sigmoid relationship between raw score and true probability. On small positive-sample problems it outperforms Platt scaling on the Brier score.

**Why a rainfall proxy for FF labels?** The INDOFLOODS gauge database has no stations within 200 km of VOBL. The 3-day cumsum + daily threshold proxy captures the physical mechanism: saturated antecedent conditions plus a heavy daily pulse. When better gauge data becomes available, `fetch_cb_ff_labels.py` switches to using it automatically.

---

## Things to Know

- GFS NOMADS requires a Chrome User-Agent header. Bare Python requests return 403.
- All GFS date paths are in UTC. Never use IST dates in NOMADS URLs.
- `satpy` requires the `ahi_hsd` reader for Himawari HSD files.
- The v6 models were trained with DataFrame input (not numpy arrays) to preserve named feature columns. SHAP feature names will show real column names, not f0/f54.
- `forecast.json` is auto-generated. Never manually edit it. Merge conflicts should always resolve with `git checkout --theirs forecast.json`.
- `gfs_multiday_43295.json` is a plain JSON list, one dict per day. Not an object with an `outlook` key. This matters if you are reading it directly.
- `gfs_history_43295.json` grows by one entry per pipeline run and is used for CAPE tendency computation. It is not trimmed automatically.
- SHAP values are computed with TreeExplainer, not KernelExplainer. Fast enough for GitHub Actions (under 5 seconds per slot) and gives exact Shapley values for tree models.
- The `clean_stale_data.py` guardian runs as the first pipeline step. If it writes a placeholder to `gfs_multiday_43295.json`, it writes an empty list `[]`, not a dict. The `gfs_fetcher.py` write function checks for this with an `isinstance(existing, list)` guard before merging.
- The CB/FF feature list (`cb_ff_feature_list.json`) must match the feature columns present in `obs` at scoring time. If you add or remove derived features from `compute_derived()` in `forecast_action.py`, retrain the CB/FF models and regenerate the feature list.
- The `slot_label` column (string like "0001-0600") is explicitly excluded from CB/FF training features. If it ends up in `cb_ff_feature_list.json`, the pipeline will crash at inference time with a `ValueError: could not convert string to float`.
- CB/FF calibrators are saved with `pickle`, not joblib. Use `pickle.load()` to load them. Using `joblib.load()` on them will fail silently or raise.
- TS models use joblib. CB/FF XGBoost models use `model.save_model()` (JSON) and are loaded with a fresh `xgb.XGBClassifier()` followed by `model.load_model()`. Do not mix the two loading patterns.
- If the TS slot loop exits early due to a data fetch failure, `obs_by_slot` may be partially populated. The CB/FF scoring block defaults missing slots to 0.0.

---

## What Is Next

Near-term: Add a CB/FF hazard panel to the `index.html` dashboard. The `forecast.json` already has `cb_probability`, `ff_probability`, and `hazard_summary` on every slot. The dashboard needs a hazard switcher (TS / CB / FF toggle or tabs) to surface these. Remove the pan-India SVG mini-panel, which is now redundant.

Also near-term: Himawari backtest (`python backtest_himawari.py --per-slot --start 2015-07-01`) to generate per-slot BT training data for proper v6 Himawari model training. Re-calibrate v5 models on 2024-2025 data using isotonic calibration without full retraining. Add Damini lightning network feed (pending data agreement).

Medium-term: Add 2024 and 2025 rainfall data to the CB/FF training set once available, then retrain. The current CB model has 15 positive event days; more years will improve reliability substantially. If gauge data for the VOBL region becomes available, update `floodevents_indofloods.csv` and rerun `fetch_cb_ff_labels.py` to replace the proxy labels with real ones. Extend `multiday_outlook` in `forecast.json` to include CB and FF probabilities for days 1-5 from GFS extended-range output. Email alert channel alongside WhatsApp.

---

*This is a production system, not a research prototype. Everything described above runs today.*
