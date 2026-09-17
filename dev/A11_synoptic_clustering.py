"""
A11_synoptic_clustering.py
==========================
Synoptic weather regime clustering for Bengaluru Airport (Station 43295).

Groups all 3819 days into distinct atmospheric regimes using KMeans
on ERA5 features. Then evaluates model skill per regime.

Key question: Does the XGBoost model perform equally well across all
weather regimes, or does it excel in some and fail in others?

This analysis:
  1. Finds optimal number of clusters (elbow method)
  2. Assigns each day to a synoptic regime
  3. Characterises each regime meteorologically
  4. Evaluates model skill (AUROC, HSS, POD, FAR) per regime
  5. Identifies which regime has most missed storms

Output:
  data/synoptic_clusters.csv
  results/synoptic_cluster_profiles.csv
  results/shap_figures_v2/chart13_synoptic_clustering.png

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
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import roc_auc_score, silhouette_score

warnings.filterwarnings('ignore')
np.random.seed(42)

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA    = BASE / "data"    / "bengaluru_6hr_training_dataset_v2.csv"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
FIGS    = BASE / "results" / "shap_figures_v2"

CLUSTER_FEATURES = [
    'ERA5_T2M','ERA5_D2M','ERA5_U10','ERA5_V10',
    'ERA5_t_850hPa','ERA5_t_500hPa','ERA5_q_850hPa',
    'ERA5_u_700hPa','ERA5_CAPE','MONTH_sin','MONTH_cos',
]

N_CLUSTERS = 5  # meteorologically interpretable for Bengaluru

# ── METRICS ───────────────────────────────────────────────────────────────────
def compute_metrics(y_true, y_prob, threshold):
    if y_true.sum() == 0:
        return dict(POD=0,FAR=0,CSI=0,HSS=0,AUROC=float('nan'),
                    TP=0,FP=0,FN=0,TN=int((~y_true.astype(bool)).sum()))
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
                POD=round(pod,3),FAR=round(far,3),
                CSI=round(csi,3),HSS=round(hss,3),
                AUROC=round(auroc,3))

# ── LOAD ──────────────────────────────────────────────────────────────────────
print("=" * 60)
print("A11 — Synoptic Weather Regime Clustering")
print("=" * 60)

df = pd.read_csv(DATA, parse_dates=['date'])
slot2 = df[df['slot']==2].copy().sort_values('date').reset_index(drop=True)
daily_label = df.groupby('date')['ts_label'].max().rename('daily_label')
slot2 = slot2.merge(daily_label.reset_index(), on='date', how='left')

print(f"\nDataset: {len(slot2)} days | {slot2['daily_label'].sum()} TS days")

# ── STEP 1: CLUSTERING ────────────────────────────────────────────────────────
print("\n[1/5] Running KMeans clustering...")

X_cluster = slot2[CLUSTER_FEATURES].fillna(0).values
scaler_cl  = StandardScaler()
X_scaled   = scaler_cl.fit_transform(X_cluster)

# Elbow method to validate N_CLUSTERS
inertias    = []
sil_scores  = []
k_range     = range(2, 9)
for k in k_range:
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = km.fit_predict(X_scaled)
    inertias.append(km.inertia_)
    sil_scores.append(silhouette_score(X_scaled, labels))
    print(f"  k={k}: inertia={km.inertia_:.0f}, silhouette={sil_scores[-1]:.3f}")

# Final clustering with N_CLUSTERS
print(f"\nUsing k={N_CLUSTERS}...")
kmeans = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init=20)
slot2['cluster'] = kmeans.fit_predict(X_scaled)
print(f"Silhouette score: {silhouette_score(X_scaled, slot2['cluster']):.3f}")

# ── STEP 2: CHARACTERISE CLUSTERS ────────────────────────────────────────────
print("\n[2/5] Characterising clusters...")

# PCA for 2D visualisation
pca = PCA(n_components=2, random_state=42)
X_pca = pca.fit_transform(X_scaled)
slot2['pca1'] = X_pca[:,0]
slot2['pca2'] = X_pca[:,1]

cluster_profiles = []
REGIME_NAMES = {}  # will be auto-named based on characteristics

for c in range(N_CLUSTERS):
    mask   = slot2['cluster'] == c
    subset = slot2[mask]
    n_days = len(subset)
    n_ts   = subset['daily_label'].sum()
    ts_rate = n_ts / n_days * 100

    # Key characteristics
    cape_med  = subset['ERA5_CAPE'].median()
    t2m_med   = subset['ERA5_T2M'].median() - 273.15
    d2m_med   = subset['ERA5_D2M'].median() - 273.15
    q850_med  = subset['ERA5_q_850hPa'].median() * 1000
    month_med = subset['month'].median()

    # Auto-name based on characteristics
    if ts_rate > 20:
        name = f"High-TS Regime"
    elif cape_med > 500:
        name = f"Unstable Dry"
    elif q850_med > 12:
        name = f"Moist Monsoon"
    elif t2m_med > 27:
        name = f"Hot Pre-Monsoon"
    else:
        name = f"Cool Dry Winter"

    REGIME_NAMES[c] = f"R{c+1}: {name}"

    profile = {
        'cluster': c,
        'regime_name': REGIME_NAMES[c],
        'n_days': n_days,
        'n_ts_days': int(n_ts),
        'ts_rate_pct': round(ts_rate, 1),
        'CAPE_median': round(cape_med, 0),
        'T2M_C_median': round(t2m_med, 1),
        'D2M_C_median': round(d2m_med, 1),
        'q850_gkg_median': round(q850_med, 2),
        'peak_month': round(month_med, 1),
    }
    cluster_profiles.append(profile)
    print(f"\n  Cluster {c} — {REGIME_NAMES[c]}")
    print(f"    Days: {n_days} | TS days: {int(n_ts)} ({ts_rate:.1f}%)")
    print(f"    CAPE: {cape_med:.0f} J/kg | T2M: {t2m_med:.1f}°C | "
          f"q850: {q850_med:.2f} g/kg | Peak month: {month_med:.0f}")

profiles_df = pd.DataFrame(cluster_profiles)

# ── STEP 3: MODEL SKILL PER CLUSTER ──────────────────────────────────────────
print("\n[3/5] Evaluating model skill per cluster...")

# Load calibrated slot 2 model
artifact     = joblib.load(MODELS / "nowcast_slot2_xgb_v2_calibrated.pkl")
model        = artifact['model']
feature_cols = artifact['feature_cols']
threshold    = artifact['threshold']

def apply_calibrator(artifact, raw_prob):
    cal = artifact.get('calibrator')
    if cal is None: return raw_prob
    if artifact.get('calib_method') == 'sigmoid':
        return cal.predict_proba(raw_prob.reshape(-1,1))[:,1]
    return cal.predict(raw_prob)

# Get predictions for entire slot2 dataset
X_all    = slot2[feature_cols].values
raw_prob = model.predict_proba(X_all)[:,1]
cal_prob = apply_calibrator(artifact, raw_prob)
slot2['pred_prob'] = cal_prob

skill_rows = []
for c in range(N_CLUSTERS):
    mask   = slot2['cluster'] == c
    subset = slot2[mask]
    y_true = subset['daily_label'].values
    y_prob = subset['pred_prob'].values

    metrics = compute_metrics(y_true, y_prob, threshold)
    metrics.update({
        'cluster':      c,
        'regime_name':  REGIME_NAMES[c],
        'n_days':       len(subset),
        'n_ts':         int(y_true.sum()),
        'ts_rate':      round(y_true.mean()*100, 1),
    })
    skill_rows.append(metrics)
    print(f"  {REGIME_NAMES[c]}: n={len(subset)} TS={int(y_true.sum())} "
          f"AUROC={metrics['AUROC']} POD={metrics['POD']} "
          f"FAR={metrics['FAR']} HSS={metrics['HSS']}")

skill_df = pd.DataFrame(skill_rows)

# ── STEP 4: BUILD CHARTS ──────────────────────────────────────────────────────
print("\n[4/5] Building charts...")

colors = ['#E74C3C','#E67E22','#27AE60','#3498DB','#9B59B6']
fig = plt.figure(figsize=(20, 14))
gs  = gridspec.GridSpec(2, 3, figure=fig, hspace=0.4, wspace=0.35)

# ── Panel 1: Elbow curve ──────────────────────────────────────────────────────
ax1 = fig.add_subplot(gs[0, 0])
ax1_twin = ax1.twinx()
ax1.plot(list(k_range), inertias, 'o-', color='#2C3E50', linewidth=2)
ax1_twin.plot(list(k_range), sil_scores, 's--', color='#E74C3C', linewidth=2)
ax1.axvline(N_CLUSTERS, color='#27AE60', linestyle='--', linewidth=1.5,
            label=f'Chosen k={N_CLUSTERS}')
ax1.set_xlabel('Number of Clusters (k)', fontsize=10)
ax1.set_ylabel('Inertia', fontsize=10, color='#2C3E50')
ax1_twin.set_ylabel('Silhouette Score', fontsize=10, color='#E74C3C')
ax1.set_title('Elbow Method — Optimal k', fontsize=11, fontweight='bold')
ax1.legend(fontsize=9)
ax1.grid(alpha=0.3)
ax1.spines[['top']].set_visible(False)

# ── Panel 2: PCA scatter ──────────────────────────────────────────────────────
ax2 = fig.add_subplot(gs[0, 1])
for c in range(N_CLUSTERS):
    mask = slot2['cluster'] == c
    ax2.scatter(slot2.loc[mask,'pca1'], slot2.loc[mask,'pca2'],
                c=colors[c], alpha=0.4, s=8, label=REGIME_NAMES[c])
ax2.set_xlabel(f'PC1 ({pca.explained_variance_ratio_[0]*100:.1f}%)', fontsize=10)
ax2.set_ylabel(f'PC2 ({pca.explained_variance_ratio_[1]*100:.1f}%)', fontsize=10)
ax2.set_title('Synoptic Regimes — PCA Space', fontsize=11, fontweight='bold')
ax2.legend(fontsize=7, markerscale=2)
ax2.grid(alpha=0.3)
ax2.spines[['top','right']].set_visible(False)

# ── Panel 3: TS rate per cluster ──────────────────────────────────────────────
ax3 = fig.add_subplot(gs[0, 2])
sorted_profiles = profiles_df.sort_values('ts_rate_pct', ascending=True)
bars = ax3.barh(range(N_CLUSTERS), sorted_profiles['ts_rate_pct'],
                color=[colors[c] for c in sorted_profiles['cluster']], alpha=0.85)
ax3.set_yticks(range(N_CLUSTERS))
ax3.set_yticklabels([f"{r}\n(n={n})" for r,n in
                     zip(sorted_profiles['regime_name'],
                         sorted_profiles['n_days'])], fontsize=8)
ax3.set_xlabel('Thunderstorm Frequency (%)', fontsize=10)
ax3.set_title('TS Rate per Weather Regime', fontsize=11, fontweight='bold')
ax3.bar_label(bars, fmt='%.1f%%', fontsize=8, padding=3)
ax3.grid(axis='x', alpha=0.3)
ax3.spines[['top','right']].set_visible(False)

# ── Panel 4: Model skill per cluster ─────────────────────────────────────────
ax4 = fig.add_subplot(gs[1, :2])
x      = np.arange(N_CLUSTERS)
w      = 0.2
sorted_skill = skill_df.sort_values('cluster')
m_colors = ['#27AE60','#E74C3C','#3498DB','#9B59B6']
metric_names = ['POD','FAR','CSI','HSS']
for i, (metric, mc) in enumerate(zip(metric_names, m_colors)):
    vals = sorted_skill[metric].values
    ax4.bar(x + (i-1.5)*w, vals, w, label=metric, color=mc, alpha=0.85)
    for j, v in enumerate(vals):
        if not np.isnan(v):
            ax4.text(x[j]+(i-1.5)*w, v+0.01, f'{v:.2f}',
                    ha='center', fontsize=6)
ax4.set_xticks(x)
ax4.set_xticklabels([REGIME_NAMES[c] for c in sorted_skill['cluster']],
                    fontsize=8, rotation=15, ha='right')
ax4.axhline(0, color='black', linewidth=0.8)
ax4.set_ylabel('Score', fontsize=10)
ax4.set_title('Model Skill per Synoptic Regime — Slot 2 Calibrated v2',
              fontsize=11, fontweight='bold')
ax4.legend(fontsize=9)
ax4.grid(axis='y', alpha=0.3)
ax4.spines[['top','right']].set_visible(False)

# ── Panel 5: CAPE vs q850 scatter coloured by cluster ─────────────────────────
ax5 = fig.add_subplot(gs[1, 2])
for c in range(N_CLUSTERS):
    mask = slot2['cluster'] == c
    ts_mask   = slot2['daily_label'] == 1
    nts_mask  = slot2['daily_label'] == 0
    ax5.scatter(slot2.loc[mask&nts_mask,'ERA5_q_850hPa']*1000,
                slot2.loc[mask&nts_mask,'ERA5_CAPE'],
                c=colors[c], alpha=0.2, s=6)
    ax5.scatter(slot2.loc[mask&ts_mask,'ERA5_q_850hPa']*1000,
                slot2.loc[mask&ts_mask,'ERA5_CAPE'],
                c=colors[c], alpha=0.9, s=25, marker='*',
                label=REGIME_NAMES[c])
ax5.set_xlabel('q850 (g/kg) — Low-level moisture', fontsize=10)
ax5.set_ylabel('ERA5 CAPE (J/kg)', fontsize=10)
ax5.set_title('CAPE vs Moisture by Regime\n★ = Thunderstorm days',
              fontsize=10, fontweight='bold')
ax5.legend(fontsize=7, markerscale=1.5)
ax5.grid(alpha=0.3)
ax5.spines[['top','right']].set_visible(False)

plt.suptitle('Synoptic Weather Regime Analysis — Bengaluru Airport 2015-2025\n'
             f'KMeans k={N_CLUSTERS} on ERA5 Atmospheric Features',
             fontsize=13, y=1.01)
path = FIGS / "chart13_synoptic_clustering.png"
fig.savefig(path, dpi=150, bbox_inches='tight')
plt.close()
print(f"  Chart saved → {path}")

# ── STEP 5: SAVE ──────────────────────────────────────────────────────────────
print("\n[5/5] Saving results...")
slot2[['date','cluster']].rename(
    columns={'cluster':'synoptic_cluster'}).to_csv(
    BASE / "data" / "synoptic_clusters.csv", index=False)

profiles_df.to_csv(RESULTS / "synoptic_cluster_profiles.csv", index=False)
skill_df.to_csv(RESULTS / "synoptic_skill_per_regime.csv", index=False)

joblib.dump({'kmeans': kmeans, 'scaler': scaler_cl,
             'pca': pca, 'features': CLUSTER_FEATURES,
             'regime_names': REGIME_NAMES},
            MODELS / "synoptic_clusterer.pkl")

# ── PRINT SUMMARY ─────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("SYNOPTIC CLUSTERING SUMMARY")
print("="*60)
print(f"\n{'Regime':<25} {'Days':<7} {'TS%':<7} {'AUROC':<8} "
      f"{'POD':<7} {'FAR':<7} {'HSS'}")
print("-"*65)
for _, r in skill_df.sort_values('ts_rate', ascending=False).iterrows():
    auroc_str = f"{r['AUROC']:.3f}" if not np.isnan(r['AUROC']) else "  N/A"
    print(f"  {r['regime_name']:<23} {r['n_days']:<7} {r['ts_rate']:<7} "
          f"{auroc_str:<8} {r['POD']:<7} {r['FAR']:<7} {r['HSS']}")

print("\nA11 complete.")
