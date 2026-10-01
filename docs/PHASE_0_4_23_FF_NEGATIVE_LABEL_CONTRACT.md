# Phase 0.4.23 — FF Observed Negative-Label Eligibility Contract (Design Only)

This document corrects the earlier, now-retracted claim (`ff_labels_summary.json`/`coverage_report.json`, flagged in Phase 0.4.22) that observed FF labels are impossible due to missing gauge coordinates. They are not missing — `data/metadata_indofloods.csv` has `Latitude`/`Longitude` for all 214 gauges, and `processed/indofloods/indofloods_grid_mapping.csv` already maps all 214 to canonical cells. The real, substantiated gap is the one this document addresses: **no negative label exists yet**, because absence of a recorded event cannot, by itself, be read as a confirmed non-flood day (`docs/INDOFLOODS_NEGATIVE_LABEL_AUDIT.md`, Case B).

## 1. Evidence actually inspected this phase

- `data/metadata_indofloods.csv`: 214 gauges, each with `Start_date`, `End_date`, `Level_Entries`, `Streamflow_Entries`.
- `data/floodevents_indofloods.csv`: 4,548 events, each tied to one `GaugeID` and a `Start Date`/`End Date`.
- `processed/indofloods/indofloods_grid_mapping.csv`: 214/214 gauges `MAPPED` to a `cell_id`.
- `processed/indofloods/indofloods_grid_events.csv`: 4,548 events joined to `cell_id`, all `MAPPED`.
- Recomputed directly this phase (not reused from the earlier Phase 5.5 audit, though the result matches it): for each gauge, `ratio_level = Level_Entries / ((End_date - Start_date).days + 1)`. **106 of 214 gauges (49.5%) have `ratio_level >= 0.9`** — i.e. a level-data entry exists for at least 90% of the days in their own stated operating window. These are the gauges this contract calls "high-coverage."

## 2. The negative-label eligibility contract

A (cell, date) pair is **NEGATIVE_ELIGIBLE** for FF-observed only if every one of the following holds, derived from the data actually available (not copied from a generic template):

1. **The cell contains at least one high-coverage gauge** (`ratio_level >= 0.9` at that gauge). Cells whose only mapped gauge(s) fall below this threshold are not used for negatives — a "no event" day at a sparse-record gauge is UNKNOWN, not negative, exactly as the Phase 5.5 audit concluded.
2. **The date falls within that gauge's `[Start_date, End_date]` operating window.** A date before a gauge existed or after it stopped reporting is out of scope entirely, not a negative.
3. **No flood event exists at that gauge with `Start Date <= date <= End Date`** for any event belonging to that gauge (using the event's own start/end span, not just its start date, since an ongoing multi-day flood's later days are not non-flood days even though the event's own `Start Date` differs).
4. **No flood event exists at *any other* gauge mapped into the same cell with the same date criterion** — consistent with the existing OR-aggregation rule for positives (`docs/INDOFLOODS_LABEL_DEFINITION.md` §3): a cell is only a valid negative if it is clear of events from every gauge inside it, not just the high-coverage one.
5. **The required predictor data exists for that (cell, date)** — in practice, a GFS cycle must be acquirable for that date before the row is usable for training; this contract defines *label* eligibility only, GFS-alignment is a separate, later step (Part 5 below).

This is intentionally stricter than treating "any non-event day" as negative, and stricter than the illustrative example in the phase request — in particular it adds criterion 4 (cell-level clearance across all co-located gauges, not just the anchor gauge), which the project's own OR-aggregation positive-labeling rule makes necessary for consistency: if a positive is "any qualifying gauge in the cell had an event," the matching negative must be "no gauge in the cell had an event," not merely "the specific gauge I'm checking didn't."

## 3. What this contract explicitly does NOT allow

- It does not treat a gauge with `ratio_level < 0.9` as ever contributing a negative, even for calendar days that happen to fall in a locally dense stretch of its record — per-day sub-selection within a low-coverage gauge was considered and rejected this phase as unverifiable without the raw streamflow time series, which is not in this repository (confirmed absent — only the derived event table and entry counts exist).
- It does not shorten a flood event's exclusion window to just its `Start Date` — the full `[Start Date, End Date]` span of every event (own-gauge and cell-co-located) is excluded.
- It does not infer a negative from rainfall data alone (e.g. "it didn't rain much that day, so probably no flood") — that would be constructing a proxy label under the name of an observed one, exactly the confusion this project's FF-proxy track already exists to avoid and keep separate.

## 4. Potential scale (ceiling estimate, not a final dataset)

Computed directly this phase from the 106 high-coverage gauges:

- **61 unique canonical cells** are reachable through at least one high-coverage gauge (down from 75 cells when all 214 gauges, including low-coverage ones, are counted).
- **2,864 unique (cell, date) positive pairs** survive when positives are restricted to high-coverage gauges only (versus 4,106 using all gauges) — this is the usable positive pool under this contract, smaller than the full archive because low-coverage-gauge events are excluded from training (though they remain real events, just not ones that can be paired with a trustworthy negative population around them).
- **Rough candidate negative-day ceiling ≈1,475,466 gauge-days** (sum over the 106 high-coverage gauges of `operating_window_days − event_days`). This is a loose upper bound, not a usable count: it does not yet apply criterion 4 (cell-level clearance across co-located gauges), does not dedupe multiple gauges mapped into the same cell, and does not check GFS-alignability (criterion 5) at all. The true NEGATIVE_ELIGIBLE count is certainly far smaller and was not computed exactly this phase — doing so requires a dedicated script (a natural Phase 0.4.24 deliverable), not a one-off estimate.

## 5. Recommendation

Adopt this contract as the basis for a dedicated `scripts/build_indofloods_negative_labels.py`-style script in a future phase, which would: iterate the 106 high-coverage gauges' operating windows, exclude event-covered spans at the gauge *and* cell level, and emit the exact NEGATIVE_ELIGIBLE (cell, date) table together with a provenance record (gauge(s) checked, coverage ratio, exclusion spans applied) for every row — mirroring the rigor already applied to the positive side in `docs/INDOFLOODS_LABEL_DEFINITION.md`. That script, its output, and its own leakage/correctness tests are out of scope for this design-only phase.
