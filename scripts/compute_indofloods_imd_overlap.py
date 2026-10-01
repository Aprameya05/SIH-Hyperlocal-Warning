#!/usr/bin/env python3
"""
Phase 5.6 (Task 5): READ-ONLY overlap computation between INDOFLOODS
events, the real IMD gridded daily rainfall (imd_rain/rain/*.grd), and the
canonical 992-cell grid.

Reuses the grid constants and .grd-reading logic from Phase 5.5's
scripts/prepare_indofloods_rainfall_context.py verbatim (not reinvented).
Writes nothing; only reads existing files and prints/returns numbers used
in docs/PHASE_5_6_HISTORICAL_DATA_DECISION.md.

Candidate background days are explicitly labeled "candidate unlabeled /
background" throughout -- never "negative" -- per Case B (Phase 5.5:
no true negatives are establishable from INDOFLOODS).
"""
import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
from prepare_indofloods_rainfall_context import (  # noqa: E402
    IMD_YEARS_AVAILABLE, RAIN_DIR, EVENTS_CSV,
    imd_lat_lon_arrays, canonical_cell_to_imd_indices, read_grd_year,
)


def main():
    events = pd.read_csv(EVENTS_CSV)
    events = events[events["mapping_status"] == "MAPPED"].copy()
    events["Start Date"] = pd.to_datetime(events["Start Date"])

    imd_lats, imd_lons = imd_lat_lon_arrays()

    arr_by_year, dates_by_year = {}, {}
    for year in IMD_YEARS_AVAILABLE:
        arr, dates = read_grd_year(year)
        if arr is not None:
            arr_by_year[year] = arr
            dates_by_year[year] = dates

    all_dates = pd.concat([pd.Series(d) for d in dates_by_year.values()]).sort_values()
    imd_earliest, imd_latest = all_dates.iloc[0], all_dates.iloc[-1]

    window_start = pd.Timestamp(f"{IMD_YEARS_AVAILABLE[0]}-01-01")
    window_end = pd.Timestamp(f"{IMD_YEARS_AVAILABLE[-1]}-12-31")
    in_window = events[(events["Start Date"] >= window_start) & (events["Start Date"] <= window_end)].copy()

    # earliest/latest common date = intersection of IMD availability and actual event dates in-window
    common_earliest = in_window["Start Date"].min()
    common_latest = in_window["Start Date"].max()

    unique_cell_dates = in_window[["cell_id", "Start Date"]].drop_duplicates()
    n_unique_event_celldates = len(unique_cell_dates)
    n_unique_cells = in_window["cell_id"].nunique()
    n_unique_gauges = in_window["GaugeID"].nunique() if "GaugeID" in in_window.columns else None

    # Candidate background days: for each event cell (69 cells), each real
    # rainfall day (non-NaN after masking) in 2015-2025 that is NOT an
    # event (cell, date) pair for that cell.
    event_cells = sorted(in_window["cell_id"].unique())
    cell_coords = {}
    for cid in event_cells:
        # cell_id format like "9.0_76.0" (lat_lon) per Phase 5.5 docs
        parts = cid.split("_")
        cell_coords[cid] = (float(parts[0]), float(parts[1]))

    total_rain_days = 0
    total_candidate_background = 0
    per_cell_rain_days = {}
    for cid, (clat, clon) in cell_coords.items():
        lat_idx, lon_idx = canonical_cell_to_imd_indices(clat, clon, imd_lats, imd_lons)
        if len(lat_idx) == 0 or len(lon_idx) == 0:
            per_cell_rain_days[cid] = 0
            continue
        parts = []
        for year, arr in arr_by_year.items():
            sub = arr[:, lat_idx[:, None], lon_idx[None, :]].reshape(arr.shape[0], -1).astype(float)
            sub[sub <= -998.0] = np.nan
            daily_mean = np.nanmean(sub, axis=1)
            parts.append(pd.Series(daily_mean, index=dates_by_year[year]))
        series = pd.concat(parts).sort_index()
        valid = series.dropna()
        per_cell_rain_days[cid] = len(valid)
        total_rain_days += len(valid)

        event_dates_this_cell = set(
            unique_cell_dates.loc[unique_cell_dates["cell_id"] == cid, "Start Date"]
        )
        n_event_days_this_cell = len(event_dates_this_cell & set(valid.index))
        total_candidate_background += (len(valid) - n_event_days_this_cell)

    result = {
        "imd_earliest_date": str(imd_earliest.date()),
        "imd_latest_date": str(imd_latest.date()),
        "events_in_2015_2025_window": len(in_window),
        "earliest_common_event_date": str(common_earliest.date()),
        "latest_common_event_date": str(common_latest.date()),
        "unique_event_cell_dates_in_overlap": n_unique_event_celldates,
        "unique_cells_in_overlap": n_unique_cells,
        "unique_gauges_in_overlap": n_unique_gauges,
        "total_rainfall_days_available_across_69_cells": total_rain_days,
        "candidate_unlabeled_background_cell_days": total_candidate_background,
        "note": "candidate_unlabeled_background_cell_days are grid-cell-days with real "
                "rainfall data but NO recorded INDOFLOODS event in that cell on that day. "
                "These are candidate UNLABELED/BACKGROUND days, never 'negative' days "
                "(Case B: true negatives are not establishable -- see FF_LABEL_READINESS.md).",
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
