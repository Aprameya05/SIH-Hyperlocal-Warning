"""
A4_shap_analysis.py
===================
SHAP analysis for all 4 per-slot XGBoost models.

Produces per slot:
  - Top 15 features by mean |SHAP| (bar chart)
  - SHAP summary dot plot
  - CSV of feature importances

Produces combined:
  - Heatmap comparing feature importance across all 4 slots
  - results/shap_per_slot_importance.csv

Author: Aprameya, CSIR Thunderstorm Project
"""

import pandas as pd
import numpy as np
import joblib
import shap
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import warnings
from pathlib import Path

warnings.filterwarnings('ignore')

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA    = BASE / "data"    / "bengaluru_6hr_training_dataset.csv"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
FIGS    = BASE / "results" / "shap_figures"
FIGS.mkdir(parents=True, exist_ok=True)

SLOT_NAMES = {
    0: "0001-0600 IST",
    1: "0601-1200 IST",
    2: "1201-1800 IST",
    3: "1801-2400 IST",
}

SLOT_COLORS = {0: "#4A90D9", 1: "#27AE60", 2: "#E67E22", 3: "#8E44AD"}

# ── LOAD DATA ─────────────────────────────────────────────────────────────────
print("=" * 60)
print("A4 — SHAP Feature Importance Analysis")
print("=" * 60)

df = pd.read_csv(DATA, parse_dates=['date'])
all_importance = {}

# ── PER-SLOT SHAP ─────────────────────────────────────────────────────────────
for slot_id in range(4):
    slot_name = SLOT_NAMES[slot_id]
    print(f"\n[Slot {slot_id}] {slot_name}")

    # Load model
    artifact     = joblib.load(MODELS / f"nowcast_slot{slot_id}_xgb.pkl")
    model        = artifact['model']
    feature_cols = artifact['feature_cols']

    # Use test set for SHAP (2023-2025) — more honest than training set
    slot_test = df[(df['slot'] == slot_id) & (df['year'] >= 2023)]
    X_test    = slot_test[feature_cols].values

    print(f"  Computing SHAP values on {len(X_test)} test rows...")
    explainer   = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_test)

    # Mean absolute SHAP per feature
    mean_shap = np.abs(shap_values).mean(axis=0)
    importance_df = pd.DataFrame({
        'feature':    feature_cols,
        'mean_shap':  mean_shap,
        'slot':       slot_id,
        'slot_label': slot_name,
    }).sort_values('mean_shap', ascending=False).reset_index(drop=True)
    importance_df['rank'] = importance_df.index + 1

    all_importance[slot_id] = importance_df
    print(f"  Top 5 features:")
    for _, row in importance_df.head(5).iterrows():
        print(f"    {int(row['rank']):>2}. {row['feature']:<30} {row['mean_shap']:.4f}")

    # ── PLOT 1: Bar chart — top 15 features ───────────────────────────────────
    top15 = importance_df.head(15)
    fig, ax = plt.subplots(figsize=(9, 6))
    bars = ax.barh(range(15), top15['mean_shap'].values[::-1],
                   color=SLOT_COLORS[slot_id], alpha=0.85, edgecolor='white')
    ax.set_yticks(range(15))
    ax.set_yticklabels(top15['feature'].values[::-1], fontsize=10)
    ax.set_xlabel("Mean |SHAP value|", fontsize=11)
    ax.set_title(f"Top 15 Features — Slot {slot_id} ({slot_name})", fontsize=13, pad=12)
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='x', alpha=0.3, linestyle='--')
    plt.tight_layout()
    path = FIGS / f"shap_bar_slot{slot_id}.png"
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved bar chart → {path}")

    # ── PLOT 2: SHAP dot summary plot ─────────────────────────────────────────
    top10_features = importance_df.head(10)['feature'].tolist()
    top10_idx      = [feature_cols.index(f) for f in top10_features]
    shap_top10     = shap_values[:, top10_idx]
    X_top10        = X_test[:, top10_idx]

    fig, ax = plt.subplots(figsize=(9, 6))
    for i, feat in enumerate(reversed(top10_features)):
        feat_idx    = top10_features.index(feat)
        sv          = shap_top10[:, feat_idx]
        fv          = X_top10[:, feat_idx]
        fv_norm     = (fv - fv.min()) / (fv.max() - fv.min() + 1e-9)
        colors_feat = plt.cm.coolwarm(fv_norm)
        ax.scatter(sv, np.full_like(sv, i) + np.random.uniform(-0.15, 0.15, len(sv)),
                   c=colors_feat, alpha=0.5, s=12, linewidths=0)

    ax.set_yticks(range(10))
    ax.set_yticklabels(list(reversed(top10_features)), fontsize=10)
    ax.axvline(0, color='black', linewidth=0.8, linestyle='--', alpha=0.5)
    ax.set_xlabel("SHAP value (impact on model output)", fontsize=11)
    ax.set_title(f"SHAP Summary — Slot {slot_id} ({slot_name})\n"
                 f"Color: blue=low feature value, red=high", fontsize=12, pad=10)
    ax.spines[['top','right']].set_visible(False)
    plt.tight_layout()
    path = FIGS / f"shap_dot_slot{slot_id}.png"
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved dot plot   → {path}")

