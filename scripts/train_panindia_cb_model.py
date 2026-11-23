#!/usr/bin/env python3
"""
scripts/train_panindia_cb_model.py

Phase 21 -- reproducible training/validation pipeline for the first real,
learned pan-India cloudburst (CB) model, built from the Phase 20 historical
GFS + IMD dataset. This is the first real CB component intended to become
one head of the eventual shared-encoder TS/CB/FF architecture -- it is NOT
a heuristic score and NOT a claim of a unified multi-task model.

Pipeline stages (kept as separate, individually callable functions, per
the Phase 21 instruction):
  1. load_data()           -- scripts/panindia_cb_features.load_full_dataset
  2. filter_labels()        -- excludes MISSING rows from supervised training
  3. engineer_features()    -- scripts/panindia_cb_features.engineer_daily_features
  4. build_splits()         -- grouped leave-one-date-out (LODO), 10 folds
  5. train_model()          -- XGBoost, scale_pos_weight for imbalance
  6. calibrate()            -- isotonic calibration on held-out-date predictions
  7. evaluate()             -- AUROC, PR-AUC, POD, FAR, Precision, CSI, HSS, Brier
  8. save_artifacts()       -- models/panindia_cb_v1/ (does NOT touch models/cb_slot_*)

CRITICAL: only 10 independent target dates exist. Validation is grouped by
date (LODO) throughout -- a naive random row split is never used as a
headline result anywhere in this module.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (roc_auc_score, average_precision_score,
                              brier_score_loss, confusion_matrix)
from sklearn.isotonic import IsotonicRegression
import xgboost as xgb

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from panindia_cb_features import (load_full_dataset, engineer_daily_features,
                                   get_feature_columns, FEATURE_DOCS)

ARTIFACT_DIR = REPO_ROOT / "models/panindia_cb_v1"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

ALL_CYCLES = [
    "2015022718", "2016010918", "2016011418", "2016070218", "2018020818",
    "2018071518", "2019120818", "2020081218", "2022071318", "2024073118",
]


def load_data() -> pd.DataFrame:
    """Stage 1."""
    return load_full_dataset()


def filter_labels(feat_df: pd.DataFrame) -> pd.DataFrame:
    """Stage 2. Excludes MISSING rows from supervised binary training.
    MISSING rows are NEVER treated as negative -- they are dropped here,
    not coerced. Returns only POSITIVE/CONFIRMED_NEGATIVE rows."""
    labeled = feat_df[feat_df["label_status"].isin(["POSITIVE", "CONFIRMED_NEGATIVE"])].copy()
    n_missing_excluded = (feat_df["label_status"] == "MISSING").sum()
    print(f"[filter_labels] excluded {n_missing_excluded} MISSING rows; "
          f"retained {len(labeled)} labeled rows "
          f"(positive={int((labeled.cb_label_daily==1).sum())}, "
          f"negative={int((labeled.cb_label_daily==0).sum())})", file=sys.stderr)
    return labeled


def engineer_features(raw_df: pd.DataFrame, include_prate: bool, include_9000pa: bool) -> pd.DataFrame:
    """Stage 3."""
    return engineer_daily_features(raw_df, include_prate=include_prate, include_9000pa=include_9000pa)


def build_splits(labeled_df: pd.DataFrame) -> list[dict]:
    """Stage 4. Grouped leave-one-date-out: for each of the (up to) 10
    target dates present in labeled_df, train on the other 9, validate on
    the held-out date. A date with zero positive OR zero negative labeled
    rows cannot be meaningfully validated (AUROC undefined) and is
    reported as such, not silently skipped without explanation."""
    dates = sorted(labeled_df["target_date"].unique())
    splits = []
    for held_out in dates:
        train_mask = labeled_df["target_date"] != held_out
        test_mask = labeled_df["target_date"] == held_out
        test_labels = labeled_df.loc[test_mask, "cb_label_daily"]
        splits.append({
            "held_out_date": held_out,
            "train_idx": labeled_df.index[train_mask],
            "test_idx": labeled_df.index[test_mask],
            "n_train": int(train_mask.sum()),
            "n_test": int(test_mask.sum()),
            "n_test_positive": int((test_labels == 1).sum()),
            "n_test_negative": int((test_labels == 0).sum()),
            "degenerate": (test_labels == 1).sum() == 0 or (test_labels == 0).sum() == 0,
        })
    return splits


def train_model(X_train: pd.DataFrame, y_train: pd.Series) -> xgb.XGBClassifier:
    """Stage 5. XGBoost with scale_pos_weight for class imbalance
    (never naive oversampling of spatially correlated rows)."""
    n_pos = int((y_train == 1).sum())
    n_neg = int((y_train == 0).sum())
    spw = n_neg / max(n_pos, 1)
    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=spw, eval_metric="logloss",
        random_state=42, missing=np.nan,
    )
    model.fit(X_train, y_train)
    return model


def calibrate(model: xgb.XGBClassifier, X_cal: pd.DataFrame, y_cal: pd.Series) -> IsotonicRegression | None:
    """Stage 6. Isotonic calibration using held-out-date predictions
    pooled across all LODO folds (never calibrated on training-fold data,
    which would be circular)."""
    if y_cal.nunique() < 2:
        return None
    raw_probs = model.predict_proba(X_cal)[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(raw_probs, y_cal)
    return iso


def _pod(tp, fn):
    return tp / (tp + fn) if (tp + fn) > 0 else float("nan")


def _far(fp, tp):
    return fp / (fp + tp) if (fp + tp) > 0 else float("nan")


def _precision(tp, fp):
    return tp / (tp + fp) if (tp + fp) > 0 else float("nan")


def _csi(tp, fp, fn):
    denom = tp + fp + fn
    return tp / denom if denom > 0 else float("nan")


def _hss(tp, fp, fn, tn):
    n = tp + fp + fn + tn
    expected_correct = ((tp + fn) * (tp + fp) + (tn + fp) * (tn + fn)) / n if n > 0 else 0
    observed_correct = tp + tn
    denom = n - expected_correct
    return (observed_correct - expected_correct) / denom if denom != 0 else float("nan")


def evaluate(y_true: np.ndarray, y_prob: np.ndarray, threshold: float = 0.5) -> dict:
    """Stage 7. Full metric set, always reported together with which
    split produced y_true/y_prob (caller's responsibility to label)."""
    result = {"n": len(y_true), "n_positive": int((y_true == 1).sum()), "n_negative": int((y_true == 0).sum())}
    if y_true.sum() == 0 or y_true.sum() == len(y_true):
        result["auroc"] = None
        result["pr_auc"] = None
        result["note"] = "degenerate split (single class) -- AUROC/PR-AUC undefined"
    else:
        result["auroc"] = float(roc_auc_score(y_true, y_prob))
        result["pr_auc"] = float(average_precision_score(y_true, y_prob))
    result["brier_score"] = float(brier_score_loss(y_true, y_prob))

    y_pred = (y_prob >= threshold).astype(int)
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = cm.ravel()
    result["threshold"] = threshold
    result["confusion_matrix"] = {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}
    result["pod_recall"] = _pod(tp, fn)
    result["far"] = _far(fp, tp)
    result["precision"] = _precision(tp, fp)
    result["csi"] = _csi(tp, fp, fn)
    result["hss"] = _hss(tp, fp, fn, tn)
    return result


def save_artifacts(model: xgb.XGBClassifier, calibrator, feature_cols: list[str],
                    metrics: dict, name: str) -> dict:
    """Stage 8. Saves under models/panindia_cb_v1/ -- never touches the
    existing VOBL models/cb_slot_*.json/pkl artifacts."""
    model_path = ARTIFACT_DIR / f"{name}_model.json"
    model.save_model(str(model_path))
    paths = {"model": str(model_path.relative_to(REPO_ROOT))}
    if calibrator is not None:
        import pickle
        cal_path = ARTIFACT_DIR / f"{name}_calibrator.pkl"
        with open(cal_path, "wb") as f:
            pickle.dump(calibrator, f)
        paths["calibrator"] = str(cal_path.relative_to(REPO_ROOT))
    feat_path = ARTIFACT_DIR / f"{name}_feature_list.json"
    with open(feat_path, "w") as f:
        json.dump(feature_cols, f, indent=2)
    paths["feature_list"] = str(feat_path.relative_to(REPO_ROOT))
    metrics_path = ARTIFACT_DIR / f"{name}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    paths["metrics"] = str(metrics_path.relative_to(REPO_ROOT))
    return paths


def run_lodo_experiment(labeled_df: pd.DataFrame, feature_cols: list[str], experiment_name: str) -> dict:
    """Runs the full leave-one-date-out experiment: trains 10 models (one
    per held-out date), pools out-of-fold predictions, computes per-date
    and pooled metrics. This IS the primary/headline validation result --
    never a random row split."""
    splits = build_splits(labeled_df)
    per_date_results = []
    pooled_y_true = []
    pooled_y_prob = []
    pooled_dates = []
    last_model = None

    for split in splits:
        X_train = labeled_df.loc[split["train_idx"], feature_cols]
        y_train = labeled_df.loc[split["train_idx"], "cb_label_daily"].astype(int)
        X_test = labeled_df.loc[split["test_idx"], feature_cols]
        y_test = labeled_df.loc[split["test_idx"], "cb_label_daily"].astype(int)

        if split["degenerate"]:
            per_date_results.append({**split, "metrics": None,
                                      "note": "held-out date has only one class present; "
                                              "metrics requiring both classes are undefined for this date"})
            continue

        model = train_model(X_train, y_train)
        last_model = model
        y_prob = model.predict_proba(X_test)[:, 1]
        date_metrics = evaluate(y_test.values, y_prob)
        per_date_results.append({**{k: v for k, v in split.items() if k not in ("train_idx", "test_idx")},
                                  "metrics": date_metrics})
        pooled_y_true.extend(y_test.values.tolist())
        pooled_y_prob.extend(y_prob.tolist())
        pooled_dates.extend([split["held_out_date"]] * len(y_test))

    pooled_y_true = np.array(pooled_y_true)
    pooled_y_prob = np.array(pooled_y_prob)
    pooled_metrics = evaluate(pooled_y_true, pooled_y_prob)

    # Calibration: fit isotonic on the pooled out-of-fold predictions
    # (these are genuinely held-out for every row, since each row's
    # prediction came from a model that never saw that row's date).
    if len(np.unique(pooled_y_true)) >= 2:
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(pooled_y_prob, pooled_y_true)
        calibrated_probs = calibrator.predict(pooled_y_prob)
        calibrated_metrics = evaluate(pooled_y_true, calibrated_probs)
    else:
        calibrator = None
        calibrated_metrics = None

    # Train a final full-data model (for artifact/SHAP purposes) on ALL
    # labeled data -- this model is NOT what the headline LODO metrics
    # describe (those describe the 10 per-date held-out models); it is
    # the deployable artifact, trained on everything once LODO has
    # established the expected out-of-sample performance.
    X_all = labeled_df[feature_cols]
    y_all = labeled_df["cb_label_daily"].astype(int)
    final_model = train_model(X_all, y_all)

    return {
        "experiment_name": experiment_name,
        "feature_cols": feature_cols,
        "n_labeled_rows": len(labeled_df),
        "per_date_results": per_date_results,
        "pooled_metrics_uncalibrated": pooled_metrics,
        "pooled_metrics_calibrated": calibrated_metrics,
        "final_model": final_model,
        "calibrator": calibrator,
        "pooled_y_true": pooled_y_true,
        "pooled_y_prob": pooled_y_prob,
        "pooled_dates": pooled_dates,
    }
