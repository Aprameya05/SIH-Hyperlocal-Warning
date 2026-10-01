# Phase 0.4.22 — Model Readiness Report

Audit + dataset-design phase, closing the question: "What is the largest scientifically defensible training dataset we can assemble from data that actually exists or can be acquired through already-available project sources today?" No training, no fabricated samples, no production/UI change, no commit/push. See `docs/PHASE_0_4_22_DATASET_READINESS_MATRIX.md` for the full per-dataset evidence table this report is built on.

## 1. Current verified data

- 40/40 Phase 0.4.17 manifest historical GFS files acquired and SHA256-valid (Phase 0.4.21, re-confirmed not re-litigated this phase).
- `phase_0_4_20_train.csv`: 28 rows / 14 event groups / 20 positive rows / 8 negative rows. `phase_0_4_20_holdout.csv`: 12 rows / 6 event groups / 6 positive rows / 6 negative rows. Both `validation_status: GREEN`.
- Combined TRAIN+HOLDOUT: 40 rows, **20 unique `event_group_key` values, 20 unique GFS init cycles, 18 unique calendar dates** (two dates each have two distinct init cycles: 2024-01-01 has three groups across three cycles 2023-12-31T18Z/2024-01-01T00Z/2024-01-01T06Z spanning one 15-hour window, already flagged YELLOW in Phase 0.4.21 §11). Label split at the event-group level: **13 positive event groups, 7 negative event groups**.
- Test suite re-run live this phase, unmodified: `python3 -m pytest tests/ -q` → **285 passed, 0 failed** (77.57s). This matches the 285/285 figure reported in Phase 0.4.21 and is now independently re-verified rather than carried over.

## 2. TS readiness

- Full label archive (`ts_labels.csv`): 15,276 rows, 584 positive / 14,692 negative, VOBL-only (1/992 cells).
- GFS-aligned subset actually usable today: 40 rows / 20 event groups / 13 positive / 7 negative — this is a small fraction of the 584 positive labels available in the archive. **The bottleneck is GFS predictor acquisition (historical GRIB2 downloads), not label scarcity**: up to 584 positive event-days theoretically exist to align against, versus 13 currently aligned.
- Max usable training set today: 40 rows (20 event groups), pipeline-smoke-test scale only.

## 3. CB readiness

- Pan-India observed coverage exists (382 of 992 cells seen in the raw positive-label file; summary reports 992/992 coverage overall), genuinely broader than TS.
- **Temporal resolution mismatch, confirmed directly from raw rows**: labels are daily (64.5mm/day IMD threshold) and the identical daily label is copied onto all 4 six-hour slots (`quality_flag: daily_resolution_applied_to_all_4_slots`). For a 2–6h nowcast target this means a model would be trained to predict "did it rain ≥64.5mm *today*," not "will it rain in *this specific 6-hour window*" — the two are not the same target, and training on the duplicated label as if it were slot-specific would overstate the precision of what the label can actually support.
- **Answer to the explicit question "is daily labeling compatible with the intended 2-6h prediction target?": No, not without correction.** It can support a coarser, defensible claim ("elevated CB risk today, this cell") but not a genuine 2–6h-resolution claim. A corrected version would need either (a) sub-daily rainfall observations re-aggregated to each slot's own window, or (b) an explicit, documented downgrade of the CB target to daily resolution with the UI/claims changed to match — both are correction options, not yet implemented, and out of scope to implement in this audit-only phase.
- Not yet GFS-joined beyond the VOBL cell; max usable set today for a genuine 2-6h CB model: **0** until the resolution mismatch is resolved. Usable for a daily-resolution smoke test only, pending that correction.

## 4. FF readiness

