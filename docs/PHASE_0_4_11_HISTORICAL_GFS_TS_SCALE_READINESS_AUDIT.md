# Phase 0.4.11 — Historical GFS → VOBL TS Scale-Up Readiness Audit

This is an audit only. No GFS files were downloaded this phase. No model
was trained. No production code was touched. Nothing was deployed,
committed, or pushed. Every claim below is traced to an actual file,
function, or previously-run pilot already in this repository — nothing is
assumed for convenience.

## Part 1 — Defining the target correctly

### Tracing the actual label contract

Two independent pieces of code define "did a thunderstorm occur," and they
agree with each other:

- **Live production**: `metar_ground_truth.py`. Its own docstring states
  the rule explicitly: aggregate every real METAR observation that falls
  inside a slot's IST window; the slot is `POSITIVE` (`TS_OBSERVED`) if
  **any** observation in that window shows TS; it is `NEGATIVE_CONFIRMED`
  (`NO_TS_OBSERVED`) only once the window has **closed** and at least one
  observation exists with none showing TS; otherwise it is left unlabeled
  (`NO_OBSERVATION_AVAILABLE`) — never scored as a negative while the
  window is still open.
- **Historical labels** (`processed/labels/ts_labels.csv`, built by
  `scripts/build_panindia_ts_labels.py`): reads `ts_label` directly from
  `data/bengaluru_6hr_training_dataset_v4.csv`, which already carries one
  row per `(date, slot 0-3)` with a pre-computed `ts_label` for that whole
  6-hour window. `build_panindia_ts_labels.py` does not re-aggregate
  sub-slot observations itself — it trusts the upstream dataset's
  per-slot `ts_label` column, which was built from real IMD/METAR data at
  the same 6-hour-slot granularity the live pipeline now formalizes.

Both sources describe the **same target shape**: one label per 6-hour IST
slot, decided by whether a thunderstorm was observed **anywhere inside
that window** — not at a specific minute, not at a specific hour offset
from any forecast.

### Answers

1. **Prediction target**: "will a thunderstorm be observed at VOBL at any
   point during IST slot S," where S is one of the four fixed 6-hour
   windows (`00:00–05:59`, `06:00–11:59`, `12:00–17:59`, `18:00–23:59`).
2. **POSITIVE window**: any real station/METAR observation inside the
   slot's IST window shows TS/VCTS/+TSRA-equivalent weather.
3. **NEGATIVE_CONFIRMED window**: the slot's IST window has fully closed,
   at least one real observation exists inside it, and none show TS.
4. **Forecast leads that can legitimately be trained**: any GFS forecast
   lead whose `valid_time` falls inside a slot that both (a) lies entirely
   in the historical past relative to "now" (for historical/offline
   training this is automatic) and (b) is a slot the label file actually
   has a `POSITIVE`/`NEGATIVE_CONFIRMED` row for (not `UNKNOWN`, not a
   still-open window — moot for historical data where every slot is
   closed, but relevant if this pipeline is ever reused near real time).
5. **Does +3h cleanly correspond to the 6-hour slot?** Yes — demonstrated
   directly in Phase 0.4.8/0.4.9/0.4.10/0.4.10B: every +3h/+6h/+9h lead
   examined so far mapped cleanly to exactly one slot with 2.5+ hour
   margin on both sides of the slot boundary (Section "Part 2" table
   below makes this exhaustive for one full cycle, not just the four
   leads already tested).
6. **Do +6h, +9h, +12h, etc. create independent samples, or multiple
   forecasts of the same slot?** **Both, depending on which pair.**
   Computed directly from `ist_slot_for()` (the exact function the join
   script uses, not reimplemented for this audit) for one representative
   00Z cycle:

   | Lead | Valid UTC | Valid IST | Slot |
   |---|---|---|---|
   | +3h | 03:00 | 08:30 | 1 |
   | +6h | 06:00 | 11:30 | **1** |
   | +9h | 09:00 | 14:30 | 2 |
   | +12h | 12:00 | 17:30 | **2** |
   | +15h | 15:00 | 20:30 | 3 |
   | +18h | 18:00 | 23:30 | **3** |
   | +21h | 21:00 | 02:30 (+1 day) | 0 |
   | +24h | 00:00 (+1 day) | 05:30 (+1 day) | **0** |

   Every pair of adjacent 3-hourly leads from the **same 00Z cycle** lands
   in the **same slot** (exactly what Phase 0.4.8 already found for
   +3h/+6h). This is a structural fact of the archive's 3-hourly step
   spacing against the label's 6-hour slot width, not specific to
   2020-07-15 or 2015-03-03.
