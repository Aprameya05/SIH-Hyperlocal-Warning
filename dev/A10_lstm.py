"""
A10_lstm.py
===========
LSTM temporal model for daily thunderstorm prediction.

Captures sequential patterns that XGBoost misses:
  - Multi-day moisture build-up before a storm
  - Consecutive storm days
  - Weekly atmospheric cycles

Architecture:
  Input:  (7, 29) — 7 days lookback × 29 daily features
  LSTM(64) → Dropout(0.3) → LSTM(32) → Dropout(0.2) → Dense(1, sigmoid)

Target: daily_label (did ANY slot have a thunderstorm today?)

Comparison:
  - XGBoost daily baseline: AUROC 0.871, HSS 0.389
  - Ensemble (A9):          AUROC 0.846, HSS 0.365
  - LSTM (this script):     TBD

Output:
  models/lstm_daily_v1.keras
  results/lstm_results.csv
  results/shap_figures_v2/chart12_lstm.png

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
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, brier_score_loss

warnings.filterwarnings('ignore')

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA    = BASE / "data"    / "bengaluru_6hr_training_dataset_v2.csv"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
FIGS    = BASE / "results" / "shap_figures_v2"

LOOKBACK     = 7     # days of history to use
RANDOM_STATE = 42
np.random.seed(RANDOM_STATE)

DAILY_FEATURES = [
    'MAX','MIN','DTR','RF','EVP','DRNRF','SSH',
    'RF_3d','RF_7d','MAX_3d_avg','MIN_3d_avg',
    'CAPE','K_INDEX','LIFTED_INDEX','TOTALS_TOTALS',
    'ERA5_T2M','ERA5_D2M','ERA5_CAPE','ERA5_SP',
    'ERA5_t_850hPa','ERA5_q_850hPa',
    'MONTH_sin','MONTH_cos','DOY_sin','DOY_cos','SEASON',
    'RF_lag1','LABEL_lag1','ts_any_yesterday',
]

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

# ── LOAD + BUILD DAILY DATASET ────────────────────────────────────────────────
print("=" * 60)
print("A10 — LSTM Temporal Model")
print("=" * 60)

print("\n[1/6] Loading and building daily dataset...")
df = pd.read_csv(DATA, parse_dates=['date'])

# Use slot 2 as representative daily row (most informative slot)
slot2 = df[df['slot']==2].copy()
slot2 = slot2.sort_values('date').reset_index(drop=True)

# Daily label
daily_label = df.groupby('date')['ts_label'].max().rename('daily_label')
slot2 = slot2.merge(daily_label.reset_index(), on='date', how='left')

# Check features
available_feats = [f for f in DAILY_FEATURES if f in slot2.columns]
print(f"  Features available: {len(available_feats)}/{len(DAILY_FEATURES)}")
print(f"  Daily rows: {len(slot2)}")
print(f"  TS days: {slot2['daily_label'].sum()} ({slot2['daily_label'].mean()*100:.1f}%)")

# Split
train_daily = slot2[slot2['year'] < 2023].reset_index(drop=True)
test_daily  = slot2[slot2['year'] >= 2023].reset_index(drop=True)
print(f"  Train: {len(train_daily)} days | {train_daily['daily_label'].sum()} TS")
print(f"  Test:  {len(test_daily)} days  | {test_daily['daily_label'].sum()} TS")

# ── SEQUENCE BUILDER ──────────────────────────────────────────────────────────
print(f"\n[2/6] Building {LOOKBACK}-day sequences...")

def build_sequences(daily_df, features, lookback, scaler=None, fit_scaler=False):
    X_raw = daily_df[features].fillna(0).values.astype(np.float32)
    y     = daily_df['daily_label'].values.astype(np.float32)

    if fit_scaler:
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_raw)
    else:
        X_scaled = scaler.transform(X_raw)

    X_seq, y_seq = [], []
    for i in range(lookback, len(X_scaled)):
        X_seq.append(X_scaled[i-lookback:i])
        y_seq.append(y[i])

    return np.array(X_seq), np.array(y_seq), scaler

X_train, y_train, scaler = build_sequences(
    train_daily, available_feats, LOOKBACK, fit_scaler=True)
X_test, y_test, _ = build_sequences(
    test_daily, available_feats, LOOKBACK, scaler=scaler)

print(f"  X_train: {X_train.shape} | y_train pos: {y_train.sum():.0f}")
print(f"  X_test:  {X_test.shape}  | y_test pos:  {y_test.sum():.0f}")

# ── BUILD LSTM ────────────────────────────────────────────────────────────────
print("\n[3/6] Building LSTM model...")
try:
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers, callbacks
    tf.random.set_seed(RANDOM_STATE)
    print(f"  TensorFlow version: {tf.__version__}")
except ImportError:
    print("  TensorFlow not found. Installing...")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "tensorflow", "--quiet"])
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers, callbacks
    print(f"  TensorFlow installed: {tf.__version__}")

n_features = X_train.shape[2]
pos_weight = float((y_train==0).sum() / (y_train==1).sum())
print(f"  Input shape: ({LOOKBACK}, {n_features})")
print(f"  Class weight (pos): {pos_weight:.1f}")

model = keras.Sequential([
    layers.Input(shape=(LOOKBACK, n_features)),
    layers.Conv1D(64, kernel_size=3, activation='relu', padding='same',
                  kernel_regularizer=keras.regularizers.l2(1e-4)),
    layers.Dropout(0.2),
    layers.Conv1D(32, kernel_size=3, activation='relu', padding='same',
                  kernel_regularizer=keras.regularizers.l2(1e-4)),
    layers.GlobalMaxPooling1D(),
    layers.Dense(32, activation='relu'),
    layers.Dropout(0.2),
    layers.Dense(1, activation='sigmoid'),
])

model.compile(
    optimizer=keras.optimizers.Adam(learning_rate=0.001),
    loss='binary_crossentropy',
    metrics=['AUC'],
)
model.summary()

# ── TRAIN ─────────────────────────────────────────────────────────────────────
print("\n[4/6] Training LSTM...")
cb_list = [
    callbacks.EarlyStopping(monitor='val_loss', patience=20,
                            restore_best_weights=True, verbose=1),
    callbacks.ReduceLROnPlateau(monitor='val_loss', factor=0.5,
                                patience=8, min_lr=1e-5, verbose=1),
]

history = model.fit(
    X_train, y_train,
    epochs=150,
    batch_size=32,
    validation_split=0.15,
    class_weight={0: 1.0, 1: pos_weight},
    callbacks=cb_list,
    verbose=1,
)

# ── THRESHOLD TUNING ──────────────────────────────────────────────────────────
print("\n[5/6] Tuning threshold on training set...")
train_prob = model.predict(X_train, verbose=0).flatten()
threshold  = find_best_threshold(y_train, train_prob)
print(f"  Best threshold: {threshold}")

# ── EVALUATE ──────────────────────────────────────────────────────────────────
print("\nEvaluating on test set...")
test_prob = model.predict(X_test, verbose=0).flatten()
metrics   = compute_metrics(y_test, test_prob, threshold)

print("\n" + "="*60)
print("LSTM RESULTS")
print("="*60)
print(f"\n{'Model':<30} {'AUROC':<8} {'POD':<7} {'FAR':<7} {'CSI':<7} {'HSS':<7} {'Brier'}")
print("-"*65)
print(f"  {'XGBoost daily (baseline)':<28} {'0.8715':<8} {'0.500':<7} {'0.586':<7} {'0.293':<7} {'0.389':<7}")
print(f"  {'Ensemble A9':<28}             {'0.8456':<8} {'0.385':<7} {'0.538':<7} {'0.266':<7} {'0.365':<7}")
print(f"  {'LSTM (7-day sequence)':<28} {metrics['AUROC']:<8} {metrics['POD']:<7} "
      f"{metrics['FAR']:<7} {metrics['CSI']:<7} {metrics['HSS']:<7} {metrics['Brier']}")

# ── CHART ─────────────────────────────────────────────────────────────────────
print("\n[6/6] Building charts...")
fig, axes = plt.subplots(1, 3, figsize=(16, 5))

# Training curves
ax = axes[0]
ax.plot(history.history['loss'],     label='Train loss', color='#2C3E50')
ax.plot(history.history['val_loss'], label='Val loss',   color='#E74C3C')
ax.set_xlabel('Epoch', fontsize=10)
ax.set_ylabel('Loss', fontsize=10)
ax.set_title('LSTM Training Curves', fontsize=11)
ax.legend(fontsize=9)
ax.grid(alpha=0.3)
ax.spines[['top','right']].set_visible(False)

# Probability distribution
ax = axes[1]
ts_prob  = test_prob[y_test==1]
nts_prob = test_prob[y_test==0]
ax.hist(nts_prob, bins=20, alpha=0.7, color='#3498DB',
        label=f'No TS (n={len(nts_prob)})', density=True)
ax.hist(ts_prob,  bins=20, alpha=0.7, color='#E74C3C',
        label=f'TS day (n={len(ts_prob)})',  density=True)
ax.axvline(threshold, color='black', linestyle='--',
           linewidth=1.5, label=f'Threshold={threshold}')
ax.set_xlabel('LSTM Predicted Probability', fontsize=10)
ax.set_ylabel('Density', fontsize=10)
ax.set_title('LSTM Probability Distribution\nTS vs Non-TS Days', fontsize=10)
ax.legend(fontsize=9)
ax.grid(alpha=0.3)
ax.spines[['top','right']].set_visible(False)

# Model comparison
ax = axes[2]
model_names = ['XGBoost\nBaseline','Ensemble\nA9','LSTM\n7-day']
auroc_vals  = [0.8715, 0.8456, metrics['AUROC']]
hss_vals    = [0.389,  0.365,  metrics['HSS']]
csi_vals    = [0.293,  0.266,  metrics['CSI']]

x = np.arange(3)
w = 0.25
ax.bar(x-w, auroc_vals, w, label='AUROC', color='#2C3E50', alpha=0.85)
ax.bar(x,   hss_vals,   w, label='HSS',   color='#27AE60', alpha=0.85)
ax.bar(x+w, csi_vals,   w, label='CSI',   color='#E67E22', alpha=0.85)
for i,(a,h,c) in enumerate(zip(auroc_vals,hss_vals,csi_vals)):
    ax.text(i-w, a+0.005, f'{a:.3f}', ha='center', fontsize=7)
    ax.text(i,   h+0.005, f'{h:.3f}', ha='center', fontsize=7)
    ax.text(i+w, c+0.005, f'{c:.3f}', ha='center', fontsize=7)
ax.set_xticks(x)
ax.set_xticklabels(model_names, fontsize=10)
ax.set_ylabel('Score', fontsize=10)
ax.set_title('Model Comparison', fontsize=11)
ax.legend(fontsize=9)
ax.grid(axis='y', alpha=0.3)
ax.spines[['top','right']].set_visible(False)
ax.set_ylim(0, 1.05)

plt.suptitle('LSTM Temporal Model — Daily Thunderstorm Prediction\n'
             '7-Day Lookback Sequence', fontsize=12, y=1.01)
plt.tight_layout()
path = FIGS / "chart12_lstm.png"
fig.savefig(path, dpi=150, bbox_inches='tight')
plt.close()
print(f"  Chart saved → {path}")
print(f"\nModel saved → {MODELS / 'cnn1d_daily_v1.keras'}")
# ── SAVE ──────────────────────────────────────────────────────────────────────
model.save(MODELS / "cnn1d_daily_v1.keras")
joblib.dump({'scaler': scaler, 'features': available_feats,
             'lookback': LOOKBACK, 'threshold': threshold},
            MODELS / "lstm_daily_v1_meta.pkl")

pd.DataFrame([{'model':'LSTM_daily_v1', **metrics,
               'threshold': threshold, 'lookback': LOOKBACK}]).to_csv(
    RESULTS / "lstm_results.csv", index=False)

print(f"\nModel saved → {MODELS / 'cnn1d_daily_v1.keras'}")
print("A10 complete.")
