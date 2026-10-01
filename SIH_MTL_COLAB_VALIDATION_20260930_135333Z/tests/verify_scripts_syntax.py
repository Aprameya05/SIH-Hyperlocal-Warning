"""Re-runnable syntax/import-path sanity check for the colab/ package."""
import ast
from pathlib import Path

files = [
    "colab/build_blr_dataset.py",
    "colab/train_mtl_blr.py",
    "colab/evaluate_mtl_blr.py",
    "colab/evaluate_xgb_production_baseline.py",
]
for f in files:
    ast.parse(Path(f).read_text())
    print(f"OK: {f}")

import json
nb = json.load(open("colab/DRIFT_MTL_BLR_Colab.ipynb"))
assert nb["nbformat"] == 4
print("OK: DRIFT_MTL_BLR_Colab.ipynb is valid notebook JSON")
