<div align="center">
<img src="https://img.shields.io/badge/CSIR-Research%20Project-blue?style=for-the-badge" />

# CSIR Thunderstorm Prediction System

**Operational AI Thunderstorm Nowcasting for Bengaluru Airport (VOBL)**

IMD Station 43295 · Kempegowda International Airport · Bengaluru, India

<p>
  <img src="https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/XGBoost-v6%20Temporal%20%2B%20Himawari-success" />
  <img src="https://img.shields.io/badge/Himawari--9-Band%2013%20IR-blueviolet" />
  <img src="https://img.shields.io/badge/Cloudflare%20Pages-Live-F38020?logo=cloudflare&logoColor=white" />
  <img src="https://img.shields.io/badge/GitHub%20Actions-5--cron%20CI%2FCD-2088FF?logo=githubactions&logoColor=white" />
  <img src="https://img.shields.io/badge/SHAP-Real--Time%20Explainability-red" />
  <img src="https://img.shields.io/badge/Status-Operational%20%E2%9C%85-brightgreen" />
</p>

**[Live Dashboard](https://csir-thunderstorm-bengaluru.pages.dev)** &nbsp;·&nbsp; **[Live API](https://csir-thunderstorm-api.onrender.com)** &nbsp;·&nbsp; **[API Docs](https://csir-thunderstorm-api.onrender.com/docs)**

</div>

---

## What This Is

This is a fully operational AI system that predicts thunderstorm probability at Bengaluru Airport (VOBL) across four 6-hour windows every day. A 5-cron GitHub Actions pipeline pulls real-time atmospheric data, runs XGBoost inference, computes SHAP explanations, sends WhatsApp alerts to subscribers, and deploys a public dashboard on Cloudflare Pages, all without any human intervention.

The goal is a nowcasting product that IMD and ATC can trust and eventually put their name on.

What makes this different from a typical ML project: it runs in production today, not just in a notebook. It ingests four live data sources on every pipeline run. The model explains every prediction in real-time using SHAP. It handles missing data, stale sources, and fallback chains gracefully. Verification metrics are computed daily against actual IMD observations. And anyone can subscribe to WhatsApp alerts directly from the dashboard.

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
        |       Compares previous-day forecast against IMD VOBL observations
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

**Base meteorological features (54):** GFS surface fields (CAPE, CIN, K-Index, LI, Total Totals, PW, T2m, Td2m), ERA5-derived multi-level fields (T/q/u/v at 500/700/850 hPa), and daily IMD obs (Tmax, Tmin, rainfall, sunshine hours, evaporation).

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

v6 training details: 100 Optuna trials per slot on A100 GPU (`tree_method="hist", device="cuda"`). Walk-forward cross-validation (train on years before test year). F-beta=1.5 threshold tuning (recall weighted 1.5x over precision, missed storm is a safety event). October-specific class weight x2 on positive samples for Slot 2. Models saved as both `.pkl` (joblib) and `.ubj` (XGBoost Booster binary, version-stable).

Model fallback chain per slot:
```
v6 Himawari > v6 Temporal > v5 Temporal > v4 Ensemble > v3 Calibrated > v2 Calibrated > Climatology
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

Verification runs daily via `verify_today.py`, comparing the previous day's forecasts against IMD VOBL surface observations. Rolling 30-day metrics are written to `forecast.json` and shown on the dashboard.

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
| IMD VOBL surface obs | Daily thunderstorm occurrence | Daily | Institutional |
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
verify_today.py                 Daily IMD verification (WMO metrics, 30-day rolling)
populate_skill_scores.py        Aggregates skill scores into skill_scores.json
clean_stale_data.py             Guardian: resets stale JSON files before pipeline runs
send_alerts.py                  WhatsApp alert delivery via CallMeBot + Cloudflare Worker
resave_models.py                XGBoost version-agnostic model resave (.ubj format)
train_v6_slot_models.py         v6 training script (A100/GPU, Optuna, walk-forward CV)
backtest_himawari.py            Historical Himawari BT retrieval for v6 training data
index.html                      Dashboard (React + Tailwind, fetches forecast.json)
index_backup.html               Pre-3D-map rollback snapshot of the dashboard

worker/
  index.js                      Cloudflare Worker: subscribe/unsubscribe/list API
  wrangler.toml                 Worker config (KV namespace binding)

models/
  nowcast_slot*_xgb_v6_temporal.pkl      v6 production models (4 slots)
  nowcast_slot*_xgb_v5_temporal.pkl      v5 temporal, 30 lag features
  nowcast_slot0_xgb_v4_ensemble.pkl      v4 ensemble (Slot 0 only)
  nowcast_slot*_xgb_v3_calibrated.pkl    v3 fallback
  boosters/nowcast_slot*_v6.ubj          XGBoost Booster binary (version-stable)

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

forecast.json                            Live output committed by GitHub Actions
.github/workflows/forecast_update.yml   5-cron CI/CD pipeline
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

Saves each model using `Booster.save_model()` into `.ubj` format, which is stable across XGBoost versions. Old `.pkl` files remain for the fallback chain.

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

**Why Cloudflare Worker for alerts?** The dashboard is a static site hosted on Cloudflare Pages. Static sites cannot write data from the browser. A Cloudflare Worker gives a lightweight API backend (free tier: 100k requests/day) that can read and write KV, without running any server. The alternative would have been a server on Render or Railway, which adds cold start latency and more infrastructure to maintain.

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

---

## What Is Next

Near-term: Himawari backtest (`python backtest_himawari.py --per-slot --start 2015-07-01`) to generate per-slot BT training data for proper v6 Himawari model training. Re-calibrate v5 models on 2024-2025 data using isotonic calibration without full retraining. Integrate Dr. Agnihotri's 2026 raw VOBL surface data when received. Add Damini lightning network feed (pending IMD agreement).

Medium-term: API rate limiting and authentication for IMD production deployment. Automated retraining trigger when new IMD annual data arrives. Email alert channel (currently WhatsApp only).

---

## Team

| Member | Role |
|--------|------|
| **Aprameya** | ML lead, project architect |
| **Atul Denny** | Upper-air data, GFS pipeline, Himawari satellite fetching |
| **Satvik** | FastAPI backend on Render |
| **Vidhi** | ERA5 historical data |
| **Sneha** | Verification pipeline, visualisation |

**Institutional collaborator:** Dr. Geeta Agnihotri, Scientist F, IMD Bengaluru. Agreed to share 2026 VOBL raw surface observation data and is a candidate co-author on a joint technical report.

---

*This is a production system, not a research prototype. Everything described above runs today.*
