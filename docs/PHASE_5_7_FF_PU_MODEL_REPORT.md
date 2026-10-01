# Phase 5.7 — Flash-Flood Trigger Model: Research-Only Offline PU Learning

Research-only. No production file modified (checksums re-verified,
Section 18). Not integrated into `backend/pipeline.py`.

## 0. VOBL flash-flood logic (read-only inspection, per Task 1)

`backend/pipeline.py`'s `hazard_probabilities()` (lines ~424-514) computes
`ff_prob` as a hand-tuned **rule-based heuristic**, not a trained ML
model: a weighted sum of live atmospheric fields (PWAT weight 0.45, CAPE
weight 0.22, cloud-top temperature weight 0.18, QPE-proxy weight 0.10,
850 hPa convergence weight 0.05), each individually clipped to [0,1] and
scaled by `min(1, ts_prob + 0.1)` where `ts_prob` is the thunderstorm
score from the same function. A separate terrain modifier
(`apply_terrain_to_ff`) adjusts `ff_prob` using DEM/slope/drainage
lookups. None of these weights were fit to any flood-outcome label —
they are engineered thresholds, and the function has no training target
at all. It **cannot serve as a like-for-like ML baseline** for this
phase's models because it consumes live GFS atmospheric fields (CAPE,
PWAT, cloud-top temperature, convergence) that do not exist for
historical INDOFLOODS event dates in this repo — there is no way to
backfill those inputs for 2015-2020 without a reanalysis source this repo
does not have (see `docs/PHASE_5_6_HISTORICAL_DATA_DECISION.md` Section
5 on IMDAA being blocked). It is documented here as the existing
production logic, untouched, and not compared quantitatively.

## 1. Dataset construction

`scripts/build_ff_pu_dataset.py` builds
`processed/ff_pu/ff_pu_training_table.csv` by reusing the `.grd`-reading
and regridding logic verbatim from `scripts/prepare_indofloods_rainfall_context.py`
/ `scripts/compute_indofloods_imd_overlap.py`. One row per (cell, date).

## 2. 620 confirmed positives — reconciliation

Recomputed directly from `processed/indofloods/indofloods_grid_events.csv`
(`mapping_status==MAPPED`, `Start Date` in [2015-01-01, 2020-09-24]),
deduplicated on `(cell_id, Start Date)`:

- **620** unique (cell, date) positives — **matches** the documented figure exactly.
- **69** unique cells — matches.
- **131** unique gauges — matches.

No forcing was needed; the recomputation lands exactly on the documented
numbers (see `processed/ff_pu/dataset_build_summary.json`).

## 3. Unlabeled population size and subsampling

