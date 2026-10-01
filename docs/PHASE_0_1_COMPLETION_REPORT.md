# Phase 0.1 Completion Report

## 1. Audit discrepancies found

The master audit's RED finding "missing drainage/catchment join file for FF"
(and the specific matrix row 17 claim that `data/catchment_characteristics_indofloods.csv`
does not exist) is **factually wrong**. That exact file exists at that exact
path, with 155 real gauge rows, and predates the audit documents that claim
it is missing. The same wrong claim is also repeated, independently, in the
older `docs/SIH_REQUIREMENT_MATRIX.md`. Full evidence in
`docs/PHASE_0_1_AUDIT_DISCREPANCY_RESOLUTION.md`. This correction does not
change the matrix's RED status for row 17 — the real, still-accurate gap is
that production never consumes this data, not that the data is absent.

## 2. Exact INDOFLOODS current state

- `processed/indofloods/indofloods_grid_mapping.csv`: 214 gauge rows — matches documented 214/214.
- `processed/indofloods/indofloods_grid_events.csv`: 4,548 event rows — matches documented 4,548/4,548.
- `processed/ff_pu/dataset_build_summary.json`: 620 positives / 69 cells / 131 gauges, recomputed and matched exactly (`match: true`).
- `data/catchment_characteristics_indofloods.csv` (raw, 155 rows) and `data/floodevents_indofloods.csv` (raw) both exist and are real.
- All of this is research-only; none of it is wired into production (see below).

## 3. Exact FF production state

`backend/pipeline.py::hazard_probabilities()` computes `ff_prob` as a
hand-weighted linear heuristic over live GFS fields (PWAT 0.45, CAPE 0.22,
CTT proxy 0.18, QPE/APCP proxy 0.10, convergence 0.05), scaled by thunderstorm
probability. `terrain_lookup.py::apply_terrain_to_ff()` applies a documented
post-hoc multiplier (`0.70 + 0.60 * flood_susceptibility`) afterward — not a
trained-model input. No observed-flood-label model is loaded in production.
Full trace in `docs/PHASE_0_1_FF_CURRENT_STATE.md`.

## 4. Exact FF research state

Phase 5.7's PU XGBoost/logistic models (`processed/ff_pu/RESEARCH_ONLY_model_c_*`)
are real, loadable artifacts, validated as PU-appropriate ranking scores
(validation AUC 0.584, spatial-holdout AUC 0.878 for the combined model), but
**not promotable to production this phase**: (a) no real-time IMD rainfall
feed exists in production (the rainfall features come from an offline daily
`.grd` archive), (b) daily rainfall resolution cannot support the claimed
2-6h nowcast lead time, (c) the output is an uncalibrated PU ranking score,
not a calibrated probability. None of this blocks on implementation effort
alone. Full trace in `docs/PHASE_0_1_FF_CURRENT_STATE.md`.

## 5. IWV correction (exact files/strings changed)

- `forecast_action.py`: added a comment above the `iwv_mm`/`iwv_category` block
  explaining the semantic issue, and added a new `met_parameters["iwv_source"]`
  field: `"GFS PRECIP_WATER (model-derived precipitable water, not satellite-observed IWV)"`.
  No existing key renamed or removed; `iwv_mm`, `iwv_category`,
  `iwv_threshold_mm`, `iwv_pct_of_threshold` are unchanged.
- `index.html`:
  - Comment above the IWV panel: `"IWV Satellite Moisture Tracker"` → `"IWV Moisture Tracker (GFS precipitable water, not satellite-observed)"`.
  - Panel footer label: `"PRECIP WATER · HIMAWARI-9 ANCHOR"` → `"PRECIP WATER · GFS MODEL-DERIVED (NOT SATELLITE)"`.
  - "DATA FUSION INPUTS" row: `desc` no longer claims Himawari provides "IWV variations"; now reads `"CTT drop rate, storm cell detection · headline feature. IWV shown elsewhere is GFS precipitable water, not from this source."`

## 6. Precipitation/QPE correction (exact files/strings changed)

No change needed. `backend/pipeline.py` already consistently labels this
value "QPE PROXY" / "QPE (APCP)" in all comments and log lines, and QPE is
never surfaced to the frontend or schema docs under a name implying observed
QPE — `qpe_mm` only feeds the internal heuristic. Verified by grep across
`backend/pipeline.py`, `forecast_action.py`, `index.html`,
`canonical_forecast_writer.py`, `docs/CANONICAL_FORECAST_SCHEMA.md`.

## 7. INSAT/Himawari correction (exact files/strings changed)

- `index.html` "DATA FUSION INPUTS" row: `{ src: 'Himawari-9 IR/WV', spec: 'INSAT-3D/3DR (MOSDAC)', ... }` → `{ src: 'Himawari-9 IR (live)', spec: 'INSAT-3D/3DR not live — scaffolded only', ... }`.
- `backend/fetch_insat3d.py` reviewed, not modified: it already states plainly at the top ("Status: Integration code ready. Requires MOSDAC institutional credentials") that INSAT is not live; no change needed and the scaffolding is left in place.

## 8. Terrain/DEM correction (documentation only)

No overstated claim found requiring a label fix. `terrain_lookup.py` and
`backend/pipeline.py` (line ~687) already explicitly document the DEM/terrain
adjustment as "a post-processing layer, not a trained terrain-aware model,"
Bengaluru-only, and not pan-India. Existing docs (`docs/PAN_INDIA_BASELINE.md`,
`docs/PHASE_1_OPERATIONAL_AUDIT.md`) already state this accurately. This
phase's `docs/PHASE_0_1_FF_CURRENT_STATE.md` Section 1 restates this
explicitly (post-hoc multiplier, not a model input) for completeness.

