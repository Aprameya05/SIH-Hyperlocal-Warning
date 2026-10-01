# Phase 0.4.17 — Historical GFS Sample-Size & Acquisition Design Audit

Audit/design phase only. No GFS files downloaded. No model trained. No
production code touched. No commit, no push. Every number in this
document is either recomputed directly from a real file this phase read,
or explicitly marked as a planning estimate.

## 0. Verification of the Phase 0.4.16 GREEN claim

Before anything else, `data/processed/historical_gfs_ts/historical_gfs_ts_dataset.csv`
and its manifest were read directly from your machine (staged fresh this
phase, not reused from memory). Confirmed independently: `row_count=16`,
`event_group_count=9`, `positive_count=7`, `negative_count=9`,
`unknown_count=0`, `cycles_skipped=[]`, `validation_status="GREEN"`, and
all 8 source cycles present with 2 rows each. The claim in your message is
correct — verified, not assumed.

## 1. Audit of the current dataset (recomputed directly from the CSV)

| metric | value |
|---|---|
| total rows | 16 |
| unique event groups | 9 |
| positive event groups | 4 |
| negative event groups | 5 |
| positive rows | 7 |
| negative rows | 9 |
| rows per event group | 7 groups × 2 rows, 2 groups × 1 row |
| forecast lead distribution | f003: 8, f006: 7, f009: 1 |
| IST slot distribution | slot 0: 2, slot 1: 5, slot 2: 5, slot 3: 4 |
| month distribution | Jan:2, Mar:2, Apr:2, Jul:4, Aug:2, Nov:2, Dec:2 |
| season distribution | monsoon:6, pre-monsoon:4, winter:4, post-monsoon:2 |
| year distribution | every year 2015,2016,2017,2019,2020,2021,2022,2023 → 2 rows each |
| precipitation provenance | `gfs_prate_stepType_used`: instant=10, avg=6 |
| `gfs_precip_3h_interval_mm` missing | 9 of 16 (56%) — by contract (lead-start rows and the one incompatible-window pair), not by accident |
| all 15 core forecast features | 0% missing across all 16 rows |
| source cycle distribution | all 8 cycles × 2 rows each |
| partition | 16/16 `train`; 0 `holdout` |

The 2 single-row event groups are both from the 2015-03-03 cycle, whose
f003 and f009 leads land in two *different* IST slots (slot 1 and slot 2)
rather than the same slot — a real, previously-documented archive finding
(Phase 0.4.8/0.4.10B), not a defect in this audit.

## 2. Audit of the full observed TS label space

File used: `SIH_PANINDIA_GRID_LABELS_20260930_143541Z/processed/labels/ts_labels.csv`.
Columns used: `timestamp` (parsed into `date` + `slot_label`), `cell_id`,
`hazard`, `label_status`, `source`. Filtered to `cell_id=IND_13.0_78.0`,
`hazard=ts`. No label was created, inferred, or altered.

| metric | value |
|---|---|
| full date range | 2015-01-02 → 2025-12-31 |
| total 6-hour slots (this cell/hazard) | 15,276 |
| total POSITIVE slots | 584 |
| total NEGATIVE_CONFIRMED slots | 14,692 |
| total UNKNOWN/other slots | **0** — every slot in this archive is already resolved to POSITIVE or NEGATIVE_CONFIRMED |

Positives by year: 2015:59, 2016:36, 2017:90, 2018:63, **2019:2**, 2020:51,
2021:87, 2022:89, 2023:52, 2024:20, 2025:35. **2019 is an outlier** — 2
positive slots for the entire year versus 36–90 in every other year. This
phase does not know why (a genuinely quiet year, or a gap in the
underlying station record) and does not speculate further — it is
reported as an observed sparsity, not explained.

Positives by IST slot: slot 0: 113, slot 1: 34, slot 2: 250, slot 3: 187.
Slot 2 (12:00–17:59 IST, afternoon) dominates, consistent with typical
convective-afternoon thunderstorm timing; slot 1 (early morning) is the
rarest.

