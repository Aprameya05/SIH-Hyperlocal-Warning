"""
A12_feature_engineering_v3.py
==============================
Adds derived atmospheric features to improve performance on R5
(pre-monsoon convective burst regime) identified in A11.

New features derived from existing ERA5 pressure levels:
  1. q_gradient_500_850   — moisture gradient (best correlation r=0.224)
  2. wind_shear_500_850   — horizontal wind shear 500-850 hPa
  3. thickness_500_850    — temperature thickness (stability proxy)
  4. moisture_flux_850    — low-level moisture transport
  5. divergence_proxy     — u700 divergence approximation
  6. thetae_850           — equivalent potential temperature (convective instability)
  7. cape_x_k_index       — interaction term (both top SHAP features)
  8. lifted_x_totals      — interaction term
  9. wind_shear_700_850   — low-level wind shear
  10. q_flux_850          — specific humidity × wind speed at 850hPa

These add 10 new features to the existing 54 = 64 total features.

Output:
  data/bengaluru_6hr_training_dataset_v3.csv
  models/nowcast_slot{0-3}_xgb_v3.pkl
  results/evaluation_results_per_slot_v3.csv

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
DATA_V2  = BASE / "data" / "bengaluru_6hr_training_dataset_v2.csv"
OUT_V3   = BASE / "data" / "bengaluru_6hr_training_dataset_v3.csv"
MODELS   = BASE / "models"
RESULTS  = BASE / "results"
MODELS.mkdir(parents=True, exist_ok=True)
RESULTS.mkdir(parents=True, exist_ok=True)

N_TRIALS     = 50
N_FOLDS      = 5
RANDOM_STATE = 42
SKIP_OPTUNA  = {1}

SLOT_NAMES = {
    0:"0001-0600 IST", 1:"0601-1200 IST",
    2:"1201-1800 IST", 3:"1801-2400 IST"
}

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
    return dict(TP=tp,FP=fp,FN=fn,TN=tn,
                AUROC=round(auroc,4),POD=round(pod,4),
                FAR=round(far,4),CSI=round(csi,4),
                HSS=round(hss,4))

def find_best_threshold(y_true, y_prob, min_t=0.15):
    best_csi, best_t = 0, 0.5
    for t in np.arange(min_t, 0.95, 0.01):
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
    p = {k:v for k,v in params.items() if k!='early_stopping_rounds'}
    return XGBClassifier(**p, verbosity=0)

def make_xgb_es(params):
    return XGBClassifier(**params, verbosity=0)

# ── STEP 1: LOAD V2 AND ADD NEW FEATURES ─────────────────────────────────────
print("=" * 60)
print("A12 — Feature Engineering v3 + Retrain")
print("=" * 60)

print("\n[1/3] Adding derived atmospheric features...")
df = pd.read_csv(DATA_V2, parse_dates=['date'])

# ── NEW FEATURES ──────────────────────────────────────────────────────────────

# 1. Moisture gradient — vertical change in specific humidity
df['q_gradient_500_850'] = df['ERA5_q_850hPa'] - df['ERA5_q_500hPa']

# 2. Horizontal wind shear 500-850 hPa (deep layer shear)
df['wind_shear_500_850'] = np.sqrt(
    (df['ERA5_u_500hPa'] - df['ERA5_u_850hPa'])**2 +
    (df['ERA5_v_500hPa'] - df['ERA5_v_850hPa'])**2
)

# 3. Temperature thickness 500-850 (higher = more unstable)
df['thickness_500_850'] = df['ERA5_t_850hPa'] - df['ERA5_t_500hPa']

# 4. Low-level moisture flux at 850 hPa
df['moisture_flux_850'] = df['ERA5_q_850hPa'] * np.sqrt(
    df['ERA5_u_850hPa']**2 + df['ERA5_v_850hPa']**2
)

# 5. 700-850 hPa wind shear (low-level jet proxy)
df['wind_shear_700_850'] = np.sqrt(
    (df['ERA5_u_700hPa'] - df['ERA5_u_850hPa'])**2 +
    (df['ERA5_v_700hPa'] - df['ERA5_v_850hPa'])**2
)

# 6. Equivalent potential temperature proxy at 850 hPa
# theta_e ≈ T + (Lv/Cp) * q  (simplified)
Lv_Cp = 2501000 / 1004  # ~2491 K/(kg/kg)
df['thetae_850'] = (df['ERA5_t_850hPa'] +
                    Lv_Cp * df['ERA5_q_850hPa'])

# 7. CAPE × K_INDEX interaction (both top SHAP features for Slot 2)
df['cape_x_kindex'] = df['CAPE'] * df['K_INDEX']

# 8. Lifted Index × Totals-Totals interaction
df['li_x_totals'] = df['LIFTED_INDEX'].abs() * df['TOTALS_TOTALS']

# 9. Moisture flux divergence proxy (700 hPa)
df['moisture_flux_700'] = df['ERA5_q_700hPa'] * np.sqrt(
    df['ERA5_u_700hPa']**2 + df['ERA5_v_700hPa']**2
)

# 10. Mid-level drying indicator (q700/q850 ratio — low = mid-level dry air)
df['mid_level_drying'] = df['ERA5_q_700hPa'] / (df['ERA5_q_850hPa'] + 1e-9)

NEW_FEATURES = [
    'q_gradient_500_850', 'wind_shear_500_850', 'thickness_500_850',
    'moisture_flux_850', 'wind_shear_700_850', 'thetae_850',
    'cape_x_kindex', 'li_x_totals', 'moisture_flux_700', 'mid_level_drying'
]

print(f"  Added {len(NEW_FEATURES)} new features:")
for f in NEW_FEATURES:
    corr = df[df['slot']==2][f].corr(df[df['slot']==2]['ts_label'])
    print(f"    {f:<25} r={corr:.3f}")

df.to_csv(OUT_V3, index=False)
print(f"\n  Saved v3 dataset → {OUT_V3}")
print(f"  Shape: {df.shape} (was 60 cols, now {df.shape[1]} cols)")

# ── STEP 2: TRAIN PER-SLOT MODELS ─────────────────────────────────────────────
print("\n[2/3] Training v3 slot models...")

FEATURE_COLS = [c for c in df.columns if c not in
                ['date','year','month','slot','slot_label','ts_label']]
print(f"  Total features: {len(FEATURE_COLS)}")

all_results = []

for slot_id in range(4):
    slot_name  = SLOT_NAMES[slot_id]
    print(f"\n{'='*60}")
    print(f"SLOT {slot_id} — {slot_name}")

    slot_df    = df[df['slot']==slot_id].copy()
    train_slot = slot_df[slot_df['year'] < 2023]
    test_slot  = slot_df[slot_df['year'] >= 2023]

    X_train = train_slot[FEATURE_COLS].values
    y_train = train_slot['ts_label'].values
    X_test  = test_slot[FEATURE_COLS].values
    y_test  = test_slot['ts_label'].values

    n_pos = y_train.sum()
    n_neg = (y_train==0).sum()
    spw   = n_neg/n_pos if n_pos>0 else 1.0

    print(f"Train: {len(y_train)} | {n_pos} pos | SPW={spw:.1f}")
    print(f"Test:  {len(y_test)}  | {y_test.sum()} pos")

    if slot_id in SKIP_OPTUNA:
        best_params = {
            'n_estimators':200,'max_depth':4,'learning_rate':0.05,
            'subsample':0.8,'colsample_bytree':0.8,'min_child_weight':5,
            'reg_alpha':5.0,'reg_lambda':5.0,
            'scale_pos_weight':spw,'random_state':RANDOM_STATE,'eval_metric':'auc',
        }
        cv_auroc = float('nan')
    else:
        def objective(trial):
            params = {
                'n_estimators':        trial.suggest_int('n_estimators',100,600),
                'max_depth':           trial.suggest_int('max_depth',3,7),
                'learning_rate':       trial.suggest_float('learning_rate',0.01,0.2,log=True),
                'subsample':           trial.suggest_float('subsample',0.6,1.0),
                'colsample_bytree':    trial.suggest_float('colsample_bytree',0.6,1.0),
                'min_child_weight':    trial.suggest_int('min_child_weight',1,10),
                'reg_alpha':           trial.suggest_float('reg_alpha',1e-4,10.0,log=True),
                'reg_lambda':          trial.suggest_float('reg_lambda',1e-4,10.0,log=True),
                'scale_pos_weight':    spw,'random_state':RANDOM_STATE,
                'eval_metric':'auc','early_stopping_rounds':30,
            }
            skf = StratifiedKFold(n_splits=N_FOLDS,shuffle=True,
                                  random_state=RANDOM_STATE)
            scores = []
            for tr_idx,val_idx in skf.split(X_train,y_train):
                m = make_xgb_es(params)
                m.fit(X_train[tr_idx],y_train[tr_idx],
                      eval_set=[(X_train[val_idx],y_train[val_idx])],
                      verbose=False)
                prob = m.predict_proba(X_train[val_idx])[:,1]
                if y_train[val_idx].sum()>0:
                    scores.append(roc_auc_score(y_train[val_idx],prob))
            return np.mean(scores) if scores else 0.5

        print(f"Running Optuna ({N_TRIALS} trials)...")
        study = optuna.create_study(
            direction='maximize',
            sampler=optuna.samplers.TPESampler(seed=RANDOM_STATE))
        study.optimize(objective,n_trials=N_TRIALS,show_progress_bar=True)
        cv_auroc    = round(study.best_value,4)
        best_params = study.best_params
        best_params.update({
            'scale_pos_weight':spw,'random_state':RANDOM_STATE,'eval_metric':'auc'
        })
        print(f"Best CV AUROC: {cv_auroc}")

    # Train final model
    print("Training final model...")
    model = make_xgb(best_params)
    model.fit(X_train,y_train)

    # OOF threshold
    print("Tuning threshold...")
    skf_t    = StratifiedKFold(n_splits=N_FOLDS,shuffle=True,
                                random_state=RANDOM_STATE)
    oof_prob = np.zeros(len(y_train))
    for tr_idx,val_idx in skf_t.split(X_train,y_train):
        m = make_xgb(best_params)
        m.fit(X_train[tr_idx],y_train[tr_idx])
        oof_prob[val_idx] = m.predict_proba(X_train[val_idx])[:,1]
    threshold = find_best_threshold(y_train,oof_prob)
    print(f"  Threshold: {threshold}")

    # Evaluate
    test_prob = model.predict_proba(X_test)[:,1]
    metrics   = compute_metrics(y_test,test_prob,threshold)
    print(f"\n  AUROC={metrics['AUROC']} POD={metrics['POD']} "
          f"FAR={metrics['FAR']} CSI={metrics['CSI']} HSS={metrics['HSS']}")

    # Save
    model_path = MODELS / f"nowcast_slot{slot_id}_xgb_v3.pkl"
    joblib.dump({
        'model':model,'feature_cols':FEATURE_COLS,
        'threshold':threshold,'best_params':best_params,
        'slot_id':slot_id,'slot_name':slot_name,'era5_version':'6hourly_v3',
    }, model_path)
    print(f"  Saved → {model_path}")

    row = {'Slot':slot_id,'Slot_label':slot_name,
           'CV_AUROC':cv_auroc,'Threshold':threshold}
    row.update(metrics)
    all_results.append(row)

# ── STEP 3: SUMMARY ───────────────────────────────────────────────────────────
print(f"\n[3/3] Summary...")
results_df = pd.DataFrame(all_results)
test_all   = df[df['year']>=2023]
weights    = [test_all[test_all['slot']==s]['ts_label'].sum() for s in range(4)]
total_w    = sum(weights)

w_auroc = sum(results_df['AUROC'].fillna(0)*weights)/total_w
w_pod   = sum(results_df['POD']*weights)/total_w
w_far   = sum(results_df['FAR']*weights)/total_w
w_csi   = sum(results_df['CSI']*weights)/total_w
w_hss   = sum(results_df['HSS']*weights)/total_w

print(f"\n{'='*60}")
print("SLOT MODEL SUMMARY v3 — WITH DERIVED FEATURES")
print(f"{'='*60}")
print(f"\n{'Slot':<6} {'Window':<16} {'AUROC':<8} {'POD':<7} "
      f"{'FAR':<7} {'CSI':<7} {'HSS':<7} {'Thresh'}")
print("-"*70)
for _,r in results_df.iterrows():
    print(f"  {int(r['Slot']):<5} {r['Slot_label']:<16} {r['AUROC']:<8} "
          f"{r['POD']:<7} {r['FAR']:<7} {r['CSI']:<7} {r['HSS']:<7} {r['Threshold']}")
print("-"*70)
print(f"  {'WEIGHTED v3':<22} {w_auroc:<8.4f} {w_pod:<7.4f} "
      f"{w_far:<7.4f} {w_csi:<7.4f} {w_hss:<7.4f}")
print(f"\n  vs v2: AUROC=0.8352 POD=0.3178 FAR=0.7237 CSI=0.1691 HSS=0.2400")
print(f"  vs Daily baseline: AUROC=0.8715 POD=0.500 FAR=0.586 CSI=0.293 HSS=0.389")

results_df.to_csv(RESULTS/"evaluation_results_per_slot_v3.csv",index=False)
print(f"\nSaved → {RESULTS/'evaluation_results_per_slot_v3.csv'}")
print("A12 complete.")
