# Phase 0.4.19 — Acquisition Readiness Audit

Status: AUDIT + TOOLING BUILD ONLY. The Phase 0.4.18 candidate batch was
**not** downloaded this phase. No model trained. No production file
touched. Nothing committed or pushed.

## 1. Manifest integrity (verified against the real CSV, not assumed)

Read directly from `docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv`:

- 20 event groups, 40 rows (2 per event group: f003 + f006) — confirmed
- 13 POSITIVE, 7 NEGATIVE_CONFIRMED event groups — confirmed
- 14 TRAIN, 6 HOLDOUT event groups — confirmed
- Every event group has exactly one f003 row and one f006 row — confirmed
- 0 duplicate event groups — confirmed
- 0 rows with `selected_gfs_cycle == "AMBIGUOUS"` — confirmed

**Unique source files**: 40. Each of the 20 event groups in this batch
maps to its own distinct GFS cycle — computed by grouping on
`(selected_gfs_cycle, selected_lead)`, not assumed from the row count.
Nothing in this batch shares a cycle across event groups, so 40 rows = 40
unique files here, but `scripts/acquire_phase_0_4_19_batch.py`'s
`load_batch_targets()` always de-duplicates on the actual file identity
rather than hardcoding "2 files per row" — a future manifest where two
event groups legitimately share a cycle would not be double-counted.

## 2. URL construction (traced, not invented)

`acquire_phase_0_4_19_batch.py` imports `build_direct_url()` and the
`DIRECT_FILE_BASE`/`DIRECT_FILE_NAME` constants directly from the existing
`scripts/acquire_historical_gfs_pilot.py` (Phase 0.4.3) rather than
re-deriving the pattern. The resulting URL template is:

```
https://data.gdex.ucar.edu/d084001/{YYYY}/{YYYYMMDD}/gfs.0p25.{cycle}.f{lead:03d}.grib2
```

This was cross-checked against the real, already-downloaded files sitting
in `data/external/historical_gfs/raw/` and their entries in
`data/external/historical_gfs/manifest.json` (e.g.
`gfs.0p25.2020071500.f003.grib2`, source URL
`https://data.gdex.ucar.edu/d084001/2020/20200715/gfs.0p25.2020071500.f003.grib2`)
— the pattern matches exactly. It was also diffed against the filename
convention `grib_filename_for()` in `build_vobl_historical_gfs_ts_join.py`
(`gfs.0p25.{cycle}.f{lead:03d}.grib2`), which every one of the Phase
0.4.13-0.4.16 cycles (2015/2016/2017/2019/2020/2021/2022/2023) was already
built against — identical. No new pattern was invented for this phase.

## 3. Download safety (built this phase, not previously existing)

The existing `acquire_historical_gfs_pilot.py` (Phase 0.4.3) only
supports one cycle at a time via CLI flags, uses a bare
`urllib.request.urlretrieve()` with no resume, no retry, and no
atomic-rename — it was explicitly documented as "reviewed but
sandbox-untested." It was not sufficient for a 40-file batch, so this
phase adds `scripts/acquire_phase_0_4_19_batch.py`, which:

- reads the candidate CSV and downloads the de-duplicated unique-file set
- streams every response in 1 MiB chunks (`CHUNK_SIZE`), never reading a
  whole 200-550 MB file into memory
- writes to a `<filename>.part` temp file; only `Path.replace()`s
  (atomic on the same filesystem) it to the final name after the byte
  count matches the server's reported `Content-Length` (when available)
- resumes a `.part` file via an HTTP `Range: bytes=<offset>-` request,
  but **only if** a prior `HEAD` response for that exact URL advertised
  `Accept-Ranges: bytes` — this is checked per file, never assumed; if
  the server doesn't advertise range support, the script restarts that
  file from scratch rather than risk a corrupt silent append
- retries up to 5 times with exponential backoff (2s, 4s, 8s, 16s, 32s)
  on any exception during the GET: the `.part` file is left on disk
  after the final failed attempt, specifically so a subsequent run can
  resume it — nothing is ever deleted on failure
- never re-downloads a file whose on-disk size AND locally-recomputed
  SHA-256 already match a prior provenance record for that exact
  filename (`already_verified()`) — this is checked before any network
  call for that file is made