Positives by month: Feb:5, Mar:24, Apr:76, May:162, Jun:70, Jul:32, Aug:42,
Sep:67, Oct:84, Nov:20, Dec:2. **January: 0** — zero POSITIVE slots in
January across all 11 years of this archive.

Positives by season: pre-monsoon (Mar–May): 262 (45%), monsoon (Jun–Sep):
211 (36%), post-monsoon (Oct–Nov): 104 (18%), winter (Dec–Feb): 7 (1.2%).
Negatives are comparatively flat across months (1,053–1,336/month) and
slots (3,569–3,785/slot) — the seasonal concentration is specific to
positives, not an artifact of the station reporting less in winter.

**This is a real climatological signal in the label archive, not an
acquisition-side gap**: VOBL thunderstorms in this dataset are
overwhelmingly a pre-monsoon/monsoon/post-monsoon phenomenon, and winter
TS events, while not literally zero (7 exist), are genuinely rare.

## 3. The actual forecast sampling unit (Phase 0.4.11/0.4.12 contract, restated precisely)

- **Prediction unit**: one (`cell_id`, 6-hour IST `slot_id`) target per
  `ist_date`.
- **Cell**: `IND_13.0_78.0` (VOBL), reused from the existing canonical
  mapping — not re-derived.
- **`event_group_key`**: `f"{cell_id}|{ist_date}|{slot_id}"` — the unit
  that must never be split across train/holdout or treated as
  independent replicates.
- **GFS initialization cycle + forecast lead → valid time** determines
  which slot a given forecast file targets, via `ist_slot_for()`.

**Are f003/f006/f009 independent samples?** No — not in general. Two
leads from the *same cycle* that land in the *same* IST slot (the
f003/f006 pattern used throughout this project) are two different
forecasts of the *identical* real-world outcome: correlated predictors of
one event, never two independent statistical observations. This project's
own `event_group_key` contract exists specifically to prevent treating
them as independent. The one exception observed so far (2015-03-03's
f003/f009) is not a counter-example to this rule — it happened because
the slot boundary fell between +3h and +9h for that specific cycle, so
the two leads ended up targeting two genuinely different events. This is
incidental to where a slot boundary falls relative to the cycle's init
hour, not a general property of longer leads.

**Which leads are actually useful for a 2–6h prediction problem?** Short
leads (f003, f006) are the direct match — they are what this project has
used throughout, and they're consistent with the intended nowcasting-
adjacent problem framing. Extending to longer leads (f009, f012, ...)
should be done only when a specific target slot genuinely requires a
longer lead to get two natively-spaced forecasts inside it (as happened,
by chance rather than design, for 2015-03-03) — not as a default strategy,
since longer leads represent measurably degraded GFS forecast skill and
start to look like a different prediction problem.

## 4. Scientifically useful sample targets (NOT a generic "10× features" rule)

Derived from: 15 predictor variables, the current 4 positive / 5 negative
event groups, the need to keep TRAIN (2015-2023) and HOLDOUT (2024-2025)
both populated without leakage, and the real seasonal concentration found
in Step 2. These are **planning ranges**, not proof that hitting a number
makes a result valid.

**A. Minimum dataset for pipeline/baseline validation**: ~20–30 event
groups. Reasoning: a grouped k-fold cross-validation (k=3–5) needs at
least 2–3 groups per class per fold to even run without a degenerate
fold, which is roughly 3× the fold count per class → ~10–15 per class →
20–30 total. This range is deliberately what Phase 0.4.17's own candidate
batch (Step 11, 21 event groups) targets. At this scale, the goal is
proving the extraction/grouping/split mechanics work across real
multi-year, multi-season data — not reporting a trustworthy statistical
result.

**B. Minimum dataset for a preliminary statistical model** (logistic
regression / a shallow baseline tree over the 15 features): the relevant,
*named* statistical guideline here is "events per variable" (EPV) for
logistic regression specifically — convention (Peduzzi et al.) recommends
EPV ≥ 10, counted on the minority class, per predictor. With 15
predictors, EPV ≥ 10 → **at least ~150 positive event groups** to meet
the named guideline itself, matched by a comparable or moderately larger
negative pool (a 1:1 to 1:2 positive:negative ratio is a reasonable
baseline target — going further toward the archive's natural ~1:25
imbalance mainly adds storage cost without much added statistical power
for an initial baseline).

