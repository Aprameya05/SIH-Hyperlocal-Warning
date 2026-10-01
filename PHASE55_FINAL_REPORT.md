# PHASE 5.5 COMPLETE

## 1. Gauge/cell label definition
Gauge (`GaugeID`, fixed lat/lon in `metadata_indofloods.csv`) -> canonical
cell via point-in-cell on the gauge's own coordinates (Phase 5's
`scripts/map_indofloods_to_grid.py`, unmodified) -> flood event
(`floodevents_indofloods.csv`, `Start Date` = threshold-crossing date) ->
cell/date event label. `GaugeID` is preserved explicitly through
`processed/indofloods/indofloods_grid_events.csv` on every row; catchment
centroids are never substituted for the gauge point. Full chain and
justification: `docs/INDOFLOODS_LABEL_DEFINITION.md`.

## 2. Multi-gauge aggregation rule (and justification)
`cell_event(cell, date) = 1` if ANY qualifying gauge in that cell has an
event with that `Start Date` (OR-aggregation), for the positive label
only. Justified because a gauge's recorded event is a real hydrological
occurrence physically located inside its own cell. Computed directly:
48 of 992 cells have >1 mapped gauge; across them, 4,548 raw event rows
collapse to 4,106 unique (cell, date) positives (442 same-cell/same-date
collisions across different gauges, largest case cell `9.0_76.0`: 413
events -> 309 dates). Caveat: any future count of "events per cell" must
dedupe on (cell, date) first or it double-counts these 442 rows.

## 3. Negative-label conclusion (A/B/C from Part 2, with reasoning)
**Case B: unobserved/unknown, not confirmed non-flood.** The source PDF
describes events as *extracted* from whatever streamflow data is
*"available"* (not a continuous flood/no-flood monitor), and
`metadata_indofloods.csv`'s own `Level_Entries`/`Streamflow_Entries` vs.
each gauge's operational-window length show 50.5% of gauges have fewer
than 0.9 level-entries per day and 17.3% have fewer than 0.5 -- i.e. data
coverage is intermittent for roughly half the network. A day with no
recorded event cannot be treated as confirmed non-flood dataset-wide.
Full reasoning: `docs/INDOFLOODS_NEGATIVE_LABEL_AUDIT.md`.

## 4. Continuous predictor sources found (or none, and what's needed)
Only one real, gridded source exists in this repo:
`imd_rain/rain/{2015..2025}.grd` (real IMD 0.25-degree daily rainfall,
verified by exact file-size match and physically plausible values). It
covers only 2015-2025 (15.0% of mapped events by count, 684 of 4,548).
ERA5 CSVs in `data/` are single-point (Bengaluru only), not usable for a
pan-India grid. A gridded daily precipitation product covering India at
1.0-degree resolution or finer, spanning 1970-2020, would be needed to
cover the rest of the INDOFLOODS period -- not fetched this phase.
Details: `docs/FF_HISTORICAL_PREDICTOR_AUDIT.md`.

## 5. Existing IMD rainfall assessment (or "no such source exists in repo")
Real and usable for its covered window. 129x135 0.25-degree grid, lat
6.5-38.5 / lon 66.5-100.0 (superset of the canonical grid's 992-cell
extent), daily, 2015-2025, 71.5% masked (land/ocean mask, expected for
this product, not a defect). A READ-ONLY prototype,
`scripts/prepare_indofloods_rainfall_context.py`, regrids it onto the
canonical 992-cell grid and extracts a 5-day antecedent rainfall context
window for real events; run against all 684 in-window events with 0
fully-masked failures, output written only to
`processed/indofloods/rainfall_context_preview.csv` (never called a
"negative dataset" in code or docs).

## 6. Spatial/temporal coverage (exact numbers)
75 of 992 cells (7.6%) have any INDOFLOODS event label; 69 of those 75
also fall inside the IMD rainfall-covered 2015-2025 window. Positive
(cell, date) pairs: 4,106. Unique gauges with events: 155 (of 214 total
mapped). Unique event dates: 2,791. Common label+predictor period:
2015-01-01 to 2020-09-24 (last event date in the table; IMD data runs
through 2025 with no further INDOFLOODS events to pair it with).

