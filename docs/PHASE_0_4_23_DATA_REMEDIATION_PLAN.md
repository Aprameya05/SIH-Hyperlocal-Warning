# Phase 0.4.23 — Data Foundation Remediation Plan

Design-only phase. No training, no download, no production/UI/label change, no commit/push. Companion documents: `PHASE_0_4_23_TS_ACQUISITION_PLAN.md`, `PHASE_0_4_23_FF_NEGATIVE_LABEL_CONTRACT.md`, `PHASE_0_4_23_MTL_FEASIBILITY.md`. This document covers TS holdout design, CB remediation, A100 gate conditions, and the storage/time plan.

## Part 2 — TS holdout design

**Problem restated**: the current 6-group holdout includes 3 groups (2023-12-31T18Z, 2024-01-01T00Z, 2024-01-01T06Z) that are three GFS cycles 6 hours apart, very plausibly one continuous synoptic episode — not leakage (verified in Phase 0.4.21: distinct `event_group_key`s, non-overlapping valid-time windows, zero shared source files), but a real reduction in how much the holdout actually tests generalization to *independent* weather.

**Revised selection procedure for future holdout construction**:

1. Build the full candidate pool of labeled TS event groups first (per the acquisition plan), then **cluster candidate dates into episodes** using a simple, defensible rule: any two candidate dates (same label class or not) within 48 hours of each other are provisionally treated as the same episode unless there is a specific reason to believe otherwise (e.g. a documented distinct storm system) — a conservative rule that errs toward calling things "the same episode" rather than risking undercounting correlation, consistent with how the 2023-12-31/2024-01-01 cluster was itself judged in Phase 0.4.21.
2. **Select holdout episodes, not holdout event groups.** Once episodes are identified, draw the 60-group (Part 1 target) holdout allocation by episode, so that two groups from the same 48-hour episode never both land in the holdout (or both land in train) — eliminating the exact failure mode flagged in Phase 0.4.21 by construction, not by after-the-fact auditing.
3. **Require holdout episodes to span at least 4 distinct years and at least 4 distinct months**, given the archive supports this (Part 1 above: positives exist across 11 years and 9+ months). This is stricter than "whatever is left after train selection" and should be enforced as a selection constraint, not a post-hoc check.
4. **Preserve the existing leakage machinery** (`assign_partition()`, `validate_no_event_group_key_crosses_partitions()`) unchanged — this plan adds an episode-awareness layer on top of, not instead of, the existing event-group-key partition safety that Phase 0.4.21 already verified at 0 violations.
5. Keep positive/negative holdout representation roughly matched to the overall target ratio (Part 1: 150/150 TS-wide, so holdout should carry a comparable ~1:1 mix within its 60 groups, not skew to one class because of episode clustering).

This procedure is a design for the *next* acquisition-and-split phase; it does not retroactively rebuild the existing 40-file manifest or its TRAIN/HOLDOUT split, per this phase's explicit constraint.

## Part 3 — CB temporal-resolution remediation

**Traced directly this phase** (not inferred): `SIH_PANINDIA_GRID_LABELS_20260930_143541Z/scripts/build_panindia_cb_labels.py` reads `imd_rain/rain/{2015-2025}.grd` — the IMD 0.25-degree **gridded daily rainfall** product (one `.grd` binary file per year, confirmed present in the repo for all 11 years 2015-2025). The script's own header comment is explicit and matches its executed behavior: *"this is a DAILY product. Output labels carry `temporal_resolution="daily"` and apply to all 4 six-hour slots"* — the limitation is already honestly documented in code, not hidden; the gap is that this caveat has not consistently traveled to every downstream presentation of CB risk (the same pattern flagged repeatedly in the A-to-Z claim audit for other components).

- **Underlying IMD rainfall resolution**: daily only. No sub-daily (hourly/6-hourly) IMD rainfall product exists anywhere in this repository.
- **What the current label represents**: "did this cell's daily-max rainfall reach 64.5mm on this calendar date" — a single yes/no fact about the whole day.
- **How it's applied to slots**: the identical day-level label is copied onto all 4 six-hour slot rows (`slot0`-`slot3`), verified directly from the raw `cb_labels.csv.gz` rows this phase (Phase 0.4.22).
- **Why incompatible with a 2-6h target**: a 2-6h nowcast model is supposed to predict risk in a specific narrow window; a label that is identical across all 4 slots of a day cannot distinguish "heavy rain fell in slot 2 only" from "heavy rain fell across the whole day" — training a slot-specific head on this label teaches it to predict the day's outcome, not the slot's, which overstates the temporal precision of whatever the model then claims.

**Options evaluated against only the data actually in this repository**:

- **Option A — retain CB as a daily hazard, explicitly separated from the 6h nowcast models.** Fully supported by existing data (the `.grd` files are genuinely daily; no engineering effort can extract sub-daily signal from them). **Recommended.**
- **Option B — derive six-hour CB labels from the underlying rainfall observations.** Not supported: the `.grd` source itself has no sub-daily field to derive from. This would require a different, currently-absent data source (see Option D).
- **Option C — event-window labels tied to rainfall accumulation.** Not supported for the same reason as B — any sub-6h or sub-daily accumulation window requires rainfall data finer than daily, which does not exist in this repo's IMD source.
- **Option D — a different observed CB dataset already available locally.** Checked: IMDAA (which would carry sub-daily reanalysis fields) has never been acquired (P0-4, still open per the A-to-Z execution plan); INSAT-3D/3DR (P0-5) is also not acquired. No other sub-daily precipitation-adjacent dataset was found in the repository's file listing. **Not currently available.**

