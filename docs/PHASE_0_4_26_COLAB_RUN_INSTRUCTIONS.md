# Phase 0.4.26 — Colab Run Instructions

This is the practical, user-facing guide for running `notebooks/PHASE_0_4_26_TS_ACQUISITION_AND_DATASET.ipynb`. It does not acquire, train, or modify anything by itself — it is instructions only. The notebook has not been executed by Claude; it was prepared this phase and must be run manually in Google Colab.

## Where to open it

Upload `notebooks/PHASE_0_4_26_TS_ACQUISITION_AND_DATASET.ipynb` to Google Drive and open it with Google Colab (right-click the file in Drive → "Open with" → "Google Colaboratory"), or go to colab.research.google.com → File → Upload notebook.

## What runtime to select

A standard **CPU runtime** is sufficient and recommended for this notebook (Runtime → Change runtime type → CPU). This notebook only acquires GFS files, verifies them, extracts features, and validates the dataset — it does **not** train anything, so no GPU/A100 is needed here. The A100 is reserved for the next notebook (Phase 0.4.27+, XGBoost baseline and MTL/A100 training), not this one.

## What it downloads

Up to 600 GFS GRIB2 files (2 per event group × 300 new event groups), drawn from `docs/PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv`, in small batches — never all 600 at once. Each file is roughly 200-560MB (measured range from the 40 files already acquired in earlier phases).

## Where files are stored

- **Temporarily** on the Colab runtime's own disk (`/content/...`), only for the duration of processing one batch.
- **Permanently** in your Google Drive, under `/content/drive/MyDrive/SIH-Historical-GFS/` (subfolders: `raw_archive/`, `processed_batches/`, `train/`, `holdout/`, `manifests/`, `provenance/`, `logs/`).
- **Never** on your Windows C:/D:/G: drives — this notebook does not touch your local machine at all; it runs entirely in Google's cloud.

## Expected storage

Roughly 242-334GB of raw GRIB2 traffic total across all batches (measured-size-based estimate, Phase 0.4.23/0.4.24), but **never more than about 11GB on Colab's temporary disk at once** (one batch's worth), and only the much smaller extracted/processed data (kilobytes to low megabytes per batch) persists long-term in your Drive. Your ~2TB Drive quota comfortably covers the persistent artifacts; the large raw files are never kept.

## Approximate batch size

10 event groups (20 files) per batch by default — about 30 batches total to cover all 300 new event groups. The notebook checks your actual free Colab disk space before every batch and will automatically shrink the batch size (or stop with a clear message) if there isn't enough room, rather than assuming a fixed amount of space.

## What happens if Colab disconnects

Nothing is lost. The notebook keeps a persistent state file in your Drive (`manifests/phase_0_4_26_batch_state.json`) that tracks every file's status (PENDING / DOWNLOADED / VERIFIED / EXTRACTED / VALIDATED / PERSISTED / PURGED / FAILED). Simply reopen the notebook and re-run the cells from the top — it will skip everything already marked PERSISTED or PURGED and resume exactly where it left off. Colab disconnects are expected and normal for a job this size; this is why the state file exists.

## What successful completion looks like

- All 300 new event groups reach `PURGED` status in the state file (meaning: downloaded, verified, extracted, validated, persisted to Drive, and their temporary raw files safely deleted).
- `phase_0_4_26_train.csv` and `phase_0_4_26_holdout.csv` exist in your Drive's `train/`/`holdout/` folders, combining the existing 20 already-acquired event groups with the new 300.
- A final evidence report cell prints exact counts (event groups, rows, positives, negatives, unknowns, feature completeness, train/holdout separation, any duplicates) — these are **not assumed**, they are computed from the actual data at the end.
- No hard-fail condition was triggered (duplicate event groups, train/holdout overlap, label mismatch, missing feature, corrupted GRIB, provenance mismatch, or incomplete persistent write).

## What files to send back for audit

After the notebook finishes (or if you want an interim check), send back:

- `manifests/phase_0_4_26_batch_state.json` (from Drive)
- `phase_0_4_26_train.csv` and `phase_0_4_26_holdout.csv` (from Drive)
- The final evidence report's printed output (copy/paste or screenshot is fine, or the notebook's own saved output cells)
- Any `FAILED`-status entries in the state file, if present, plus the corresponding log file under `logs/`

This is exactly what the next phase needs to independently verify the acquisition before anything is used for training — nothing will be taken on faith from a summary alone.