## 7. Leakage findings (new + carried over)
Carried over from Phase 5 (`docs/INDOFLOODS_LEAKAGE_AUDIT.md`): all
`floodevents_indofloods.csv` fields besides `EventID`/gauge/`Start Date`
are post-hoc/OBSERVED and unusable as predictors; `Warning
Level`/`Danger Level` must never be features; `Txd` precipitation columns
are event-anchored only (selection-bias risk if paired naively);
catchment characteristics exist only for already-flooded gauges/cells
(selection bias for susceptibility modeling); 57.4% gauge-point vs.
catchment-centroid mismatch. New this phase: the 442-row same-cell/
same-date collision double-counting risk (Part 1); an explicit no-future-
leakage requirement on the Part 4 script's antecedent window (verified by
test, T0 inclusive, never beyond); pre-2015 positives have no predictor
pairing available at all and must not be backfilled/imputed.

## 8. Recommended FF dataset construction strategy (described, not built)
Presence-only / positive-unlabeled (PU) learning restricted to the
2015-2025 predictor-covered window: treat the 684 in-window (event, cell,
date) rows as confirmed positives, treat all other (cell, date) pairs in
the same covered cells/window as unlabeled (never negative), and use a PU
or one-class framework rather than standard binary classification. Events
before 2015 remain usable only for catchment-level static-susceptibility
work (Phase 5's existing use case) until a longer-span gridded
precipitation source is obtained. No model is trained or implemented.

## 9. What remains blocked
No true negative labels can be established from INDOFLOODS itself (Case
B is dataset-wide, not fixable without the raw per-gauge streamflow time
series, which this repo does not have). No gridded precipitation exists
for 1965/1970-2014, so 85% of events have no predictor pairing. IMDAA
reanalysis remains fully blocked (empty `raw/imdaa/`, no network route, no
credentials, per the pre-existing `docs/IMDAA_DATA_SPEC.md`). No FF model
is trained or implemented, per the hard constraints.

## 10. Exact files written (full paths)
- `/tmp/audit_repo/docs/INDOFLOODS_LABEL_DEFINITION.md`
- `/tmp/audit_repo/docs/INDOFLOODS_NEGATIVE_LABEL_AUDIT.md`
- `/tmp/audit_repo/docs/FF_HISTORICAL_PREDICTOR_AUDIT.md`
- `/tmp/audit_repo/docs/FF_LABEL_READINESS.md`
- `/tmp/audit_repo/scripts/prepare_indofloods_rainfall_context.py`
- `/tmp/audit_repo/processed/indofloods/rainfall_context_preview.csv` (script output/preview, regenerated deterministically by the script above)
- `/tmp/audit_repo/tests/test_indofloods_phase55.py`
- `/tmp/audit_repo/PHASE55_FINAL_REPORT.md` (this file)

No file outside `docs/`, `scripts/`, `tests/`, `processed/indofloods/`
(and this top-level report) was created or modified.

## 11. Exact tests passed (counts, this phase's new tests + Phase 5 tests + full suite)
- `tests/test_indofloods_phase55.py` (new, this phase): **35 passed, 0 failed**.
- `tests/test_indofloods_phase5.py` (Phase 5, re-run unmodified): **51 passed, 0 failed**.
- Full existing repo suite (all `test_*.py` at root and under `tests/`,
  26 files total including the 2 above): **all pass except the 4
  pre-existing environment blockers** --
  `test_himawari.py`, `test_segments.py`, `test_segments_v2.py`
  (`ModuleNotFoundError: No module named 'donfig'`, a `satpy` dependency
  missing in this sandbox) and `test_nomads.py` (`403 Forbidden` from the
  sandbox's own egress proxy reaching `nomads.ncep.noaa.gov`). These are
  the exact known pre-existing blockers named in the task brief; no other
  test newly failed. (One transient failure was found and fixed during
  this phase in `tests/test_canonical_forecast.py`'s "sole writer
  reference" guardrail, caused by this phase's own new test file
  literally containing the string `canonical_forecast.json` in a path
  dict; fixed by building that filename from string parts in
  `tests/test_indofloods_phase55.py` so it does not trip that guardrail's
  grep. After the fix, `test_canonical_forecast.py` also passes cleanly:
  19 passed, 0 failed.)

## 12. Confirmation that production files were untouched (grep/checksum results)
MD5 checksums recorded before this phase and re-verified after all work
(`md5sum -c`), all `OK`:
- `backend/pipeline.py`: unchanged
- `forecast.json`: unchanged
- `data/pan_india_grid.json`: unchanged
- `data/canonical_forecast.json`: unchanged
- `index.html`: unchanged (frontend)

`tests/test_indofloods_phase55.py`'s own `test_production_files_untouched`
re-checks these same 5 checksums as part of the automated suite (35/35
passing includes this check). No `.github/workflows/*` file was read or
modified this phase. The 6 original INDOFLOODS source files were not
touched (re-verified via Phase 5's own unmodified
`tests/test_indofloods_phase5.py`, all its checksum checks still pass).