Two tiers, kept explicit rather than blurred into one number (this
revises the single "~150 floor" figure used in the original Phase 0.4.17
draft, which Phase 0.4.18's audit found was being cited inconsistently
elsewhere in this document — see Strategy B below):

- **Minimum-attempt tier (~100 positive event groups)**: enough to fit a
  logistic-regression/shallow baseline at all without a degenerate
  minority class, but this is **below the EPV ≥ 10 floor for 15
  predictors** (EPV ≈ 6.7 at 100 positives) and must be reported as such
  — any model trained at this tier is a preliminary/exploratory fit, not
  one meeting the named statistical guideline.
- **EPV ≥ 10-defensible tier (~150 positive event groups)**: the actual
  floor the named guideline requires for 15 predictors. This is the
  number that can be described as "meeting EPV ≥ 10," not the 100-tier
  figure above.

So: **~150 positive (to meet EPV ≥ 10), ~150–300 negative, ~300–450
event groups total** as the floor for a preliminary statistical attempt
that meets the named guideline; ~100 positive is a lower, explicitly
sub-EPV "can fit something, cannot call it EPV-adequate" floor. Meeting
EPV ≥ 10 is a sample-size necessary condition, not sufficient proof of
statistical validity — it says nothing about calibration, feature
quality, temporal autocorrelation between nearby event groups, or
generalization to 2024–2025, all of which still need separate
evaluation once the data exists.

**C. A more defensible dataset for a real historical baseline**
(XGBoost or a tuned logistic baseline, with a genuine year-based holdout
evaluation): needs multiple years *and* seasons represented with enough
density per cell to report confidence intervals on recall/precision that
aren't dominated by sampling noise. Target: **~500–1,000 event groups**
(roughly 150–300 positive, 350–700 negative), spanning most or all of
2015–2023, all 4 seasons (accepting that winter positives are capped
around the ~7 that climatologically exist per Step 2 — this cannot be
engineered around), plus a genuinely separate HOLDOUT pool of **~30–60
event groups** from 2024–2025 reserved purely for evaluation.

**D. Scale required before attempting deep-learning / MTL work**: order
of magnitude **5,000–20,000+ event groups** for a single cell/single
hazard, and realistically far more once extended pan-India/multi-hazard
(which is this project's eventual stated direction per
`MASTER_SIH_REQUIREMENT_MATRIX.md`/`MASTER_REMEDIATION_PLAN.md`, not
reproduced here). At that scale, cycle-by-cycle manual selection (as used
in every phase so far) stops being practical, and the acquisition
approach would need to shift to continuous/bulk date-range downloads —
which multiplies storage into the hundreds of GB to multi-TB range (Step
9). **This phase does not recommend attempting Target D with the current
acquisition tooling or at the current project stage.**

## 5. Positive/negative balance

Current: **4 positive event groups**, **5 negative event groups**
(counting event groups, not rows — f003/f006 pairs from the same event
are one group, not two samples).

Available, unacquired, in the full label archive: **580 more positive
event groups** (584 total − 4 already acquired) and **14,687 more
negative event groups** (14,692 − 5 already acquired). Label availability
is not the bottleneck anywhere in this plan — GFS file acquisition
(storage/bandwidth) is.

Recommendation: do **not** chase the archive's natural ~1:25 positive:
negative ratio. For a baseline-model target (Step 4.B/C), acquire
negatives at roughly a 1:1 to 1:2 ratio to positives — enough to represent
genuine negative conditions across seasons/years without spending
hundreds of extra GB on negatives whose marginal value drops quickly past
a few hundred (negative/no-TS atmospheric states repeat heavily within a
season). The realistic natural imbalance can be reintroduced later, at
evaluation time, by *not* undersampling the HOLDOUT negative pool — i.e.
balance TRAIN for learnability, keep HOLDOUT closer to natural prevalence
for a realistic skill estimate. This is a design recommendation, not
something this phase implements.

## 6. Temporal split design

