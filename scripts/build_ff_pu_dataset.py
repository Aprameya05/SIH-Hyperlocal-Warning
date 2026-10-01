#!/usr/bin/env python3
"""
Phase 5.7 (research-only): build the PU training table for a flash-flood
trigger model.

READ-ONLY with respect to backend/pipeline.py, all production JSON,
index.html, and everything under processed/indofloods/. Writes only under
processed/ff_pu/.

Reuses the .grd-reading / regridding logic and the 2015-2025 IMD-overlap
constants verbatim from scripts/prepare_indofloods_rainfall_context.py and
scripts/compute_indofloods_imd_overlap.py (not reinvented).

Positives: unique (cell_id, Start Date) pairs from
processed/indofloods/indofloods_grid_events.csv restricted to events whose
Start Date falls in the IMD-covered window (2015-01-01 to 2025-12-31) --
label = 1.

Unlabeled/background: every other (cell, date) with real (non-masked) IMD
rainfall in the 69 event cells, 2015-01-01 to 2020-09-24 (the documented
common period; see docs/PHASE_5_6_HISTORICAL_DATA_DECISION.md Section 6).
label = None (never 0). We do NOT extend the background population to
2025 even though .grd files exist through 2025, because INDOFLOODS has no
event record past 2020-09-24 -- treating 2021-2025 cell-days as unlabeled
"absence of flood" would be uninterpretable (no gauge gave the dataset a
chance to record an event there in that period). This keeps the labeled
period and the background period on the same footing, which the PU
formulation (Section 6 of the assignment) requires.

label_status: "POSITIVE" or "UNLABELED"; label: 1 or null (never 0).
"""
import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
from prepare_indofloods_rainfall_context import (  # noqa: E402
    IMD_YEARS_AVAILABLE, EVENTS_CSV,
    imd_lat_lon_arrays, canonical_cell_to_imd_indices, read_grd_year,
)

OUT_DIR = os.path.join(REPO_ROOT, "processed", "ff_pu")
CATCHMENT_CSV = os.path.join(REPO_ROOT, "data", "catchment_characteristics_indofloods.csv")
MAPPING_CSV = os.path.join(REPO_ROOT, "processed", "indofloods", "indofloods_grid_mapping.csv")

COMMON_WINDOW_START = pd.Timestamp("2015-01-01")
COMMON_WINDOW_END = pd.Timestamp("2020-09-24")  # last mapped INDOFLOODS event date, per Phase 5.6

# Backward-looking rainfall windows. "date" in the label = flood Start
# Date, i.e. the day the flood was first recorded. In real-world issue-time
# terms, a nowcast/trigger model issued for day T would NOT yet have that
# day's full 24h rainfall total in hand at the moment a flash flood is
# actually developing (flash floods are sub-daily events; IMD's gridded
# product is a T+1 daily accumulation, not a same-day nowcast feed).
# Decision (documented here and in docs/FF_PU_FEATURE_CATALOG.md): rain_Xd
# features use STRICTLY PRIOR days [T-X, T-1] -- same-day (T) rainfall is
# EXCLUDED from all backward-sum features to avoid using a same-day total
# that would not be fully observed yet at flash-flood issue time. This is
# the conservative, leakage-safe choice; it is more conservative than the
# Phase 5.5 rainfall-context script (which included T0 in its window for
# a purely descriptive antecedent-context preview, not a predictive
# feature set).
RAINFALL_WINDOWS = {"rain_1d": 1, "rain_3d": 3, "rain_5d": 5, "rain_10d": 10}
MAX_WINDOW = 10

CATCHMENT_ALLOWLIST = [
    # drainage network / topology
    "Stream Order", "Drainage Density", "Drainage Texture", "Drainage Intensity",
    "Channel Frequency", "Infiltration Number", "No. of Firstorder Streams",
    "No. of Secondorder Streams", "No. of Thirdorder Streams",
    "Basin Magnitude", "Fitness Ratio", "Wandering Ratio",
    # morphometry / basin shape / relief
    "Maximal Flow Length", "Downvalley Length", "Drainage Area",
    "Catchment Relief", "Catchment Length", "Catchment Perimeter",
    "Sinuosity Index", "Form Factor", "Relief Ratio", "Elongation Ratio",
    "Circularity Ratio", "Lemniscates Value", "Compactness Coefficient",
    "Ruggedness Number",
    # soil / lithology / land cover
    "Soil type", "lithology type", "Land cover",
    # climate normals (static, long-run averages -- NOT dynamic weather)
    "Annual Precipitation", "Precipitation Seasonality",
    "Precipitation of Wettest Month", "Precipitation of Wettest Quarter",
    "Annual Mean Temperature", "Temperature Seasonality", "KoppenGeiger Climate Type",
]
# Explicitly excluded (exposure/impact, not hazard, per assignment):
CATCHMENT_EXCLUDED = [
    "2015_GDP_PPP", "2010_GDP_PPP", "2005_GDP_PPP", "2000_GDP_PPP", "1995_GDP_PPP", "1990_GDP_PPP",
    "2010_GDP_PC_PPP", "2015_GDP_PC_PPP", "2005_GDP_PC_PPP", "2000_GDP_PC_PPP", "1995_GDP_PC_PPP", "1990_GDP_PC_PPP",
    "Night Light", "Road Density", "Urban percentage",
    "2010_HDI", "2015_HDI", "2005_HDI", "2000_HDI", "1995_HDI", "1990_HDI",
    "Population Count", "Population Density",
]

