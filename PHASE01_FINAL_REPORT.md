# PHASE 0.1 — FINAL REPORT (Top-Level Summary)

Full detail: `docs/PHASE_0_1_COMPLETION_REPORT.md`,
`docs/PHASE_0_1_AUDIT_DISCREPANCY_RESOLUTION.md`,
`docs/PHASE_0_1_FF_CURRENT_STATE.md`.

1. **Audit discrepancy**: The master audit's claim that
   `data/catchment_characteristics_indofloods.csv` is missing is **wrong** —
   it exists (155 gauge rows) at the exact path checked. All other
   INDOFLOODS/ff_pu row/positive/cell/gauge counts (214 gauges, 4,548
   events, 620 positives/69 cells/131 gauges) were independently verified
   and match exactly. No matrix status changed as a result.

2. **INDOFLOODS state**: real, present, verified — research-only, not wired
   into production.

3. **FF production state**: `backend/pipeline.py::hazard_probabilities()` is
   a hand-weighted heuristic over live GFS fields; terrain is a post-hoc
   multiplier. No observed-label model loaded in production.

4. **FF research state**: Phase 5.7's PU XGBoost model is real and validated
   as a ranking score (spatial-holdout AUC 0.878), but not promotable this
   phase — no real-time IMD rainfall feed, daily resolution incompatible
   with the 2-6h lead-time requirement, and an uncalibrated/ranking-only
   output.

5. **IWV**: corrected 2 user-facing `index.html` strings and 1 data-fusion
   row that implied a satellite/Himawari source; added an `iwv_source`
   provenance field in `forecast_action.py`. Value and all other field names
   unchanged — it is GFS PWAT, as before.

6. **QPE**: already correctly labeled "proxy" everywhere it appears; no
   change needed.

7. **INSAT/Himawari**: corrected one `index.html` row that could read as
   INSAT being live; `backend/fetch_insat3d.py` already honestly scaffolded
   and left untouched.

8. **DEM/terrain**: already correctly documented as a Bengaluru-only,
   post-hoc multiplier, not a trained predictor; no correction needed beyond
   restating it in the new FF current-state doc.

9. **GFS test**: fixed `test_out_of_order_rows_pick_newest`'s
   `latest_gfs_frame()` call to pass `now_utc=FIXED_NOW` (added an optional
   `now_utc` passthrough parameter to `latest_gfs_frame()` for this purpose;
   `GFS_MAX_AGE_HOURS` and the freshness gate itself untouched).

10. **Tests**: `test_gfs_row_select.py` 4/4 passed; the named phase test
    files together 41/41 passed; full repo suite 151/151 passed, with only
    the 4 known pre-existing blockers (donfig x3, NOMADS network x1)
    excluded from collection, exactly as before this phase.

11. **Files changed**: `forecast_action.py`, `index.html`,
    `gfs_row_select.py`, `test_gfs_row_select.py`,
    `docs/MASTER_SIH_REQUIREMENT_MATRIX.md`, plus 3 new docs.

PRODUCTION MODIFIED: YES — labels/provenance strings and one test-support
function parameter only; no prediction logic, coefficients, formulas, or
schema fields were changed or removed.
PRODUCTION DEPLOYED: NO
COMMITTED: NO
PUSHED: NO
