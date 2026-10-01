# Phase 0.4.23 — TS GFS Acquisition Plan (Design Only, No Download)

Audit + design phase. No GFS files were downloaded to produce this document. All counts below come from directly parsing `ts_labels.csv` (16,267 rows: 584 POSITIVE, 14,692 NEGATIVE_CONFIRMED, 991 UNKNOWN) and the existing 40-row `phase_0_4_20_train.csv`/`phase_0_4_20_holdout.csv`.

## 1. What the full TS label archive actually supports

- 584 positive label-rows reduce to **457 unique positive calendar dates** (some dates have more than one positive six-hour slot — e.g. a storm spanning two adjacent slots).
- Positive-slot distribution across the day: `1201-1800` (250), `1801-2400` (187), `0001-0600` (113), `0601-1200` (34) — afternoon/evening dominant, consistent with VOBL's known diurnal thunderstorm pattern, and not a reason to doubt the label quality.
- Year distribution of positives (2015-2025, 11 years): 2015:59, 2016:36, 2017:90, 2018:63, 2019:2 (anomalously low — flagged, not corrected, this phase), 2020:51, 2021:87, 2022:89, 2023:52, 2024:20, 2025:35.
- Month distribution of positives: heavily pre-monsoon/monsoon (Apr:76, May:162, Jun:70, Jul:32, Aug:42, Sep:67, Oct:84), negligible Jan/Feb/Nov/Dec (5/24/20/2) — again consistent with known climatology, not an artifact.
- 3,819 unique negative calendar dates exist (14,692 negative slots) — **negatives are not the scarce resource**, acquisition effort should not be split evenly between positive and negative candidate search.

## 2. Auditing the Phase 0.4.22 working target (300 / 150 / 150 / 240 / 60)

Checked against each requested axis:

- **Positive prevalence**: 457 independent candidate positive dates exist against a target of 150 — comfortably achievable (33% of the available positive-date pool), leaving room to also select for seasonal/year diversity rather than taking the first 150 found.
- **Event-group definition**: unchanged from Phase 0.4.20/0.4.22 — `event_group_key = cell_id|ist_date|slot_id`. A "positive event group" here means one (date, 6h-slot) pair with a POSITIVE label, matched to the nearest usable GFS cycle.
- **f003/f006 correlation**: both leads come from the same GFS initialization and describe the same synoptic state 3 hours apart — they are correlated by construction and are correctly **not** counted as two independent samples; the existing design already treats a cycle's f003+f006 pair as one event group, and this plan does not change that.
- **Seasonal distribution**: the current 20-group set has 0 events in Aug/Sep/Dec despite Aug/Sep being real contributors to the archive's positive pool (42 and 67 positive dates respectively). **300 is not at risk from label scarcity here** — the gap is acquisition-selection, not data availability.
- **Yearly distribution**: current set already skews toward 2021/2024 (3+4 of 20 groups = 35%). The archive supports much better spread (every year 2015-2025 has positives except the anomalous 2019 dip), so a 300-group target has ample room to flatten this.
- **Independence of weather episodes**: the archive's 457 unique dates are not all independent — days within a few days of each other during an active monsoon spell are plausibly the same synoptic system. This plan does not attempt automated episode-clustering (out of scope — would need synoptic-scale reanalysis not available here); instead it recommends a **minimum-gap selection rule** (Part 2 below) applied at acquisition-candidate-design time, same mechanism already used informally by the Phase 0.4.17 candidate designer.
- **Train/holdout separation**: addressed directly in the TS Holdout Design doc (`PHASE_0_4_23_DATA_REMEDIATION_PLAN.md` Part 2) — 240/60 (80/20) is kept, but *how* the 60 holdout groups are chosen is revised.
- **GFS storage requirements**: 300 event groups × 2 files × ~404MB average (measured directly from the 40 already-acquired files, Phase 0.4.22) ≈ **242 GB**. This is within reach of the already-demonstrated 16.1GB/40-file tooling, scaled ~7.5x — no indication in `scripts/acquire_phase_0_4_19_batch.py`'s manifest-driven design that this is structurally harder, only slower and larger.
- **Intended XGBoost baseline**: 150 positive event groups against the existing 17-feature contract gives EPV≈8.8 — just under the EPV≥10 floor used in Phase 0.4.22 Part 7, but within the same order of magnitude and a legitimate "defensible baseline, lower-confidence" tier rather than "exploratory only."
- **Eventual MTL experiment**: 300 total event groups for TS alone is still well short of the "several hundred to low-thousands per head" MTL floor named in Phase 0.4.22 Part 7 §8. 300 should be understood as the step that unlocks an XGBoost baseline, not as sufficient for MTL on its own.

**Verdict: 300 / 150 / 150 / 240 / 60 is confirmed as the next acquisition target, with one addition — not a change to the four numbers themselves, but an explicit selection procedure (below) so that reaching 300 does not just mean "the next 300 dates on the list."**

## 3. Candidate selection procedure (for the next acquisition-design phase, not run here)

1. Start from the 457 unique positive dates and 3,819 unique negative dates in `ts_labels.csv`.
2. Exclude the 20 dates already acquired (Phase 0.4.17/0.4.19/0.4.20 manifest).
3. Apply a **minimum-gap rule**: no two selected candidate dates (of the same label class) within 3 calendar days of each other, unless the gap itself is the point of a deliberately-included "adjacent-day pair" earmarked for holdout-diversity testing (none should be, per Part 2 of the main remediation plan). This directly targets the Phase 0.4.21 concentration problem at the design stage instead of discovering it after the fact.
4. Stratify positive selection across year (target: no single year contributes more than ~15% of the 150, i.e. ≤22-23 positive groups/year) and month (prioritize Aug/Sep/Dec, currently absent from the acquired set, before adding more Apr/May/Jun which are already well represented).
5. Stratify negative selection similarly, but since negatives are abundant (3,819 candidates for 150 slots), prioritize negatives that are temporally close to selected positives (same season/year) to give the model contrastive examples rather than negatives drawn only from quiet off-season periods, which would make the classification task artificially easy.
6. Partition 240/60 only after the above selection, using the revised holdout procedure in the main remediation plan — never partition by simply taking the last N selected.

## 4. What this document does not do

No manifest file was generated or written this phase (that is explicitly the next phase's acquisition-design step, analogous to `scripts/design_phase_0_4_17_acquisition_candidates.py`). No GFS files were downloaded. No existing manifest (`docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv`) was modified.
