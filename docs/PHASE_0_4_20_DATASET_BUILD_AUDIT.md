# Phase 0.4.20 — Manifest-Driven, Holdout-Safe Dataset Builder

Status: AUDIT + CODE BUILD ONLY. No file was downloaded, no model was
trained, no production code was touched, nothing was committed or
pushed. The actual dataset build against the real 40-file D: batch was
**not** run from this sandbox (the files live on your machine's D: drive,
not here) — this is expected and is itself proof the new builder's
fail-closed behavior works (see "Live fail-closed demonstration" below).

## Pipeline trace (as it actually exists in code, not assumed)

```
docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv (candidate manifest: event_group_key, cycle, lead, label, partition)
        |
        v  [NEW, Phase 0.4.20] load_event_groups() -- groups rows by event_group_key,
        |  fails if a group spans >1 cycle/partition/label
        v
scripts/build_phase_0_4_20_dataset.py: verify_partition_agreement()
        |  cross-checks csv_partition against assign_partition(ist_date) AND
        |  data/external/historical_gfs/manifest.json's recorded partition
        v
filter_by_partition(groups, --partition) -- STRUCTURAL exclusion before any file I/O
        |
        v
verify_files_present() -- fails loudly, names filename+event_group_key, no substitution
        v
verify_grib2_integrity() -- header/trailer check before handing to build_pilot()
        v
build_vobl_historical_gfs_ts_join.py: build_pilot(cycle, [3,6], ...)   [REUSED, unmodified]
        |  -- enumerates real GRIB2 messages via eccodes
        |  -- extracts WANTED_FIELDS at the VOBL cell via regrid_point_value()
        |  -- computes valid_time -> ist_slot_for() -> event_group_key_for()
        |  -- joins ts_labels.csv by (cell_id, timestamp) -- label_status is
        |     whatever the archive says (POSITIVE/NEGATIVE_CONFIRMED/UNKNOWN/UNMATCHED),
        |     never derived or defaulted
        v
combined DataFrame (all selected event groups concatenated)
        |
        v  [NEW, Phase 0.4.20] validate_labels() / validate_event_group_keys() /
        |  validate_holdout_contamination() / validate_leakage() / reconcile_row_counts()
        v
data/external/historical_gfs/phase_0_4_20_{train,holdout,all}.csv + _manifest.json
```

Every arrow above was traced by reading the actual code, not assumed —
`build_pilot()`'s label-join logic (lines ~487-513 of
`build_vobl_historical_gfs_ts_join.py`) was read directly to confirm
`label_status` is copied verbatim from whatever `ts_labels.csv` row
matches, with no fallback to "negative" anywhere in that path.

## Why the prior pipeline was NOT provably safe (proven from code, not assumed)

1. `build_phase_0_4_16_historical_gfs_ts_dataset.py`'s `CYCLES` list is a
   literal Python list of 8 cycles (lines 56-64) — adding this batch's 20
   cycles would mean hand-editing that list, which is exactly the
   "another hardcoded 20-cycle list" the brief says not to repeat.
2. That same script has **no `--partition` argument and no HOLDOUT
   refusal at all** — it assigns `train_holdout_partition` via
   `assign_partition()` *after* combining every cycle in `CYCLES`
   (line ~128), which means a HOLDOUT cycle, if ever added to that list,
   would be fully extracted, joined, and written into the same combined
   CSV as TRAIN rows, distinguished only by a column value. Nothing in
   that script would stop a careless caller from handing the whole
   combined CSV to a training script.
3. `build_vobl_historical_gfs_ts_join.py`'s `RAW_DIR` (line 80) is a
   module-level constant with no CLI override, confirming the Phase
   0.4.19.1 finding — the D: batch's files would never be found by this
   builder without code-level intervention.

## What Phase 0.4.20 changes

`scripts/build_phase_0_4_20_dataset.py` (new) replaces the
cycle-selection and partition-handling logic, while **reusing, unmodified,
`build_pilot()`** for the actual GRIB extraction/label-join/event-group
computation — the proven, already-tested extraction logic is not
touched or reimplemented.

