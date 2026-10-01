# Phase 0.4.16 — Historical GFS → Observed TS Label Dataset

Dataset construction only. No model trained, tuned, or evaluated. No
production code, deployment workflow, or pan-India heuristic layer
touched. No commit, no push.

## 0. Verification of the Phase 0.4.15 GREEN claim (done before touching anything)

Before building anything, `docs/PHASE_0_4_15_results.json` was read
directly (not assumed from the user's summary of it). All 6 oversized
Stage B entries show `"failures": []` and `"warnings": []`, with
`sha256` matching `manifest_sha256`, the expected `slot_id`/
`event_group_key`/`label_status`/`partition` for every file, confirming
Phase 0.4.15 is genuinely GREEN — independently re-checked, not taken on
faith. All 12 Stage B files, plus the 2 pre-existing pilot cycles
(already regenerated under the Phase 0.4.12 contract in that phase), are
therefore eligible source material for this phase.

## 1. Source files used — and an honest execution gap

Eight cycles make up the intended complete dataset:

| cycle | leads | role | rows if built |
|---|---|---|---|
| 2015030300 | f003, f009 | existing pilot | 2 |
| 2020071500 | f003, f006 | existing pilot | 2 |
| 2016011500 | f003, f006 | Stage B | 2 |
| 2017041606 | f003, f006 | Stage B | 2 |
| 2019120918 | f003, f006 | Stage B | 2 |
| 2021072406 | f003, f006 | Stage B | 2 |
| 2022081512 | f003, f006 | Stage B | 2 |
| 2023110612 | f003, f006 | Stage B | 2 |

**This session could only build 5 of the 8 cycles (10 of the intended 16
rows).** The same 400 MB device-staging limit and unavailable device
shell that blocked Phase 0.4.14/0.4.15 from directly inspecting the three
largest Stage B cycles (`2021072406`, `2022081512`, `2023110612` — each
pair 518–526 MB) also blocks this session from reading their GRIB
content to extract feature values. This is not a new problem — it is the
same environment limitation, now hit by a different task. Phase 0.4.15
confirmed those 3 cycles' *files* are correct (header/trailer, grid,
cycle/lead, event-group key, labels, SHA256 against manifest); this
phase could not go further and actually *extract* CAPE/CIN/PWAT/etc. from
them in this session, because that requires opening the file, which is
exactly what's blocked.

**What this phase delivers**: the complete, tested dataset-construction
script (`scripts/build_phase_0_4_16_historical_gfs_ts_dataset.py`),
run successfully against the 5 accessible cycles (producing a real,
fully-extracted 10-row dataset as proof the script works end-to-end), and
the exact command for you to re-run it on your own machine where all 8
cycles' files already sit on disk — that run will produce the complete
16-row dataset without this session's limitation. The 10-row dataset
delivered with this phase is real (every value genuinely extracted from
the GRIB files, nothing invented) but **incomplete** — it does not yet
include the 2021/2022/2023 Stage B cycles.

Source files actually used this run (5 cycles, 10 files — exact SHA256
values recorded in the output manifest, reused from each cycle's own
`build_pilot()` call, not recomputed by hand):

```
gfs.0p25.2015030300.f003.grib2
gfs.0p25.2015030300.f009.grib2
gfs.0p25.2020071500.f003.grib2
gfs.0p25.2020071500.f006.grib2
gfs.0p25.2016011500.f003.grib2
gfs.0p25.2016011500.f006.grib2
gfs.0p25.2017041606.f003.grib2
gfs.0p25.2017041606.f006.grib2
gfs.0p25.2019120918.f003.grib2
gfs.0p25.2019120918.f006.grib2
```

Skipped this run (files not present in this session's environment, not
dropped for any content reason):

```
gfs.0p25.2021072406.f003.grib2, gfs.0p25.2021072406.f006.grib2
gfs.0p25.2022081512.f003.grib2, gfs.0p25.2022081512.f006.grib2
gfs.0p25.2023110612.f003.grib2, gfs.0p25.2023110612.f006.grib2
```

## 2. Number of rows

**10** (this run). The complete dataset, once you re-run the script with
all 8 cycles' files present, will have **16** rows (10 here + 6 from the
three skipped cycles, 2 rows each).

