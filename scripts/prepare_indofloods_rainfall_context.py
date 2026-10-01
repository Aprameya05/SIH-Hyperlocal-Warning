#!/usr/bin/env python3
"""
Phase 5.5 (Part 4) prototype: event-centered rainfall CONTEXT, not a
negative/training dataset.

Reads the real IMD 0.25-degree gridded daily rainfall files already in
this repo (`imd_rain/rain/{year}.grd`), regrids them onto the canonical
992-cell 1.0-degree grid (bounds/step reused read-only from
`scripts/map_indofloods_to_grid.py` / `backend/pipeline.py` -- not
reinvented), and for every mapped INDOFLOODS flood event whose
`Start Date` falls inside the IMD files' real coverage (2015-2025),
extracts the real antecedent daily rainfall for that event's canonical
cell over [Start Date - CONTEXT_DAYS, Start Date] using the source's own
timestamps.

This is READ-ONLY with respect to:
  - the IMD .grd files (only read, never modified)
  - processed/indofloods/indofloods_grid_events.csv (only read)
  - the canonical grid definition (bounds are copied read-only, matching
    scripts/map_indofloods_to_grid.py; nothing is re-derived differently)
  - backend/pipeline.py and all production JSON/frontend files (never
    touched)

Output is written ONLY to processed/indofloods/rainfall_context_preview.csv
(a preview artifact for this audit, not a production file, not a "negative
dataset" -- it contains only real antecedent-rainfall context rows for
already-known positive flood-event dates; see
docs/FF_HISTORICAL_PREDICTOR_AUDIT.md).

Deterministic: reading the same .grd files and events CSV twice produces
byte-identical output (no randomness, no wall-clock dependence).
"""

import csv
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIN_DIR = os.path.join(REPO_ROOT, "imd_rain", "rain")
EVENTS_CSV = os.path.join(REPO_ROOT, "processed", "indofloods", "indofloods_grid_events.csv")
OUT_CSV = os.path.join(REPO_ROOT, "processed", "indofloods", "rainfall_context_preview.csv")

# IMD .grd native grid (read-only, mirrored from dev/Fetch cb ff labels.py
# / scripts/build_panindia_cb_labels.py's own documented constants for this
# same file family -- not invented here).
IMD_LAT_START, IMD_LAT_END = 6.5, 38.5
IMD_LON_START, IMD_LON_END = 66.5, 100.0
IMD_STEP = 0.25
IMD_N_LAT = int(round((IMD_LAT_END - IMD_LAT_START) / IMD_STEP)) + 1  # 129
IMD_N_LON = int(round((IMD_LON_END - IMD_LON_START) / IMD_STEP)) + 1  # 135
IMD_MISSING = -999.0

# Canonical 992-cell grid (read-only, mirrored from
# scripts/map_indofloods_to_grid.py -- same values, not re-derived
# differently).
GRID_S, GRID_N, GRID_W, GRID_E = 6.0, 37.0, 68.0, 98.0
GRID_STEP = 1.0

CONTEXT_DAYS = 5  # T-5..T0, inclusive of the event start date itself
IMD_YEARS_AVAILABLE = list(range(2015, 2026))


def imd_lat_lon_arrays():
    lats = np.round(np.arange(IMD_LAT_START, IMD_LAT_END + IMD_STEP / 2, IMD_STEP), 4)
    lons = np.round(np.arange(IMD_LON_START, IMD_LON_END + IMD_STEP / 2, IMD_STEP), 4)
    return lats, lons


def canonical_cell_to_imd_indices(cell_lat, cell_lon, imd_lats, imd_lons):
    """Every IMD 0.25-degree grid point whose center falls inside the
    1.0-degree canonical cell [cell_lat, cell_lat+1) x [cell_lon, cell_lon+1).
    """
    lat_mask = (imd_lats >= cell_lat) & (imd_lats < cell_lat + GRID_STEP)
    lon_mask = (imd_lons >= cell_lon) & (imd_lons < cell_lon + GRID_STEP)
    lat_idx = np.where(lat_mask)[0]
    lon_idx = np.where(lon_mask)[0]
    return lat_idx, lon_idx


def read_grd_year(year):
    n_days = 366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365
    path_candidates = [os.path.join(RAIN_DIR, f"{year}.grd")]
    path = next((p for p in path_candidates if os.path.exists(p)), None)
    if path is None:
        return None, None
    expected = n_days * IMD_N_LAT * IMD_N_LON * 4
    size = os.path.getsize(path)
    if size != expected:
        print(f"  WARNING: {year}.grd size {size} != expected {expected}, skipping year")
        return None, None
    arr = np.fromfile(path, dtype=np.float32).reshape(n_days, IMD_N_LAT, IMD_N_LON)
    dates = pd.date_range(f"{year}-01-01", periods=n_days, freq="D")
    return arr, dates


