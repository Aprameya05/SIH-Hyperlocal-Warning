# Phase 5 — Pan-India Supervised Dataset — Construction Report — 2026-09-30

No MTL training, no A100 run, no frontend change, no production-inference change, no deployment, no commit, no push. This report documents what was actually built, from what real data, and — per the phase's own stated purpose — whether it is ready to support MTL training before any GPU time is spent on it.

## 1. Dataset Objective

Build the maximum real, non-manufactured supervised dataset this repository's actual data can support for TS/CB/FF multi-task learning, on the canonical 992-cell grid, preserving every label-status semantic Phase 4 established (`UNKNOWN` never becomes negative, the FF rainfall proxy never becomes an observed label).

## 2. Canonical Grid

Unchanged from Phase 4.5: 992 cells, 1.0°, `docs/CANONICAL_GRID.md`, now drift-guarded in `backend/pipeline.py`. This dataset's `panindia_cb_partition` touches exactly these 992 cells (verified, `tests/test_panindia_dataset.py` check 18).

## 3. Source Data — and the Central Finding of This Phase

Every candidate source file in the repository was inspected directly (not assumed) before deciding what could be joined:

| Source | Real spatial coverage | Real temporal coverage |
|---|---|---|
| `data/era5_6hrly_bengaluru_2015_2025.csv` | **Single point (Bengaluru)** — confirmed no lat/lon columns exist in the file | 2015-01-01 to 2025-12-31, 6-hourly, 16,072 rows |
| `data/bengaluru_6hr_training_dataset_v4.csv` | Single point (Bengaluru) | 15,276 rows, matches production TS training data |
| `data/pan_india_grid.json` | **992 cells, genuinely pan-India** | **A single live snapshot, regenerated in place every 6 hours — not a time series.** No archived history of past pan-India predictor grids exists anywhere in this repository. |
| `imd_rain/rain/*.grd` | 992/992 canonical cells have IMD land coverage | 2015-2025, daily, real IMD 0.25° gridded rainfall (file sizes verified against expected byte counts in Phase 4) |

