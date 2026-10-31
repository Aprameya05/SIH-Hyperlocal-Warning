# DRIFT — SIH Demonstration Guide

DRIFT screens severe-weather hazards (thunderstorms, extreme rainfall, flash floods) across India on a 992-cell grid and
is explicit about what it can and cannot support. **LIVE** mode shows only integrity-checked data; **SIMULATION** mode
shows clearly-marked synthetic events so the warning workflow can be demonstrated without any real alert.

## 1. Launch locally (about 1 minute)

```bash
python3 scripts/build_pages_dist.py pages_dist          # builds exactly what would be published (allow-list)
python3 -m http.server 8000 --directory pages_dist
```

Open <http://127.0.0.1:8000/>. The homepage has a blue **“Open command centre →”** banner; the command centre is
`/drift.html` (direct link works too, `#sim` opens simulation mode). MapLibre is vendored (`assets/vendor`), so the page
works with no CDN. Only the grey basemap tiles come from the internet and are optional.

Optional, to refresh live data (needs network; see “Data dependencies”):

```bash
python3 backend/pipeline.py && python3 scripts/sync_unified_cb_into_pan_india_grid.py
python3 scripts/phase34_build_unified_forecast.py       # ~5 min, live GFS from AWS
python3 scripts/eval_hazard_baselines.py && python3 scripts/build_drift_live.py
python3 scripts/ci_validate_drift_live.py data/drift_live.json --require-cells --max-age-minutes 30
```

## 2. Recommended 5-minute judging demonstration

Press **Guided tour** (top right) to have the app drive each step, or do it by hand:

| Min | Do | Say |
|---|---|---|
| 0:00 | Homepage → click the banner. | “DRIFT is a national-scale screening layer. 992 one-degree cells over 6–37°N, 68–98°E. Anything we cannot support stays *unavailable*.” Point at the **LIVE · FRESH** chip and the freshness panel (Sources tab). |
| 0:45 | **Thunderstorm** → layer *Instability screening*, move the CAPE slider, click a hot cell. | CAPE = stored convective energy; CIN = the cap that suppresses storms. These are GFS inputs, **not** a thunderstorm probability. |
| 1:45 | Click the Bengaluru cell (13°N 78°E); scroll *Hazard info*. | Only **VOBL station** has a trained model, METAR, satellite evidence and per-input explanations. Everywhere else is guidance only. |
| 2:15 | **Extreme rainfall** → *Spatial extent*, lower the threshold. | GFS rain by model hour, with cell count and km² above threshold. Then *Heavy-rain-day outlook*: a daily ≥64.5 mm/24 h ranking, **not a cloudburst prediction** (no cloudburst labels, no sub-hourly rain input). |
| 3:00 | **Flash flood** → *Rainfall trigger on susceptible terrain*; *Gauge hydrology coverage*. | We show rain triggers and SRTM-derived susceptibility as context. Prediction is unavailable: no flood labels, no gauge/soil-moisture inputs; hydrology exists for only 75 of 992 cells. |
| 3:45 | **SIMULATION** → *Extreme* → **Run event** → *Alerts & actions* tab. | Amber banner and SIMULATION badges everywhere. Conditions build; WATCH → ALERT → WARNING are issued; affected cells change; each issue lists recommended actions and a **simulated** lifecycle (eligible → recorded → dispatch attempted → provider accepted → delivery confirmed). Nothing is sent. |
| 4:45 | Back to **LIVE**; open *Evaluation*. | Honest metrics (section 5). No 2–6 h forecast is claimed because no forecast-availability time is recorded. |

## 3. The three hazard walkthroughs (what each shows)

- **Thunderstorm** — CAPE, CIN (two GFS hours), user-threshold screening, VOBL METAR/Himawari evidence with ages, station-slot scores with explanations. *Pan-India thunderstorm probability: unavailable.*
- **Extreme rainfall** — GFS precipitation (two hours), extent statistics, daily heavy-rain-day outlook (`panindia_cb_v1`). *Cloudburst prediction: unavailable.*
- **Flash flood** — rain trigger × DEM susceptibility, elevation, gauge-hydrology coverage, research-only PU ranking (toggle). *Flood prediction: unavailable.*

## 4. Simulation walkthrough

Scenario presets (Mild / Severe / Extreme) and sliders (intensity, radius, speed, heading, soil moisture) drive a
deterministic synthetic storm. **Run event** plays T+0…T+6 h; the timeline marks the 2–6 h band, issue points, and a
cursor. The feed shows each hour’s stage, how many cells changed level, and each issued level with actions and lifecycle.
Switching to LIVE restores live data untouched; the two modes share no data.