RNG_SEED = 42
BACKGROUND_SAMPLE_PER_CELL_DAY_FRACTION = 1.0  # no subsampling needed -- see main()


def build_rain_lookup():
    imd_lats, imd_lons = imd_lat_lon_arrays()
    arr_by_year, dates_by_year = {}, {}
    for year in IMD_YEARS_AVAILABLE:
        arr, dates = read_grd_year(year)
        if arr is not None:
            arr_by_year[year] = arr
            dates_by_year[year] = dates
    return imd_lats, imd_lons, arr_by_year, dates_by_year


def cell_daily_series(cell_lat, cell_lon, imd_lats, imd_lons, arr_by_year, dates_by_year):
    lat_idx, lon_idx = canonical_cell_to_imd_indices(cell_lat, cell_lon, imd_lats, imd_lons)
    if len(lat_idx) == 0 or len(lon_idx) == 0:
        return pd.Series(dtype=float)
    parts = []
    for year, arr in arr_by_year.items():
        sub = arr[:, lat_idx[:, None], lon_idx[None, :]].reshape(arr.shape[0], -1).astype(float)
        sub[sub <= -998.0] = np.nan
        daily_mean = np.nanmean(sub, axis=1)
        parts.append(pd.Series(daily_mean, index=dates_by_year[year]))
    return pd.concat(parts).sort_index()


