# Phase 0.4.6 — Historical GFS ↔ Observed Hazard Label Alignment Contract

This is a formal join contract between a historical GFS forecast record and
an observed hazard label record. It is descriptive of what the repository's
actual label-engine artifacts (`processed/labels/*.csv`,
`processed/indofloods/*.csv`) already produce — this phase does not create
new labels, only defines and tests the alignment rule against real,
already-existing rows.

## FORECAST RECORD (produced by the historical GFS pilot, Phase 0.4.5)

```
initialization_time    -- GRIB dataDate+dataTime, UTC (e.g. "2020-07-15 0000")
forecast_lead_hours    -- GRIB forecastTime (e.g. 3, 6)
valid_time             -- GRIB validityDate+validityTime, UTC (initialization_time + lead)
cell_id                -- canonical 992-cell grid ID, via regrid.cell_id_for(lat, lon)
```

## LABEL RECORD (produced by the existing label-engine scripts)

```
observation_time/date  -- the label's own timestamp; FORMAT AND TIMEZONE VARY BY HAZARD (see below) -- this is itself a finding, not an assumption
cell_id                -- canonical 992-cell grid ID (same cell_id_for convention)
hazard                 -- "ts" | "cb" | "ff"
label                  -- 1 | 0 | null
label_status           -- POSITIVE | NEGATIVE_CONFIRMED | UNKNOWN | PROXY_NOT_OBSERVED
```

Confirmed directly from `processed/labels/ts_labels.csv`,
`processed/labels/cb_labels.csv`, `processed/labels/ff_labels_proxy.csv`
(real files, already on disk, read this phase) and
`docs/LABEL_ENGINE.md` (which documents the identical schema — this phase
verifies that documentation against the actual CSV headers rather than
trusting it, and the headers match: `timestamp, cell_id, hazard, label,
label_status, source, source_event_id, source_timestamp,
spatial_distance_km, temporal_distance_hours, quality_flag[, temporal_resolution, threshold_mm]`).

**Important, verified-by-reading finding**: each hazard's `timestamp` column
uses a **different convention**:
- TS: `"{date}T{IST_slot_window}"`, e.g. `"2020-07-15T0601-1200"` — an IST
  calendar-date + IST slot-window string.
- CB / FF proxy: `"{date}T slot{N}"`, e.g. `"2020-07-15T slot1"` — same
  underlying slot concept, different literal string format (includes a
  space, uses a slot index rather than the IST window boundaries spelled
  out). **This is a real, pre-existing inconsistency across the three
  label files, not introduced by this phase** — any code joining across
  hazards must not assume these strings are directly comparable without a
  separate slot→window lookup for each.
- All three ultimately key off the same 4-slot IST calendar-day structure
  (`SLOT_WINDOWS_IST` in `lead_time.py` / `metar_ground_truth.py`): slot 0 =
  00:00–05:59 IST, slot 1 = 06:00–11:59 IST, slot 2 = 12:00–17:59 IST,
  slot 3 = 18:00–23:59 IST, all for one IST calendar date.

## THE JOIN: forecast valid time → observed hazard window

**Step 1 — convert `valid_time` (UTC) to IST**: `valid_time_ist =
valid_time_utc + 5:30`.

**Step 2 — determine the IST calendar date and slot** that `valid_time_ist`
falls into, using the same `SLOT_WINDOWS_IST` table production already uses
(not a new or different table — reusing
`lead_time.SLOT_WINDOWS_IST`/`metar_ground_truth.SLOT_WINDOWS_IST`, which
the repository's own `test_lead_time.py` already cross-checks for mutual
consistency).

