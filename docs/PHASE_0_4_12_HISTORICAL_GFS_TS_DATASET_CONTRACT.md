# Phase 0.4.12 — Historical GFS → VOBL TS Dataset Contract

This is the formal, concise contract later phases implement against. It
adopts the three decisions Phase 0.4.11's audit identified as required
before Stage B acquisition is defined. It does not repeat that audit's
reasoning in full — see
`docs/PHASE_0_4_11_HISTORICAL_GFS_TS_SCALE_READINESS_AUDIT.md` for the
evidence trail.

## 1. Target definition

The prediction target is: **will a thunderstorm be observed at VOBL at
any point during a given 6-hour IST slot window**. This is a per-(cell,
date, slot) binary occurrence target, not a per-minute or per-exact-hour
target — traced directly from both `metar_ground_truth.py` (live) and
`scripts/build_panindia_ts_labels.py` (historical), which define the same
window-based target shape (Phase 0.4.11 Part 1).

## 2. Target time resolution

Fixed 6-hour IST slots:

```
slot 0: 00:00-05:59 IST
slot 1: 06:00-11:59 IST
slot 2: 12:00-17:59 IST
slot 3: 18:00-23:59 IST
```

No sub-slot timestamp exists in the label source for historical rows. A
model trained against this target answers "does slot S contain a TS,"
never "does TS occur at hour H."

## 3. Forecast lead semantics

A GFS forecast's `valid_time` (UTC, converted to IST) determines which
slot it targets, via `ist_slot_for()`
(`scripts/build_vobl_historical_gfs_ts_join.py`). Every native 3-hourly
GDEX d084001 lead maps to exactly one slot. Consecutive 3-hourly leads
from the same cycle (e.g. +3h/+6h, or +9h/+12h) structurally alias to the
**same** slot (Phase 0.4.11 Part 2) — both are legitimate, differently-
informative predictor rows, but both target the identical observed
outcome.

## 4. Event grouping key

```
event_group_key = f"{cell_id}|{ist_date}|{slot_id}"
```

Implemented as `event_group_key_for(cell_id, ist_date, slot_id)` in
`scripts/build_vobl_historical_gfs_ts_join.py`, and written as the
`event_group_key` column (alongside a plain `ist_date` column) in every
pilot row.

**Rule**: rows sharing an `event_group_key` are correlated predictions of
one real-world outcome and must never be split across train/validation/
test. Neither lead of a same-slot pair is discarded or deduplicated —
both remain as separate rows, distinguished by `forecast_lead_hours`.

## 5. Positive label semantics

`label_status = POSITIVE`, `label = 1`: at least one real station/METAR
observation inside the slot's IST window showed TS/VCTS/+TSRA-equivalent
weather. Sourced from `ts_labels.csv`, never generated or inferred by the
join script.

## 6. Negative label semantics

`label_status = NEGATIVE_CONFIRMED`, `label = 0`: the slot's IST window
has fully closed, at least one real observation exists inside it, and
none showed TS. **Never** inferred from the absence of a label row —
every negative in this dataset is a materialized row in `ts_labels.csv`.

## 7. Unknown / unmatched handling

- `label_status = UNKNOWN` in the source label file (e.g. non-VOBL cells,
  or a slot with no observation): such rows are never joined as a
  negative.
- `label_status = UNMATCHED` (join-script-specific): a forecast valid time
  that could not be matched to any row in the label file at all (wrong
  cell, out-of-range date, or ambiguous multiple matches). Recorded in the
  manifest's `unmatched_detail` with an explicit reason — never silently
  dropped, never treated as a negative.

## 8. Primary precipitation feature

`gfs_prate_kg_m2_s` — GFS `prate` (surface, kg m⁻² s⁻¹), extracted
directly at each row's own valid time. Does not require cross-lead
differencing, so it is not subject to the accumulation-window-
compatibility problem `tp` has. Extraction prefers a `stepType="instant"`
message (true point-in-time rate); when the archive file has no instant
`prate` message (observed for the real 2015-03-03 files), falls back to
`stepType="avg"` and records that explicitly in `gfs_prate_note` —
**never silently presented as instantaneous when it is not.** Full
provenance (`gfs_prate_stepType_used`, `gfs_prate_startStep`,
`gfs_prate_endStep`, `gfs_prate_note`) is written alongside the value.
Never named or treated as equivalent to production's `qpe_mm`.

## 9. Secondary precipitation feature

`gfs_precip_3h_interval_mm` — `tp(lead_end) - tp(lead_start)`, computed
only when `compute_interval_precipitation()`'s existing validity check
(unchanged this phase) confirms both accumulation windows share
`startStep=0`. When the check fails (confirmed to happen in this
archive — Phase 0.4.10B found 2015-03-03's f009 `tp` accumulates from
`startStep=6`, not 0), the feature is recorded as unavailable with an
explicit rejection reason in `gfs_precip_3h_interval_mm_note` — never
fabricated, never backfilled from another lead, never silently
substituted with a different quantity.