- **FF-observed** (INDOFLOODS): 4,548 real flood events, 100% gauge-to-cell mapped (214/214 gauges, 75/992 cells, 7.6% pan-India spatial coverage), collapsing to 4,106 unique positive (cell, date) pairs. Gauge coordinates exist and mapping is already built — the earlier "impossible, no coordinates" framing in `ff_labels_summary.json`/`coverage_report.json` is corrected in the companion matrix doc as a finding of this phase. The real, substantiated blocker is **no valid negative label**: `docs/INDOFLOODS_NEGATIVE_LABEL_AUDIT.md` finds that absence of a recorded event cannot be read as confirmed non-flood, because per-gauge data-coverage ratios are inconsistent (50.5% of gauges have <90% coverage in their own stated operational window; 17.3% have <50%). **Can INDOFLOODS be GFS-aligned? Not yet — no GFS join has been built for any of its 75 cells; what prevents it today is prioritization/acquisition effort, not a structural blocker, since the same GFS acquisition pattern already used for VOBL could in principle be extended to these cells and dates.** What prevents *training* on it, even once GFS-aligned, is the missing negative class.
- **FF-proxy**: 78,442 positive cell-days / 1,472,501 negative-confirmed cell-days, pan-India (378 of 992 cells seen in raw file), daily resolution, explicitly and repeatedly self-labeled `PROXY_NOT_OBSERVED -- not a real flood observation` in every row. Usable only as an explicitly-labeled proxy target, never presented as or substituted for observed flood ground truth.
- Max usable set today: FF-observed is **not usable for binary classification** (no negative class) regardless of sample count; FF-proxy is usable only as a labeled proxy, and is not yet GFS-joined beyond the conceptual VOBL pattern.

## 5. Historical GFS readiness

Computed directly from the 40-row combined TRAIN+HOLDOUT CSV this phase (not inferred):

- **Files**: 40 GRIB2 files, 20 GFS initialization cycles (one cycle = 2 files, f003+f006).
- **Event groups**: 20 (`event_group_key` = `cell_id|ist_date|slot_id`), of which 13 positive / 7 negative.
- **Calendar dates**: 18 unique `ist_date` values (2024-01-01 and 2021-02-19/20 are the only dates contributing more than one event group; see concentration caveat below).
- **Years spanned**: 2015, 2016, 2017(×2), 2018(×2), 2019, 2020, 2021(×3), 2022, 2023(×2), 2024(×4) — 10 distinct years, uneven distribution, 2024 and 2021 over-represented relative to others.
- **Months spanned**: Feb(4), Mar(4), Jun(3), Oct(2), Apr(1), Jul(1), Nov(1), Jan(1), May(1) — 9 of 12 calendar months represented, none from Aug/Sep/Dec.
- **Lead-hour distribution**: exactly 20 at f003, 20 at f006 (every event group has both leads — complete, no gaps).
- **Slot distribution**: slot 2 dominates (24 of 40 rows), slots 0/1/3 are minority (4/4/8 rows) — skewed, not balanced across the day.
- **ROWS ≠ EVENT GROUPS ≠ INDEPENDENT WEATHER EVENTS, explicitly distinguished**: 40 rows reduce to 20 event groups (2 leads/group); 20 event groups reduce to at most 18 *calendar days*, and — per the Phase 0.4.21 holdout-concentration finding — the three 2024-01-01-area holdout groups (2023-12-31T18Z, 2024-01-01T00Z, 2024-01-01T06Z) are three GFS cycles 6 hours apart describing what is very plausibly **one single continuous synoptic episode**, not three independent weather events. The true count of independent weather episodes in the current 20-group set is therefore likely closer to **18** than 20, and possibly lower still if the 2021-02-19/2021-02-20 pair (also adjacent calendar days) is a continuation of one system rather than two. This report does not inflate sample size by counting rows or event groups as if they were independent events.

## 6. Maximum defensible dataset available now

- **TS**: 40 rows / 20 event groups / 13 positive / 7 negative — pipeline-smoke-test scale. Not a defensible baseline (see Part 7 gates below).
- **CB**: 0 rows usable at the intended 2–6h resolution (daily/6-hourly mismatch unresolved); the full daily-resolution label archive (49,613 positive cell-days) is large and pan-India, but not GFS-joined and not slot-resolved.
- **FF-observed**: 0 usable rows for binary classification (no negative class), despite 4,106 real positive (cell, date) pairs existing and being grid-mapped.
- **FF-proxy**: 78,442 positive / 1,472,501 negative cell-days exist and are internally consistent, but 0 are GFS-joined beyond the conceptual VOBL pattern, and the label must never be presented as observed ground truth.
- **Largest scientifically defensible dataset assemble-able today, honestly stated: the 40-row/20-event-group TS set.** Every other hazard has either a resolution mismatch (CB), a missing negative class (FF-observed), or a proxy-label/no-GFS-join gap (FF-proxy) that blocks training today without further work this phase was not authorized to perform (acquisition, label correction, or negative-sampling design).

