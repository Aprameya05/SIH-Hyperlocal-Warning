"""
fetch_cb_ff_labels.py
=====================
Derives Cloudburst (CB) and Flash Flood (FF) labels for BLR (VOBL station)
from two sources:
  1. IMD 0.25-degree gridded daily rainfall (.grd files, read directly as binary)
     -- CB label: RF >= 100 mm/day (IMD cloudburst threshold)
  2. INDOFLOODS database (floodevents_indofloods.csv)
     -- FF label: any flood event within 200 km of BLR

Then merges both labels into the existing training dataset:
  data/bengaluru_6hr_training_dataset_v4.csv

Output:
  data/bengaluru_6hr_training_dataset_cb_ff.csv
  (same as v4 but with two extra columns: cb_label, ff_label)

Usage:
  python fetch_cb_ff_labels.py
"""

import os
import glob
import numpy as np
import pandas as pd

# ── Config ────────────────────────────────────────────────────────────────────

IMD_RAIN_DIR   = "./imd_rain/rain/"      # folder containing 2015.grd, 2016.grd, ...
FLOOD_EVENTS   = "data/floodevents_indofloods.csv"
CATCHMENT_FILE = "data/catchment_characteristics_indofloods.csv"
TRAINING_CSV   = "data/bengaluru_6hr_training_dataset_v4.csv"
OUTPUT_CSV     = "data/bengaluru_6hr_training_dataset_cb_ff.csv"

# BLR / VOBL coordinates
BLR_LAT = 12.97
BLR_LON = 77.58

# IMD 0.25-degree grid parameters
LAT_START, LAT_END = 6.5, 38.5
LON_START, LON_END = 66.5, 100.0
GRID_STEP = 0.25

# Grid size
N_LAT = int(round((LAT_END - LAT_START) / GRID_STEP)) + 1   # 129
N_LON = int(round((LON_END - LON_START) / GRID_STEP)) + 1   # 135

START_YEAR = 2015
END_YEAR   = 2025

# Thresholds
CB_THRESHOLD_MM = 64.5
FF_RADIUS_KM    = 200.0

# ── Step 1: Read .grd files directly ─────────────────────────────────────────

def get_blr_grid_index():
    lats = np.arange(LAT_START, LAT_END + GRID_STEP / 2, GRID_STEP)
    lons = np.arange(LON_START, LON_END + GRID_STEP / 2, GRID_STEP)
    lat_idx = int(np.argmin(np.abs(lats - BLR_LAT)))
    lon_idx = int(np.argmin(np.abs(lons - BLR_LON)))
    print(f"BLR grid cell: lat={lats[lat_idx]:.2f} lon={lons[lon_idx]:.2f} "
          f"(idx {lat_idx}, {lon_idx})")
    return lat_idx, lon_idx


def read_grd_year(filepath, year, lat_idx, lon_idx):
    """
    IMD .grd format: raw float32, shape (days, N_LAT, N_LON), row-major.
    Missing value = -999.0
    """
    # Days in year
    n_days = 366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365
    expected_bytes = n_days * N_LAT * N_LON * 4

    file_size = os.path.getsize(filepath)
    if file_size != expected_bytes:
        # Some years have a 4-byte header -- try skipping it
        if file_size == expected_bytes + 4:
            with open(filepath, 'rb') as f:
                f.read(4)
                arr = np.frombuffer(f.read(), dtype=np.float32).reshape(n_days, N_LAT, N_LON)
        else:
            print(f"  Warning: unexpected file size for {year}: {file_size} bytes "
                  f"(expected {expected_bytes}). Skipping.")
            return None
    else:
        arr = np.fromfile(filepath, dtype=np.float32).reshape(n_days, N_LAT, N_LON)

    series = arr[:, lat_idx, lon_idx].astype(float)
    series[series <= -900] = np.nan
    return series


def load_imd_daily_rain(lat_idx, lon_idx):
    records = []
    for year in range(START_YEAR, END_YEAR + 1):
        # Try both possible filename patterns
        candidates = [
            os.path.join(IMD_RAIN_DIR, f"{year}.grd"),
            os.path.join(IMD_RAIN_DIR, f"RF{year}.grd"),
            os.path.join(IMD_RAIN_DIR, f"rain{year}.grd"),
        ]
        found = next((p for p in candidates if os.path.exists(p)), None)

        if found is None:
            print(f"  Warning: no .grd file found for {year} in {IMD_RAIN_DIR}")
            continue

        print(f"  Reading {found} ...", end=" ")
        series = read_grd_year(found, year, lat_idx, lon_idx)
        if series is None:
            continue

        dates = pd.date_range(f"{year}-01-01", periods=len(series), freq='D')
        for d, v in zip(dates, series):
            records.append({'date': d.date(), 'imd_rf_mm': float(v)})
        print(f"{len(series)} days, BLR max={np.nanmax(series):.1f} mm")

    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'])
    print(f"\nIMD rainfall loaded: {len(df)} daily records, "
          f"{df['imd_rf_mm'].isna().sum()} missing")
    return df


# ── Step 2: Derive CB label ────────────────────────────────────────────────