# ── COMBINED HEATMAP — feature importance across all slots ────────────────────
print("\nBuilding cross-slot importance heatmap...")

# Get union of top 20 features across all slots
top20_each = set()
for sid, imp in all_importance.items():
    top20_each.update(imp.head(20)['feature'].tolist())

# Build matrix: rows=features, cols=slots
heatmap_data = pd.DataFrame(index=sorted(top20_each), columns=range(4))
for slot_id, imp in all_importance.items():
    for _, row in imp.iterrows():
        if row['feature'] in heatmap_data.index:
            heatmap_data.loc[row['feature'], slot_id] = row['mean_shap']
heatmap_data = heatmap_data.fillna(0).astype(float)

# Sort by total importance across slots
heatmap_data['total'] = heatmap_data.sum(axis=1)
heatmap_data = heatmap_data.sort_values('total', ascending=False).drop(columns='total')
heatmap_data = heatmap_data.head(25)  # top 25 features

fig, ax = plt.subplots(figsize=(8, 10))
im = ax.imshow(heatmap_data.values, aspect='auto', cmap='YlOrRd')

ax.set_xticks(range(4))
ax.set_xticklabels([f"Slot {i}\n{SLOT_NAMES[i]}" for i in range(4)], fontsize=9)
ax.set_yticks(range(len(heatmap_data)))
ax.set_yticklabels(heatmap_data.index.tolist(), fontsize=9)
ax.set_title("Feature Importance Heatmap — All Slots\n(Mean |SHAP|)",
             fontsize=13, pad=12)
plt.colorbar(im, ax=ax, shrink=0.6, label='Mean |SHAP value|')
plt.tight_layout()
path = FIGS / "shap_heatmap_all_slots.png"
fig.savefig(path, dpi=150, bbox_inches='tight')
plt.close()
print(f"Saved heatmap → {path}")

# ── SAVE COMBINED CSV ─────────────────────────────────────────────────────────
combined = pd.concat(all_importance.values(), ignore_index=True)
combined.to_csv(RESULTS / "shap_per_slot_importance.csv", index=False)
print(f"Saved CSV → {RESULTS / 'shap_per_slot_importance.csv'}")

# ── PRINT KEY FINDINGS ────────────────────────────────────────────────────────
print("\n" + "="*60)
print("KEY FINDINGS — TOP 5 FEATURES PER SLOT")
print("="*60)
for slot_id in range(4):
    print(f"\nSlot {slot_id} ({SLOT_NAMES[slot_id]}):")
    top5 = all_importance[slot_id].head(5)
    for _, row in top5.iterrows():
        print(f"  {int(row['rank'])}. {row['feature']:<30} {row['mean_shap']:.4f}")

print("\nA4 complete.")
print(f"All figures saved to: {FIGS}")