## 7. XGBoost baseline gate

Minimum data gates, distinguishing four tiers (based on standard events-per-variable (EPV) guidance for the 17-feature `FEATURE_COLUMNS` contract — EPV≥10 as a floor, EPV≥20 preferred for a defensible result):

| Tier | Approx. positive-event-group floor (17 features) | Current TS status |
|---|---|---|
| Pipeline smoke test | Any N≥1 per class, just to confirm the code runs end-to-end | **MET** (13 positive / 7 negative event groups) |
| Exploratory model (directional signal only, no claimed generalization) | ~30-50 positive event groups (EPV≈2-3) | NOT MET |
| Defensible baseline (XGBoost, reportable with caveats) | ~170 positive event groups (EPV≈10), ideally with a comparably sized or larger negative class and genuine multi-year/multi-season spread | NOT MET |
| Serious MTL/Transformer experiment | Several hundred to low-thousands of event groups per hazard, plus all three hazards independently at that scale (shared backbone needs enough data per head, not just combined count) | NOT MET for any hazard |

Event-group-count requirements differ by hazard only in label *quality*, not in the EPV arithmetic above (same 17-feature contract is shared): TS is closest to usable (observed, GFS-joined, just small); CB and FF-observed each have a structural gap (resolution mismatch; no negative class) that must be resolved before EPV-based scaling is even the binding constraint; FF-proxy is EPV-sufficient in raw count but needs GFS-joining and must stay labeled as a proxy throughout.

**XGBOOST_GATE = NOT_READY.**

## 8. MTL/A100 gate

**A100_GATE = NOT_READY.**

Justification against each required axis:
- **Training sample scale**: 20 event groups total across all hazards combined (TS only) — multiple orders of magnitude below even the defensible-XGBoost-baseline floor (~170 positive event groups), let alone an MTL/Transformer floor (hundreds-to-thousands per head).
- **Label quality**: TS is observed and clean; CB has a documented resolution mismatch; FF-observed has no valid negative class; FF-proxy is explicitly non-observed. Only one of four candidate datasets is currently clean end-to-end.
- **Feature completeness**: the 17-feature `FEATURE_COLUMNS` contract is complete and consistently populated for every acquired TS row (no missing-feature gaps found in the inspected CSVs) — this axis is not the blocker.
- **Hazard coverage**: 1 of 3 hazards (TS) has any GFS-aligned trainable data at all; CB and FF have zero rows that clear their respective structural blockers.
- **Train/holdout separation**: event-group-key partitioning is leakage-safe and verified (0 cross-partition violations, Phase 0.4.21), but holdout diversity is compromised — 3 of 6 holdout groups are concentrated in one 15-hour window, meaning the holdout likely does not test generalization across independent synoptic episodes.
- **Leakage controls**: sound design (`assign_partition()`, `validate_no_event_group_key_crosses_partitions()`) is in place and tested, but controls being sound does not substitute for data scale.
- **Meaningful evaluability**: with 6-12 holdout rows (6 event groups, 3 of which are correlated), no held-out metric computed on this dataset can be presented as a generalization estimate rather than an anecdote.

**Explicit next steps given NOT_READY**: do not proceed to A100/MTL training; first close the XGBoost defensible-baseline gate for TS (Part 9 below gives a concrete target), and in parallel resolve the CB resolution mismatch and the FF-observed negative-label gap so that all three hazards have at least a baseline-eligible dataset before any shared-backbone MTL experiment is attempted (an MTL model with two of three heads undertrained relative to the third is not a meaningful experiment).

## 9. Data acquisition required before A100

Targets are derived, not invented, from: the EPV≥10 defensible-baseline floor for the existing 17-feature contract (Part 7), the size of the already-available TS label archive (584 positive / 14,692 negative rows, so acquisition is predictor-limited, not label-limited), the need for genuine (non-concentrated) train/holdout separation (Phase 0.4.21 §11), and the already-observed per-event-group storage cost (~0.807 GB/event group = 2 files averaging ~404MB each, measured directly from the 40 acquired files this phase).

