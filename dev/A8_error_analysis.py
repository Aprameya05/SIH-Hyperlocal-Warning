"""
A8_error_analysis.py
====================
Deep error analysis on the 2023-2025 test set.

Categorises every prediction into:
  TP  — True Positive  (caught storm)
  FP  — False Positive (false alarm)
  FN  — False Negative (missed storm)
  TN  — True Negative  (correct no-storm)

Then analyses what separates misses (FN) from hits (TP),
and false alarms (FP) from correct negatives (TN).

Key questions answered:
  1. Which months have most misses?
  2. What CAPE/K-Index values characterise missed storms?
  3. Are false alarms clustered in certain seasons?
  4. Which features differ most between hits and misses?
  5. Is there a pattern in consecutive missed days?

Focus: Slot 2 (operational window, most test cases)

Output:
  results/error_analysis_results.csv
  results/shap_figures_v2/chart10_error_analysis.png

Author: Aprameya, CSIR Thunderstorm Project
"""

import pandas as pd
import numpy as np
import joblib
import warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from pathlib import Path
from sklearn.isotonic import IsotonicRegression

warnings.filterwarnings('ignore')

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA    = BASE / "data"    / "bengaluru_6hr_training_dataset_v2.csv"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
FIGS    = BASE / "results" / "shap_figures_v2"

SLOT_NAMES = {0:"0001-0600",1:"0601-1200",2:"1201-1800",3:"1801-2400"}
MONTH_NAMES = {1:'Jan',2:'Feb',3:'Mar',4:'Apr',5:'May',6:'Jun',
               7:'Jul',8:'Aug',9:'Sep',10:'Oct',11:'Nov',12:'Dec'}

# ── LOAD ──────────────────────────────────────────────────────────────────────
print("=" * 60)
print("A8 — Deep Error Analysis")
print("=" * 60)

df = pd.read_csv(DATA, parse_dates=['date'])
test = df[df['year'] >= 2023].copy()

def apply_calibrator(artifact, raw_prob):
    calibrator   = artifact.get('calibrator')
    calib_method = artifact.get('calib_method','none')
    if calibrator is None:
        return raw_prob
    if calib_method == 'sigmoid':
        return calibrator.predict_proba(raw_prob.reshape(-1,1))[:,1]
    return calibrator.predict(raw_prob)

# ── GET PREDICTIONS FOR ALL SLOTS ─────────────────────────────────────────────
print("\nGenerating predictions on test set...")
test['prob']     = 0.0
test['predicted'] = 0
test['outcome']   = 'TN'

for slot_id in range(4):
    artifact     = joblib.load(MODELS / f"nowcast_slot{slot_id}_xgb_v2_calibrated.pkl")
    model        = artifact['model']
    feature_cols = artifact['feature_cols']
    threshold    = artifact['threshold']

    slot_mask = test['slot'] == slot_id
    X         = test.loc[slot_mask, feature_cols].values
    raw_prob  = model.predict_proba(X)[:,1]
    cal_prob  = apply_calibrator(artifact, raw_prob)
    predicted = (cal_prob >= threshold).astype(int)

    test.loc[slot_mask, 'prob']      = cal_prob
    test.loc[slot_mask, 'predicted'] = predicted

    # Classify outcomes
    y = test.loc[slot_mask, 'ts_label'].values
    outcomes = np.where((predicted==1)&(y==1), 'TP',
               np.where((predicted==1)&(y==0), 'FP',
               np.where((predicted==0)&(y==1), 'FN', 'TN')))
    test.loc[slot_mask, 'outcome'] = outcomes

    counts = pd.Series(outcomes).value_counts()
    print(f"  Slot {slot_id}: TP={counts.get('TP',0)} FP={counts.get('FP',0)} "
          f"FN={counts.get('FN',0)} TN={counts.get('TN',0)}")

# Focus on Slot 2 for deep analysis
s2 = test[test['slot'] == 2].copy()
s2['month_name'] = s2['month'].map(MONTH_NAMES)

print(f"\nSlot 2 test set: {len(s2)} rows, {s2['ts_label'].sum()} positives")
print(f"Outcomes: {s2['outcome'].value_counts().to_dict()}")

