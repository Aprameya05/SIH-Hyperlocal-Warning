# Repository structure

| Path | Purpose |
|---|---|
| `backend/` | Location service, API (`/v1`), alert policy, data-source adapters |
| `pipelines/{ingestion,feature_engineering,publishing,validation}/` | Data pipelines and gates |
| `models/<hazard>/` | Training, evaluation, prediction code and artifacts |
| `frontend/` | Static UI (published at bundle root) |
| `worker/` | Edge worker |
| `tests/` | Python tests (flat) and `tests/e2e` (Playwright) |
| `scripts/{utilities,diagnostics}/` | One-off and generated-doc tools |
| `data/`, `processed/`, `imd_rain/` | Generated artifacts and datasets |
| `docs/{architecture,data_sources,models,evaluation,operations,api,security,demo,archive}/` | Documentation |
| `research/legacy_experiments/` | Archived former `dev/` |

## Documented compatibility decisions
- Root operational modules (`forecast_action.py`, `send_alerts.py`, `regrid.py`, `gfs_fetcher.py`, ...) stay at the root: workflows and tests import them by path.
- Tests remain flat for the same reason.
- Large datasets are tracked as-is; a Git LFS policy is deferred.
- No LICENSE file is added: the licence choice belongs to the owners.
- `backend/requirements.txt` remains the dependency list used by workflows.
