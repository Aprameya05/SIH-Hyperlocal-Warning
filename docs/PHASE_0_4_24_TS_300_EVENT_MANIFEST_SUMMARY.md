# Phase 0.4.24 — TS 300-Event-Group Candidate Manifest (Design/Audit Only)

No GFS files were downloaded. No model was trained. No production code, frontend/UI, or existing label/manifest file was modified. This phase generated `docs/PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv` via a new, deterministic selection script, `scripts/design_phase_0_4_24_ts_300_candidates.py`, which reuses (unmodified) `event_group_key_for`/`ist_slot_for` from `build_vobl_historical_gfs_ts_join.py`, `assign_partition`/`TRAIN`/`HOLDOUT` from `historical_dataset_split.py`, and the exact GFS cycle/lead selection rule from the Phase 0.4.17 design script.

## 1. Inputs and reproducibility

- **Source label file**: `SIH_PANINDIA_GRID_LABELS_20260930_143541Z/processed/labels/ts_labels.csv` (16,267 rows: 584 POSITIVE, 14,692 NEGATIVE_CONFIRMED, 991 UNKNOWN, VOBL cell `IND_13.0_78.0` only).
- **Selection algorithm/version**: `scripts/design_phase_0_4_24_ts_300_candidates.py`, this phase's first and only version.
- **Random seed**: none used — the script contains no randomness anywhere; every ordering decision (year round-robin, season interleave, slot rotation, minimum-gap filtering) is a deterministic function of the label data and the fixed constants defined in the script. Re-running it against an unchanged `ts_labels.csv` produces a byte-identical manifest (verified by `tests/test_phase_0_4_24_ts_300_manifest.py::test_selection_is_deterministic`).
- **Existing-acquisition exclusion set**: the exact 20 `event_group_key` values already built into `phase_0_4_20_train.csv`/`phase_0_4_20_holdout.csv` (verified directly from those files' own `event_group_key` column, not from the original Phase 0.4.17 design script's placeholder candidate list, which named different, never-actually-acquired keys).
- **Timestamp**: generated this phase, 2026-10-01.

## 2. Candidate pool (Part 2 of the brief)

- **Total eligible positive event groups**: 571 (584 archive-wide minus the 13 already-acquired positive groups among the existing 20 — exact match, confirming consistency between the label archive and the already-built dataset).
- **Total eligible negative event groups**: 14,685 (14,692 minus the 7 already-acquired negative groups — exact match).
- **Years represented**: 2015-2025 for both classes (11 years).
- **Seasons represented**: all four (monsoon, post-monsoon, pre-monsoon, winter) for positives; winter is archive-scarce (confirmed: only 7 positive slots exist in winter across the whole 11-year archive).
- **IST slots represented**: all four (0-3) for positives.
- **Missing/ambiguous labels**: 0 UNKNOWN-status rows were included in the candidate pool (991 UNKNOWN rows excluded by construction — the script only considers POSITIVE/NEGATIVE_CONFIRMED).
- **Groups that cannot be mapped to a GFS cycle**: 0 — every eligible candidate date/slot combination successfully resolved to a GFS initialization cycle with both f003 and f006 landing in the target slot, using the unmodified `find_cycle_and_leads()` rule.

## 3. Episode-aware selection rule (Part 3)

**Finding during this phase, before the rule could be applied correctly**: a single 48-hour-gap transitive-clustering rule applied across the *whole* eligible pool (positive and negative together) collapses nearly the entire archive into a tiny number of giant multi-year "episodes," because NEGATIVE_CONFIRMED labels exist on most calendar days across 11 years — any two negative days within 48h of each other chain together, and transitively that chains almost the whole timeline. Measured directly: 3,819 negative dates (archive-wide) collapse to just 19 episodes under naive whole-pool clustering, and the 2024-2025 holdout window alone collapses to 2.