def canonical_cell_daily_series(arr_by_year, dates_by_year, cell_lat, cell_lon, imd_lats, imd_lons):
    """Area-mean over the IMD points inside this canonical cell, per day,
    across every loaded year. Returns a pandas Series indexed by real date.
    NaN where all IMD points in the cell are the -999 sentinel that day."""
    lat_idx, lon_idx = canonical_cell_to_imd_indices(cell_lat, cell_lon, imd_lats, imd_lons)
    if len(lat_idx) == 0 or len(lon_idx) == 0:
        return pd.Series(dtype=float)
    parts = []
    for year, arr in arr_by_year.items():
        sub = arr[:, lat_idx[:, None], lon_idx[None, :]]  # (days, nlat, nlon)
        sub = sub.reshape(sub.shape[0], -1).astype(float)
        sub[sub <= (IMD_MISSING + 1)] = np.nan
        daily_mean = np.nanmean(sub, axis=1)  # NaN if all masked that day
        parts.append(pd.Series(daily_mean, index=dates_by_year[year]))
    return pd.concat(parts).sort_index()


def main():
    if not os.path.exists(EVENTS_CSV):
        print(f"ERROR: {EVENTS_CSV} not found. Run scripts/map_indofloods_to_grid.py first.")
        sys.exit(1)

    events = pd.read_csv(EVENTS_CSV)
    events = events[events["mapping_status"] == "MAPPED"].copy()
    events["Start Date"] = pd.to_datetime(events["Start Date"])

    window_start = pd.Timestamp(f"{IMD_YEARS_AVAILABLE[0]}-01-01")
    window_end = pd.Timestamp(f"{IMD_YEARS_AVAILABLE[-1]}-12-31")
    in_window = events[(events["Start Date"] >= window_start) & (events["Start Date"] <= window_end)].copy()
    print(f"Mapped events total: {len(events)}")
    print(f"Events with Start Date inside IMD coverage ({IMD_YEARS_AVAILABLE[0]}-{IMD_YEARS_AVAILABLE[-1]}): {len(in_window)}")
    print(f"Distinct cells among those: {in_window['cell_id'].nunique()}")
    print(f"Distinct dates among those: {in_window['Start Date'].nunique()}")

    print("Loading IMD .grd files ...")
    arr_by_year, dates_by_year = {}, {}
    for year in IMD_YEARS_AVAILABLE:
        arr, dates = read_grd_year(year)
        if arr is not None:
            arr_by_year[year] = arr
            dates_by_year[year] = dates
    print(f"Loaded {len(arr_by_year)} of {len(IMD_YEARS_AVAILABLE)} years.")

    imd_lats, imd_lons = imd_lat_lon_arrays()

    rows = []
    cell_series_cache = {}
    n_fully_masked = 0
    n_ok = 0
    for _, ev in in_window.iterrows():
        cell_lat, cell_lon = ev["cell_lat"], ev["cell_lon"]
        cache_key = (cell_lat, cell_lon)
        if cache_key not in cell_series_cache:
            cell_series_cache[cache_key] = canonical_cell_daily_series(
                arr_by_year, dates_by_year, cell_lat, cell_lon, imd_lats, imd_lons
            )
        series = cell_series_cache[cache_key]
        if series.empty:
            continue
        start = ev["Start Date"]
        window_dates = pd.date_range(start - pd.Timedelta(days=CONTEXT_DAYS), start, freq="D")
        vals = series.reindex(window_dates)
        if vals.isna().all():
            n_fully_masked += 1
            continue
        n_ok += 1
        for d, v in vals.items():
            rows.append({
                "EventID": ev["EventID"],
                "GaugeID": ev["GaugeID"],
                "cell_id": ev["cell_id"],
                "event_start_date": start.date().isoformat(),
                "context_date": d.date().isoformat(),
                "days_before_event_start": (start - d).days,
                "imd_rain_mm": None if pd.isna(v) else round(float(v), 3),
            })

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "EventID", "GaugeID", "cell_id", "event_start_date",
            "context_date", "days_before_event_start", "imd_rain_mm",
        ])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"Events with usable (non-fully-masked) rainfall context: {n_ok}")
    print(f"Events skipped (cell fully masked/no IMD land point in window): {n_fully_masked}")
    print(f"Context rows written: {len(rows)} -> {OUT_CSV}")
    print("NOTE: this is antecedent rainfall CONTEXT for known positive event")
    print("dates only. It is not a negative/background dataset.")


if __name__ == "__main__":
    main()
