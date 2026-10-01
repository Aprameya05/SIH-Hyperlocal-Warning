#!/usr/bin/env python3
"""
train_mtl_blr.py -- run this on Google Colab (A100), NOT in the Claude sandbox.

Trains backend/mtl_backbone.py's existing MTLHazardModel architecture
(unchanged -- imported, not rewritten) on the real BLR multi-task dataset
built by build_blr_dataset.py. Runs a small, documented experiment matrix
(A: baseline BCE, B: class-weighted BCE) and keeps the best checkpoint by
VALIDATION performance only (never test).

This does NOT touch backend/mtl_backbone.py, forecast_action.py, or any
production file. It only imports MTLHazardModel from mtl_backbone.py.

Prerequisites (see colab/DRIFT_MTL_BLR_Colab.ipynb for the full sequence):
    1. Upload/clone the repo so that backend/mtl_backbone.py and
       colab/training/{train,val,test}.npz exist.
    2. pip install torch (Colab already has it for GPU runtimes)
    3. Runtime -> Change runtime type -> A100 GPU
    4. python3 colab/build_blr_dataset.py   (if not already run)
    5. python3 colab/train_mtl_blr.py

Output (under colab/training/runs/<experiment>/):
    best_checkpoint.pt
    training_curve.json
    run_metadata.json
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
except ImportError:
    print("PyTorch not installed. On Colab: this should already be present for a GPU runtime.")
    print("Locally: pip install torch")
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from backend.mtl_backbone import MTLHazardModel, FEATURE_MEAN, FEATURE_STD, N_FEATURES  # noqa: E402

TRAIN_DIR = Path(__file__).resolve().parent / "training"
RUNS_DIR = TRAIN_DIR / "runs"

SEED = 1337
EPOCHS = 60
BATCH_SIZE = 128
LR = 1e-3
PATIENCE = 10  # early stopping on val loss

TRAINING_DOMAIN = "VOBL / Bengaluru only"


def set_seed(seed=SEED):
    torch.manual_seed(seed)
    np.random.seed(seed)


def load_split(name):
    d = np.load(TRAIN_DIR / f"{name}.npz", allow_pickle=True)
    feats = torch.tensor(d["features"], dtype=torch.float32)
    mean = torch.tensor(FEATURE_MEAN)
    std = torch.tensor(FEATURE_STD).clamp(min=1e-6)
    feats = (feats - mean) / std
    labels = torch.tensor(d["labels"], dtype=torch.float32)  # (N,3): ts, cb, ff
    lat = torch.tensor(d["lat"], dtype=torch.float32)
    lon = torch.tensor(d["lon"], dtype=torch.float32)
    return feats, lat, lon, labels


def run_experiment(name: str, pos_weights: dict, device):
    """pos_weights: {'ts':float,'cb':float,'ff':float} or None per key for unweighted."""
    set_seed()
    train_feats, train_lat, train_lon, train_labels = load_split("train")
    val_feats, val_lat, val_lon, val_labels = load_split("val")

    train_ds = TensorDataset(train_feats, train_lat, train_lon, train_labels)
    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, generator=torch.Generator().manual_seed(SEED))

    model = MTLHazardModel(n_features=N_FEATURES).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)

    # BCEWithLogitsLoss needs raw logits, but MTLHazardModel's heads apply
    # Sigmoid internally (see backend/mtl_backbone.py HazardHead). To add
    # class weighting WITHOUT modifying the architecture, we invert the
    # sigmoid output back to a logit before applying BCEWithLogitsLoss.
    # This is mathematically equivalent to weighted BCE on probabilities
    # and does not change the model's forward pass or parameters.
    def to_logit(p, eps=1e-6):
        p = p.clamp(eps, 1 - eps)
        return torch.log(p / (1 - p))

    pw = {k: (torch.tensor([v]).to(device) if v else None) for k, v in (pos_weights or {}).items()}
    loss_fns = {
        k: nn.BCEWithLogitsLoss(pos_weight=pw.get(k)) for k in ("ts", "cb", "ff")
    }

    history = []
    best_val_loss = float("inf")
    best_state = None
    epochs_no_improve = 0

    for epoch in range(1, EPOCHS + 1):
        model.train()
        total_loss = 0.0
        for feats, lat, lon, labels in train_loader:
            feats, lat, lon, labels = feats.to(device), lat.to(device), lon.to(device), labels.to(device)
            out = model(feats, lat, lon)
            loss = (
                loss_fns["ts"](to_logit(out["ts"]), labels[:, 0]) +
                loss_fns["cb"](to_logit(out["cb"]), labels[:, 1]) +
                loss_fns["ff"](to_logit(out["ff"]), labels[:, 2])
            )
            opt.zero_grad()
            loss.backward()
            opt.step()
            total_loss += loss.item() * feats.size(0)
        train_loss = total_loss / len(train_ds)

        model.eval()
        with torch.no_grad():
            vf, vlat, vlon, vl = val_feats.to(device), val_lat.to(device), val_lon.to(device), val_labels.to(device)
            out = model(vf, vlat, vlon)
            val_loss = (
                loss_fns["ts"](to_logit(out["ts"]), vl[:, 0]) +
                loss_fns["cb"](to_logit(out["cb"]), vl[:, 1]) +
                loss_fns["ff"](to_logit(out["ff"]), vl[:, 2])
            ).item()

        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})
        print(f"[{name}] epoch {epoch:3d}/{EPOCHS}  train_loss={train_loss:.4f}  val_loss={val_loss:.4f}")

        if val_loss < best_val_loss - 1e-5:
            best_val_loss = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            epochs_no_improve = 0
        else:
            epochs_no_improve += 1
            if epochs_no_improve >= PATIENCE:
                print(f"[{name}] early stopping at epoch {epoch} (no val improvement for {PATIENCE} epochs)")
                break

    run_dir = RUNS_DIR / name
    run_dir.mkdir(parents=True, exist_ok=True)
    torch.save(best_state, run_dir / "best_checkpoint.pt")
    (run_dir / "training_curve.json").write_text(json.dumps(history, indent=2))
    metadata = {
        "experiment": name,
        "TRAINING_DOMAIN": TRAINING_DOMAIN,
        "note": "This experiment DOES NOT establish pan-India model validity. "
                "It does not justify deploying the MTL model to the 992-cell production grid.",
        "seed": SEED,
        "epochs_run": len(history),
        "best_val_loss": best_val_loss,
        "pos_weights": pos_weights,
        "device": str(device),
        "architecture": "backend.mtl_backbone.MTLHazardModel (unmodified)",
        "feature_names": ["cape", "cin", "pwat_mm", "k_index", "totals_totals", "wind_shear_ms",
                           "t850", "t700", "t500", "td850", "td700", "ctt_c"],
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (run_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2))
    print(f"[{name}] saved to {run_dir}")
    return best_val_loss, run_dir


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")
    else:
        print("WARNING: no GPU detected -- this will be slow. On Colab, set Runtime > Change runtime type > A100 GPU.")

    weights = json.loads((TRAIN_DIR / "class_weights.json").read_text())
    pw = {
        "ts": weights["ts_label"]["pos_weight"],
        "cb": weights["cb_label"]["pos_weight"],
        "ff": weights["ff_label"]["pos_weight"],
    }

    results = {}
    results["A_baseline"] = run_experiment("A_baseline", pos_weights=None, device=device)
    results["B_class_weighted"] = run_experiment("B_class_weighted", pos_weights=pw, device=device)
    # C_focal_loss intentionally not run by default -- add only if A/B show the
    # weighted-BCE approach is insufficient; the smallest scientifically
    # defensible approach (class-weighted BCE) is tried first, per instructions.

    best_name = min(results, key=lambda k: results[k][0])
    print(f"\nBest experiment by validation loss: {best_name} (val_loss={results[best_name][0]:.4f})")
    (RUNS_DIR / "summary.json").write_text(json.dumps(
        {k: {"best_val_loss": v[0], "run_dir": str(v[1])} for k, v in results.items()} | {"selected": best_name},
        indent=2,
    ))


if __name__ == "__main__":
    main()
