# Phase 0.4.19.1 — Historical GFS Storage Relocation Audit

Status: AUDIT + CODE CHANGE ONLY. No download was started or resumed, no
file was deleted or moved, and no migration to D: was performed this
phase. All changes below are code/test changes only, left for review.

## Trigger

The Phase 0.4.19 acquisition run on your machine hit
`[Errno 28] No space left on device` partway through, while writing into
`C:\Users\Aprameya\OneDrive\Pictures\Desktop\SIH-Hyperlocal-Warning\data\external\historical_gfs\raw\`.
Inspecting that directory directly (via the device bridge, read-only —
nothing was staged, moved, or deleted) confirms the actual state:

| File | Size on disk | State |
|---|---|---|
| `gfs.0p25.2019100206.f003.grib2.part` | 136,314,880 bytes (~130 MiB) | partial — in progress when disk filled |
| `gfs.0p25.2019100206.f006.grib2.part` | 0 bytes | never started writing |
| `gfs.0p25.2020032006.f003.grib2.part` | 0 bytes | never started writing |

14 complete `.grib2` files (the ones acquired before the disk filled) are
untouched and were not read, moved, or re-verified this phase — nothing
in this audit touches them.

## A. Exact current storage mechanism

Before this phase, the raw-file location was a **hardcoded module
constant**, not a configurable setting, in three places:

- `scripts/acquire_phase_0_4_19_batch.py`: `RAW_DIR = OUT_DIR / "raw"`
  where `OUT_DIR = REPO_ROOT / "data" / "external" / "historical_gfs"`
  — used directly inside `load_batch_targets()` with no override
- `scripts/verify_phase_0_4_19_batch.py`: same `RAW_DIR` constant, but
  **this one already exposed a `--raw-dir` CLI flag** (see B)
- `scripts/build_vobl_historical_gfs_ts_join.py` (the downstream dataset
  builder used in every phase since 0.4.8, including the eventual
  dataset-construction step for this batch): `RAW_DIR = REPO_ROOT / "data" / "external" / "historical_gfs" / "raw"`,
  used by `path_for(cycle, lead)` — **no CLI override exists here**

The provenance manifest (`data/external/historical_gfs/manifest.json`)
was similarly a hardcoded `PROVENANCE_MANIFEST` constant in the
acquisition script, with no override. Critically, every manifest entry
only ever records a bare `file_name` (e.g.
`"gfs.0p25.2020071500.f003.grib2"`), never an absolute path — this turns
out to be exactly what makes relocation safe (see D/item 6 below).

Neither `data/external/historical_gfs/raw/` nor `manifest.json` is
tracked by git (confirmed via `git ls-files | grep historical_gfs` —
zero hits, and `.gitignore` has no entry for this path either, because
nothing there has ever been committed) — this directory has always been
local-only, untracked working storage, which is why relocating it has no
git implications.

## B. Does `--raw-root` already exist?

**No, not under that name, and not uniformly.** `verify_phase_0_4_19_batch.py`
already had a `--raw-dir` flag (added in Phase 0.4.19), but
`acquire_phase_0_4_19_batch.py` — the script that actually writes the
large files and is the one that hit the disk-full error — had **no CLI
override at all**. This was the real gap: the downloader, not the
verifier, needed it most.

## C. Exact minimal code changes made this phase

1. **`scripts/acquire_phase_0_4_19_batch.py`**:
   - `load_batch_targets()` gained a `raw_root: Path = RAW_DIR` parameter
     (default unchanged, so every existing caller/test keeps working);
     `DownloadTarget.dest` is now built from `raw_root`, not the global
     constant directly
   - `load_existing_provenance()` / `write_provenance()` gained a
     `provenance_path` parameter (same backward-compatible default)
   - `main()` gained `--raw-root` (default: the original `data/external/historical_gfs/raw`)
     and `--provenance-manifest` (default: the original `manifest.json`
     path) CLI flags, both threaded through to the functions above
   - added `check_free_space()`: a preflight warning (not a hard stop)
     that compares `shutil.disk_usage(raw_root).free` against an estimate
     for the not-yet-downloaded files, using this project's own observed
     216.9-551.7 MB file-size range — printed before any network call, so
     a repeat of the exact failure that triggered this phase would be
     flagged up front instead of failing mid-batch
2. **`scripts/verify_phase_0_4_19_batch.py`**:
   - `--raw-dir` is renamed to the canonical `--raw-root`, with `--raw-dir`
     kept as an accepted alias (`dest="raw_root"` on both flag spellings)
     so nothing already relying on the old flag name breaks
   - `load_provenance()` gained a `provenance_path` parameter; `main()`
     gained `--provenance-manifest`
   - both resolved paths are now printed at the start of a run, for an
     obvious audit trail of which drive is being checked
3. **`tests/test_phase_0_4_19_1_raw_root_relocation.py`** (new, 10 tests)
4. No change to `scripts/build_vobl_historical_gfs_ts_join.py`, any
   production file, or any committed configuration.

**Explicitly NOT done, and why**: `build_vobl_historical_gfs_ts_join.py`'s
own hardcoded `RAW_DIR` was left untouched. It is the core, heavily-reused
builder behind every phase since 0.4.8 (imported by
`build_phase_0_4_16_historical_gfs_ts_dataset.py`,
`verify_phase_0_4_15_oversized_stage_b.py`,
`design_phase_0_4_17_acquisition_candidates.py`, and this phase's own
verifier) — the brief scoped this change specifically to "the Phase
0.4.19 acquisition and verification scripts," and touching that shared
module's path resolution carries real regression risk across every prior
phase's tests for a change not asked for here. **This is a known
follow-up gap**: once this batch's files are actually used to build a
dataset, whatever future script does that will either need the files
copied/symlinked back under the original `data/external/historical_gfs/raw/`
path, or `build_vobl_historical_gfs_ts_join.py` will need the same kind
of `raw_root` parameterization applied in a dedicated future phase. Flag
this to yourself before the next dataset-build phase.

## D. How to safely move the existing `.part` files

**First, the honest limitation**: whether GDEX's `data.gdex.ucar.edu`
actually honors HTTP Range requests has never been verified — this was
already flagged as unresolved in Phase 0.4.18/0.4.19 (the sandbox and the
device-bridge shell both fail to reach the host at all). This phase did
not change that; it only confirmed, by reading the code, exactly what
`acquire_phase_0_4_19_batch.py` will do with each `.part` file, so you
can judge the risk yourself rather than trusting an assumption:

- `download_one()` computes `resume_from = tmp_path.stat().st_size if tmp_path.exists() else 0`
  — it looks at whatever `.part` file is sitting at the destination path
  it's been told to use (i.e., wherever `--raw-root` points), by filename
  alone. It does not care how that `.part` file got there.
- `stream_download()` only sends a `Range: bytes=<offset>-` header **if**
  a fresh `HEAD` request to the file's URL reports `Accept-Ranges: bytes`
  for that exact file. This was verified by test this phase
  (`test_resume_is_only_attempted_when_server_advertises_accept_ranges`):
  when the server does *not* advertise range support, the code resets
  `resume_from` to 0 and opens the file in `"wb"` (overwrite) mode instead
  of blindly appending.
- Consequence: moving a `.part` file to D: and re-running with
  `--raw-root D:\...` is **safe either way**. Two possible outcomes, both
  non-corrupting:
  - If GDEX supports Range requests for this file: the download resumes
    from byte 136,314,880 for `gfs.0p25.2019100206.f003.grib2.part`,
    saving that much re-download.
  - If it does not: the script detects no `Accept-Ranges: bytes` header
    and restarts that file cleanly from 0, overwriting the moved
    `.part`'s content with a correct, complete file. No partial bytes
    are retained in the output, and nothing is corrupted — you only lose
    the head start, you don't get a bad file.
- The two 0-byte `.part` files (`gfs.0p25.2019100206.f006.grib2.part`,
  `gfs.0p25.2020032006.f003.grib2.part`) carry no resumable data either
  way — moving them is harmless (equivalent to not having them at all)
  but there is no reason to delete them either; the script treats a
  0-byte `.part` exactly like a fresh start.

**Recommended move procedure** (manual — this phase does not do this for
you, and no file was moved):

```
# From a normal Windows terminal, NOT this session:
mkdir D:\SIH-Historical-GFS\raw
move "C:\Users\Aprameya\OneDrive\Pictures\Desktop\SIH-Hyperlocal-Warning\data\external\historical_gfs\raw\*.part" D:\SIH-Historical-GFS\raw\
```

This moves only the three `.part` files — the 14 already-complete
`.grib2` files on C: are untouched (they were never in scope for this
move; per your instructions they stay where they are unless you decide
otherwise later).

## 6/7. Provenance and verification against D:

Because every provenance entry stores only a bare `file_name` (confirmed
by `test_provenance_manifest_records_only_bare_filenames_not_absolute_paths`
this phase), the existing `data/external/historical_gfs/manifest.json`
needs **no changes at all** to remain valid once files live on D: — it
was never path-dependent in the first place. The default behavior (no
`--raw-root` passed) keeps the provenance manifest itself on C:, since
it's small, not a space concern, and arguably belongs with the repo's
research artifacts even though it isn't git-tracked; only the directory
the large `.grib2`/`.part` files live in needs to move.

`verify_phase_0_4_19_batch.py --raw-root D:\SIH-Historical-GFS\raw`
checks files there using the exact same logic as before — confirmed by
`test_verifier_reads_from_a_custom_raw_root` and
`test_verifier_raw_dir_alias_still_works` this phase (the latter checks
that anyone who already has `--raw-dir` in a saved command doesn't break).

## E. Exact commands to run (after you've reviewed and want to proceed)

```
# 1. Create the new location (once)
mkdir D:\SIH-Historical-GFS\raw

