"""
A6_calibration.py
=================
Probability calibration for all 4 per-slot XGBoost v2 models.

Raw XGBoost probabilities are often overconfident — a predicted 80%
doesn't necessarily mean 80% of those days actually have thunderstorms.
Calibration fixes this so probabilities are operationally meaningful.

Methods used:
  Slots 0, 1 (few positives): Platt scaling (sigmoid)
  Slots 2, 3 (more positives): Isotonic regression

What this script does:
  1. Generates OOF predictions on training set
  2. Fits calibrator on OOF predictions
  3. Evaluates calibration on test set
  4. Plots reliability diagrams (before vs after calibration)
  5. Saves calibrated model artifacts

Output:
  models/nowcast_slot{0-3}_xgb_v2_calibrated.pkl
  results/calibration_results.csv
  results/shap_figures_v2/calibration_reliability_diagrams.png

Author: Aprameya, CSIR Thunderstorm Project
"""

import pandas as pd
import numpy as np
import joblib
import warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path
from sklearn.model_selection import StratifiedKFold
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from xgboost import XGBClassifier

warnings.filterwarnings('ignore')

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA    = BASE / "data"    / "bengaluru_6hr_training_dataset_v3.csv"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
FIGS    = BASE / "results" / "shap_figures_v2"
FIGS.mkdir(parents=True, exist_ok=True)

SLOT_NAMES = {
    0: "0001-0600 IST",
    1: "0601-1200 IST",
    2: "1201-1800 IST",
    3: "1801-2400 IST",
}
SLOT_COLORS = {0:"#4A90D9", 1:"#27AE60", 2:"#E67E22", 3:"#8E44AD"}

# Calibration method per slot
CALIB_METHOD = {0:'sigmoid', 1:'sigmoid', 2:'isotonic', 3:'isotonic'}

N_FOLDS      = 5
RANDOM_STATE = 42

# ── LOAD DATA ─────────────────────────────────────────────────────────────────
print("=" * 60)
print("A6 — Probability Calibration")
print("=" * 60)

df = pd.read_csv(DATA, parse_dates=['date'])
all_results = []

# ── PER-SLOT CALIBRATION ──────────────────────────────────────────────────────
fig, axes = plt.subplots(2, 4, figsize=(18, 9))