# ── ANALYSIS FEATURES ─────────────────────────────────────────────────────────
analysis_features = {
    'CAPE':             'CAPE (J/kg)',
    'K_INDEX':          'K-Index',
    'LIFTED_INDEX':     'Lifted Index',
    'TOTALS_TOTALS':    'Totals-Totals',
    'ERA5_T2M':         'ERA5 T2M (K)',
    'MAX':              'Max Temp (°C)',
    'RF':               'Rainfall (mm)',
    'slot_month_clim':  'Climatological Prob',
}

# ── CHART 10: Multi-panel error analysis ──────────────────────────────────────
print("\nBuilding Chart 10...")
fig = plt.figure(figsize=(18, 14))
gs  = gridspec.GridSpec(3, 3, figure=fig, hspace=0.45, wspace=0.35)

# ── Panel 1: Outcome breakdown by month ───────────────────────────────────────
ax1 = fig.add_subplot(gs[0, 0])
months_order = [3,4,5,6,7,8,9,10,11,12]
tp_by_month  = s2[s2['outcome']=='TP'].groupby('month').size().reindex(months_order, fill_value=0)
fn_by_month  = s2[s2['outcome']=='FN'].groupby('month').size().reindex(months_order, fill_value=0)
fp_by_month  = s2[s2['outcome']=='FP'].groupby('month').size().reindex(months_order, fill_value=0)

x = np.arange(len(months_order))
ax1.bar(x, tp_by_month, label='TP (hit)',       color='#27AE60', alpha=0.85)
ax1.bar(x, fn_by_month, bottom=tp_by_month,     label='FN (miss)',     color='#E74C3C', alpha=0.85)
ax1.bar(x, fp_by_month, bottom=tp_by_month+fn_by_month, label='FP (false alarm)', color='#F39C12', alpha=0.85)
ax1.set_xticks(x)
ax1.set_xticklabels([MONTH_NAMES[m] for m in months_order], fontsize=8, rotation=45)
ax1.set_title('Slot 2: Outcomes by Month', fontsize=10, fontweight='bold')
ax1.set_ylabel('Count', fontsize=9)
ax1.legend(fontsize=7)
ax1.grid(axis='y', alpha=0.3)
ax1.spines[['top','right']].set_visible(False)

# ── Panel 2: CAPE distribution TP vs FN ───────────────────────────────────────
ax2 = fig.add_subplot(gs[0, 1])
tp_cape = s2[s2['outcome']=='TP']['CAPE'].dropna()
fn_cape = s2[s2['outcome']=='FN']['CAPE'].dropna()
fp_cape = s2[s2['outcome']=='FP']['CAPE'].dropna()

ax2.hist(fn_cape, bins=15, alpha=0.7, color='#E74C3C', label=f'FN (miss) n={len(fn_cape)}', density=True)
ax2.hist(tp_cape, bins=15, alpha=0.7, color='#27AE60', label=f'TP (hit)  n={len(tp_cape)}', density=True)
ax2.axvline(fn_cape.median(), color='#E74C3C', linestyle='--', linewidth=1.5,
            label=f'FN median={fn_cape.median():.0f}')
ax2.axvline(tp_cape.median(), color='#27AE60', linestyle='--', linewidth=1.5,
            label=f'TP median={tp_cape.median():.0f}')
ax2.set_title('CAPE: Hits vs Misses', fontsize=10, fontweight='bold')
ax2.set_xlabel('CAPE (J/kg)', fontsize=9)
ax2.set_ylabel('Density', fontsize=9)
ax2.legend(fontsize=7)
ax2.grid(alpha=0.3)
ax2.spines[['top','right']].set_visible(False)

# ── Panel 3: K-Index distribution TP vs FN ────────────────────────────────────
ax3 = fig.add_subplot(gs[0, 2])
tp_k = s2[s2['outcome']=='TP']['K_INDEX'].dropna()
fn_k = s2[s2['outcome']=='FN']['K_INDEX'].dropna()

ax3.hist(fn_k, bins=15, alpha=0.7, color='#E74C3C', label=f'FN n={len(fn_k)}', density=True)
ax3.hist(tp_k, bins=15, alpha=0.7, color='#27AE60', label=f'TP n={len(tp_k)}', density=True)
ax3.axvline(fn_k.median(), color='#E74C3C', linestyle='--', linewidth=1.5,
            label=f'FN median={fn_k.median():.1f}')