# 2. Move ONLY the partial files (safe either way, per Part D)
move "C:\Users\Aprameya\OneDrive\Pictures\Desktop\SIH-Hyperlocal-Warning\data\external\historical_gfs\raw\*.part" D:\SIH-Historical-GFS\raw\

# 3. Dry run against the new location -- confirms the plan, checks free space, makes no network request
python scripts\acquire_phase_0_4_19_batch.py --dry-run --raw-root D:\SIH-Historical-GFS\raw

# 4. Smoke-test the two files that previously failed -- this is where
#    resume (or clean restart) for the 130MB partial actually gets tested
python scripts\acquire_phase_0_4_19_batch.py --execute --raw-root D:\SIH-Historical-GFS\raw --limit 2

# 5. If that succeeds, run the remaining batch
python scripts\acquire_phase_0_4_19_batch.py --execute --raw-root D:\SIH-Historical-GFS\raw

# 6. Verify everything against the new location
python scripts\verify_phase_0_4_19_batch.py --raw-root D:\SIH-Historical-GFS\raw --json-out phase_0_4_19_results.json
```

This phase does not run any of these commands. Step 4 is where you will
learn empirically whether GDEX honored the Range resume or restarted
clean — either is fine, but worth watching the "resumed: True/False"
status line in the script's output to know which happened.

## F. Expected free-space requirement

Same batch as before (the relocation doesn't change what needs
downloading): of the 40 unique files, 14 are already complete on C:
(untouched, not moving), 1 is ~130 MiB partial, and the remaining 25 are
not yet started (2 of which have 0-byte `.part` placeholders). Using this
project's observed per-file range (216.9-551.7 MB, mean ~376 MiB):

- Remaining ~26 files (25 not-started + the 1 partial, counted as needing
  its full size minus the ~130 MiB already on the `.part`) need
  approximately **9-10 GB** more.
- The new `acquire_phase_0_4_19_batch.py`'s `check_free_space()` prints
  this estimate automatically before any download starts, using whatever
  `--raw-root` you pass — confirmed working against a scratch directory
  this phase (see test output below).

Recommend having at least **15 GB free on D:** as a safety margin (the
script's estimate is a mean-based approximation, not a guarantee, since
actual per-file sizes vary).

## G. Test results

New tests this phase: `tests/test_phase_0_4_19_1_raw_root_relocation.py`
(10 tests) — covering custom `--raw-root` threading through
`load_batch_targets()` and the CLI, provenance manifests never embedding
absolute paths, custom provenance-manifest paths round-tripping, the
verifier reading from a custom root via both `--raw-root` and the
`--raw-dir` alias, the exact `.part` filename convention matching what
was observed on your disk, and — the most load-bearing test — that a
Range request is only ever sent when the server has confirmed
`Accept-Ranges: bytes` support, with a clean-restart fallback otherwise.

Results, actually run this phase:

- New Phase 0.4.19.1 tests: **10/10 passed**
- Combined Phase 0.4.19 + 0.4.19.1 tests: **25/25 passed**
- Full test suite (`pytest tests/`): **242/242 passed, 0 failed, 0
  skipped, 0 errors** (up from 232 before this phase's 10 new tests)

No existing test was weakened, removed, or rewritten for style.

## H. Final status

```
PHASE_0_4_19_1_STORAGE_RELOCATION = YELLOW
```

Reasoning: the code change itself is small, tested, backward-compatible
(every existing default is unchanged), and does not touch git-tracked
files, production code, or any committed configuration — that part would
support GREEN. It stays YELLOW for the same honest reason as Phase
0.4.18/0.4.19: whether the server actually supports Range-resume for the
partially-downloaded 130 MiB file remains unverified (untestable from
both this sandbox and the device-bridge shell), so the "resume" half of
the `.part`-file recovery is a defensively-correct but empirically
untested code path. This does not block the move or the smoke test in
Part E — worst case for the resumed file is a clean restart, never
corruption — it only means you should watch the actual output of step 4
in Part E to learn which behavior your environment gets.

## Scope confirmation

No file was downloaded, moved, or deleted this phase. No `.part` file was
touched. No production code, schema, or model was modified. No model was
trained. Nothing was committed or pushed — all changes are new/modified
files left for your review and your own git operations.