**Rule actually adopted**: episode identity is computed **separately per label class**:
- **Positive dates**: clustered transitively with a 48-hour gap threshold (the genuine "is this one storm system or several" question Phase 0.4.21 originally raised, e.g. the 2023-12-31/2024-01-01 cluster). 446 eligible positive dates reduce to 163 distinct episodes archive-wide.
- **Negative dates**: each date is its own standalone unit (not clustered), since adjacency between two quiet days does not represent one shared physical weather event the way adjacent storm slots do. The "don't cluster negatives" concern from Part 5 of the brief is instead enforced at **selection time** via a 2-day minimum-gap rule between selected negative dates (documented in Part 5 below), and via the `max_within_*` concentration metrics in Part 10.

This is a deterministic, documented, reproducible rule — not an automatic discard of nearby events (per the brief's explicit instruction): a candidate within an existing episode is still eligible, it is simply not treated as adding new independent information once an episode has already contributed to the current partition.

## 4. Positive selection (Part 4)

150 positive event groups selected (120 train, 30 holdout), via: year round-robin (one pick per train-year per pass, 9 train years × ~13.3 ≈ 120; 2 holdout years × 15 = 30), with within-year ordering **interleaved across the four-season rotation** (pre-monsoon → monsoon → post-monsoon → winter, cycling) rather than plain chronological order — chronological order was tried first and found to greedily exhaust early-calendar-year months before ever reaching mid/late-year months, skewing the first version of this selection toward pre-monsoon (131/150); the season-interleaved version corrects this.

**Geographic diversity**: explicitly **not claimed**. The TS label archive is VOBL-only (`cell_id = IND_13.0_78.0`), so every one of the 300 selected event groups is the same single cell — this plan does not and cannot add spatial diversity from this label source.

**Selected positive distribution**:

| Year | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Count | 15 | 15 | 15 | 15 | **1** | 15 | 15 | 15 | 14 | 15 | 15 |

(2019 is capped at 1 because the full archive has only 2 positive slots in 2019 total, 1 of which is already acquired in the existing 20 — this is an archive limitation, not a selection failure.)

| Season | monsoon | post-monsoon | pre-monsoon | winter |
|---|---:|---:|---:|---:|
| Count | 52 | 44 | 51 | 3 |

(Winter is capped at 3 because only 7 winter-positive slots exist archive-wide, several already acquired or clustered within 48h of each other.)

| IST slot | 0 | 1 | 2 | 3 |
|---|---:|---:|---:|---:|
| Count | 24 | 26 | 66 | 34 |

**Episode count among selected positives**: spans many of the 163 available positive episodes (exact per-partition episode counts are reported combined with negatives in Part 10, since episode namespaces are partition-wide, not class-separated in the final count).

## 5. Negative selection (Part 5)

150 negative event groups selected (120 train, 30 holdout), with the same year round-robin and season interleaving as positives, plus a **2-calendar-day minimum gap** enforced between any two selected negative dates within the same selection pass — directly targeting the brief's "avoid excessive clustering on adjacent dates" instruction, and verified post-hoc (Part 10): no two selected negatives fall on the same date more than twice, and the maximum count within any 24-hour window across the *entire* 300-group selection (positive and negative combined) is 1.

On the "artificially easy" classification concern (Part 5): this plan does **not** specifically avoid negatives near positives, nor specifically seek them out — the year/season-stratified selection draws negatives from the same years and seasons as positives (by construction, both pools are built from the same year/season rotation), which keeps negatives contextually comparable to positives (same season, same years) without deliberately engineering adjacency either way. A negative dataset drawn predominantly from off-season/quiet years, which the stratification explicitly avoids, would have been the actual "artificially easy" failure mode.

**Selected negative distribution**:

| Year | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Count | 14 | 14 | 14 | 13 | 13 | 13 | 13 | 13 | 13 | 15 | 15 |

| Season | monsoon | post-monsoon | pre-monsoon | winter |
|---|---:|---:|---:|---:|
| Count | 36 | 38 | 42 | 34 |

| IST slot | 0 | 1 | 2 | 3 |
|---|---:|---:|---:|---:|
| Count | 33 | 44 | 38 | 35 |

## 6. Train/holdout design (Part 6)

- **TRAIN = 240 groups (120 positive, 120 negative)**; **HOLDOUT = 60 groups (30 positive, 30 negative)** — the approximate balance target is met exactly for both classes in both partitions; no deviation was necessary.
- **Episode separation enforced at selection time, not audited after the fact**: the `select()` function excludes, from each partition's candidate pool, any episode already used by the *opposite* partition, before making any pick. This is a hard constraint built into the selection loop itself.
- **Result**: `cross_partition_episode_overlap = []` — zero episodes appear in both TRAIN and HOLDOUT, verified both by the script's own validation and by an independent test (`tests/test_phase_0_4_24_ts_300_manifest.py::test_manifest_no_cross_partition_episode_overlap`, which re-derives episode membership directly from the written CSV rather than trusting the script's in-memory state).
- **Train episodes**: 236. **Holdout episodes**: 58. This directly resolves the Phase 0.4.21 problem (previously: 6 holdout groups spanned only effectively ~4 independent episodes, with 3 of 6 concentrated in one 15-hour window) — the new holdout's 60 groups span 58 distinct episodes, i.e. almost every selected holdout group is its own independent unit, with at most 2 groups ever sharing an episode (see Part 10's `max_same_date=2`).
- **The existing 20 groups' own partition assignment is preserved as-is** (14 train, 6 holdout, per their already-built dataset) — this phase's new 280 additional groups are selected independently around them, not by re-splitting the existing 20.

