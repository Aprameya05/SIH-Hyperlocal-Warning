"""
train_cb_ff_models.py
=====================
Trains XGBoost slot models for Cloudburst (CB) and Flash Flood (FF)
using the same architecture as the existing TS slot models.

Input:
  data/bengaluru_6hr_training_dataset_cb_ff.csv

Output:
  models/cb_slot_0_model.json  ...  models/cb_slot_3_model.json
  models/ff_slot_0_model.json  ...  models/ff_slot_3_model.json
  models/cb_slot_0_calibrator.pkl ... models/cb_slot_3_calibrator.pkl
  models/ff_slot_0_calibrator.pkl ... models/ff_slot_3_calibrator.pkl
  models/cb_ff_feature_list.json

Usage:
  python train_cb_ff_models.py

Requires:
  pip install xgboost scikit-learn pandas numpy
"""

import os
import json
import pickle
import warnings
import numpy as np
import pandas as pd
from datetime import datetime

import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import (
    roc_auc_score, average_precision_score,
    f1_score, precision_score, recall_score,
    brier_score_loss
)

warnings.filterwarnings('ignore')

# ── Config ────────────────────────────────────────────────────────────────────

DATA_CSV   = "data/bengaluru_6hr_training_dataset_cb_ff.csv"
MODEL_DIR  = "models"
os.makedirs(MODEL_DIR, exist_ok=True)

SLOTS        = [0, 1, 2, 3]
SLOT_COL     = "slot"           # column identifying 0/1/2/3
DATE_COL     = "date"
CB_LABEL_COL = "cb_label"
FF_LABEL_COL = "ff_label"
TS_LABEL_COL = "ts_label"

# Features to exclude from training (labels, IDs, dates)
EXCLUDE_COLS = {
    DATE_COL, SLOT_COL,
    CB_LABEL_COL, FF_LABEL_COL, TS_LABEL_COL,
    "imd_rf_mm",    # derived label source, not a forecast feature
}

# Temporal split: train on everything before cutoff, test after
TRAIN_CUTOFF = "2023-12-31"   # ~80/20 split

