# Phase 0.4.25 — Colab Cloud Acquisition Design

Design/feasibility audit only. No GFS files were downloaded, no manifest was modified, no model was trained, no production/UI/label file was changed. All compatibility claims in this document are backed by evidence actually gathered this phase (direct imports and a full test-suite run on a real Linux environment), not assumption.

## 1. Architecture

```
GDEX (data.gdex.ucar.edu, HTTPS)
  -> Colab temporary runtime disk (/content/...)
       -> verify (SHA256 + GRIB2 structural check)
       -> extract (17-feature join, existing scripts, unmodified)
       -> validate (event-group-key partition/leakage checks, unmodified)
  -> Google Drive (/content/drive/MyDrive/SIH-Historical-GFS/...)
       -> persist: processed rows, provenance, manifests, logs
  -> delete temporary raw GRIB from Colab disk
  -> repeat for next batch
```

The eventual A100 training phase reuses the same Colab runtime and the same Drive-persisted dataset — no separate transfer step is required once acquisition completes (see Part 9).

## 2. Why local D: is not used

Superseded by two prior findings in this engineering effort, not re-litigated here: (1) Phase 0.4.23/0.4.24 estimated ~242-334GB for the 300-event-group TS target, which exceeds what the user's local Windows machine has available; (2) Phase 0.4.?? (Google Drive check) found the user's local `G:` Google-Drive-for-Desktop mount cannot currently accept even a 50-byte write (`"device disk full"`), ruling it out as a local staging path too. Colab's own temporary disk plus Drive's cloud quota (user-reported ~2TB, separate from the local G: mount's local-cache limitation) avoids both problems: nothing large is ever stored on the user's local Windows disk at all.

## 3. Colab temporary storage

**Raw GRIB files are never all present on Colab's temporary disk at once.** Based on the actual measured file-size evidence (40 already-acquired files, Phase 0.4.19/0.4.22: mean 403,658,081 bytes/file, max 556,624,838 bytes/file), a batch is sized conservatively against the *maximum* observed size, not the mean, since Colab's actual free disk is unknown until the runtime is started (per this phase's explicit instruction not to assume a specific capacity):

- **Recommended batch size: 10 event groups (20 files) per cycle.** Worst case (all 20 files at the observed maximum 556.6MB): ~11.1GB of temporary raw GRIB on disk at any one time. This is small enough to be safe even against a minimal free-tier Colab disk allocation (historically on the order of tens of GB free), while still being large enough that 30 batches (300 event groups ÷ 10) is a manageable number of acquisition cycles.
- **The exact free disk space must be queried at the start of the actual Colab session** (e.g. `shutil.disk_usage("/content")`) before committing to this batch size — this document recommends the size, it does not assume the capacity it will run against. If the queried free space is smaller than ~15GB (leaving headroom above the 11.1GB worst case), the batch size should be reduced (e.g. to 5 event groups) rather than proceeding.
- Each batch downloads only its own files, never stages the next batch's files early.

## 4. Google Drive persistent storage

Proposed structure (not created this phase):

```
/content/drive/MyDrive/SIH-Historical-GFS/
  raw_archive/        -- NOT populated long-term; see Part 11 (raw GRIB is temporary)
  processed_batches/  -- per-batch extracted feature rows + per-batch provenance JSON
  train/              -- cumulative train CSV, rebuilt/appended per batch
  holdout/             -- cumulative holdout CSV, rebuilt/appended per batch
  manifests/           -- a copy of docs/PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv and the existing 20-group manifest, plus acquisition-state tracking
  provenance/          -- per-file SHA256, source URL, acquisition timestamp
  logs/                -- per-batch download/verify/extract/validate logs
```

**What persists in Drive**: everything that is expensive or risky to regenerate (extracted feature rows, provenance, manifests, logs, final train/holdout CSVs) — i.e. the *results* of processing a batch, not the batch's raw bytes. `raw_archive/` is listed in the proposed structure for symmetry with the original request but this document does not recommend actually populating it with full raw GRIB files long-term (see Part 11) — at most it could hold a short-lived staging copy mirroring the current batch, deleted on the same cadence as the Colab-local copy.

## 5. Batch lifecycle

```
DOWNLOAD BATCH (10 event groups / 20 files, to Colab temp disk)
  -> VERIFY (SHA256 against provenance manifest; GRIB2 structural read via eccodes)
  -> EXTRACT (existing build_vobl_historical_gfs_ts_join.py logic, --raw-root pointed at the Colab temp dir)
  -> VALIDATE (existing event_group_key partition/leakage checks, unmodified, via historical_dataset_split.py)
  -> PERSIST processed rows + provenance + logs to Drive
  -> DELETE temporary raw GRIB from Colab disk
  -> NEXT BATCH
```

This reuses the existing scripts' CLI argument surface without code changes: `acquire_phase_0_4_19_batch.py --raw-root /content/tmp_raw`, `build_phase_0_4_20_dataset.py --raw-root /content/tmp_raw --out-dir /content/drive/MyDrive/SIH-Historical-GFS/processed_batches/batch_NNN`. No script logic needs modification for this phase's design — see Part 1 compatibility findings below.

## 6. Provenance contract

**Must be stored permanently in Drive, for every batch** (per this phase's explicit list):

- Phase 0.4.24 manifest (copied into `manifests/`, not re-derived)
- acquisition state (which event groups are DOWNLOADED / VERIFIED / EXTRACTED / VALIDATED / PERSISTED / PURGED — a small JSON state file, append-only)
- per-file: source filename, source URL, source SHA256, GFS cycle, lead
- per-event-group: `event_group_key`, label, partition
- extracted features (the 17-column `FEATURE_COLUMNS` contract, unchanged)
- feature provenance (already written by the existing extraction script's manifest output — unchanged)
- validation results (the existing builder's `validation_status`/row-count/leakage-check output)
- batch metadata (batch number, event groups included, start/end timestamp, Colab session id if available)
- dataset manifests (`phase_0_4_20_train_manifest.json`-style output, per batch and cumulative)
- final train CSV, final holdout CSV (cumulative, rebuilt or appended after each batch)
- processing logs (stdout/stderr of each stage, per batch)

**DATASET_PROVENANCE = preserved.** Every fact needed to understand what was acquired, from where, with what hash, joined into what row, with what validation outcome, survives the purge of the raw GRIB files.

**RAW_GRIB_REPROCESSING = requires re-download.** This document does not claim that a purged raw GRIB file can be regenerated from the persisted provenance — the provenance record (URL, cycle, lead, SHA256) is sufficient to *re-acquire* the identical file from GDEX if ever needed (assuming GDEX's archive remains stable), but it is not a substitute for the file itself; any future re-extraction with a changed feature-extraction script would require downloading the raw file again, not replaying it from provenance. This distinction is kept explicit everywhere this design is referenced, per this phase's hard instruction not to overstate reproducibility.

## 7. Failure recovery

A batch is **COMPLETE** only after all six conditions hold, in order:

1. All required GFS files for the batch verified (SHA256 + structural read)
2. Extraction completed (17-feature rows produced for every event group in the batch)
3. Dataset rows validated (existing `validation_status` check passes, 0 leakage violations)
4. Provenance persisted to Google Drive (write confirmed, not just attempted)
5. Persistent dataset artifact verified (re-read the just-written Drive file and confirm row count/hash matches what was just produced — do not trust the write call alone)
6. Acquisition state updated (the append-only state file records this batch as COMPLETE)

**Only after step 6 may the batch's temporary raw GRIB files be deleted.** Failure-mode handling, per stage:

- **Disconnect during download**: no files were verified yet; the partial batch is simply re-downloaded from scratch on reconnect (the acquisition-state file never marked it beyond DOWNLOADING, so nothing is purged and nothing is falsely trusted).
- **Disconnect during verification**: same as above — verification failure or incompleteness blocks progression to extraction; temp files remain untouched until the next session re-attempts verification.
- **Disconnect during extraction**: extraction is re-run from the (still-present, still-verified) raw files already on Colab's temp disk — no re-download needed if the raw files survived the disconnect (a Colab runtime disconnect usually also wipes `/content`, so in practice this likely means falling back to re-download, which is still safe, just slower).
- **Disconnect during Drive upload**: the write to Drive is re-attempted; step 5's "verify the persisted artifact" check is exactly what catches a partial/corrupted upload before the batch is ever marked COMPLETE.
- **Disconnect during purge**: the worst case is a batch that is COMPLETE (fully persisted and verified) but whose temp raw files were never deleted — this is safe (wastes temp disk, risks nothing) and is caught on the next session's startup by checking the acquisition-state file against what's still present on `/content`, deleting any COMPLETE batch's leftover temp files.

**Invariant preserved throughout**: the only copy of a batch's validated output that is ever trusted as "the" copy is the one verified on Drive in step 5 — raw temp files are always disposable once that verification passes, and are never deleted before it does.

## 8. Required dependencies

Checked by direct import and test execution in this phase's sandbox (a real Linux x86_64 environment, architecturally equivalent to Colab's runtime — not a hypothetical check):

- `eccodes` (Python bindings to the ECMWF ecCodes GRIB2 library) — **confirmed importable via plain `pip install eccodes`** on this Linux sandbox (version 2.49.0, no separate system package needed — modern `eccodes-python` ships a bundled binary wheel for Linux x86_64). `EC_CODES_COMPATIBLE = YES` on evidence, not assumption.
- `numpy`, `pandas` — standard, already present in both this sandbox and any default Colab runtime.
- `urllib.request` (stdlib) — used for all GDEX HTTP downloads in `acquire_phase_0_4_19_batch.py`/`acquire_historical_gfs_pilot.py`; no third-party HTTP library dependency, nothing Colab-incompatible.
- No OS-specific calls (`os.name`, `platform`, `win32*`, `ntpath`) were found anywhere in the acquisition/verification/extraction/dataset-building scripts — every path is built via `pathlib.Path`, which is cross-platform by construction.
- All repository-relative paths (`REPO_ROOT = Path(__file__).resolve().parent.parent`) are computed at runtime from the script's own location, not hardcoded — running the same repo checkout from `/content/SIH-Hyperlocal-Warning` on Colab instead of the Windows path would work unchanged.
- Every path that currently defaults to a local location (`--raw-root`, `--out-dir`, `--candidate-manifest`, `--provenance-manifest`, `--ts-labels`) is already an overridable CLI argument in every script that needs one — no code change is required to redirect raw storage to a Colab temp dir and output storage to a Drive-mounted path.
- **Direct evidence**: all six relevant modules (`acquire_phase_0_4_19_batch`, `verify_phase_0_4_19_batch`, `build_phase_0_4_20_dataset`, `build_vobl_historical_gfs_ts_join`, `historical_gfs_thermodynamics`, `historical_gfs_precipitation`, `historical_dataset_split`) were imported successfully on this Linux sandbox this phase, and the full repository test suite (294 tests) passed on this same Linux environment (`python3 -m pytest tests/ -q` → 294 passed).

## 9. A100 integration

**Yes, the same Colab runtime can perform all six stages** (acquisition, extraction, dataset construction, XGBoost baseline, MTL/A100 training, holdout evaluation) **without transferring the dataset to a different environment**, provided the runtime is switched to a GPU/A100 instance type for the training stages (Colab does not run acquisition and A100 training concurrently on the same instance type in practice — acquisition is CPU/network-bound and does not need a GPU runtime, while training does). The cleanest workflow:

1. **CPU runtime**: run all acquisition/extraction/validation batches (Parts 3-7), persisting everything to Drive.
2. **Switch to A100 runtime** (same Colab notebook, same mounted Drive — switching accelerator type does not lose the Drive mount): load the persisted train/holdout CSVs directly from Drive.
3. Train the XGBoost baseline (CPU-bound, could even run on either runtime type) and then the MTL/A100 experiment, both reading the same Drive-persisted dataset, with no intermediate export/import step.
4. Holdout evaluation reads the same Drive-persisted holdout CSV.

**What must be "transferred"**: nothing across environments — only the Colab *runtime type* changes (CPU to GPU), and Drive, being mounted identically regardless of runtime type, is the one constant storage layer across that switch. **A100_SAME_RUNTIME = YES** (same Colab notebook/session concept, same Drive mount; only the underlying compute accelerator changes, which Colab supports via its standard "Change runtime type" mechanism, not a data migration).

## 10. Execution sequence

1. Start a Colab CPU runtime; mount Drive (`from google.colab import drive; drive.mount('/content/drive')`); clone or sync the repository to `/content/SIH-Hyperlocal-Warning`.
2. Query actual free temp disk (`shutil.disk_usage('/content')`) and confirm or adjust the Part 3 batch size against the real number, not the assumption used for planning.
3. Create the Drive directory structure from Part 4 (not done this phase).
4. For each of the 30 batches (10 event groups each) in `docs/PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv`: run the Part 5 lifecycle, writing provenance/logs/processed rows to Drive and purging temp raw files only after Part 7's six-condition COMPLETE check passes.
5. After all 30 batches, rebuild the cumulative train/holdout CSVs (combining the existing 20 acquired groups with the newly acquired 300) using the unmodified `build_phase_0_4_20_dataset.py`, pointed at the Drive-persisted processed batches.
6. Re-run the full existing test suite against the Colab checkout as a final compatibility confirmation before treating the acquisition as done.
7. Only then proceed to the XGBoost baseline (Phase 0.4.22's Part 7 gate) and, if it clears, the A100/MTL experiment.

## 11. Reproducibility limitations

- **DATASET_PROVENANCE = preserved**: every fact needed to audit what was acquired and how it was processed survives indefinitely in Drive.
- **RAW_GRIB_REPROCESSING = requires re-download**: once a batch's temporary raw GRIB files are purged from Colab's disk, they are gone from this pipeline's storage entirely. Re-running feature extraction with a changed script in the future means re-downloading the same files from GDEX again (using the preserved URL/cycle/lead/SHA256 to fetch the identical bytes), not regenerating them from anything stored in Drive. This is an explicit, accepted tradeoff for staying within the user's available storage, not an oversight.
- The combined dataset after this acquisition (existing 20 + new 300 = ~320 event groups) must have its **actual final row counts determined by running the real extraction**, not assumed from the manifest's target counts — GRIB2 read failures, missing messages, or manifest/label drift between now and acquisition time could still reduce the realized count below 320, exactly as the existing builder's fail-closed design would report rather than silently paper over.

## 12. Next execution step

Do not begin acquisition yet. The next concrete step is to start a Colab notebook, mount Drive, clone the repository, and run Part 10 Step 2 (the real `shutil.disk_usage` check) to confirm or revise the Part 3 batch size against the actual runtime — only after that should Batch 1 of 30 begin.
