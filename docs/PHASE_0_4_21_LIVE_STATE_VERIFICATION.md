# Phase 0.4.21 — Live Acquisition + Dataset State Verification

## 1. Verification timestamp

Verification performed 2026-10-01, against live inspection of the user's connected repository folder (`C:\Users\Aprameya\OneDrive\Pictures\Desktop\SIH-Hyperlocal-Warning`) via the device bridge, plus staged copies of the small manifest/CSV files for exact parsing. `device_bash` (a shell on the user's machine) reported "Workspace unavailable" for this session, so filesystem listing used `device_list_dir` and file content verification used `device_stage_files` + local parsing of the staged copies — both are read-only operations; nothing was modified, moved, or deleted on the user's machine.

## 2. Repository state inspected

`data/external/historical_gfs/` tree (recursive listing) and `docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv`, staged and parsed directly: `data/external/historical_gfs/manifest.json` (acquisition provenance), `phase_0_4_20_train.csv` + `phase_0_4_20_train_manifest.json`, `phase_0_4_20_holdout.csv` + `phase_0_4_20_holdout_manifest.json`. No `phase_0_4_20_all.csv` exists (confirmed absent from the directory listing).

## 3. Acquisition root inspected

The build manifests themselves record the raw root actually used: `"raw_root": "D:\\SIH-Historical-GFS\\raw"` (both `phase_0_4_20_train_manifest.json` and `phase_0_4_20_holdout_manifest.json`, field `raw_root`). This is **not** the repository's own `data/external/historical_gfs/raw/` directory — that directory still exists and contains 24 GRIB2 files from an earlier/different cycle set (2015030300, 2016011500, 2017041606, 2019120918, 2020071500, 2021072406, 2022081512, 2023110612 — none of which are among the 40 files the current Phase 0.4.17 manifest actually requires). That directory is a stale leftover from earlier work and was correctly **not** used by this build; it is not a discrepancy in the current result, since `--raw-root D:\SIH-Historical-GFS\raw` was explicitly passed (per the manifest's own provenance field) and every one of the 40 required files was found and read from there. `D:\SIH-Historical-GFS\raw` itself is not a folder connected to this session, so its contents were not listed directly — the evidence below instead relies on a SHA256 cross-check between the Phase 0.4.19 acquisition provenance (`manifest.json`, written when the files were originally verified) and a sha256 computed fresh, directly from the files, by the build run that produced the current TRAIN/HOLDOUT output today (`sha256_of()` reads actual file bytes — see `scripts/build_vobl_historical_gfs_ts_join.py:227-232`). These are two independently-generated hashes of the same claimed file; agreement between them is real evidence the file on disk today is byte-identical to what was verified at acquisition, not an assumption.

## 4. Manifest summary

| Metric | Expected | Observed |
|---|---:|---:|
| Total files | 40 | 40 |
| Train files | 28 | 28 |
| Holdout files | 12 | 12 |
| Event groups | 20 | 20 (14 train + 6 holdout) |

Verified by direct `csv.DictReader` parse of `docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv`: 40 data rows, 0 rows marked `AMBIGUOUS`, 20 distinct `event_group_key` values, 14 with `partition=train` (28 rows) and 6 with `partition=holdout` (12 rows).

## 5. Per-file acquisition table

All 40 expected files, cross-referenced against the acquisition provenance manifest (`data/external/historical_gfs/manifest.json`) and the two build-output manifests' own recorded `source_files` (which carry a SHA256 computed directly from the file at build time). Every file matched on both existence and SHA256 agreement between acquisition-time and build-time hashes.

