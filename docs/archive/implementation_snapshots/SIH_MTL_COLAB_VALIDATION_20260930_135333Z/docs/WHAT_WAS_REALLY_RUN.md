# What Was Actually Executed vs. What Needs Your A100

## Executed for real, in the Claude sandbox (no GPU/torch needed):
- colab/build_blr_dataset.py -- built training/val/test.npz from the real CSV,
  computed real class weights and a real leakage report.
- colab/evaluate_xgb_production_baseline.py -- loaded the real, deployed
  production XGBoost model files and scored them on the real held-out
  2024-2025 test rows. Results in evaluation/production_baseline_metrics.json.

## NOT executed (needs torch + your A100, on Colab):
- colab/train_mtl_blr.py
- colab/evaluate_mtl_blr.py
These were syntax-checked (ast.parse) and manually verified against
backend/mtl_backbone.py's real class/function signatures, but never run,
because this sandbox has no GPU and pip install torch timed out (no
network route to PyPI's large wheel within this session's constraints).

Run DRIFT_MTL_BLR_Colab.ipynb on Colab with an A100 runtime to get real
MTL numbers, then bring colab/training/runs/ and
colab/evaluation/mtl_test_metrics.json back for the comparison table in
REPORT.md section 9.
