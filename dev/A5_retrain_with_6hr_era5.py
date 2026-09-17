"""
A5_retrain_with_6hr_era5.py
============================
Retrains all 4 per-slot XGBoost models using slot-specific 6-hourly
ERA5 features instead of daily averages.

What changes from A3:
  - Each slot now gets the ERA5 snapshot that matches its time window
    (Slot 0 → 00Z, Slot 1 → 06Z, Slot 2 → 12Z, Slot 3 → 18Z)
  - Previously all 4 slots shared the same daily ERA5 average
  - This is the key fix for Slot 3's high FAR

Expected improvement:
  - Slot 2 vs Slot 3 can now be distinguished by their atmospheric state
  - Slot 3 FAR should drop significantly (was 0.93 with daily ERA5)
  - Overall weighted AUROC should improve

Output:
  models/nowcast_slot{0-3}_xgb_v2.pkl   (v2 = with 6hr ERA5)
  data/bengaluru_6hr_training_dataset_v2.csv
  results/evaluation_results_per_slot_v2.csv
  results/slot_model_summary_v2.txt

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
LABELS   = BASE / "data" / "bengaluru_6hr_labels.csv"
DAILY    = BASE / "data" / "bengaluru_thunderstorm_features_merged.csv"
ERA5_6H  = BASE / "data" / "era5_6hrly_bengaluru_2015_2025.csv"
OUT_DATA = BASE / "data" / "bengaluru_6hr_training_dataset_v2.csv"
MODELS   = BASE / "models"
RESULTS  = BASE / "results"
MODELS.mkdir(parents=True, exist_ok=True)
RESULTS.mkdir(parents=True, exist_ok=True)

# ── CONFIG ────────────────────────────────────────────────────────────────────
N_TRIALS     = 50
N_FOLDS      = 5
RANDOM_STATE = 42
SKIP_OPTUNA_SLOTS = {1}  # Slot 1 has only 3 test positives

SLOT_NAMES = {
    0: "0001-0600 IST",
    1: "0601-1200 IST",
    2: "1201-1800 IST",
    3: "1801-2400 IST",
}

# ERA5 columns to replace with 6-hourly versions
ERA5_COLS = [
    'ERA5_T2M','ERA5_D2M','ERA5_U10','ERA5_V10','ERA5_CAPE','ERA5_SP',
    'ERA5_t_500hPa','ERA5_t_700hPa','ERA5_t_850hPa',
    'ERA5_q_500hPa','ERA5_q_700hPa','ERA5_q_850hPa',
    'ERA5_u_500hPa','ERA5_u_700hPa','ERA5_u_850hPa',
    'ERA5_v_500hPa','ERA5_v_700hPa','ERA5_v_850hPa',
]

# ── METRICS ───────────────────────────────────────────────────────────────────
def compute_metrics(y_true, y_prob, threshold):
    y_pred = (y_prob >= threshold).astype(int)
    tp = int(((y_pred==1)&(y_true==1)).sum())
    fp = int(((y_pred==1)&(y_true==0)).sum())
    fn = int(((y_pred==0)&(y_true==1)).sum())
    tn = int(((y_pred==0)&(y_true==0)).sum())
    pod  = tp/(tp+fn)       if (tp+fn)>0    else 0
    far  = fp/(tp+fp)       if (tp+fp)>0    else 0
    csi  = tp/(tp+fp+fn)    if (tp+fp+fn)>0 else 0
    bias = (tp+fp)/(tp+fn)  if (tp+fn)>0    else 0
    hss_num = 2*(tp*tn - fp*fn)
    hss_den = (tp+fn)*(fn+tn) + (tp+fp)*(fp+tn)
    hss   = hss_num/hss_den if hss_den>0 else 0
    auroc = roc_auc_score(y_true, y_prob) if y_true.sum()>0 else float('nan')
    return dict(TP=tp, FP=fp, FN=fn, TN=tn,
                AUROC=round(auroc,4), POD=round(pod,4),
                FAR=round(far,4),    CSI=round(csi,4),
                HSS=round(hss,4),    BIAS=round(bias,4))

def find_best_threshold(y_true, y_prob, min_threshold=0.15):
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
    p = {k: v for k, v in params.items() if k != 'early_stopping_rounds'}
    return XGBClassifier(**p, verbosity=0)

def make_xgb_es(params):
    return XGBClassifier(**params, verbosity=0)

# ── STEP 1: BUILD V2 TRAINING DATASET ─────────────────────────────────────────
print("=" * 60)
print("A5 — Retrain with 6-Hourly ERA5 Features")
print("=" * 60)

print("\n[1/3] Building v2 training dataset...")

labels  = pd.read_csv(LABELS, parse_dates=['date'])
daily   = pd.read_csv(DAILY,  parse_dates=['date'])
era5_6h = pd.read_csv(ERA5_6H, parse_dates=['date'])

# Drop daily ERA5 cols — will be replaced by 6hr versions
drop_cols = ['YEAR','MONTH','LABEL','CIN'] + ERA5_COLS
daily_non_era5 = daily.drop(columns=[c for c in drop_cols if c in daily.columns])

# Fill lag/rolling nulls
lag_cols = ['RF_3d','RF_7d','MAX_3d_avg','MIN_3d_avg','DTR_3d_avg','MAX_lag1','MIN_lag1']
for col in lag_cols:
    if col in daily_non_era5.columns:
        daily_non_era5[col] = daily_non_era5[col].fillna(daily_non_era5[col].median())

print(f"  Daily non-ERA5 features: {daily_non_era5.shape[1]-1}")

# Merge: labels → daily features → 6hr ERA5
df = labels.merge(daily_non_era5, on='date', how='left')
df = df.merge(era5_6h[['date','slot'] + ERA5_COLS], on=['date','slot'], how='left')

# Drop rows with missing data
before = len(df)
df = df.dropna(subset=['MAX', 'ERA5_CAPE'])
after  = len(df)
print(f"  Rows after merge: {after} (dropped {before-after} with missing data)")
print(f"  Positives retained: {df['ts_label'].sum()}")

# Add slot features
df['slot_sin'] = np.sin(2 * np.pi * df['slot'] / 4)
df['slot_cos'] = np.cos(2 * np.pi * df['slot'] / 4)

train_mask = df['year'] < 2023
clim = (df[train_mask].groupby(['month','slot'])['ts_label']
        .mean().rename('slot_month_clim').reset_index())
df = df.merge(clim, on=['month','slot'], how='left')
df['slot_month_clim'] = df['slot_month_clim'].fillna(0)

df['doy']     = df['date'].dt.dayofyear
df['doy_sin'] = np.sin(2 * np.pi * df['doy'] / 365)
df['doy_cos'] = np.cos(2 * np.pi * df['doy'] / 365)

# Slot lag features
df = df.sort_values(['date','slot']).reset_index(drop=True)
df['prev_date']  = df['date'] - pd.Timedelta(days=1)
slot_lookup      = df.set_index(['date','slot'])['ts_label'].to_dict()
df['ts_label_lag1_slot'] = df.apply(
    lambda r: slot_lookup.get((r['prev_date'], r['slot']), 0), axis=1)

daily_ts = df.groupby('date')['ts_label'].max().rename('ts_any_yesterday')
df = df.merge(daily_ts.rename_axis('date').reset_index()
              .rename(columns={'date':'prev_date','ts_any_yesterday':'ts_any_yesterday'}),
              on='prev_date', how='left')
df['ts_any_yesterday'] = df['ts_any_yesterday'].fillna(0).astype(int)

# Clean up
drop_helper = ['ts_source','g_code','duration_min','prev_date','doy']
df = df.drop(columns=[c for c in drop_helper if c in df.columns])

id_cols      = ['date','year','month','slot','slot_label']
target_col   = ['ts_label']
feature_cols = [c for c in df.columns if c not in id_cols + target_col]
df = df[id_cols + feature_cols + target_col]

train = df[df['year'] < 2023]
test  = df[df['year'] >= 2023]

print(f"  Final shape: {df.shape} | Features: {len(feature_cols)}")
print(f"  Train: {len(train)} rows | {train['ts_label'].sum()} positives")
print(f"  Test : {len(test)} rows  | {test['ts_label'].sum()} positives")
print(f"  ERA5 features are now SLOT-SPECIFIC (6-hourly snapshots)")

df.to_csv(OUT_DATA, index=False)
print(f"  Saved → {OUT_DATA}")

# ── STEP 2: TRAIN PER-SLOT MODELS ─────────────────────────────────────────────
print("\n[2/3] Training per-slot models with 6-hr ERA5...")

all_results   = []
summary_lines = []

for slot_id in range(4):
    slot_name  = SLOT_NAMES[slot_id]
    print(f"\n{'='*60}")
    print(f"SLOT {slot_id} — {slot_name}")
    print(f"{'='*60}")

    slot_df    = df[df['slot'] == slot_id].copy()
    train_slot = slot_df[slot_df['year'] < 2023]
    test_slot  = slot_df[slot_df['year'] >= 2023]

    X_train = train_slot[feature_cols].values
    y_train = train_slot['ts_label'].values
    X_test  = test_slot[feature_cols].values
    y_test  = test_slot['ts_label'].values

    n_pos = y_train.sum()
    n_neg = (y_train==0).sum()
    spw   = n_neg / n_pos if n_pos > 0 else 1.0

    print(f"Train: {len(y_train)} | {n_pos} pos ({n_pos/len(y_train)*100:.1f}%) | SPW={spw:.1f}")
    print(f"Test : {len(y_test)}  | {y_test.sum()} pos ({y_test.mean()*100:.1f}%)")

    # ── Optuna or fixed params ─────────────────────────────────────────────
    if slot_id in SKIP_OPTUNA_SLOTS:
        print("Using fixed params (too few positives for Optuna)")
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

    # ── Train final model ──────────────────────────────────────────────────
    print("Training final model...")
    model = make_xgb(best_params)
    model.fit(X_train, y_train)

    # ── OOF threshold tuning ───────────────────────────────────────────────
    print("Tuning threshold on OOF predictions...")
    skf_t    = StratifiedKFold(n_splits=N_FOLDS, shuffle=True,
                                random_state=RANDOM_STATE)
    oof_prob = np.zeros(len(y_train))
    for tr_idx, val_idx in skf_t.split(X_train, y_train):
        m = make_xgb(best_params)
        m.fit(X_train[tr_idx], y_train[tr_idx])
        oof_prob[val_idx] = m.predict_proba(X_train[val_idx])[:,1]

    threshold = find_best_threshold(y_train, oof_prob)
    print(f"  Threshold: {threshold}")

    # ── Evaluate ───────────────────────────────────────────────────────────
    test_prob = model.predict_proba(X_test)[:,1]
    metrics   = compute_metrics(y_test, test_prob, threshold)

    print(f"\n  AUROC={metrics['AUROC']}  POD={metrics['POD']}  "
          f"FAR={metrics['FAR']}  CSI={metrics['CSI']}  HSS={metrics['HSS']}")
    print(f"  TP={metrics['TP']}  FP={metrics['FP']}  "
          f"FN={metrics['FN']}  TN={metrics['TN']}")

    # ── Save model v2 ──────────────────────────────────────────────────────
    model_path = MODELS / f"nowcast_slot{slot_id}_xgb_v2.pkl"
    joblib.dump({
        'model':        model,
        'feature_cols': feature_cols,
        'threshold':    threshold,
        'best_params':  best_params,
        'slot_id':      slot_id,
        'slot_name':    slot_name,
        'era5_version': '6hourly',
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

# ── STEP 3: SUMMARY ───────────────────────────────────────────────────────────
print(f"\n[3/3] Results summary...")
print(f"\n{'='*60}")
print("SLOT MODEL SUMMARY v2 — WITH 6-HOURLY ERA5")
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
print(f"  {'WEIGHTED v2':<22} {w_auroc:<8.4f} {w_pod:<7.4f} {w_far:<7.4f} "
      f"{w_csi:<7.4f} {w_hss:<7.4f}")
print(f"\n  vs A3 (daily ERA5): AUROC=0.8390 POD=0.3178 FAR=0.7390 CSI=0.1637 HSS=0.2315")
print(f"  vs Daily model:     AUROC=0.8715 POD=0.5000 FAR=0.5860 CSI=0.2930 HSS=0.3890")

results_df.to_csv(RESULTS / "evaluation_results_per_slot_v2.csv", index=False)
summary_text  = "\n".join(summary_lines)
summary_text += (f"\n\nWeighted v2: AUROC={w_auroc:.4f} POD={w_pod:.4f} "
                 f"FAR={w_far:.4f} CSI={w_csi:.4f} HSS={w_hss:.4f}")
(RESULTS / "slot_model_summary_v2.txt").write_text(summary_text)

print(f"\nSaved → {RESULTS / 'evaluation_results_per_slot_v2.csv'}")
print("A5 complete.")