| Target | Value | Basis |
|---|---|---|
| TARGET_EVENT_GROUPS | 300 | ~15x current (20), reaching the EPV≈10 defensible-baseline floor for positives with room for a comparable negative class |
| TARGET_POSITIVE | 150 | EPV≈8.8 against 17 features (close to the EPV≥10 floor; achievable against the 584-row positive label archive, which has far more positive VOBL days than this) |
| TARGET_NEGATIVE | 150 | Matched to positive count to avoid the current 13:7 (≈1.9:1) imbalance; a roughly balanced set is preferred for a first defensible baseline |
| TARGET_TRAIN | 240 (80%) | Standard 80/20 split, consistent with the existing manifest-driven partitioning design |
| TARGET_HOLDOUT | 60 (20%) | Must be explicitly checked for the same concentration issue found in Phase 0.4.21 — event groups drawn from at least 15 distinct, non-adjacent calendar dates/synoptic episodes, not clustered within any single multi-cycle window |
| TARGET_SEASON_COVERAGE | All 12 calendar months represented, with pre-monsoon (Mar-May) and monsoon (Jun-Sep) — VOBL's primary thunderstorm seasons — weighted higher than the currently-absent Aug/Sep/Dec | Current set has 0 events in Aug/Sep/Dec; thunderstorm climatology for VOBL concentrates in pre-monsoon/monsoon, so under-sampling those months understates the real positive rate |
| TARGET_YEAR_COVERAGE | At least 2 independent event groups per year across 2015-2025 (11 years), none contributing more than ~15% of the total | Current set already spans 2015-2024 unevenly (2024 alone contributes 4 of 20 groups = 20%); spreading further reduces any single year's climate anomalies from dominating the trained signal |

**Acquisition priorities, in order**: (1) more independent positive event groups — already-labeled VOBL positive days from the 584-row archive, prioritizing dates not adjacent to any already-acquired cycle, to avoid repeating the Phase 0.4.21 concentration problem; (2) more negative event groups, since the current 7:13 ratio under-represents the negative class relative to the 14,692:584 ratio in the full label archive — negatives are not scarce in the archive, only in what's been acquired; (3) seasonal diversity, specifically filling the Aug/Sep/Dec gap; (4) year diversity, avoiding further concentration in 2021/2024; (5) independent-episode diversity — explicitly excluding candidate dates within 24-48h of an already-selected date unless the labels justify treating them as genuinely distinct systems; (6) CB and FF-observed are not included in this specific acquisition target because they are blocked by a labeling/resolution issue, not an acquisition-volume issue — acquiring more GFS files for them before that issue is resolved would not move their gate status.

This target does not estimate acquisition *time* (network/runtime conditions were not measured this phase) but does estimate storage: 300 event groups × ~0.807 GB/group ≈ **242 GB**, well within the already-demonstrated single-file-up-to-557MB handling capability of the existing acquisition tooling (`scripts/design_phase_0_4_17_acquisition_candidates.py` generates a manifest, `scripts/acquire_phase_0_4_19_batch.py` acquires against it — both already manifest-driven and already proven at 40-file scale with 0 missing/invalid/partial files). No download was performed to validate this estimate further; it is a storage projection only, consistent with this phase's no-acquisition constraint.

## 10. Recommended next phase

Acquire the TARGET_EVENT_GROUPS=300 TS batch per Part 9 (a new acquisition-design + acquisition-execution phase, not this audit phase), in parallel with: (a) a label-correction phase for CB's daily/slot resolution mismatch, and (b) a negative-labeling-strategy design phase for FF-observed (INDOFLOODS) that does not fabricate negatives from absent data — e.g. restricting negative candidates to gauges with ≥90% `ratio_level` coverage in `metadata_indofloods.csv`, per the "what would move this from Case B to Case A" note in `docs/INDOFLOODS_NEGATIVE_LABEL_AUDIT.md`. Only after at least TS clears the defensible-baseline gate (Part 7) should an XGBoost baseline be trained and evaluated; only after all three hazards clear it should A100/MTL experimentation begin.

## 11. Production/scope confirmation

No production inference code, frontend/UI, labels, features, thresholds, models, workflows, or deployment configuration were changed in this phase. No data was downloaded. No model was trained. No commit or push was made. The two corrections noted in this report (CB resolution mismatch, FF-observed "impossible" claim) are documented findings only — the underlying generated files (`cb_labels_summary.json`, `ff_labels_summary.json`, `coverage_report.json`) were not edited, since doing so would itself be a label/data change outside this audit-only phase's scope.