for slot_id in range(4):
    slot_name = SLOT_NAMES[slot_id]
    method    = CALIB_METHOD[slot_id]
    print(f"\n{'='*60}")
    print(f"SLOT {slot_id} — {slot_name} [{method}]")
    print(f"{'='*60}")

    # Load v2 model
    artifact     = joblib.load(MODELS / f"nowcast_slot{slot_id}_xgb_v3.pkl")
    model        = artifact['model']
    feature_cols = artifact['feature_cols']
    threshold    = artifact['threshold']

    slot_df    = df[df['slot'] == slot_id].copy()
    train_slot = slot_df[slot_df['year'] < 2023]
    test_slot  = slot_df[slot_df['year'] >= 2023]

    X_train = train_slot[feature_cols].values
    y_train = train_slot['ts_label'].values
    X_test  = test_slot[feature_cols].values
    y_test  = test_slot['ts_label'].values

    # ── Step 1: Generate OOF predictions ──────────────────────────────────────
    print("  Generating OOF predictions...")
    skf      = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_STATE)
    oof_prob = np.zeros(len(y_train))

    best_params = {k: v for k, v in artifact['best_params'].items()
                   if k != 'early_stopping_rounds'}

    for tr_idx, val_idx in skf.split(X_train, y_train):
        m = XGBClassifier(**best_params, verbosity=0)
        m.fit(X_train[tr_idx], y_train[tr_idx])
        oof_prob[val_idx] = m.predict_proba(X_train[val_idx])[:, 1]

    # ── Step 2: Raw test predictions ──────────────────────────────────────────
    raw_test_prob = model.predict_proba(X_test)[:, 1]

    # ── Step 3: Fit calibrator on OOF ─────────────────────────────────────────
    print(f"  Fitting {method} calibrator on OOF predictions...")
    if method == 'sigmoid':
        calibrator = LogisticRegression(C=1.0)
        calibrator.fit(oof_prob.reshape(-1, 1), y_train)
        calib_test_prob = calibrator.predict_proba(
            raw_test_prob.reshape(-1, 1))[:, 1]
        calib_oof_prob  = calibrator.predict_proba(
            oof_prob.reshape(-1, 1))[:, 1]
    else:  # isotonic
        calibrator = IsotonicRegression(out_of_bounds='clip')
        calibrator.fit(oof_prob, y_train)
        calib_test_prob = calibrator.predict(raw_test_prob)
        calib_oof_prob  = calibrator.predict(oof_prob)

    # ── Step 4: Evaluate calibration ──────────────────────────────────────────
    if y_test.sum() > 0:
        brier_raw   = brier_score_loss(y_test, raw_test_prob)
        brier_calib = brier_score_loss(y_test, calib_test_prob)
        auroc_raw   = roc_auc_score(y_test, raw_test_prob)
        auroc_calib = roc_auc_score(y_test, calib_test_prob)
    else:
        brier_raw = brier_calib = auroc_raw = auroc_calib = float('nan')

    print(f"  Brier score: {brier_raw:.4f} → {brier_calib:.4f} "
          f"({'↓ better' if brier_calib < brier_raw else '↑ worse'})")
    print(f"  AUROC:       {auroc_raw:.4f} → {auroc_calib:.4f}")

    # ── Step 5: Reliability diagram ───────────────────────────────────────────
    ax_raw   = axes[0, slot_id]
    ax_calib = axes[1, slot_id]

    n_bins = 5 if y_test.sum() < 20 else 8

    # Raw reliability
    if y_test.sum() >= 3:
        frac_pos_raw, mean_pred_raw = calibration_curve(
            y_test, raw_test_prob, n_bins=n_bins, strategy='quantile')
        ax_raw.plot(mean_pred_raw, frac_pos_raw, 's-',
                    color=SLOT_COLORS[slot_id], linewidth=2, markersize=7,
                    label='Model')
    ax_raw.plot([0,1],[0,1],'k--', linewidth=1, alpha=0.6, label='Perfect')
    ax_raw.set_title(f"Slot {slot_id} — Raw\n{slot_name}", fontsize=9)
    ax_raw.set_xlabel("Mean predicted prob", fontsize=8)
    ax_raw.set_ylabel("Fraction positive", fontsize=8)
    ax_raw.legend(fontsize=7)
    ax_raw.grid(alpha=0.3)
    ax_raw.set_xlim(0,1); ax_raw.set_ylim(0,1)
    if not np.isnan(brier_raw):
        ax_raw.text(0.05, 0.92, f"Brier={brier_raw:.3f}",
                    transform=ax_raw.transAxes, fontsize=8)

    # Calibrated reliability
    if y_test.sum() >= 3:
        frac_pos_cal, mean_pred_cal = calibration_curve(
            y_test, calib_test_prob, n_bins=n_bins, strategy='quantile')
        ax_calib.plot(mean_pred_cal, frac_pos_cal, 's-',
                      color=SLOT_COLORS[slot_id], linewidth=2, markersize=7,
                      label='Calibrated')
    ax_calib.plot([0,1],[0,1],'k--', linewidth=1, alpha=0.6, label='Perfect')
    ax_calib.set_title(f"Slot {slot_id} — Calibrated [{method}]\n{slot_name}",
                       fontsize=9)
    ax_calib.set_xlabel("Mean predicted prob", fontsize=8)
    ax_calib.set_ylabel("Fraction positive", fontsize=8)
    ax_calib.legend(fontsize=7)
    ax_calib.grid(alpha=0.3)
    ax_calib.set_xlim(0,1); ax_calib.set_ylim(0,1)
    if not np.isnan(brier_calib):
        ax_calib.text(0.05, 0.92, f"Brier={brier_calib:.3f}",
                      transform=ax_calib.transAxes, fontsize=8)

    # ── Step 6: Save calibrated artifact ──────────────────────────────────────
    calibrated_artifact = {
        'model':        model,
        'calibrator':   calibrator,
        'calib_method': method,
        'feature_cols': feature_cols,
        'threshold':    threshold,
        'slot_id':      slot_id,
        'slot_name':    slot_name,
        'era5_version': '6hourly',
    }
    out_path = MODELS / f"nowcast_slot{slot_id}_xgb_v3_calibrated.pkl"
    joblib.dump(calibrated_artifact, out_path)
    print(f"  Saved → {out_path}")

    all_results.append({
        'slot':         slot_id,
        'slot_label':   slot_name,
        'method':       method,
        'brier_raw':    round(brier_raw, 4),
        'brier_calib':  round(brier_calib, 4),
        'brier_improvement': round(brier_raw - brier_calib, 4),
        'auroc_raw':    round(auroc_raw, 4),
        'auroc_calib':  round(auroc_calib, 4),
    })

# ── SAVE FIGURE ───────────────────────────────────────────────────────────────
plt.suptitle("Reliability Diagrams — Before vs After Calibration\n"
             "Top row: Raw XGBoost | Bottom row: Calibrated",
             fontsize=12, y=1.01)
plt.tight_layout()
fig_path = FIGS / "calibration_reliability_diagrams_v3.png"
fig.savefig(fig_path, dpi=150, bbox_inches='tight')
plt.close()
print(f"\nReliability diagrams saved → {fig_path}")

# ── SUMMARY ───────────────────────────────────────────────────────────────────
results_df = pd.DataFrame(all_results)
results_df.to_csv(RESULTS / "calibration_results_v3.csv", index=False)

print("\n" + "="*60)
print("CALIBRATION SUMMARY")
print("="*60)
print(f"\n{'Slot':<6} {'Method':<10} {'Brier Raw':<12} {'Brier Cal':<12} "
      f"{'Improvement':<14} {'AUROC Raw':<12} {'AUROC Cal'}")
print("-"*75)
for _, r in results_df.iterrows():
    arrow = "↓" if r['brier_improvement'] > 0 else "↑"
    print(f"  {int(r['slot']):<5} {r['method']:<10} {r['brier_raw']:<12} "
          f"{r['brier_calib']:<12} {r['brier_improvement']:.4f} {arrow:<10} "
          f"{r['auroc_raw']:<12} {r['auroc_calib']}")

print(f"\nNote: Lower Brier score = better calibration")
print(f"      AUROC should stay same/similar after calibration")
print(f"\nCalibrated models saved to models/nowcast_slot{{0-3}}_xgb_v2_calibrated.pkl")
print("A6 complete.")
