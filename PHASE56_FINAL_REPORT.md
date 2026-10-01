# PHASE 5.6 COMPLETE

## Files created/modified (exact paths, all new — no existing file modified)
- `/tmp/audit_repo/scripts/compute_indofloods_imd_overlap.py` (new, read-only overlap computation, Task 5)
- `/tmp/audit_repo/tests/test_indofloods_phase56.py` (new, tests for the above)
- `/tmp/audit_repo/docs/PHASE_5_6_HISTORICAL_DATA_DECISION.md` (new, 15-section decision doc, Task 10)
- `/tmp/audit_repo/PHASE56_FINAL_REPORT.md` (this file)

No other file was created or modified. `backend/pipeline.py`, `forecast.json`,
`data/pan_india_grid.json`, `data/canonical_forecast.json`, `index.html`,
and all `.github/workflows/*.yml` were re-verified byte-unchanged by MD5
checksum before and after this phase (all `OK`).

## Tests run and pass/fail counts
- `tests/test_indofloods_phase56.py` (new, this phase): **4 passed, 0 failed**.
- `tests/test_indofloods_phase5.py` + `tests/test_indofloods_phase55.py`: all passing (unchanged from Phase 5.5, re-run this phase).
- Full repo suite (`python3 -m pytest`, all `test_*.py` at root and under `tests/`): **151 passed, 0 newly failed**, with exactly the 4 known pre-existing blockers as collection/runtime errors: `test_himawari.py`, `test_segments.py`, `test_segments_v2.py` (`ModuleNotFoundError: No module named 'donfig'`) and `test_nomads.py` (403 from the sandbox's own egress proxy to `nomads.ncep.noaa.gov`). No new failures introduced.

## Exact dataset counts from Task 5 (computed this phase, read-only, against real files)
- IMD `.grd` coverage: 2015-01-01 to 2025-12-31.
- INDOFLOODS events with `Start Date` inside that window: 684 of 4,548 raw mapped events.
- Earliest/latest common event date: 2015-05-18 to 2020-09-24.
- Unique event (cell, date) pairs in the overlap: 620, across 69 unique cells and 131 unique gauges.
- Rainfall days available across those 69 cells: 277,242.
- Candidate unlabeled/background cell-days (real rainfall, no recorded event; never called "negative"): 276,622.

## Remaining blockers
- No gridded precipitation for 1965/1970-2014 (85% of raw events have no predictor pairing).
- IMDAA: fully blocked — no network route from this sandbox (403 from the sandbox's own proxy), no credentials, `raw/imdaa/` confirmed empty.
- MERA: no credentials/config anywhere in the repo; official access requires NCMRWF registration/approval; even if resolved, its ~2020-2025 coverage barely overlaps INDOFLOODS' own event record (last event 2020-09-24).
- No true negatives are establishable from INDOFLOODS itself (Case B, dataset-wide, unfixable without raw per-gauge streamflow data this repo lacks).
- No pan-India gridded CAPE/K-index/shear predictors exist (only single-point Bengaluru ERA5).

## Recommended next phase
Implement the positive-unlabeled (PU) FF trigger model on the 620 in-window
(cell, date) positives against the 276,622 candidate background cell-days,
temporally split at 2015-2018 (train) vs. 2019-2020-09-24 (validate), plus
a spatial holdout of the highest-volume cell, using only rainfall-window
and catchment-characteristic predictors (deferring CAPE/K-index/shear until
IMDAA access is resolved).
