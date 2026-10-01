# Phase 0.4.15 — Oversized Stage B Local Verification

Status: **script delivered and self-tested; the 6 oversized files
themselves have NOT yet been verified, because this session cannot open a
file over 400 MB and cannot run a shell on the machine that holds them.**
This document reports exactly what was and was not established in this
phase, and gives the exact command to close the gap.

## Why this phase could not run the real check itself

Phase 0.4.14 already hit this limit and reported it as a blocker. This
phase re-attempted both available paths before concluding the limit is
still in force:

- `mcp__remote-devices__device_stage_files` enforces a hard 400 MB
  per-file cap. All 6 target files (518-526 MB) exceed it.
- `mcp__remote-devices__device_bash` (a shell on the user's machine) was
  attempted and returned `"Workspace unavailable. The isolated Linux
  environment on this device failed to start."` — the same failure
  Phase 0.4.14 hit.

Neither path can open these 6 files in place from this session. The
brief's own instruction — "do not impose a 400 MB staging step" — is
honored by NOT attempting to work around this (e.g. by trying to read a
truncated prefix of the file, which would not be a real GRIB check, or by
reporting a result that was never actually computed). Instead, this phase
delivers a correct, self-tested verification script and hands execution
to the one place that can actually open these files: your own machine.

## What this phase DID do

### 1. Wrote the verification script

`scripts/verify_phase_0_4_15_oversized_stage_b.py` — operates strictly
in place (opens each file by path under `data/external/historical_gfs/raw/`,
streams it with eccodes, never copies or stages it). It reuses, rather
than reimplements:

- `enumerate_messages()`, `find_message()`, `sha256_of()`, `parse_grib_dt()`,
  `ist_slot_for()`, `event_group_key_for()`, `WANTED_FIELDS`, `IST` — all
  imported directly from `scripts/build_vobl_historical_gfs_ts_join.py`.
- `assign_partition()`, `TRAIN` — from `scripts/historical_dataset_split.py`.

It performs, per file: existence/size/non-empty check; streaming SHA256
compared against `data/external/historical_gfs/manifest.json`; GRIB
header (`GRIB`)/trailer (`7777`) byte check; full eccodes message
enumeration; grid metadata (`gridType`, `Ni`, `Nj`, lat/lon extents,
`jScansPositively`) compared against the expected global 0.25° grid;
initialization time and forecast lead read from GRIB metadata (the
`cape@surface/0` message's `endStep`, not the filename); independent
recomputation of `valid_time_utc` → IST → `ist_slot_for()` →
`event_group_key_for()`, each compared against the Phase 0.4.13/0.4.14
expected values (a mismatch is a hard failure, not a warning); presence
of all 12 required fields plus `tp`/`prate`; full `tp`/`prate` message
metadata (shortName/typeOfLevel/level/stepType/startStep/endStep/
forecastTime/units) reported without computing any feature; a real label
cross-check against `ts_labels.csv`; `assign_partition()` on each target
date; and a cross-file duplicate-SHA256 check.

### 2. Validated the script's own logic (self-test, not the real check)

Before handing this off, the script's `verify_one_file()` and full
`main()` flow were run against the 3 small Stage B files already
confirmed good in Phase 0.4.14 (`gfs.0p25.2016011500.f003/f006.grib2`,
`gfs.0p25.2017041606.f003.grib2`), which this session *can* open:

- Against correct expected values: **0 failures**, matched manifest
  SHA256 exactly, matched grid exactly, matched slot/event_group_key
  exactly — `OVERSIZED_STAGE_B_LOCAL_VERIFICATION = GREEN` on this known-
  good subset.
- Against a deliberately wrong expected slot/event_group_key/label: the
  script correctly reported `slot mismatch`, `event_group_key mismatch`,
  and `label_status mismatch` as failures and printed `RED` — confirming
  it does not silently pass on a disagreement.
- Against a deliberately tampered manifest SHA256: correctly reported
  `SHA256 mismatch`.

This confirms the script's logic is sound. **It does not confirm anything
about the 6 oversized files themselves** — they were not opened by this
script in this session.

### 3. Tests

New file: `tests/test_phase_0_4_15_verify_oversized_stage_b.py` — 7
tests, run against the small already-present Stage B files (skipped
gracefully if those files or the manifest are absent from a given
environment, never fabricating a pass):

- `test_verify_one_file_passes_on_known_good_file`
- `test_verify_one_file_fails_on_wrong_expected_slot`
- `test_verify_one_file_fails_on_wrong_expected_lead`
- `test_verify_one_file_reports_manifest_sha_mismatch`
- `test_verify_one_file_grid_matches_expected_global_grid`
- `test_load_label_row_reads_real_csv_without_regenerating`
- `test_no_production_code_imports_the_verification_script`

One pre-existing test needed a one-line addition (not a weakening):
`test_no_production_code_imports_research_builder_repo_wide` in
`tests/test_vobl_historical_gfs_ts_join.py` now also excludes
`scripts/verify_phase_0_4_15_oversized_stage_b.py` from its repo-wide
grep-hit check, since that script legitimately imports
`build_vobl_historical_gfs_ts_join` by design (the same pattern already
used for `scripts/historical_dataset_split.py` in Phase 0.4.12).

Results:
- New file alone: **7/7 passed**.
- `tests/test_vobl_historical_gfs_ts_join.py` +
  `tests/test_historical_dataset_split.py` +
  `tests/test_phase_0_4_14_stage_b_verification.py` +
  `tests/test_phase_0_4_15_verify_oversized_stage_b.py`: **73/73 passed**.
- Full suite (excluding the same 4 pre-existing environment-only failures
  as every prior phase — `test_himawari.py`, `test_nomads.py`,
  `test_segments.py`, `test_segments_v2.py`): **249/249 passed** (up from
  the Phase 0.4.14 baseline of 242; +7, exactly the new test file;
  nothing weakened).

## A-K: per-check sections

**PENDING — not yet run against the 6 real oversized files.** Each
section below exists in the script's output and will populate this
document once you run it; none of it is filled in here with assumed or
estimated values, because that would be exactly the kind of unverified
claim this phase exists to avoid.

- A. File inventory — pending
- B. SHA256 comparison — pending
- C. GRIB integrity — pending
- D. Grid metadata — pending
- E. Cycle/lead/valid-time verification — pending
- F. IST slot verification — pending
- G. Field presence — pending
- H. Precipitation/prate semantics — pending
- I. event_group_key verification — pending
- J. Label verification — pending (already independently confirmed at the
  label-file level for these 3 target dates in Phase 0.4.13/0.4.14; what's
  pending here is the file-to-label linkage via the actual GRIB-derived
  slot for these specific 6 files)
- K. Duplication/corruption — pending

## L. Blockers / warnings

**Blocker (carried over from Phase 0.4.14, still open)**: this session
cannot open a file larger than 400 MB (device-staging cap) and cannot run
a shell directly on the user's machine (`device_bash` reports
`"Workspace unavailable"`). This blocks Parts A-K above for the 6
oversized files specifically. It does not indicate a problem with those
files — it is an execution-environment limitation of this session.

**Warning**: the script's self-test used only 3 of the 6 already-small
Stage B files as stand-ins (2016011500.f003/f006, 2017041606.f003) rather
than all 6 small ones, since 3 was sufficient to exercise every code path
(pass case, slot-mismatch case, lead-mismatch case, SHA-mismatch case,
grid check, label check, partition check). This is a coverage choice for
the self-test, not a gap in the real target: the real target is still all
6 oversized files, unchanged.

## M. Final gate

```
OVERSIZED_STAGE_B_LOCAL_VERIFICATION = PENDING (cannot be set to GREEN or
RED from this session — see "Why this phase could not run the real check
itself" above)
```

Setting GREEN here would violate the brief's own instruction ("Do not
assume GREEN merely because the manifest agrees") in spirit: no check in
Parts A-K above has actually been run against the 6 real files this
phase was asked to verify. Setting RED would be equally dishonest in the
other direction — nothing has failed either, because nothing has run.
**PENDING is the only truthful answer available from this session.**

The overall Phase 0.4.14 gate therefore **remains YELLOW** until you run
the command below and this document (or its JSON output) is updated with
real results.

## Exact command to run

From the repository root, in Git Bash (or any shell with the project's
Python environment and `eccodes` available) on your own machine:

```bash
python scripts/verify_phase_0_4_15_oversized_stage_b.py \
  --raw-dir data/external/historical_gfs/raw \
  --manifest data/external/historical_gfs/manifest.json \
  --labels SIH_PANINDIA_GRID_LABELS_20260930_143541Z/processed/labels/ts_labels.csv \
  --json-out docs/PHASE_0_4_15_results.json
```

(If your real label file lives at a different path than the one assumed
here, pass `--labels <actual path>` — the script does not search for it.)

This reads all 6 oversized files directly from
`data/external/historical_gfs/raw/`, in place, streaming — no file is
copied, staged, or moved. It prints a PASS/FAIL per file and a final
`OVERSIZED_STAGE_B_LOCAL_VERIFICATION = GREEN` or `= RED` line, and
(with `--json-out`) writes the full per-file results to a JSON file you
can paste back for this document to be completed, or inspect yourself.

If it prints `GREEN`, then — and only then —

**"All 12 Stage B files now have full verification coverage when Phase
0.4.14 and Phase 0.4.15 are considered together."**

If it prints `RED`, the script's per-file FAIL lines state exactly which
check failed on which file; report those back before promoting anything.