## 9. GFS test correction (exact fix)

Root cause: `gfs_row_select.py::latest_gfs_frame()` had no `now_utc`
parameter, so even though `test_out_of_order_rows_pick_newest()` correctly
pinned `now_utc=FIXED_NOW` for its `select_latest_gfs()` call, its second
call — `latest_gfs_frame(df, date_str="2026-09-30")` — used real wall-clock
time, and the fixture's 2026-09-30 timestamps eventually aged past
`GFS_MAX_AGE_HOURS` (7.0h), making the freshness gate correctly reject them.

Fix:
- `gfs_row_select.py`: added an optional `now_utc=None` parameter to
  `latest_gfs_frame()` that is forwarded to `select_latest_gfs()`. Default
  behavior (real wall-clock time) is unchanged for every production call
  site; `GFS_MAX_AGE_HOURS` itself was not touched.
- `test_gfs_row_select.py`: the test's `latest_gfs_frame(df, date_str="2026-09-30")`
  call now passes `now_utc=FIXED_NOW`, exactly matching the pattern already
  used for its `select_latest_gfs()` call and the file's other three tests.

`tests/test_phase45_reliability.py::test_4_gfs_max_age_gate` was verified to
still exist and still pass — it independently covers fresh/boundary/stale/
invalid-timestamp rejection, so no new test was added (would have duplicated
existing coverage).

## 10. Files changed (full list)

- `forecast_action.py` — added `iwv_source` provenance field + explanatory comment (met_parameters block). No logic change.
- `index.html` — 3 label/comment string corrections (IWV panel comment, IWV panel footer, data-fusion-inputs INSAT/Himawari row). No logic/styling/schema change.
- `gfs_row_select.py` — added optional `now_utc` passthrough parameter to `latest_gfs_frame()`. No change to `GFS_MAX_AGE_HOURS` or freshness-gate logic.
- `test_gfs_row_select.py` — one call site now passes `now_utc=FIXED_NOW`.
- `docs/MASTER_SIH_REQUIREMENT_MATRIX.md` — rows 8, 9, 17 updated with this phase's corrected evidence text. No status changed (RED stayed RED throughout).
- `docs/PHASE_0_1_AUDIT_DISCREPANCY_RESOLUTION.md` — new.
- `docs/PHASE_0_1_FF_CURRENT_STATE.md` — new.
- `docs/PHASE_0_1_COMPLETION_REPORT.md` — new (this file).
- `/tmp/audit_repo/PHASE01_FINAL_REPORT.md` — new (top-level summary).

## 11. Tests passed (exact counts)

- `tests/test_gfs_row_select.py` equivalent (`test_gfs_row_select.py` at repo root — the actual file; no `tests/test_gfs_row_select.py` exists): **4/4 passed** (previously 1 failing on `latest_gfs_frame`).
- `tests/test_phase45_reliability.py`: passed (part of the 41/41 group below).
- `tests/test_indofloods_phase5.py`, `test_indofloods_phase55.py`, `test_indofloods_phase56.py`, `test_ff_pu_phase57.py`, `test_canonical_forecast.py`, `test_phase45_reliability.py` together: **41/41 passed**.
- Full repo suite (`python3 -m pytest`, all `test_*.py`), excluding the 4 known pre-existing collection blockers: **151/151 passed**.
- Known pre-existing blockers, confirmed still the only failures (nothing new broke): `test_himawari.py`, `test_segments.py`, `test_segments_v2.py` (all `ModuleNotFoundError: No module named 'donfig'`), `test_nomads.py` (`ProxyError` reaching `nomads.ncep.noaa.gov`, no live network access in this sandbox).

## 12. Remaining blockers

- `donfig` package not installed (blocks `satpy`/`test_himawari.py`, `test_segments.py`, `test_segments_v2.py`).
- No live network access to NOMADS (`test_nomads.py`) or MOSDAC in this sandbox.
- IMDAA and INSAT-3D/3DR remain genuinely blocked on external registration/credentials (unchanged by this phase).
- The real structural gaps documented in `docs/PHASE_0_1_FF_CURRENT_STATE.md` (no real-time IMD rainfall feed, daily-resolution/lead-time mismatch, uncalibrated PU output) are not implementation debt closeable by more engineering effort alone within this phase's scope.

## 13. Next recommended phase

A dedicated phase to either (a) acquire/wire a sub-daily precipitation feed
(IMDAA or an equivalent reanalysis/nowcast product) so the FF_PU rainfall
features could in principle be recomputed at an operationally relevant
resolution, or (b) explicitly re-scope the FF_PU research line as a
longer-lead-time (daily) flood-susceptibility ranking product distinct from
the SIH's 2-6h nowcast requirement, rather than attempting to force it into
the existing nowcast pipeline.

---

PRODUCTION MODIFIED: YES — `forecast_action.py` (added one new `met_parameters.iwv_source` string field and an explanatory comment; no prediction logic, probabilities, coefficients, or existing field values changed), `index.html` (three label/comment string corrections only; no visual design, map styling, alert thresholds, or logic changed), and `gfs_row_select.py` (added an optional `now_utc` passthrough parameter to a test-support function `latest_gfs_frame()`; `GFS_MAX_AGE_HOURS` and all freshness-gate decision logic unchanged, default production behavior unchanged). `backend/pipeline.py` and `canonical_forecast_writer.py` were read and traced but not modified.
PRODUCTION DEPLOYED: NO
COMMITTED: NO
PUSHED: NO
