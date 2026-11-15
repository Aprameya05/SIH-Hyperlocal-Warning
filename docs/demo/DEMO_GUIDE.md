# DRIFT — Demonstration & Operations Guide

DRIFT is a location-aware severe-weather intelligence platform for India. Pick any Indian location and you see **that
location's** atmospheric, precipitation, terrain and hydrological context, plus three independent hazard analyses
(thunderstorm, extreme rainfall, flash flood) with their provenance, freshness and evaluation status. A separate,
clearly marked **SIMULATION** mode demonstrates the warning workflow with synthetic data.

Detailed numbers (24-location table, coverage matrix, full evaluation tables) are in
[`docs/data_sources/NATIONAL_DATA_REPORT.md`](../data_sources/NATIONAL_DATA_REPORT.md), generated from the artifacts.

## 1. Launch locally

```bash
python3 pipelines/publishing/build_pages_bundle.py pages_dist            # exactly what would be published (allow-list)
python3 -m http.server 8000 --directory pages_dist        # open http://127.0.0.1:8000/ then "Open command centre"
```

Refresh the national data (network needed; ~2 min; all-or-nothing, atomic):

```bash
python3 pipelines/grid/build_india_grid.py        # once: 4,643 land cells + gazetteer (needs cache/geo, see script header)
python3 pipelines/grid/build_terrain_and_gauge_layers.py      # once: SRTM terrain + gauge context
python3 pipelines/ingestion/ingest_gfs_forecast.py     # latest complete 00Z/12Z GFS cycle, 8 forecast hours, every cell
python3 models/thunderstorm/predict_thunderstorm.py && python3 models/extreme_rainfall/predict_extreme_rainfall.py
python3 pipelines/validation/validate_national_fields.py --max-age-hours 20 && python3 scripts/utilities/generate_national_report.py
```

API (FastAPI, same logic as the browser): `/v1/search?q=`, `/v1/resolve?lat=&lon=`, `/v1/location?cell_id=|lat=&lon=[&hour=]`,
`/v1/region?state=|district=`, `/v1/compare?cells=a,b,c`, `/v1/coverage`, `/v1/meta` (ETag + invalidation on new ingestion).
Browser tests: `cd tests/e2e && npm install && PYTHON=python3 npm test` (system Chrome; `DRIFT_SHOTS=1` refreshes `docs/demo/screenshots`).

## 2. Five-minute judging demonstration

Press **Guided tour** to have the app drive each step, or:

| Min | Do | Say |
|---|---|---|
| 0:00 | Homepage → “Open command centre”. | 4,643 land cells at 0.25° (~28 km) — the *same* resolution as the GFS source, so no cell implies finer detail than the model has. Cells are ≥50 % Indian land; sea and neighbouring countries are not locations (click one: “Not covered”). |
| 0:40 | Search **Mumbai**. In the Location tab read the provenance block. | Every value has unit, kind (raw model / derived / model state), valid time, model init, **S3 publication time of the NWP file (observed)** and acquisition time. Operational lead is shown as *unavailable*: DRIFT records no publication time for its own forecasts. |
| 1:30 | Search **Srinagar**, then **Chennai**, then **Guwahati**. | CAPE, humidity, temperature, terrain, rainfall all change — the underlying data changes, not just the highlight. Chennai’s soil moisture is missing (GFS water pixel) and stays *unavailable*. Press forecast-hour chips f3…f24: values change; chip status is recomputed from the clock. |
| 2:15 | **Hazards** tab (Mumbai). | *Thunderstorm*: CAPE/CIN/K/TT/shear/ω with what each means, instability band (not a probability), lead-specific pan-India model probabilities with TreeSHAP reasons and its evaluation. *Extreme rainfall*: GFS 3 h amounts, accumulation, spatial extent, the daily heavy-rain-day score (1°, uncalibrated) and a separately evaluated 3 h heavy-rainfall model; **cloudburst is UNAVAILABLE** and explained. *Flash flood*: rain trigger, soil wetness (model), slope/relief, nearest gauge history, a heuristic screening index (labelled not a probability); **prediction UNAVAILABLE** with the list of missing inputs. |
| 3:15 | Search **Kerala** (region summary), add Delhi, Chennai, Guwahati to **Compare**. | Different Indian regions side by side at one forecast hour. |
| 3:50 | **SIMULATION** → *Extreme* → **Run event** → Alerts tab. | Amber banner; synthetic grid; conditions build; WATCH → ALERT → WARNING; affected cells; recommended actions; simulated lifecycle (nothing sent). Switch back to LIVE: your selected location is untouched. |
| 4:40 | **Evaluation** tab. | Honest metrics, validation design, and what is withheld. |

## 3. What is operational vs simulated

