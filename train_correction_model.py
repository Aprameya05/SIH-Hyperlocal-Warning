"""
train_correction_model.py — Satellite Correction Model for CSIR Thunderstorm System
Trains a logistic correction model that adjusts XGBoost base probabilities
using atmospheric proxies for Himawari-9 satellite cold-top signal.

When real Himawari historical BT data is available (Atul's archive pull),
replace 'cold_top_proxy' with actual min_bt_50km and cold_pixels_count.

Usage: python train_correction_model.py
Output: models/himawari_correction_model.pkl
        models/correction_model_meta.json

Author: Aprameya (ML Lead), CSIR Thunderstorm Project
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, brier_score_loss, classification_report
import joblib, os, json
from pathlib import Path

BASE = Path(__file__).resolve().parent
FEATURES_CSV = BASE / 'bengaluru_thunderstorm_features_merged.csv'
MODELS_DIR   = BASE / 'models'
MODEL_PATH   = MODELS_DIR / 'himawari_correction_model.pkl'
META_PATH    = MODELS_DIR / 'correction_model_meta.json'

CORRECTION_FEATURES = [
    'ERA5_CAPE',            # convective available potential energy (J/kg)
    'cold_top_proxy',       # ERA5 500hPa T anomaly — proxy for cold cloud tops
    'mid_moisture',         # 700hPa specific humidity (g/kg scale)
    'lapse_rate_850_500',   # 850-500 hPa temperature difference (instability)
    'shear_850_500',        # bulk wind shear 850-500 hPa (m/s)
    'K_INDEX',              # K-Index
    'LIFTED_INDEX',         # Lifted Index
    'TOTALS_TOTALS',        # Totals-Totals Index
    'PRECIP_WATER',         # precipitable water (mm)
    'cape_x_ki',            # CAPE × K-Index interaction
    'MONTH_sin',            # seasonal cycle (sin)
    'MONTH_cos',            # seasonal cycle (cos)
]

def engineer_features(df):
    t500_mean = df['ERA5_t_500hPa'].mean()
    df['cold_top_proxy']      = t500_mean - df['ERA5_t_500hPa']
    df['mid_moisture']        = df['ERA5_q_700hPa'] * 1000
    df['lapse_rate_850_500']  = df['ERA5_t_850hPa'] - df['ERA5_t_500hPa']
    df['shear_850_500']       = np.sqrt(
        (df['ERA5_u_500hPa'] - df['ERA5_u_850hPa'])**2 +
        (df['ERA5_v_500hPa'] - df['ERA5_v_850hPa'])**2
    )
    df['cape_x_ki'] = df['ERA5_CAPE'] * df['K_INDEX']
    return df

def main():
    print("=" * 60)
    print("  CSIR Thunderstorm — Correction Model Training")
    print("=" * 60)

    df = pd.read_csv(FEATURES_CSV)
    df['date'] = pd.to_datetime(df['date'])
    df = engineer_features(df)

    X = df[CORRECTION_FEATURES].fillna(0).values
    y = df['LABEL'].values

    print(f"\nDataset: {len(X)} samples | Positives: {y.sum()} ({y.mean()*100:.1f}%)")

    # Base pipeline
    pipe = Pipeline([
        ('scaler', StandardScaler()),
        ('clf', LogisticRegression(C=0.5, class_weight='balanced',
                                    max_iter=1000, random_state=42))
    ])

    # 5-fold stratified CV
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    aucs = cross_val_score(pipe, X, y, cv=cv, scoring='roc_auc')
    print(f"\n5-Fold CV AUROC: {aucs.mean():.4f} ± {aucs.std():.4f}")

    # Calibrated model
    calibrated = CalibratedClassifierCV(pipe, method='isotonic', cv=5)
    calibrated.fit(X, y)

    probs = calibrated.predict_proba(X)[:, 1]
    full_auroc = roc_auc_score(y, probs)
    brier      = brier_score_loss(y, probs)

    print(f"Full AUROC:   {full_auroc:.4f}")
    print(f"Brier Score:  {brier:.4f}")
    print(f"\nMean prob — Storm days: {probs[y==1].mean():.4f}")
    print(f"Mean prob — Clear days: {probs[y==0].mean():.4f}")
    print(f"Discrimination ratio:   {probs[y==1].mean()/probs[y==0].mean():.1f}x")

    # Save
    MODELS_DIR.mkdir(exist_ok=True)
    joblib.dump(calibrated, MODEL_PATH)

    # Save t500 mean for inference (needed to compute cold_top_proxy in real-time)
    t500_mean = float(df['ERA5_t_500hPa'].mean())
    q700_scale = 1000.0

    meta = {
        'features':        CORRECTION_FEATURES,
        'cv_auroc_mean':   float(aucs.mean()),
        'cv_auroc_std':    float(aucs.std()),
        'full_auroc':      float(full_auroc),
        'brier_score':     float(brier),
        'n_train':         int(len(X)),
        'n_positives':     int(y.sum()),
        'base_rate':       float(y.mean()),
        't500_climatological_mean': t500_mean,
        'q700_scale':      q700_scale,
        'trained_on':      '2015-2025 ERA5+IMD Bengaluru Station 43295',
        'model_type':      'LogisticRegression(C=0.5, balanced) + IsotonicCalibration',
        'note':            'cold_top_proxy = t500_mean - ERA5_t_500hPa. Replace with real Himawari min_bt_50km when archive available.',
    }
    with open(META_PATH, 'w') as f:
        json.dump(meta, f, indent=2)

    print(f"\n✓ Model → {MODEL_PATH}")
    print(f"✓ Meta  → {META_PATH}")
    print(f"\nKey inference parameters:")
    print(f"  t500_climatological_mean = {t500_mean:.4f} K")
    print(f"  q700_scale = {q700_scale}")

if __name__ == '__main__':
    main()