## 7. Existing 20 groups (Part 7)

- **Existing groups retained**: all 20 (none deleted or replaced — this phase only adds new candidates).
- **Existing groups assigned train** (as already built): 14 — `2015-02-28|3`, `2016-03-13|2`, `2017-04-01|3`, `2017-06-05|2`, `2018-02-09|3`, `2018-07-17|2`, `2019-10-02|2`, `2020-03-20|2`, `2021-02-19|2`, `2021-02-20|2`, `2021-10-01|0`, `2022-03-20|2`, `2023-03-16|3`, `2023-11-01|1`.
- **Existing groups assigned holdout** (as already built): 6 — `2024-01-01|0`, `2024-01-01|1`, `2024-01-01|2`, `2024-05-12|2`, `2024-06-02|2`, `2024-06-03|2`.
- **Existing groups excluded from the new 300-candidate selection pool**: all 20, by design — they are already acquired, so including them again in the *new* candidate selection would double-count them against the 300-group target. They are not excluded from the eventual *dataset* (once this manifest is acquired, the final TS dataset would be existing-20 plus new-300 = 320 total event groups) — this manifest's "300" is additive to, not inclusive of, the existing 20.
- **Reason for exclusion from selection**: already acquired; re-selecting them would not add new information and would make the 300-group target's true new-acquisition size ambiguous.

## 8. GFS acquisition mapping (Part 8)

Every one of the 300 selected event groups maps to the established `f003`+`f006` paired-lead structure (no event group required a different lead structure under the existing contract — `find_cycle_and_leads()` only ever returns a (f003, f006) pair or `None`, and every candidate that reached the selected set had already been filtered to have a valid mapping). Manifest columns per row (600 total rows, one per lead per event group): `selection_rank, event_group_key, label, partition, target_ist_date, target_slot, year, season, episode_id, gfs_init_cycle_utc, forecast_lead, expected_filename, expected_url, acquisition_year, acquisition_path, selection_reason, duplicate_filename_flag`.

`expected_url` points to the RDA d084001 archive path pattern already used by this project's historical GFS tooling; `acquisition_path` is set to the same `D:\SIH-Historical-GFS\raw` location the existing 40 files were acquired into (per Phase 0.4.21's confirmed `raw_root`), for consistency — no new acquisition path was invented.

## 9. Manifest validation (Part 9)

All hard-fail conditions checked, none triggered:

