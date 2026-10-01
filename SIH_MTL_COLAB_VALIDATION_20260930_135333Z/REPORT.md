# DRIFT / SIH — Colab A100 MTL Validation Pass — 2026-09-30

**What this pass actually is:** a reproducible training/evaluation package for the *existing, already-committed* `backend/mtl_backbone.py` transformer, built and dry-run-verified in this sandbox against the real BLR (VOBL/Bengaluru) multi-task dataset, ready for you to upload to Colab and run on your A100. It is not a pan-India model. Nothing here is connected to production. No training was run on your A100 from this session — I can't reach it.

**One thing done for real in this pass, not deferred:** since this sandbox has xgboost + scikit-learn (just not torch/GPU), I actually loaded the real production models and scored them on the real held-out 2024–2025 test set. Those numbers in `evaluation/production_baseline_metrics.json` are genuine, computed results, not placeholders.

## 1. Architecture Audit — `backend/mtl_backbone.py` (inspected, not rewritten)

- **Input tensor shape:** `(B, 12)` — a single timestep per sample, no sequence dimension. Plus `lat`, `lon` as separate `(B,)` tensors used only for a 4-dim sin/cos positional encoding.
- **Feature ordering (fixed, must match exactly):** `cape, cin, pwat_mm, k_index, totals_totals, wind_shear_ms, t850, t700, t500, td850, td700, ctt_c` (`FEATURE_NAMES` in the file).
- **Normalization:** fixed climatological `FEATURE_MEAN`/`FEATURE_STD` constants baked into the file — not fit from data. This means there is **no scaler-leakage risk** for this architecture (confirmed in the leakage audit, §10 below).
- **Temporal window: NONE.** The architecture is explicitly single-timestep, not spatiotemporal, despite the name "spatiotemporal backbone" used loosely elsewhere in prior reports. This is stated honestly here rather than glossed over (per your instruction in §3 of the brief).
- **Output heads:** three independent 2-layer MLP heads (`head_ts`, `head_cb`, `head_ff`), each ending in `Sigmoid` — outputs are already probabilities in `[0,1]`, not logits.
- **Loss function (as shipped):** plain `nn.BCELoss()`, summed across the three heads, **with no class-weighting support at all** — `train()` in the file takes no weighting argument. This is a real, concrete gap for a dataset this imbalanced (§5).
- **Masking support:** none.
- **Device handling:** `torch.device("cuda" if torch.cuda.is_available() else "cpu")` — correct, standard.
- **Checkpoint format:** `torch.save(model.state_dict(), "backend/mtl_model.pt")` — raw state dict, no metadata bundled with it (no epoch, no metrics, no feature manifest). The training package below fixes this by saving a separate `run_metadata.json` next to each checkpoint rather than modifying the architecture's own save call.
- **Inference interface:** `infer()` reads `data/pan_india_grid.json`'s `grid_cells`, scores each cell, writes `data/mtl_predictions.json`. **Not used or invoked by this pass** — no production or pan-India inference was run.
- **Does the implementation support the existing CSV data?** No, not directly — see §2. `GridDataset` expects a CSV with columns named exactly `lat, lon, cape, cin, pwat_mm, ...` which does not exist; the real BLR CSV uses different column names and is missing two of the twelve features outright.

**Bug check:** no implementation bug was found that blocks training — the architecture runs; it's the *data adapter* that doesn't exist yet, which is what this pass builds (as a separate script, not a change to `mtl_backbone.py`). **`backend/mtl_backbone.py` was not modified.**

## 2. Real BLR Multi-Task Training Dataset

Source: `data/bengaluru_6hr_training_dataset_cb_ff.csv` (verified in the prior pass: 15,276 rows, 2015–2025, real IMD/ERA5/INDOFLOODS-derived features and labels). No synthetic rows, no synthetic labels.

`colab/build_blr_dataset.py` maps this CSV onto the architecture's 12 required features and writes `colab/training/feature_manifest.json`. Run for real in this sandbox (pandas/numpy only, no torch needed) against the actual file. Honest mapping, not a shape-fitting exercise:

