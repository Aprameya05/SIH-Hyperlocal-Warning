# INDOFLOODS Label Definition — Phase 5.5, Part 1

Formalizes the gauge -> cell -> label chain that Phase 5 built the raw
mapping for, and decides how to aggregate multiple co-located gauges into
one cell/date label. All numbers below were computed by actually loading
`processed/indofloods/indofloods_grid_mapping.csv` and
`processed/indofloods/indofloods_grid_events.csv` with `pandas` this phase
(see `tests/test_indofloods_phase55.py` for the reproducible checks).

## 1. The chain

1. **Gauge** (`GaugeID`, `metadata_indofloods.csv`): a physical CWC
   streamflow-measurement station with a fixed `Latitude`/`Longitude`.
2. **Canonical cell** (`cell_id`, `processed/indofloods/indofloods_grid_mapping.csv`):
   the gauge's lat/lon is floored to the enclosing 1.0-degree cell of the
   production 992-cell grid, via `scripts/map_indofloods_to_grid.py`
   (Phase 5, unmodified this phase). This is **point-in-cell on the gauge's
   own coordinates**, not a catchment centroid — see
   `docs/INDOFLOODS_FORENSIC_INVENTORY.md` Section 4 for why (catchment
   centroid disagrees with the gauge-point cell for 57.4% of gauges; the
   gauge point is where the flood was physically observed).
3. **Flood event** (`EventID`, `floodevents_indofloods.csv`): a discrete
   streamflow-threshold-crossing episode at one gauge, with `Start Date`
   the date flow first exceeded that gauge's `Warning Level`.
4. **Cell/date event label**
   (`processed/indofloods/indofloods_grid_events.csv`, produced by the
   same Phase 5 script): each event row carries its gauge's `cell_id`
   alongside the original `EventID`/`GaugeID`/`Start Date`/`End Date`/
   `Flood Type` — `GaugeID` is preserved explicitly on every row, exactly
   as this phase's brief requires; no row anywhere replaces the gauge
   point with a catchment centroid.

`cell_event(cell, date)` is then a further reduction of step 4's table:
"did any qualifying gauge event in this cell start on this date."
Catchment polygon geometry (`catchments_shapefiles_indofloods.zip`) is
**not** used to build this label — it remains available only as a
separate, optional static feature source per the Phase 5 decision.

## 2. Multi-gauge cells: the actual data

48 of the 992 grid cells contain more than one mapped gauge (out of 214
mapped gauges total, all `MAPPED`, 0 unmapped). For each of these 48 cells,
this phase computed, directly from the two Phase 5 CSVs:

- number of gauges mapped into the cell,
- number of those gauges that have >=1 flood event,
- total per-gauge event rows in the cell,
- number of *distinct* `Start Date` values in the cell (i.e. what the
  cell/date label collapses those event rows to),
- how many event rows collapse onto a shared date (their difference).

Full per-cell table (48 rows) reproduced by
`tests/test_indofloods_phase55.py`. Headline results:

- 34 of the 48 multi-gauge cells have at least one flood event; 14 have
  none (their gauges recorded no qualifying event in this dataset).
- Across the 34 event-bearing multi-gauge cells: **4,548 event rows collapse
  to 4,106 unique (cell, date) positive pairs when reduced across the
  whole dataset** — i.e. 442 event rows (about 9.7% of all 4,548) share a
  `(cell_id, Start Date)` pair with at least one other event row from a
  *different* gauge in the same cell.
- The largest single case is cell `9.0_76.0` (10 co-located gauges,
  Kerala/Western Ghats area): 413 raw event rows collapse to 309 distinct
  dates (104 same-cell/same-date collisions). Cell `12.0_77.0` (3 gauges)
  has 671 raw events -> 601 distinct dates (70 collisions). Both are
  dense, multi-gauge river networks where independent upstream and
  downstream gauges genuinely do flood on the same calendar day during a
  shared monsoon system.
- Spot-checking cell `9.0_76.0`'s earliest collisions (gauges
  `INDOFLOODS-gauge-429` and `-440`) shows event windows that are close in
  time (days apart) but the exact same `Start Date` is comparatively rare
  relative to total events — most of the 442 collisions are two
  *different* gauges independently crossing their own warning levels on
  the same day during a shared regional rain system, not one flood
  double-entered under two `EventID`s (each `EventID` is unique and tied
  to a distinct `GaugeID`; there is no duplicate-`EventID` case anywhere
  in the 4,548 rows).

## 3. Aggregation rule and justification

**Rule adopted: `cell_event(cell, date) = 1` if ANY qualifying gauge in
that cell has a flood event with that `Start Date`.** I.e. OR-aggregation
across co-located gauges, for the positive label only.

Justification, against the actual co-located-gauge data above:

- A flood event recorded at any gauge is a real, physically occurring
  hydrological event within that gauge's cell's area (the gauge is
  physically inside the cell by construction of the point-in-cell
  mapping). OR-aggregation for a *positive* label does not manufacture
  information — it only says "something we know happened in this cell,
  happened."
- The edge case the brief asks about — multiple gauges in the same cell
  recording what's "clearly the same physical flood system" — is real
  (e.g. `9.0_76.0`, `12.0_77.0`) but is **not a labeling defect** for a
  positive/binary `cell_event` label: whether the two gauges are
  independently reacting to one regional storm or to two hydrologically
  unrelated events, the cell did experience a flood-threshold-crossing
  event on that day either way. OR-aggregation is defensible for a
  presence label.
- Where it **would** be a problem: if a downstream analysis counted
  *number of events per cell/date* (severity or frequency) using the raw
  4,548 gauge-level rows without deduplicating by cell/date, it would
  inflate apparent event frequency by the 442-row collision count (9.7%)
  and specifically overstate the 34 multi-gauge event-bearing cells'
  flood frequency relative to single-gauge cells. **This is the residual
  caveat**: `cell_event` must be built by deduplicating on
  `(cell_id, Start Date)` before counting, not by summing per-gauge event
  rows per cell.

## 4. Residual caveats

1. **Binary presence only.** The OR-aggregation rule is justified for a
   yes/no "did this cell flood on this date" label. It is explicitly NOT
   validated here for any severity/magnitude aggregation (e.g. "worst
   `Flood Type` among co-located gauges") — that would need a separate
   decision.
2. **Double-counting risk persists in any raw, non-deduplicated use of
   `indofloods_grid_events.csv`.** Any future script that counts events
   per cell (rather than per cell/date) must dedupe on
   `(cell_id, Start Date)` first, or it will double count the 442
   collision rows identified above. `tests/test_indofloods_phase55.py`
   encodes this as an explicit check.
3. **Gauge-point/catchment mismatch (carried over from Phase 5)** still
   applies per-gauge: the `cell_id` a gauge's event is attributed to is
   the cell containing the gauge point, not necessarily the cell that
   contains most of that gauge's catchment area.
4. **`GaugeID` is preserved on every row** of
   `indofloods_grid_events.csv` (this phase did not touch that file); any
   future derived table must keep doing so, per this phase's hard
   constraint, so per-gauge provenance of a cell/date label is always
   recoverable.