## 10. Missing-feature policy

Every feature lookup failure (a GRIB message not found, or a regrid
returning unavailable) is recorded by name in the row's `missing_features`
column — never silently defaulted to zero, interpolated, or dropped from
the row. A row with missing features is still retained in the output
(unless the label itself is unmatched); the manifest's `quality_issues`
flags any row missing a column in `REQUIRED_FEATURES`.

## 11. Leakage rules

Every included feature is classified in `FEATURE_LEAKAGE_CLASS`
(`scripts/build_vobl_historical_gfs_ts_join.py`) as one of
`FORECAST_DERIVED` / `USES_FUTURE_INFORMATION` / `UNKNOWN`. The pilot
output must contain zero `USES_FUTURE_INFORMATION` features (checked
programmatically via `features_using_future_information` in every
manifest). `initialization_time < valid_time` is independently verified
for every row, and the GRIB-reported lead is cross-checked against the
recomputed `(valid_time - initialization_time)` difference. CTT
drop-rate and spatial-convergence features remain excluded (not
`TEMPORALLY_INVALID`-included) — see Phase 0.4.11 Part 8.

## 12. Train / validation / test split policy

**Primary: deterministic, year-based chronological holdout.**

```
TRAIN:         2015-2023
TEST/HOLDOUT:  2024-2025
```

Implemented in `scripts/historical_dataset_split.py`
(`assign_partition()`, `DEFAULT_TRAIN_YEARS`/`DEFAULT_HOLDOUT_YEARS`).
Validation within the training period may be chronological or grouped,
to be decided when the actual training dataset is built (not this
phase).

**Hard constraint, checked not assumed**: no `event_group_key` may appear
in more than one partition. Enforced by
`validate_no_event_group_key_crosses_partitions()` in the same module,
which inspects every row's assigned partition against its
`event_group_key` and reports any violation explicitly rather than
silently allowing it.

**Why random row-level splitting is rejected**: per Section 4, rows
sharing an `event_group_key` are correlated predictions of the identical
real-world outcome (e.g. a cycle's +3h and +6h forecasts of the same
slot). A random split could place one such row in train and its sibling
in test, letting a model see a near-duplicate of a test example during
training — the exact train/test leakage this project's standing rules
prohibit creating, even unintentionally.

**Why a year-based holdout tests genuine generalization**: holding out
entire calendar years (2024-2025) the model never saw any forecast from —
at any lead, any cycle, any slot — tests whether the learned predictors
(CAPE, K-Index, shear, etc.) generalize to unseen weather periods, not
whether the model has memorized a specific season's pattern or a
neighboring forecast of the same event. It also trivially satisfies the
grouping constraint, since no single IST calendar date/slot spans a year
boundary.

## 13. Provenance requirements

Every row must carry: `source`, `archive_source`, `source_file`,
`initialization_time_utc`, `valid_time_utc`, `valid_time_ist`,
`forecast_lead_hours`, `slot_id`, `ist_date`, `event_group_key`,
`cell_id`, `label_source`, `label_timestamp`, `label_definition`,
`label_status` (the `REQUIRED_PROVENANCE` list, enforced by the existing
quality-issue check). Every manifest additionally carries
`contract_version` (`CONTRACT_VERSION` constant, currently
`"phase_0_4_12_v1"`) and `contract_doc` (a pointer back to this file), so
any pilot artifact can be traced to the exact contract it was produced
under.

## 14. What remains research-only

The entire `scripts/build_vobl_historical_gfs_ts_join.py` /
`scripts/historical_dataset_split.py` pipeline, every feature it extracts
(including the derived K-Index/Totals-Totals with their Magnus-Tetens
dewpoint), and every label join it performs. None of this is imported by,
or alters, any production code path — verified by
`test_no_production_code_imports_research_builder` and
`test_no_production_code_imports_research_split_utility`.

## 15. What is explicitly NOT production-compatible

- `gfs_prate_kg_m2_s` is not `qpe_mm` and must never be presented as such.
- `k_index`/`totals_totals` here use a **derived** (Magnus-Tetens) dewpoint,
  not GFS's native dewpoint field (none exists at isobaric levels in this
  archive) — not bit-identical to `backend/pipeline.py`'s live
  computation even though the formula is the same.
- CTT drop-rate and the spatial-convergence feature are not present at all
  (structurally unavailable from a single historical cycle's point
  extraction) — a model trained on this dataset cannot use those two
  production signals.
- The dataset's size (4 real rows as of this phase) is far below anything
  trainable; this contract governs how to scale it, not a claim that
  scaling has happened.