ax3.axvline(tp_k.median(), color='#27AE60', linestyle='--', linewidth=1.5,
            label=f'TP median={tp_k.median():.1f}')
ax3.set_title('K-Index: Hits vs Misses', fontsize=10, fontweight='bold')
ax3.set_xlabel('K-Index', fontsize=9)
ax3.set_ylabel('Density', fontsize=9)
ax3.legend(fontsize=7)
ax3.grid(alpha=0.3)
ax3.spines[['top','right']].set_visible(False)

# ── Panel 4: Feature medians comparison ───────────────────────────────────────
ax4 = fig.add_subplot(gs[1, :2])
feat_cols  = ['CAPE','K_INDEX','TOTALS_TOTALS','ERA5_T2M','MAX','slot_month_clim']
feat_labels = ['CAPE','K-Index','TT','ERA5 T2M','Max Temp','Clim Prob']

tp_medians = [s2[s2['outcome']=='TP'][f].median() for f in feat_cols]
fn_medians = [s2[s2['outcome']=='FN'][f].median() for f in feat_cols]
fp_medians = [s2[s2['outcome']=='FP'][f].median() for f in feat_cols]

# Normalise to % difference from TN baseline
tn_medians = [s2[s2['outcome']=='TN'][f].median() for f in feat_cols]
def pct_diff(vals, base):
    return [(v-b)/abs(b)*100 if b!=0 else 0 for v,b in zip(vals,base)]

x = np.arange(len(feat_cols))
w = 0.25
ax4.bar(x-w, pct_diff(tp_medians, tn_medians), w, label='TP vs TN', color='#27AE60', alpha=0.85)
ax4.bar(x,   pct_diff(fn_medians, tn_medians), w, label='FN vs TN', color='#E74C3C', alpha=0.85)
ax4.bar(x+w, pct_diff(fp_medians, tn_medians), w, label='FP vs TN', color='#F39C12', alpha=0.85)
ax4.set_xticks(x)
ax4.set_xticklabels(feat_labels, fontsize=9)
ax4.axhline(0, color='black', linewidth=0.8)
ax4.set_ylabel('% difference from TN median', fontsize=9)
ax4.set_title('Feature Differences vs True Negatives (Slot 2)', fontsize=10, fontweight='bold')
ax4.legend(fontsize=8)
ax4.grid(axis='y', alpha=0.3)
ax4.spines[['top','right']].set_visible(False)

# ── Panel 5: Probability distribution by outcome ──────────────────────────────
ax5 = fig.add_subplot(gs[1, 2])
for outcome, color, label in [
    ('TP','#27AE60','TP (hit)'),
    ('FN','#E74C3C','FN (miss)'),
    ('FP','#F39C12','FP (false alarm)'),
]:
    data = s2[s2['outcome']==outcome]['prob']
    if len(data) > 0:
        ax5.hist(data, bins=12, alpha=0.7, color=color,
                 label=f'{label} n={len(data)}', density=True)
threshold_s2 = joblib.load(MODELS / "nowcast_slot2_xgb_v2_calibrated.pkl")['threshold']
ax5.axvline(threshold_s2, color='black', linestyle='--', linewidth=1.5,
            label=f'Threshold={threshold_s2}')
ax5.set_title('Predicted Probability by Outcome', fontsize=10, fontweight='bold')
ax5.set_xlabel('Calibrated Probability', fontsize=9)
ax5.set_ylabel('Density', fontsize=9)
ax5.legend(fontsize=7)
ax5.grid(alpha=0.3)
ax5.spines[['top','right']].set_visible(False)

# ── Panel 6: Missed storms summary table ──────────────────────────────────────
ax6 = fig.add_subplot(gs[2, :])
ax6.axis('off')

