# FF Label Readiness Report — Phase 5.5, Part 5

All numbers below are computed directly from
`processed/indofloods/indofloods_grid_mapping.csv`,
`processed/indofloods/indofloods_grid_events.csv`, `data/metadata_indofloods.csv`,
and `imd_rain/rain/*.grd`, this phase, and are reproduced by
`tests/test_indofloods_phase55.py`.

**1. Positive flood cell/date examples (after Part 1's OR-aggregation
rule):** **4,106** unique `(cell_id, Start Date)` pairs, deduplicated from
4,548 raw per-gauge event rows. Caveat (Part 1): 442 raw event rows
(9.7%) collapse onto a `(cell, date)` pair shared with another gauge in
the same cell — any count of "events" rather than "cell/date positives"
must not simply be 4,548, or it double-counts co-located-gauge same-day
events.

**2. Unique cells:** **75** of 992 canonical cells (7.6% of the grid).

**3. Unique gauges:** **155** gauges have >=1 flood event (of 214 total
mapped gauges; the other 59 have no catchment characteristics or flood
events).

**4. Unique event (start) dates:** **2,791** distinct calendar dates
across all events.

**5. Can true negatives be established?** **No** (Part 2's Case-B
conclusion). `Start_date`/`End_date`/`Level_Entries`/`Streamflow_Entries`
in `metadata_indofloods.csv` show non-continuous data coverage for the
majority of gauges (50.5% below 0.9 records/day, 17.3% below 0.5), and the
source PDF describes flood events as *extracted* from whatever streamflow
data is *available*, not a continuous flood/no-flood monitor. A day with
no recorded event cannot be read as confirmed non-flood dataset-wide.

**6. Can unknown/background examples be established, and how?** Yes, in
a limited form: for the 69 event cells and 395 event dates that fall
inside 2015-2025 (the real IMD gridded-rainfall coverage window), a
non-event day at the same cell can be paired with real antecedent
rainfall (via `scripts/prepare_indofloods_rainfall_context.py`) to build
an "unknown/background" (not "negative") example set — a day with real
rainfall context but no confirmed flood outcome either way. This is not
attempted as a full labeling scheme here (Part 5.12 below describes the
strategy without implementing it).

**7. Historical predictors available (Part 3/4):** Real, gridded, daily
IMD 0.25-degree rainfall (`imd_rain/rain/2015.grd`...`2025.grd`),
covering the whole canonical grid's spatial extent, for 2015-2025 only.
684 of 4,548 mapped events (15.0%) fall inside this window; a working
prototype (`scripts/prepare_indofloods_rainfall_context.py`) extracted
real antecedent rainfall context (T-5..T0) for all 684 of those events
(4,104 context rows), with 0 fully-masked/unusable cases.

**8. Predictors missing:** (a) Gridded precipitation for 1965/1970-2014
(85% of events, by count, have no gridded rainfall source in this repo at
all); (b) any per-cell-per-day upper-air/atmospheric-instability
predictors (CAPE, wind shear, moisture) at a pan-India gridded scale —
only single-point Bengaluru ERA5 series exist; (c) IMDAA reanalysis
(scaffolding exists, `raw/imdaa/` is empty, confirmed blocked per
`docs/IMDAA_DATA_SPEC.md`); (d) any confirmed-negative-day ground truth
(Part 2).

**9. Earliest/latest common period across label + usable predictor
source:** **2015-01-01 to 2020-09-24** — the intersection of IMD gridded
rainfall availability (2015-2025) and the actual INDOFLOODS event dates
present in this window (last mapped event in the events table is
2020-09-24; IMD files run through 2025 but no INDOFLOODS events exist
past 2020-09-24 in this dataset).

**10. Spatial coverage:** 75 of 992 cells (7.6%) have any label
(positive) history at all; of those, 69 cells (6.95% of 992) have a
label that also falls inside the 2015-2025 IMD-covered period. The IMD
grid's own spatial bounds (lat 6.5-38.5, lon 66.5-100.0) are a superset
of the canonical grid's bounds, so the *rainfall source* itself covers
all 992 cells — the limiting factor is label coverage (only 75/992 cells
have ever had a recorded INDOFLOODS event), not rainfall-source coverage.

**11. Leakage risks (Phase 5's audit plus this phase's findings):**
- Carried over from Phase 5 (`docs/INDOFLOODS_LEAKAGE_AUDIT.md`): all
  `floodevents_indofloods.csv` fields except `EventID`/gauge
  linkage/`Start Date` are OBSERVED/post-hoc and must never be used as
  predictors; `Warning Level`/`Danger Level` must never be features
  (they define the label); `Txd` antecedent precipitation columns are
  event-anchored and only exist for known event dates (selection bias if
  paired naively with "flood" without an equivalent non-event
  population); catchment characteristics exist only for the 155
  gauges/cells that already have events (selection bias for a general
  susceptibility model); gauge-point vs. catchment-centroid mismatch for
  57.4% of gauges.
- New this phase: (a) the 442-row same-cell/same-date collision issue
  (Part 1) is a concrete double-counting risk if any future script counts
  raw event rows per cell instead of deduplicated `(cell, date)` pairs;
  (b) the IMD rainfall context script's own antecedent window ends at the
  event's `Start Date` itself (T0), inclusive — a future model MUST NOT
  use rainfall *after* `Start Date` for that event, and
  `tests/test_indofloods_phase55.py` includes an explicit
  no-future-leakage check on the context script's window construction;
  (c) since only 2015-2025 has gridded rainfall, any positives from
  before 2015 used with a predictor source would be nonsensical/absent —
  a training set must not silently backfill or impute rainfall for those.

**12. Scientifically defensible training strategy (described, not
implemented):** Given (i) no true negatives are establishable dataset-wide,
(ii) usable gridded predictors exist only for 2015-2025, and (iii) the
label is sparse and concentrated (75/992 cells, mostly Western Ghats and a
few northern river systems), a **presence-only / positive-unlabeled (PU)
learning approach restricted to the 2015-2025 predictor-covered window**
is the most defensible starting point:
- Treat the 684 (event, cell, date) positives inside 2015-2025 as
  confirmed positives.
- Treat all other (cell, date) pairs inside the same window and same 69
  (or a wider, rainfall-covered) cell set as **unlabeled** (not negative),
  consistent with Part 2's Case-B conclusion.
- Use a PU-learning framework (e.g. treating unlabeled examples as a
  weighted mixture of hidden positives and true negatives, or a
  one-class/anomaly-style approach anchored on the confirmed positives'
  antecedent-rainfall distribution) rather than standard binary
  classification, which would require true negatives this dataset cannot
  supply.
- Any extension to 1965-2014 events would need either a new gridded
  historical precipitation source (Part 3's stated requirement) or
  restricting those events to catchment-level static-susceptibility
  analysis only (Phase 5's existing use case), not a day-level predictive
  model.
- This is a description of a strategy for a later phase to design and
  implement; no model is trained and no FF system is claimed implemented
  by this phase, per the hard constraints.