Contract unchanged: **TRAIN 2015–2023, HOLDOUT 2024–2025**, no
`event_group_key` may cross partitions (enforced programmatically by
`validate_no_event_group_key_crosses_partitions()`, unchanged).

Current state: all 9 acquired event groups are TRAIN; **0 HOLDOUT event
groups exist yet.**

Label-archive support: TRAIN has 529 positive / 12,067 negative slots
available; HOLDOUT (2024–2025) has 55 positive / 2,625 negative slots
available — ample for a holdout set of even several hundred events. **A
genuine evaluation cannot happen until HOLDOUT cycles are actually
acquired** — right now there is nothing to evaluate against.

One label-level observation, not an acquisition failure: 2024 and 2025
show 20 and 35 positive slots respectively — both below the 2015–2023
per-year range of 36–90 (excluding the 2019 outlier). Two years is too
small a sample to call this a trend; it is reported as-is.

Recommended acquisition split: once a TRAIN batch is chosen (Strategy A/B/C
below), acquire a HOLDOUT batch at roughly 10–20% of the TRAIN volume,
composed from 2024–2025 only, and treat it as off-limits for any
training or tuning decision from the moment it's built — exactly the
discipline Phase 0.4.17's own candidate manifest already applies to its
6 HOLDOUT-flagged candidates (Step 11).

## 7. Seasonal coverage

The current 9 acquired event groups' **positive** coverage is: pre-monsoon
×2 (2015-03-03, 2017-04-16), monsoon ×1 (2021-07-24), post-monsoon ×1
(2023-11-06), **winter ×0**. Given Step 2's finding that winter positives
are genuinely rare (7 of 584 in the full archive), this is not a sampling
failure — it reflects reality — but it does mean the current dataset, if
used naively, would imply "winter never produces a VOBL thunderstorm,"
which the label archive itself contradicts (7 real documented cases
exist). Phase 0.4.17's candidate batch (Step 11) deliberately includes 2
of those 7 winter positives to correct this, explicitly flagged as a
targeted correction rather than a claim that winter is proportionally
represented.

**If the available labels are too sparse to support a stronger
conclusion here**: they are, for winter specifically — 7 real cases
across 11 years is not enough to characterize a within-season pattern,
only to confirm winter TS at VOBL is rare-but-real.

## 8. GFS acquisition efficiency

**Smallest useful acquisition unit confirmed**: one GFS cycle × 2 leads
(f003 + f006) — exactly the unit used throughout this project. All 15
required predictor fields plus `tp`/`prate` have been confirmed present
in a single GRIB2 file in every one of the 16 rows built so far (0
missing-field incidents) — no need for multiple files per cycle to
assemble the full feature set.

Whether a single cycle can cover **two** target slots (doubling event
yield per 2-file download) depends entirely on where the slot boundary
falls relative to the cycle's init hour — it happened for 2015-03-03
(f003→slot 1, f009→slot 2) but is not something that can be engineered on
demand; it is a fortunate side effect, not a general acquisition
strategy, and should not be assumed when planning cycle counts.

**THREDDS/OPeNDAP subsetting — explicitly NOT verified working.**
`docs/PHASE_0_4_2_HISTORICAL_GFS_FEASIBILITY.md` and
`docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md` both *identify*
`tds.gdex.ucar.edu` (OPeNDAP) and `thredds.rda.ucar.edu` (catalog-based
subsetting) as theoretically capable of returning a bounding-box/variable
subset without downloading the full ~200–550 MB file. **Neither has ever
actually been exercised successfully in this project** — Phase 0.4.2
recorded that this sandbox's network proxy rejects `rda.ucar.edu` and
`thredds.rda.ucar.edu` at the connection level, and every one of the 16
real downloads to date used the direct full-file GDEX HTTPS path. This
phase re-confirms that gap rather than assuming it away: **whether
subsetting would actually reduce download size for this archive is an
open, unresolved question**, not a verified efficiency gain. Any
acquisition-scale decision made in Step 10 below assumes full-file
downloads unless and until subsetting is actually tried and measured from
an environment that can reach those hosts (most likely your own machine,
not this sandbox).

