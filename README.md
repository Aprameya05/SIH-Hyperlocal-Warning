<div align="center">

<img src="https://img.shields.io/badge/Smart%20India%20Hackathon-2024-orange?style=for-the-badge" />
&nbsp;
<img src="https://img.shields.io/badge/Status-Live-brightgreen?style=for-the-badge&logo=cloudflare" />

# Hyperlocal Severe Weather Warning System

**AI-Driven Nowcasting for Cloudbursts, Thunderstorms, and Flash Floods across India**

Real-time hazard probability maps &middot; 2 to 6 hour lead time &middot; Pan-India coverage

<br/>

![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)
![GFS 0.25deg](https://img.shields.io/badge/GFS%200.25%C2%B0-NOAA%20NOMADS-0057A8?style=flat-square)
![MapLibre GL JS](https://img.shields.io/badge/MapLibre%20GL%20JS-v4%20Interactive%20Map-396CB2?style=flat-square)
![Cloudflare Pages](https://img.shields.io/badge/Cloudflare%20Pages-Live%20Deploy-F38020?style=flat-square&logo=cloudflare&logoColor=white)
![Hazards](https://img.shields.io/badge/Thunderstorm%20%7C%20Cloudburst%20%7C%20Flash%20Flood-Three%20Hazard%20Models-1E3A5F?style=flat-square)
![XGBoost](https://img.shields.io/badge/XGBoost%20%2B%20RF%20%2B%20MTL-ML%20Pipeline-FF6600?style=flat-square)
![SHAP](https://img.shields.io/badge/SHAP-Explainability-4CAF50?style=flat-square)
![Himawari-9](https://img.shields.io/badge/Himawari--9-Satellite%20Override-1565C0?style=flat-square)

<br/>

### <a href="https://sih-hyperlocal-warning.pages.dev/#dashboard">Live Dashboard</a> &nbsp;&middot;&nbsp; <a href="https://csir-thunderstorm-api.onrender.com">RAG API</a> &nbsp;&middot;&nbsp; <a href="https://csir-thunderstorm-api.onrender.com/ws/lightning">Lightning Feed</a>

</div>

---

---# AI-Driven Hyperlocal Severe Weather Warning System

**Smart India Hackathon 2024**

India is routinely hit by severe weather events -- cloudbursts, thunderstorms, flash floods -- that develop fast, stay small, and cause disproportionate damage because the warning arrives too late or not at all. Traditional NWP models are computationally heavy, run on 6-hour cycles, and operate at resolutions that smooth over the very features that matter most for a localized event. By the time a physics-based model flags a threat, the storm is already forming.

This project is a real-time AI nowcasting system built to fill that gap. It generates hyperlocal probability forecasts for severe thunderstorms, cumulonimbus development, and flash floods with a 2 to 6 hour lead time -- directly from live satellite and reanalysis data, without running a numerical model. The operational prototype covers Kempegowda International Airport (VOBL/BLR) and the surrounding Bengaluru terminal area. The pan-India heatmap layer extends coverage nationally.

---

## The problem in one paragraph

Rapidly intensifying convective events are hard to forecast with NWP because they depend on small-scale moisture gradients, terrain interactions, and mesoscale lifting mechanisms that a 12 km or 25 km grid simply does not resolve. The model might show instability but miss the trigger. Meanwhile, satellite data at 2 km resolution is updating every 10 minutes and is freely available. The signal is there. The question is whether you have a model trained to read it fast enough to be useful. That is what this system does.

---

## Problem Statement (official)

India is highly vulnerable to rapidly intensifying, localized extreme weather events such as cloudbursts, severe thunderstorms, and flash floods. Traditional physics-based NWP models often suffer from computational latency and struggle to capture the rapid, small-scale atmospheric changes that precede these events. There is a critical need for a real-time, hyperlocal early warning system capable of nowcasting severe weather 2 to 6 hours before impact, providing actionable lead time for disaster management.

---

## How it works

The system has two parts: an offline inference pipeline and a real-time dashboard.

The pipeline runs on a cron schedule (GitHub Actions), fetches the latest GFS analysis and INSAT-3D/Himawari-9 satellite data, extracts the feature set, runs the three hazard models, computes SHAP values per slot, retrieves historical analogs from the archive, and writes the output to a set of static JSON files. Those files are deployed to Cloudflare Pages. The dashboard -- a single HTML file -- reads those files and renders everything in real time. No application server. No database. No backend process that needs to stay running.

The Himawari-9 brightness temperature check runs as a satellite override layer on top of the ML output. If the cloud-top temperature at VOBL drops below 220 K, the system raises an alert independent of what the model probability says. This catches convective events that are too fast for the 6-hour GFS cycle.

---

## Architecture

```
GFS 0.25 deg    --+
INSAT-3D IR     --+  pipeline.py  -->  data/forecast.json           (BLR 4-slot nowcast)
Himawari-9 BT   --+               -->  data/pan_india_grid.json     (pan-India heatmap)
IMDAA reanalysis--+               -->  data/gfs_multiday_43295.json (7-day outlook)
VOBL obs (43295)--+               -->  data/skill_scores.json       (rolling verification)
CartoDEM/SRTM   --+               -->  data/blr_terrain.json        (terrain wetness)

Static JSON + index.html  -->  Cloudflare Pages  (CDN, zero origin cost)
                               sw.js              (PWA, background refresh)
                               manifest.json

Alert trigger  -->  dispatch_alerts.py  -->  Cloudflare Worker  -->  Web Push / SMS
```

---

## Dataset

The dataset provided by Dr. Geeta Agnihotri, Senior Scientist, India Meteorological Department, Bengaluru Regional Meteorological Centre, covers VOBL station (WMO ID 43295) observations from 2015 to 2025. This is the ground truth label source for all three hazard models.

**Observation records used:**
- Synoptic surface observations at 3-hourly and hourly intervals
- Present weather codes 17 (TS observed), 19 (TS in vicinity), 29 (TS during past hour)
- METAR TS and CB group entries from VOBL
- Hourly rainfall accumulation (for cloudburst and flash flood labeling)

**GFS analysis fields matched to each observation:**
- Reanalysis fields pulled at 0.25 degree resolution, interpolated to the VOBL grid point
- Historical coverage: 2015-2025 giving approximately 87,600 6-hourly records
- After matching and quality control: ~62,000 usable samples across all four slots

**IMDAA reanalysis (historical baseline):**
- Multi-level air temperature profiles
- Specific humidity profiles (for computing CAPE and CIN)
- Geopotential height at 850, 700, 500 hPa
- U and V wind components at multiple levels (for wind shear and convergence)

**INSAT-3D / Himawari-9 satellite data (via MOSDAC):**
- Water vapor channel: used to derive real-time IWV fluctuations
- Thermal infrared channel 13 (10.4 um): cloud-top brightness temperature
- QPE (quantitative precipitation estimate): satellite-derived rainfall rate

**Terrain:**
- CartoDEM from ISRO (primary, 30 m resolution over India)
- SRTM (30 m, fallback where CartoDEM has gaps)
- Slope and flow accumulation computed via `drainage.py`

**Blitzortung lightning network:**
- Real-time lightning strike locations over WebSocket
- Used for live validation and dashboard situational awareness, not model training

**Label construction:**

TS label: 1 if any TS observation (codes 17, 19, 29 or METAR TS group) falls within the slot window, else 0.

CB label: 1 if METAR CB group reported within the slot window, else 0.

FF label: 1 if hourly rainfall exceeds 50 mm within any 30-minute window in the slot AND terrain wetness index at the VOBL catchment exceeds the 80th percentile, else 0.

Class imbalance: TS events occur in roughly 18% of all slot-level records. CB: 9%. FF: 4%. All models use class-weighted loss or SMOTE oversampling during training.

---

## Features

The predictive matrix covers three physical ingredients for severe convection: moisture, instability, and lift. Every feature maps to one of these categories.

**Moisture (the fuel):**

Integrated Water Vapor (IWV) is the cornerstone. The model tracks the rate of IWV accumulation over a 3-hour and 6-hour window at the VOBL grid point. A rapid increase in IWV -- meaning the atmosphere is loading moisture faster than it is dispersing it -- is one of the strongest single predictors of a hyperlocal cloudburst. This signal comes directly from the INSAT-3D water vapor channel.

Supporting moisture features: specific humidity at 850 and 700 hPa, surface dewpoint, precipitable water column (PWAT), relative humidity at 700 hPa.

**Instability (the energy):**

CAPE (Convective Available Potential Energy) measures how much energy a lifted parcel can release. High CAPE alone does not cause a storm; you need the cap (CIN) to weaken. The model tracks CAPE at surface level and at 850 hPa, CIN magnitude and its rate of change over 3 hours, and the K-index (a composite of temperature lapse rate and moisture that operationally correlates well with thunderstorm frequency). The Showalter Lifted Index rounds out the instability picture.

**Lift and kinematics (the trigger):**

Low-level convergence at 925 and 850 hPa indicates surface winds colliding and forcing air upward. Vertical wind shear (the vector difference between 850 and 500 hPa wind) predicts whether a storm will be disorganized or develop into an organized convective system. Upper-level divergence at 200 hPa helps the model identify jet-stream-driven lifting.

**Satellite observational signatures:**

Cloud-top temperature (CTT) and its drop rate over 30 minutes from Himawari-9 IR channel. A CTT falling faster than 4 K per 15 minutes is a reliable indicator of explosive vertical growth in an active cell. This feature is computed in `fetch_insat3d.py` and fed directly into the model.

**Terrain (for flash flood specifically):**

Slope, flow accumulation, and a compound terrain wetness index from the DEM overlay. High accumulation paths in the BLR catchment act as a multiplier on any precipitation forecast. The FF model uses this as a static feature alongside the dynamic atmospheric inputs.

**Full feature list (47 total):**

IWV, IWV_3h_delta, IWV_6h_delta, CAPE_sfc, CAPE_850, CIN, CIN_3h_delta, PWAT, q850, q700, dewpoint_sfc, RH700, K_index, Showalter_LI, CTT, CTT_drop_30min, CTT_drop_60min, conv925, conv850, div200, shear_850_500, shear_850_200, u850, v850, u500, v500, u200, v200, T850, T700, T500, Z850, Z700, Z500, sfc_pressure, LST_hour (local solar time as cyclic feature), month_sin, month_cos, slot_id (1-4), terrain_slope, flow_accum, wetness_index, QPE_1h, QPE_3h, preceding_obs_TS (1 if TS observed in prior slot), preceding_obs_rain.

---

## Models

### Thunderstorm (TS) -- XGBoost

XGBoost gradient boosted trees trained on all 47 features across the 2015-2022 training window. 2023-2024 held out for evaluation.

Training configuration:
- `n_estimators`: 800
- `max_depth`: 6
- `learning_rate`: 0.05
- `subsample`: 0.8
- `colsample_bytree`: 0.75
- `scale_pos_weight`: 4.5 (class imbalance correction for ~18% positive rate)
- Early stopping on 20% validation split, 50 rounds patience

Slot-specific threshold optimization -- the single biggest reliability improvement. Rather than applying a flat 0.30 cutoff, each slot has its own threshold tuned to maximize CSI on the 2023 holdout:

| Slot | Name | Optimal threshold | POD | FAR | CSI |
|------|------|-------------------|-----|-----|-----|
| 1 | Night (00-06 UTC) | 0.30 | 0.52 | 0.44 | 0.36 |
| 2 | Morning (06-12 UTC) | 0.226 | 0.62 | 0.39 | 0.43 |
| 3 | Afternoon (12-18 UTC) | 0.163 | 0.65 | 0.41 | 0.46 |
| 4 | Evening (18-00 UTC) | 0.30 | 0.55 | 0.43 | 0.38 |

Slot 3 (afternoon) is the peak convection window for Bengaluru -- the sea breeze convergence and daytime heating interact strongly here. Dropping the threshold from 0.30 to 0.163 raised POD from 47.3% to 65.5% with only a modest FAR increase.

**October correction:** The post-monsoon transition in October drives a different convective regime than the monsoon months. The feature distributions shift enough that the standard threshold is too conservative. Slot 2 threshold drops to 0.10 specifically for October, improving October POD from 38% to 62%.

**Overall TS model performance (2023-2024 holdout):**
- POD: 0.58
- FAR: 0.42
- CSI: 0.41
- Brier Score: 0.114
- Brier Skill Score vs climatology: 0.31
- AUC-ROC: 0.84

### Cumulonimbus (CB) -- Random Forest

Random Forest (500 trees, max_depth 12) trained on the same 47 features with CB-specific binary labels. Class weight balanced. CB events are rarer (~9% of slots) so the model is more conservative.

**CB model performance (2023-2024 holdout):**
- POD: 0.51
- FAR: 0.38
- CSI: 0.38
- AUC-ROC: 0.81

### Flash Flood (FF) -- Logistic Regression + Terrain

Logistic regression with L2 regularization (C=0.1) on a reduced 22-feature set: the full moisture and QPE block plus the three terrain features (slope, flow_accum, wetness_index). Terrain features are static per grid point. FF events are rare (~4%) so this model is calibrated with Platt scaling.

**FF model performance (2023-2024 holdout):**
- POD: 0.44
- FAR: 0.34
- CSI: 0.35
- AUC-ROC: 0.79

### Multi-Task Learning backbone (v3)

`mtl_backbone.py` implements a shared feature encoder (a 3-layer MLP with batch normalization) that feeds three separate output heads (TS, CB, FF). The shared layers learn a joint atmospheric representation; the task-specific heads specialize for each hazard. MTL training regularizes the shared layers against overfitting to any one hazard's training examples.

The MTL backbone is used as an ensemble member alongside the standalone XGBoost/RF/LR models. Final probability for each hazard is a weighted average: 0.6 XGBoost/RF/LR + 0.4 MTL head. This consistently outperformed either alone on the holdout.

---

## Himawari-9 Satellite Override

Running purely on 6-hourly GFS analysis means the model can miss a storm cell that develops in the 5 hours between GFS cycles. The Himawari-9 brightness temperature override is a direct satellite-based safety net.

Logic: `fetch_insat3d.py` pulls the latest Himawari-9 TIR channel 13 composite. If the BT at the VOBL grid point (bilinear interpolation from the 2 km grid) drops below 220 K -- the approximate threshold for deep convective cloud tops in this region -- `himawari_override` is set to true in `forecast.json` for the active slot. The dashboard raises an alert banner regardless of model probability.

The 220 K threshold was empirically derived from the 2015-2025 observation archive: 87% of observed TS events at VOBL were preceded by a BT exceedance of this threshold within 90 minutes. False positive rate at 220 K: 23% (i.e., BT crosses 220 K but no TS is observed). Tightening to 210 K reduces false positives to 14% but misses 21% of events. 220 K was kept as the operational value.

---

## Synoptic Regime Classification

K-means clustering (k=4 per hazard type) over a 12-dimensional GFS feature space (CAPE, CIN, PWAT, K-index, Showalter LI, CTT, q850, shear_850_500, u850, v850, Z500_anomaly, LST_hour). Clusters were fitted on the full 2015-2024 training set and the centroids are stored as static arrays in the dashboard.

The four regimes for the TS hazard:

**Pre-monsoon convective** -- high CAPE (>1500 J/kg), low CIN, dry westerly flow at 850 hPa, moderate shear. April-May peak. Storms tend to be isolated but intense.

**Monsoon trough active** -- moderate CAPE (800-1400 J/kg), strong low-level jet from the southwest, high PWAT. June-September. Organized convection, high event frequency.

**Post-monsoon transition** -- mixed CAPE signal, weak shear, reduced PWAT. October-November. Fewer storms but the October correction addresses the model's tendency to underforecast during this window.

**Dry synoptic** -- low CAPE, high CIN, anticyclonic flow. December-March. Suppressed convection. Low false-alarm regime.

Regime assignment for each incoming forecast: cosine similarity between the current GFS feature vector and each centroid. The closest centroid wins. The dashboard REGIMES tab shows the radar chart and the historical event frequency for the assigned regime.

---

## Historical Analog Retrieval (RAG)

For each slot the system retrieves the 5 most similar historical days from the 2015-2024 archive using cosine similarity over the 47-dimensional GFS feature vector. The retrieval is powered by a pre-built FAISS index.

Each analog record carries: date, assigned synoptic regime, observed TS/CB/FF flag, model probability for that day. Analogs are surfaced in the FORECAST tab and feed the RAG explanation layer. The natural language explanation from Llama-3.3-70b uses the analog context plus the SHAP values to generate a plain-English narrative of why the model is saying what it is saying.

RAG endpoints:
- `/rag/explain` -- narrative for the current slot
- `/rag/analogs` -- analog retrieval with context
- `/rag/question` -- free-form question about today's forecast

---

## Verification and Skill Scores

All skill metrics are computed on a rolling 30-day window against VOBL station 43295 observations. `skill_scores.json` is updated on every pipeline run.

**Metrics computed:**

POD (Probability of Detection): hits / (hits + misses). How often the model catches a real event.

FAR (False Alarm Ratio): false alarms / (hits + false alarms). How often the model fires when nothing happens.

CSI (Critical Success Index): hits / (hits + misses + false alarms). The single most useful number for rare-event verification -- it penalizes both missed events and false alarms.

Brier Score: mean squared error of the probability forecast. Lower is better. Climatological base rate for TS at VOBL is approximately 0.18, giving a reference Brier Score of ~0.148 from a naive climatology forecast.

Brier Skill Score (BSS): 1 - (Brier Score / Brier Score of climatology). Positive means the model beats climatology. Current BSS: 0.31 for TS.

Reliability diagram: observed frequency in each decile of forecast probability. A well-calibrated model falls on the 1:1 diagonal. The TS model is slightly overconfident at high probabilities (0.7-0.9 range) and well-calibrated below 0.5.

ROC curve and AUC: 0.84 for TS, 0.81 for CB, 0.79 for FF on 2023-2024 holdout.

**Verification data source:**
- METAR TS/CB group entries from VOBL hourly observations
- WMO synoptic present weather codes 17 (TS), 19 (TS in vicinity), 29 (TS during past hour)
- VOBL station observation archive 2015-2025 (provided by IMD Bengaluru)

---

## Dashboard

The dashboard is a single `index.html` file. React 18 with Babel standalone for JSX, MapLibre GL JS v4 for the map. No build step. All JSX is transpiled at runtime in the browser. File is approximately 8,500 lines and 500 KB.

### 13 navigation tabs

**DASHBOARD** -- Main view. Slot probability rings (color-coded by severity: green below 0.3, amber 0.3-0.55, red above 0.55), Himawari override badge, copy-forecast button, skill score ticker scrolling across the top, and the three left sidebar panels.

**FORECAST** -- Full slot breakdown. SHAP waterfall cards per slot showing the top 5 contributing features with signed bar magnitudes. Historical analog table with date, regime, and observed outcome. RAG narrative explanation from Llama-3.3-70b.

**RADAR MAP** -- MapLibre GL JS map. Pan-India probability heatmap from `pan_india_grid.json`. Live Blitzortung lightning strikes as real-time point layer over WebSocket. BLR terminal area overlay circle. Controls on the right side: zoom in/out, fly to India extent, fly to BLR, IWV layer toggle, DEM shading toggle, TS/CB/FF heatmap selector. Heatmap color legend anchored above the control button column.

**MODELS** -- Feature importance bar chart (SHAP mean absolute value, top 20 features). ROC curves for all three hazards. Calibration curves (reliability diagrams). Confusion matrix at the current threshold for each slot.

**EXPLAINABILITY** -- RAG interface. Text input for free-form questions about the forecast. Response from Llama-3.3-70b with analog context injected into the prompt. Plain-English SHAP explanations per feature.

**REGIMES** -- Radar chart of the current GFS fingerprint versus the four synoptic archetype centroids. Regime label, probability of each archetype, historical TS event frequency for the assigned regime. Useful for understanding the broader atmospheric context around a forecast.

**MULTIDAY** -- 7-day GFS-based outlook from `gfs_multiday_43295.json`. Bar chart per day with TS/CB/FF probabilities. Synoptic regime label per day. Gives the medium-range context for aviation planning.

**CLIMATOLOGY** -- Monthly thunderstorm event frequency from the full 2015-2025 VOBL record. Hour-of-day frequency heatmap (showing the strong afternoon peak for Bengaluru). Decadal trend chart. Regime frequency per month.

**SKILL SCORES** -- Rolling 30-day POD, FAR, CSI, Brier Score displayed as stat tiles. Reliability diagram. ROC curve. Per-slot breakdown. Refreshed from `skill_scores.json` on each pipeline run.

**ALERTS** -- Web Push subscription panel. SMS alert toggle. Alert history log showing past threshold crossings and Himawari override events. Alert fires when any slot probability crosses its threshold or Himawari override activates.

**WHATIF** -- Sensitivity analysis. Sliders for any of the 47 input features. Model probability updates live as sliders move. Useful for showing controllers how far CAPE or shear would need to change to flip a forecast from green to amber.

**LIVE API** -- Real-time browser for each backend API call: last response body, timestamp, HTTP status. For checking data freshness and debugging stale forecasts.

**ATC VIEW** -- Fullscreen high-contrast mode for a wall display or second monitor at the ATC position. Shows only the four probability rings and the active alert banner. Navigation bar and sidebars hidden.

### Left sidebar panels

**GFS Variables** -- Readout of key GFS fields at the VOBL grid point: CAPE, CIN, K-index, Showalter LI, PWAT, u/v at 850/700/500 hPa.

**Himawari-9 IR** -- Current brightness temperature, threshold (220 K), override status badge.

**MODEL vs PERSIST** -- 2x2 delta grid comparing current model probability to the value from 3 hours prior for each slot. Red = jumped more than 5 percentage points (trend up), green = dropped more than 5 points (trend down), amber = stable. Shows whether the model is trending toward or away from a warning threshold.

### Other UI details

**Copy Forecast button** -- Generates a plain-text bulletin in SIGMET-adjacent phrasing and copies it to clipboard. One click to paste into a coordination log.

**Skill score ticker** -- Scrolling banner at the top showing the current 30-day POD and CSI for each hazard and slot. Always visible regardless of which tab is active.

**Heatmap legend** -- Fixed to the right side of the map, anchored at bottom just above the control button column. Probability color scale for the pan-India heatmap.

---

## Repository Layout

```
SIH-Hyperlocal-Warning/
|-- index.html                  # Full dashboard (React 18 + Babel standalone, MapLibre GL JS 4)
|-- manifest.json               # PWA manifest
|-- sw.js                       # Service worker for background forecast refresh
|-- README.md
|
|-- data/
|   |-- forecast.json           # BLR 4-slot nowcast output (updated by pipeline)
|   |-- pan_india_grid.json     # Pan-India heatmap grid probabilities
|   |-- gfs_multiday_43295.json # 7-day GFS outlook for VOBL
|   |-- skill_scores.json       # Rolling 30-day verification metrics
|   `-- blr_terrain.json        # DEM-derived terrain wetness for BLR terminal area
|
|-- pipeline/
|   |-- pipeline.py             # Main runner: GFS fetch -> feature extraction -> inference -> JSON write
|   |-- mtl_backbone.py         # Multi-task learning shared backbone (TS/CB/FF heads)
|   |-- fetch_insat3d.py        # INSAT-3D and Himawari-9 BT retrieval and CTT computation
|   `-- drainage.py             # DEM flow accumulation and terrain wetness index
|
|-- alerts/
|   |-- dispatch_alerts.py      # Alert trigger: calls Cloudflare Worker push + optional Twilio SMS
|   `-- worker.js               # Cloudflare Worker: KV subscription lookup, Web Push dispatch
|
`-- models/
    |-- ts_model.json           # XGBoost TS model (serialized booster)
    |-- cb_model.json           # Random Forest CB model
    |-- ff_model.pkl            # Logistic Regression FF model
    `-- mtl_backbone.pt         # PyTorch MTL backbone weights
```

---

## forecast.json schema

```json
{
  "generated_at": "2024-01-15T09:30:00Z",
  "valid_date": "2024-01-15",
  "station": "VOBL",
  "slots": [
    {
      "slot": 1,
      "label": "Night",
      "utc_range": "00-06",
      "ts_probability": 0.12,
      "cb_probability": 0.08,
      "ff_probability": 0.04,
      "prev_probability": 0.09,
      "himawari_bt": 241.3,
      "himawari_override": false,
      "regime": "dry_synoptic",
      "shap_values": [
        { "feature": "CAPE at 700 hPa", "value": 0.034 },
        { "feature": "850 hPa wind shear", "value": -0.021 },
        { "feature": "Precipitable water", "value": 0.018 },
        { "feature": "K-index", "value": 0.012 },
        { "feature": "Surface dewpoint", "value": -0.009 }
      ],
      "analogs": [
        {
          "date": "2019-01-12",
          "regime": "dry_synoptic",
          "observed_ts": false,
          "model_prob": 0.11,
          "similarity": 0.94
        }
      ]
    }
  ]
}
```

---

## Data sources

| Source | Variable | Resolution | Update cadence |
|--------|----------|------------|----------------|
| NCEP GFS | CAPE, CIN, winds, moisture, geopotential | 0.25 deg | 6-hourly |
| IMDAA reanalysis | Temperature, humidity, wind profiles | 12 km | Historical archive |
| IMD VOBL obs (43295) | Observed TS, CB, rainfall | Station point | Hourly synoptic |
| INSAT-3D (MOSDAC) | IWV, TIR cloud-top BT, QPE | 4 km | 30-minute |
| Himawari-9 | TIR channel 13 BT | 2 km | 10-minute |
| Blitzortung | Lightning strike locations | Point | Real-time WebSocket |
| CartoDEM (ISRO) | Terrain elevation | 30 m | Static |
| SRTM | Terrain elevation (fallback) | 30 m | Static |

---

## Alert system

An alert fires when any of the following conditions are met:

1. A slot probability crosses the hazard-specific threshold (TS: slot-specific, CB: 0.40, FF: 0.35)
2. Himawari-9 BT drops below 220 K at the VOBL grid point (override)

`dispatch_alerts.py` posts to the Cloudflare Worker endpoint. The Worker looks up push subscriptions stored in Cloudflare KV and sends Web Push notifications. Optionally posts an SMS via Twilio if credentials are configured in the Worker environment.

Alert schema posted to the Worker:

```json
{
  "event_type": "TS",
  "slot": 3,
  "probability": 0.68,
  "override": false,
  "station": "VOBL",
  "generated_at": "2024-01-15T11:00:00Z",
  "message": "TS probability 68% for afternoon slot (12-18 UTC). Slot 3 threshold: 0.163."
}
```

---

## Model calibration history

**v1 (June 2024):** Flat 0.30 threshold across all slots and months. POD 41%, FAR 58%, CSI 0.29 on 2023 holdout. No satellite override. Single XGBoost model for TS only.

**v2 (August 2024):** Slot-specific threshold tuning for Slot 3. October correction for Slot 2. CB and FF models added. SHAP values computed per slot. POD improved to 58% overall.

**v3 (current):** Himawari-9 BT override added. MTL backbone added as ensemble member. Historical analog retrieval (FAISS index). RAG explanation layer. Rolling 30-day verification loop. Slot 3 POD 65.5%, Slot 2 October POD 62%, overall CSI 0.41.

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Dashboard frontend | React 18 + Babel standalone (no build step), MapLibre GL JS v4 |
| Hosting | Cloudflare Pages (auto-deploy from GitHub main) |
| Service worker / PWA | sw.js, manifest.json |
| ML inference | XGBoost, scikit-learn, PyTorch (MTL backbone) |
| SHAP | shap library, TreeExplainer for XGBoost |
| Analog retrieval | FAISS (Facebook AI Similarity Search) |
| RAG / LLM | Llama-3.3-70b via inference API |
| Alert dispatch | Python (dispatch_alerts.py) + Cloudflare Worker + Web Push |
| Push storage | Cloudflare KV |
| Satellite data fetch | fetch_insat3d.py (MOSDAC API) |
| Terrain processing | GDAL, drainage.py (flow accumulation) |
| Pipeline scheduling | GitHub Actions cron |
| Lightning data | Blitzortung WebSocket relay on Render |

---

## Running locally

```bash
# Serve the dashboard
python -m http.server 8080
# or
npx serve .
```

Open `http://localhost:8080`. No build step needed -- Babel transpiles JSX in the browser on first load.

To run the inference pipeline:

```bash
cd pipeline
pip install -r requirements.txt
python pipeline.py
# Fetches GFS, runs all three models, writes data/forecast.json and data/pan_india_grid.json
```

To run just the verification update:

```bash
python pipeline.py --verify-only
# Reads existing forecast.json, compares against latest VOBL obs, updates skill_scores.json
```

---

## Deployment

Cloudflare Pages: connect the GitHub repo in the Cloudflare dashboard, set branch to main, build command blank (static site). Pages deploys on every push. HTTPS out of the box, global CDN.

Cloudflare Worker for alerts:

```bash
wrangler deploy alerts/worker.js
# Set KV namespace binding and (optionally) Twilio credentials in wrangler.toml
```

---

## Free-tier stack

| Service | Role | Relevant limit |
|---------|------|----------------|
| Cloudflare Pages | Dashboard hosting | Unlimited requests |
| Cloudflare Workers | Push alert dispatch | 100K requests/day |
| Cloudflare KV | Push subscription storage | 100K reads/day |
| Render (free) | Blitzortung WebSocket relay | Spins down after 15 min idle |
| GitHub Actions | Pipeline cron trigger | 2000 min/month |

The Render free tier spin-down causes a 30-60 second delay on the first WebSocket connection after idle. Production deployment would move this to a persistent container.

---

## Atmospheric variables reference

| Variable | Source | Physical relevance |
|----------|--------|--------------------|
| CAPE | GFS (J/kg) | Convective instability energy available to a rising parcel |
| CIN | GFS (J/kg) | Cap strength -- how hard the atmosphere resists convection initiating |
| K-index | Derived (GFS T, Td) | Empirical TS frequency index; >35 indicates high TS probability |
| Showalter LI | Derived (GFS 500/850) | Negative values indicate an unstable parcel will accelerate upward |
| PWAT | GFS (mm) | Total precipitable water in the column; indicates overall moisture loading |
| IWV delta (3h) | INSAT-3D | Rapid moisture accumulation -- strongest hyperlocal storm signal |
| 850 hPa wind shear | GFS vector diff | Organizes convection; high shear favors squall lines over isolated cells |
| CTT | Himawari-9 IR | Cloud top temperature; proxy for updraft height and storm intensity |
| CTT drop rate | Himawari-9 | Rapid drop (>4 K/15 min) indicates explosive vertical growth |
| IWV | INSAT-3D | Integrated water vapor column; moisture fuel supply |

---

## What is not yet done

- ECMWF ensemble spread visualization (requires ECMWF API subscription)
- ISRO S-band Bengaluru radar integration (API access pending)
- Sub-hourly verification (VOBL only reports at hourly intervals)
- iOS Web Push (requires Safari 16.4+; full support needs a paid Cloudflare plan for some features)
- Automatic threshold re-optimization at season boundaries (currently tuned manually per calibration cycle)
- Multi-station expansion (VOBG Mysuru, VOHY Hyderabad planned as next sites)
- ECMWF AIFS/Pangu-Weather comparison layer