## 5. What is operational vs simulated

| | Operational (LIVE) | Simulated only |
|---|---|---|
| GFS CAPE/CIN/precip fields, 992 cells | ✅ guidance | |
| SRTM terrain, gauge hydrology (75 cells) | ✅ static context | |
| VOBL station model, METAR, satellite | ✅ one station | |
| Daily heavy-rain-day outlook | ✅ uncalibrated score | |
| WATCH/ALERT/WARNING levels, recommended actions | ❌ not issued live | ✅ |
| Notification lifecycle | ❌ automated dispatch disabled | ✅ (never sent) |
| 2–6 h lead-time forecasts | ❌ unavailable | ✅ synthetic |
| Pan-India thunderstorm, cloudburst, flash-flood prediction | ❌ unavailable | ✅ synthetic |

## 6. Architecture (short and defensible)

GFS 0.25° (AWS Open Data) → `backend/pipeline.py` builds the 992-cell grid (CAPE, CIN, precipitation; seven other
predictors are null and stay null) → `scripts/phase34_build_unified_forecast.py` runs `panindia_cb_v1` (XGBoost
heavy-rain-day model) and the VOBL station models → `backend/forecast_integrity.py` **recomputes** every temporal status
from timestamps and provenance (labels in files are never trusted) → independent fail-closed CI validators (unified
artifact, grid, `forecast.json`, `drift_live.json`) → `scripts/build_drift_live.py` exports only honest fields →
`scripts/build_pages_dist.py` publishes from an allow-list → `drift.html` renders it. All automated alert paths pass one
gate (`backend/alert_policy.py`) whose production registries are empty, so no automated alert can fire.

## 7. Actual current model performance (from `data/hazard_evaluation.json`, recomputed from committed result files)

- **Thunderstorm, VOBL station only** (walk-forward by year 2021–2025, 6-h slots): mean AUROC 0.90; mean POD 0.76, FAR 0.55,
  HSS 0.48. Brier skill vs climatology is positive in 3 of 5 years (0.45, 0.57, 0.14) and **negative in 2024 (−0.29) and
  2025 (−0.08)**; FAR rises to 0.75/0.69 in those years. Skill is for slot windows, not 2–6 h lead.
- **Heavy-rain day, pan-India** (leave-one-date-out, 10 dates, 1,930 cell-days, 20.7 % event rate): AUROC 0.746, PR-AUC
  0.42 (no-skill 0.21). The uncalibrated score has **negative** Brier skill (−0.14): it is a ranking score, not a
  probability. Calibrated: Brier skill +0.14 but POD 0.15, so no alert threshold is validated.
- **Flash flood:** not validated; no evaluation exists.
- **No hazard has evaluated 2–6 h lead-time skill.**

## 8. Known limitations and data dependencies

- No recorded forecast-availability time ⇒ no operational lead time; the 2–6 h band is shown unpopulated.
- Needs network for refresh: AWS Open Data GFS (`noaa-gfs-bdp-pds`), NOMADS filter; VOBL fetchers need `metpy`.
- Seven grid predictors (K-index, Totals-Totals, shear, PWAT, CTT, convergence, CTT drop rate) are null in every cell.
- Hydrology: 75 of 992 cells. Flood labels, soil moisture and river gauges are not wired in.
- GFS precipitation windows are not verified for summation, so f6 and f9 are not added together.
- The legacy Bengaluru dashboard (`index.html`) still contains some static fallback metrics from earlier work.
- The production website still serves the **old** bundle until a deployment is approved (see the completion report).

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| Grey/blank basemap | Tiles are optional. After 3 tile failures the app hides them and shows “Basemap tiles unavailable”; the India outline, land fill and grid keep working. |
| “Source is STALE — values are withheld” | Data older than 12 h. Re-run the refresh commands in section 1 (or wait for CI). Withholding is intended. |
| “LIVE feed unavailable” | `data/drift_live.json` missing/invalid: run `scripts/build_drift_live.py`; check `scripts/ci_validate_drift_live.py` output. |
| Map frame looks blank in a hidden/background tab | Browsers pause rendering for hidden tabs; focus the tab. |
| Blank page offline | Serve over http (not `file://`); everything needed is in the bundle. |
| Stale page after redeploy | The service worker is network-first for data and drift pages; hard-reload once. |
| Browser tests | `cd tests/e2e && npm install && PYTHON=python3 npm test` (uses system Chrome; set `CHROME_PATH` if needed; `DRIFT_SHOTS=1` regenerates `docs/demo_screenshots`). |
