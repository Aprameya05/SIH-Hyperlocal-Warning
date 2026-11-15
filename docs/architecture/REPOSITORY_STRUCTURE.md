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

## Two grids (not a duplicate)
- **National grid** (current): 4,643 land cells at 0.25 degrees, `IN025_*` ids, `data/india_fields_latest.json`, served by `backend/location_service.py` and `frontend/assets/location_engine.js`.
- **Legacy application grid**: 992 cells at 1 degree, `IND_*` ids, `data/pan_india_grid.json` plus the VOBL station forecast. Still produced by `update_grid.yml` and `forecast_update.yml`; documented in `docs/architecture/CANONICAL_GRID.md`.

## Removed in this pass (history keeps them)
Raw Himawari segment binaries (`B13_S05/06.DAT`, `HS_H09_*.bz2`), four demo `.mp4` clips, `index_backup.html`, a duplicate `forecast_action-1.py`, a Windows `.wslconfig` and a `.bat` launcher, all under `research/legacy_experiments/` with zero references from code, tests or workflows (evidence: `docs/architecture/REPOSITORY_INVENTORY.md`). `rag/chroma_db` is kept and flagged experimental pending the owners' decision.
