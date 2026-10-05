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
| `data/unified_forecast.json` | `scripts/phase34_build_unified_forecast.py` | none (local/offline artifact, no workflow writes it) | on demand |

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


## `data/unified_forecast.json` (added Phase 36.1)

This artifact is the Phase 34/35/36 unified inference contract: exactly
992 unique `IND_<lat>_<lon>` cells x 5 lead times (2/3/4/5/6h) = 4960
records, each with `TS`/`CB`/`FF` blocks (`probability`/`risk_category`/
`status`/`model_version`/`provenance`), `terrain`, and top-level
`n_cells`/`n_lead_times`/`n_records`/`init_time_utc`/`lead_hours_supported`.
It is **not** written by any GitHub Actions workflow -- `git grep` across
`.github/workflows/`, every script, and every test confirms no CI job,
cron, or bot ever touches this path.

The artifact was nonetheless found reverted to an older, unrelated schema
(`scripts/phase24_unified_pipeline.py`'s Phase 24 research schema --
`ts_probability`, `cb_probability_baseline`, `latitude`/`longitude`,
`target_timestamp_utc`, etc.) multiple times. Root cause: that script
defines `OUTPUT_PATH = data/unified_forecast.json` and
`tests/test_phase25_hazard_gaps.py::test_pipeline_remains_executable_with_ts_now_partially_available`
called `phase24_unified_pipeline.main()` directly with no output-path
redirection -- so every time the regression suite ran, that single test
silently overwrote the real production artifact as a side effect. Fixed
in Phase 36.1 by redirecting that test's output to `tmp_path` via
`monkeypatch`; `scripts/phase24_unified_pipeline.py` itself is left in
place as a historical Phase 24 research artifact (it is not deleted, and
nothing else currently imports or runs its `main()`).

`scripts/phase34_build_unified_forecast.py` is the sole authoritative
generator of `data/unified_forecast.json` going forward.
`tests/test_unified_forecast_schema_integrity.py` is a deterministic
regression guard that fails the suite if the artifact is ever found back
in the Phase 24 schema (or missing the Phase 34/35/36 contract keys).


## `data/alerts.db` (added Phase 36.1)

The FastAPI app's startup (`init_db()` in `backend/alerts.py`) failed with
`sqlite3.OperationalError: disk I/O error` on this dev machine. Root cause:
not disk exhaustion (a direct multi-MB file write succeeded instantly
despite the volume being at ~98% used) and not corruption (`PRAGMA
integrity_check` -> `ok` on the existing, empty, 0-byte file) -- the repo
directory itself is a FUSE-mounted remote-device bridge, and SQLite's
write path fails there unconditionally. Confirmed with a brand-new SQLite
file both inside `data/` and at the repo root, and confirmed that even
`PRAGMA journal_mode=MEMORY` (which avoids creating a rollback-journal
file) still fails -- the incompatibility is in SQLite's locking/fsync
semantics against this FUSE implementation, not journal-file creation.

Fix: `DB_PATH` in `backend/alerts.py` now honors an `ALERTS_DB_PATH`
environment variable, falling back to the original `data/alerts.db` path
when unset, so production deployments on a normal filesystem are
unaffected. On this dev machine, start the app with e.g.
`ALERTS_DB_PATH=/tmp/sih_alerts/alerts.db uvicorn backend.unified_api:app`
to work around the FUSE mount.


## `data/external/historical_gfs/phase20_full_predictor_dataset.csv` (added Phase 36.1, staged for commit)

84MB processed/derived GFS predictor dataset, required at runtime by
`backend/unified_api.py::_get_cb_features_for()` (via
`scripts/panindia_cb_features.engineer_daily_features()`) on the **live**
`GET /forecast` path for a single cell -- NOT required by `GET
/forecast/all`, which serves the pre-built `data/unified_forecast.json`
artifact verbatim. Not excluded by `.gitignore` (that file only excludes
the raw GRIB intermediates under `data/external/historical_gfs/raw/`,
not this final processed CSV). No credentials, tokens, absolute
machine-specific paths, or personal data in it -- columns are GFS
meteorological predictors (CAPE/CIN/geopotential height/wind
shear/humidity/pressure/temperature/precip) plus cell_id/lat/lon/cycle
metadata. Provenance: `docs/PHASE_19_GFS_PREDICTOR_EXTRACTION.md`,
`docs/PHASE_19B_GFS_SCHEMA_VALIDATION.md`,
`docs/PHASE_20_FULL_GFS_EXTRACTION.md` (not themselves part of this
commit). SHA256 at the time of staging:
`f7ea7daa08957f9ecbe2a827f00601de15ea633053cef9d1e46be22eb051c811`.
