"""
A7_rolling_verification.py
===========================
Year-by-year WMO verification of calibrated slot models.

Evaluates model performance on each year from 2022-2025 separately,
showing how skill evolves over time and identifying which years/slots
are harder to predict.

Uses CALIBRATED models (v2_calibrated) for operationally meaningful
probability scores.

Output:
  results/rolling_verification_results.csv
  results/shap_figures_v2/chart8_rolling_verification.png
  results/shap_figures_v2/chart9_slot2_yearly_deep_dive.png

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
from sklearn.metrics import roc_auc_score
from sklearn.isotonic import IsotonicRegression

warnings.filterwarnings('ignore')

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA    = BASE / "data"    / "bengaluru_6hr_training_dataset_v2.csv"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
FIGS    = BASE / "results" / "shap_figures_v2"
FIGS.mkdir(parents=True, exist_ok=True)

SLOT_NAMES  = {0:"0001-0600", 1:"0601-1200", 2:"1201-1800", 3:"1801-2400"}
SLOT_COLORS = {0:"#4A90D9", 1:"#27AE60", 2:"#E67E22", 3:"#8E44AD"}
EVAL_YEARS  = [2023, 2024, 2025]

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
    hss_den = (tp+fn)*(fn+tn) + (tp+fp)*(fp+tn)
    hss  = hss_num/hss_den if hss_den>0      else 0
    auroc = roc_auc_score(y_true, y_prob) if y_true.sum()>1 else float('nan')
    from sklearn.metrics import brier_score_loss
    brier = brier_score_loss(y_true, y_prob) if y_true.sum()>0 else float('nan')
    return dict(TP=tp, FP=fp, FN=fn, TN=tn,
                POD=round(pod,3), FAR=round(far,3),
                CSI=round(csi,3), HSS=round(hss,3),
                AUROC=round(auroc,3), Brier=round(brier,4))

def apply_calibrator(artifact, raw_prob):
    calibrator  = artifact.get('calibrator')
    calib_method = artifact.get('calib_method', 'none')
    if calibrator is None:
        return raw_prob
    if calib_method == 'sigmoid':
        return calibrator.predict_proba(raw_prob.reshape(-1,1))[:,1]
    else:  # isotonic
        return calibrator.predict(raw_prob)

# ── LOAD ──────────────────────────────────────────────────────────────────────
print("=" * 60)
print("A7 — Year-by-Year Rolling Verification")
print("=" * 60)

df = pd.read_csv(DATA, parse_dates=['date'])

print("\nLoading calibrated models...")
artifacts = {}
for slot_id in range(4):
    path = MODELS / f"nowcast_slot{slot_id}_xgb_v2_calibrated.pkl"
    if not path.exists():
        # Fall back to uncalibrated
        path = MODELS / f"nowcast_slot{slot_id}_xgb_v2.pkl"
        print(f"  Slot {slot_id}: calibrated not found, using v2")
    artifacts[slot_id] = joblib.load(path)
    print(f"  Slot {slot_id} loaded")

# ── YEAR BY YEAR EVALUATION ────────────────────────────────────────────────────
print("\nRunning year-by-year evaluation...")
all_rows = []

for year in EVAL_YEARS:
    year_df = df[df['year'] == year]
    print(f"\n  {year}: {len(year_df)} rows")

    for slot_id in range(4):
        slot_df = year_df[year_df['slot'] == slot_id]
        if len(slot_df) == 0:
            continue

        artifact     = artifacts[slot_id]
        model        = artifact['model']
        feature_cols = artifact['feature_cols']
        threshold    = artifact['threshold']

        X = slot_df[feature_cols].values
        y = slot_df['ts_label'].values

        if y.sum() == 0:
            print(f"    Slot {slot_id}: 0 positives — skipping")
            continue

        raw_prob   = model.predict_proba(X)[:,1]
        calib_prob = apply_calibrator(artifact, raw_prob)

        metrics = compute_metrics(y, calib_prob, threshold)
        metrics.update({
            'year':      year,
            'slot':      slot_id,
            'slot_name': SLOT_NAMES[slot_id],
            'n_total':   len(y),
            'n_pos':     int(y.sum()),
            'pos_rate':  round(y.mean()*100, 1),
        })
        all_rows.append(metrics)
        print(f"    Slot {slot_id} ({SLOT_NAMES[slot_id]}): "
              f"n_pos={int(y.sum())} POD={metrics['POD']} "
              f"FAR={metrics['FAR']} HSS={metrics['HSS']} "
              f"AUROC={metrics['AUROC']}")

results = pd.DataFrame(all_rows)
results.to_csv(RESULTS / "rolling_verification_results.csv", index=False)
print(f"\nSaved → {RESULTS / 'rolling_verification_results.csv'}")

# ── CHART 8: 4-panel metrics by year per slot ─────────────────────────────────
print("\nBuilding Chart 8 — Rolling verification overview...")
metrics_to_plot = ['AUROC','POD','FAR','HSS']
fig, axes = plt.subplots(2, 2, figsize=(14, 10))
axes = axes.flatten()

for idx, metric in enumerate(metrics_to_plot):
    ax = axes[idx]
    for slot_id in range(4):
        slot_data = results[results['slot']==slot_id].sort_values('year')
        if len(slot_data) == 0:
            continue
        ax.plot(slot_data['year'], slot_data[metric],
                marker='o', linewidth=2.5, markersize=9,
                color=SLOT_COLORS[slot_id],
                label=f"Slot {slot_id} ({SLOT_NAMES[slot_id]})")
        # Add value labels
        for _, row in slot_data.iterrows():
            if not np.isnan(row[metric]):
                ax.annotate(f"{row[metric]:.2f}",
                           (row['year'], row[metric]),
                           textcoords="offset points",
                           xytext=(0, 8), ha='center', fontsize=7,
                           color=SLOT_COLORS[slot_id])

    ax.set_title(metric, fontsize=13, fontweight='bold')
    ax.set_xlabel('Year', fontsize=10)
    ax.set_ylabel(metric, fontsize=10)
    ax.set_xticks(EVAL_YEARS)
    ax.legend(fontsize=8, loc='best')
    ax.grid(alpha=0.3)
    ax.spines[['top','right']].set_visible(False)
    if metric == 'HSS':
        ax.axhline(0, color='black', linewidth=0.8, linestyle='--', alpha=0.5)
    if metric in ['FAR','POD','AUROC']:
        ax.set_ylim(0, 1.05)

plt.suptitle('Year-by-Year Model Verification (2022–2025)\n'
             'Calibrated Slot Models v2 — 6-Hourly ERA5',
             fontsize=13, y=1.01)
plt.tight_layout()
path = FIGS / "chart8_rolling_verification.png"
fig.savefig(path, dpi=150, bbox_inches='tight')
plt.close()
print(f"Chart 8 saved → {path}")

# ── CHART 9: Slot 2 deep dive ─────────────────────────────────────────────────
print("Building Chart 9 — Slot 2 deep dive...")
slot2 = results[results['slot']==2].sort_values('year')

if len(slot2) > 0:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Left: grouped bars
    x     = np.arange(len(slot2))
    width = 0.18
    metrics_bar = ['POD','FAR','CSI','HSS']
    bar_colors  = ['#27AE60','#E74C3C','#3498DB','#9B59B6']

    for i, (m, c) in enumerate(zip(metrics_bar, bar_colors)):
        bars = ax1.bar(x + (i-1.5)*width, slot2[m], width,
                       label=m, color=c, alpha=0.85)
        ax1.bar_label(bars, fmt='%.2f', fontsize=7, padding=2)

    ax1.set_xticks(x)
    ax1.set_xticklabels([f"{yr}\n(n={int(r['n_pos'])})"
                         for yr, (_, r) in zip(slot2['year'], slot2.iterrows())],
                        fontsize=10)
    ax1.set_xlabel('Year (n = positive windows)', fontsize=10)
    ax1.set_ylabel('Score', fontsize=10)
    ax1.set_title('Slot 2 (1201-1800 IST) — Verification Metrics by Year\n'
                  '[Operational Window — Calibrated]', fontsize=11)
    ax1.legend(fontsize=9)
    ax1.axhline(0, color='black', linewidth=0.8)
    ax1.grid(axis='y', alpha=0.3)
    ax1.spines[['top','right']].set_visible(False)

    # Right: AUROC and Brier trend
    ax2_twin = ax2.twinx()
    ax2.plot(slot2['year'], slot2['AUROC'], 'o-',
             color='#2C3E50', linewidth=2.5, markersize=9, label='AUROC')
    ax2_twin.plot(slot2['year'], slot2['Brier'], 's--',
                  color='#E67E22', linewidth=2, markersize=8, label='Brier')
    ax2.set_xlabel('Year', fontsize=10)
    ax2.set_ylabel('AUROC', fontsize=10, color='#2C3E50')
    ax2_twin.set_ylabel('Brier Score (lower=better)', fontsize=10, color='#E67E22')
    ax2.set_title('Slot 2 — AUROC and Brier Score Trend', fontsize=11)
    ax2.set_xticks(EVAL_YEARS)
    ax2.grid(alpha=0.3)
    ax2.spines[['top','right']].set_visible(False)

    lines1, labels1 = ax2.get_legend_handles_labels()
    lines2, labels2 = ax2_twin.get_legend_handles_labels()
    ax2.legend(lines1+lines2, labels1+labels2, fontsize=9, loc='best')

    plt.suptitle('Slot 2 Deep Dive — Year-by-Year Performance',
                 fontsize=12, y=1.01)
    plt.tight_layout()
    path = FIGS / "chart9_slot2_yearly_deep_dive.png"
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Chart 9 saved → {path}")

# ── PRINT FULL TABLE ──────────────────────────────────────────────────────────
print("\n" + "="*60)
print("ROLLING VERIFICATION SUMMARY TABLE")
print("="*60)
print(f"\n{'Year':<6} {'Slot':<6} {'Window':<14} {'n_pos':<7} "
      f"{'POD':<7} {'FAR':<7} {'CSI':<7} {'HSS':<7} {'AUROC':<8} {'Brier'}")
print("-"*80)
for _, r in results.sort_values(['slot','year']).iterrows():
    auroc_str = f"{r['AUROC']:.3f}" if not np.isnan(r['AUROC']) else "  N/A"
    print(f"  {int(r['year']):<5} {int(r['slot']):<6} {r['slot_name']:<14} "
          f"{int(r['n_pos']):<7} {r['POD']:<7} {r['FAR']:<7} "
          f"{r['CSI']:<7} {r['HSS']:<7} {auroc_str:<8} {r['Brier']}")

print("\nA7 complete.")
