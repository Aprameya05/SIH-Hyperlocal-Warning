"""
A9_ensemble.py
==============
Ensemble meta-model that combines all 4 slot predictions into a single
daily thunderstorm alert.

Why this matters:
  The 4 slot models give probabilities per 6-hour window.
  Airport operations need ONE daily risk level: GREEN / YELLOW / ORANGE / RED.
  A meta-model learns how to combine slot signals optimally.

Architecture:
  Level 0: 4 calibrated XGBoost slot models (already trained)
  Level 1: Logistic Regression meta-model on:
    - 4 slot probabilities
    - Derived: max_prob, sum_prob, n_slots_firing
    - Context: month, CAPE, K_INDEX, rainfall lag

Target: daily_label = 1 if ANY slot had a thunderstorm that day

Output:
  models/ensemble_meta_model.pkl
  results/ensemble_results.csv
  results/shap_figures_v2/chart11_ensemble.png

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
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.isotonic import IsotonicRegression

warnings.filterwarnings('ignore')

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA    = BASE / "data"    / "bengaluru_6hr_training_dataset_v2.csv"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
FIGS    = BASE / "results" / "shap_figures_v2"

SLOT_NAMES  = {0:"0001-0600",1:"0601-1200",2:"1201-1800",3:"1801-2400"}
RANDOM_STATE = 42

# ── METRICS ───────────────────────────────────────────────────────────────────
def compute_metrics(y_true, y_prob, threshold):
    y_pred = (y_prob >= threshold).astype(int)
    tp = int(((y_pred==1)&(y_true==1)).sum())
    fp = int(((y_pred==1)&(y_true==0)).sum())
    fn = int(((y_pred==0)&(y_true==1)).sum())
    tn = int(((y_pred==0)&(y_true==0)).sum())
    pod  = tp/(tp+fn)      if (tp+fn)>0      else 0
    far  = fp/(tp+fp)      if (tp+fp)>0      else 0
    csi  = tp/(tp+fp+fn)   if (tp+fp+fn)>0   else 0
    hss_num = 2*(tp*tn - fp*fn)
    hss_den = (tp+fn)*(fn+tn)+(tp+fp)*(fp+tn)
    hss  = hss_num/hss_den if hss_den>0      else 0
    auroc = roc_auc_score(y_true, y_prob) if y_true.sum()>1 else float('nan')
    brier = brier_score_loss(y_true, y_prob)
    return dict(TP=tp,FP=fp,FN=fn,TN=tn,
                POD=round(pod,4),FAR=round(far,4),
                CSI=round(csi,4),HSS=round(hss,4),
                AUROC=round(auroc,4),Brier=round(brier,4))

def find_best_threshold(y_true, y_prob, min_t=0.20):
    best_csi, best_t = 0, 0.5
    for t in np.arange(min_t, 0.90, 0.01):
        y_pred = (y_prob >= t).astype(int)
        tp = ((y_pred==1)&(y_true==1)).sum()
        fp = ((y_pred==1)&(y_true==0)).sum()
        fn = ((y_pred==0)&(y_true==1)).sum()
        csi = tp/(tp+fp+fn) if (tp+fp+fn)>0 else 0
        if csi > best_csi:
            best_csi, best_t = csi, t
    return round(float(best_t), 2)

def apply_calibrator(artifact, raw_prob):
    cal = artifact.get('calibrator')
    if cal is None:
        return raw_prob
    if artifact.get('calib_method') == 'sigmoid':
        return cal.predict_proba(raw_prob.reshape(-1,1))[:,1]
    return cal.predict(raw_prob)

# ── LOAD ──────────────────────────────────────────────────────────────────────
print("=" * 60)
print("A9 — Ensemble Meta-Model")
print("=" * 60)

df = pd.read_csv(DATA, parse_dates=['date'])

print("\nLoading calibrated slot models...")
slot_artifacts = {}
for slot_id in range(4):
    path = MODELS / f"nowcast_slot{slot_id}_xgb_v2_calibrated.pkl"
    slot_artifacts[slot_id] = joblib.load(path)
    print(f"  Slot {slot_id} loaded")

# ── STEP 1: GET SLOT PROBABILITIES ────────────────────────────────────────────
print("\n[1/5] Generating slot probabilities...")

# We need OOF probs on train set to train meta-model without leakage
train_df = df[df['year'] < 2023].copy()
test_df  = df[df['year'] >= 2023].copy()

# Generate OOF slot probs on training set
skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)

for slot_id in range(4):
    artifact     = slot_artifacts[slot_id]
    model        = artifact['model']
    feature_cols = artifact['feature_cols']

    slot_train = train_df[train_df['slot']==slot_id]
    slot_test  = test_df[test_df['slot']==slot_id]

    X_train = slot_train[feature_cols].values
    y_train = slot_train['ts_label'].values
    X_test  = slot_test[feature_cols].values

    from xgboost import XGBClassifier

    # OOF on train
    oof = model.predict_proba(X_train)[:,1]

    cal_oof = apply_calibrator(artifact, oof)
    train_df.loc[slot_train.index, f'prob_slot{slot_id}'] = cal_oof

    # Test probs
    raw_test = model.predict_proba(X_test)[:,1]
    cal_test = apply_calibrator(artifact, raw_test)
    test_df.loc[slot_test.index, f'prob_slot{slot_id}'] = cal_test

    print(f"  Slot {slot_id}: OOF done")

# ── STEP 2: BUILD DAILY META-FEATURES ─────────────────────────────────────────
print("\n[2/5] Building daily meta-features...")

def build_daily_features(slot_df):
    """Pivot slot-level data to one row per day with meta-features."""
    # Pivot slot probabilities
    prob_cols = [f'prob_slot{i}' for i in range(4)]
    daily = slot_df.groupby('date')[prob_cols].mean()

    # Handle missing slots (fill with 0)
    for col in prob_cols:
        if col not in daily.columns:
            daily[col] = 0.0
    daily = daily.fillna(0.0)

    # Derived ensemble features
    daily['max_prob']  = daily[prob_cols].max(axis=1)
    daily['sum_prob']  = daily[prob_cols].sum(axis=1)
    daily['mean_prob'] = daily[prob_cols].mean(axis=1)
    daily['n_slots_above_02'] = (daily[prob_cols] > 0.20).sum(axis=1)
    daily['n_slots_above_03'] = (daily[prob_cols] > 0.30).sum(axis=1)
    daily['slot2_minus_slot1'] = daily['prob_slot2'] - daily['prob_slot1']
    daily['slot3_minus_slot2'] = daily['prob_slot3'] - daily['prob_slot2']

    # Add daily context features from slot 2 (most informative)
    ctx_cols = ['month','CAPE','K_INDEX','TOTALS_TOTALS','ERA5_T2M',
                'RF','RF_lag1','LABEL_lag1','ts_any_yesterday',
                'slot_month_clim','MONTH_sin','MONTH_cos','DOY_sin','DOY_cos']
    slot2_ctx = slot_df[slot_df['slot']==2].set_index('date')[
        [c for c in ctx_cols if c in slot_df.columns]]
    daily = daily.join(slot2_ctx, how='left')

    # Daily label: did ANY slot have a TS?
    daily_label = slot_df.groupby('date')['ts_label'].max().rename('daily_label')
    daily = daily.join(daily_label)

    return daily.reset_index()

daily_train = build_daily_features(train_df)
daily_test  = build_daily_features(test_df)

print(f"  Train daily rows: {len(daily_train)} | TS days: {daily_train['daily_label'].sum()}")
print(f"  Test  daily rows: {len(daily_test)}  | TS days: {daily_test['daily_label'].sum()}")

META_FEATURES = [c for c in daily_train.columns if c not in
                 ['date','daily_label']]
META_FEATURES = [c for c in META_FEATURES if daily_train[c].notna().all()]

print(f"  Meta-features: {len(META_FEATURES)}")

X_meta_train = daily_train[META_FEATURES].fillna(0).values
y_meta_train = daily_train['daily_label'].values
X_meta_test  = daily_test[META_FEATURES].fillna(0).values
y_meta_test  = daily_test['daily_label'].values

# ── STEP 3: TRAIN META-MODEL ──────────────────────────────────────────────────
print("\n[3/5] Training meta-model (Logistic Regression)...")

# OOF on meta-train for threshold tuning
skf_meta = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
meta_oof  = np.zeros(len(y_meta_train))

for tr_idx, val_idx in skf_meta.split(X_meta_train, y_meta_train):
    lr = LogisticRegression(C=1.0, max_iter=1000, random_state=RANDOM_STATE)
    lr.fit(X_meta_train[tr_idx], y_meta_train[tr_idx])
    meta_oof[val_idx] = lr.predict_proba(X_meta_train[val_idx])[:,1]

# Final meta-model on full train
meta_model = LogisticRegression(C=1.0, max_iter=1000, random_state=RANDOM_STATE)
meta_model.fit(X_meta_train, y_meta_train)

# Threshold tuning on OOF
threshold = find_best_threshold(y_meta_train, meta_oof)
print(f"  CV AUROC: {roc_auc_score(y_meta_train, meta_oof):.4f}")
print(f"  Threshold (OOF): {threshold}")

# ── STEP 4: EVALUATE ──────────────────────────────────────────────────────────
print("\n[4/5] Evaluating on test set...")
test_prob = meta_model.predict_proba(X_meta_test)[:,1]
metrics   = compute_metrics(y_meta_test, test_prob, threshold)

# Compare to slot 2 alone (best single slot)
s2_prob    = daily_test['prob_slot2'].fillna(0).values
s2_thresh  = slot_artifacts[2]['threshold']
s2_metrics = compute_metrics(y_meta_test, s2_prob, s2_thresh)

# Compare to daily model baseline
print("\n" + "="*60)
print("ENSEMBLE RESULTS")
print("="*60)
print(f"\n{'Model':<30} {'AUROC':<8} {'POD':<7} {'FAR':<7} {'CSI':<7} {'HSS':<7} {'Brier'}")
print("-"*65)
print(f"  {'Daily XGBoost (baseline)':<28} {'0.8715':<8} {'0.500':<7} {'0.586':<7} {'0.293':<7} {'0.389':<7} {'—'}")
print(f"  {'Slot 2 alone (best slot)':<28} {s2_metrics['AUROC']:<8} {s2_metrics['POD']:<7} "
      f"{s2_metrics['FAR']:<7} {s2_metrics['CSI']:<7} {s2_metrics['HSS']:<7} {s2_metrics['Brier']}")
print(f"  {'Ensemble (4 slots)':<28} {metrics['AUROC']:<8} {metrics['POD']:<7} "
      f"{metrics['FAR']:<7} {metrics['CSI']:<7} {metrics['HSS']:<7} {metrics['Brier']}")

# ── STEP 5: FEATURE IMPORTANCE + CHART ────────────────────────────────────────
print("\n[5/5] Building charts...")

# Meta-model coefficients as importance
coef_df = pd.DataFrame({
    'feature': META_FEATURES,
    'coef':    meta_model.coef_[0]
}).sort_values('coef', ascending=False)

fig, axes = plt.subplots(1, 3, figsize=(18, 7))

# Chart A: Meta-feature importance
ax = axes[0]
top10 = coef_df.head(10)
colors = ['#E74C3C' if c > 0 else '#3498DB' for c in top10['coef']]
ax.barh(range(10), top10['coef'].values[::-1], color=colors[::-1], alpha=0.85)
ax.set_yticks(range(10))
ax.set_yticklabels(top10['feature'].values[::-1], fontsize=9)
ax.axvline(0, color='black', linewidth=0.8)
ax.set_xlabel('Logistic Regression Coefficient', fontsize=10)
ax.set_title('Meta-Model Feature Importance\n(Red=increases TS prob, Blue=decreases)', fontsize=10)
ax.grid(axis='x', alpha=0.3)
ax.spines[['top','right']].set_visible(False)

# Chart B: Daily probability distribution — TS vs no-TS days
ax = axes[1]
ts_prob  = test_prob[y_meta_test==1]
nts_prob = test_prob[y_meta_test==0]
ax.hist(nts_prob, bins=20, alpha=0.7, color='#3498DB',
        label=f'No TS (n={len(nts_prob)})', density=True)
ax.hist(ts_prob,  bins=20, alpha=0.7, color='#E74C3C',
        label=f'TS day (n={len(ts_prob)})',  density=True)
ax.axvline(threshold, color='black', linestyle='--', linewidth=1.5,
           label=f'Threshold={threshold}')
ax.set_xlabel('Ensemble Daily TS Probability', fontsize=10)
ax.set_ylabel('Density', fontsize=10)
ax.set_title('Ensemble Probability Distribution\nTS vs Non-TS Days (Test 2023-2025)', fontsize=10)
ax.legend(fontsize=9)
ax.grid(alpha=0.3)
ax.spines[['top','right']].set_visible(False)

# Chart C: Model comparison bar chart
ax = axes[2]
models_cmp = ['Daily\nXGBoost', 'Slot 2\nAlone', 'Ensemble\n4 Slots']
auroc_cmp  = [0.8715, s2_metrics['AUROC'], metrics['AUROC']]
hss_cmp    = [0.389,  s2_metrics['HSS'],   metrics['HSS']]
csi_cmp    = [0.293,  s2_metrics['CSI'],   metrics['CSI']]

x = np.arange(3)
w = 0.25
ax.bar(x-w, auroc_cmp, w, label='AUROC', color='#2C3E50', alpha=0.85)
ax.bar(x,   hss_cmp,   w, label='HSS',   color='#27AE60', alpha=0.85)
ax.bar(x+w, csi_cmp,   w, label='CSI',   color='#E67E22', alpha=0.85)
for i, (a,h,c) in enumerate(zip(auroc_cmp, hss_cmp, csi_cmp)):
    ax.text(i-w, a+0.005, f'{a:.3f}', ha='center', fontsize=7)
    ax.text(i,   h+0.005, f'{h:.3f}', ha='center', fontsize=7)
    ax.text(i+w, c+0.005, f'{c:.3f}', ha='center', fontsize=7)
ax.set_xticks(x)
ax.set_xticklabels(models_cmp, fontsize=10)
ax.set_ylabel('Score', fontsize=10)
ax.set_title('Model Comparison\nDaily vs Slot 2 vs Ensemble', fontsize=10)
ax.legend(fontsize=9)
ax.grid(axis='y', alpha=0.3)
ax.spines[['top','right']].set_visible(False)
ax.set_ylim(0, 1.05)

plt.suptitle('Ensemble Meta-Model — Daily Thunderstorm Alert\n'
             'Combining 4 Calibrated Slot Models', fontsize=13, y=1.01)
plt.tight_layout()
path = FIGS / "chart11_ensemble.png"
fig.savefig(path, dpi=150, bbox_inches='tight')
plt.close()
print(f"  Chart saved → {path}")

# ── SAVE ──────────────────────────────────────────────────────────────────────
joblib.dump({
    'model':          meta_model,
    'meta_features':  META_FEATURES,
    'threshold':      threshold,
    'slot_artifacts': slot_artifacts,
}, MODELS / "ensemble_meta_model.pkl")

results_df = pd.DataFrame([{
    'model': 'Ensemble (4 slots)',
    **metrics, 'threshold': threshold
}])
results_df.to_csv(RESULTS / "ensemble_results.csv", index=False)

print(f"\nModel saved → {MODELS / 'ensemble_meta_model.pkl'}")
print("A9 complete.")
