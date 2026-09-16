"""
A2_train_model.py
=================
Trains the 6-hour nowcast XGBoost model on the dataset built by A1.

What this script does:
  1. Loads bengaluru_6hr_training_dataset.csv
  2. Splits into train (2015-2022) and test (2023-2025)
  3. Runs 5-fold stratified cross-validation with Optuna hyperparameter tuning
  4. Trains final model on full training set with best params
  5. Evaluates on test set — AUROC, POD, FAR, CSI, HSS, BIAS
  6. Compares performance to the daily model baseline
  7. Saves model artifact and evaluation results

Author: Aprameya, CSIR Thunderstorm Project
"""

import pandas as pd
import numpy as np
import joblib
import optuna
import warnings
from pathlib import Path
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score
from xgboost import XGBClassifier

warnings.filterwarnings('ignore')
optuna.logging.set_verbosity(optuna.logging.WARNING)

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE     = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA     = BASE / "data" / "bengaluru_6hr_training_dataset.csv"
MODELS   = BASE / "models"
RESULTS  = BASE / "results"
MODELS.mkdir(parents=True, exist_ok=True)
RESULTS.mkdir(parents=True, exist_ok=True)

# Daily model baseline (for comparison)
DAILY_AUROC = 0.8715

# ── CONFIG ────────────────────────────────────────────────────────────────────
N_TRIALS   = 50    # Optuna trials — increase to 100 for final run
N_FOLDS    = 5
THRESHOLD  = 0.45  # starting threshold, will tune per slot
RANDOM_STATE = 42

# ── METRICS ──────────────────────────────────────────────────────────────────
def compute_metrics(y_true, y_prob, threshold):
    y_pred = (y_prob >= threshold).astype(int)
    tp = ((y_pred == 1) & (y_true == 1)).sum()
    fp = ((y_pred == 1) & (y_true == 0)).sum()
    fn = ((y_pred == 0) & (y_true == 1)).sum()
    tn = ((y_pred == 0) & (y_true == 0)).sum()
    pod  = tp / (tp + fn) if (tp + fn) > 0 else 0
    far  = fp / (tp + fp) if (tp + fp) > 0 else 0
    csi  = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0
    bias = (tp + fp) / (tp + fn) if (tp + fn) > 0 else 0
    hss_num = 2 * (tp * tn - fp * fn)
    hss_den = (tp + fn) * (fn + tn) + (tp + fp) * (fp + tn)
    hss  = hss_num / hss_den if hss_den > 0 else 0
    auroc = roc_auc_score(y_true, y_prob)
    return dict(AUROC=round(auroc,4), POD=round(pod,4), FAR=round(far,4),
                CSI=round(csi,4), HSS=round(hss,4), BIAS=round(bias,4))

def find_best_threshold(y_true, y_prob):
    """Find threshold that maximises CSI on given set."""
    best_csi, best_thresh = 0, 0.5
    for t in np.arange(0.1, 0.9, 0.01):
        y_pred = (y_prob >= t).astype(int)
        tp = ((y_pred==1)&(y_true==1)).sum()
        fp = ((y_pred==1)&(y_true==0)).sum()
        fn = ((y_pred==0)&(y_true==1)).sum()
        denom = tp + fp + fn
        csi = tp / denom if denom > 0 else 0
        if csi > best_csi:
            best_csi, best_thresh = csi, t
    return round(best_thresh, 2)

# ── LOAD DATA ─────────────────────────────────────────────────────────────────
print("=" * 60)
print("A2 — XGBoost 6-Hour Nowcast Model Training")
print("=" * 60)

df = pd.read_csv(DATA, parse_dates=['date'])

FEATURE_COLS = [c for c in df.columns if c not in
                ['date','year','month','slot','slot_label','ts_label']]

train = df[df['year'] < 2023].copy()
test  = df[df['year'] >= 2023].copy()

X_train = train[FEATURE_COLS].values
y_train = train['ts_label'].values
X_test  = test[FEATURE_COLS].values
y_test  = test['ts_label'].values

scale_pos = (y_train == 0).sum() / (y_train == 1).sum()

print(f"\nTrain: {len(train)} rows | {y_train.sum()} positives ({y_train.mean()*100:.1f}%)")
print(f"Test : {len(test)} rows  | {y_test.sum()} positives ({y_test.mean()*100:.1f}%)")
print(f"Scale pos weight: {scale_pos:.1f}")
print(f"Features: {len(FEATURE_COLS)}")