def rainfall_feature_frame(series):
    """Vectorized backward-looking, STRICTLY PRIOR-day features for every
    date in `series`'s reindexed daily calendar. Window [T-w, T-1] --
    excludes same-day (T) rainfall (see module docstring for rationale)."""
    full_idx = pd.date_range(series.index.min(), series.index.max(), freq="D")
    s = series.reindex(full_idx)  # NaN where IMD data is masked/missing
    shifted = s.shift(1)  # so any rolling window over `shifted` is [T-w, T-1]
    feats = pd.DataFrame(index=full_idx)
    for name, w in RAINFALL_WINDOWS.items():
        feats[name] = shifted.rolling(window=w, min_periods=1).sum()
    feats["rain_max1d_5d"] = shifted.rolling(window=5, min_periods=1).max()
    denom = feats["rain_10d"].replace(0, np.nan)
    feats["rain_recent_vs_antecedent_ratio"] = feats["rain_1d"] / denom
    prior3_6 = shifted.shift(3).rolling(window=3, min_periods=1).sum()
    feats["rain_accel_3d_minus_prior3d"] = feats["rain_3d"] - prior3_6
    return feats


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    events = pd.read_csv(EVENTS_CSV)
    events = events[events["mapping_status"] == "MAPPED"].copy()
    events["Start Date"] = pd.to_datetime(events["Start Date"])
    in_window = events[(events["Start Date"] >= COMMON_WINDOW_START) & (events["Start Date"] <= COMMON_WINDOW_END)].copy()

    positives = in_window[["cell_id", "Start Date", "GaugeID"]].drop_duplicates(subset=["cell_id", "Start Date"]).copy()
    positives = positives.rename(columns={"Start Date": "date"})
    n_positive_raw_rows_in_window = len(in_window)
    n_positive_dedup = len(positives)
    n_positive_cells = positives["cell_id"].nunique()
    n_positive_gauges = in_window["GaugeID"].nunique()

    print(f"Raw mapped event rows in {COMMON_WINDOW_START.date()}..{COMMON_WINDOW_END.date()}: {n_positive_raw_rows_in_window}")
    print(f"Deduplicated (cell,date) positives: {n_positive_dedup} (doc-stated: 620)")
    print(f"Unique positive cells: {n_positive_cells} (doc-stated: 69)")
    print(f"Unique positive gauges: {n_positive_gauges} (doc-stated: 131)")

    reconciliation = {
        "documented_count": 620,
        "recomputed_count": int(n_positive_dedup),
        "match": bool(n_positive_dedup == 620),
        "documented_cells": 69,
        "recomputed_cells": int(n_positive_cells),
        "documented_gauges": 131,
        "recomputed_gauges": int(n_positive_gauges),
        "method": "processed/indofloods/indofloods_grid_events.csv, mapping_status==MAPPED, "
                  "Start Date in [2015-01-01, 2020-09-24] (Phase 5.6's documented common window, "
                  "a slightly narrower cut than the full 2015-2025 IMD file coverage used for the "
                  "620/69/131 figures themselves -- see note below), deduplicated on (cell_id, Start Date).",
    }
    if n_positive_dedup != 620:
        reconciliation["discrepancy_reason"] = (
            "Phase 5.6's 620/69/131 figures were computed over the full 2015-2025 IMD file "
            "window (docs/PHASE_5_6_HISTORICAL_DATA_DECISION.md Section 7), not the narrower "
            "2015-01-01..2020-09-24 common-period cut used here for the actual training table "
            "(no INDOFLOODS event exists after 2020-09-24 in this dataset, so the two windows "
            "produce identical positive sets in practice -- any difference is not expected and is "
            "reported here, not silently forced.")

    event_cells = sorted(positives["cell_id"].unique())
    cell_coords = {cid: tuple(map(float, cid.split("_"))) for cid in event_cells}

    imd_lats, imd_lons, arr_by_year, dates_by_year = build_rain_lookup()

    # Build per-cell rainfall series once.
    series_by_cell = {}
    for cid, (clat, clon) in cell_coords.items():
        series_by_cell[cid] = cell_daily_series(clat, clon, imd_lats, imd_lons, arr_by_year, dates_by_year)

    positive_key = set(zip(positives["cell_id"], positives["date"]))

    # Background / unlabeled population: every real-rainfall (cell, date)
    # in the common window, for the 69 event cells, that is not a positive.
    rows = []
    total_background = 0
    for cid, series in series_by_cell.items():
        valid_dates = series.dropna().index
        valid_dates = valid_dates[(valid_dates >= COMMON_WINDOW_START) & (valid_dates <= COMMON_WINDOW_END)]
        total_background += len(valid_dates) - sum(1 for d in valid_dates if (cid, d) in positive_key)

    print(f"Candidate unlabeled/background cell-days in common window (69 cells, 2015-01-01..2020-09-24): {total_background}")
    print("(doc-stated 276,622 figure spans the fuller 2015-2025 IMD coverage across the same 69 cells; "
          "this run's window is narrower -- 2015-01-01..2020-09-24 -- so a smaller number is expected and correct.)")

    # Subsample the unlabeled population for computational tractability.
    # Method: stratified by cell (keep ALL dates within the common window
    # for every one of the 69 event cells is already tractable -- ~a few
    # hundred thousand rows is fine for pandas/xgboost on this machine --
    # so NO subsampling is applied; every real-rainfall, non-positive
    # (cell, date) in the 69 cells x common window is included. This keeps
    # the temporal and spatial structure fully intact (no bias risk from
    # subsampling to document).
    rng = np.random.RandomState(RNG_SEED)

    catchment = pd.read_csv(CATCHMENT_CSV)
    mapping = pd.read_csv(MAPPING_CSV)
    # cell -> GaugeID(s) -> catchment features. A cell can map to >1 gauge;
    # average numeric catchment features across gauges mapped to that cell
    # (documented simplification -- catchment features are gauge-native,
    # not cell-native, since the canonical grid is IMD/1-degree while
    # catchments are gauge-specific polygons).
    mapping_valid = mapping[(mapping["cell_id"].notna()) & (mapping["source_type"] == "gauge") & (mapping["status"] == "MAPPED")].copy()
    mapping_valid = mapping_valid.rename(columns={"source_id": "GaugeID"})
    cell_to_gauges = mapping_valid.groupby("cell_id")["GaugeID"].apply(list).to_dict()

    catchment_idx = catchment.set_index("GaugeID")
    numeric_allow = [c for c in CATCHMENT_ALLOWLIST if c in catchment_idx.columns]
    missing_allow = [c for c in CATCHMENT_ALLOWLIST if c not in catchment_idx.columns]
    if missing_allow:
        print(f"WARNING: allowlisted catchment columns not found in CSV: {missing_allow}")

    def catchment_features_for_cell(cid):
        gauges = cell_to_gauges.get(cid, [])
        gauges = [g for g in gauges if g in catchment_idx.index]
        if not gauges:
            return {c: np.nan for c in numeric_allow}
        sub = catchment_idx.loc[gauges, numeric_allow]
        out = {}
        for c in numeric_allow:
            col = sub[c]
            if not pd.api.types.is_numeric_dtype(col):
                # categorical (Land cover / Soil type / lithology type / KoppenGeiger): take mode
                out[c] = col.mode().iloc[0] if not col.mode().empty else np.nan
            else:
                out[c] = float(pd.to_numeric(col, errors="coerce").mean())
        return out

    catchment_cache = {cid: catchment_features_for_cell(cid) for cid in event_cells}
    rain_feat_cache = {cid: rainfall_feature_frame(series) for cid, series in series_by_cell.items()}
    rain_feat_cols = list(RAINFALL_WINDOWS.keys()) + ["rain_max1d_5d", "rain_recent_vs_antecedent_ratio", "rain_accel_3d_minus_prior3d"]

    gauge_by_cell_date = {(r["cell_id"], r["date"]): r["GaugeID"] for _, r in positives.iterrows()}

    def make_row(cid, date, label_status):
        clat, clon = cell_coords[cid]
        rf = rain_feat_cache[cid]
        rfeats = rf.loc[date, rain_feat_cols].to_dict() if date in rf.index else {c: np.nan for c in rain_feat_cols}
        row = {
            "cell_id": cid, "cell_lat": clat, "cell_lon": clon, "date": date.strftime("%Y-%m-%d"),
            "label_status": label_status, "label": 1 if label_status == "POSITIVE" else None,
            "GaugeID": gauge_by_cell_date.get((cid, date)) if label_status == "POSITIVE" else None,
        }
        row.update(rfeats)
        row.update(catchment_cache[cid])
        row["feature_provenance"] = "rainfall=imd_rain/rain/*.grd(T-1..T-10);catchment=data/catchment_characteristics_indofloods.csv"
        return row

    for _, r in positives.iterrows():
        rows.append(make_row(r["cell_id"], r["date"], "POSITIVE"))

    for cid, series in series_by_cell.items():
        valid_dates = series.dropna().index
        valid_dates = valid_dates[(valid_dates >= COMMON_WINDOW_START) & (valid_dates <= COMMON_WINDOW_END)]
        for d in valid_dates:
            if (cid, d) in positive_key:
                continue
            rows.append(make_row(cid, d, "UNLABELED"))

    df = pd.DataFrame(rows)
    df = df.sort_values(["date", "cell_id"]).reset_index(drop=True)

    out_csv = os.path.join(OUT_DIR, "ff_pu_training_table.csv")
    df.to_csv(out_csv, index=False)

    assert df.loc[df["label_status"] == "UNLABELED", "label"].isna().all(), "UNLABELED rows must have label=null"
    assert (df.loc[df["label_status"] == "POSITIVE", "label"] == 1).all(), "POSITIVE rows must have label=1"
    assert not (df["label"] == 0).any(), "label must never be 0 anywhere"

    summary = {
        "positive_reconciliation": reconciliation,
        "n_rows_total": int(len(df)),
        "n_positive": int((df["label_status"] == "POSITIVE").sum()),
        "n_unlabeled": int((df["label_status"] == "UNLABELED").sum()),
        "unlabeled_subsampled": False,
        "unlabeled_sampling_method": "none -- full population of real-rainfall, non-positive (cell,date) "
                                      "pairs across the 69 event cells in the common window is used; row "
                                      "count is computationally tractable without subsampling",
        "common_window_start": str(COMMON_WINDOW_START.date()),
        "common_window_end": str(COMMON_WINDOW_END.date()),
        "catchment_allowlist_used": numeric_allow,
        "catchment_allowlist_missing_from_source": missing_allow,
        "catchment_excluded_socioeconomic": CATCHMENT_EXCLUDED,
        "rainfall_feature_columns": list(RAINFALL_WINDOWS.keys()) + ["rain_max1d_5d", "rain_recent_vs_antecedent_ratio", "rain_accel_3d_minus_prior3d"],
        "date_semantics": "date = flood Start Date (day flood first recorded). Rainfall windows use "
                           "STRICTLY PRIOR days [T-w, T-1]; same-day (T) rainfall is excluded from all "
                           "rain_Xd features because it would not be fully observed at flash-flood "
                           "issue time (IMD gridded product is a T+1 daily accumulation).",
        "seed": RNG_SEED,
    }
    with open(os.path.join(OUT_DIR, "dataset_build_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {out_csv} ({len(df)} rows)")


if __name__ == "__main__":
    main()