- reports HTTP status, resumed/fresh, attempt count, and final byte
  count per file, and a pass/fail summary at the end

**Not verified this phase**: whether `data.gdex.ucar.edu` actually
supports HTTP Range requests. This could not be tested — the sandbox's
own `curl` attempts to GDEX hosts return `403` at the proxy CONNECT layer
(same failure already documented in Phase 0.4.2/0.4.18), and the
device-bridge shell on the linked machine is still reporting "Workspace
unavailable" (same failure as Phase 0.4.14-0.4.18). The script's resume
logic is written defensively (checks `Accept-Ranges` before attempting a
Range request, falls back to a clean restart otherwise) but has not been
exercised against the real server. This should be verified on the first
real run, by interrupting a download mid-file and confirming the resume
path is actually taken (the script prints `resumed: True` when it is).

## 4. Verification (generalized from Phase 0.4.15, not rewritten for style)

Phase 0.4.15's `verify_phase_0_4_15_oversized_stage_b.py` hardcodes a
6-entry Python list (`TARGET_FILES`) of expected init/lead/valid/slot
values — workable for 6 files, not for 40. This phase adds
`scripts/verify_phase_0_4_19_batch.py`, which keeps every one of Phase
0.4.15's actual check primitives (GRIB header/trailer bytes, `eccodes`
message enumeration, grid metadata against `EXPECTED_GRID`, init/lead
recomputation from the GRIB bytes themselves via
`parse_grib_dt`/`find_message`, `ist_slot_for`/`event_group_key_for`
recomputation, `WANTED_FIELDS` presence check, `tp`/`prate` metadata
reporting, label cross-check against the real `ts_labels.csv`, duplicate
SHA-256 detection) but drives its list of expected targets from
`docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv` instead of a literal table.
Every expected value is still independently recomputed from the CSV's
`(target_ist_date, target_slot, selected_gfs_cycle, selected_lead)`
columns using the same `ist_slot_for()`/`event_group_key_for()` functions
already used in production research tooling — the CSV's own
`event_group_key`/`target_valid_time` columns are cross-checked against
this recomputation, not trusted blindly (a manifest bug would surface as
a `FAIL`, not be silently absorbed).

Run against the current (not-yet-acquired) batch, it correctly reports
`0/40 PASS, 0 FAIL, 40 not yet acquired` and
`PHASE_0_4_19_BATCH_VERIFICATION = YELLOW` — it does not fabricate a PASS
for a file that doesn't exist, and does not conflate "not yet acquired"
with "failed."

## 5. Checksum honesty

Per `docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md`, GDEX's direct
full-file HTTPS path (the only confirmed-working mechanism) does **not**
publish a remote checksum for a file at the time of this project's
research — no `.sha256`/`.md5` sidecar or checksum header was documented.
`acquire_phase_0_4_19_batch.py`'s provenance entries therefore explicitly
label the locally-computed SHA-256 as an **acquisition fingerprint**, with
a note stating plainly that it is not a remote-verification match. It is
still useful for two real things this phase relies on: (a) detecting a
corrupted/incomplete local file on a later re-run (`already_verified()`),
and (b) detecting if two different filenames ever end up with identical
bytes (`detect_duplicate_fingerprints()`), which would indicate an
acquisition bug, not a remote tampering check.

## 6. Holdout protection

Every provenance entry this phase's acquisition script writes carries
`partition` (`train`/`holdout`), `event_group_key`, and `label` fields
copied from the candidate manifest. The acquisition stage **does**
download HOLDOUT files (12 of the 40 — the brief permits this), but
nothing in `acquire_phase_0_4_19_batch.py` or
`verify_phase_0_4_19_batch.py` builds a training dataset or trains a
model — that remains out of scope for this phase.

**Flagged as missing, not fixed this phase** (per item C below):
`scripts/build_phase_0_4_16_historical_gfs_ts_dataset.py`'s `CYCLES` list
is still hardcoded to the original 8 Phase 0.4.13-0.4.16 cycles — it does
not yet know about this batch's 20 new cycles, and critically, it has no
TRAIN/HOLDOUT filtering logic of its own (it assigns `train_holdout_partition`
after the fact via `assign_partition()`, which is correct, but nothing
currently stops a future caller from feeding all 20 new cycles, HOLDOUT
included, into whatever downstream script eventually trains a model). The
partition tagging in this phase's provenance manifest is the protection
mechanism available today; a future phase should make the training-set
builder explicitly refuse HOLDOUT-partitioned cycles, not just label them.