def derive_cb_labels(rain_df):
    rain_df = rain_df.copy()
    rain_df['cb_label'] = (rain_df['imd_rf_mm'] >= CB_THRESHOLD_MM).astype(int)
    n = rain_df['cb_label'].sum()
    print(f"CB events (RF >= {CB_THRESHOLD_MM} mm): {n} days")
    return rain_df


# ── Step 3: Derive FF label from INDOFLOODS ────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlon/2)**2
    return R * 2 * np.arcsin(np.sqrt(a))


def load_gauge_locations():
    if os.path.exists(CATCHMENT_FILE):
        cc = pd.read_csv(CATCHMENT_FILE)
        print(f"Catchment file loaded: {len(cc)} gauges")
        lat_col = next((c for c in cc.columns if 'lat' in c.lower()), None)
        lon_col = next((c for c in cc.columns if 'lon' in c.lower()), None)
        id_col  = next((c for c in cc.columns if 'gauge' in c.lower() or 'id' in c.lower()), None)
        if lat_col and lon_col and id_col:
            cc = cc[[id_col, lat_col, lon_col]].copy()
            cc.columns = ['gauge_id', 'lat', 'lon']
            cc['gauge_id'] = cc['gauge_id'].astype(str)
            return cc
        else:
            print(f"  Could not identify lat/lon cols: {list(cc.columns)}")
    print("No catchment file -- using RF-based FF proxy")
    return None


def derive_ff_labels(rain_df):
    fe = pd.read_csv(FLOOD_EVENTS)
    fe['Start Date'] = pd.to_datetime(fe['Start Date'])
    fe['End Date']   = pd.to_datetime(fe['End Date'])
    fe['gauge_id']   = fe['EventID'].str.extract(r'gauge-(\d+)')[0].astype(str)

    gauges = load_gauge_locations()

    ff_dates = set()
    if gauges is not None:
        gauges['dist_km'] = gauges.apply(
            lambda r: haversine_km(BLR_LAT, BLR_LON, r['lat'], r['lon']), axis=1
        )
        near = gauges[gauges['dist_km'] <= FF_RADIUS_KM]['gauge_id'].tolist()
        print(f"Gauges within {FF_RADIUS_KM} km of BLR: {len(near)}")

        if near:
            events = fe[fe['gauge_id'].isin(near)]
            print(f"Flood events near BLR: {len(events)}")
            for _, row in events.iterrows():
                d = row['Start Date']
                while d <= row['End Date']:
                    ff_dates.add(d.date())
                    d += pd.Timedelta(days=1)

    if ff_dates:
        rain_df = rain_df.copy()
        rain_df['ff_label'] = rain_df['date'].dt.date.isin(ff_dates).astype(int)
        print(f"FF events (INDOFLOODS): {rain_df['ff_label'].sum()} days")
        return rain_df

    # Fallback: RF-based proxy
    print("Using RF-based FF proxy (3-day cumsum >= 150 mm AND daily >= 50 mm)")
    rain_df = rain_df.copy()
    rain_df['rf_3d'] = rain_df['imd_rf_mm'].rolling(3, min_periods=1).sum()
    rain_df["ff_label"] = ((rain_df["rf_3d"] >= 100) & (rain_df["imd_rf_mm"] >= 40)).astype(int)
    print(f"FF events (proxy): {rain_df['ff_label'].sum()} days")
    return rain_df


# ── Step 4: Merge into training dataset ───────────────────────────────────

def merge_labels():
    print("\n=== Loading IMD grid data ===")
    lat_idx, lon_idx = get_blr_grid_index()
    rain_df = load_imd_daily_rain(lat_idx, lon_idx)

    if rain_df.empty:
        print(f"\nERROR: No .grd files loaded. Check that {IMD_RAIN_DIR} contains files like 2015.grd")
        return

    print("\n=== Deriving CB labels ===")
    rain_df = derive_cb_labels(rain_df)

    print("\n=== Deriving FF labels ===")
    rain_df = derive_ff_labels(rain_df)

    print("\n=== Merging into training dataset ===")
    train = pd.read_csv(TRAINING_CSV)
    train['date'] = pd.to_datetime(train['date'])

    label_df = rain_df[['date', 'imd_rf_mm', 'cb_label', 'ff_label']].copy()
    label_df['date'] = pd.to_datetime(label_df['date'])

    merged = train.merge(label_df, on='date', how='left')
    merged['cb_label'] = merged['cb_label'].fillna(0).astype(int)
    merged['ff_label'] = merged['ff_label'].fillna(0).astype(int)

    print(f"\nFinal dataset shape: {merged.shape}")
    print(f"CB label dist:\n{merged['cb_label'].value_counts().to_string()}")
    print(f"\nFF label dist:\n{merged['ff_label'].value_counts().to_string()}")
    print(f"\nTS label dist:\n{merged['ts_label'].value_counts().to_string()}")
    print(f"\nTS-CB overlap: {((merged['ts_label']==1) & (merged['cb_label']==1)).sum()} rows")
    print(f"CB-FF overlap: {((merged['cb_label']==1) & (merged['ff_label']==1)).sum()} rows")

    merged.to_csv(OUTPUT_CSV, index=False)
    print(f"\nSaved -> {OUTPUT_CSV}")


if __name__ == "__main__":
    merge_labels()