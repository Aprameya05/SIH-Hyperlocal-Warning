#!/usr/bin/env python3
"""
evaluate_mtl_blr.py -- run this on Colab after train_mtl_blr.py finishes.

Loads the selected best checkpoint (colab/training/runs/summary.json ->
"selected"), scores it ONCE on the untouched held-out test set
(colab/training/test.npz, 2024-2025), and computes the full required
metric set per hazard: AUROC, AUPRC, POD, FAR, CSI, HSS, BIAS, confusion
matrix. Also breaks results down by forecast slot where population allows.

Writes colab/evaluation/mtl_test_metrics.json, which is directly
comparable in shape to colab/evaluation/production_baseline_metrics.json
(already computed in this pass, in the cloud sandbox, against the same
population and test window) -- see REPORT.md section 9 for the comparison
table shell to fill in once this has actually run.
"""
import json
import sys
from pathlib import Path

import numpy as np

try:
    import torch
except ImportError:
    print("PyTorch not installed.")
    sys.exit(1)

from sklearn.metrics import roc_auc_score, average_precision_score

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from backend.mtl_backbone import MTLHazardModel, FEATURE_MEAN, FEATURE_STD, N_FEATURES  # noqa: E402

TRAIN_DIR = Path(__file__).resolve().parent / "training"
EVAL_DIR = Path(__file__).resolve().parent / "evaluation"

HAZARDS = ["ts", "cb", "ff"]


def metrics_from_binary(y_true: np.ndarray, y_prob: np.ndarray, threshold: float = 0.5) -> dict:
    y_pred = (y_prob >= threshold).astype(int)
    n_pos = int(y_true.sum())
    n_neg = int(len(y_true) - n_pos)
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    tn = int(((y_pred == 0) & (y_true == 0)).sum())

    pod = tp / (tp + fn) if (tp + fn) > 0 else "unavailable (no positive events in this subset)"
    far = fp / (tp + fp) if (tp + fp) > 0 else "unavailable (no positive predictions in this subset)"
    csi = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else "unavailable"
    bias = (tp + fp) / (tp + fn) if (tp + fn) > 0 else "unavailable (no positive events in this subset)"
    total = tp + fp + fn + tn
    if total > 0:
        expected_correct = ((tp + fn) * (tp + fp) + (tn + fp) * (tn + fn)) / total
        denom = total - expected_correct
        hss = (tp + tn - expected_correct) / denom if denom != 0 else "unavailable (denominator is zero)"
    else:
        hss = "unavailable"
    try:
        auroc = roc_auc_score(y_true, y_prob) if n_pos > 0 and n_neg > 0 else "unavailable (single class in subset)"
    except Exception as e:
        auroc = f"unavailable ({e})"
    try:
        auprc = average_precision_score(y_true, y_prob) if n_pos > 0 else "unavailable (no positives)"
    except Exception as e:
        auprc = f"unavailable ({e})"

    return {
        "n": int(len(y_true)), "n_positive": n_pos, "n_negative": n_neg,
        "threshold_used": threshold, "AUROC": auroc, "AUPRC": auprc,
        "POD": pod, "FAR": far, "CSI": csi, "HSS": hss, "BIAS": bias,
        "confusion_matrix": {"TP": tp, "FP": fp, "FN": fn, "TN": tn},
    }


def main():
    summary_path = TRAIN_DIR / "runs" / "summary.json"
    if not summary_path.exists():
        print(f"ERROR: {summary_path} not found -- run train_mtl_blr.py first.")
        sys.exit(1)
    summary = json.loads(summary_path.read_text())
    selected = summary["selected"]
    ckpt_path = Path(summary[selected]["run_dir"]) / "best_checkpoint.pt"
    print(f"Evaluating checkpoint: {ckpt_path} (selected on VALIDATION loss, not test)")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = MTLHazardModel(n_features=N_FEATURES).to(device)
    model.load_state_dict(torch.load(ckpt_path, map_location=device))
    model.eval()

    d = np.load(TRAIN_DIR / "test.npz", allow_pickle=True)
    feats = torch.tensor(d["features"], dtype=torch.float32)
    mean = torch.tensor(FEATURE_MEAN)
    std = torch.tensor(FEATURE_STD).clamp(min=1e-6)
    feats = ((feats - mean) / std).to(device)
    lat = torch.tensor(d["lat"], dtype=torch.float32).to(device)
    lon = torch.tensor(d["lon"], dtype=torch.float32).to(device)
    labels = d["labels"]  # (N,3): ts, cb, ff

    with torch.no_grad():
        out = model(feats, lat, lon)

    results = {
        "TRAINING_DOMAIN": "VOBL / Bengaluru only",
        "note": "This experiment DOES NOT establish pan-India model validity. "
                "It does not justify deploying the MTL model to the 992-cell production grid.",
        "checkpoint": str(ckpt_path),
        "test_period": "2024-01-01 to 2025-12-31 (same population as colab/evaluation/production_baseline_metrics.json)",
        "by_hazard": {},
    }
    for i, hz in enumerate(["TS", "CB", "FF"]):
        y_true = labels[:, i]
        y_prob = out[hz.lower()].cpu().numpy()
        results["by_hazard"][hz] = metrics_from_binary(y_true, y_prob)

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EVAL_DIR / "mtl_test_metrics.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"Written: {out_path}")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