## 7. Storage (computed from unique files and real observed sizes)

40 unique files, no two sharing a cycle. Real observed sizes from the 14
files already on disk this project has downloaded (`data/external/historical_gfs/manifest.json`):
min 216.9 MB, max 551.7 MB, mean 394.7 MB. GDEX direct files are full
global grids (not pre-subset), so there is no reason this batch's 40
files would fall outside that observed range.

| Basis | Total |
|---|---|
| 40 × min observed (216.9 MB) | ~8.7 GB |
| 40 × mean observed (394.7 MB) | **~15.8 GB** |
| 40 × max observed (551.7 MB) | ~22.1 GB |

**Expected storage: ~9-22 GB, central estimate ~16 GB** — consistent with
Phase 0.4.17's prior ~16 GB estimate for a similarly-sized batch (21
event groups / 42 files then vs. 20 / 40 now, after the Phase 0.4.18
cleanup dropped one event group).

## 8. Tests

New tests this phase: `tests/test_phase_0_4_19_acquisition_batch.py` (15
tests) covering unique-file-count derivation from the manifest, no
duplicate destination filenames, URL construction against the confirmed
pattern (including a cross-check against a real already-downloaded file),
destination-path correctness, TRAIN/HOLDOUT separability, dry-run safety
(asserts `stream_download` is never called without `--execute`),
already-verified/skip-redownload logic (both the match and mismatch
cases), duplicate-fingerprint detection, ambiguous-row exclusion, the
verifier's independent expectation recomputation, and the
file-not-found-is-not-a-pass case. One existing test
(`test_no_production_code_imports_research_builder_repo_wide` in
`tests/test_vobl_historical_gfs_ts_join.py`) got one more exclusion line
added for the new `verify_phase_0_4_19_batch.py`, which legitimately
imports `build_vobl_historical_gfs_ts_join` by name — the same pattern
used for every prior phase's research-only sibling script. No existing
test was weakened or rewritten for style.

Results, actually run this phase:

- New Phase 0.4.19 tests: **15/15 passed**
- Full test suite (`pytest tests/`): **232/232 passed, 0 failed, 0
  skipped, 0 errors** (up from 217 before this phase's 15 new tests)

## A. What already works

- The URL/filename convention (`grib_filename_for`, `build_direct_url`)
  — confirmed consistent across every phase since 0.4.3, cross-checked
  against real downloaded files
- GRIB2 verification primitives (header/trailer bytes, `eccodes`
  enumeration, grid metadata, required-field presence, `tp`/`prate`
  metadata) — proven in Phase 0.4.14/0.4.15/0.4.16 against real files
- `ist_slot_for()` / `event_group_key_for()` / `assign_partition()` —
  the same functions used throughout this project, reused (not
  reimplemented) by the new tooling
- The candidate manifest itself (Phase 0.4.17/0.4.18) — clean, no
  duplicates, no ambiguous mappings, correct TRAIN/HOLDOUT split

## B. What was missing (and is now built this phase)

- A manifest-driven batch downloader (the old tool only did one cycle at
  a time, with no resume/retry/atomic-write safety)
- A manifest-driven batch verifier (the old tool hardcoded 6 files)
- Explicit "acquisition fingerprint, not remote checksum" labeling
- Duplicate-SHA256-across-filenames detection at the batch level

**Still missing, out of scope for this audit-only phase**:

- Real confirmation that GDEX supports HTTP Range requests (untestable
  from both available environments this phase — see Part 3)
- A training-set builder that refuses HOLDOUT-partitioned cycles by
  construction, rather than merely labeling them (Part 6)
- `build_phase_0_4_16_historical_gfs_ts_dataset.py`'s `CYCLES` list does
  not yet include this batch's 20 new cycles — it will need updating (or
  replacing with a manifest-driven equivalent, mirroring the pattern used
  for the acquisition/verification scripts this phase) once the files are
  actually on disk, in a future phase

## C. Exact files changed this phase