**Step 3 — look up the label row** for `(cell_id, ist_date, slot)` in the
hazard's label file, using that hazard's own timestamp string convention
(Step 2's slot number formatted per the hazard-specific pattern above).

**Step 4 — apply the per-hazard rules below.**

### Per-hazard alignment rules

| | Target window start | Target window end | Observation timestamp convention | Spatial mapping | Acceptable temporal tolerance | Positive | Negative | Unknown |
|---|---|---|---|---|---|---|---|---|
| **TS** | IST slot start (e.g. 06:00 IST) | IST slot end (e.g. 11:59 IST) | Real station observation(s) aggregated over the IST slot window (`metar_ground_truth.py`'s rule: TS if ANY obs in-window shows TS; else NO-TS only once the window has closed) | Station-to-cell: VOBL (12.97°N/77.58°E) → nearest canonical cell `IND_13.0_78.0` ONLY. All 991 other cells: always UNKNOWN (no pan-India TS observation network exists — verified, not assumed, Part 1 below) | Zero — an observation must fall inside the exact IST slot window to count; a mid-slot forecast valid time does not get a "nearest" observation from an adjacent slot | `label=1`, `label_status=POSITIVE` | `label=0`, `label_status=NEGATIVE_CONFIRMED` — **genuinely a confirmed negative**, because a closed observation window with ≥1 real METAR/station read and none showing TS is a real, continuous-monitoring-backed absence, not an unlabeled gap | `label_status=UNKNOWN` for every cell other than VOBL's, and for VOBL itself if the slot window hasn't closed yet or no observation exists in it |
| **CB** | IST slot start | IST slot end | IMD 0.25° gridded **daily** rainfall, re-applied identically to all 4 slots of that IST calendar date (the source has no finer temporal resolution — this is stated as a limitation, not hidden) | Grid-to-grid: each canonical cell takes the max daily rainfall among all IMD 0.25° fine cells whose center falls in its ±0.5° box | Slot-level tolerance is meaningless here since the source is daily, not slot-resolved — the true tolerance is **1 calendar day**, honestly labeled `temporal_resolution="daily"` | `label=1`, `label_status=POSITIVE` (daily max rainfall ≥ 64.5mm — the ACTUAL threshold the executed code uses, not the 100mm the module docstring incorrectly claims, per `docs/LABEL_ENGINE.md`'s own correction, re-verified this phase) | **Only stored implicitly**: `processed/labels/cb_labels.csv` is a **sparse, POSITIVE-only file** (verified this phase: 452 of 452 real rows for cell `IND_13.0_77.0` are all `label=1`/`POSITIVE`; zero `NEGATIVE_CONFIRMED` rows exist anywhere in the file). A day/cell/slot combination absent from this file means "daily rainfall at this cell did not reach 64.5mm" — a **genuinely inferable negative** (IMD's gridded rainfall is continuous, not event-only, so "no row" here is NOT the same ambiguity as INDOFLOODS' "no event"), but this inference is NOT materialized as an explicit row in the current file, and any consumer must know this convention rather than discovering it from the data alone | `label_status=UNKNOWN` only for dates/cells genuinely outside IMD's coverage (before 2015, after 2025, or outside the IMD grid's domain before clipping to the canonical grid) |
| **FF** (two separate sub-labels — never merged, per the brief) | IST slot start | IST slot end | **A. Observed INDOFLOODS event**: gauge-level flood event window (`Start Date`–`End Date`, daily resolution), spatially joined via real gauge lat/lon (see Part 6). **B. Rainfall proxy**: same IMD daily rainfall source as CB, different threshold (3-day cumulative ≥100mm AND daily ≥40mm) | **A**: point-in-cell (gauge lat/lon → canonical cell containing it). **B**: grid-to-grid, same max-over-fine-cells method as CB | **A**: 1 calendar day (INDOFLOODS events are recorded as daily start/end dates, no finer resolution). **B**: 1 calendar day, same as CB | **A**: `label=1`, `label_status=POSITIVE` for a cell+date inside a real, spatially-assigned flood event's window. **B**: `label=1`, `label_status=PROXY_NOT_OBSERVED` (never `POSITIVE` — explicitly distinguished so it cannot be mistaken for an observed event) | **A**: **no confirmed negative exists or should ever be manufactured** — gauge monitoring is intermittent/event-triggered (Phase 5.5's established finding, re-confirmed this phase), so absence of a recorded event at a cell/date is `label_status=UNKNOWN`, never `0`. **B**: same non-claim — the proxy's absence is `PROXY_NOT_OBSERVED` with `label=0`-equivalent-in-spirit but is not a real negative of anything, since the proxy never claimed to observe floods in the first place | **A**: `UNKNOWN` for every cell/date without an assignable, spatially-confirmed event (the overwhelming majority of cell/date combinations) |

## Safety rule, applied uniformly

**`UNKNOWN` is never collapsed into `0`/`NEGATIVE_CONFIRMED` anywhere.**
`docs/LABEL_ENGINE.md` names this test as `test_unknown_not_collapsed_to_negative`;
the actual test, read directly this phase, is `tests/test_panindia_labels.py::test_7_unknown_not_collapsed`
(the doc's name is slightly off — a minor, harmless documentation drift, noted
here rather than silently repeated). It and the other 10 tests in that file
all pass (11/11, confirmed this phase — see Testing section of the
companion report).