| | LIVE (real artifacts) | SIMULATION only |
|---|---|---|
| 0.25° GFS fields (CAPE, CIN, LI, K, TT, PWAT, RH, T, wind, 850–500 hPa shear, ω, MSLP, soil moisture, rain), 8 forecast hours, every land cell | ✅ guidance, verified | |
| SRTM terrain, gauge history (106 gauges, historical) | ✅ static context | |
| Pan-India thunderstorm model, leads +6/12/18/24 h (00Z/12Z cycles) | ✅ evaluated, calibrated to airport METAR reports | |
| Short-duration heavy rainfall (≥20 mm/3 h, +9 h, 00Z cycle) | ✅ evaluated; other leads/thresholds withheld by the gate | |
| Daily heavy-rain-day outlook (1°) | ✅ uncalibrated score | |
| Flash-flood screening index | ⚠ heuristic, unvalidated | |
| Cloudburst, extreme short-duration rainfall, flash-flood *prediction* | ❌ unavailable | ✅ synthetic |
| WATCH/ALERT/WARNING, recommended actions, notification lifecycle | ❌ automated alerting disabled (empty registries) | ✅ never sent |
| 2–6 h operational lead time | ❌ no DRIFT publication time recorded | ✅ synthetic |

## 4. Architecture (technical and current)

`pipelines/ingestion/gfs_reader.py` byte-range-reads GFS 0.25° from the NOAA AWS mirror and **cross-checks every GRIB message** (init, step, level,
accumulation window) → `pipelines/ingestion/ingest_gfs_forecast.py` extracts all fields at all cells (cell centres are GFS grid points: exact,
no interpolation), records S3 Last-Modified per file, writes atomically and all-or-nothing → `location_service.py` /
`drift_location.js` (parity-tested twins) **re-verify** the artifact and recompute every status from timestamps and the clock →
`/v1` API and the static site. Models: XGBoost per lead with isotonic calibration fitted on date-grouped out-of-fold training
predictions. Heavy-rainfall labels are NOAA CMORPH CDR hourly 0.25° (satellite estimates, gauge-adjusted over land); thunderstorm
labels are airport METAR present weather (IEM). All automated alert paths still go through one gate with empty registries.

## 5. Actual model performance (from `data/*_evaluation.json`)

**Thunderstorm** — 113 METAR stations, 43 init dates (Apr–Jun 2024/25), 19,994 samples, event rate 6.5 %. Train 2024 → test 2025:

| Lead | AUROC | PR-AUC (no-skill) | Brier skill vs climatology | POD / FAR / CSI | Leave-one-region-out AUROC / Brier skill |
|---|---|---|---|---|---|
| +6 h | 0.82 | 0.18 (0.06) | 0.09 | 0.50 / 0.79 / 0.17 | 0.81 / 0.09 |
| +12 h | 0.74 | 0.23 (0.10) | 0.07 | 0.44 / 0.74 / 0.20 | 0.74 / 0.06 |
| +18 h | 0.84 | 0.18 (0.05) | 0.09 | 0.57 / 0.79 / 0.18 | 0.83 / 0.08 |
| +24 h | 0.74 | 0.21 (0.09) | 0.07 | 0.41 / 0.76 / 0.18 | 0.71 / 0.05 |

Skill over climatology is real but modest; false-alarm ratios are high; the Northeast held-out region is weakest (AUROC 0.64 at +12 h).
**Heavy 3 h rainfall ≥20 mm (+9 h)** — 32 monsoon init dates × all land cells (74,288 test samples, 0.9 % event rate): AUROC 0.89
(raw GFS rainfall 0.86), PR-AUC 0.09 (no-skill 0.009), Brier skill 0.05, POD 0.22 / FAR 0.83 / CSI 0.10; leave-region-out AUROC 0.87.
Other leads and the ≥40 mm target fail the gate and are withheld. **No hazard has an evaluated operational 2–6 h *warning* lead time.**
Flash flood: not validated.

## 6. Known limitations and data dependencies

- Pilot-size training sets (every 3rd/6th day of one season per hazard, two years): wide uncertainty; thunderstorm = pre-monsoon only, rain = monsoon only; lead and time of day are confounded (only 00Z/12Z cycles).
- Thunderstorm probabilities are calibrated to *airport* reports (urban/plain bias); elsewhere they are model estimates of the same event.
- District/state attribution is by cell centre (cities can sit near a boundary; 2011/2021-vintage boundary files; Ladakh is inside J&K in the state layer).
- Soil moisture is a GFS land-surface model state; hydrology exists only near 106 historical gauges; no river network, no observed soil moisture or live gauge levels.
- Wind shear is the 850–500 hPa bulk difference, not 0–6 km shear. Heavy-rain-day outlook is 1°, uncalibrated.
- Data needs: AWS Open Data (`noaa-gfs-bdp-pds`, `noaa-cdr-precip-cmorph-pds`), IEM METAR archive, NOAA Terrain Tiles; geographic sources are cached under `cache/geo`.
- The production website serves the *old* bundle until a deployment is approved.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| Grey/blank basemap | Tiles are optional; after 3 failures they are hidden and a notice appears. Grid, outline and land fill are local. |
| Overlay “Model cycle is STALE — values withheld” | The GFS cycle is older than 18 h. Run `pipelines/ingestion/ingest_gfs_forecast.py` (or wait for the scheduled workflow). Intended behaviour. |
| Overlay “Atmospheric fields are UNAVAILABLE: …” | The artifact failed independent verification; the message names the failed check. Re-run ingestion. |
| “Pan-India model … UNAVAILABLE” | No lead passed the gate, or the cycle is not 00Z/12Z for that model; the reason is in the artifact. |
| Map blank for seconds in an embedded/unfocused browser pane | `requestAnimationFrame` is throttled there; click into the page. |
| Stale page after redeploy | Service worker is network-first for data and drift pages; hard-reload once. |
