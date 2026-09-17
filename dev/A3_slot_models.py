"""
A3_slot_models.py
=================
Trains one XGBoost model per 6-hour slot.
Each slot gets its own Optuna tuning, threshold, and evaluation.

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
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA    = BASE / "data"    / "bengaluru_6hr_training_dataset.csv"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
MODELS.mkdir(parents=True, exist_ok=True)
RESULTS.mkdir(parents=True, exist_ok=True)

# ── CONFIG ────────────────────────────────────────────────────────────────────
N_TRIALS     = 50
N_FOLDS      = 5
RANDOM_STATE = 42
DAILY_AUROC  = 0.8715

SLOT_NAMES = {
    0: "0001-0600 IST",
    1: "0601-1200 IST",
    2: "1201-1800 IST",
    3: "1801-2400 IST",
}

# Slot 1 has only 3 positive test cases — skip Optuna, use fixed conservative params
SKIP_OPTUNA_SLOTS = {1}

# ── METRICS ───────────────────────────────────────────────────────────────────
def compute_metrics(y_true, y_prob, threshold):
    y_pred = (y_prob >= threshold).astype(int)
    tp = int(((y_pred==1)&(y_true==1)).sum())
    fp = int(((y_pred==1)&(y_true==0)).sum())
    fn = int(((y_pred==0)&(y_true==1)).sum())
    tn = int(((y_pred==0)&(y_true==0)).sum())
    pod  = tp/(tp+fn)       if (tp+fn)>0       else 0
    far  = fp/(tp+fp)       if (tp+fp)>0       else 0
    csi  = tp/(tp+fp+fn)    if (tp+fp+fn)>0    else 0
    bias = (tp+fp)/(tp+fn)  if (tp+fn)>0       else 0
    hss_num = 2*(tp*tn - fp*fn)
    hss_den = (tp+fn)*(fn+tn) + (tp+fp)*(fp+tn)
    hss   = hss_num/hss_den if hss_den>0 else 0
    auroc = roc_auc_score(y_true, y_prob) if y_true.sum()>0 else float('nan')
    return dict(TP=tp, FP=fp, FN=fn, TN=tn,
                AUROC=round(auroc,4), POD=round(pod,4),
                FAR=round(far,4),    CSI=round(csi,4),
                HSS=round(hss,4),    BIAS=round(bias,4))

def find_best_threshold(y_true, y_prob, min_threshold=0.15):
    """
    Maximise CSI over OOF predictions.
    min_threshold prevents collapse to near-zero on poorly calibrated slots.
    """
    best_csi, best_t = 0, 0.5
    for t in np.arange(min_threshold, 0.95, 0.01):
        y_pred = (y_prob >= t).astype(int)
        tp = ((y_pred==1)&(y_true==1)).sum()
        fp = ((y_pred==1)&(y_true==0)).sum()
        fn = ((y_pred==0)&(y_true==1)).sum()
        denom = tp+fp+fn
        csi = tp/denom if denom>0 else 0
        if csi > best_csi:
            best_csi, best_t = csi, t
    return round(float(best_t), 2)

def make_xgb(params):
    """Always strip early_stopping_rounds for fits that have no eval_set."""
    p = {k: v for k, v in params.items() if k != 'early_stopping_rounds'}
    return XGBClassifier(**p, verbosity=0)

def make_xgb_es(params):
    """Keep early_stopping_rounds for fits that DO supply eval_set."""
    return XGBClassifier(**params, verbosity=0)

# ── LOAD ──────────────────────────────────────────────────────────────────────
print("=" * 60)
print("A3 — Per-Slot XGBoost Model Training")
print("=" * 60)

df = pd.read_csv(DATA, parse_dates=['date'])
FEATURE_COLS = [c for c in df.columns if c not in
                ['date','year','month','slot','slot_label','ts_label']]

all_results   = []
summary_lines = []

# ── MAIN LOOP — ONE MODEL PER SLOT ───────────────────────────────────────────
for slot_id in range(4):
    slot_name = SLOT_NAMES[slot_id]
    print(f"\n{'='*60}")
    print(f"SLOT {slot_id} — {slot_name}")
    print(f"{'='*60}")

    slot_df    = df[df['slot'] == slot_id].copy()
    train_slot = slot_df[slot_df['year'] < 2023]
    test_slot  = slot_df[slot_df['year'] >= 2023]

    X_train = train_slot[FEATURE_COLS].values
    y_train = train_slot['ts_label'].values
    X_test  = test_slot[FEATURE_COLS].values
    y_test  = test_slot['ts_label'].values

    n_pos = y_train.sum()
    n_neg = (y_train==0).sum()
    spw   = n_neg / n_pos if n_pos > 0 else 1.0

    print(f"Train: {len(y_train)} rows | {n_pos} pos ({n_pos/len(y_train)*100:.1f}%)")
    print(f"Test : {len(y_test)} rows  | {y_test.sum()} pos ({y_test.mean()*100:.1f}%)")
    print(f"Scale pos weight: {spw:.1f}")

    # ── OPTUNA OR FIXED PARAMS ────────────────────────────────────────────────
    if slot_id in SKIP_OPTUNA_SLOTS:
        print(f"Slot {slot_id}: too few positives — using fixed conservative params.")
        best_params = {
            'n_estimators': 200, 'max_depth': 4,
            'learning_rate': 0.05, 'subsample': 0.8,
            'colsample_bytree': 0.8, 'min_child_weight': 5,
            'reg_alpha': 5.0, 'reg_lambda': 5.0,
            'scale_pos_weight': spw, 'random_state': RANDOM_STATE,
            'eval_metric': 'auc',
        }
        cv_auroc = float('nan')

    else:
        def objective(trial):
            params = {
                'n_estimators':          trial.suggest_int('n_estimators', 100, 600),
                'max_depth':             trial.suggest_int('max_depth', 3, 7),
                'learning_rate':         trial.suggest_float('learning_rate', 0.01, 0.2, log=True),
                'subsample':             trial.suggest_float('subsample', 0.6, 1.0),
                'colsample_bytree':      trial.suggest_float('colsample_bytree', 0.6, 1.0),
                'min_child_weight':      trial.suggest_int('min_child_weight', 1, 10),
                'reg_alpha':             trial.suggest_float('reg_alpha', 1e-4, 10.0, log=True),
                'reg_lambda':            trial.suggest_float('reg_lambda', 1e-4, 10.0, log=True),
                'scale_pos_weight':      spw,
                'random_state':          RANDOM_STATE,
                'eval_metric':           'auc',
                'early_stopping_rounds': 30,
            }
            skf    = StratifiedKFold(n_splits=N_FOLDS, shuffle=True,
                                     random_state=RANDOM_STATE)
            scores = []
            for tr_idx, val_idx in skf.split(X_train, y_train):
                m = make_xgb_es(params)
                m.fit(X_train[tr_idx], y_train[tr_idx],
                      eval_set=[(X_train[val_idx], y_train[val_idx])],
                      verbose=False)
                prob = m.predict_proba(X_train[val_idx])[:,1]
                if y_train[val_idx].sum() > 0:
                    scores.append(roc_auc_score(y_train[val_idx], prob))
            return np.mean(scores) if scores else 0.5

        print(f"Running Optuna ({N_TRIALS} trials)...")
        study = optuna.create_study(
            direction='maximize',
            sampler=optuna.samplers.TPESampler(seed=RANDOM_STATE))
        study.optimize(objective, n_trials=N_TRIALS, show_progress_bar=True)
        cv_auroc    = round(study.best_value, 4)
        best_params = study.best_params
        best_params.update({
            'scale_pos_weight': spw,
            'random_state':     RANDOM_STATE,
            'eval_metric':      'auc',
        })
        print(f"Best CV AUROC: {cv_auroc}")

    # ── TRAIN FINAL MODEL (no early stopping — use all estimators) ────────────
    print("Training final model...")
    model = make_xgb(best_params)   # strips early_stopping_rounds
    model.fit(X_train, y_train)     # no eval_set needed

    # ── OOF THRESHOLD TUNING ──────────────────────────────────────────────────
    print("Tuning threshold on OOF predictions...")
    skf_t    = StratifiedKFold(n_splits=N_FOLDS, shuffle=True,
                                random_state=RANDOM_STATE)
    oof_prob = np.zeros(len(y_train))
    for tr_idx, val_idx in skf_t.split(X_train, y_train):
        m = make_xgb(best_params)   # no early stopping — no eval_set needed
        m.fit(X_train[tr_idx], y_train[tr_idx])
        oof_prob[val_idx] = m.predict_proba(X_train[val_idx])[:,1]

    threshold = find_best_threshold(y_train, oof_prob)
    print(f"  Threshold: {threshold}")

    # ── EVALUATE ON TEST ──────────────────────────────────────────────────────
    test_prob = model.predict_proba(X_test)[:,1]
    metrics   = compute_metrics(y_test, test_prob, threshold)

    print(f"\n  AUROC={metrics['AUROC']}  POD={metrics['POD']}  "
          f"FAR={metrics['FAR']}  CSI={metrics['CSI']}  HSS={metrics['HSS']}")
    print(f"  TP={metrics['TP']}  FP={metrics['FP']}  "
          f"FN={metrics['FN']}  TN={metrics['TN']}")

    # ── SAVE MODEL ────────────────────────────────────────────────────────────
    model_path = MODELS / f"nowcast_slot{slot_id}_xgb.pkl"
    joblib.dump({
        'model':        model,
        'feature_cols': FEATURE_COLS,
        'threshold':    threshold,
        'best_params':  best_params,
        'slot_id':      slot_id,
        'slot_name':    slot_name,
    }, model_path)
    print(f"  Saved → {model_path}")

    row = {'Slot': slot_id, 'Slot_label': slot_name,
           'CV_AUROC': cv_auroc, 'Threshold': threshold}
    row.update(metrics)
    all_results.append(row)
    summary_lines.append(
        f"Slot {slot_id} ({slot_name}): AUROC={metrics['AUROC']} "
        f"POD={metrics['POD']} FAR={metrics['FAR']} "
        f"CSI={metrics['CSI']} HSS={metrics['HSS']} thresh={threshold}"
    )

# ── AGGREGATE SUMMARY ─────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("SLOT MODEL SUMMARY — ALL SLOTS")
print(f"{'='*60}")

results_df = pd.DataFrame(all_results)
test_all   = df[df['year'] >= 2023]
weights    = [test_all[test_all['slot']==s]['ts_label'].sum() for s in range(4)]
total_w    = sum(weights)

w_auroc = sum(results_df['AUROC'].fillna(0) * weights) / total_w
w_pod   = sum(results_df['POD']  * weights) / total_w
w_far   = sum(results_df['FAR']  * weights) / total_w
w_csi   = sum(results_df['CSI']  * weights) / total_w
w_hss   = sum(results_df['HSS']  * weights) / total_w

print(f"\n{'Slot':<6} {'Window':<16} {'AUROC':<8} {'POD':<7} "
      f"{'FAR':<7} {'CSI':<7} {'HSS':<7} {'Thresh':<8} {'TP':<5} {'FP':<6} {'FN'}")
print("-" * 85)
for _, r in results_df.iterrows():
    print(f"  {int(r['Slot']):<5} {r['Slot_label']:<16} {r['AUROC']:<8} "
          f"{r['POD']:<7} {r['FAR']:<7} {r['CSI']:<7} {r['HSS']:<7} "
          f"{r['Threshold']:<8} {int(r['TP']):<5} {int(r['FP']):<6} {int(r['FN'])}")
print("-" * 85)
print(f"  {'WEIGHTED':<22} {w_auroc:<8.4f} {w_pod:<7.4f} {w_far:<7.4f} "
      f"{w_csi:<7.4f} {w_hss:<7.4f}")
print(f"\n  vs Daily model:  AUROC=0.8715  POD=0.500  FAR=0.586  CSI=0.293  HSS=0.389")

results_df.to_csv(RESULTS / "evaluation_results_per_slot.csv", index=False)
summary_text  = "\n".join(summary_lines)
summary_text += (f"\n\nWeighted: AUROC={w_auroc:.4f} POD={w_pod:.4f} "
                 f"FAR={w_far:.4f} CSI={w_csi:.4f} HSS={w_hss:.4f}")
(RESULTS / "slot_model_summary.txt").write_text(summary_text)

print(f"\nSaved → {RESULTS / 'evaluation_results_per_slot.csv'}")
print("A3 complete. Paste the summary table to Aprameya.")