#!/usr/bin/env python3
"""
build_panindia_ff_labels.py

Attempts genuine event-based flash-flood labeling from
data/floodevents_indofloods.csv, generalized pan-India (NOT the old
"blindly apply 200km-of-Bengaluru" approach).

REAL FINDING (see docs/LABEL_ENGINE.md for full detail): there is no
gauge-coordinate file anywhere in this repo. data/floodevents_indofloods.csv
and data/precipitation_variables_indofloods.csv both lack lat/lon;
data/catchment_characteristics_indofloods.csv (which would have carried
gauge coordinates) does not exist. Without it, no INDOFLOODS event can be
spatially assigned to any of the 992 canonical cells at all -- not just
outside Bengaluru, everywhere, including Bengaluru itself.

So this script does two honest things instead of one dishonest thing:
  1. Marks every canonical cell UNKNOWN for genuine event-based FF (not
     NEGATIVE_CONFIRMED -- the absence of an assignable event reflects a
     missing coordinate file, not an absence of floods).
  2. Separately computes a rainfall-only FF PROXY (reusing the exact
     fallback formula already in dev/Fetch_cb_ff_labels.py: 3-day
     cumulative >= 100mm AND daily >= 40mm), labeled label_status=
     PROXY_NOT_OBSERVED so nothing downstream can mistake it for an
     observed flood.

Output: processed/labels/ff_labels_proxy.csv (proxy only -- the UNKNOWN
event-based result is summarized in ff_labels_summary.json since
materializing "UNKNOWN" for 992 cells x ~4000 dates x 4 slots as full rows
would be an 15M-row file with zero informational content).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from regrid import cell_id_for  # noqa: E402

IMD_LAT_START, IMD_LAT_END = 6.5, 38.5
IMD_LON_START, IMD_LON_END = 66.5, 100.0
IMD_STEP = 0.25
IMD_N_LAT = int(round((IMD_LAT_END - IMD_LAT_START) / IMD_STEP)) + 1
IMD_N_LON = int(round((IMD_LON_END - IMD_LON_START) / IMD_STEP)) + 1

CANON_BOUNDS = {"S": 6, "N": 37, "W": 68, "E": 98}
CANON_STEP = 1.0

RAIN_DIR = REPO_ROOT / "imd_rain" / "rain"
FLOOD_EVENTS = REPO_ROOT / "data" / "floodevents_indofloods.csv"
CATCHMENT_FILE = REPO_ROOT / "data" / "catchment_characteristics_indofloods.csv"
OUT_DIR = REPO_ROOT / "processed" / "labels"
OUT_PATH = OUT_DIR / "ff_labels_proxy.csv"

SLOTS = [0, 1, 2, 3]


def canonical_lat_lon_grids():
    lats = [round(v, 1) for v in np.arange(CANON_BOUNDS["S"], CANON_BOUNDS["N"] + CANON_STEP * 0.5, CANON_STEP)]
    lons = [round(v, 1) for v in np.arange(CANON_BOUNDS["W"], CANON_BOUNDS["E"] + CANON_STEP * 0.5, CANON_STEP)]
    return lats, lons


def imd_lat_lon_grids():
    lats = np.arange(IMD_LAT_START, IMD_LAT_END + IMD_STEP / 2, IMD_STEP)
    lons = np.arange(IMD_LON_START, IMD_LON_END + IMD_STEP / 2, IMD_STEP)
    return lats, lons


def read_grd_year(path: Path, year: int):
    n_days = 366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365
    expected = n_days * IMD_N_LAT * IMD_N_LON * 4
    size = path.stat().st_size
    if size == expected:
        arr = np.fromfile(path, dtype=np.float32).reshape(n_days, IMD_N_LAT, IMD_N_LON)
    elif size == expected + 4:
        with open(path, "rb") as f:
            f.read(4)
            arr = np.frombuffer(f.read(), dtype=np.float32).reshape(n_days, IMD_N_LAT, IMD_N_LON)
    else:
        return None, n_days
    arr = arr.copy()
    arr[arr <= -900] = np.nan
    return arr, n_days


def step1_event_based_check():
    """Verify, explicitly, that spatial assignment is impossible -- not
    assumed, checked directly against the real files."""
    fe = pd.read_csv(FLOOD_EVENTS)
    has_latlon_in_events = any("lat" in c.lower() or "lon" in c.lower() for c in fe.columns)
    catchment_exists = CATCHMENT_FILE.exists()
    n_events = len(fe)
    n_gauges = fe["EventID"].str.extract(r"gauge-(\d+)")[0].nunique()
    return {
        "n_indofloods_events": n_events,
        "n_unique_gauges": int(n_gauges),
        "floodevents_csv_has_latlon_columns": has_latlon_in_events,
        "catchment_file_exists": catchment_exists,
        "genuine_spatial_assignment_possible": has_latlon_in_events or catchment_exists,
        "conclusion": (
            "IMPOSSIBLE -- no coordinate source exists for any INDOFLOODS gauge in this repo"
            if not (has_latlon_in_events or catchment_exists)
            else "possible -- re-run with real assignment logic"
        ),
    }


def step2_rainfall_proxy():
    imd_lats, imd_lons = imd_lat_lon_grids()
    canon_lats, canon_lons = canonical_lat_lon_grids()
    canon_to_imd_idx = {}
    for clat in canon_lats:
        for clon in canon_lons:
            lat_mask = np.where(np.abs(imd_lats - clat) <= CANON_STEP / 2)[0]
            lon_mask = np.where(np.abs(imd_lons - clon) <= CANON_STEP / 2)[0]
            canon_to_imd_idx[(clat, clon)] = (lat_mask, lon_mask)

    years_available = sorted(int(p.stem) for p in RAIN_DIR.glob("*.grd"))
    rows = []
    n_positive = 0
    n_negative = 0

    for year in years_available:
        arr, n_days = read_grd_year(RAIN_DIR / f"{year}.grd", year)
        if arr is None:
            continue
        dates = pd.date_range(f"{year}-01-01", periods=n_days, freq="D")

        for clat in canon_lats:
            for clon in canon_lons:
                lat_idx, lon_idx = canon_to_imd_idx[(clat, clon)]
                if len(lat_idx) == 0 or len(lon_idx) == 0:
                    continue
                sub = arr[:, lat_idx[:, None], lon_idx[None, :]]
                daily_max = np.nanmax(sub.reshape(n_days, -1), axis=1)
                daily_series = pd.Series(daily_max, index=dates)
                rf_3d = daily_series.rolling(3, min_periods=1).sum()
                proxy_positive = (rf_3d >= 100) & (daily_series >= 40)
                cell_id = cell_id_for(clat, clon)
                for d, is_pos, v in zip(dates, proxy_positive, daily_series):
                    if np.isnan(v):
                        continue
                    if is_pos:
                        n_positive += 1
                        for slot in SLOTS:
                            rows.append({
                                "timestamp": f"{d.date()}T slot{slot}",
                                "cell_id": cell_id,
                                "hazard": "ff",
                                "label": 1,
                                "label_status": "PROXY_NOT_OBSERVED",
                                "source": "rainfall-only proxy (3d cumsum>=100mm AND daily>=40mm), "
                                          "reusing dev/Fetch cb ff labels.py's own fallback formula",
                                "source_event_id": None,
                                "source_timestamp": str(d.date()),
                                "spatial_distance_km": None,
                                "temporal_distance_hours": None,
                                "quality_flag": "NOT_an_observed_flood_event -- rainfall proxy only",
                                "temporal_resolution": "daily",
                            })
                    else:
                        n_negative += 1

    return rows, n_positive, n_negative, len(canon_lats) * len(canon_lons)


def main():
    print("Step 1: checking whether genuine event-based spatial assignment is possible...")
    check = step1_event_based_check()
    print(json.dumps(check, indent=2))

    print("\nStep 2: computing rainfall-only PROXY (explicitly not an observed-flood label)...")
    rows, n_pos, n_neg, n_cells = step2_rainfall_proxy()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_PATH, index=False)

    summary = {
        "genuine_event_based_ff": {
            "status": "UNKNOWN for all 992 cells, all timestamps",
            "reason": check["conclusion"],
            "n_indofloods_events_in_source_file": check["n_indofloods_events"],
            "n_unique_gauges_in_source_file": check["n_unique_gauges"],
        },
        "rainfall_proxy_ff": {
            "status": "PROXY_NOT_OBSERVED -- not a real flood observation",
            "cell_days_positive": n_pos,
            "cell_days_negative": n_neg,
            "positive_rate_pct": round(100.0 * n_pos / (n_pos + n_neg), 4) if (n_pos + n_neg) else None,
            "total_canonical_cells": n_cells,
        },
    }
    print(json.dumps(summary, indent=2))
    (OUT_DIR / "ff_labels_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nWritten: {OUT_PATH} ({len(rows)} proxy-positive-slot rows)")


if __name__ == "__main__":
    main()