7. **A or B?** The scientifically defensible answer, traced from both
   label-generation code paths, is **A: "will TS occur in the target
   6-hour slot."** Option B ("will TS occur exactly N hours after
   initialization") is not supportable by this label source at all — the
   label file has no sub-slot timestamp for historical rows (confirmed in
   Phase 0.4.7 Part 1 reasoning applied here: the upstream
   `bengaluru_6hr_training_dataset_v4.csv` is itself slot-level, not
   sub-slot). Training as if the target were B would silently assume a
   temporal precision the label does not carry.

## Part 2 — Forecast lead / target alignment table

Using the real `ist_slot_for()` function from
`scripts/build_vobl_historical_gfs_ts_join.py` (not reimplemented), for a
representative 00Z cycle:

| Lead | Valid UTC | Valid IST | Slot | Window state (historical) | Genuine future prediction? | Shares slot with | Overlap risk |
|---|---|---|---|---|---|---|---|
| +3h | 03:00 | 08:30 | 1 | closed | Yes (valid_time > init_time, slot closed by data-collection time) | +6h | Same-slot pair: using both as independent training rows would present the model with **two different predictor snapshots pointing at one identical label** |
| +6h | 06:00 | 11:30 | 1 | closed | Yes | +3h | (same pair as above) |
| +9h | 09:00 | 14:30 | 2 | closed | Yes | +12h | Same-slot pair |
| +12h | 12:00 | 17:30 | 2 | closed | Yes | +9h | Same-slot pair |
| +15h | 15:00 | 20:30 | 3 | closed | Yes | +18h | Same-slot pair |
| +18h | 18:00 | 23:30 | 3 | closed | Yes | +15h | Same-slot pair |
| +21h | 21:00 (2nd day) | 02:30 (2nd day) | 0 | closed | Yes | +24h | Same-slot pair |
| +24h | 00:00 (2nd day) | 05:30 (2nd day) | 0 | closed | Yes | +21h | Same-slot pair |

**Every native 3-hourly lead maps to a real, closed slot with a usable
label for historical data** (there is no "window still open" case once
the event is in the past — that concern only applies to a live/real-time
deployment, not historical training). The one structural issue, true for
every pair, not just the one examined in Phase 0.4.8: **the archive's
3-hour lead spacing is finer than the label's 6-hour slot width, so
consecutive lead pairs always alias to the same target.**