## 9. Storage estimate (from the actual 16 Stage A+B files, not the earlier rough estimate)

| metric | value |
|---|---|
| minimum file size | 216.9 MB |
| maximum file size | 551.7 MB |
| mean file size | 373.0 MB |
| median file size | 335.0 MB |
| mean size per cycle-pair (2 files) | ~0.746 GB |

Extrapolated raw storage (full-file downloads, no subsetting assumed):

| cycles (2 files each) | approx. raw storage |
|---|---|
| 50 | ~37 GB |
| 100 | ~75 GB |
| 200 | ~149 GB |
| 500 | ~373 GB |
| 1,000 | ~746 GB |

These are estimates from a 16-file sample with real variance (216.9–551.7
MB) — actual totals for a specific batch will vary.

Extracted CSV size, for comparison: the current 16-row CSV is 15,960
bytes (~998 bytes/row). At 100 rows: ~0.1 MB. At 500: ~0.5 MB. At 1,000:
~1.0 MB. At 5,000: ~5.0 MB. **The final dataset artifact is negligible
compared to raw GRIB storage at any scale considered here** — storage
planning should be entirely about the raw GRIB acquisition step, not the
extracted dataset.

## 10. Three acquisition strategies (none labeled "best" — your decision)

### Strategy A — Minimum viable historical baseline

- ~20–30 event groups (this phase's own candidate batch: 21)
- positive target: ~12–15 event groups
- negative target: ~8–15 event groups
- years covered: most of 2015–2023, ~1–2 events/year
- seasonal coverage: all 4 seasons represented at least once
- holdout coverage: small reserve, 3–6 event groups (2024–2025)
- approx. GFS cycles required: ~21 (42 files) — matches Step 11's manifest
- approx. raw storage: ~16 GB
- expected extracted-row scale: ~40–50 rows
- scientific purpose: prove the pipeline/grouping/split mechanics hold at
  real multi-year, multi-season scale; exercise the code, not a result
- main limitation: nowhere near enough for any trustworthy statistical
  model (Target B territory is ~5–10× this)

### Strategy B — Recommended historical baseline

- ~150–300 event groups (corrected from the original draft's "~150–300
  event groups / ~100–150 positive," which cited the Step 4.B EPV≥10
  figure inconsistently — fixed here per the Phase 0.4.18 audit, Part 3)
- positive target: **~150 event groups** — this is the actual EPV ≥ 10
  floor for 15 predictors from Step 4.B, not the lower ~100
  minimum-attempt tier (also defined in Step 4.B, which is sub-EPV and
  should not be described as "meeting EPV≥10")
- negative target: ~150–300 event groups (roughly 1:1 to 1:2 ratio)
- years covered: full 2015–2023 span, aiming for ~15–30 events/TRAIN year
- seasonal coverage: all 4 seasons every year where labels allow it;
  winter stays capped near its natural ~7-case ceiling per Step 2/7
- holdout coverage: ~30–60 event groups (2024–2025), proportioned similarly
- approx. GFS cycles required: ~150–300 (300–600 files)
- approx. raw storage: ~112–224 GB
- expected extracted-row scale: ~300–600 rows
- scientific purpose: large enough to attempt a genuine logistic-
  regression/XGBoost baseline meeting the EPV≥10 guideline (Step 4.B) for
  15 predictors, with a real year-based holdout evaluation. Meeting
  EPV≥10 is a necessary sample-size condition only — it does not by
  itself establish calibration, generalization, or 2024–2025 holdout
  performance, which still require separate evaluation after acquisition
- main limitation: still single-cell, single-hazard; 100–225 GB and the
  associated download time become a real operational cost; no claim of
  pan-India or multi-hazard generalization. A partial acquisition that
  only reaches the ~100-positive minimum-attempt tier should be reported
  as sub-EPV, not rounded up to "meets Strategy B"

### Strategy C — Large-scale historical archive

- ~1,000+ event groups
- positive target: several hundred
- negative target: several hundred to low thousands
- years covered: full 2015–2023 TRAIN + a substantial 2024–2025 HOLDOUT
  (~150–250 event groups)
- seasonal coverage: maximal — most months represented across many years
- holdout coverage: ~150–250 event groups
- approx. GFS cycles required: ~1,000+ (2,000+ files)
- approx. raw storage: ~750 GB+ (approaching/exceeding 1 TB)
- expected extracted-row scale: ~2,000+ rows
- scientific purpose: begins to approach the scale needed to seriously
  evaluate a more complex model (regularized gradient boosting, or a
  first cautious step toward a sequence/MTL-style architecture) for this
  single cell — still short of the 5,000–20,000+ scale Step 4's Target D
  describes for genuine deep-learning confidence
- main limitation: storage and download time at this scale become a
  project of their own (likely days of sustained downloading at
  ~0.5–0.75 GB/cycle-pair, compounded by the unresolved subsetting
  question from Step 8); still single-cell/single-hazard; this is the
  point where bulk/continuous date-range acquisition would likely be more
  efficient than hand-selecting individual cycles — a different
  acquisition design than anything built so far

## 11. Next-batch acquisition manifest

Generated by `scripts/design_phase_0_4_17_acquisition_candidates.py`
(new, research/design-only — never downloads anything) directly from the
real `ts_labels.csv`, excluding every already-acquired `event_group_key`.
Every candidate's GFS cycle/lead was computed with the exact same
verified rule from Phase 0.4.13 (latest 00/06/12/18Z cycle whose f003 and
f006 both land in the target slot, checked via the real `ist_slot_for()`
— never assumed from the date).

Output: `docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv` (42 rows = 21 event
groups × 2 leads) and `docs/PHASE_0_4_17_ACQUISITION_SUMMARY.json`.

| | |
|---|---|
| candidate event groups | 21 |
| candidate files | 42 |
| positive event groups | 14 |
| negative event groups | 7 |
| TRAIN event groups | 15 |
| HOLDOUT event groups | 6 (explicitly reserved — never to be trained on) |
| years covered | 2015–2024 |
| seasons covered | winter, pre-monsoon, monsoon, post-monsoon (all 4) |
| ambiguous/flagged cycles | 0 (no candidate failed to resolve a cycle) |

This intentionally **over-weights positives and winter** relative to the
full archive's natural climatology (14 of 21 candidates are positive, 6
are winter) — a deliberate correction for Step 7's finding that the
*current* 9-event dataset has zero winter positives, not a claim that
winter or positives are proportionally this common in reality.

