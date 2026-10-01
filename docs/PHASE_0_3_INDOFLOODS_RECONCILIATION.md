# Phase 0.3-A — INDOFLOODS 8,342 vs 4,548 Reconciliation

## 1. The two numbers and where each comes from

- **4,548** — the row count of `data/floodevents_indofloods.csv` in this repo.
  Verified directly this pass: `tail -n +2 data/floodevents_indofloods.csv | wc -l` = **4,548** data
  rows (4,549 lines including header). Breakdown by the file's own `Flood Type` column:
  **2,919 "Flood" + 1,629 "Severe Flood" = 4,548** (one header-row artifact excluded).
- **8,342** — first appears in `docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md` line 164, attributed to
  "the paper, independently fetched this pass," stated as 5,525 regular + 2,817 severe flood
  events across 214 stations, 1959-2020. It was carried forward verbatim into
  `docs/PHASE_0_2_RECOMMENDATIONS.md` and `PHASE02_FINAL_REPORT.md` without independent
  re-verification in those later docs.

## 2. Chain of custody for the repo's file

`docs/INDOFLOODS_DATA_INVENTORY.md` (2026-09-30) documents the actual provenance: the repo's
INDOFLOODS files were copied from `Downloads/14584655.zip` (2,185,271 bytes) on the user's own
machine, inspected via the device bridge. `floodevents_indofloods.csv` in that archive was
**content-identical** (checksum/byte comparison, differing only in CRLF vs LF line endings) to the
copy already in `data/`. No filtering, truncation, or subsetting step exists anywhere between the
archive and `data/floodevents_indofloods.csv`.

`scripts/map_indofloods_to_grid.py` (the only script that reads this file downstream) was
re-inspected this phase: it performs gauge-to-cell spatial mapping only. It contains no row
filter, no date-range filter, and no severity filter on `floodevents_indofloods.csv` — it passes
every row through to `processed/indofloods/indofloods_grid_events.csv`, which itself has exactly
4,548 data rows (independently re-counted this phase, matching `docs/PHASE_0_1_AUDIT_DISCREPANCY_RESOLUTION.md`'s
earlier count). **4,548 is not a filtered subset produced by this project's own code — it is the
complete, unmodified content of the archive file the project possesses.**

## 3. Identifying the archive itself

`14584655.zip` is not an arbitrary filename — a web search this phase found it is the literal
Zenodo record id: **https://zenodo.org/records/14584655, "Published January 24, 2025 | Version
1.0"** (concept DOI 10.5281/zenodo.14584654, the same DOI `docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md`
cites for the paper). So the file in `data/` is, as far as this phase can confirm, the actual
Version 1.0 Zenodo deposit, not an older or unofficial copy.

## 4. What could not be verified

This phase attempted to re-fetch the BAMS paper and/or its abstract (journals.ametsoc.org,
researchgate.net) via WebFetch/WebSearch to directly confirm the "8,342 events / 5,525 + 2,817"
figures in the paper's own text. Both attempts were **blocked by the proxy with HTTP 429
(rate-limited)** before the full text could be read; the paper's exact wording on total event
count was not independently re-confirmed this phase, and no PDF of the paper itself is present in
this repo (`data/variables_description_indofloods.pdf` is the *database codebook*, not the paper —
confirmed by `pdftotext` extraction this phase, which contains no total-event-count statement
anywhere in its text).

One circumstantial data point does *not* contradict the two figures being the same underlying
database: the repo file's severe/regular split (1,629/4,548 = 35.8% severe) is reasonably close to
the paper-cited split (2,817/8,342 = 33.8% severe) — consistent with, though not proof of, the same
event-classification method applied to two different coverage windows or releases.

## 5. Verdict

**D — Insufficient evidence to conclude A, B, or C with confidence.**

What is established with high confidence:
- `data/floodevents_indofloods.csv`'s 4,548 rows are the complete, byte-identical content of the
  Zenodo record 14584655 (v1.0) archive the project actually downloaded — not a subset created by
  any script in this repo.
- No in-repo filtering explains the gap; the discrepancy, if real, exists between the published
  paper's stated total and the publicly downloadable Zenodo archive's actual row count.

What remains unverified because of this phase's blockers (proxy rate-limiting on the paper source):
- Whether the BAMS paper's "8,342" figure is for a different data release/version than
  14584655/v1.0, a different counting methodology (e.g., per-gauge annual counts vs. the merged
  per-gauge event table), or whether the "8,342" figure recorded in
  `docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md` was itself transcribed/estimated rather than read
  directly off the paper (that doc's own wording, "independently fetched this pass," does not cite
  a direct quote or page reference).

## 6. Recommendation for Phase 0.4

1. Fetch the BAMS paper PDF directly (journals.ametsoc.org/view/journals/bams/106/2/BAMS-D-24-0008.1.xml
   or the open-access AOP PDF) once rate-limiting clears, and quote its exact total-event-count
   statement with a page/table reference.
2. Check the Zenodo record's version history (it is a versioned DOI; v1.0 was the only version
   found this phase, but a later version may have been published since January 2025) for a changed
   file size or row count.
3. Until (1)-(2) are done, treat `data/floodevents_indofloods.csv`'s 4,548 events as the confirmed,
   checksum-verified, complete content of the dataset version this project actually holds, and do
   not scale any downstream count (the 620 `ff_pu` positives, the 214 mapped gauges, etc.) by a
   8,342/4,548 ratio — that would be fabricating a correction with no verified basis.

No existing file in `data/`, `processed/indofloods/`, or `processed/ff_pu/` was modified in this
phase, per instruction.