# ── OPTUNA OBJECTIVE ──────────────────────────────────────────────────────────
def objective(trial):
    params = {
    'n_estimators':        trial.suggest_int('n_estimators', 100, 600),
    'max_depth':           trial.suggest_int('max_depth', 3, 8),
    'learning_rate':       trial.suggest_float('learning_rate', 0.01, 0.2, log=True),
    'subsample':           trial.suggest_float('subsample', 0.6, 1.0),
    'colsample_bytree':    trial.suggest_float('colsample_bytree', 0.6, 1.0),
    'min_child_weight':    trial.suggest_int('min_child_weight', 1, 10),
    'reg_alpha':           trial.suggest_float('reg_alpha', 1e-4, 10.0, log=True),
    'reg_lambda':          trial.suggest_float('reg_lambda', 1e-4, 10.0, log=True),
    'scale_pos_weight':    scale_pos,
    'random_state':        RANDOM_STATE,
    'eval_metric':         'auc',
    'early_stopping_rounds': 30,
}
    skf    = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_STATE)
    scores = []
    for fold_train_idx, fold_val_idx in skf.split(X_train, y_train):
        Xtr, Xval = X_train[fold_train_idx], X_train[fold_val_idx]
        ytr, yval = y_train[fold_train_idx], y_train[fold_val_idx]
        model = XGBClassifier(**params, verbosity=0)
        model.fit(Xtr, ytr,
          eval_set=[(Xval, yval)],
          verbose=False)
        prob = model.predict_proba(Xval)[:, 1]
        scores.append(roc_auc_score(yval, prob))
    return np.mean(scores)

# ── OPTUNA STUDY ──────────────────────────────────────────────────────────────
print(f"\nRunning Optuna ({N_TRIALS} trials, {N_FOLDS}-fold CV)...")
study = optuna.create_study(direction='maximize',
                            sampler=optuna.samplers.TPESampler(seed=RANDOM_STATE))
study.optimize(objective, n_trials=N_TRIALS, show_progress_bar=True)

best_params = study.best_params
best_params.update({'scale_pos_weight': scale_pos,
                    'random_state': RANDOM_STATE,
                    'eval_metric': 'auc',
                    'use_label_encoder': False})

print(f"\nBest CV AUROC : {study.best_value:.4f}")
print(f"Best params   :")
for k, v in best_params.items():
    print(f"  {k}: {v}")

# ── FINAL TRAINING ────────────────────────────────────────────────────────────
print("\nTraining final model on full training set...")
final_model = XGBClassifier(**best_params, verbosity=0)
final_model.fit(X_train, y_train)

# ── THRESHOLD TUNING ─────────────────────────────────────────────────────────
print("Tuning decision threshold on cross-validation...")
skf_thresh = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
oof_probs  = np.zeros(len(y_train))
for fold_tr_idx, fold_val_idx in skf_thresh.split(X_train, y_train):
    m = XGBClassifier(**best_params, verbosity=0)
    m.fit(X_train[fold_tr_idx], y_train[fold_tr_idx],
          eval_set=[(X_train[fold_val_idx], y_train[fold_val_idx])],
          verbose=False)
    oof_probs[fold_val_idx] = m.predict_proba(X_train[fold_val_idx])[:, 1]

best_threshold = find_best_threshold(y_train, oof_probs)
print(f"  Best threshold (max CSI on OOF): {best_threshold}")

# ── TEST EVALUATION ───────────────────────────────────────────────────────────
print("\nEvaluating on test set (2023-2025)...")
test_prob = final_model.predict_proba(X_test)[:, 1]
metrics   = compute_metrics(y_test, test_prob, best_threshold)
metrics['Threshold'] = best_threshold
metrics['CV_AUROC']  = round(study.best_value, 4)
metrics['Model']     = 'XGBoost_6hr_nowcast_v1'

print("\n" + "=" * 60)
print("TEST RESULTS")
print("=" * 60)
print(f"  AUROC  : {metrics['AUROC']:.4f}  (daily baseline: {DAILY_AUROC})")
print(f"  CV AUROC: {metrics['CV_AUROC']:.4f}")
print(f"  POD    : {metrics['POD']:.4f}")
print(f"  FAR    : {metrics['FAR']:.4f}")
print(f"  CSI    : {metrics['CSI']:.4f}")
print(f"  HSS    : {metrics['HSS']:.4f}")
print(f"  BIAS   : {metrics['BIAS']:.4f}")
print(f"  Threshold: {metrics['Threshold']}")

if metrics['AUROC'] >= DAILY_AUROC:
    print(f"\n✓ 6-hr model matches or beats daily model AUROC!")
else:
    delta = DAILY_AUROC - metrics['AUROC']
    print(f"\n  6-hr model is {delta:.4f} below daily model — expected,")
    print(f"  will improve once Vidhi delivers sub-daily ERA5 features.")

# ── SAVE ──────────────────────────────────────────────────────────────────────
model_path  = MODELS / "nowcast_6hr_xgb_v1.pkl"
results_path = RESULTS / "evaluation_results_6hr.csv"

joblib.dump({'model': final_model,
             'feature_cols': FEATURE_COLS,
             'threshold': best_threshold,
             'best_params': best_params}, model_path)

results_df = pd.DataFrame([metrics])
results_df.to_csv(results_path, index=False)

print(f"\nModel saved   → {model_path}")
print(f"Results saved → {results_path}")
print("\nA2 complete. Run A3_slot_models.py next.")
