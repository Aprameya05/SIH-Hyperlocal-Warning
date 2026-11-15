# Final system architecture

DRIFT is a static-first, fail-closed early-warning platform. Everything the browser shows is a committed JSON artifact whose integrity is **recomputed** at load time; nothing is trusted because a producer labelled it.

## Data flow

```
 NOAA GFS 0.25deg (AWS S3, .idx byte-range)            METAR (IEM) / CMORPH (NOAA CDR)  -> training labels only
        |                                                        |
 pipelines/ingestion/gfs_reader.py  (eccodes decode, message cross-checks, S3 Last-Modified = input publication time)
        |  atomic all-or-nothing, no-downgrade guard, data/ingest_status.json
        v
 data/india_fields_latest.json  (4,643 land cells x forecast hours x fields; derived physics)
        |                                   \
        |                                    models/<hazard>/predict_*.py  -> data/india_ts_model_latest.json, india_rain_model_latest.json
        v                                    (XGBoost + isotonic, TreeSHAP top-3)
 backend/location_service.py  (Store: resolve, record, explain, health, alert_eligibility)   backend/location_api.py (/v1/*)
        |
 pipelines/validation/check_release_readiness.py  (gate)  ->  pipelines/publishing/build_pages_bundle.py (allow-list)
        v
 Cloudflare Pages static bundle  ->  frontend/ (drift.html, drift_app.js, drift_location.js; JS verifier mirrors Python verifier)
```

## Layers
| Layer | Location | Responsibility |
|---|---|---|
| Ingestion | `pipelines/ingestion/` | Fetch and decode inputs, record provenance, refuse older cycles |
| Feature/labels | `pipelines/feature_engineering/` | Build training tables with leakage control |
| Models | `models/<hazard>/` | Train, evaluate (CIs, baselines), predict; honest "unavailable" artifacts |
| Backend | `backend/` | Location resolution, hazard assembly, health, alert eligibility, alert policy |
| Publishing | `pipelines/publishing/` | Allow-list bundle; frontend published at bundle root |
| Validation | `pipelines/validation/` | Live-feed and national-field validators, readiness gate |
| Frontend | `frontend/` | Command-centre UI, parity JS twin, simulation (isolated, no network) |

## Integrity rules
- Status (`CURRENT / PAST_VALID_TIME / STALE / INVALID_PROVENANCE`) is recomputed from timestamps and the server clock (`MODEL_MAX_AGE_H=18`, `PAST_TOLERANCE_H=1.5`).
- Three hazard pathways are independent: thunderstorm (trained, evaluated); extreme rainfall (trained 3 h target only); cloudburst and flash-flood prediction **unavailable** (no labels/observations). A flash-flood *screening index* is labelled unvalidated and is never a probability.
- Missing values are `null`, never zero. Operational lead time is `null`: DRIFT's own publication time is not recorded.
- Alerting is fail-closed: `backend/alert_policy.py` registries are empty; `/v1/alert-eligibility` reports why nothing is eligible. Simulation cannot reach alert paths.
- Release gate: the bundle is built only if all required artifacts pass independent verification.

## Compatibility decisions
Root-level operational modules (`forecast_action.py`, `send_alerts.py`, `regrid.py`, ...) remain at the root because workflows and tests import them by that path; see `docs/architecture/REPOSITORY_STRUCTURE.md`.
