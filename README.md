<div align="center">

# DRIFT-01

**AI-Driven Hyperlocal Early Warning System for Severe Weather Nowcasting**

Localized thunderstorm, cloudburst and flash-flood risk on a pan-India 992-cell grid, with an explicit 2 to 6 hour lead-time framework and clearly labeled production, research and blocked components.

Smart India Hackathon 2026 · Problem Statement SIH26077 · Domain: Disaster Management / Severe Weather Early Warning

</div>

---

<div align="center">

[![Overview](https://img.shields.io/badge/01--04-Overview-6B4FBB?style=flat-square)](#1-project-title-and-description)
[![Architecture](https://img.shields.io/badge/05--09-Architecture%20%26%20Data-396CB2?style=flat-square)](#5-system-architecture)
[![Models](https://img.shields.io/badge/10--14-Models-FF6600?style=flat-square)](#10-thunderstorm-model)
[![Integrations](https://img.shields.io/badge/15--20-Integrations%20%26%20Ops-1565C0?style=flat-square)](#15-satellite-imd-metar-gfs-imerg-and-other-data-integrations)
[![Results](https://img.shields.io/badge/21--24-Results%20%26%20Coverage-4CAF50?style=flat-square)](#21-validation-results-and-metrics)
[![Reference](https://img.shields.io/badge/25--29-Reproducibility%20%26%20Reference-7A7A7A?style=flat-square)](#25-reproducibility-and-data-provenance)

</div>

---

<div align="center">

### `01` Project Overview

</div>

## 1. Project title and description

DRIFT-01 is an engineering prototype for hyperlocal, short-fuse severe weather warning across India, built against SIH26077. It produces per-cell risk information for three related hazards, thunderstorm (TS), cloudburst (CB) and flash flood (FF), on a common 992-cell grid, with an explicit lead-time structure and an explicit distinction between validated model output, research-only output, and output that is currently unavailable because the underlying data or training does not yet exist.

The repository currently contains **two coexisting pipelines**: a legacy, VOBL-centric operational pipeline that also drives a pan-India heuristic layer, and a newer unified pipeline (Phase 21 / Phase 34-36) that is the intended direction for the final system. Both are documented below, separately, because they are not the same system and this README does not present them as such.

<div align="center">

### `02` Problem Statement

</div>

## 2. Problem statement

India is affected by rapidly developing, spatially localized severe weather: thunderstorms, cloudbursts and the flash floods they can trigger. Conventional regional numerical weather prediction (NWP) runs on multi-hour cycles and at resolutions that can miss small-scale hazard development, or produce information too coarse for a disaster-management user to act on locally. The engineering problem is not "predict the weather" in general. It is to produce localized, actionable warning information inside the 2 to 6 hour window, at a cell resolution fine enough to be useful, while being explicit about which parts of that prediction are backed by validated models and which are not.

Four properties shape the system:

- **Spatial localization**: a correct state-level or city-level average forecast can still miss the one cell where a hazard forms.
- **Rapid atmospheric evolution**: convective initiation and rainfall intensification can occur inside a single 6-hour NWP cycle.
- **Cascading hazards**: a thunderstorm can produce a cloudburst, which can produce a flash flood, on timescales of hours; the three are related but not identical problems.
- **Multi-physics dependence**: moisture, instability, kinematics, observed rainfall and terrain all matter, and no single variable is sufficient across all three hazards.

<div align="center">

### `03` DRIFT / SIH Objective

</div>

## 3. DRIFT / SIH objective

DRIFT-01's response is a fixed pipeline shape:

```
DATA
  -> COMMON 992-CELL INDIA GRID
    -> FEATURE / PROVENANCE FUSION
      -> HAZARD-SPECIFIC + SHARED MODEL ARCHITECTURE
        -> 2 TO 6 HOUR FORECAST INTERFACE
          -> RISK MAPS
            -> XAI
              -> ALERT / API LAYER
```

The common grid addresses spatial localization. Feature and provenance fusion addresses multi-physics dependence while keeping every input's origin (observed, forecast, reanalysis, derived, proxy or missing) explicit. Hazard-specific heads with a shared backbone are the architectural answer to cascading hazards: independent per-hazard evaluation, with a shared representation intended once training is complete. The lead-time interface, risk maps, XAI and alert/API layer address actionability directly.

<div align="center">

### `04` Key Capabilities

</div>

## 4. Key capabilities

- A canonical 992-cell grid covering mainland India, used as the single coordinate system for every data source.
- A 2 to 6 hour lead-time interface attached to every unified forecast record (lead hours 2, 3, 4, 5, 6).
- A validated thunderstorm model at the VOBL (Bengaluru) station, trained on real IMD-observed labels.
- A validated pan-India cloudburst model (`panindia_cb_v1`), trained on real IMD rainfall-derived labels and evaluated with a grouped-date (leave-one-date-out) split.
- A flash-flood research pipeline built on INDOFLOODS data, using positive-unlabeled (PU) learning because confirmed negatives do not exist for this problem, and explicitly exposed as a ranking score rather than a probability.
- A unified inference layer that never fabricates a value for a hazard that has no legitimate prediction: unavailable cases return `null` probability and `NOT_AVAILABLE` risk, never zero or LOW.
- Local explainability (SHAP) for the models that are actually trained, with an explicit "not available" state where no supported explanation method exists.
- A FastAPI backend serving both a live per-cell endpoint and a precomputed bulk endpoint.
- A dashboard with two nav paths: the legacy operational view and the newer unified 992-cell map view.
- A four-state alert delivery model that distinguishes a real failure from a correctly-skipped no-alert case.
- Scheduled, cron-driven data acquisition and Cloudflare Pages deployment.

<div align="center">

### `05` System Architecture

</div>

## 5. System architecture

```
MULTI-SOURCE DATA
GFS | IMD | METAR | Himawari-9 | SRTM/DEM | INDOFLOODS hydrology
                    |
                    v
          DATA INGESTION / QC
                    |
                    v
          COMMON 992-CELL GRID
                    |
                    v
      FEATURE + PROVENANCE FUSION
                    |
         +----------+-----------+
         |                      |
         v                      v
  LEGACY PIPELINE        UNIFIED PIPELINE
  (operational dashboard)  (Phase 21 / 34-36)
         |                      |
         v                      v
  Hand-weighted           Hazard-specific inference:
  heuristic formula       TS  -> VOBL XGBoost (station)
  (PWAT/CAPE/CTT/QPE/      CB  -> panindia_cb_v1 XGBoost
   convergence weights)          (Phase 21, grouped-date validated)
                           FF  -> PU-ranking research score
                                  (never a probability)
         |                      |
         v                      v
  forecast.json            data/unified_forecast.json
  data/pan_india_grid.json  backend/unified_api.py
         |                      |
         v                      v
   DASHBOARD / FORECAST     UNIFIED FORECAST tab
   (public site today)      (992-cell map, per-cell XAI)
```

### 5.1 Legacy / operational pipeline

`backend/pipeline.py` and `forecast_action.py` compute hazard scores per cell and write `data/pan_india_grid.json` and `forecast.json`. The pan-India TS/CB/FF scores in this path come from `backend/pipeline.py::hazard_probabilities()`, a hand-weighted linear heuristic over live GFS fields, not a trained classifier:

```python
ff_score = 0.0
if pwat is not None:
    ff_score += min(1.0, max(0.0, (pwat - 35) / 30.0)) * 0.45   # PWAT
if cape is not None:
    ff_score += min(1.0, cape / 2000.0) * 0.22                  # CAPE
if ctt_c is not None:
    ff_score += min(1.0, max(0.0, (-ctt_c - 5) / 45.0)) * 0.18  # CTT
if qpe_mm is not None and qpe_mm > 0:
    ff_score += min(1.0, qpe_mm / 30.0) * 0.10                  # QPE
if convergence is not None and convergence > 0:
    ff_score += min(1.0, convergence / 2e-4) * 0.05             # convergence
```

None of these five weights were fit to a labeled outcome. This is a legacy heuristic baseline, not the unified trained model, and is labeled as such throughout this document. Its VOBL thunderstorm component is a real trained XGBoost model; its pan-India CB/FF components are the heuristic above.

### 5.2 Unified forecast pipeline (Phase 21 / 34-36)

`backend/models/unified_mtl/inference_engine.py` performs per-hazard inference and returns a `HazardPrediction` with `probability`, `risk_category`, `status`, `model_version`, `provenance`, `confidence` and `extra`. `backend/unified_api.py` serves this over `/forecast` (live) and `/forecast/all` (the offline artifact `data/unified_forecast.json`, 992 cells x 5 lead times = 4,960 records). `UnifiedForecastPage` in `index.html` is the frontend for this path.

TS in this path uses the same VOBL station model as the legacy path. CB uses `panindia_cb_v1`. FF exposes the INDOFLOODS PU-ranking research score, never converted into a probability. This is the intended direction of the final DRIFT architecture because of its status/provenance discipline, not because its underlying models are all fully trained yet.

<div align="center">

### `06` Data Sources & Status

</div>

## 6. Data sources and current status

| Source | Role | Coverage | Status |
|---|---|---|---|
| NOAA/NCEP GFS | CAPE, CIN, wind shear, PWAT, APCP, geopotential height | Pan-India, live | Operational |
| IMD (Bengaluru station) | Thunderstorm labels, rainfall-derived cloudburst labels | VOBL for TS; pan-India rainfall for CB | Operational for the labels actually used |
| METAR (VOBL/VOBG) | Station weather observations | VOBL, VOBG | Operational |
| Himawari-9 | Cloud-top brightness temperature, Band 13 | Bengaluru crop | Limited, Band 13 only |
| NASA IMERG | Satellite-merged precipitation | One day sampled and validated; full archive not acquired | Real sample validated, limited archive coverage |
| SRTM / DEM | Elevation, slope | 353 of 992 cells | Partial |
| INDOFLOODS | Flood event history, catchment characteristics | 214 gauges mapped, 155 with catchment attributes | Research-only, not consumed by either production pipeline |
| IMDAA reanalysis | Would supply higher-resolution reanalysis fields | None | Blocked, pending NCMRWF registration |
| INSAT-3D/3DR | Would supply satellite QPE and IWV | None | Blocked, pending MOSDAC credentials |

<div align="center">

### `07` Canonical 992-Cell Grid

</div>

## 7. Canonical 992-cell India grid

`data/pan_india_common_grid_992.json` defines the canonical application grid: **992 cells** covering mainland India, used as the single coordinate system every data source is aligned to regardless of its native resolution. This grid is what makes it possible to say that a thunderstorm model, a cloudburst model and a flash-flood model are evaluating the same location. Grid mechanics (cell definitions, cell-count guardrails) are implemented and tested; what varies by hazard is how much of the grid has a validated model behind it, documented per hazard below.

<div align="center">

### `08` 2-6 Hour Forecast Framework

</div>

## 8. 2 to 6 hour forecast framework

`lead_time.py` and `backend/models/unified_mtl/lead_time_interface.py` attach explicit lead-time metadata to forecasts. In the unified pipeline, every cell is forecast at five lead hours: **2h, 3h, 4h, 5h, 6h**, giving 992 cells x 5 lead hours = **4,960 records** in `data/unified_forecast.json`. This lead-time structure is real and present for every record. A genuine, per-cell, per-hazard validated skill score across the full 2 to 6 hour window is still in progress; the lead-time framework itself should not be read as implying that every hazard has been independently validated at every lead hour.

<div align="center">

### `09` Feature Engineering

</div>

## 9. Feature engineering

- **Moisture**: PWAT, humidity, dewpoint, rainfall context. Operational PWAT is GFS-derived, not satellite-observed, and is never presented as satellite IWV.
- **Instability**: CAPE, CIN, K-index and related stability indices from GFS/ERA5.
- **Kinematics**: wind shear and wind components, computed in both pipelines.
- **Observational signatures**: cloud-top temperature from Himawari-9 Band 13 (Bengaluru-only), METAR station reports, IMERG rainfall where sampled.
- **Terrain**: elevation and slope from SRTM/DEM (353 of 992 cells), plus INDOFLOODS catchment morphometry, soil, lithology, land-cover and climate-normal attributes for 155 gauges.
- **Provenance taxonomy**: every feature and hazard output carries one of `OBSERVED`, `FORECAST`, `REANALYSIS`, `DERIVED`, `PROXY`, `MISSING`, enforced in the unified pipeline's `HazardPrediction.provenance` field.

<div align="center">

### `10` Thunderstorm Model

</div>

## 10. Thunderstorm model

A real XGBoost model trained on real IMD-observed labels at the VOBL (Bengaluru) station. Validated with a time-split (future days held out), reporting:

- **AUROC = 0.8715**
- **1,001 held-out days**
- VOBL / IMD Bengaluru station baseline

This model is station-domain only. For every cell other than VOBL, the unified pipeline returns `status = OUT_OF_DOMAIN_STATION_ONLY`, `probability = null`, `risk_category = NOT_AVAILABLE`, never an extrapolated value. Pan-India thunderstorm prediction is not implemented.

<div align="center">

### `11` Cloudburst Model

</div>

## 11. Cloudburst model

A pan-India XGBoost model, `panindia_cb_v1` (Phase 21), trained on real IMD rainfall-derived labels and evaluated with a grouped-date (leave-one-date-out) validation scheme across held-out dates, reporting:

- **Calibrated AUROC = 0.7542**
- Grouped-date / leave-one-date-out validation across held-out dates

A known limitation of this model is daily resolution: all lead-hour slots currently repeat the same daily-resolution snapshot rather than a genuine sub-daily forecast. This is stated explicitly in the model's own output metadata.

<div align="center">

### `12` Flash-Flood Research Model

</div>

## 12. Flash-flood research model

No production or research flash-flood component in this repository outputs a calibrated flood probability. The legacy pipeline's FF score is the hand-weighted heuristic in Section 5.1. The unified pipeline exposes a separate research model built from INDOFLOODS data.

- INDOFLOODS: **214 gauges** mapped, **4,548 raw flood events** joined, **620 reproducible positive labels** across **69 cells** and **131 gauges**, **155 catchment rows**.
- Training population: **144,486 rows** = **620 positive** + **143,866 unlabeled**.
- Confirmed negatives do not exist: INDOFLOODS records threshold-crossing events, and a day with no recorded event cannot be treated as a confirmed non-event, only as unlabeled. The model is trained as positive-unlabeled (PU) learning for this reason.
- Reported metric: **PU-ranking AUROC = 0.590** (research-only, positive-unlabeled ranking, not an observed flood probability).
- The unified inference engine always sets `probability = null` and `risk_category = NOT_AVAILABLE` for FF. The real PU-ranking value is exposed only in an `extra` field, labeled as a ranking score, never as a calibrated probability.
- This model is not promotable to production today: its strongest feature group (antecedent rainfall) has no real-time production feed, its features are daily-resolution which cannot support a 2 to 6 hour lead time by construction, and its output has only been validated as a relative ranking, not a calibrated probability.
- The PU-ranking AUROC must never be compared directly against the TS or CB supervised AUROC figures above. They evaluate different things against different label types.

<div align="center">

### `13` Unified Inference Architecture

</div>

## 13. Multi-hazard / unified inference architecture

`backend/models/unified_mtl/shared_backbone.py` and `unified_model.py` define a shared-representation architecture intended to let TS, CB and FF share learned features and condition on lead time and provenance. This architecture exists in code. **It has not been fully trained and validated for all three hazards.** The TS and CB models currently used in production are independent XGBoost heads, not output of this shared backbone. The unified inference engine (`inference_engine.py`) is the part of this layer that is real and in use today: it standardizes the per-hazard output contract (`probability`, `risk_category`, `status`, `model_version`, `provenance`, `confidence`, `extra`) and enforces that unavailable values are never fabricated.

<div align="center">

### `14` Terrain & Hydrology

</div>

## 14. Terrain and hydrology integration

- Terrain (SRTM/DEM elevation and slope): **353 of 992 cells**.
- Hydrology (catchment-derived features): **75 of 992 cells**.
- INDOFLOODS catchment characteristics: **155 gauge rows** with morphometry, soil, lithology, land-cover and climate-normal attributes.

Cells without DEM coverage return an explicit `MISSING` terrain state, never an interpolated value. Neither the legacy pipeline nor the unified pipeline currently consumes INDOFLOODS catchment data for anything other than the FF research model.

<div align="center">

### `15` Data Integrations

</div>

## 15. Satellite, IMD, METAR, GFS, IMERG and other data integrations

- **GFS**: the operational forecast source for both pipelines, providing CAPE, CIN, wind shear, PWAT and APCP pan-India.
- **IMD**: source of the real observed labels used for the VOBL thunderstorm model and the rainfall-derived labels used for the pan-India cloudburst model.
- **METAR**: VOBL/VOBG station observations parsed into the feature pipeline.
- **Himawari-9**: Band 13 brightness temperature, Bengaluru crop only, used for cloud-top temperature.
- **IMERG**: one day sampled and validated end to end; the full archive has not been acquired, so pan-India IMERG coverage is currently limited.
- **IMDAA**: not integrated. Blocked pending NCMRWF registration.
- **INSAT-3D/3DR**: not integrated. Blocked pending MOSDAC credentials. Not claimed live anywhere in this repository.

<div align="center">

### `16` XAI / SHAP

</div>

## 16. XAI / SHAP

SHAP-based local explainability is wired for the models that are actually trained: the VOBL thunderstorm model and the pan-India cloudburst model. For flash flood, the unified inference engine returns an explicit `NOT_AVAILABLE` XAI state with a stated reason, because no supported local-SHAP story exists for the PU-corrected logistic model used there. SHAP output is produced per prediction for the hazards that support it; it is not produced for every prediction across all three hazards, and is not claimed to be.

<div align="center">

### `17` API & Backend

</div>

## 17. API and backend

`backend/unified_api.py` (FastAPI) serves:

| Endpoint | Behavior |
|---|---|
| `GET /forecast?cell_id=&lead_hours=` | Live per-cell inference: TS, CB, FF blocks plus XAI, terrain, and data-source status. FF's `probability` is always `null`; its PU-ranking score is in an `extra` field |
| `GET /forecast/all` | Serves `data/unified_forecast.json` verbatim (4,960 records), tagged as an offline artifact rather than a live call |
| `GET /forecast/sources` | Reports the real status of every data source, including blocked and partial-coverage sources |
| `GET /forecast/alerts`, `POST /forecast/alerts/dispatch` | Alert state surface and dispatch for the unified pipeline |
| `GET /health` | Service health check |
| `/alert`, `/alerts` | Legacy alert endpoints (`backend/alerts.py`), separate from the unified alert surface |

No endpoint claims a value it does not have. Where a hazard has no legitimate prediction for a cell, the response carries `null` probability, `NOT_AVAILABLE` risk, and a `status` string explaining why, rather than omitting the field or defaulting to zero.

<div align="center">

### `18` Dashboard / Frontend

</div>

## 18. Dashboard / frontend

The dashboard is a single-file React application (`index.html`) using MapLibre GL JS for the map layer. It contains two nav paths:

- **Legacy DASHBOARD / FORECAST**: the existing VOBL-centric operational view, rendering the station-level thunderstorm nowcast and the pan-India heuristic grid. This is what the public deployment currently serves.
- **UNIFIED FORECAST**: the newer tab, rendering the 992-cell unified artifact with per-cell TS/CB/FF detail cards, explicit unavailable states, SHAP where supported, and a research-score label (not "probability") for flash flood.

Both paths exist in the repository; they are not merged into a single view.

<div align="center">

### `19` Alerting

</div>

## 19. Alerting

Every alert attempt resolves to exactly one of four states, enforced in `alert_delivery.py`:

- `SUCCESS`: delivered
- `FAILED`: attempted, did not deliver
- `NOT_CONFIGURED`: no delivery channel configured for this subscriber
- `SKIPPED_NO_ALERT`: no alert condition was met

This distinguishes a real delivery failure from a correctly-skipped no-alert case, which a simpler "sent" / "not sent" model cannot. The current delivery channel is WhatsApp via a third-party API, not SMS. Delivery is not guaranteed; `FAILED` and `NOT_CONFIGURED` are both real, reachable outcomes.

<div align="center">

### `20` Pipeline & Deployment

</div>

## 20. Automated pipeline and deployment architecture

Data acquisition and forecast generation run on scheduled GitHub Actions workflows (cron-driven, several runs per day), writing the legacy pipeline's artifacts and triggering a Cloudflare Pages deployment of the dashboard build. The unified pipeline's offline artifact (`data/unified_forecast.json`) is generated on demand by a dedicated script and is not currently written by any scheduled workflow. Deployment to the public site is tied to the scheduled workflow's run, not to every push to the main branch.

<div align="center">

### `21` Validation Results

</div>

## 21. Validation results and metrics

| Model | Domain | Evaluation | Result | Status |
|---|---|---|---|---|
| Thunderstorm | VOBL (station) | Time-split, 1,001 held-out days | AUROC 0.8715 | Validated, station-only |
| Cloudburst | Pan-India | Grouped-date / leave-one-date-out, held-out dates | Calibrated AUROC 0.7542 | Validated baseline, daily resolution |
| Flash flood | Research (INDOFLOODS PU) | Positive-unlabeled ranking | PU-ranking AUROC 0.590 | Research-only, not a supervised flood AUROC |

Each row's metric is scoped to its own evaluation method. The PU-ranking AUROC is not comparable to the two supervised AUROC figures above it.

<div align="center">

### `22` SIH Requirement Coverage

</div>

## 22. SIH problem-statement requirement coverage

DRIFT-01 addresses the architectural scope of SIH26077. The table below distinguishes requirements already demonstrated in code and data from those still dependent on external data access, additional training, or further validation. Not every requirement is fully satisfied, and this table does not claim otherwise.

| Requirement | Current implementation | Status |
|---|---|---|
| Pan-India common grid | 992-cell canonical grid | Implemented |
| 2-6h lead time | Explicit per-record lead-hour metadata, 5 lead hours in the unified artifact | Implemented, full per-cell validation in progress |
| Thunderstorm prediction | VOBL station XGBoost, AUROC 0.8715 | Validated at VOBL only, not pan-India |
| Cloudburst prediction | `panindia_cb_v1`, calibrated AUROC 0.7542 | Validated pan-India baseline |
| Flash-flood prediction | Legacy heuristic (production) and INDOFLOODS PU-ranking (research) | No calibrated flood probability exists anywhere in the repository |
| Shared multi-hazard representation (MTL) | Shared backbone architecture exists | Not trained for all three hazards |
| IMDAA reanalysis | Not integrated | Blocked, external registration required |
| INSAT-3D/3DR | Not integrated | Blocked, external credentials required |
| Moisture / IWV | GFS PWAT used as a labeled proxy | Proxy, not satellite-observed |
| CAPE, wind shear | Real GFS/ERA5 fields | Implemented |
| Terrain / DEM | SRTM, 353 of 992 cells | Partial coverage |
| Drainage / catchment | INDOFLOODS catchment file, 155 gauges | Present but consumed only by the FF research path |
| Unified risk maps | Legacy heuristic map and unified 992-cell map | Both implemented, values differ in validation status |
| Explainability (SHAP) | TS and CB supported, FF explicitly not available | Partial |
| Categorized alerts | Four-state delivery model | Implemented |
| Real-time API | FastAPI unified API | Implemented |
| Deployment | Scheduled GitHub Actions and Cloudflare Pages | Implemented, cron-driven rather than continuous |

This table is a summary. It does not assert that every SIH requirement is completely satisfied; several rows above are explicitly partial, proxy-based, or blocked.

<div align="center">

### `23` Production vs Research

</div>

## 23. Production-ready vs research/experimental functionality

| Component | Production | Research | Blocked |
|---|---|---|---|
| TS (VOBL station XGBoost) | Yes, both pipelines | | |
| CB (`panindia_cb_v1`) | Yes, unified pipeline | | |
| CB (legacy pan-India heuristic) | Yes, legacy pipeline | | |
| FF (legacy heuristic) | Yes, legacy pipeline | | |
| FF (INDOFLOODS PU-ranking score) | | Yes, never shown as a probability | |
| Shared MTL backbone | | Architecture only, untrained | |
| Unified API endpoints | Yes | | |
| Alert delivery (4-state) | Yes | | |
| SHAP (TS, CB) | Yes | | |
| SHAP (FF) | | Not available by design | |
| IMDAA reanalysis | | | Yes |
| INSAT-3D/3DR | | | Yes |

<div align="center">

### `24` Limitations & Blocked Sources

</div>

## 24. Current limitations and blocked data sources

- The production flash-flood label (legacy pipeline) is a heuristic formula, not an observed flood event, and no component in this repository outputs an observed nationwide flash-flood probability.
- Thunderstorm prediction is validated at a single station (VOBL) and is not available pan-India.
- The shared MTL backbone has no trained weights; it is an architectural component, not a working multi-hazard model.
- IMDAA reanalysis is blocked pending NCMRWF registration.
- INSAT-3D/3DR is blocked pending MOSDAC credentials; it is not live anywhere in this project.
- DEM/terrain coverage is 353 of 992 cells; hydrology coverage is 75 of 992 cells.
- IMERG coverage is a single validated sample day; the full archive has not been acquired.
- Full 2 to 6 hour validation, per hazard and per cell, is still in progress and is not claimed as complete.

<div align="center">

### `25` Reproducibility & Provenance

</div>

## 25. Reproducibility and data provenance

```bash
# Backend dependencies
pip install -r backend/requirements.txt

# Start the unified API locally
uvicorn backend.unified_api:app --port 8000

# Serve the frontend locally
python3 -m http.server 8080
```

Every feature and hazard output in the unified pipeline carries a provenance tag (`OBSERVED`, `FORECAST`, `REANALYSIS`, `DERIVED`, `PROXY`, `MISSING`), so a given number's origin can always be traced without re-reading the pipeline code. The guiding rules applied throughout this repository and this document:

- A proxy is not an observation.
- A heuristic is not a trained model.
- An untrained architecture is not a validated model.

<div align="center">

### `26` Repository Structure

</div>

## 26. Repository structure

```
SIH-Hyperlocal-Warning/
├── backend/
│   ├── unified_api.py              # FastAPI app: /forecast, /forecast/all, /forecast/sources, alerts
│   ├── pipeline.py                 # legacy pan-India heuristic pipeline
│   ├── alerts.py                   # legacy alert endpoints
│   └── models/unified_mtl/
│       ├── inference_engine.py     # per-hazard HazardPrediction logic (TS/CB/FF)
│       ├── heads.py                # trained-head descriptions and metrics
│       ├── shared_backbone.py      # untrained MTL architecture
│       └── lead_time_interface.py  # 2-6h lead-time handling
├── scripts/
│   └── phase34_build_unified_forecast.py   # generator of data/unified_forecast.json
├── data/
│   ├── pan_india_common_grid_992.json      # canonical 992-cell grid
│   ├── unified_forecast.json               # 992 cells x 5 leads = 4,960 records
│   ├── catchment_characteristics_indofloods.csv
│   └── pan_india_grid.json, forecast.json  # legacy pipeline artifacts
├── processed/
│   ├── ff_pu/                       # INDOFLOODS PU-ranking research pipeline
│   └── indofloods/                  # INDOFLOODS grid mapping/events
├── index.html                       # dashboard: legacy DASHBOARD/FORECAST + UNIFIED FORECAST tab
├── forecast_action.py               # legacy VOBL forecast generator
├── tests/                           # backend and frontend test suites
├── docs/                            # phase reports and audit documents
└── .github/workflows/                # scheduled forecast-update, drift-check, grid-update jobs
```

<div align="center">

### `27` Testing

</div>

## 27. Testing

- Backend unified-pipeline tests (`tests/test_phase35_unified_api.py`, `tests/test_unified_forecast_schema_integrity.py`): passing.
- Frontend unified-forecast tests (`tests/test_unified_forecast_frontend.mjs`): passing.
- Some tests in the broader suite are time-bound or environment-dependent (for example, a fixed timestamp aging past its own staleness window, or a missing optional dependency in a given environment). These are documented as known environment conditions, not as evidence of broken functionality, and the suite is not described as unconditionally passing in every environment.

<div align="center">

### `28` Future Roadmap

</div>

## 28. Future roadmap

**Phase 1, scientific blockers**: pursue IMDAA and INSAT-3D/3DR registration; identify a real-time or sub-daily rainfall feed that could support flash-flood prediction at the required lead time.

**Phase 2, model completion**: decide the path for the legacy pan-India heuristic (calibrate against real labels, or retire in favor of the unified pipeline); continue work toward training the shared MTL backbone against cross-hazard labels once available.

**Phase 3, data expansion**: extend DEM/terrain coverage beyond 353 of 992 cells; extend hydrology coverage beyond 75 of 992 cells; acquire the full IMERG archive.

**Phase 4, operational hardening**: unify the legacy and unified dashboards once the unified pipeline covers the same operational needs; align the live and offline API field naming; move Cloudflare Pages deployment to a push-triggered or on-demand path.

<div align="center">

### `29` References / Data Sources

</div>

## 29. References / data sources

- India Meteorological Department (IMD): station observations and rainfall-derived labels
- NOAA/NCEP Global Forecast System (GFS): atmospheric model fields
- NASA IMERG: satellite-merged precipitation
- Himawari-9 (JMA): geostationary satellite brightness temperature, Band 13
- SRTM: digital elevation data
- INDOFLOODS: flood event and catchment-characteristics research dataset