### 1. Manifest-driven input
`load_event_groups()` reads `docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv`
and groups its 40 rows into 20 event groups at run time. No cycle is
hardcoded anywhere in the new script. Confirmed against the real file:
**20 event groups, 14 TRAIN, 6 HOLDOUT** — verified by test and by
running the loader directly against the real CSV this phase.

### 2/3. Holdout safety + fail closed
`--partition` is a **required** argument (`choices=["train","holdout","all"]`,
no default). `filter_by_partition()` drops every non-matching event group
**before** `verify_files_present()` or `verify_grib2_integrity()` ever
runs — a HOLDOUT file's path is never even constructed during a TRAIN
build (confirmed by test
`test_a_holdout_group_is_never_passed_to_verify_files_present_in_a_train_build`).
A missing file, a partition disagreement, a duplicate event group, an
unexpected label status, or a HOLDOUT row surviving into a TRAIN output
all raise `BuildFailure`, caught only at `main()`'s top level, which
prints `PHASE_0_4_20_BUILD = RED` and exits non-zero — **no output file
is ever written on any failure path** (the CSV/manifest writes happen
only after every validation in `run_build()` has already passed).

**Live fail-closed demonstration, actually run this phase**: running
`python scripts/build_phase_0_4_20_dataset.py --partition train` in this
sandbox (which does not have your D: batch's files) correctly aborts
with `BUILD FAILED: MISSING SOURCE FILE(S) ... PHASE_0_4_20_BUILD = RED`,
naming all 28 missing files and their event_group_keys, and writes
nothing. This is the intended behavior, not a bug — it's the same
fail-closed path that will protect you if a file goes missing on your
own machine.

### 4. D: storage
`--raw-root` (default: the original `data/external/historical_gfs/raw`
under the repo) is threaded through to `build_pilot()` by temporarily
reassigning `build_vobl_historical_gfs_ts_join.RAW_DIR` for the duration
of the build process only (restored in a `finally` block, confirmed by
test `test_run_build_restores_raw_dir_after_a_failure` — the restore
happens even when the build fails). No D:\ path is hardcoded anywhere in
the repository; it only ever exists as a value you pass on the command
line.

### 5/6/7/8. Feature contract, labels, sampling unit, leakage
All inherited unchanged from `build_pilot()`, which this script calls
without modification — the full 17-column `FEATURE_COLUMNS` list, the
`gfs_prate_kg_m2_s`/`gfs_precip_3h_interval_mm` precipitation-provenance
contract, and the `event_group_key = cell_id|ist_date|slot_id` sampling
unit are all identical to Phase 0.4.16. `validate_leakage()` (new) checks
every predictor column's entry in `build_vobl_historical_gfs_ts_join.FEATURE_LEAKAGE_CLASS`
is `FORECAST_DERIVED` and that no label field appears among the
predictor columns — this re-verifies, rather than assumes, the leakage
table every single build.

### 9. Partition authority
`verify_partition_agreement()` checks **two** independent things against
every event group: (a) `assign_partition(target_ist_date)` must equal the
candidate manifest's `partition` column, and (b) if the acquisition
provenance manifest (`data/external/historical_gfs/manifest.json`) has a
`partition` field recorded for that file, it must agree too. Either
disagreement is a hard `BuildFailure`. Run against the real candidate
manifest this phase: **all 20 event groups agree**, no disagreement found.

### 10. Outputs
Writes `data/external/historical_gfs/phase_0_4_20_{train,holdout,all}.csv`
and a matching `_manifest.json` build report — never
`data/processed/historical_gfs_ts/historical_gfs_ts_dataset.csv` (the
existing Phase 0.4.16 16-row dataset), confirmed by test
`test_output_filenames_never_collide_with_the_existing_phase_0_4_16_dataset`.

### 11. Validation (A-N)
All fourteen checks map to actual code, not just prose:

| Item | Implementation |
|---|---|
| A. every expected file present | `verify_files_present()` |
| B. every source GRIB2-valid | `verify_grib2_integrity()` |
| C. source → exactly one manifest target | `load_event_groups()` groups by event_group_key; one cycle per group enforced |
| D. target → expected event_group_key | `validate_event_group_keys()` compares recomputed vs. candidate-manifest column |
| E. TRAIN has zero HOLDOUT files | `validate_holdout_contamination()`, structural pre-filter in (2) |
| F. HOLDOUT has zero TRAIN files | same function, symmetric check |
| G. no duplicate event_group_keys | `validate_no_event_group_key_crosses_partitions()` (reused from Phase 0.4.12) |
| H. f003/f006 correctly associated | `validate_event_group_keys()`'s per-group lead-set check |
| I. no UNKNOWN silently negative | `validate_labels()` hard-fails on UNKNOWN/UNMATCHED |
| J. all predictor fields present | inherited from `build_pilot()`'s `REQUIRED_FEATURES` quality-issue tracking + `feature_missingness` in the report |
| K. missing values explicitly counted | `feature_missingness` dict per column in the build report |
| L. no predictor uses label metadata | `validate_leakage()` |
| M. zero train/holdout event-group overlap | guaranteed by (2)'s structural filter; also checked in tests |
| N. row counts reconcile with manifest | `reconcile_row_counts()` |

### 12. Tests

`tests/test_phase_0_4_20_dataset_builder.py` (new, 32 tests) covers every
item requested: manifest-driven source selection (including a
multi-cycle-per-group failure and ambiguous-row exclusion), TRAIN
structurally refusing HOLDOUT and vice versa, a missing-file failure that
names the exact file, a same-cycle-but-only-one-lead-file failure (no
silent partial build), manifest/`assign_partition()` disagreement (both
against the CSV and against the provenance manifest), duplicate
event-group failure, UNKNOWN/UNMATCHED label failure, label-vs-manifest
mismatch, a custom raw-root being honored (and `RAW_DIR` being restored
after a failure), deterministic output (loading the same manifest twice
yields identical groups), train/holdout disjointness (14 vs. 6, zero
overlap), leakage checks (both the real feature columns passing and an
injected label field being rejected), row-count reconciliation, GRIB2
integrity pre-checks, and the output-filename collision check. One
existing test (`test_no_production_code_imports_research_builder_repo_wide`
in `tests/test_vobl_historical_gfs_ts_join.py`) got one more exclusion
line for the new builder, which legitimately imports
`build_vobl_historical_gfs_ts_join` by name — same pattern as every prior
phase's research-only sibling script.

Results, actually run this phase:

- New Phase 0.4.20 tests: **32/32 passed**
- Historical-GFS-related selection (`-k "historical or vobl or gfs_ts or phase_0_4_1 or phase_0_4_20"`): **186/186 passed**
- Full suite (`pytest tests/`): **274/274 passed, 0 failed, 0 skipped, 0 errors** (up from 242 before this phase)

No existing test was weakened, removed, or rewritten for style.

## 13. Dataset-size reality check (restated, not softened)

This is still a 20-event-group pilot expansion. Combined with the
existing 9 event groups from Phase 0.4.16 (if a future phase merges
them), that is 29 event groups total. It is **not** a large training
dataset, and this phase does not claim otherwise. Nothing here trains the
MTL transformer or any other model.

## A. Exact files changed

- `scripts/build_phase_0_4_20_dataset.py` (new)
- `tests/test_phase_0_4_20_dataset_builder.py` (new, 32 tests)
- `tests/test_vobl_historical_gfs_ts_join.py` (modified — one exclusion line)
- `docs/PHASE_0_4_20_DATASET_BUILD_AUDIT.md` (new, this document)

No existing dataset file, production file, or committed configuration
was changed. `build_phase_0_4_16_historical_gfs_ts_dataset.py` and
`build_vobl_historical_gfs_ts_join.py` were read and reused by reference
(the latter is imported and has its `RAW_DIR` temporarily reassigned at
runtime only), not modified.

## B. Exact dataset-building command

```
# TRAIN build, reading the acquired batch from D:
python scripts/build_phase_0_4_20_dataset.py --partition train --raw-root D:\SIH-Historical-GFS\raw

# HOLDOUT build (reserved for evaluation only -- never training)
python scripts/build_phase_0_4_20_dataset.py --partition holdout --raw-root D:\SIH-Historical-GFS\raw

# Inspection/export only -- the script prints a loud warning and this
# output must never be fed to a trainer
python scripts/build_phase_0_4_20_dataset.py --partition all --raw-root D:\SIH-Historical-GFS\raw
```

This phase did not run these commands against your real D: files — they
exist only on your machine, not in this sandbox. The TRAIN build was
attempted here against the default (empty, C:-pointed) raw root
specifically to demonstrate the fail-closed path (see above); it
correctly refused to produce output.

## C-M. Dataset counts, missingness, provenance, leakage, contamination, status, sufficiency

**Not available from this sandbox** — producing real values for C
through I would require actually running the builder against your D:
batch's 40 real files, which this sandbox cannot reach (same Phase
0.4.18/0.4.19 network/device-bridge limitations as every prior phase).
Reporting fabricated numbers here would violate this project's standing
non-fabrication rule. What this audit *can* state, because it was
actually computed from the real manifest this phase:

