"""
A4_shap_analysis_v2.py
=======================
SHAP analysis for all 4 per-slot XGBoost v2 models
(retrained with 6-hourly ERA5 features).

Compares feature importance between v1 (daily ERA5) and v2 (6-hourly ERA5).

Output:
  results/shap_figures_v2/shap_bar_slot{0-3}_v2.png
  results/shap_figures_v2/shap_dot_slot{0-3}_v2.png
  results/shap_figures_v2/shap_heatmap_all_slots_v2.png
  results/shap_figures_v2/shap_comparison_v1_v2.png   ← new: v1 vs v2 comparison
  results/shap_per_slot_importance_v2.csv

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

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA    = BASE / "data"    / "bengaluru_6hr_training_dataset_v3.csv"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
FIGS    = BASE / "results" / "shap_figures_v2"
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
print("A4 v2 — SHAP Analysis on 6-Hourly ERA5 Models")
print("=" * 60)

df = pd.read_csv(DATA, parse_dates=['date'])
all_importance    = {}
all_importance_v1 = {}

# Load v1 importance for comparison
v1_csv = RESULTS / "shap_per_slot_importance.csv"
if v1_csv.exists():
    v1_df = pd.read_csv(v1_csv)
    for slot_id in range(4):
        all_importance_v1[slot_id] = (v1_df[v1_df['slot'] == slot_id]
                                      .sort_values('mean_shap', ascending=False)
                                      .reset_index(drop=True))
    print(f"Loaded v1 SHAP importance for comparison")
else:
    print("No v1 SHAP file found — skipping comparison chart")

# ── PER-SLOT SHAP ─────────────────────────────────────────────────────────────
for slot_id in range(4):
    slot_name = SLOT_NAMES[slot_id]
    print(f"\n[Slot {slot_id}] {slot_name}")

    # Load v2 model
    model_path = MODELS / f"nowcast_slot{slot_id}_xgb_v2.pkl"
    if not model_path.exists():
        print(f"  ⚠ Model not found: {model_path}")
        continue

    artifact     = joblib.load(model_path)
    model        = artifact['model']
    feature_cols = artifact['feature_cols']

    # Use test set for SHAP
    slot_test = df[(df['slot'] == slot_id) & (df['year'] >= 2023)]
    X_test    = slot_test[feature_cols].values

    print(f"  Computing SHAP on {len(X_test)} test rows...")
    explainer   = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_test)

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

    # ── Bar chart ─────────────────────────────────────────────────────────────
    top15 = importance_df.head(15)
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.barh(range(15), top15['mean_shap'].values[::-1],
            color=SLOT_COLORS[slot_id], alpha=0.85, edgecolor='white')
    ax.set_yticks(range(15))
    ax.set_yticklabels(top15['feature'].values[::-1], fontsize=10)
    ax.set_xlabel("Mean |SHAP value|", fontsize=11)
    ax.set_title(f"Top 15 Features — Slot {slot_id} ({slot_name})\n[v2 — 6-hourly ERA5]",
                 fontsize=12, pad=10)
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='x', alpha=0.3, linestyle='--')
    plt.tight_layout()
    fig.savefig(FIGS / f"shap_bar_slot{slot_id}_v2.png", dpi=150, bbox_inches='tight')
    plt.close()

    # ── Dot plot ──────────────────────────────────────────────────────────────
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
    ax.set_xlabel("SHAP value", fontsize=11)
    ax.set_title(f"SHAP Summary — Slot {slot_id} ({slot_name})\n"
                 f"[v2 — 6-hourly ERA5] Blue=low value, Red=high",
                 fontsize=11, pad=10)
    ax.spines[['top','right']].set_visible(False)
    plt.tight_layout()
    fig.savefig(FIGS / f"shap_dot_slot{slot_id}_v2.png", dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Charts saved")

# ── CROSS-SLOT HEATMAP ────────────────────────────────────────────────────────
print("\nBuilding cross-slot heatmap...")
top20_each = set()
for imp in all_importance.values():
    top20_each.update(imp.head(20)['feature'].tolist())

heatmap_data = pd.DataFrame(index=sorted(top20_each), columns=range(4))
for slot_id, imp in all_importance.items():
    for _, row in imp.iterrows():
        if row['feature'] in heatmap_data.index:
            heatmap_data.loc[row['feature'], slot_id] = row['mean_shap']
heatmap_data = heatmap_data.fillna(0).astype(float)
heatmap_data['total'] = heatmap_data.sum(axis=1)
heatmap_data = heatmap_data.sort_values('total', ascending=False).drop(columns='total').head(25)

fig, ax = plt.subplots(figsize=(8, 10))
im = ax.imshow(heatmap_data.values, aspect='auto', cmap='YlOrRd')
ax.set_xticks(range(4))
ax.set_xticklabels([f"Slot {i}\n{SLOT_NAMES[i]}" for i in range(4)], fontsize=9)
ax.set_yticks(range(len(heatmap_data)))
ax.set_yticklabels(heatmap_data.index.tolist(), fontsize=9)
ax.set_title("Feature Importance Heatmap — All Slots\n[v2 — 6-hourly ERA5]",
             fontsize=12, pad=10)
plt.colorbar(im, ax=ax, shrink=0.6, label='Mean |SHAP value|')
plt.tight_layout()
fig.savefig(FIGS / "shap_heatmap_all_slots_v2.png", dpi=150, bbox_inches='tight')
plt.close()
print(f"Heatmap saved")

# ── V1 vs V2 COMPARISON CHART ─────────────────────────────────────────────────
if all_importance_v1:
    print("Building v1 vs v2 comparison chart...")
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.flatten()

    for slot_id in range(4):
        ax = axes[slot_id]
        if slot_id not in all_importance or slot_id not in all_importance_v1:
            continue

        top10_v2 = all_importance[slot_id].head(10)['feature'].tolist()
        top10_v1 = all_importance_v1[slot_id].head(10)['feature'].tolist()
        all_feats = list(dict.fromkeys(top10_v2 + top10_v1))[:12]

        v2_vals = []
        v1_vals = []
        for f in all_feats:
            v2_row = all_importance[slot_id][all_importance[slot_id]['feature']==f]
            v1_row = all_importance_v1[slot_id][all_importance_v1[slot_id]['feature']==f]
            v2_vals.append(float(v2_row['mean_shap'].values[0]) if len(v2_row) else 0)
            v1_vals.append(float(v1_row['mean_shap'].values[0]) if len(v1_row) else 0)

        x     = np.arange(len(all_feats))
        width = 0.35
        ax.barh(x - width/2, v1_vals, width, label='v1 Daily ERA5',   color='#4A90D9', alpha=0.8)
        ax.barh(x + width/2, v2_vals, width, label='v2 6-Hourly ERA5', color='#E67E22', alpha=0.8)
        ax.set_yticks(x)
        ax.set_yticklabels(all_feats, fontsize=8)
        ax.set_xlabel("Mean |SHAP|", fontsize=9)
        ax.set_title(f"Slot {slot_id} — {SLOT_NAMES[slot_id]}", fontsize=10)
        ax.legend(fontsize=8)
        ax.spines[['top','right']].set_visible(False)

    plt.suptitle("Feature Importance: v1 (Daily ERA5) vs v2 (6-Hourly ERA5)",
                 fontsize=13, y=1.01)
    plt.tight_layout()
    fig.savefig(FIGS / "shap_comparison_v1_v2.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("Comparison chart saved")

# ── SAVE CSV ──────────────────────────────────────────────────────────────────
combined = pd.concat(all_importance.values(), ignore_index=True)
combined.to_csv(RESULTS / "shap_per_slot_importance_v2.csv", index=False)

# ── KEY FINDINGS ──────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("KEY FINDINGS — TOP 5 FEATURES PER SLOT (v2)")
print("="*60)
for slot_id in range(4):
    if slot_id not in all_importance:
        continue
    print(f"\nSlot {slot_id} ({SLOT_NAMES[slot_id]}):")
    for _, row in all_importance[slot_id].head(5).iterrows():
        # Check if rank changed vs v1
        if slot_id in all_importance_v1:
            v1_rank = all_importance_v1[slot_id][
                all_importance_v1[slot_id]['feature']==row['feature']]['rank']
            rank_str = f"(was #{int(v1_rank.values[0])} in v1)" if len(v1_rank) else "(new)"
        else:
            rank_str = ""
        print(f"  {int(row['rank'])}. {row['feature']:<30} {row['mean_shap']:.4f}  {rank_str}")

print(f"\nAll figures saved to: {FIGS}")
print("A4 v2 complete.")