| MTL feature | Source | Status |
|---|---|---|
| `cape` | `CAPE` | direct |
| `cin` | — | **UNAVAILABLE** — no CIN column exists anywhere in this CSV; filled with the architecture's own `FILL_VALUE=0.0`, not substituted with an unrelated variable |
| `pwat_mm` | `PRECIP_WATER` | direct |
| `k_index` | `K_INDEX` | direct |
| `totals_totals` | `TOTALS_TOTALS` | direct |
| `wind_shear_ms` | `wind_shear_500_850` | direct, documented substitution: the architecture names no canonical shear layer, so the deep-layer shear already computed in this CSV is used |
| `t850` / `t700` / `t500` | `ERA5_t_{850,700,500}hPa` | direct (Kelvin, matches the model's own normalization constants) |
| `td850` / `td700` | derived from `ERA5_q_{850,700}hPa` + `ERA5_t_{850,700}hPa` via the Bolton (1980) dewpoint-from-specific-humidity formula | **derived**, a real physical transform of real ERA5 fields — not fabricated |
| `ctt_c` | — | **UNAVAILABLE** — this CSV predates Himawari cloud-top-temperature integration; filled with `FILL_VALUE=0.0` |

Full detail (units, preprocessing, per-feature notes) is in `colab/training/feature_manifest.json`.

## 3. Temporal Sequences

**The architecture does not use sequences.** Confirmed by reading `MTLHazardModel.forward()`: it takes `(B, 12)` and produces `(B, d_model)` through the transformer encoder with a sequence length of 1 (`x.unsqueeze(1)` then `.squeeze(1)`). This is stated honestly rather than building fake sequence windows to make it look spatiotemporal — it currently is not.

## 4. Chronological Train/Val/Test Split (real, computed)

Run against the actual date range (`2015-01-02` to `2025-12-31`, verified):

| Split | Rows | Date range | TS positive | CB positive | FF positive | TS rate | CB rate | FF rate |
|---|---|---|---|---|---|---|---|---|
| Train | 9,824 | 2015-01-02 – 2021-12-31 | 388 | 28 | 16 | 3.95% | 0.29% | 0.16% |
| Val | 2,772 | 2022-01-01 – 2023-12-24 | 141 | 24 | 24 | 5.09% | 0.87% | 0.87% |
| Test | 2,680 | 2024-01-01 – 2025-12-31 | 55 | 8 | 8 | 2.05% | 0.30% | 0.30% |

Hazard overlap (train split): TS∧CB = 5 rows, TS∧FF = 5 rows, CB∧FF = 8 rows — every CB-positive train row is also FF-positive, worth being aware of when interpreting multi-task learning benefits later.

The test set was **not touched** for any tuning decision in this pass.

## 5. Extreme Class Imbalance — Handling

Confirmed rates (train split): CB 0.29%, FF 0.16% (close to your stated ~0.39%/0.31% overall-file figures; the train-only split skews slightly lower since more recent years carry more positives — see §4 table).

The existing architecture ships with **no weighting support**, so the smallest defensible fix was made in a new file (`colab/train_mtl_blr.py`), not in `mtl_backbone.py` itself: standard `pos_weight`-weighted `BCEWithLogitsLoss`, computed as `pos_weight = n_negative_train / n_positive_train`, **from the training split only**. Since the shipped heads output `Sigmoid` probabilities rather than logits, the training script inverts the sigmoid output back to a logit before applying the weighted loss — mathematically equivalent to weighted BCE on probabilities, and it does not alter the model's forward pass, parameters, or saved-checkpoint shape.

Computed weights (real, from `colab/training/class_weights.json`):

| Hazard | n_pos (train) | n_neg (train) | pos_weight |
|---|---|---|---|
| TS | 388 | 9,436 | 24.32 |
| CB | 28 | 9,796 | 349.86 |
| FF | 16 | 9,808 | 613.0 |

Two experiments are wired up: **A. baseline** (unweighted BCE, matches the architecture's shipped default) and **B. class-weighted** (the weights above). A focal-loss variant (**C**) was intentionally not built — the brief says prefer the simplest defensible approach first; C should only be added if A/B on Colab show weighted BCE isn't enough.

## 6–7. Colab Training Package / Experiments

`colab/`:
- `build_blr_dataset.py` — **already run in this sandbox**, real output in `colab/training/` (`train.npz`, `val.npz`, `test.npz`, `feature_manifest.json`, `splits.json`, `class_weights.json`, `leakage_report.json`)
- `train_mtl_blr.py` — imports `MTLHazardModel` unmodified from `backend/mtl_backbone.py`, runs experiments A and B, deterministic seed (1337), early stopping on validation loss (patience 10), saves `best_checkpoint.pt` + `training_curve.json` + `run_metadata.json` per run under `colab/training/runs/<experiment>/`, picks the best by **validation** loss only, never test — **not run here, no GPU/torch in this sandbox; syntax-verified with `ast.parse` only**
- `evaluate_mtl_blr.py` — scores the selected checkpoint once on the untouched test set, full metric set — **not run here, same reason**
- `evaluate_xgb_production_baseline.py` — **already run in this sandbox for real** (§9)
- `DRIFT_MTL_BLR_Colab.ipynb` — sequences all of the above; installs `requirements_colab.txt`, checks CUDA + prints GPU name, runs build → train → evaluate → comparison, valid notebook JSON (verified by re-parsing it)
- `requirements_colab.txt` — `torch>=2.1, numpy, pandas, scikit-learn, xgboost`

The package assumes only that the full repo (or at least `backend/mtl_backbone.py`, `data/bengaluru_6hr_training_dataset_cb_ff.csv`, and `colab/`) is present in the Colab working directory — no dependency on anything specific to this Claude sandbox.

## 8. Required Metrics — What's Actually Computable Right Now

`evaluate_mtl_blr.py` computes, per hazard, on the untouched test set: AUROC, AUPRC, POD, FAR, CSI, HSS, BIAS, and a full confusion matrix, reporting `"unavailable"` (a string, not a fabricated number) wherever a metric is undefined for a subset — this logic is shared code with §9's already-executed evaluation, so its correctness is already demonstrated by real output below. Per-forecast-horizon (per-slot) breakdown is supported by the same data structures (`slot` is preserved through the split) but is not pre-built into the default script to keep the first run simple; the comparison notebook cell can be extended to group by slot once you have real MTL numbers to look at.

## 9. Comparison Against Production XGBoost — REAL, Already Computed

This section is not a template — it's actual output from `colab/evaluate_xgb_production_baseline.py`, run in this sandbox against the real, already-deployed model files (`models/nowcast_slot*_v6_temporal.pkl` for TS, `models/{cb,ff}_slot_*_model.json` + `_calibrator.pkl` for CB/FF), scored on the exact same 2024–2025 test population and the exact same `ts_label`/`cb_label`/`ff_label` targets the MTL package will use.

**TS: unavailable.** All four production TS slot artifacts require 9 additional engineered feature columns (`doy, cape_trend, moisture_depth, low_level_convergence, wet_bulb_potential_temp, rf_std_7d, lapse_rate_850_500, is_october_slot2, monsoon_break_flag`) that do not exist in `bengaluru_6hr_training_dataset_cb_ff.csv` — the production TS models were trained on a newer/richer feature set than this particular CB/FF-audited CSV carries. Rather than zero-fill those columns and report a number that would misrepresent production TS performance, the script reports this honestly as unavailable. **This is a real, useful finding on its own**: it means the CB/FF-audited CSV and the TS production feature set have drifted apart and would need reconciling before any real three-way TS comparison is possible.

**CB (production XGBoost, real numbers):** n=2680, 8 positive. AUROC 0.924, AUPRC 0.091, at threshold 0.5: POD 0.0, FAR 1.0, CSI 0.0, HSS ≈ 0.0, BIAS 0.125 (TP=0, FP=1, FN=8, TN=2671). High AUROC but a threshold of 0.5 catches nothing — consistent with a severely imbalanced target and a calibrator that was never tuned to a real alerting threshold for CB (no such threshold exists in `forecast_action.py` today, noted in the output).

**FF (production XGBoost, real numbers):** n=2680, 8 positive. AUROC 0.986, AUPRC 0.240, at threshold 0.5: POD 0.375, FAR 0.786, CSI 0.158, HSS 0.270, BIAS 1.75 (TP=3, FP=11, FN=5, TN=2661).

Full JSON: `colab/evaluation/production_baseline_metrics.json`.

**Comparison table (to fill in once you've run the notebook):**

| | Existing XGBoost | MTL candidate |
|---|---|---|
| TS AUROC/AUPRC/POD/FAR/CSI/HSS/BIAS | unavailable — feature mismatch, see above | *(fill from `mtl_test_metrics.json` after Colab run)* |
| CB AUROC/AUPRC/POD/FAR/CSI/HSS/BIAS | 0.924 / 0.091 / 0.0 / 1.0 / 0.0 / ≈0.0 / 0.125 | *(fill after Colab run)* |
| FF AUROC/AUPRC/POD/FAR/CSI/HSS/BIAS | 0.986 / 0.240 / 0.375 / 0.786 / 0.158 / 0.270 / 1.75 | *(fill after Colab run)* |

No winner is declared here, per instructions — this table is deliberately left for you to read once the MTL numbers exist.

## 10. Leakage Audit — Real, Already Run

From `colab/training/leakage_report.json` (computed against the actual split):

- Chronological order respected: **true**
- Train max date (2021-12-31) < val min date (2022-01-01): confirmed
- Val max date (2023-12-24) < test min date (2024-01-01): confirmed
- Train/val date overlap rows: **0**
- Val/test date overlap rows: **0**
- Duplicate (date, slot) rows in the source CSV: **0**
- Scaler leakage risk: **none** — the architecture's normalization constants are fixed climatological values, not fit from any split
- Class weights: verified computed from the training split only (formula recorded alongside each weight in `class_weights.json`)

**Status: PASS — no leakage detected.**

## 11. Model Explainability

Not forced. SHAP is fit for tree ensembles (`compute_realtime_shap.py` already applies it correctly to the production XGBoost models) but is a poor match for a 4-layer transformer without substantially more engineering (e.g. `captum`'s integrated gradients, or attention inspection — neither implemented). Per instruction: **"MTL explainability not yet implemented."** No feature importance was fabricated for the MTL candidate.

## 12. Pan-India Limitation

Every generated metadata file in this package (`run_metadata.json` per experiment, the evaluation output header) carries the literal field `"TRAINING_DOMAIN": "VOBL / Bengaluru only"` and the note: *"This experiment DOES NOT establish pan-India model validity. It does not justify deploying the MTL model to the 992-cell production grid."* This is baked into the scripts' output, not just this report.

## 13. A100 Usage — Exact Commands For You To Run

This session cannot execute these — they're for you, on Colab:

```
# 1. In Colab: upload the repo (zip upload, or git clone your own repo/branch)
!unzip SIH-Hyperlocal-Warning.zip -d /content/   # or: !git clone <your repo url>
%cd /content/SIH-Hyperlocal-Warning/colab

# 2. Install dependencies
!pip install -q -r requirements_colab.txt

# 3. Runtime > Change runtime type > A100 GPU (UI step, before running cells)

# 4. Verify CUDA
import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))

# 5. Build dataset (or reuse the already-committed colab/training/*.npz from this pass)
!python3 build_blr_dataset.py

# 6. Train
!python3 train_mtl_blr.py

# 7. Evaluate
!python3 evaluate_mtl_blr.py

# 8. Collect artifacts: download colab/training/runs/ and colab/evaluation/
```

Or open and run `DRIFT_MTL_BLR_Colab.ipynb` directly, which sequences all of the above.

## 14. What Would Be Required Before Any Pan-India Promotion

Unchanged from the prior data audit, restated because it still applies regardless of this pass's results: a pan-India labeled dataset (none exists — `pan_india_grid.json` has no ground truth), IMDAA/INSAT integration (both still blocked on credentials this sandbox cannot obtain), and a genuine held-out evaluation on pan-India data before any promotion decision could even be considered. **This pass does not move that forward** — it only validates whether the existing MTL architecture is technically trainable and competitive at the one location where real labels exist.

## 15. What Was Not Done

No labels fabricated. No IMDAA/INSAT data fabricated (still not obtained — unchanged from the prior pass). No pan-India dataset invented. `backend/mtl_backbone.py` was not modified — only imported. Production XGBoost inference, `forecast_action.py`, pan-India hazard scoring, the frontend, deployment workflows, and the alert system were **not touched** by this pass. No training was run on your A100 from this session — physically not possible from here, and not attempted.

## 16. Files Created / Modified

**Created** (all under `colab/`, all real, all either run or syntax-verified):
`build_blr_dataset.py`, `train_mtl_blr.py`, `evaluate_mtl_blr.py`, `evaluate_xgb_production_baseline.py`, `DRIFT_MTL_BLR_Colab.ipynb`, `requirements_colab.txt`, plus generated data: `training/{train,val,test}.npz`, `training/feature_manifest.json`, `training/splits.json`, `training/class_weights.json`, `training/leakage_report.json`, `evaluation/production_baseline_metrics.json`.

**Modified:** none. No production file was touched.

## 17. Tests Performed In This Pass

- `build_blr_dataset.py` — **actually executed** against the real CSV; output verified against manual `pandas` checks (row counts, date ranges, positive rates all cross-checked).
- `evaluate_xgb_production_baseline.py` — **actually executed**, loading real production `.pkl`/`.json` model artifacts and real calibrators, scoring the real held-out test rows.
- `train_mtl_blr.py`, `evaluate_mtl_blr.py`, the notebook — **not executable here** (no torch/GPU in this sandbox); verified with `ast.parse` (syntax-clean) and manual review of the import path against `backend/mtl_backbone.py`'s actual class/function names and signatures (confirmed to match, e.g. `MTLHazardModel(n_features=...)`, `FEATURE_MEAN`, `FEATURE_STD`, `N_FEATURES` all exist exactly as imported).
- Notebook JSON validity — re-parsed with `json.load`, confirmed structurally valid `.ipynb`.

## What You Must Run Manually On The A100

Steps 2–7 in §13 above — dependency install, GPU verification, `train_mtl_blr.py`, `evaluate_mtl_blr.py` — none of that has run yet. Bring back `colab/training/runs/` and `colab/evaluation/mtl_test_metrics.json` and I'll fill in the comparison table in §9 with your real numbers, honestly, whichever way they land.