- **C. TRAIN**: 14 event groups → **28 rows expected** (2 leads × 14) once built successfully
- **D. HOLDOUT**: 6 event groups → **12 rows expected** (2 leads × 6) once built successfully
- **E. Positive/negative/unknown**: from the real candidate manifest, the
  20 event groups are 13 POSITIVE / 7 NEGATIVE_CONFIRMED (all already
  independently verified against the live label archive in Phase
  0.4.17/0.4.18) → expect 0 UNKNOWN in a successful build (the builder
  hard-fails if it finds any, per requirement 6/11I)
- **F-I (feature missingness, precipitation provenance, leakage result,
  contamination result)**: can only be reported once you run the
  command in Part B on your machine and share the printed build report
  (or its `phase_0_4_20_{partition}_manifest.json` output) back — the
  script prints and writes all of these automatically

**J. Test results**: 32/32 new, 186/186 historical-GFS selection, 274/274
full suite (all actually run this phase, see Part 12 above).

**K. Status**:
```
PHASE_0_4_20_BUILD_PIPELINE = YELLOW
```
The builder itself is complete, tested, and fail-closed — that part is
GREEN-quality work. It stays YELLOW rather than GREEN only because the
real build against your actual 40-file D: batch has not yet been run
(cannot be, from this sandbox) — once you run the Part B command and it
reports `validation_status: GREEN` in its own output, this phase's work
is empirically confirmed rather than just code-reviewed.