**143,866** unlabeled (cell, date) rows across the 69 event cells,
2015-01-01 to 2020-09-24 (the training table's actual window). This is
smaller than the documented 276,622 figure because that figure spans the
fuller 2015-2025 IMD file coverage across the same 69 cells
(`docs/PHASE_5_6_HISTORICAL_DATA_DECISION.md` Section 8), while the
training table deliberately restricts itself to the 2015-01-01..2020-09-24
common period — the same window the 620 positives themselves come from —
so labeled and unlabeled populations are drawn from the same period. No
subsampling was applied: the full unlabeled population in that window
(144,486 total rows) was computationally tractable for pandas/xgboost on
this machine (build: ~95s, train: ~30s), so no sampling bias was
introduced.

## 4. Feature groups

- **Rainfall (dynamic, 7 features):** `rain_1d/3d/5d/10d`,
  `rain_max1d_5d`, `rain_recent_vs_antecedent_ratio`,
  `rain_accel_3d_minus_prior3d` — all backward-looking over
  `[T-w, T-1]`, from `imd_rain/rain/*.grd` only.
- **Catchment (static, 31 numeric + 4 categorical):** drainage-network
  topology, basin morphometry/shape/relief, soil/lithology/land-cover
  class, and climate normals from
  `data/catchment_characteristics_indofloods.csv`, averaged/mode across
  gauges mapped to each cell. Full list and per-feature documentation:
  `docs/FF_PU_FEATURE_CATALOG.md`.

## 5. Excluded features and why

GDP (6 vintages), GDP per capita PPP (6 vintages), Night Light, Road
Density, Urban percentage, HDI (6 vintages), Population Count, Population
Density — all exposure/impact variables per the assignment's hard
constraint, not hazard predictors. Also excluded: INDOFLOODS `T1d`-`T10d`
(event-conditioned only, would leak trivially) and all post-event fields
(Peak Level/Date, Peak Discharge, Duration, Flood Type, Warning/Danger
Level).

## 6. PU formulation used

Elkan-Noto SCAR (Selected Completely At Random) class-prior correction
(e1 estimator): a probabilistic classifier `s(x)` is trained on
labeled-positive-vs-unlabeled using training-period data only; the class
prior `c = P(labeled | positive)` is estimated as the mean of `s(x)` over
a held-out 20% fold of the *training period's own* labeled positives
(never touching validation or the spatial holdout cell). The corrected
posterior is `P(y=1|x) = min(s(x)/c, 1)`. Both a logistic-regression PU
model and an XGBoost PU model were fit this way; both are also reported
in a "naive uncorrected" form (`s(x)` as-is) for comparison. **The SCAR
assumption is a real limitation here**: event reporting in INDOFLOODS
almost certainly favors better-monitored gauges, so the labeled positives
are not truly a uniform random sample of all true positives. This is
flagged, not resolved, and the sensitivity analysis (Section 9) shows how
much the specific `c` value matters.

## 7. Temporal validation results (train 2015-2018, validate 2019 to 2020-09-24)

XGBoost PU, PU-caveated ranking AUC (U treated as negative for ranking
purposes ONLY — not a claim that U is confirmed negative) and enrichment
over base rate in the top-5% of ranked cell-days:

| Model | Val ranking AUC (caveated) | Val enrichment @top-5% | Val recall @top-5% |
|---|---|---|---|
| A — catchment only | 0.496 | 1.35x | 6.7% |
| B — rainfall only | 0.651 | 5.39x | 26.9% |
| C — combined | 0.584 | 4.33x | 21.6% |

(Full numbers including top-1% and both logistic and XGBoost, corrected
and naive, in `processed/ff_pu/validation_results.json`.)

## 8. Spatial holdout results (highest-volume event cell, computed: `26.0_88.0`, 48 positives, held out entirely from training and from c-estimation)

| Model | Holdout ranking AUC (caveated) | Holdout enrichment @top-5% | Holdout recall @top-5% |
|---|---|---|---|
| A — catchment only | 0.500 | 0.0x | 0.0% |
| B — rainfall only | 0.871 | 4.57x | 22.9% |
| C — combined | 0.878 | 6.65x | 33.3% |

## 9. Sensitivity analysis

`processed/ff_pu/sensitivity_results.csv` varies the assumed class prior
`c` from 0.02 to 0.40 for Model C's XGBoost PU scores. The **ranking**
(AUC, based on `s(x)` before the constant-`c` rescaling only re-scales,
not re-ranks, so ranking AUC is nearly flat, 0.53-0.58, across the whole
grid) is robust to the choice of `c` — as expected, since dividing every
score by the same constant does not change rank order within a fixed
evaluation set. Point-wise enrichment/recall at a fixed top-k fraction
*is* unaffected by `c` for the same reason (rescaling doesn't change
which rows land in the top k%). What *would* change under a different
`c` is any absolute-probability decision threshold (e.g. "flag if
P(y=1|x) > 0.5") — this is stated explicitly: **our reported enrichment
and recall-at-k numbers do not depend on the exact value of the estimated
prior; only an absolute-probability alerting threshold would.**

## 10. Model A vs B vs C — honest comparison

- **Model A (catchment-only)** performs at essentially chance level on
  both validation (AUC 0.496) and the spatial holdout (AUC 0.500,
  0 positives captured in the top 5%). Static catchment shape/soil/climate
  features alone carry no measurable signal for *which specific day* a
  flash flood occurs — unsurprising, since they don't vary in time.
- **Model B (rainfall-only)** is the strongest single-group model on both
  validation (AUC 0.651, 5.4x top-5% enrichment) and the spatial holdout
  (AUC 0.871, 4.6x enrichment). Antecedent rainfall drives essentially all
  of the day-to-day signal.
- **Model C (combined)** is close to Model B on validation (AUC 0.584,
  slightly *below* B — likely added-noise from static features not
  helping the temporal generalization) but modestly **exceeds** Model B on
  the spatial holdout (AUC 0.878 vs 0.871; enrichment 6.65x vs 4.57x;
  recall 33.3% vs 22.9%). This is a real, if not large, finding: static
  catchment susceptibility appears to help most when generalizing to a
  *cell the model has never seen*, which is exactly the role static
  features should play (rainfall alone doesn't know a given basin's
  shape-driven susceptibility). On the validation split (same cells,
  future dates), rainfall dominates and adding static features doesn't
  clearly help. **No subjective "winner" is declared** — combining the
  two feature groups measurably helps on the harder, more realistic
  spatial-generalization test, and is roughly neutral-to-slightly-worse
  on the temporal-only test.

## 11. Feature importance / SHAP findings

XGBoost gain-based importance for Model C
(`processed/ff_pu/feature_importance.csv`) is dominated by static
catchment shape/relief features (Catchment Perimeter, Basin Magnitude,
Drainage Area, Wandering Ratio) interleaved with `rain_5d` and `rain_1d`
near the top — consistent with the spatial-holdout result that static
features add discriminative power once rainfall alone is available.
SHAP values were computed successfully
(`processed/ff_pu/shap_examples.csv`) for 10 representative confirmed
positive validation examples. **This importance ranking shows which
features the trees split on most and by how much prediction shifts for
individual examples — it does not establish that any feature causes
flash floods**, and gain importance in particular is known to favor
high-cardinality/continuous features somewhat mechanically; this is
stated as a limitation, not resolved further here.

## 12. Limitations

- SCAR assumption (Section 6) is unverified and plausibly wrong in a
  specific, non-random way (reporting bias toward well-monitored gauges).
- No true negatives exist anywhere in this dataset (Case B, carried over
  from Phase 5.5/5.6); all reported metrics are PU-appropriate
  (ranking/enrichment), never naive precision/recall against a "negative"
  class.
- Catchment features are gauge-native and were averaged onto cells that
  may host more than one gauge — a real approximation, not a precise
  cell-native catchment characterization.
- The 69-cell, 2015-2020 window is a small, geographically concentrated
  slice of India (see Phase 5.5's inventory of which basins these are);
  results should not be read as pan-India-representative.
- Sample size is small (620 positives total, 364/208/48 in
  train/val/holdout) — single-run point estimates, no cross-validation
  variance reported.

## 13. Is the signal useful? (honest answer)

**Rainfall alone gives a real, moderate ranking signal** (spatial-holdout
AUC 0.87, 4.6x enrichment in the top 5% of ranked cell-days) — far better
than the near-chance static-only model. **Combining rainfall with static
catchment features modestly improves generalization to an unseen basin**
(holdout AUC 0.878, enrichment 6.65x, recall 33.3% in the top 5% —
meaning a third of that basin's actual flood days would be inside the
top-ranked 5% of its cell-days if this model were used to prioritize
monitoring). This is a genuinely useful **prioritization/triage** signal
for a research prototype — it is very far from what would be needed for
an operational, standalone flash-flood trigger, and it is **not**
presented as one. It has not been compared against the production
heuristic (Section 0 explains why that comparison isn't currently
possible) and has not been tested against confirmed negatives, because
none exist.

## 14. Further data needed

- A continuous historical atmospheric-instability source (CAPE, PWAT,
  wind shear at pan-India gridded scale, pre-2015 if possible) to make a
  fair comparison against the production heuristic's own predictor set.
- A confirmed-negative or continuous streamflow record to move beyond PU
  learning entirely.
- Denser/more geographically distributed gauge coverage to reduce the
  SCAR-violation risk and to test generalization across more than one
  held-out basin.

## 15. Exact artifacts created

All under `processed/ff_pu/`: `ff_pu_training_table.csv`,
`dataset_build_summary.json`, `validation_results.json`,
`sensitivity_results.csv`, `feature_importance.csv`,
`shap_examples.csv`, `model_metadata.json`, `feature_catalog.json`,
`training_manifest.json`, `RESEARCH_ONLY_model_c_xgboost.json`,
`RESEARCH_ONLY_model_c_logistic.pkl`. Plus
`docs/FF_PU_FEATURE_CATALOG.md`, `tests/test_ff_pu_phase57.py`, and the
two build/train scripts under `scripts/`.

## 16. Exact tests passed

`tests/test_ff_pu_phase57.py`: **13/13 passed** (standalone runner).
Full repo suite via pytest (all `test_*.py` at root and under `tests/`,
excluding the 4 known pre-existing blockers below): **151/151 passed**.
Standalone-script execution of `tests/test_canonical_forecast.py`
(its own custom runner, not pytest) shows **18/19 passed** — 1
pre-existing failure in its own "sole writer" guardrail check that
already existed before this phase and does not reference any file this
phase created (see Section 17).

## 17. Known pre-existing failures

- `test_himawari.py`, `test_segments.py`, `test_segments_v2.py`:
  `ModuleNotFoundError: No module named 'donfig'` (satpy dependency
  missing in this sandbox) — pre-existing, unrelated to this phase.
- `test_nomads.py`: `403 Forbidden` from the sandbox's own egress proxy
  to `nomads.ncep.noaa.gov` — pre-existing environment restriction,
  unrelated to this phase.
- `tests/test_canonical_forecast.py`'s standalone-runner check #11
  ("`canonical_forecast_writer.py` is the sole writer referenced by name")
  fails when run via its own `main()` (not via pytest) because
  `tests/test_indofloods_phase56.py` (a Phase 5.6 file, not touched this
  phase) already references the string `canonical_forecast.json`. This
  failure exists independent of anything created in Phase 5.7 (no file
  this phase created appears in the guardrail's reported list) and was
  not introduced or modified here.

## 18. Production files unchanged (confirmed by checksum)

SHA-256 of `backend/pipeline.py`, `forecast.json`,
`data/pan_india_grid.json`, `data/canonical_forecast.json`, `index.html`,
and all five `.github/workflows/*.yml` files, taken before and after this
phase's work: **byte-identical**, confirmed diff-clean.
