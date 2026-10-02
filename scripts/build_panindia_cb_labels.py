#!/usr/bin/env python3
"""
build_panindia_cb_labels.py

Generalizes the existing, real, IMD-gridded-rainfall-derived cloudburst
methodology (dev/Fetch cb ff labels.py) from Bengaluru-only to all 992
canonical cells. Threshold is UNCHANGED from what the code actually runs
(CB_THRESHOLD_MM = 64.5 -- note: dev/Fetch cb ff labels.py's own docstring
says "100 mm/day", but its executed CB_THRESHOLD_MM constant is 64.5; this
script matches the real executed behavior, not the stale comment -- see
docs/LABEL_ENGINE.md).

Source: imd_rain/rain/{2015-2025}.grd -- real IMD 0.25-degree daily
gridded rainfall, format verified this pass (file sizes match
129 x 135 x days x 4 bytes exactly, including leap years).

Temporal limitation, not hidden: this is a DAILY product. Output labels
carry temporal_resolution="daily" and apply to all 4 six-hour slots of
that date -- this script does not invent 6-hourly detail the source
doesn't have.

Output: processed/labels/cb_labels.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from regrid import cell_id_for  # noqa: E402

# IMD .grd native grid (from dev/Fetch cb ff labels.py, unchanged)
IMD_LAT_START, IMD_LAT_END = 6.5, 38.5
IMD_LON_START, IMD_LON_END = 66.5, 100.0
IMD_STEP = 0.25
IMD_N_LAT = int(round((IMD_LAT_END - IMD_LAT_START) / IMD_STEP)) + 1  # 129
IMD_N_LON = int(round((IMD_LON_END - IMD_LON_START) / IMD_STEP)) + 1  # 135

CB_THRESHOLD_MM = 64.5  # matches the ACTUAL executed constant, not the stale docstring

# Canonical 992-cell grid (docs/CANONICAL_GRID.md)
CANON_BOUNDS = {"S": 6, "N": 37, "W": 68, "E": 98}
CANON_STEP = 1.0

RAIN_DIR = REPO_ROOT / "imd_rain" / "rain"
OUT_DIR = REPO_ROOT / "processed" / "labels"
OUT_PATH = OUT_DIR / "cb_labels.csv"

SLOTS = [0, 1, 2, 3]


def imd_lat_lon_grids():
    lats = np.arange(IMD_LAT_START, IMD_LAT_END + IMD_STEP / 2, IMD_STEP)
    lons = np.arange(IMD_LON_START, IMD_LON_END + IMD_STEP / 2, IMD_STEP)
    return lats, lons


def canonical_lat_lon_grids():
    lats = [round(v, 1) for v in np.arange(CANON_BOUNDS["S"], CANON_BOUNDS["N"] + CANON_STEP * 0.5, CANON_STEP)]
    lons = [round(v, 1) for v in np.arange(CANON_BOUNDS["W"], CANON_BOUNDS["E"] + CANON_STEP * 0.5, CANON_STEP)]
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
        print(f"  WARNING: {path.name} unexpected size {size}, expected {expected}. Skipped.")
        return None, n_days
    arr = arr.copy()
    arr[arr <= -900] = np.nan
    return arr, n_days


def main():
    imd_lats, imd_lons = imd_lat_lon_grids()
    canon_lats, canon_lons = canonical_lat_lon_grids()

    # Precompute, for each canonical cell, which IMD grid indices fall in its +-0.5deg box
    canon_to_imd_idx = {}
    for clat in canon_lats:
        for clon in canon_lons:
            lat_mask = np.where(np.abs(imd_lats - clat) <= CANON_STEP / 2)[0]
            lon_mask = np.where(np.abs(imd_lons - clon) <= CANON_STEP / 2)[0]
            canon_to_imd_idx[(clat, clon)] = (lat_mask, lon_mask)

    years_available = sorted(int(p.stem) for p in RAIN_DIR.glob("*.grd"))
    print(f"Years found: {years_available}")

    all_rows = []
    n_positive = 0
    n_negative = 0
    n_missing = 0

    for year in years_available:
        path = RAIN_DIR / f"{year}.grd"
        arr, n_days = read_grd_year(path, year)
        if arr is None:
            continue
        dates = pd.date_range(f"{year}-01-01", periods=n_days, freq="D")

        for clat in canon_lats:
            for clon in canon_lons:
                lat_idx, lon_idx = canon_to_imd_idx[(clat, clon)]
                if len(lat_idx) == 0 or len(lon_idx) == 0:
                    continue  # canonical cell outside IMD's native grid bounds entirely
                sub = arr[:, lat_idx[:, None], lon_idx[None, :]]  # (n_days, nlat_sub, nlon_sub)
                daily_max = np.nanmax(sub.reshape(n_days, -1), axis=1)
                cell_id = cell_id_for(clat, clon)
                for d, v in zip(dates, daily_max):
                    if np.isnan(v):
                        n_missing += 1
                        continue
                    label = int(v >= CB_THRESHOLD_MM)
                    if label:
                        n_positive += 1
                    else:
                        n_negative += 1
                    if label:  # only materialize positives fully to keep file size sane;
                        # negatives are summarized in the coverage report, not omitted from counts
                        for slot in SLOTS:
                            all_rows.append({
                                "timestamp": f"{d.date()}T slot{slot}",
                                "cell_id": cell_id,
                                "hazard": "cb",
                                "label": 1,
                                "label_status": "POSITIVE",
                                "source": "IMD 0.25deg gridded daily rainfall",
                                "source_event_id": None,
                                "source_timestamp": str(d.date()),
                                "spatial_distance_km": None,
                                "temporal_distance_hours": None,
                                "quality_flag": "daily_resolution_applied_to_all_4_slots",
                                "temporal_resolution": "daily",
                                "threshold_mm": CB_THRESHOLD_MM,
                            })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_rows).to_csv(OUT_PATH, index=False)

    summary = {
        "years_processed": years_available,
        "canonical_cells_with_imd_coverage": sum(1 for k, v in canon_to_imd_idx.items() if len(v[0]) and len(v[1])),
        "total_canonical_cells": len(canon_lats) * len(canon_lons),
        "cell_days_positive": n_positive,
        "cell_days_negative_confirmed": n_negative,
        "cell_days_missing": n_missing,
        "threshold_mm": CB_THRESHOLD_MM,
        "positive_rate_pct": round(100.0 * n_positive / (n_positive + n_negative), 4) if (n_positive + n_negative) else None,
        "temporal_resolution": "daily (source), applied to all 4 six-hour slots -- see docs/LABEL_ENGINE.md",
    }
    print(summary)
    import json
    (OUT_DIR / "cb_labels_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"Written: {OUT_PATH} ({len(all_rows)} positive-slot rows)")


if __name__ == "__main__":
    main()