**L. Sufficient for an XGBoost baseline?** No, not yet, and this phase
does not claim otherwise. Combined with the existing Phase 0.4.16 9 event
groups, a successful TRAIN build here adds 14 more TRAIN event groups
(23 TRAIN total) — still far short of the ~150-positive EPV≥10 floor
Phase 0.4.17 Step 4.B established for a defensible preliminary logistic/
XGBoost baseline, and also short of the ~20-30 "Target A" pipeline-
validation floor once you count only event groups, not rows. This batch
remains what Phase 0.4.17/0.4.19 always said it was: a pipeline-
validation-scale expansion, not a modeling-ready dataset.

**M. Sufficient for the A100/deep-learning MTL experiment?** No. Target D
(deep learning/MTL scale) was estimated in Phase 0.4.17 at 5,000-20,000+
event groups for a single cell/hazard — this batch, even combined with
everything acquired so far, is three orders of magnitude below that.
Nothing in this phase changes that assessment, and nothing in this phase
trains any model, MTL or otherwise.

## Scope confirmation

No file was downloaded this phase. No model was trained. No production
code was modified. Nothing was committed or pushed. The only script
executions this phase were: running the new test suite, and one
deliberate attempt to run `--partition train` against this sandbox's
(empty) default raw root to demonstrate the fail-closed path — which
correctly wrote no output.
