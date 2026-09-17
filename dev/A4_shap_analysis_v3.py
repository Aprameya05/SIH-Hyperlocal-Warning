"""
A4_shap_analysis_v3.py
=======================
SHAP analysis for all 4 per-slot XGBoost v3 models
(with derived atmospheric features from A12).

Key question: Did cape_x_kindex and li_x_totals enter the top 5?

Output:
  results/shap_figures_v3/shap_bar_slot{0-3}_v3.png
  results/shap_figures_v3/shap_dot_slot{0-3}_v3.png
  results/shap_figures_v3/shap_heatmap_all_slots_v3.png
  results/shap_per_slot_importance_v3.csv

Author: Aprameya, CSIR Thunderstorm Project
"""

import pandas as pd
import numpy as np
import joblib
import shap
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import warnings
from pathlib import Path

warnings.filterwarnings('ignore')

BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA    = BASE / "data"    / "bengaluru_6hr_training_dataset_v3.csv"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
FIGS    = BASE / "results" / "shap_figures_v3"
FIGS.mkdir(parents=True, exist_ok=True)

SLOT_NAMES  = {0:"0001-0600 IST",1:"0601-1200 IST",2:"1201-1800 IST",3:"1801-2400 IST"}
SLOT_COLORS = {0:"#4A90D9",1:"#27AE60",2:"#E67E22",3:"#8E44AD"}

print("=" * 60)
print("A4 v3 — SHAP Analysis on v3 Models (Derived Features)")
print("=" * 60)

df = pd.read_csv(DATA, parse_dates=['date'])
all_importance = {}

for slot_id in range(4):
    slot_name  = SLOT_NAMES[slot_id]
    print(f"\n[Slot {slot_id}] {slot_name}")

    model_path = MODELS / f"nowcast_slot{slot_id}_xgb_v3.pkl"
    if not model_path.exists():
        print(f"  ⚠ Not found: {model_path}")
        continue

    artifact     = joblib.load(model_path)
    model        = artifact['model']
    feature_cols = artifact['feature_cols']

    slot_test = df[(df['slot']==slot_id) & (df['year']>=2023)]
    X_test    = slot_test[feature_cols].values

    print(f"  Computing SHAP on {len(X_test)} test rows...")
    explainer   = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_test)

    mean_shap = np.abs(shap_values).mean(axis=0)
    imp = pd.DataFrame({
        'feature':    feature_cols,
        'mean_shap':  mean_shap,
        'slot':       slot_id,
        'slot_label': slot_name,
    }).sort_values('mean_shap', ascending=False).reset_index(drop=True)
    imp['rank'] = imp.index + 1
    all_importance[slot_id] = imp

    print(f"  Top 5:")
    for _, row in imp.head(5).iterrows():
        new_flag = " ← NEW" if row['feature'] in [
            'cape_x_kindex','li_x_totals','q_gradient_500_850',
            'thetae_850','wind_shear_500_850','wind_shear_700_850',
            'moisture_flux_850','moisture_flux_700','mid_level_drying',
            'thickness_500_850'] else ""
        print(f"    {int(row['rank']):>2}. {row['feature']:<30} "
              f"{row['mean_shap']:.4f}{new_flag}")

    # Bar chart
    top15 = imp.head(15)
    fig, ax = plt.subplots(figsize=(9, 6))
    bar_colors = ['#E74C3C' if f in ['cape_x_kindex','li_x_totals',
                  'q_gradient_500_850','thetae_850','wind_shear_700_850']
                  else SLOT_COLORS[slot_id]
                  for f in top15['feature'].values[::-1]]
    ax.barh(range(15), top15['mean_shap'].values[::-1],
            color=bar_colors, alpha=0.85, edgecolor='white')
    ax.set_yticks(range(15))
    ax.set_yticklabels(top15['feature'].values[::-1], fontsize=9)
    ax.set_xlabel("Mean |SHAP value|", fontsize=11)
    ax.set_title(f"Top 15 Features — Slot {slot_id} ({slot_name})\n"
                 f"[v3 — derived features in red]", fontsize=11, pad=10)
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='x', alpha=0.3, linestyle='--')
    plt.tight_layout()
    fig.savefig(FIGS / f"shap_bar_slot{slot_id}_v3.png", dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Chart saved")

# Cross-slot heatmap
print("\nBuilding heatmap...")
top20_each = set()
for imp in all_importance.values():
    top20_each.update(imp.head(20)['feature'].tolist())

heatmap_data = pd.DataFrame(index=sorted(top20_each), columns=range(4))
for sid, imp in all_importance.items():
    for _, row in imp.iterrows():
        if row['feature'] in heatmap_data.index:
            heatmap_data.loc[row['feature'], sid] = row['mean_shap']
heatmap_data = heatmap_data.fillna(0).astype(float)
heatmap_data['total'] = heatmap_data.sum(axis=1)
heatmap_data = heatmap_data.sort_values('total', ascending=False).drop(
    columns='total').head(25)

fig, ax = plt.subplots(figsize=(8, 10))
im = ax.imshow(heatmap_data.values, aspect='auto', cmap='YlOrRd')
ax.set_xticks(range(4))
ax.set_xticklabels([f"Slot {i}\n{SLOT_NAMES[i]}" for i in range(4)], fontsize=9)
ax.set_yticks(range(len(heatmap_data)))
ax.set_yticklabels(heatmap_data.index.tolist(), fontsize=9)
ax.set_title("Feature Importance Heatmap\n[v3 — derived features]", fontsize=12)
plt.colorbar(im, ax=ax, shrink=0.6, label='Mean |SHAP value|')
plt.tight_layout()
fig.savefig(FIGS / "shap_heatmap_all_slots_v3.png", dpi=150, bbox_inches='tight')
plt.close()
print("Heatmap saved")

combined = pd.concat(all_importance.values(), ignore_index=True)
combined.to_csv(RESULTS / "shap_per_slot_importance_v3.csv", index=False)

print("\n" + "="*60)
print("KEY FINDINGS — TOP 5 FEATURES PER SLOT (v3)")
print("="*60)
NEW_FEATS = ['cape_x_kindex','li_x_totals','q_gradient_500_850',
             'thetae_850','wind_shear_500_850','wind_shear_700_850',
             'moisture_flux_850','moisture_flux_700','mid_level_drying','thickness_500_850']
for slot_id in range(4):
    if slot_id not in all_importance:
        continue
    print(f"\nSlot {slot_id} ({SLOT_NAMES[slot_id]}):")
    for _, row in all_importance[slot_id].head(5).iterrows():
        flag = " ← NEW FEATURE" if row['feature'] in NEW_FEATS else ""
        print(f"  {int(row['rank'])}. {row['feature']:<30} {row['mean_shap']:.4f}{flag}")

print(f"\nAll figures saved to: {FIGS}")
print("A4 v3 complete.")