- `scripts/acquire_phase_0_4_19_batch.py` (new)
- `scripts/verify_phase_0_4_19_batch.py` (new)
- `tests/test_phase_0_4_19_acquisition_batch.py` (new, 15 tests)
- `tests/test_vobl_historical_gfs_ts_join.py` (modified — one exclusion
  line added to the existing repo-wide import-isolation test)

No other file was changed. `scripts/acquire_historical_gfs_pilot.py` and
`scripts/validate_historical_gfs_pilot.py` were read and reused by
reference (imported, in the former's case), not modified.

## D. Exact command to run on your PC

From the repository root, on your own network-connected machine (not this
sandbox, and not the still-broken device-bridge shell):

```
# 1. Dry run first -- confirms the plan, makes no network request
python scripts/acquire_phase_0_4_19_batch.py --dry-run

# 2. Smoke-test with 2 files before committing to the full batch
python scripts/acquire_phase_0_4_19_batch.py --execute --limit 2

# 3. If that works (and ideally after confirming resume behavior by
#    interrupting one download), run the full batch
python scripts/acquire_phase_0_4_19_batch.py --execute

# 4. Verify everything that was downloaded
python scripts/verify_phase_0_4_19_batch.py --json-out /tmp/phase_0_4_19_results.json
```

Optional: `--only-partition train` / `--only-partition holdout` to split
the run; `--allow-large` only if a file genuinely exceeds the 700 MB
per-file ceiling (none observed so far do).

## E. Expected storage

**~9-22 GB, central estimate ~16 GB** for all 40 unique files (see Part 7).

## F. Unique files to be downloaded

**40** — 28 TRAIN, 12 HOLDOUT. Confirmed by `load_batch_targets()`'s
de-duplication logic (by cycle+lead, not by row count), and by the dry
run's printed list, which enumerates exactly 40 distinct filenames.

## G. How the system verifies each file

Per `verify_phase_0_4_19_batch.py`, for every file: existence and
non-zero size; GRIB2 header (`GRIB`) and trailer (`7777`) bytes; full
`eccodes` message enumeration (not `cfgrib.open_dataset` on the whole
file, which fails on multi-level-type GFS GRIB2); 0.25° global
`regular_ll` grid metadata (`Ni=1440, Nj=721`, exact lat/lon bounds);
init time and forecast lead recomputed from the GRIB bytes themselves
(via the `cape@surface/0` message's `endStep`), compared against what the
manifest intended; valid time, IST date, IST slot, and
`event_group_key` all independently recomputed (not copied from the CSV)
and cross-checked against the CSV's own columns; every `WANTED_FIELDS`
entry's presence; `tp`/`prate` message metadata reported (stepType,
startStep, endStep) rather than assumed; the real label archive's
`label_status` for that exact (cell, date, slot) checked against what the
manifest expected; TRAIN/HOLDOUT partition recomputed via
`assign_partition()` and checked against the manifest and the acquisition
provenance; and duplicate-SHA256 detection across the whole batch. Any
disagreement is a `FAIL`, not a warning.

## H. Final status

```
PHASE_0_4_19_READINESS = YELLOW
```

Reasoning: the acquisition and verification tooling is now built, tested
(15/15 new, 232/232 full suite), and traced against the one confirmed
URL/filename convention — this alone would support GREEN for "is the
repo ready to acquire." It stays YELLOW, not GREEN, for two honestly
unresolved items that matter before a real 40-file, ~16 GB run: (1) the
resumable-Range-request path is written defensively but has never been
exercised against the real GDEX server (same network-blocked limitation
carried over from Phase 0.4.18), and (2) nothing yet stops a future
dataset-build step from accidentally including HOLDOUT-partitioned
cycles beyond the partition label itself (Part 6/B). Neither issue
blocks running the `--limit 2` smoke test in Part D; the full batch
should wait until the smoke test confirms both the direct-download and
(if a download is deliberately interrupted) the resume path work as
expected on your machine.

## Scope confirmation

No production code, schema, model, or data file was modified. No
network request reached GDEX this phase (same sandbox/device-bridge
blockers as Phase 0.4.18). No bulk acquisition occurred. No model was
trained. Nothing was committed, pushed, or deployed — all changes are
left for your review.