| Partition | Event group | Filename | Exists | Size (bytes) | SHA256 (first 8 hex) | Status |
|---|---|---|---|---:|---|---|
| train | IND_13.0_78.0\|2015-02-28\|3 | gfs.0p25.2015022812.f003.grib2 | YES | 221303474 | b9201eb6 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2015-02-28\|3 | gfs.0p25.2015022812.f006.grib2 | YES | 225649192 | d2b41c99 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2016-03-13\|2 | gfs.0p25.2016031306.f003.grib2 | YES | 218992074 | 4df3b18c | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2016-03-13\|2 | gfs.0p25.2016031306.f006.grib2 | YES | 225313035 | 906af261 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2017-06-05\|2 | gfs.0p25.2017060506.f003.grib2 | YES | 226549197 | ddc469b7 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2017-06-05\|2 | gfs.0p25.2017060506.f006.grib2 | YES | 230521569 | f03bb6a8 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2018-02-09\|3 | gfs.0p25.2018020912.f003.grib2 | YES | 206240681 | e8c03576 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2018-02-09\|3 | gfs.0p25.2018020912.f006.grib2 | YES | 209889718 | e25ae099 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2019-10-02\|2 | gfs.0p25.2019100206.f003.grib2 | YES | 334222104 | 5000d6dd | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2019-10-02\|2 | gfs.0p25.2019100206.f006.grib2 | YES | 338357332 | d294f2a4 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2020-03-20\|2 | gfs.0p25.2020032006.f003.grib2 | YES | 332646013 | 541a0a54 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2020-03-20\|2 | gfs.0p25.2020032006.f006.grib2 | YES | 337465463 | 70436a53 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2021-02-19\|2 | gfs.0p25.2021021906.f003.grib2 | YES | 336254702 | f06c0f54 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2021-02-19\|2 | gfs.0p25.2021021906.f006.grib2 | YES | 337543368 | 10e79523 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2022-03-20\|2 | gfs.0p25.2022032006.f003.grib2 | YES | 548975814 | c785fcbf | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2022-03-20\|2 | gfs.0p25.2022032006.f006.grib2 | YES | 551143470 | d0a2b472 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2023-03-16\|3 | gfs.0p25.2023031612.f003.grib2 | YES | 552988860 | 194afbe7 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2023-03-16\|3 | gfs.0p25.2023031612.f006.grib2 | YES | 556624838 | d0798b19 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2017-04-01\|3 | gfs.0p25.2017040112.f003.grib2 | YES | 230411210 | 8c6410a9 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2017-04-01\|3 | gfs.0p25.2017040112.f006.grib2 | YES | 234042800 | 660cc630 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2018-07-17\|2 | gfs.0p25.2018071706.f003.grib2 | YES | 204587152 | 12fbc701 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2018-07-17\|2 | gfs.0p25.2018071706.f006.grib2 | YES | 208779501 | c444c600 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2021-10-01\|0 | gfs.0p25.2021093018.f003.grib2 | YES | 527330797 | 086d7513 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2021-10-01\|0 | gfs.0p25.2021093018.f006.grib2 | YES | 533930256 | 5953c088 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2023-11-01\|1 | gfs.0p25.2023110100.f003.grib2 | YES | 539698805 | df2f9714 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2023-11-01\|1 | gfs.0p25.2023110100.f006.grib2 | YES | 543730729 | e04c7f7a | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2021-02-20\|2 | gfs.0p25.2021022006.f003.grib2 | YES | 331135962 | 2a007418 | ACQUIRED_AND_VALID |
| train | IND_13.0_78.0\|2021-02-20\|2 | gfs.0p25.2021022006.f006.grib2 | YES | 335083457 | e6984239 | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-05-12\|2 | gfs.0p25.2024051206.f003.grib2 | YES | 543364213 | b6e3676c | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-05-12\|2 | gfs.0p25.2024051206.f006.grib2 | YES | 546282172 | 7c011f27 | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-06-02\|2 | gfs.0p25.2024060206.f003.grib2 | YES | 545083634 | 75ae279c | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-06-02\|2 | gfs.0p25.2024060206.f006.grib2 | YES | 547998850 | ce4f62bb | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-06-03\|2 | gfs.0p25.2024060306.f003.grib2 | YES | 547258274 | 13b235b1 | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-06-03\|2 | gfs.0p25.2024060306.f006.grib2 | YES | 547653299 | 63b9ec0b | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-01-01\|0 | gfs.0p25.2023123118.f003.grib2 | YES | 532929138 | dad0bb33 | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-01-01\|0 | gfs.0p25.2023123118.f006.grib2 | YES | 532955341 | 0d546204 | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-01-01\|1 | gfs.0p25.2024010100.f003.grib2 | YES | 529882452 | 9d7ccb93 | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-01-01\|1 | gfs.0p25.2024010100.f006.grib2 | YES | 528625255 | 189f1fdc | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-01-01\|2 | gfs.0p25.2024010106.f003.grib2 | YES | 529867265 | 3968f1ad | ACQUIRED_AND_VALID |
| holdout | IND_13.0_78.0\|2024-01-01\|2 | gfs.0p25.2024010106.f006.grib2 | YES | 535011779 | cea2d5b8 | ACQUIRED_AND_VALID |