**Conclusion for Part 2**: not every 3-hour lead should become a separate
*independent* training target from the same cycle. The two leads per slot
carry genuinely different predictor information (a forecast made 3h vs 6h
before the slot's midpoint is meteorologically different) and are
legitimate to **include as separate rows**, but they must be treated as
**correlated observations of the same underlying event**, not i.i.d.
samples — this becomes the grouping-key requirement in Part 6.

## Part 3 — Historical GFS availability

All figures below are from this repository's own prior, already-verified
findings (Phase 0.4.2/0.4.3), re-cited here rather than re-guessed:

- **Archive start date**: 2015-01-15 00:00 UTC (confirmed by direct fetch
  of the GDEX d084001 landing page, Phase 0.4.2).
- **Archive end date currently available**: "2026-10-15 12:00 UTC" per the
  live GDEX page as fetched in Phase 0.4.2 — this is the page's own
  stated coverage at the time of that fetch, not independently
  re-verified this phase (no network access in this sandbox). **Marked
  UNVERIFIED for today's actual date** since GDEX's rolling coverage
  window advances continuously and was last checked on 2026-09-30.
- **Cycle frequency**: 4 cycles/day (00Z, 06Z, 12Z, 18Z) — confirmed from
  both the GDEX documentation (Phase 0.4.2) and the real downloaded file
  naming convention (`gfs.0p25.{YYYYMMDDHH}.f{FFF}.grib2`, HH ∈
  {00,06,12,18}).
- **Native forecast lead spacing**: 3-hourly (f000, f003, f006, f009, ...)
  — confirmed from both documentation and the real f003/f006/f009 files
  already downloaded and GRIB-verified in this project (Phase
  0.4.3A/0.4.5/0.4.10B).
- **Which leads are actually useful for the TS slot target**: per Part 2,
  **two leads per slot** (the pair whose valid times both fall in that
  slot) is the natural unit — e.g. f003+f006 for slot 1 of a 00Z cycle,
  f009+f012 for slot 2, etc. Using every 3-hourly lead out to +24h per
  cycle gives 8 leads covering 4 slots (today's remaining slots +
  tomorrow's slot 0), with exactly 2 (correlated) leads per slot.
- **Approximate GRIB2 file size**: directly measured from the four files
  already on disk: 2020-07-15 00Z files were 335.7 MB (f003) / 342.2 MB
  (f006); 2015-03-03 00Z files were 219.4 MB (f003) / 222.4 MB (f009).
  Global full-field files vary somewhat by date/content but are
  consistently in the **~220–345 MB per file** range in this project's own
  direct experience (the Phase 0.4.3 acquisition guide's independently
  fetched page claimed a documented range of ~487–532 MB for this naming
  pattern — both figures are reported since neither has been reconciled,
  per Phase 0.4.9's own prior finding).
- **Is downloading every 3-hour lead necessary?** **No.** Per Part 2, a
  given slot's label only benefits from the 1-2 leads whose valid time
  falls inside it; downloading all 8 leads of a cycle to cover 4 slots is
  already minimal relative to slot coverage, but a pilot that only needs
  **one slot's positive/negative evidence** (as Phase 0.4.9/0.4.10 did)
  needs only 1-2 files, not 8.
- **Can cycles be sampled intelligently instead of downloading
  everything?** **Yes, and this project has already demonstrated it**:
  Phase 0.4.9 selected one specific cycle/lead pair by first checking the
  real label file for a cell/date combination with a confirmed positive,
  rather than downloading dates at random and hoping for a positive by
  chance. This label-first selection strategy generalizes directly to a
  larger pilot (Part 4).

## Part 4 — Smallest meaningful pilot design

### Evidence this design is built from, not invented

- `processed/labels/ts_labels.csv` already contains the ground truth for
  **every** slot from 2015-01-02 through 2025-12-31 at VOBL (15,276 real
  rows: 584 POSITIVE / 14,692 NEGATIVE_CONFIRMED, confirmed in Phase
  0.4.7/0.4.9). The data to pick a representative, balanced sample
  **already exists and costs nothing to query** — no GFS download is
  needed to plan which dates to target, only to realize them.
- The two completed pilots (2020-07-15, 2015-03-03) already prove the
  join mechanics (feature extraction, slot mapping, label lookup, leakage
  checks) work correctly across two different years and two different
  lead-pair spacings. A scale-up pilot's job is **not** to re-prove
  mechanics — it is to test whether there is enough real signal to justify
  a larger acquisition.

### Proposed staged pilot

**Stage A — Correctness (already complete).** 2 cycles, 4 rows total
(2020-07-15 00Z f003/f006; 2015-03-03 00Z f003/f009). Already done in
Phase 0.4.8/0.4.9/0.4.10/0.4.10B. No further files needed for this stage.

**Stage B — Temporal diversity.** Select a small number of additional
cycles **spanning multiple distinct years and at least two seasons**
(VOBL's real TS positives cluster in pre-monsoon/monsoon months — visible
directly in the Phase 0.4.9 Section 1 table: 2015-02-28, 03-03, 03-06,
04-11 through 04-30 were all real positive dates), each contributing one
slot's worth of leads (2 files per cycle, matching Phase 0.4.9's own
selection method: query `ts_labels.csv` first, then pick the minimal
cycle/lead pair). A defensible Stage B size, derived from wanting at least
2-3 independent positive dates and 2-3 independent negative dates **per
season represented**, not from an arbitrary round number:

- 3 positive-anchored cycles (one per season with real positives: e.g. one
  Feb/Mar pre-monsoon, one Apr "peak" pre-monsoon date from the Section 1
  table, one monsoon-season date if `ts_labels.csv` confirms one exists)
- 3 negative-anchored cycles, chosen from **different years** than the
  positives to avoid the dataset being dominated by one year's weather
  pattern
- = 6 cycles x 2 files = **12 GRIB2 files**, ≈ 12 x 280 MB (using this
  project's own observed midpoint of 220-345 MB) ≈ **3.4 GB**

**Stage C — Signal-sufficiency test.** Only after Stage B, if its 12-ish
rows show the predictors (CAPE, K-Index, Totals-Totals, shear, PWAT)
plausibly differ between the real positive and negative rows (a direct,
cheap check — no model training required, just inspecting whether e.g.
CAPE is systematically higher on the positive rows), scale to enough
cycles for a **minimally statistically meaningful** positive/negative
count. Using the real base rate already measured (584/15276 ≈ 3.8%
positive-slot rate), getting even 20 real positive examples would require
sampling roughly 20 / 0.038 ≈ 526 slots if sampled at random — but since
Stage B's label-first strategy lets us **deliberately target known
positive dates** (584 of them are already enumerated in
`ts_labels.csv`), Stage C's actual requirement is just: pick ~20-30 of the
584 known positive dates (spread across years/seasons) plus a
similar-or-larger count of known negative dates, 1-2 files each. That is
**~40-60 cycles x 2 files ≈ 80-120 GRIB2 files ≈ 22-34 GB** — still a
small, targeted acquisition, not the full archive (which Phase 0.4.2
already established is not even fully enumerable without an account).

### Validation strategy for the pilot itself (not the eventual model)

Reuse exactly the checks already implemented and tested in
`scripts/build_vobl_historical_gfs_ts_join.py`: temporal-order check,
lead-consistency check, duplicate-row check, missing-feature check,
leakage classification, and the label join's `UNMATCHED` reporting (never
silently dropping a row). No new validation logic is required for Stage
B — the existing `--cycle`/`--leads`/`--ts-labels` interface already
supports running it once per selected cycle.

### What this design explicitly avoids

- **No fabricated negatives**: every cycle is chosen because
  `ts_labels.csv` already has a real `POSITIVE` or `NEGATIVE_CONFIRMED`
  row for it — never inferred from absence.
- **No future leakage**: every lead's `valid_time` is still read from the
  GRIB file's own metadata and checked `initialization_time < valid_time`,
  exactly as the existing script already does.

## Part 5 — Precipitation feature contract

### The four options, evaluated

**A. Only use accumulation fields whose startStep/endStep are explicitly
compatible.**
- Scientific meaning: correct, physically meaningful interval
  precipitation, but only when both ends of the interval share an
  accumulation origin.
- Leakage risk: none (both fields are forecast outputs from the same
  initialization).
- Implementation complexity: **already implemented** — exactly the
  `compute_interval_precipitation()` validity check in
  `historical_gfs_precipitation.py`, which Phase 0.4.10B's real run just
  exercised for the first time on a genuinely incompatible pair (f003
  `startStep=0` vs f009 `startStep=6`) and correctly rejected it rather
  than computing a wrong number.
- Robustness across leads: **not robust in generality** — Phase 0.4.10B's
  finding shows GDEX d084001's accumulation windows are not uniform past a
  certain lead (some reset every 6h rather than accumulating from step 0).
  This means a naive "always pair f003 with the next lead" strategy will
  sometimes silently produce no value (which is honest, not wrong, but
  reduces feature coverage unpredictably).
- Comparable with production: no — production's `qpe_mm` is read at f000
  (near-zero, Phase 0.4.4 Part 1), a structurally different quantity.

**B. Use GFS `prate` (precipitation rate) instead of accumulated `tp`.**
- Scientific meaning: `prate` (kg/m²/s) is an instantaneous rate at the
  valid time, not an accumulation — it does not require differencing two
  leads at all, sidestepping the whole startStep-compatibility problem.
- Leakage risk: none (still a pure forecast output at the valid time).
- Implementation complexity: **low** — `prate` was already confirmed
  present in the archive (Phase 0.4.3A's pilot report: "`prate` (precip
  rate) @ surface, units kg/m²/s — 6 messages total" alongside `tp`). No
  new derivation module would be needed beyond a direct GRIB field read,
  simpler than the existing interval-differencing code.
- Robustness across leads: **high** — every lead has its own `prate`
  value independently; no cross-lead compatibility check is ever needed.
- Comparable with production: no closer or further than `tp` — production
  does not currently use `prate` either, so this would be a new,
  explicitly-labeled research feature either way.

**C. Reconstruct interval precipitation from compatible accumulation
windows only (a refinement of A).**
- Same properties as A, with the added discipline of actively **selecting
  lead pairs known in advance to share an accumulation origin** (e.g.
  checking `startStep` by probing one file per cycle before committing to
  a lead pair), rather than discovering incompatibility after the fact.
- This adds a small amount of pre-flight-check complexity but avoids
  wasting a download on a lead pair that turns out to be
  non-differenceable.

**D. Exclude precipitation from the first scale-up pilot and add it
later.**
- Scientific meaning: defers the decision; the Stage B/C pilots can
  proceed on CAPE/CIN/PWAT/shear/K-Index/Totals-Totals/humidity/
  temperature alone, all of which have already been shown fully available
  and leak-free at every lead tested so far.
- Leakage risk: none (removes a feature rather than adding one).
- Implementation complexity: **zero** — simply omit the column.
- Robustness: trivially robust, since nothing is computed.
- Comparable with production: moot.

### Recommendation

**Adopt B (`prate`) as the primary research-only precipitation feature for
scale-up, with D as the immediate fallback for Stage B if `prate`
extraction surfaces any unexpected issue, and keep A/C's existing
`gfs_precip_3h_interval_mm` logic as a secondary, best-effort feature
wherever a compatible lead pair happens to be available** (its validity
check already correctly self-polices, so including it costs nothing when
it is simply absent for an incompatible pair — Phase 0.4.10B's own output
proves this fails safe, not silently).

This is not the "easier" choice by implementation effort alone (`prate` is
easier, but A/C are already built) — it is recommended because B is the
only option that is **robust across every lead independently**, which Part
2's finding (every lead maps to a real, usable slot) makes the structurally
important property for a pilot that will span many different lead values
across many different cycles, several of which Phase 0.4.10B has now shown
may not share compatible accumulation windows.

## Part 6 — Sample independence

### The leakage risk, demonstrated with the project's own data

Phase 0.4.10B's own completed pilots already contain the exact structural
risk this part asks about: **2015-03-03 00Z f003** (valid 08:30 IST, slot
1) and **2015-03-03 00Z f009** (valid 14:30 IST, slot 2) both describe
weather on the **same calendar day**, driven by the same larger-scale
synoptic pattern, even though they fall in different slots. More directly,
per Part 2's table, **2015-03-03 00Z +9h and a hypothetical 2015-03-03 06Z
+3h cycle would both have valid time 09:00 UTC** — literally the same
valid time from two different initializations, which would be strongly
correlated predictor sets (both forecasting the same atmospheric moment,
one 9 hours ahead, one 3 hours ahead) mapped to the identical label row.

### Recommended grouping key

```
event_group_key = (cell_id, ist_date, slot_id)
```

This groups every row — regardless of which cycle or which lead produced
it — by the single real-world target it is predicting. This is a direct
consequence of Part 1's finding that the label is fundamentally a
per-(cell, date, slot) quantity: any two rows sharing this key are
predicting the exact same observed outcome and must never be split across
train and validation/test.

**Decision: group them, never split them.** Allowing both members of a
same-slot lead pair into the dataset is fine (they carry different,
legitimately informative predictor snapshots), but a random row-level
split would let one member train and the other test on literally the same
label — the textbook train/test leakage this part warns about. All rows
sharing an `event_group_key` must be assigned to the same split.

## Part 7 — Train/validation/test split

| Strategy | Property | Risk for this dataset |
|---|---|---|
| Random split | Simple | **Rejected** — per Part 6, would split correlated same-slot rows across partitions, leaking the event |
| Chronological split (e.g. train on earlier dates, test on later) | Tests generalization to a future period | Reasonable, but a single cutoff can still separate two rows of the *same multi-day synoptic event* if the cutoff falls mid-event |
| Year-based split (e.g. hold out 1-2 full years) | Tests generalization across different years' weather | Strong for seasonal/year-level generalization; naturally respects `event_group_key` since no event spans a year boundary in this project's slot/day granularity |
| Event-based grouping (group by `event_group_key`, then split groups) | Directly prevents the Part 6 leakage | Strongest guarantee, but by itself says nothing about *distributional* generalization (could still randomly scatter groups across time) |

### Recommended primary evaluation

**Year-based split, with `event_group_key` grouping enforced within it as
a hard constraint** (belt-and-suspenders, not either/or): hold out one or
two full calendar years entirely (e.g. train on 2015-2023, validate/test
on 2024-2025, both already covered by `ts_labels.csv`'s 2015-2025 range),
and additionally verify programmatically that no `event_group_key` ever
appears in more than one split (trivially true under a year-based split
unless a slot's IST date straddles Dec 31/Jan 1 at slot 3, a one-line edge
case to check when this is implemented).

This directly answers the stated goal — "whether the model generalizes to
unseen weather periods, not whether it memorizes neighboring forecasts
from the same event" — because an unseen year contains no cycles the model
could have seen during training, closing both the Part 6 same-event
leakage and the broader concern of memorizing a specific year's synoptic
pattern.

## Part 8 — Feature audit

Current feature set, read directly from
`scripts/build_vobl_historical_gfs_ts_join.py`'s `WANTED_FIELDS` and
`FEATURE_LEAKAGE_CLASS` (no feature not actually implemented is listed):

| Feature | Classification | Note |
|---|---|---|
| CAPE | DIRECT_GFS, RESEARCH_ONLY | Read directly from GRIB (`cape`, surface). Present and non-null in every row tested across both pilots. |
| CIN | DIRECT_GFS, RESEARCH_ONLY | Read directly (`cin`, surface). Present in every row tested; Phase 0.4.2 flagged this as UNCONFIRMED before any file was downloaded — now CONFIRMED present by actual extraction in Phase 0.4.5/0.4.8/0.4.10B. |
| PWAT | DIRECT_GFS, RESEARCH_ONLY | Read directly (`pwat`, atmosphereSingleLayer). Present in every row tested. |
| U850 / V850 | DIRECT_GFS, RESEARCH_ONLY, PRODUCTION_COMPATIBLE (same physical field production already uses) | |
| U200 / V200 | DIRECT_GFS, RESEARCH_ONLY | Production's live shear uses 850/200 per `backend/pipeline.py::compute_wind_shear` signature — matches this project's own derivation, see next row. |
| Wind shear 850-200 hPa | DERIVED_FROM_GFS, PRODUCTION_COMPATIBLE | Computed with the exact same formula as `backend/pipeline.py::compute_wind_shear` (Euclidean difference of U/V at two levels) — same method, independently computed, not imported from production. |
| Temperature (850/700/500 hPa) | DIRECT_GFS, RESEARCH_ONLY | Present in every row tested. |
| Relative humidity (850/700 hPa) | DIRECT_GFS, RESEARCH_ONLY | Present in every row tested. |
| K-Index | DERIVED_FROM_GFS, RESEARCH_ONLY | Uses the same formula as `backend/pipeline.py::compute_k_index`, but with a **derived** (Magnus-Tetens) dewpoint rather than a native GRIB dewpoint field (none exists in this archive at isobaric levels, confirmed Phase 0.4.4). Explicitly tagged `dewpoint_source=derived_from_temperature_rh` in its own output — never presented as production-identical. |
| Totals-Totals | DERIVED_FROM_GFS, RESEARCH_ONLY | Same caveat as K-Index (derived dewpoint). |
| Geopotential height | **MISSING** | Not read by the current join script at all — `WANTED_FIELDS` has no geopotential-height entry. Phase 0.4.2/0.4.3B confirmed the *field exists* in the archive, but the join script never extracts it. |
| Convergence (850 hPa divergence field) | **MISSING / TEMPORALLY_INVALID for this architecture** | Production's `compute_convergence_grid()` (`backend/pipeline.py`) needs a **spatial grid** of neighboring U/V values to compute a horizontal-gradient divergence — it is not a point quantity. The current join script only regrids to a single VOBL-cell point value (`regrid_point_value()`), discarding the neighbor structure convergence needs. Not implemented, not a simple omission — would require a different extraction path. |
| Precipitation (`gfs_precip_3h_interval_mm`) | DERIVED_FROM_GFS, RESEARCH_ONLY, **QUESTIONABLE across arbitrary lead pairs** | Valid only when both ends of the interval share `startStep=0` — Phase 0.4.10B found this does not hold for every lead pair in this archive (see Part 5). Classified QUESTIONABLE for scale-up specifically because its *availability* (not its correctness, when available) is lead-pair-dependent and not yet characterized across the full lead range. |
| `prate` | **MISSING (not yet implemented)**, but **confirmed present in the archive** (Phase 0.4.3A) | Recommended in Part 5 as the primary precipitation feature for scale-up; not yet wired into the join script. |
| CTT proxy / CTT drop-rate | **TEMPORALLY_INVALID** (unchanged from Phase 0.4.4/0.4.5's finding) | Requires cross-cycle (not cross-lead) temporal evolution — production compares the *current* invocation's CTT grid against the *previous* invocation's, a fundamentally different data need than a single historical cycle can supply. Explicitly excluded from this join, as instructed by every phase since 0.4.5. |

No feature in the current join is classified `TEMPORALLY_INVALID` among
those actually implemented — that classification applies only to the two
features (convergence, CTT) that are correctly *excluded* rather than
incorrectly included.

## Part 9 — Computational cost

### Observed bottleneck, from actual runs

Every pilot run so far (Phase 0.4.8, 0.4.10, 0.4.10B) has fully parsed
**entire global 220-345 MB GRIB2 files** via
`enumerate_messages()` (`ecCodes` message-by-message enumeration of the
whole file) and then extracted only a 2°x2° bounding box around VOBL per
needed field via `extract_bbox_values()`. For a single cycle with 2 leads
and ~13 wanted fields, this means parsing the **entire global grid's
message index** twice (once per file) even though only ~13 small regional
arrays per file are ultimately used. This already works for a 2-row pilot
(seconds of runtime), but scaling to Stage B/C's 40-120 files would mean
repeating this full-file parse 40-120 times.

### Evaluating the five options

**A. Parse full global GRIBs (current approach).** Simple, already
implemented and tested, but wasteful at scale — most of each 220-345 MB
file (full global 1440x721 grid for dozens of unused fields/levels) is
read and discarded per file.

**B. Subset spatially before parsing.** Would require either a
server-side subsetting mechanism (e.g. GDEX's OPeNDAP/TDS access,
documented but not independently re-verified working in this sandbox per
Phase 0.4.3) or a local tool that reads only the needed byte ranges from
an already-downloaded file. Not implementable without network access this
phase; flagged as the right long-term answer for the **download** step
(fetching only a regional subset instead of the full 220-345 MB global
file) but out of scope to build this phase.

**C. Use a GRIB index (e.g. a `.idx` sidecar).** The real repository
already has evidence this is a known pattern in this project: the
2020-07-15 files on the user's machine have
`gfs.0p25.2020071500.f003.grib2.5b7b6.idx` sidecar files present
(confirmed by directory listing in Phase 0.4.10B's device inspection).
An index lets a reader jump directly to the byte offset of a wanted
message instead of sequentially enumerating every message in the file —
this would materially reduce **local parsing time** (not download size)
for repeated runs against the same files.

**D. Extract only required messages.** Complementary to C: once an index
(or a first full pass) identifies the byte offsets of the ~13 wanted
`(shortName, typeOfLevel, level)` combinations, a re-run only needs to
seek to those offsets rather than re-enumerating the whole file. This is
an optimization of the existing `enumerate_messages()`/`find_message()`
pair, not a new concept.

**E. Cache extracted VOBL-cell features.** The **highest-value, lowest-risk
optimization for this project's actual scale**: once a given
`(cycle, lead)` pair's VOBL-cell values have been extracted once, write
them to a small per-cycle cache file (e.g. a row in a CSV/JSON keyed by
`(cycle, lead)`) so a second run (e.g. re-running the pipeline after a
label-file fix, exactly what Phase 0.4.10B just did) never needs to
re-parse the 220-345 MB GRIB file at all. This directly reflects this
project's own recent history: Phase 0.4.10's `BLOCKED` run and Phase
0.4.10B's fixed run both re-parsed the same two 2015-03-03 files from
scratch, when a feature cache would have made the second run
near-instant.

### Recommendation

**For Stage B/C's scale (dozens, not thousands, of files): E (cache
extracted VOBL-cell features) is the minimum architecture change that
avoids repeatedly parsing 200+ MB global files**, since it eliminates
*re*-parsing (which is the actual observed repeated cost in this
project's own history) without requiring any new infrastructure (no
index file format, no subsetting service) beyond a simple keyed cache the
existing script can check before falling back to full GRIB parsing. C/D
(index-based direct-offset reads) are a reasonable follow-on if the
*first-time* per-file parse itself becomes the bottleneck at a much larger
scale than Stage B/C — not necessary yet. B (server-side spatial
subsetting) is the right fix for **download size**, not parse time, and is
gated on network access this sandbox does not have — a acquisition-time
decision, not a code-architecture one, and explicitly out of scope ("do
not implement it yet").

## Part 10 — GO / NO-GO gate

**YELLOW.**

The join itself is valid — both completed pilots (4 real rows: 1
positive, 3 confirmed negatives, 0 unmatched, 0 fabricated) passed every
temporal and leakage check, across two different years and two different
lead-pair spacings. That part is GREEN on its own merits.

But three scientific/data contracts must be resolved or explicitly adopted
**before** committing to a specific acquisition batch, each traced to a
concrete finding above, not a hypothetical concern:

1. **The grouping key** (`event_group_key = (cell_id, ist_date, slot_id)`,
   Part 6) must be adopted as a hard constraint in whatever code eventually
   builds train/val/test splits — this is a design decision to lock in now,
   before rows accumulate, not an optional nice-to-have.
2. **The precipitation feature contract** (Part 5: `prate` as primary,
   `gfs_precip_3h_interval_mm` as best-effort secondary) should be decided
   before Stage B, since it determines whether the join script needs a
   small code change (`prate` extraction) prior to the next acquisition.
3. **The year-based split rule** (Part 7) should be written down as the
   evaluation plan before any Stage B/C rows are collected, so date
   selection for Stage B can deliberately include enough distinct years to
   support at least one real held-out year later.

None of these require new data to resolve — they are design decisions
this repository's own evidence already supports making. Once made (a
short follow-up, not another large audit), the gate moves to GREEN and
Phase 0.4.12 can define the actual Stage B acquisition batch (the ~12-file,
~3.4 GB design in Part 4).

## Deliverables

```
docs/PHASE_0_4_11_HISTORICAL_GFS_TS_SCALE_READINESS_AUDIT.md   (this file)
```

No machine-readable planning artifact was created separately — the lead/
slot alignment table (Part 2) and the Stage A/B/C sizing (Part 4) are
fully captured in this document's tables, and duplicating them into a
second JSON file this phase would not add information a future phase
could not derive directly from this report plus a one-line re-run of
`ist_slot_for()` for any other cycle. No focused tests were added: this
phase discovered no new code contract requiring coverage — the one
code-relevant finding (precipitation accumulation-window
non-uniformity) is already covered by the existing, passing
`compute_interval_precipitation()` validity-check tests from Phase 0.4.5,
which correctly caught the exact case this audit discusses.

## Tests run this phase

No code was changed, so no new tests were added. Existing relevant tests
were re-run to confirm the evidence cited above still holds:

```
python3 -m pytest tests/test_vobl_historical_gfs_ts_join.py \
    tests/test_historical_gfs_thermodynamics.py \
    tests/test_historical_gfs_precipitation.py \
    tests/test_panindia_labels.py -q
    -> 72 passed

python3 -m pytest . -q --ignore=test_himawari.py --ignore=test_nomads.py \
    --ignore=test_segments.py --ignore=test_segments_v2.py
    -> 212 passed   (unchanged from Phase 0.4.10B's baseline)
```

No test was modified, skipped, or weakened. No production code was
touched. No GFS file was downloaded. No model was trained or tuned.
Nothing was deployed, committed, or pushed.

## Final status

- **GO/NO-GO: YELLOW** — join mechanics proven, three design decisions
  (grouping key, precipitation contract, split rule) must be formally
  adopted before the next acquisition batch is defined.
- **Recommended next step**: a short decision phase (not another large
  audit, not an acquisition) that formally adopts the Part 6 grouping
  key, the Part 5 precipitation recommendation, and the Part 7 split rule
  as the project's historical-dataset contract, then defines the exact
  Stage B acquisition list (specific dates/cycles/leads, drawn from
  `ts_labels.csv`'s already-known real positive/negative dates).
- **Exact amount of GFS data the next step (Stage B) would require**: **6
  cycles x 2 files = 12 GRIB2 files, ≈3.4 GB total**, selected by
  querying the already-existing `ts_labels.csv` for specific known
  positive and negative dates across multiple years and at least two
  seasons — not a bulk or speculative download.
