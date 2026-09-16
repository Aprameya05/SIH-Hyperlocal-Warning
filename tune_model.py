import pandas as pd, numpy as np, optuna, joblib
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
import xgboost as xgb
import warnings; warnings.filterwarnings('ignore')
optuna.logging.set_verbosity(optuna.logging.WARNING)

df = pd.read_csv('bengaluru_thunderstorm_features_merged.csv')

FEATURES = [
    # Surface obs
    'MAX','MIN','DTR','AW','RF','EVP','DRNRF','SSH',
    'RF_3d','RF_7d','MAX_3d_avg','MIN_3d_avg','DTR_3d_avg',
    'RF_lag1','MAX_lag1','MIN_lag1','LABEL_lag1',
    'MONTH_sin','MONTH_cos','DOY_sin','DOY_cos',
    'RF_nonzero',
    # IGRA sounding indices (drop ERA5_CAPE — CAPE is from sounding, more accurate)
    'CAPE','K_INDEX','LIFTED_INDEX','TOTALS_TOTALS','PRECIP_WATER',
    # ERA5 surface (drop ERA5_CAPE since we have sounding CAPE)
    'ERA5_T2M','ERA5_D2M','ERA5_U10','ERA5_V10','ERA5_SP',
    # ERA5 pressure levels — keep q and u/v, drop t levels (redundant with indices)
    'ERA5_q_500hPa','ERA5_q_700hPa','ERA5_q_850hPa',
    'ERA5_u_500hPa','ERA5_u_700hPa','ERA5_u_850hPa',
    'ERA5_v_500hPa','ERA5_v_700hPa','ERA5_v_850hPa',
]

X_train = df[df['YEAR'] <= 2022][FEATURES].fillna(0)
y_train = df[df['YEAR'] <= 2022]['LABEL']
X_test  = df[df['YEAR'] >= 2023][FEATURES].fillna(0)
y_test  = df[df['YEAR'] >= 2023]['LABEL']

scale_pos = (y_train==0).sum()/(y_train==1).sum()

def objective(trial):
    params = {
        'n_estimators':     trial.suggest_int('n_estimators', 200, 800),
        'max_depth':        trial.suggest_int('max_depth', 3, 8),
        'learning_rate':    trial.suggest_float('learning_rate', 0.01, 0.15, log=True),
        'subsample':        trial.suggest_float('subsample', 0.6, 1.0),
        'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
        'min_child_weight': trial.suggest_int('min_child_weight', 1, 10),
        'gamma':            trial.suggest_float('gamma', 0, 5),
        'scale_pos_weight': scale_pos,
        'random_state': 42, 'eval_metric': 'logloss', 'verbosity': 0
    }
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    scores = []
    for tr_idx, val_idx in cv.split(X_train, y_train):
        m = xgb.XGBClassifier(**params)
        m.fit(X_train.iloc[tr_idx], y_train.iloc[tr_idx])
        scores.append(roc_auc_score(y_train.iloc[val_idx],
                                    m.predict_proba(X_train.iloc[val_idx])[:,1]))
    return np.mean(scores)

print("Running 50 trials — takes about 5 mins...")
study = optuna.create_study(direction='maximize')
study.optimize(objective, n_trials=50, show_progress_bar=True)

print("\nBest CV AUROC:", round(study.best_value, 4))
print("Best params:", study.best_params)

best = xgb.XGBClassifier(**study.best_params,
                           scale_pos_weight=scale_pos,
                           random_state=42, verbosity=0)
best.fit(X_train, y_train)

proba = best.predict_proba(X_test)[:,1]
auroc = roc_auc_score(y_test, proba)
print("\nTest AUROC:", round(auroc, 4))

joblib.dump({'model': best, 'features': FEATURES, 'threshold': 0.45}, 'thunderstorm_model.pkl')
print("Saved: thunderstorm_model.pkl")