## 3. Number of unique event groups

**6** (this run): `IND_13.0_78.0|2015-03-03|1`, `IND_13.0_78.0|2015-03-03|2`,
`IND_13.0_78.0|2020-07-15|1`, `IND_13.0_78.0|2016-01-15|1`,
`IND_13.0_78.0|2017-04-16|2`, `IND_13.0_78.0|2019-12-10|0`. The complete
dataset will have **9** (adding `IND_13.0_78.0|2021-07-24|2`,
`IND_13.0_78.0|2022-08-15|3`, `IND_13.0_78.0|2023-11-06|3`).

## 4. Positive labels

**3** rows this run (`2015-03-03` slot 1, `2017-04-16` slot 2 ×2 rows).
Complete dataset: **7** (adding `2021-07-24` ×2 and `2023-11-06` ×2).

## 5. Negative labels

**7** rows this run (`2015-03-03` slot 2, `2020-07-15` slot 1 ×2,
`2016-01-15` slot 1 ×2, `2019-12-10` slot 0 ×2). Complete dataset: **9**
(adding `2022-08-15` ×2).

## 6. Unknown/unlabeled rows

**0.** No row in this run has `label_status` other than `POSITIVE` or
`NEGATIVE_CONFIRMED` — every target slot was already confirmed against
the real `ts_labels.csv` in Phase 0.4.13/0.4.14/0.4.15.

## 7. Feature columns

Predictor (forecast-derived) columns: `cape`, `cin`, `pwat_mm` (GFS
precipitable water — explicitly NOT called IWV anywhere in this dataset
or its manifest), `u850`, `v850`, `u200`, `v200`,
`wind_shear_850_200_ms`, `t850_k`, `t700_k`, `t500_k`, `rh850_pct`,
`rh700_pct`, `k_index`, `totals_totals` (both via the existing
Magnus-Tetens-derived dewpoint formula from Phase 0.4.5/0.4.12 — no new
formula introduced), `gfs_prate_kg_m2_s` (+ `gfs_prate_stepType_used`/
`gfs_prate_startStep`/`gfs_prate_endStep`/`gfs_prate_note` provenance),
`gfs_precip_3h_interval_mm` (+ `_note`).

Target/label columns (never used as predictors — see leakage section):
`label`, `label_status`, `label_timestamp`, `label_source`,
`label_definition`.

Identity/provenance columns: `initialization_time_utc`, `valid_time_utc`,
`valid_time_ist`, `forecast_lead_hours`, `slot_id`, `ist_date`,
`event_group_key`, `cell_id`, `source`, `archive_source`, `source_file`,
`source_cycle`, `cycle_role`, `missing_features`,
`train_holdout_partition`.

## 8. Missingness by feature (this run, 10 rows)

| feature | n_missing | pct |
|---|---|---|
| cape, cin, pwat_mm, u850, v850, u200, v200, wind_shear_850_200_ms, t850_k, t700_k, t500_k, rh850_pct, rh700_pct, k_index, totals_totals, gfs_prate_kg_m2_s | 0 | 0.0% |
| gfs_precip_3h_interval_mm | 6 | 60.0% |

All 15 core forecast fields plus prate were present and extractable in
every one of the 10 rows. `gfs_precip_3h_interval_mm` is missing for 6 of
10 rows — by contract, not by accident: every lead-start row (no lead-0
`tp` exists to difference against) and the 2015-03-03 lead-end row (its
`tp` accumulation window starts at step 6, not 0 — the same
incompatible-window finding from Phase 0.4.10B) are correctly left null
with an explicit reason in `gfs_precip_3h_interval_mm_note`. No interval
value was fabricated or backfilled.

## 9. Precipitation provenance counts (this run)

`gfs_prate_stepType_used`: `avg` ×6 (2015-03-03 both rows, 2016-01-15
both rows, 2017-04-16 both rows), `instant` ×4 (2020-07-15 both rows,
2019-12-10 both rows) — consistent with the archive-inconsistency finding
from Phase 0.4.12/0.4.14: some cycles only have an `avg` prate message,
others have both `instant` and `avg` (this extractor always prefers
`instant` when present).