fn_rows = s2[s2['outcome']=='FN'][
    ['date','month','CAPE','K_INDEX','TOTALS_TOTALS','ERA5_T2M','prob','ts_label']
].copy()
fn_rows = fn_rows.sort_values('prob', ascending=False).head(15)
fn_rows['date'] = fn_rows['date'].dt.strftime('%Y-%m-%d')
fn_rows['month'] = fn_rows['month'].map(MONTH_NAMES)
fn_rows['CAPE']  = fn_rows['CAPE'].round(0).astype(int)
fn_rows['K_INDEX'] = fn_rows['K_INDEX'].round(1)
fn_rows['TOTALS_TOTALS'] = fn_rows['TOTALS_TOTALS'].round(1)
fn_rows['ERA5_T2M'] = fn_rows['ERA5_T2M'].round(1)
fn_rows['prob'] = fn_rows['prob'].round(3)
fn_rows = fn_rows.rename(columns={
    'date':'Date','month':'Month','prob':'Prob (calib)',
    'K_INDEX':'K-Idx','TOTALS_TOTALS':'TT'})

table_data = fn_rows[['Date','Month','CAPE','K-Idx','TT','ERA5_T2M','Prob (calib)']].values
col_labels = ['Date','Month','CAPE','K-Index','Tot-Tot','ERA5 T2M','Prob']

tbl = ax6.table(cellText=table_data, colLabels=col_labels,
                loc='center', cellLoc='center')
tbl.auto_set_font_size(False)
tbl.set_fontsize(8)
tbl.scale(1, 1.4)
for (row, col), cell in tbl.get_celld().items():
    if row == 0:
        cell.set_facecolor('#2C3E50')
        cell.set_text_props(color='white', fontweight='bold')
    elif row % 2 == 0:
        cell.set_facecolor('#F8F9FA')
ax6.set_title('Top 15 Missed Storms (FN) — Slot 2, sorted by predicted probability',
              fontsize=10, fontweight='bold', pad=20)

plt.suptitle('Error Analysis — Slot 2 (1201-1800 IST) | Test Set 2023-2025\n'
             'Calibrated v2 Models', fontsize=13, y=1.01)

path = FIGS / "chart10_error_analysis.png"
fig.savefig(path, dpi=150, bbox_inches='tight')
plt.close()
print(f"Chart 10 saved → {path}")

# ── SAVE ERROR ANALYSIS CSV ───────────────────────────────────────────────────
test.to_csv(RESULTS / "error_analysis_results.csv", index=False)

# ── PRINT KEY FINDINGS ────────────────────────────────────────────────────────
print("\n" + "="*60)
print("KEY FINDINGS — ERROR ANALYSIS")
print("="*60)

print(f"\n--- SLOT 2 MISS ANALYSIS ---")
fn_s2 = s2[s2['outcome']=='FN']
tp_s2 = s2[s2['outcome']=='TP']
fp_s2 = s2[s2['outcome']=='FP']

print(f"Total missed storms (FN): {len(fn_s2)}")
print(f"Total hits (TP):          {len(tp_s2)}")
print(f"Total false alarms (FP):  {len(fp_s2)}")

print(f"\nMissed storm CAPE:    median={fn_s2['CAPE'].median():.0f}  mean={fn_s2['CAPE'].mean():.0f}")
print(f"Hit storm CAPE:       median={tp_s2['CAPE'].median():.0f}  mean={tp_s2['CAPE'].mean():.0f}")
print(f"False alarm CAPE:     median={fp_s2['CAPE'].median():.0f}  mean={fp_s2['CAPE'].mean():.0f}")

print(f"\nMissed storm K-Index: median={fn_s2['K_INDEX'].median():.1f}")
print(f"Hit storm K-Index:    median={tp_s2['K_INDEX'].median():.1f}")

print(f"\nMissed storms by month:")
print(fn_s2['month'].map(MONTH_NAMES).value_counts().to_string())

print(f"\nMissed storm avg probability: {fn_s2['prob'].mean():.3f}")
print(f"  (model gave these a low prob — not near threshold)")

print(f"\nFalse alarm avg probability:  {fp_s2['prob'].mean():.3f}")
print(f"  (model was confident but wrong)")

print(f"\n--- ALL SLOTS SUMMARY ---")
for slot_id in range(4):
    s = test[test['slot']==slot_id]
    c = s['outcome'].value_counts()
    print(f"Slot {slot_id} ({SLOT_NAMES[slot_id]}): "
          f"TP={c.get('TP',0)} FP={c.get('FP',0)} "
          f"FN={c.get('FN',0)} TN={c.get('TN',0)}")

print("\nA8 complete.")