## 12. Leakage audit of every candidate

Checked programmatically (and by a new focused test) for all 21
candidates:

- **init precedes valid time**: holds structurally — every selected lead
  is f003 or f006, always strictly after its cycle's init time.
- **selected lead matches the target**: a cycle/lead pair is only ever
  accepted by `find_cycle_and_leads()` when both f003 and f006
  independently recompute to the exact target `(ist_date, slot_id)` via
  `ist_slot_for()` — never assumed from the calendar date.
- **label observation window never becomes a predictor**: structurally
  true — candidate selection reads only `ts_labels.csv`; the eventual
  feature-extraction step (reusing `build_pilot()`, unchanged) never
  reads the label file into any predictor computation, the same leak-free
  code path already verified in Phase 0.4.12/0.4.16.
- **no event group duplicated across candidate definitions**: verified —
  21 unique `event_group_key` values, 0 overlap with the 9 already
  acquired.
- **TRAIN/HOLDOUT separation intact**: verified — 15 TRAIN, 6 HOLDOUT,
  explicitly reasoned and never mixed.

**Two non-blocking ambiguities flagged, not silently resolved:**

1. The four new TRAIN negative candidates (2017-01-01, 2018-01-01,
   2021-01-01, 2023-01-01) all fall on **January 1st** of different
   years — an artifact of the selection script's "earliest available
   negative that year" rule (Jan 1 is always NEGATIVE_CONFIRMED and
   sorts first). Not incorrect, but it repeats the same day-of-year
   across 4 of the 7 new TRAIN negatives, under-sampling other
   conditions during the year. **Recommend diversifying these dates
   before actual acquisition**, rather than taking this batch as final.
