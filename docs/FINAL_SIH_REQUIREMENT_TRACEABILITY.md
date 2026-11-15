# SIH26077 requirement traceability

| Requirement | Status | Evidence |
|---|---|---|
| Pan-India location-aware coverage | MET | 4,643-cell land grid; `docs/NATIONAL_COVERAGE_AND_LOCATION_TESTS.md` (27/27 covered locations pass independent GRIB check) |
| Hyperlocal resolution | PARTIAL | 0.25deg (~25 km) cells; not sub-km |
| Severe thunderstorm prediction | PARTIAL | Ordinary-TS calibrated model, 4 leads, CIs (`HAZARD_MODEL_VALIDATION.md`) |
| Cloudburst prediction | NOT MET | Honestly unavailable; no labels/observations |
| Flash-flood prediction | NOT MET | Unavailable; unvalidated screening index only |
| Extreme rainfall | PARTIAL | 20 mm/3 h at f09 only |
| 2-6 h nowcasting | NOT MET | No validated operational lead; `FORECAST_LEAD_TIME_VALIDATION.md` |
| Satellite/radar fusion | NOT MET | Access blockers documented |
| Explainability | MET | TreeSHAP top-3 per prediction, `/v1/explain` |
| Provenance and data integrity | MET | Recomputed statuses, independent verifiers, ingest status, no-downgrade guard |
| Fail-closed alerting | MET (disabled) | `alert_policy.py`, `/v1/alert-eligibility`, simulation isolation |
| Alert delivery to users | NOT MET | Disabled by design |
| Dashboard / command-centre UI | MET | `frontend/`, 15 Playwright tests |
| Reproducible training | MET | Fixed seeds; bit-identical predictor test |
| Health / readiness monitoring | MET | `/v1/health`, `/v1/ready`, release gate |
