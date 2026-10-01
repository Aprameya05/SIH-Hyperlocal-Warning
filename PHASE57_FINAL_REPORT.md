# PHASE 5.7 COMPLETE

Research-only offline positive-unlabeled (PU) flash-flood trigger model.
Not integrated into production. No production file modified.

## What was built

- `scripts/build_ff_pu_dataset.py` — builds
  `processed/ff_pu/ff_pu_training_table.csv` (144,486 rows: 620 POSITIVE,
  143,866 UNLABELED) from real IMD `.grd` rainfall and the INDOFLOODS
  catchment/events data, reusing Phase 5.5/5.6's overlap logic.
- `scripts/train_ff_pu_models.py` — trains Models A (catchment-only), B
  (rainfall-only), C (combined), each with a logistic and an XGBoost PU
  variant (Elkan-Noto SCAR correction), temporal split, spatial holdout,
  sensitivity analysis, feature importance, and SHAP.
- `docs/FF_PU_FEATURE_CATALOG.md` — every feature, source, units,
  hazard relevance, leakage status.
- `docs/PHASE_5_7_FF_PU_MODEL_REPORT.md` — full write-up.
- `tests/test_ff_pu_phase57.py` — 13 tests.
- All model/eval artifacts under `processed/ff_pu/`.

## 620-positive reconciliation

Recomputed independently from `processed/indofloods/indofloods_grid_events.csv`:
**620 unique (cell, date) positives, 69 cells, 131 gauges — exact match**
to the documented Phase 5.6 figures. No forcing was needed.

## Test results

- `tests/test_ff_pu_phase57.py`: **13/13 passed**.
- Full repo suite (pytest, all `test_*.py` + `tests/test_*.py`, excluding
  4 known pre-existing blockers): **151/151 passed**.
- Standalone run of `tests/test_canonical_forecast.py`'s own runner:
  18/19 passed — 1 pre-existing failure unrelated to this phase (does not
  reference any Phase 5.7 file; see report Section 17).
- Known pre-existing blockers, unchanged: `donfig` missing (3 files:
  `test_himawari.py`, `test_segments.py`, `test_segments_v2.py`), NOMADS
  403 (1 file: `test_nomads.py`).

## Headline honest finding

- **Model A (catchment-only): no usable signal** — spatial-holdout AUC
  0.500, 0% enrichment in the top 5%. Static basin shape alone does not
  predict which day a flood occurs.
- **Model B (rainfall-only): real, moderate signal** — spatial-holdout
  AUC 0.871, **4.57x enrichment** over base rate in the top 5% of ranked
  cell-days, 22.9% recall-at-top-5%.
- **Model C (combined): best on the harder generalization test** —
  spatial-holdout AUC 0.878, **6.65x enrichment**, **33.3% recall-at-top-5%**
  (vs. roughly neutral-to-slightly-below-B on the temporal validation
  split: AUC 0.584 vs B's 0.651). Combining rainfall and static catchment
  features measurably helps when generalizing to an unseen basin; it does
  not clearly help when evaluating on already-seen basins at later dates.
- No claim that flash-flood prediction is solved. This is a research
  prototype prioritization signal, evaluated only against PU-appropriate
  metrics (no confirmed negatives exist in this dataset), on a small
  (620-positive), geographically concentrated (69-cell) sample.

## Production-file integrity

SHA-256 of `backend/pipeline.py`, `forecast.json`,
`data/pan_india_grid.json`, `data/canonical_forecast.json`, `index.html`,
and all `.github/workflows/*.yml` — **byte-identical before and after**,
confirmed by diff. Nothing was committed or pushed.