2. Three candidates — `2021-02-19` slot 2, `2021-02-19` slot 3, and
   `2021-02-20` slot 2 — fall within about 24–30 hours of each other and
   are plausibly the same multi-day winter synoptic system. They are
   genuinely different `event_group_key`s (different date/slot targets,
   both within TRAIN, so no cross-partition leakage), but including all
   three risks low effective independence between them for any later
   statistical use. Flagged for your judgment, not auto-resolved —
   consider replacing one with a more temporally separated winter
   positive from the remaining pool before downloading.

No candidate met any of the brief's RED stop-conditions (unparseable
file, ambiguous label, invented feature, undetermined precipitation
semantics, inconsistent event grouping, untraceable source, future-
information leakage, or a contract conflict).

## 13. What this does and does not prove

The current 16-row dataset (Phase 0.4.16) proves the extraction pipeline
works end-to-end on real files. It does **not** prove, and this
acquisition plan does not claim it will prove at any of the three
strategies' scale without actually training and evaluating a model
later: predictive skill, generalization, operational accuracy, 2–6h
performance, pan-India skill, MTL performance, transformer performance,
or production readiness. Strategy B is described as adequate for
*attempting* a preliminary statistical baseline — not as a guarantee that
baseline will show skill.

## 14. Tests

New file: `tests/test_phase_0_4_17_acquisition_design.py` — 10 tests
against the real label file (skips if absent, never fabricates):

- `test_no_candidate_duplicates_an_already_acquired_event_group`
- `test_every_candidate_event_group_is_unique`
- `test_candidate_batch_size_is_in_the_requested_range`
- `test_every_row_has_a_real_label_status`
- `test_f003_and_f006_share_the_same_event_group_key_per_candidate`
- `test_partition_assignment_matches_assign_partition`
- `test_holdout_candidates_are_never_mixed_into_train_reasoning`
- `test_cycle_lead_mapping_is_computed_not_assumed`
- `test_expected_filenames_follow_the_established_naming_convention`
- `test_no_production_code_imports_the_design_script`

One pre-existing test needed a one-line addition (same pattern as every
prior phase): `test_no_production_code_imports_research_builder_repo_wide`
now also excludes `scripts/design_phase_0_4_17_acquisition_candidates.py`.

Results:
- New file alone: **10/10 passed**.
- Combined with every historical-GFS-related test file: **95/95 passed**.
- Full suite (excluding the same 4 pre-existing environment-only
  failures as every prior phase): **271/271 passed** (up from the Phase
  0.4.16 baseline of 261; +10, exactly the new test file; nothing
  weakened).

## PHASE_0_4_17_ACQUISITION_DESIGN = YELLOW

**Why YELLOW, not GREEN**: every number in this plan was recomputed
directly from real files (the dataset, its manifest, the full label
archive, and the Stage B manifest's actual file sizes) — the GREEN bar's
"no unsupported assumptions" requirement holds for everything except one
specific external-archive fact: **whether THREDDS/OPeNDAP subsetting
would actually reduce download size is unverified** (Step 8) — this
sandbox's network cannot reach the relevant hosts to test it, and no
prior phase has either. That single unresolved external fact is exactly
what the brief's own YELLOW definition describes.

**Why not RED**: no scientific or data ambiguity was found that
undermines the design itself — the label archive, the sampling unit, the
leakage rules, and the storage math are all solid and independently
verified. The two flagged candidate-selection artifacts (Step 12) are
minor and fixable before any real acquisition, not blocking issues.

## Exact outputs from this phase

- `docs/PHASE_0_4_17_ACQUISITION_PLAN.md` (this file)
- `docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv` (42 rows / 21 event groups)
- `docs/PHASE_0_4_17_ACQUISITION_SUMMARY.json`
- `scripts/design_phase_0_4_17_acquisition_candidates.py` (new, design-only, never downloads)
- `tests/test_phase_0_4_17_acquisition_design.py` (new, 10 tests)
- `tests/test_vobl_historical_gfs_ts_join.py` (one-line addition to an
  existing repo-wide import-exclusion test)

No GFS files were downloaded. No model was trained. No production code,
deployment workflow, or pan-India heuristic layer was touched. No commit,
no push.
