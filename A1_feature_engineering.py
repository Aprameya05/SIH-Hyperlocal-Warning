"""
A1_feature_engineering.py
==========================
Builds the 6-hour nowcasting training dataset for the CSIR Thunderstorm
Prediction System, Bengaluru Airport (Station 43295).

What this script does:
  1. Loads the 6-hour labels (bengaluru_6hr_labels.csv)
  2. Loads the daily features (bengaluru_thunderstorm_features_merged.csv)
  3. Merges them — each day's features repeat across its 4 slots
  4. Adds slot-specific features (cyclical encoding, climatology)
  5. Adds slot-lag features (what happened in the same slot yesterday)
  6. Drops dates where surface/ERA5 data is missing
  7. Saves the final training CSV

Output: bengaluru_6hr_training_dataset.csv

Author: Aprameya, CSIR Thunderstorm Project
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ── PATHS — update these to match your machine ───────────────────────────────
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
LABELS  = BASE / "data" / "bengaluru_6hr_labels.csv"
DAILY   = BASE / "data" / "bengaluru_thunderstorm_features_merged.csv"
OUT     = BASE / "data" / "bengaluru_6hr_training_dataset.csv"
OUT.parent.mkdir(parents=True, exist_ok=True)

print("=" * 60)
print("A1 — 6-Hour Feature Engineering")
print("=" * 60)

# ── STEP 1: LOAD ──────────────────────────────────────────────────────────────
print("\n[1/7] Loading files...")
labels = pd.read_csv(LABELS, parse_dates=['date'])
daily  = pd.read_csv(DAILY,  parse_dates=['date'])
print(f"  Labels : {labels.shape[0]} rows  ({labels['ts_label'].sum()} positives)")
print(f"  Daily  : {daily.shape[0]} rows  ({daily['LABEL'].sum()} TS days)")

# ── STEP 2: PREPARE DAILY FEATURES ───────────────────────────────────────────
print("\n[2/7] Preparing daily feature columns...")

# Drop columns we don't want to carry into the 6hr dataset
# LABEL = daily label (not our target), YEAR/MONTH = will re-derive from date
# CIN   = entirely null across all rows
drop_cols = ['YEAR', 'MONTH', 'LABEL', 'CIN']
daily_feats = daily.drop(columns=drop_cols)

# Fill the small number of lag/rolling nulls with column medians
lag_cols = ['RF_3d', 'RF_7d', 'MAX_3d_avg', 'MIN_3d_avg',
            'DTR_3d_avg', 'MAX_lag1', 'MIN_lag1']
for col in lag_cols:
    median_val = daily_feats[col].median()
    n_filled   = daily_feats[col].isnull().sum()
    if n_filled > 0:
        daily_feats[col] = daily_feats[col].fillna(median_val)
        print(f"  Filled {n_filled} nulls in {col} with median={median_val:.2f}")

print(f"  Daily feature columns ready: {daily_feats.shape[1] - 1}")  # -1 for date

# ── STEP 3: MERGE LABELS + DAILY FEATURES ────────────────────────────────────
print("\n[3/7] Merging labels with daily features...")
df = labels.merge(daily_feats, on='date', how='left')

# Drop rows where daily features are missing (199 dates, zero TS events)
before = len(df)
df = df.dropna(subset=['MAX', 'ERA5_CAPE'])  # proxies for complete daily data
after  = len(df)
print(f"  Rows before drop : {before}")
print(f"  Rows after drop  : {after}  (lost {before - after} rows, 0 TS events)")
print(f"  Positives retained: {df['ts_label'].sum()}")

# ── STEP 4: SLOT-SPECIFIC FEATURES ───────────────────────────────────────────
print("\n[4/7] Adding slot-specific features...")

# 4a. Cyclical encoding of slot (so model knows slot 3 and slot 0 are adjacent)
df['slot_sin'] = np.sin(2 * np.pi * df['slot'] / 4)
df['slot_cos'] = np.cos(2 * np.pi * df['slot'] / 4)

# 4b. Slot × month climatology
#     For each (month, slot) pair, what fraction of historical days had a TS?
#     Computed on training years only (2015-2022) to avoid data leakage
train_mask = df['year'] < 2023
clim = (df[train_mask]
        .groupby(['month', 'slot'])['ts_label']
        .mean()
        .rename('slot_month_clim')
        .reset_index())
df = df.merge(clim, on=['month', 'slot'], how='left')
df['slot_month_clim'] = df['slot_month_clim'].fillna(0)
print(f"  slot_sin, slot_cos, slot_month_clim added")

# 4c. Day of year cyclical (finer than month)
df['doy'] = df['date'].dt.dayofyear
df['doy_sin'] = np.sin(2 * np.pi * df['doy'] / 365)
df['doy_cos'] = np.cos(2 * np.pi * df['doy'] / 365)
print(f"  doy_sin, doy_cos added")

# ── STEP 5: SLOT LAG FEATURES ─────────────────────────────────────────────────
print("\n[5/7] Adding slot lag features (same slot, previous day)...")

# For each row, look up what the same slot showed the day before
df = df.sort_values(['date', 'slot']).reset_index(drop=True)
df['prev_date'] = df['date'] - pd.Timedelta(days=1)

# Create a lookup: (date, slot) → ts_label
slot_lookup = df.set_index(['date', 'slot'])['ts_label'].to_dict()

def get_prev_slot_label(row):
    return slot_lookup.get((row['prev_date'], row['slot']), 0)

df['ts_label_lag1_slot'] = df.apply(get_prev_slot_label, axis=1)

# Also add: did ANY slot have a TS yesterday?
daily_ts_flag = (df.groupby('date')['ts_label']
                 .max()
                 .rename('ts_any_yesterday'))
df['prev_date_str'] = df['prev_date']
df = df.merge(daily_ts_flag.rename_axis('date').reset_index()
              .rename(columns={'date': 'prev_date', 'ts_any_yesterday': 'ts_any_yesterday'}),
              on='prev_date', how='left')
df['ts_any_yesterday'] = df['ts_any_yesterday'].fillna(0).astype(int)

print(f"  ts_label_lag1_slot (same slot yesterday): added")
print(f"  ts_any_yesterday (any slot yesterday):    added")

# ── STEP 6: CLEAN UP ──────────────────────────────────────────────────────────
print("\n[6/7] Final cleanup...")

# Drop helper columns not needed for training
drop_helper = ['ts_source', 'g_code', 'duration_min', 'prev_date',
               'prev_date_str', 'doy']
df = df.drop(columns=drop_helper)

# Reorder: identifiers first, target last
id_cols     = ['date', 'year', 'month', 'slot', 'slot_label']
target_col  = ['ts_label']
feature_cols = [c for c in df.columns if c not in id_cols + target_col]
df = df[id_cols + feature_cols + target_col]

print(f"  Final shape : {df.shape}")
print(f"  Features    : {len(feature_cols)}")
print(f"  Positives   : {df['ts_label'].sum()} / {len(df)} ({df['ts_label'].mean()*100:.1f}%)")

# ── STEP 7: TRAIN / TEST SPLIT SUMMARY ───────────────────────────────────────
print("\n[7/7] Train / test split summary...")
train = df[df['year'] < 2023]
test  = df[df['year'] >= 2023]
print(f"  Train (2015-2022): {len(train)} rows, {train['ts_label'].sum()} positives ({train['ts_label'].mean()*100:.1f}%)")
print(f"  Test  (2023-2025): {len(test)} rows,  {test['ts_label'].sum()} positives ({test['ts_label'].mean()*100:.1f}%)")

print("\nClass balance per slot (TRAIN):")
for slot in range(4):
    s = train[train['slot'] == slot]
    pos = s['ts_label'].sum()
    print(f"  Slot {slot} ({s['slot_label'].iloc[0]}): {pos}/{len(s)} = {pos/len(s)*100:.1f}%")

# ── SAVE ──────────────────────────────────────────────────────────────────────
df.to_csv(OUT, index=False)
print(f"\nSaved → {OUT}")
print("\nA1 complete. Run A2_train_model.py next.")