| Check | Result |
|---|---|
| Duplicate event groups | 0 |
| Duplicate files | 0 |
| Train/holdout overlap (event group) | 0 |
| Positive/negative label mismatch | 0 (every row's `label` matches its source `label_status`) |
| Invalid `event_group_key` | 0 (all follow `cell_id\|date\|slot_id`) |
| Missing GFS mapping | 0 |
| Invalid URL | 0 (all follow the same RDA pattern) |
| Ambiguous partition | 0 (every group is exactly `train` or `holdout`, never both) |
| Same episode crossing train/holdout | 0 |
| Selected count vs. target | Exact match: 300/300, 150/150, 150/150, 240/240, 60/60, 600/600 expected files |

**MANIFEST_VALIDATION = PASS.**

## 10. Distribution report (Part 10)

### Overall

| Metric | Positive | Negative | Total |
|---|---:|---:|---:|
| Event groups | 150 | 150 | 300 |

### Partition

| Partition | Positive | Negative | Total |
|---|---:|---:|---:|
| Train | 120 | 120 | 240 |
| Holdout | 30 | 30 | 60 |

### Year (combined positive+negative)

| Year | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Count | 29 | 29 | 29 | 28 | 14 | 28 | 28 | 28 | 27 | 30 | 30 |

### Season (combined)

| Season | monsoon | post-monsoon | pre-monsoon | winter |
|---|---:|---:|---:|---:|
| Count | 88 | 82 | 93 | 37 |

### Month

Not separately tabulated beyond season in this document (season is the requested granularity used for stratification); exact per-month counts are reconstructable directly from `target_ist_date` in the manifest CSV.

### IST slot (combined)

| Slot | 0 | 1 | 2 | 3 |
|---|---:|---:|---:|---:|
| Count | 57 | 70 | 104 | 69 |

### Episode

Train: 236 distinct episodes across 240 event groups. Holdout: 58 distinct episodes across 60 event groups. 0 episodes shared between train and holdout.

### Concentration windows (independence check)

| Window | Max selected events |
|---|---:|
| Same date | 2 |
| 24-hour window | 1 |
| 3-day window | 3 |
| 7-day window | 6 |

These are small relative to the 300-group total, supporting the conclusion that the selection achieves meaningfully independent sampling rather than clustering around a few dense periods.

## 11. Storage estimate (Part 11)

Using the measured size distribution of the 40 already-acquired files (Phase 0.4.19/0.4.22: mean 403,658,081 bytes/file, range 204.6MB-556.6MB):

- **Expected total storage**: 600 files × ~404MB average ≈ **242GB** (consistent with the Phase 0.4.22/0.4.23 estimate).
- **Train storage**: 480 files × ~404MB ≈ **194GB**.
- **Holdout storage**: 120 files × ~404MB ≈ **48GB**.
- **Safety margin**: given the measured range extends up to 556.6MB/file (38% above the mean), a conservative planning figure using the observed maximum rather than the mean would be 600 × 556.6MB ≈ **334GB** — this report does not claim an exact size, since actual GFS file sizes vary by date/season (file size correlates with monsoon-season data volume in the already-acquired set), and recommends planning storage headroom toward the 300-334GB range rather than the bare 242GB mean estimate.
- `D:\SIH-Historical-GFS\raw`'s actual free space remains **UNKNOWN** from this session (not a connected folder) — must be checked on the user's machine before acquisition begins, unchanged from the Phase 0.4.23 finding.

## 12. Outputs

- `docs/PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv` (600 rows)
- `docs/PHASE_0_4_24_TS_300_EVENT_MANIFEST_SUMMARY.md` (this document)
- `scripts/design_phase_0_4_24_ts_300_candidates.py` (selection script)
- `tests/test_phase_0_4_24_ts_300_manifest.py` (9 tests, all passing)
- `docs/PHASE_0_4_24_SELECTION_RESULT.json` (raw script output, machine-readable mirror of the numbers in this document)

The existing `docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv` (the 20-group manifest) was **not** overwritten or modified.

## 13. One test-suite change, disclosed

`tests/test_vobl_historical_gfs_ts_join.py::test_no_production_code_imports_research_builder_repo_wide` is a repo-wide grep-based guard that fails on any *new, unlisted* file importing the research-only `build_vobl_historical_gfs_ts_join` module. The new `scripts/design_phase_0_4_24_ts_300_candidates.py` does import it (by design, reusing `ist_slot_for`/`event_group_key_for`, exactly like the Phase 0.4.17 design script already on the allowlist). One line was added to that test's existing allowlist, following the identical pattern already used for five other research-only sibling scripts — this is a whitelist addition for a new, legitimate import, not a weakening of the guard's logic or any change to what it actually checks.