"ACQUIRED_AND_VALID" here means: (a) present in the acquisition provenance manifest with a recorded SHA256, (b) present in the build's own `source_files` list with a SHA256 computed fresh from the file at build time, (c) the two hashes agree, and (d) the file was successfully read by `enumerate_messages()`/`build_pilot()` (GRIB2 header/trailer + real message decoding) during a run that completed with `validation_status: GREEN` and the exact expected row counts — not merely that a file of that name exists. A byte-level re-read of the raw files from this session was not performed (D: is not a connected folder and the repository's own `raw/` copy holds a different, non-matching file set), so this evidence is manifest-cross-check-based, not a fresh independent hash computed by this audit itself. It is still non-circular: the two hashes being compared were generated at two different times by two different script runs (Phase 0.4.19 acquisition vs. today's Phase 0.4.20 build).

No file fell into PRESENT_BUT_INVALID, MISSING, PARTIAL_FILE_PRESENT, or AMBIGUOUS.

## 6. Acquisition summary

```
EXPECTED = 40
ACQUIRED_AND_VALID = 40
PRESENT_BUT_INVALID = 0
MISSING = 0
PARTIAL_FILE_PRESENT = 0
AMBIGUOUS = 0
```

```
TRAIN: expected files = 28, valid acquired = 28, missing/invalid/etc = 0
HOLDOUT: expected files = 12, valid acquired = 12, missing/invalid/etc = 0
```

No missing or invalid filenames to list.

## 7. Phase 0.4.20 TRAIN output

- **EXISTS**: yes — `data/external/historical_gfs/phase_0_4_20_train.csv` (26,458 bytes) and `phase_0_4_20_train_manifest.json` (9,636 bytes), modified 2026-10-01T11:16:52 UTC per the manifest's own `generation_timestamp_utc`.
- **Row count**: 28 (confirmed by direct `pandas.read_csv` + `.shape`).
- **Event groups**: 14 unique `event_group_key` values (confirmed).
- **Positive / negative / unknown**: 20 / 8 / 0 (confirmed both from the manifest's `n_positive`/`n_negative`/`n_unknown` fields and independently from `label_status.value_counts()` on the CSV itself: `{'POSITIVE': 20, 'NEGATIVE_CONFIRMED': 8}`).
- **Duplicate event groups**: none beyond the expected 2-rows-per-group structure (14 groups × 2 leads = 28 rows; `event_group_key.duplicated().sum() == 14`, i.e. exactly one duplicate per group from its second lead row — expected, not an anomaly).
- **Duplicate source files**: none — 28 unique `source_file` values for 28 rows.
- **Feature columns**: all 17 contracted predictor columns present (`cape, cin, pwat_mm, u850, v850, u200, v200, wind_shear_850_200_ms, t850_k, t700_k, t500_k, rh850_pct, rh700_pct, k_index, totals_totals, gfs_prate_kg_m2_s, gfs_precip_3h_interval_mm`).
- **Core atmospheric feature missingness**: 0% for all 16 core fields (`cape` through `gfs_prate_kg_m2_s`) per the manifest's own `feature_missingness` block.
- **Precipitation feature missingness**: `gfs_precip_3h_interval_mm` is missing for 14/28 rows (50%) — this is the f003 lead for each event group, where no prior same-cycle step exists to form a 3-hour interval; the manifest's `precipitation_provenance` block explicitly records `interval_precip_available_rows: 14, interval_precip_missing_rows: 14` and `stepType_counts: {"instant": 16, "avg": 12}`. This is a structurally expected pattern (an interval feature cannot exist for the first lead of a cycle), not a data quality defect.
- **Precipitation provenance**: recorded per-row via `gfs_prate_stepType_used`/`gfs_prate_startStep`/`gfs_prate_endStep`/`gfs_prate_note` columns in the CSV and the manifest's `precipitation_provenance` summary.
- **All predictors forecast-derived**: yes — `leakage_check: {"predictor_columns_checked": 17, "all_forecast_derived": true}`.
- **Validation status from the manifest**: `"validation_status": "GREEN"`.
- **Source raw-root recorded in the manifest**: `D:\SIH-Historical-GFS\raw`.
- **Source-label archive recorded in the manifest**: `SIH_PANINDIA_GRID_LABELS_20260930_143541Z\processed\labels\ts_labels.csv` — i.e. the dated bundle identified and wired in during the Phase 0.4.20 labels-path fix, not a copy placed at the default `processed/labels/` location (confirming no file was duplicated to "fix" the earlier missing-labels bug — the override was used as designed).

## 8. Phase 0.4.20 HOLDOUT output

- **EXISTS**: yes — `data/external/historical_gfs/phase_0_4_20_holdout.csv` (11,099 bytes) and `phase_0_4_20_holdout_manifest.json` (5,759 bytes), generated 2026-10-01T11:18:51 UTC.
- **Row count**: 12 (confirmed).
- **Event groups**: 6 unique `event_group_key` values (confirmed).
- **Positive / negative / unknown**: 6 / 6 / 0 (confirmed from both the manifest and `label_status.value_counts()` on the CSV: `{'POSITIVE': 6, 'NEGATIVE_CONFIRMED': 6}`).
- **Duplicate event groups**: none beyond the expected 2-rows-per-group structure.
- **Duplicate source files**: none — 12 unique `source_file` values for 12 rows.
- **Feature columns**: same 17-column contract as TRAIN, all present.
- **Core atmospheric feature missingness**: 0% for all 16 core fields.
- **Precipitation feature missingness**: `gfs_precip_3h_interval_mm` missing for 6/12 rows (50%), the f003 lead of each group — same structurally expected pattern as TRAIN.
- **Precipitation provenance**: recorded per-row, same mechanism as TRAIN; manifest's `precipitation_provenance.stepType_counts: {"instant": 12}` (all holdout rows happened to use an instantaneous precip field rather than an averaged one — this is a property of which GRIB message types the specific holdout cycles contained, not a processing inconsistency).
- **All predictors forecast-derived**: yes — `leakage_check: {"predictor_columns_checked": 17, "all_forecast_derived": true}`.
- **Validation status from the manifest**: `"validation_status": "GREEN"`.
- **Source raw-root recorded in the manifest**: `D:\SIH-Historical-GFS\raw` (same as TRAIN).
- **Source-label archive recorded in the manifest**: same bundle path as TRAIN.

## 9. Combined dataset if present

`data/external/historical_gfs/phase_0_4_20_all.csv` does **not** exist (confirmed absent from the live directory listing). No combined-dataset facts to report — this is expected, since `--partition all` is documented in the builder as inspection/export only and was evidently never run or never written to this path.

## 10. Dataset-to-manifest cross-check

| Check | Result |
|---|---|
| Every row's source GFS file is within the 40-file manifest | PASS — all 40 `source_file` values across TRAIN+HOLDOUT match the 40 expected filenames derived from `docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv` exactly; zero extra, zero missing |
| No TRAIN row comes from a HOLDOUT manifest entry (and vice versa) | PASS — `set(train.event_group_key) & set(holdout.event_group_key) == {}` and `set(train.source_file) & set(holdout.source_file) == {}` |
| No event_group exists in both TRAIN and HOLDOUT | PASS — confirmed by the same disjointness check above |
| Every expected event group is represented exactly as the manifest says | PASS — 14 TRAIN groups / 6 HOLDOUT groups found, matching counts and identities from the candidate manifest |
| f003/f006 rows remain associated with their event group | PASS — every event group in both CSVs has exactly 2 rows sharing one `event_group_key`, one at `forecast_lead_hours=3` and one at `forecast_lead_hours=6` |
| No unexpected labels | PASS — only `POSITIVE` and `NEGATIVE_CONFIRMED` appear in either file; no `UNKNOWN`/`UNMATCHED` rows |
| No unknown labels unless the manifest explicitly permits them | PASS — `n_unknown: 0` in both manifests, and the candidate manifest itself only selected POSITIVE/NEGATIVE_CONFIRMED event groups for this batch |
| No future-derived predictor leakage per the Phase 0.4.20 contract | PASS — `leakage_check.all_forecast_derived: true` for all 17 predictor columns, in both TRAIN and HOLDOUT manifests |

**No discrepancies found in this cross-check.**

## 11. Holdout independence audit

The HOLDOUT set contains three event groups whose IST target date is all `2024-01-01`:

| event_group_key | IST slot | IST valid-time window (first/last row) | GFS init cycle (UTC) | Forecast leads | Label |
|---|---|---|---|---|---|
| `IND_13.0_78.0\|2024-01-01\|0` | 0 | 2024-01-01 02:30 IST – 2024-01-01 05:30 IST | 2023-12-31 18Z | f003, f006 | NEGATIVE_CONFIRMED |
| `IND_13.0_78.0\|2024-01-01\|1` | 1 | 2024-01-01 08:30 IST – 2024-01-01 11:30 IST | 2024-01-01 00Z | f003, f006 | NEGATIVE_CONFIRMED |
| `IND_13.0_78.0\|2024-01-01\|2` | 2 | 2024-01-01 14:30 IST – 2024-01-01 17:30 IST | 2024-01-01 06Z | f003, f006 | NEGATIVE_CONFIRMED |

Findings:
- **Each group uses a distinct GFS initialization cycle**, six hours apart (18Z → 00Z → 06Z), confirmed from both `initialization_time_utc` and `source_cycle` columns.
- **Target valid-time windows do not overlap**: slot 0's two rows are valid at 2023-12-31T21:00Z and 2024-01-01T00:00Z; slot 1's at 2024-01-01T03:00Z and T06:00Z; slot 2's at T09:00Z and T12:00Z. Each slot's pair of valid times is disjoint from the others' — they tile a continuous 15-hour span in 3-hour steps with no shared timestamp.
- **Predictor forecast windows do not overlap**: each group's f003/f006 pair is read from its own distinct GFS cycle's own forecast steps — no GRIB file, message, or forecast-valid-time is shared between any two of the three groups (confirmed: zero overlap in `source_file` across these three groups).
- **They are genuinely distinct target samples** in the formal sense used by the leakage checker (no shared event_group_key, no shared raw file, no shared label-join timestamp) — this is correctly **not** leakage under the Phase 0.4.20 contract, and the dataset-to-manifest cross-check in §10 found no violation.
- **However, they are three consecutive 3-hour windows spanning one continuous 15-hour period of the same calm overnight-to-midday stretch**, and all three independently resolved to the same label (NEGATIVE_CONFIRMED). This is consistent with — though not proof of — a single persistent quiescent synoptic regime rather than three meteorologically independent sampling opportunities. Half of the entire 6-group HOLDOUT set (3 of 6 groups) comes from this one 15-hour window.

**Classification: YELLOW** — valid holdout (no leakage, no cross-partition contamination, no shared source files or event keys — confirmed in §10), but the three `2024-01-01` groups are very likely correlated with each other meteorologically, and their concentration (half the holdout) reduces the effective diversity of the 6-group holdout sample for the negative class. This should be treated cautiously in any accuracy claim drawn from this holdout: a model's performance on these three groups should not be treated as three independent confirmations, and a future acquisition pass should prioritize spreading additional holdout event groups across more distinct multi-day-separated periods rather than adding more same-day/adjacent-cycle groups.

## 12. Tests

Command: `pytest tests/test_phase_0_4_20_dataset_builder.py tests/test_vobl_historical_gfs_ts_join.py -q` → **90 passed** (41 + 49), 0 skipped, 0 failed.

Command: `pytest tests/ -q` (full suite) → **285 passed**, 0 skipped, 0 failed.

Both runs were executed live for this phase, against the exact code currently in the repository (unchanged since the previous Phase-0.4.20 bugfix phase). No test was modified to produce this result. These test runs exercise the dataset builder and join logic's correctness (partitioning, leakage checks, path resolution, fail-closed behavior); they do not themselves re-validate the specific 40 files on the user's D: drive (the tests that touch real GRIB fixtures use either small synthetic byte strings for integrity-check-only tests, or — where they do read a real file — whatever happens to be present in this sandboxed audit environment's own `data/external/historical_gfs/raw/`, which is a different, smaller, older file set than what the live D: build used). The acquisition and build-output verification in §§5–10 of this report is the actual live-state evidence; the test suite's pass is evidence the *code* is correct, not a re-proof that today's specific 40 D: files are intact (that proof is the SHA256 cross-check in §3/§5).

## 13. Discrepancies

None found. Specifically:
- No file is missing, partial, or hash-mismatched against its own acquisition provenance.
- No dataset row references a file outside the 40-file manifest.
- No train/holdout cross-contamination.
- No unexpected or unknown labels.
- No non-forecast-derived predictor.

The only non-conforming thing observed was **not** a discrepancy in the current result: the repository's own `data/external/historical_gfs/raw/` directory contains 24 GRIB2 files from a different, earlier cycle set that is not part of the current 40-file manifest and was correctly not used by this build (see §3). This is a stale leftover directory worth cleaning up for clarity, but it did not affect, and was not involved in, the TRAIN/HOLDOUT build being verified here.

## 14. Final gate

**GREEN**

All 40 manifest files are acquired and verified (acquisition-time and build-time SHA256 agree for every one); both the TRAIN (28 rows/14 groups/20 pos/8 neg/0 unknown) and HOLDOUT (12 rows/6 groups/6 pos/6 neg/0 unknown) datasets exist, exactly match their manifest-recorded statistics, pass every cross-check against the 40-file acquisition manifest with zero discrepancies, and both report `validation_status: GREEN` from their own build run. The holdout independence finding (§11) is a YELLOW-level scientific caution about sample diversity, not a gate-failing defect — it does not involve leakage, contamination, or any violation of the Phase 0.4.20 contract. The full test suite (285/285) passes against the current code with zero modifications made during this verification.

## 15. Exact next action

The acquisition/build/verification gate that was previously unresolved is now closed with evidence. The next justified engineering action is **not** more verification of this dataset — it is the already-identified P0 item from the prior A-to-Z audit: formally size and justify what sample scale (how many more event groups, spread across more distinct periods — informed directly by §11's finding that concentrated same-period holdout groups reduce effective diversity) would be required before any model trained on this data could be presented as scientifically final, and begin planning the next acquisition batch accordingly. This is a planning/scoping action, not data acquisition, training, or production code change, and is therefore still consistent with this phase being audit-only.