**Recommendation: Option A.** Keep CB as a daily-resolution hazard product, presented and modeled as such (a "CB risk today, this cell" output, not "CB risk this 6-hour window"), and do not train a 6h-sloted CB head on the duplicated daily label. This requires a UI/claims correction (out of scope to implement here — flagged for the next phase that touches CB-facing surfaces) alongside the labeling correction, since presenting a daily label as slot-specific is exactly the kind of claim this project's own audit standard (A-to-Z Claim Audit) exists to catch.

## Part 7 — A100 gate conditions (project-specific, not universal)

These are readiness gates for *this project's* data and architecture, not general claims about when any model anywhere is ready for A100 training:

**A100_GATE_CONDITIONS**:
- Minimum usable event groups: **≥300 for TS** (per Part 1 of the acquisition plan), with CB and FF each having their own remediation complete (resolution fix for CB; negative-label dataset built and GFS-aligned for FF, per the negative-label contract) before being included in any shared-backbone run.
- Minimum positive groups per hazard: **≥150 for TS**; CB's positive definition does not change under Option A (still daily, pan-India, already far above this floor in raw count — the blocker is resolution, not count); FF-observed positive count is capped by the negative-label contract's eligible cell set (≤2,864 per Part 4/5 of the FF document) until a dedicated negative-construction script runs.
- Minimum negative groups where required: **≥150 for TS** (matched to positive, Part 1); FF-observed requires a non-zero NEGATIVE_ELIGIBLE count from the contract in `PHASE_0_4_23_FF_NEGATIVE_LABEL_CONTRACT.md` before it can be included in training at all — today that count is 0 (the contract exists, the dataset it would produce does not).
- Temporal split: holdout must satisfy the episode-aware procedure in Part 2 (no two groups from the same ≤48h episode split across train/holdout), covering ≥4 distinct years and ≥4 distinct months.
- Spatial split: not yet meaningful for TS (single-cell); becomes relevant once/if TS acquisition expands beyond VOBL — out of scope for the current 300-group target, which remains VOBL-only.
- Feature completeness: the existing 17-feature `FEATURE_COLUMNS` contract must remain fully populated for every acquired row (true today, per Phase 0.4.22 §5 — not a current blocker).
- Label quality: every hazard included in a run must have resolved its known label-quality issue (CB resolution; FF-observed negatives) before inclusion — a hazard with a known, undocumented-at-inference-time quality gap must not be silently included.
- Leakage checks: `validate_no_event_group_key_crosses_partitions()` must continue to pass at 0 violations (true today) and must be extended to cover the episode-level check from Part 2 before the next acquisition's holdout is finalized.
- Baseline requirement: an XGBoost baseline must be trained and evaluated on the TS 300-group dataset, with its held-out performance reported honestly (including the small-N caveat if still applicable), **before** any MTL/A100 experiment is attempted — per Phase 0.4.22 Part 10's recommended ordering, unchanged by this phase.

## Part 8 — Storage and time plan

Using the 40 already-acquired files' measured sizes (Phase 0.4.22, re-used here without re-measurement): average 403,658,081 bytes/file (~404MB), range 204.6MB-556.6MB, total 16,146,323,245 bytes (~16.15GB) for 40 files / 20 event groups (~807MB/event group for the 2-file f003+f006 pair).

- **Files for 300 event groups**: 600 GRIB2 files (2 per group).
- **Expected raw storage**: 300 × ~807MB/group ≈ **242GB** (consistent with the Phase 0.4.22 estimate; recomputed here from the same measured per-file sizes, not re-derived independently, since no new files exist to measure).
- **Train/holdout split**: 240 groups (480 files, ~194GB) / 60 groups (120 files, ~48GB), matching the 80/20 target.
- **Expected dataset size after feature extraction**: the existing 40-row CSV (17 features + metadata columns, 44 total columns) is 26,458 bytes (train) + 11,099 bytes (holdout) ≈ 37.5KB combined for 40 rows, i.e. ~940 bytes/row. At 300 groups × 2 rows/group (f003+f006) = 600 rows, extracted-feature storage would be approximately **600 × 940 bytes ≈ 564KB** — negligible compared to the raw GRIB2 storage; the bottleneck is entirely the raw acquisition, not the extracted dataset.
- **Whether `D:\SIH-Historical-GFS\raw` has sufficient space**: **UNKNOWN — cannot be determined this phase.** `D:\SIH-Historical-GFS\raw` is not a folder connected to this session (confirmed in Phase 0.4.21: `device_list_dir` on it returns a names-only skeleton response), so its available free space cannot be read from here. This must be checked on the user's machine directly (e.g. via Windows Explorer/`dir`/`Get-PSDrive` on that drive) before any future acquisition phase begins; this plan does not assume sufficiency.
- **Acquisition time**: not estimated — no network-throughput evidence was gathered this phase (consistent with the no-download constraint), and estimating it without that evidence would be a fabricated number.

## Summary

This phase's output is four documents (this one plus the three companions) that convert Phase 0.4.22's audit findings into a concrete, evidence-derived acquisition and remediation design. No data was moved, no model was trained, and no existing label, manifest, or production file was changed.