**This is the central finding of Phase 5:** there is no real historical archive of pan-India meteorological predictors (CAPE, CIN, PWAT, K-index, wind shear, temperature/dewpoint profiles) in this repository — only a single current snapshot that gets overwritten four times a day. The only real historical predictor *time series* is point-based, at Bengaluru. Real historical, genuinely pan-India data exists for exactly one thing: IMD gridded rainfall (which is what CB's labels are already built from).

This was not assumed going in — it was discovered by trying to find a second timestamp for `pan_india_grid.json` and finding none.

## 4. Predictor Availability

Per-predictor status (`AVAILABLE` / `PARTIALLY AVAILABLE` / `MISSING` / `PROXY`), for the only place a genuine historical predictor series exists (VOBL point series):

| Predictor | Status | Source |
|---|---|---|
| `cape` | AVAILABLE | production CAPE feature, 0% missing |
| `cin` | **MISSING** | no CIN column exists anywhere in the real VOBL predictor history checked (`v3`/`v4` training CSVs, ERA5 BLR series) |
| `pwat_mm` | PARTIALLY AVAILABLE | production PRECIP_WATER feature, 4.95% missing (real gaps in the production CSV, not invented) |
| `k_index` | PARTIALLY AVAILABLE | same source, 4.95% missing |
| `totals_totals` | PARTIALLY AVAILABLE | same source, 4.95% missing |
| `wind_shear_ms` | PARTIALLY AVAILABLE | production wind_shear_500_850 feature, 4.95% missing |
| `t850`, `t700`, `t500` | AVAILABLE | ERA5, 0% missing |
| `td850`, `td700` | AVAILABLE (derived) | Bolton (1980) inverted Magnus formula from real ERA5 specific humidity + temperature — a physical transform, not a fabricated variable (same formula already used in the Colab pass, ported unchanged) |
| `ctt_c` | **MISSING** | no historical Himawari archive exists; only a live current snapshot (`data/himawari_realtime.json`) |

For the pan-India CB partition (991 of the 992 cells beyond VOBL, and even VOBL's rows *within* that partition): **every predictor is MISSING for every row**, for the single reason given in §3 — there is no historical pan-India predictor archive to draw from. This is stated once per partition rather than repeated across 3.99 million identical rows (see `processed/dataset/panindia_cb_partition_predictor_note.txt`).

No predictor was invented to fill a gap. `cin` and `ctt_c` are the two clearest cases: rather than following the earlier Colab pass's training-time choice to 0-fill them, this raw dataset leaves them as explicit `NaN` with `missing_flag=True` — 0-filling is deliberately left to a downstream training pipeline's own documented imputation strategy, not performed silently here (Phase 5 instruction §10).

## 5. Temporal Overlap

Computed directly, not assumed from the SIH proposal's stated 2015-2025 range:

- **VOBL partition**: earliest `2015-01-01`, latest `2025-12-31`, 4,018 days × 4 slots = up to 16,072 possible 6-hourly reference points. **Actual row count: 16,072** — full coverage, no gaps in the reference-time grid itself (individual predictor *values* within those rows have the missingness described in §4).
- **Pan-India CB partition**: same overall date range (IMD data spans 2015-2025 across all 11 `.grd` files present). Expected row count = 992 cells × 4,018 days = 3,985,856. **Actual row count: 3,985,856 — exact match.** No forced date range was assumed; this is the real intersection of "IMD data exists for this year" and "the canonical grid has this cell."

## 6. Spatial Alignment

The VOBL partition is a single, exact canonical cell (`IND_13.0_78.0`) — no regridding needed, the source point *is* the cell center used throughout this repo already. The pan-India CB partition's cell-to-IMD-pixel mapping reuses the exact `canon_to_imd_idx`-equivalent logic from Phase 4's `scripts/build_panindia_cb_labels.py` (same `±0.5°` box lookup, same `cell_id_for()` convention from `regrid.py`) — no second grid or second regridding method was introduced. Verified: all 992 canonical cells received IMD coverage (check 18), cell-ID convention is exactly `IND_{lat:.1f}_{lon:.1f}` everywhere (check 4).

## 7. Label Construction

TS, CB, and FF-proxy labels were **joined**, not re-derived with new logic, from Phase 4's own methodology — with one necessary exception: Phase 4's `processed/labels/cb_labels.csv` and `ff_labels_proxy.csv` only materialize `POSITIVE` rows (an explicit, documented Phase 4 design choice to keep file size sane). A real supervised dataset needs `NEGATIVE_CONFIRMED` rows too, so Phase 5 re-derived the identical daily rainfall-threshold logic (same `CB_THRESHOLD_MM = 64.5`, same rainfall-proxy formula) directly from `imd_rain/rain/*.grd`, materializing **both** classes. The threshold, the source file, and the resulting positive counts are all identical to Phase 4's own published numbers — verified directly (check 14: VOBL TS positive count = 584 matches Phase 4 exactly; pan-India CB positive count matches `cb_labels_summary.json` exactly).

## 8. TS Limitations

Unchanged from Phase 4: real, observed labels exist for VOBL only. In this dataset's VOBL partition, 15,276/16,072 rows have an observed TS label (`UNKNOWN` for the remaining 796 — gaps in the underlying station record, not invented). The pan-India CB partition carries `ts_label_status="UNKNOWN"` for all 3,985,856 rows, exactly as it must — no national TS negative was ever generated.

## 9. CB Limitations

Genuinely pan-India, but two real limitations carry forward unchanged: (1) daily source resolution — a CB label reflects one IMD daily rainfall reading, not four independent 6-hourly observations, and this dataset's `panindia_cb_partition` timestamps are explicitly `"...T daily"` rather than being silently exploded into four slot-rows that would misrepresent the label's true resolution (the partition's `slot_replication_note` — carried in `build_stats.json` and this report, not per-row, to avoid 4x redundant storage — states this explicitly for any downstream consumer); (2) 2,434,913 cell-days are `UNKNOWN` (mostly ocean/coastal, where IMD's land-only rainfall product has no reading) — never converted to negative.

## 10. FF Limitation

Unchanged and re-confirmed this phase: zero rows anywhere in this dataset have an observed FF label. `ff_label_status = "UNKNOWN"` for all 16,072 VOBL rows and (implicitly, by the CB partition's schema) all 3,985,856 pan-India rows. The rainfall proxy (`ff_proxy_label_status = "PROXY_NOT_OBSERVED"` for all 16,072 VOBL rows where it was computed) lives in entirely separate columns and was checked, not just designed, to never leak into `ff_label` (check 8, both conditions pass).

## 11. Missing-Data Strategy

Every predictor field is a 5-column group (`value`, `missing_flag`, `missing_reason`, `source`, `source_timestamp`) per `docs/PANINDIA_DATASET_SCHEMA.md`. No missing physical observation was ever written as `0` — verified directly for all 12 predictors across every missing row in the VOBL partition (check 10, 12/12 predictors pass). Imputation, if a training pipeline needs it, is explicitly deferred to that pipeline's own documented decision.

## 12. Leakage Prevention

Every predictor's own `source_timestamp` was checked against its row's reference date; none is ever later (check 6, both partitions pass). The pan-India CB partition's `source_timestamp` was checked the same way. No nearest-neighbor "closest available" join is used anywhere in this construction — VOBL predictors are joined on an exact `(date, slot)` key against the ERA5/production CSVs (present or absent, never fuzzy-matched to a different slot), and CB/FF labels are read directly from the source date they represent.

## 13. Dataset Dimensions

| | VOBL partition | Pan-India CB partition |
|---|---|---|
| Rows | 16,072 | 3,985,856 |
| Cells | 1 | 992 |
| Temporal resolution | 6-hourly | daily |
| Date range | 2015-01-01 to 2025-12-31 | 2015-01-01 to 2025-12-31 |
| Predictors present | Yes (see §4) | No (all missing, see §4) |
| File | `processed/dataset/vobl_partition.csv.gz` (1.4 MB) | `processed/dataset/panindia_cb_partition.csv.gz` (22.2 MB) |

## 14. Class/Label Statistics

**VOBL partition:**
- TS: 584 `POSITIVE`, 14,692 `NEGATIVE_CONFIRMED`, 796 `UNKNOWN`
- CB: 480 `POSITIVE`, 15,592 `NEGATIVE_CONFIRMED`, 0 `UNKNOWN`
- FF observed: 0 `POSITIVE`/`NEGATIVE_CONFIRMED`, 16,072 `UNKNOWN`
- FF proxy: 16,072 rows all `PROXY_NOT_OBSERVED` (binary proxy reading stored in `ff_proxy_label`, not counted as an observed rate)

**Pan-India CB partition:** 49,613 `POSITIVE`, 1,501,330 `NEGATIVE_CONFIRMED`, 2,434,913 `UNKNOWN` (matches Phase 4's `cb_labels_summary.json` exactly — this was a join/re-derivation of the same real data, not a new computation with a different answer). Positive rate among known labels: 3.20%.

No class balance was ever computed by treating `UNKNOWN` as negative (Phase 5 instruction §8) — every rate above is computed strictly over non-`UNKNOWN` rows.

## 15. Provenance

Every row carries `source_timestamp`, `source_resolution`, and `source_provenance` (free text naming the exact source file(s)). Every predictor additionally carries its own `<name>_source`. Every non-`UNKNOWN` label carries a `*_label_source` naming its exact origin (station observation string for TS, `"IMD 0.25deg gridded daily rainfall (>= 64.5mm)"` for CB). Verified directly (checks 11).

## 16. Reproducibility

`data/dataset_manifest.json` records the exact build parameters, the real base git commit this was built against (`a7d54dd8e5ed4b505e342c1b5342c1998c93e5ac` — no commit hash was fabricated; this reflects the repository state Phase 5 started from, since nothing in this phase was itself committed), and the exact regeneration command (`python3 scripts/build_panindia_dataset.py`). The build is deterministic — no random sampling, no seeded process — verified directly (check 16: `cb_daily_series_for_cell` produces identical output across repeated calls).

## 17. What Is Ready for MTL

Per Phase 5 instruction §14:

- **A. Rows with observed TS labels:** 15,276 (VOBL only)
- **B. Rows with observed CB labels:** 16,072 at VOBL; 1,550,943 pan-India total (across all 992 cells, daily granularity)
- **C. Rows with observed FF labels:** **0** — nowhere in this dataset
- **D. Rows with TS+CB simultaneously known:** 15,276 (VOBL only — every one of those rows also has CB known, since VOBL has zero CB `UNKNOWN` rows)
- **E. Masking required?** Yes — any real MTL training run must mask the FF loss term entirely (no observed FF label exists to compute a loss against) and must mask the TS loss term for every non-VOBL cell.
- **F. Class weighting required?** Yes — both TS (584/15,276 ≈ 3.8% positive) and CB (3.20% positive) are meaningfully imbalanced.
- **G. Does the existing MTL architecture need changes before training?** Not structurally — `backend/mtl_backbone.py`'s three independent heads already support masked per-task loss (an unused head can simply not contribute for a given row). What it does need, which this phase did not build and was told not to build, is: (1) a genuinely trained set of weights (still nonexistent, confirmed again in Phase 4.5), and (2) a training script that actually implements the masking in (E) — the existing `colab/train_mtl_blr.py` was written before this phase's masking requirement was known and would need to be checked against it before any real training run.

**MTL FF supervised training is currently blocked — zero observed FF rows exist anywhere in this dataset.** This is not worked around by training against the rainfall proxy; the proxy stays in its own columns, exactly as designed.

**Pan-India TS supervision does not exist** — this dataset does not pretend otherwise; the pan-India CB partition carries `ts_label_status="UNKNOWN"` for all 3,985,856 of its rows.

**What a real MTL run could honestly attempt today:** a two-task (TS+CB) model at VOBL only, using the VOBL partition's 15,276 fully-labeled, fully-predictor-complete rows. This is a materially narrower claim than "pan-India multi-hazard MTL," and this report does not claim otherwise.

## 18. What Is Still Blocked

Unchanged from Phase 4.5 (§9 of `docs/PHASE_4_5_INTEGRITY_AUDIT.md`), re-confirmed by this phase's own data audit:

1. Pan-India TS labels — no pan-India lightning/station network dataset exists in this repo.
2. Genuinely observed FF labels — `data/catchment_characteristics_indofloods.csv` (gauge coordinates) does not exist.
3. **Newly confirmed this phase**: pan-India historical *predictors* — no archived time series of the pan-India grid exists; only a live current snapshot. This blocks pan-India CB (and any future pan-India TS/FF) from ever having predictor-complete rows unless either (a) a historical archive is built going forward from today's live snapshots, or (b) a genuinely gridded historical reanalysis (e.g. real IMDAA, still DATA BLOCKED per Phase 4.5) is obtained.
4. IMDAA / INSAT-3D/3DR — still DATA BLOCKED, unchanged.
5. Trained MTL weights — still nonexistent; requires the user's own Colab/A100 run.

---

## Files Created This Phase

- `docs/PANINDIA_DATASET_SCHEMA.md`
- `docs/PHASE_5_DATASET_REPORT.md` (this file)
- `scripts/build_panindia_dataset.py`
- `scripts/validate_panindia_dataset.py`
- `tests/test_panindia_dataset.py`
- `data/dataset_manifest.json`
- `processed/dataset/vobl_partition.csv.gz`
- `processed/dataset/panindia_cb_partition.csv.gz`
- `processed/dataset/panindia_cb_partition_predictor_note.txt`
- `processed/dataset/build_stats.json`

## Files Changed This Phase

None. Phase 5 only added new scripts/docs/data under `scripts/`, `docs/`, `data/dataset_manifest.json` (new file), and `processed/dataset/` (new directory). No existing file (including anything touched in Phase 4.5) was modified.

## Tests Run, Exact Counts

| Suite | Result |
|---|---|
| `test_regrid.py` (Phase 3, pre-existing) | ALL CHECKS PASSED (8/8) |
| `tests/test_canonical_grid.py` (Phase 3) | 28/28 passed |
| `tests/test_panindia_labels.py` (Phase 4) | 19/19 passed |
| `tests/test_integrity_guardrails.py` (Phase 4.5) | 25/25 passed |
| `tests/test_panindia_dataset.py` (Phase 5, new) | 45/45 passed |
| **Total** | **125/125 passed, 0 failed** |