# XGBoost base params (same family as TS models)
XGB_BASE_PARAMS = {
    "objective":        "binary:logistic",
    "eval_metric":      "aucpr",
    "tree_method":      "hist",
    "device":           "cuda",          # A100 -- falls back to cpu automatically if unavailable
    "n_estimators":     800,
    "learning_rate":    0.03,
    "max_depth":        6,
    "subsample":        0.8,
    "colsample_bytree": 0.7,
    "min_child_weight": 5,
    "gamma":            1.0,
    "reg_alpha":        0.1,
    "reg_lambda":       1.0,
    "random_state":     42,
    "verbosity":        0,
    "early_stopping_rounds": 50,
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_data():
    df = pd.read_csv(DATA_CSV)
    df[DATE_COL] = pd.to_datetime(df[DATE_COL])
    print(f"Loaded: {df.shape[0]} rows, {df.shape[1]} cols")
    print(f"  CB positives: {df[CB_LABEL_COL].sum()}  "
          f"({df[CB_LABEL_COL].mean()*100:.2f}%)")
    print(f"  FF positives: {df[FF_LABEL_COL].sum()}  "
          f"({df[FF_LABEL_COL].mean()*100:.2f}%)")
    print(f"  TS positives: {df[TS_LABEL_COL].sum()}  "
          f"({df[TS_LABEL_COL].mean()*100:.2f}%)")
    return df


def get_feature_cols(df):
    return [c for c in df.columns if c not in EXCLUDE_COLS]


def temporal_split(df, slot):
    slot_df = df[df[SLOT_COL] == slot].copy()
    train = slot_df[slot_df[DATE_COL] <= TRAIN_CUTOFF]
    test  = slot_df[slot_df[DATE_COL] >  TRAIN_CUTOFF]
    return train, test


def isotonic_calibrate(model, X_cal, y_cal, X_test):
    """Fit isotonic regression on calibration probabilities."""
    raw_cal  = model.predict_proba(X_cal)[:, 1]
    raw_test = model.predict_proba(X_test)[:, 1]
    iso = IsotonicRegression(out_of_bounds='clip')
    iso.fit(raw_cal, y_cal)
    return iso, iso.transform(raw_test)


def print_metrics(label, y_true, y_prob, threshold=0.5):
    y_pred = (y_prob >= threshold).astype(int)
    pos = y_true.sum()
    if pos == 0:
        print(f"  {label}: no positive samples in test set")
        return
    print(f"  {label}:")
    print(f"    AUROC={roc_auc_score(y_true, y_prob):.3f}  "
          f"AUPRC={average_precision_score(y_true, y_prob):.3f}  "
          f"Brier={brier_score_loss(y_true, y_prob):.4f}")
    print(f"    P={precision_score(y_true, y_pred, zero_division=0):.3f}  "
          f"R={recall_score(y_true, y_pred, zero_division=0):.3f}  "
          f"F1={f1_score(y_true, y_pred, zero_division=0):.3f}  "
          f"(threshold={threshold}, pos={int(pos)}/{len(y_true)})")


# ── Training ──────────────────────────────────────────────────────────────────

def train_hazard(df, feature_cols, hazard, label_col):
    """Train 4 slot models for one hazard (CB or FF)."""
    print(f"\n{'='*60}")
    print(f"Training {hazard.upper()} models")
    print(f"{'='*60}")

    all_metrics = []

    for slot in SLOTS:
        print(f"\n-- Slot {slot} --")
        train, test = temporal_split(df, slot)

        X_tr = train[feature_cols].fillna(0).values
        y_tr = train[label_col].values
        X_te = test[feature_cols].fillna(0).values
        y_te = test[label_col].values

        n_pos = y_tr.sum()
        n_neg = len(y_tr) - n_pos
        spw   = max(1.0, n_neg / max(1, n_pos))
        print(f"  Train: {len(X_tr)} rows, pos={int(n_pos)}, neg={int(n_neg)}, "
              f"scale_pos_weight={spw:.1f}")
        print(f"  Test:  {len(X_te)} rows, pos={int(y_te.sum())}")

        params = {**XGB_BASE_PARAMS, "scale_pos_weight": spw}

        # Use 20% of train as internal eval set for early stopping
        split_idx = int(len(X_tr) * 0.8)
        X_fit, X_val = X_tr[:split_idx], X_tr[split_idx:]
        y_fit, y_val = y_tr[:split_idx], y_tr[split_idx:]

        model = xgb.XGBClassifier(**params)
        model.fit(
            X_fit, y_fit,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )

        # Isotonic calibration on the held-out val set
        iso, cal_probs_test = isotonic_calibrate(model, X_val, y_val, X_te)

        # Save model
        model_path = os.path.join(MODEL_DIR, f"{hazard}_slot_{slot}_model.json")
        model.save_model(model_path)
        print(f"  Saved model -> {model_path}")

        # Save calibrator
        cal_path = os.path.join(MODEL_DIR, f"{hazard}_slot_{slot}_calibrator.pkl")
        with open(cal_path, 'wb') as f:
            pickle.dump(iso, f)
        print(f"  Saved calibrator -> {cal_path}")

        # Metrics
        if y_te.sum() > 0:
            raw_probs = model.predict_proba(X_te)[:, 1]
            print_metrics(f"raw", y_te, raw_probs)
            print_metrics(f"calibrated", y_te, cal_probs_test)
            slot_metrics = {
                "slot": slot,
                "auroc": roc_auc_score(y_te, cal_probs_test),
                "auprc": average_precision_score(y_te, cal_probs_test),
            }
            all_metrics.append(slot_metrics)
        else:
            print("  No positive samples in test window for this slot.")
            slot_metrics = {"slot": slot, "auroc": None, "auprc": None}

        # Checkpoint: save progress after every slot so a disconnect loses nothing
        ckpt_path = os.path.join(MODEL_DIR, f"{hazard}_checkpoint.json")
        ckpt_data = {
            "hazard": hazard,
            "completed_slots": [m["slot"] for m in all_metrics],
            "metrics_so_far": all_metrics,
            "saved_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(ckpt_path, 'w') as f:
            json.dump(ckpt_data, f, indent=2)
        print(f"  Checkpoint saved -> {ckpt_path}")

    return all_metrics


# ── Feature importance ────────────────────────────────────────────────────────

def print_top_features(df, feature_cols, label_col, hazard, top_n=15):
    """Quick global feature importance across slots."""
    X = df[feature_cols].fillna(0).values
    y = df[label_col].values
    n_pos = y.sum()
    if n_pos < 5:
        print(f"  Skipping feature importance (only {int(n_pos)} positives)")
        return
    spw = max(1.0, (len(y) - n_pos) / n_pos)
    params = {**XGB_BASE_PARAMS, "scale_pos_weight": spw,
              "n_estimators": 300, "early_stopping_rounds": None}
    m = xgb.XGBClassifier(**params)
    m.fit(X, y, verbose=False)
    imp = pd.Series(m.feature_importances_, index=feature_cols).nlargest(top_n)
    print(f"\n  Top {top_n} features for {hazard.upper()}:")
    for feat, score in imp.items():
        print(f"    {feat:<40} {score:.4f}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    df = load_data()
    feature_cols = get_feature_cols(df)
    print(f"\nFeatures: {len(feature_cols)}")

    # Save feature list (so forecast script knows which cols to use)
    feat_path = os.path.join(MODEL_DIR, "cb_ff_feature_list.json")
    with open(feat_path, 'w') as f:
        json.dump(feature_cols, f, indent=2)
    print(f"Feature list saved -> {feat_path}")

    # Train CB
    cb_metrics = train_hazard(df, feature_cols, "cb", CB_LABEL_COL)

    # Feature importance for CB
    print_top_features(df, feature_cols, CB_LABEL_COL, "cb")

    # Train FF
    ff_metrics = train_hazard(df, feature_cols, "ff", FF_LABEL_COL)

    # Feature importance for FF
    print_top_features(df, feature_cols, FF_LABEL_COL, "ff")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print("\nCB slot metrics (calibrated, test set):")
    for m in cb_metrics:
        print(f"  slot {m['slot']}: AUROC={m['auroc']:.3f}  AUPRC={m['auprc']:.3f}")
    print("\nFF slot metrics (calibrated, test set):")
    for m in ff_metrics:
        print(f"  slot {m['slot']}: AUROC={m['auroc']:.3f}  AUPRC={m['auprc']:.3f}")

    # Save summary JSON so we have a persistent record
    summary = {
        "trained_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "n_features": len(feature_cols),
        "train_cutoff": TRAIN_CUTOFF,
        "cb_threshold_mm": 64.5,
        "ff_proxy": "3d_cumsum>=100_and_daily>=40",
        "cb_slot_metrics": cb_metrics,
        "ff_slot_metrics": ff_metrics,
    }
    summary_path = os.path.join(MODEL_DIR, "cb_ff_training_summary.json")
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved -> {summary_path}")

    print(f"\nDone: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\nNext step: update forecast_action.py to load and score these models.")


if __name__ == "__main__":
    main()
