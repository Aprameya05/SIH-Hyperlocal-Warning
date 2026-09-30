# Data artifact ownership

Two GitHub Actions workflows used to write `data/pan_india_grid.json`
independently, racing on the same path with no coordination
(`.github/workflows/forecast_update.yml` running `pan_india_gfs_fetcher.py`
on the slot cron, and `.github/workflows/update_grid.yml` running
`backend/pipeline.py` on the GFS-cycle cron). Fixed 2026-09-30: single
writer per artifact.

| Artifact                        | Owner (sole writer)      | Workflow                | Cadence |
|----------------------------------|---------------------------|--------------------------|---------|
| `data/pan_india_grid.json`       | `backend/pipeline.py`     | `update_grid.yml`        | 4x/day, GFS cycles (04:30/10:30/16:30/22:30 UTC) |
| `data/ctt_grid.json`             | `backend/pipeline.py`     | `update_grid.yml`        | same |
| `data/pan_india_grid_slotrun.json` (non-canonical, slot-scoped) | `pan_india_gfs_fetcher.py` | `forecast_update.yml` | 5x/day, forecast slot cron |
| `forecast.json`, `data/pipeline_health.json`, `data/realtime_shap.json` | `forecast_action.py` / `compute_realtime_shap.py` | `forecast_update.yml` | 5x/day |

`backend/pipeline.py` was chosen as the canonical owner of
`pan_india_grid.json` because it is strictly more complete: it also computes
CTT convergence, CTT drop-rate, QPE, and writes `ctt_grid.json`, which
`pan_india_gfs_fetcher.py` does not. `pan_india_gfs_fetcher.py` now writes its
own slot-timed grid to `data/pan_india_grid_slotrun.json` instead of the
shared path — nothing else currently reads that file; it is kept only so the
"Fetch pan-India spatial forecast" step in `forecast_update.yml` still has
somewhere to write without clobbering the canonical grid.

The dashboard (`index.html`) reads only `data/pan_india_grid.json`, i.e. only
`backend/pipeline.py`'s output.