`gfs_precip_3h_interval_mm`: available for 4 of 10 rows (the lead-end row
of 2020-07-15, 2016-01-15, 2017-04-16, 2019-12-10 — all four pairs whose
`tp` windows both start at step 0); missing for 6 of 10 (both
2015-03-03 rows, and the lead-start row of every other cycle).

## 10. Forecast lead distribution (this run)

`forecast_lead_hours = 3`: 5 rows. `forecast_lead_hours = 6`: 4 rows.
`forecast_lead_hours = 9`: 1 row (the 2015-03-03 second lead).

## 11. IST slot distribution (this run)

slot 0: 2 rows (2019-12-10). slot 1: 4 rows (2015-03-03 lead-1,
2020-07-15 ×2, 2016-01-15 ×2 — wait, see exact breakdown below). slot 2:
3 rows (2015-03-03 lead-2, 2017-04-16 ×2). Exact counts:

```
slot_id=0: 2 rows (2019-12-10, both leads)
slot_id=1: 5 rows (2015-03-03 f003; 2020-07-15 f003+f006; 2016-01-15 f003+f006)
slot_id=2: 3 rows (2015-03-03 f009; 2017-04-16 f003+f006)
slot_id=3: 0 rows this run (2022-08-15/2023-11-06 are slot 3, not yet built)
```

## 12. Train/holdout partition counts (this run)

`train`: 10. `holdout`: 0. `unassigned`: 0. Computed via the real
`assign_partition()` for every row's `ist_date`, not assumed — all 10
genuinely fall in 2015–2023.

## 13. Duplicate checks

`(source_file, cell_id, forecast_lead_hours)` triple: **0 duplicates**
across all 10 rows (10 unique triples for 10 rows).

## 14. Event-group collision checks

For every `event_group_key`, all rows sharing it were checked to agree
on `ist_date`, `slot_id`, and `cell_id`: **0 failures** — every key's
rows are internally consistent, and no two different (date, slot) pairs
produced the same key.

`validate_no_event_group_key_crosses_partitions()` run across the full
10-row combined dataset (not just within a cycle): **passes** — no
event_group_key appears in more than one partition (trivially true here,
since all 10 rows are `train`, but checked programmatically rather than
assumed).

## 15. Label distribution

3 POSITIVE rows (2 event groups), 7 NEGATIVE_CONFIRMED rows (4 event
groups), 0 UNKNOWN, 0 UNMATCHED.

## 16. Explicit limitations

- **This dataset is incomplete**: 10 of the intended 16 rows, 6 of 9
  event groups. The 3 missing cycles (2021072406, 2022081512, 2023110612)
  are confirmed-correct *files* (Phase 0.4.15) but their feature values
  have not yet been extracted, because this session cannot open files
  over 400 MB. See the exact command below to complete it.
- This is a **historical Bengaluru/VOBL pilot dataset**, not a pan-India
  predictor dataset. It contains a single `cell_id`
  (`IND_13.0_78.0`) throughout. It is not mixed with, and does not
  inform, the pan-India heuristic hazard layer.
- 16 rows (even once complete) is far below anything trainable. No model
  performance is reported because no model was trained — this phase is
  dataset construction only.
- `k_index`/`totals_totals` use a derived (Magnus-Tetens) dewpoint, not
  GFS's native dewpoint field (none exists at isobaric levels in this
  archive) — not bit-identical to `backend/pipeline.py`'s live
  computation even though the formula is the same (documented since
  Phase 0.4.5/0.4.12, not new here).
- CTT drop-rate and the spatial-convergence feature used by production
  are not present in this dataset at all — structurally unavailable from
  a single point extraction per historical cycle.
- `gfs_prate_kg_m2_s` is never called `qpe_mm` and is not claimed
  equivalent to production's live QPE field.

## Feature contract vs. leakage

Every predictor column is classified `FORECAST_DERIVED` in
`FEATURE_LEAKAGE_CLASS` (`scripts/build_vobl_historical_gfs_ts_join.py`,
unchanged this phase) — each one is a model output computed entirely from
the GFS run's own initialization-time analysis and physics integration,
describing a *future* valid time but never reading back any observation
made after initialization. `features_using_future_information` is empty
in every per-cycle manifest this phase produced. The label fields
(`label`, `label_status`, `label_timestamp`, `label_source`,
`label_definition`) are never read back into any predictor column —
checked structurally, since they are written from an entirely separate
source (`ts_labels.csv`) that the feature-extraction code path never
touches.

