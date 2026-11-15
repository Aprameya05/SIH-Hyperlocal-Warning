<div align="center">

<img src="docs/assets/readme_banner.svg" alt="DRIFT banner" width="100%" />

# DRIFT

**Pan-India, location-aware early-warning decision support for severe thunderstorms, extreme rainfall and flash-flood risk**

Smart India Hackathon · SIH26077 · [Live site](https://sih-hyperlocal-warning.pages.dev/)

</div>

---

DRIFT turns NOAA GFS model guidance into a verified, location-specific view for **4,643 land cells (0.25°) across India**, adds trained and calibrated hazard probabilities **only where a model has been validated**, and says "unavailable" everywhere else. Every value carries provenance and timestamps, and its freshness status is recomputed from the clock rather than trusted from the producer.

## What works today (and what does not)

| Capability | Status |
|---|---|
| National grid: 4,643 land cells, state/district per cell, 3,756-place gazetteer, search by name or coordinate | **Working**, independently checked against raw GRIB for 27 diverse locations ([report](docs/NATIONAL_COVERAGE_AND_LOCATION_TESTS.md)) |
| GFS fields and derived physics (K-index, Total Totals, lapse rate, 850–500 hPa shear, convergence, PWAT gradient) | **Working**, refreshed twice daily |
| Thunderstorm probability, +6/12/18/24 h | **Working**, calibrated XGBoost + TreeSHAP; target is *ordinary* thunderstorm (METAR TS/VCTS) |
| Heavy rainfall: ≥20 mm / 3 h at forecast hour 9 | **Working**, one target only |
| Cloudburst prediction | **Unavailable** (no sub-hourly labels or observations) |
| Flash-flood prediction | **Unavailable**; an unvalidated heuristic *screening index* is shown, labelled as such |
| 2–6 h operational lead time, nowcasting | **Not claimed** (DRIFT's own publication time is not recorded; satellite/radar inputs not accessible) |
| Satellite (INSAT/MOSDAC, Himawari), IMDAA, radar, lightning | **Not integrated**; blockers in [`docs/data_sources/SATELLITE_AND_REANALYSIS_ACCESS.md`](docs/data_sources/SATELLITE_AND_REANALYSIS_ACCESS.md) |
| Alert delivery | **Disabled by design** (fail-closed policy, empty registries) |
| LIVE vs SIMULATION | Strictly separated; the simulator has no network capability and cannot alert |

Validation numbers (confidence intervals, calibration, baselines, per-lead, leave-one-region-out) are generated from the evaluation files into [`docs/HAZARD_MODEL_VALIDATION.md`](docs/HAZARD_MODEL_VALIDATION.md); nothing is typed by hand. Limits: [`docs/OPERATIONAL_LIMITATIONS.md`](docs/OPERATIONAL_LIMITATIONS.md).

## Architecture

```
NOAA GFS 0.25° (AWS S3, byte-range) ──► pipelines/ingestion ──► data/india_fields_latest.json
                                              │  (eccodes decode, cross-checked headers, S3 Last-Modified = input publication time,
                                              │   atomic write, no-downgrade guard, data/ingest_status.json)
                                              ▼
              models/thunderstorm, models/extreme_rainfall ──► data/india_{ts,rain}_model_latest.json
                                              │
                          backend/ (location service, /v1 API, alert policy)
                                              │
        pipelines/validation (release gate) ─► pipelines/publishing (allow-list bundle) ─► Cloudflare Pages
                                              ▼
                      frontend/ (static command centre; JS twin of the verifier and location engine)
```

The production site is **static**: the browser loads committed JSON artifacts and re-verifies them. The FastAPI `/v1` service (`backend/location_api.py`) implements the same logic for server use but is not part of the deployed path. Details: [`docs/FINAL_SYSTEM_ARCHITECTURE.md`](docs/FINAL_SYSTEM_ARCHITECTURE.md).

## Quick start

Requires Python ≥ 3.11 and Node ≥ 20 (Node and Chrome are only needed for browser tests).

```bash
git clone https://github.com/Aprameya05/SIH-Hyperlocal-Warning.git && cd SIH-Hyperlocal-Warning
python3 -m venv .venv && source .venv/bin/activate
make install            # pip install -r requirements/dev.txt
cp .env.example .env    # optional; every value is a placeholder
make test               # Python suite
make gate               # build the bundle and run the release-readiness gate
make serve              # http://localhost:8000/drift.html
```

Refresh data (network): `make ingest && make predict`. Browser tests: `cd tests/e2e && npm ci && npm test` (uses system Chrome; set `CHROME_PATH` if needed). Full list: `make help`.

Known baseline failures on a fresh checkout (missing raw datasets, unrelated to the product): `test_indofloods_phase5` ×3, `test_vobl_historical_gfs_ts_join` ×6, and 3 modules that fail collection (`test_phase21/22/27`). See [`docs/operations/KNOWN_ISSUES.md`](docs/operations/KNOWN_ISSUES.md).

## API (`backend/location_api.py`, prefix `/v1`)

`/meta`, `/search`, `/resolve`, `/location`, `/region`, `/compare`, `/coverage`, `/forecast`, `/explain`, `/health`, `/ready`, `/alert-eligibility`. The module exposes a FastAPI `router`; mount it with `app.include_router(router)` (this is how `tests/test_location_api.py` exercises it). It is not deployed in production.

## Repository map

| Path | Contents |
|---|---|
| `frontend/` | Static UI (`drift.html`, `assets/`); published at the bundle root |
| `backend/` | Location service, `/v1` API, alert policy, data-source adapters, feature builder |
| `pipelines/ingestion` · `feature_engineering` · `publishing` · `validation` | GFS ingestion; training-table builders; allow-list bundle builder; validators and release gate |
| `models/thunderstorm`, `models/extreme_rainfall`, … | Training, evaluation, prediction code and artifacts per hazard |
| `tests/` | Python tests (flat), JS tests (`*.mjs`), `tests/e2e` (Playwright) |
| `scripts/utilities`, `scripts/diagnostics` | Generated docs, acceptance run, inventory |
| `data/`, `processed/`, `imd_rain/` | Generated artifacts and datasets |
| `worker/` | Cloudflare Worker (not in the forecast-serving path) |
| `docs/` | Architecture, data sources, models, evaluation, operations, security, demo, archive |
| `research/legacy_experiments/` | Archived experiments |
| `.github/workflows/` | Scheduled ingestion, forecasts, drift checks, retraining |

Root-level modules (`forecast_action.py`, `send_alerts.py`, `regrid.py`, …) belong to the older station/992-cell pipeline that workflows import by path; see [`docs/architecture/REPOSITORY_STRUCTURE.md`](docs/architecture/REPOSITORY_STRUCTURE.md).

## Documentation index

[Architecture](docs/FINAL_SYSTEM_ARCHITECTURE.md) · [Data sources & provenance](docs/DATA_SOURCE_AND_PROVENANCE_MATRIX.md) · [Hazard model validation](docs/HAZARD_MODEL_VALIDATION.md) · [National coverage tests](docs/NATIONAL_COVERAGE_AND_LOCATION_TESTS.md) · [Lead-time validation](docs/FORECAST_LEAD_TIME_VALIDATION.md) · [Limitations](docs/OPERATIONAL_LIMITATIONS.md) · [SIH traceability](docs/FINAL_SIH_REQUIREMENT_TRACEABILITY.md) · [Demo runbook](docs/DEMO_RUNBOOK.md) · [Verification report](docs/FINAL_VERIFICATION_REPORT.md) · [National data report](docs/data_sources/NATIONAL_DATA_REPORT.md)

The previous long-form README (engineering history, pre-national-grid) is archived at [`docs/archive/reports/README_legacy_20261011.md`](docs/archive/reports/README_legacy_20261011.md); its numbers (992-cell grid, 993 tests) are superseded.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Never fabricate data, probabilities or lead times; missing is `null`/unavailable.
