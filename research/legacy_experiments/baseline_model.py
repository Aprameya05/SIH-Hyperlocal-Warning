"""
CSIR Thunderstorm Prediction — Bengaluru
Step 2: Baseline XGBoost + LightGBM (surface features only)
"""
from sklearn.ensemble import RandomForestClassifier
import pandas as pd
import numpy as np
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (classification_report, roc_auc_score, 
                             confusion_matrix, average_precision_score)
from sklearn.linear_model import LogisticRegression
import xgboost as xgb
import lightgbm as lgb
import warnings
warnings.filterwarnings('ignore')

# ─── LOAD ────────────────────────────────────────────────────────────────────
df = pd.read_csv('bengaluru_thunderstorm_features_v2.csv')
df['date'] = pd.to_datetime(df['date'])

SURFACE_FEATURES = [
    'MAX', 'MIN', 'DTR', 'AW', 'RF', 'EVP', 'DRNRF', 'SSH',
    'RF_3d', 'RF_7d', 'MAX_3d_avg', 'MIN_3d_avg', 'DTR_3d_avg',
    'RF_lag1', 'MAX_lag1', 'MIN_lag1',
    'LABEL_lag1',
    'MONTH_sin', 'MONTH_cos', 'DOY_sin', 'DOY_cos', 'SEASON',
    'HA_flag', 'RF_nonzero',
]

X = df[SURFACE_FEATURES].fillna(0)
y = df['LABEL']
dates = df['date']

print(f"Dataset: {len(X)} samples | {y.mean()*100:.1f}% positive")

# ─── TEMPORAL TRAIN/TEST SPLIT ───────────────────────────────────────────────
# Train: 2015-2022 | Test: 2023-2025 (temporal holdout, no data leakage)
train_mask = df['YEAR'] <= 2022
test_mask  = df['YEAR'] >= 2023

X_train, y_train = X[train_mask], y[train_mask]
X_test,  y_test  = X[test_mask],  y[test_mask]

print(f"Train: {len(X_train)} | Test: {len(X_test)}")
print(f"Train positive rate: {y_train.mean()*100:.1f}%")
print(f"Test positive rate:  {y_test.mean()*100:.1f}%")

scale_pos = (y_train == 0).sum() / (y_train == 1).sum()

# ─── MODELS ──────────────────────────────────────────────────────────────────
models = {
    'XGBoost': xgb.XGBClassifier(
        n_estimators=300, max_depth=5, learning_rate=0.05,
        scale_pos_weight=scale_pos,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, eval_metric='logloss', verbosity=0
    ),
    'LightGBM': lgb.LGBMClassifier(
        n_estimators=300, max_depth=5, learning_rate=0.05,
        scale_pos_weight=scale_pos,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbose=-1
    ),
    'Logistic (baseline)': LogisticRegression(
        class_weight='balanced', max_iter=1000, random_state=42
    ),
    'Random Forest': RandomForestClassifier(
    n_estimators=300, class_weight='balanced',
    max_features='sqrt', random_state=42, n_jobs=-1
)
}

results = {}
for name, model in models.items():
    if 'Logistic' in name:
        scaler = StandardScaler()
        Xtr = scaler.fit_transform(X_train)
        Xte = scaler.transform(X_test)
    else:
        Xtr, Xte = X_train.values, X_test.values

    model.fit(Xtr, y_train)
    proba = model.predict_proba(Xte)[:, 1]
    pred  = (proba >= 0.4).astype(int)   # lower threshold → better recall

    auroc = roc_auc_score(y_test, proba)
    auprc = average_precision_score(y_test, proba)
    cm    = confusion_matrix(y_test, pred)
    tn, fp, fn, tp = cm.ravel()
    recall    = tp / (tp + fn) if (tp+fn) > 0 else 0
    precision = tp / (tp + fp) if (tp+fp) > 0 else 0
    f1 = 2*precision*recall/(precision+recall) if (precision+recall) > 0 else 0

    results[name] = {
        'AUROC': auroc, 'AUPRC': auprc,
        'Recall': recall, 'Precision': precision, 'F1': f1,
        'TP': tp, 'FP': fp, 'TN': tn, 'FN': fn
    }
    print(f"\n{'='*50}")
    print(f"{name}")
    print(f"  AUROC={auroc:.4f}  AUPRC={auprc:.4f}")
    print(f"  Recall={recall:.3f}  Precision={precision:.3f}  F1={f1:.3f}")
    print(f"  TP={tp}  FP={fp}  TN={tn}  FN={fn}")

# ─── FEATURE IMPORTANCE (XGBoost) ────────────────────────────────────────────
xgb_model = models['XGBoost']
fi = pd.Series(xgb_model.feature_importances_, index=SURFACE_FEATURES)
fi = fi.sort_values(ascending=False)
print(f"\n── XGBoost Feature Importance (top 10) ──")
print(fi.head(10).round(4).to_string())

fi.to_csv('feature_importance.csv')
print("\nSaved: data/feature_importance.csv")