## Tests

New file: `tests/test_phase_0_4_16_historical_gfs_ts_dataset.py` — 12
tests, written to hold against whatever subset of the 8 cycles is
actually present in a given environment (skips, never fabricates, when
none are):

- `test_every_row_maps_to_a_real_source_file_on_disk`
- `test_no_duplicate_source_file_cell_lead_rows`
- `test_event_group_key_is_deterministic`
- `test_same_cycle_f003_f006_rows_share_event_group_key_where_applicable`
- `test_temporal_partition_is_computed_not_assumed`
- `test_no_event_group_key_crosses_partitions`
- `test_invalid_tp_intervals_remain_missing_not_fabricated`
- `test_precipitation_provenance_columns_are_preserved`
- `test_pwat_column_is_named_pwat_not_iwv`
- `test_missing_values_are_nan_not_zero`
- `test_label_values_conform_to_established_ts_contract`
- `test_no_production_code_imports_the_phase_0_4_16_builder`

One pre-existing test needed a one-line addition (not a weakening), the
same pattern as Phases 0.4.12/0.4.15:
`test_no_production_code_imports_research_builder_repo_wide` in
`tests/test_vobl_historical_gfs_ts_join.py` now also excludes
`scripts/build_phase_0_4_16_historical_gfs_ts_dataset.py`, which
legitimately imports `build_vobl_historical_gfs_ts_join` by design.

Results (this environment, 5 of 8 cycles present):
- New file alone: **12/12 passed**.
- Combined with every prior historical-GFS-related test file
  (`test_vobl_historical_gfs_ts_join.py`,
  `test_historical_dataset_split.py`,
  `test_phase_0_4_14_stage_b_verification.py`,
  `test_phase_0_4_15_verify_oversized_stage_b.py`,
  `test_phase_0_4_16_historical_gfs_ts_dataset.py`): **85/85 passed**.
- Full suite (excluding the same 4 pre-existing environment-only
  failures as every prior phase — `test_himawari.py`, `test_nomads.py`,
  `test_segments.py`, `test_segments_v2.py`): **261/261 passed** (up from
  the Phase 0.4.15 baseline of 249; +12, exactly the new test file;
  nothing weakened).

## Exact command to complete the dataset (run on your machine)

```bash
python scripts/build_phase_0_4_16_historical_gfs_ts_dataset.py
```

Run from the repository root. It will find all 8 cycles' files already
on disk under `data/external/historical_gfs/raw/`, build all 8 (not just
5), and overwrite `data/processed/historical_gfs_ts/historical_gfs_ts_dataset.csv`
and `..._manifest.json` with the complete 16-row dataset. Expect its
printed `validation_status` to read `GREEN` (not `YELLOW`) once all 8
cycles build successfully — `YELLOW` there means cycles were skipped, so
if it still shows `YELLOW` on your machine, check which cycle's files are
missing or unreadable before trusting the output further.

## PHASE_0_4_16_DATASET_BUILD = YELLOW

**Why YELLOW, not GREEN**: the dataset built and verified in this session
is internally consistent, leak-free, and fully contract-compliant on
every row it contains (0 duplicate rows, 0 event-group collisions, 0
fabricated values, 0 silently-zeroed missingness) — but it covers 5 of 8
cycles, 10 of 16 rows, because this session cannot open 3 of the Stage B
files. This is a coverage gap, not a defect, so YELLOW rather than RED.

**Why not GREEN**: an honest GREEN requires the complete 8-cycle, 16-row,
9-event-group dataset the Phase 0.4.13 acquisition plan targeted — not
yet produced in full by this session.

**Why not RED**: no file failed to parse, no label mapping was ambiguous,
no feature required inventing a value, precipitation semantics were
determined correctly for every row built, event grouping is fully
consistent, every source file traces back to the manifest, and no feature
used observed target information. None of the RED stop-conditions in the
brief were triggered.
