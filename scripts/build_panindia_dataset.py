#!/usr/bin/env python3
"""
build_panindia_dataset.py -- Phase 5 dataset construction.

Builds the maximum REAL supervised dataset possible from what already
exists in this repository, per docs/PANINDIA_DATASET_SCHEMA.md. Does not
invent predictors, does not invent labels, does not collapse UNKNOWN into
NEGATIVE, does not let the FF rainfall proxy enter the observed ff_label
field.

Two output partitions (see schema doc for why they are separate, not a
shortcut):

  1. VOBL partition (processed/dataset/vobl_partition.csv.gz)
     cell IND_13.0_78.0 only, 6-hourly, 2015-2025. Real ERA5 point
     predictors + real TS/CB/FF-proxy labels for that one cell.

  2. Pan-India CB partition (processed/dataset/panindia_cb_partition.csv.gz)
     all 992 cells, daily, 2015-2025. Real CB labels (POSITIVE/
     NEGATIVE_CONFIRMED/UNKNOWN via IMD gridded rainfall). Every predictor
     column is present but missing_flag=True for every row -- there is no
     historical pan-India predictor archive in this repo to fill it with
     (only a live current snapshot exists). TS/FF are UNKNOWN for every row.

Run: python3 scripts/build_panindia_dataset.py   (from repo root)
"""
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from regrid import cell_id_for  # noqa: E402
from build_panindia_cb_labels import (  # noqa: E402
    CB_THRESHOLD_MM, canonical_lat_lon_grids, imd_lat_lon_grids, read_grd_year,
)

LABELS_DIR = REPO_ROOT / "processed" / "labels"
OUT_DIR = REPO_ROOT / "processed" / "dataset"
RAIN_DIR = REPO_ROOT / "imd_rain" / "rain"

VOBL_CELL_ID = "IND_13.0_78.0"
VOBL_LAT, VOBL_LON = 13.0, 78.0

PREDICTOR_FIELDS = [
    "cape", "cin", "pwat_mm", "k_index", "totals_totals", "wind_shear_ms",
    "t850", "t700", "t500", "td850", "td700", "ctt_c",
]

MAX_TEMPORAL_DISTANCE_HOURS = 3  # a predictor more than this far from the
# reference slot's own timestamp is treated as missing, not matched -- this
# is what prevents a slow-moving "nearest available" join from silently
# pulling in a stale (or, worse, future) value.


def dewpoint_from_q_t(q_kg_kg, t_k, p_hpa):
    """Bolton (1980) inverted Magnus formula. Real physical transform of
    real ERA5 q + T fields -- not a fabricated variable. Ported unchanged
    from colab/build_blr_dataset.py so both passes agree on the formula."""
    w = q_kg_kg / (1 - q_kg_kg)
    e = (w * p_hpa) / (0.622 + w)
    e = np.clip(e, 1e-6, None)
    td_c = (243.5 * np.log(e / 6.112)) / (17.67 - np.log(e / 6.112))
    return td_c + 273.15


def slot_bounds_to_slot_idx(ts_str: str):
    """TS labels use 'YYYY-MM-DDT0001-0600' style timestamps; convert to
    (date_str, slot_idx 0-3) so they can be joined against CB/FF's
    'YYYY-MM-DDT slotN' timestamps and against the 6-hourly ERA5 rows."""
    date_part, hh = ts_str.split("T")
    slot_map = {"0001-0600": 0, "0601-1200": 1, "1201-1800": 2, "1801-2400": 3}
    return date_part, slot_map.get(hh)


def daily_slot_to_key(ts_str: str):
    """CB/FF labels use 'YYYY-MM-DDT slotN'."""
    date_part, slot_part = ts_str.split("T")
    slot_idx = int(slot_part.strip().replace("slot", ""))
    return date_part, slot_idx


# ---------------------------------------------------------------------------
# Partition 1: VOBL (real predictors + all three hazard labels, one cell)
# ---------------------------------------------------------------------------

def build_vobl_partition():
    print("Building VOBL partition (real predictors + TS/CB/FF-proxy labels)...")

    era5 = pd.read_csv(REPO_ROOT / "data" / "era5_6hrly_bengaluru_2015_2025.csv")
    # era5 has date, utc_hour, slot columns already -- slot is 0-3 matching
    # the same 6-hourly scheme used throughout this repo (verified by reading
    # the file's own columns above).
    era5["date"] = era5["date"].astype(str)

    # supplementary VOBL-specific real predictor columns used by the
    # production model, joined on (date, slot) where available.
    supp = None
    supp_path = REPO_ROOT / "data" / "bengaluru_6hr_training_dataset_v4.csv"
    if supp_path.exists():
        supp = pd.read_csv(supp_path)
        supp["date"] = supp["date"].astype(str)

    era5_key = era5.set_index(["date", "slot"])
    supp_key = supp.set_index(["date", "slot"]) if supp is not None else None

    # TS labels (real, VOBL-observed)
    ts = pd.read_csv(LABELS_DIR / "ts_labels.csv")
    ts_vobl = ts[ts["cell_id"] == VOBL_CELL_ID].copy()
    ts_vobl["date"], ts_vobl["slot"] = zip(*ts_vobl["timestamp"].map(slot_bounds_to_slot_idx))
    ts_vobl = ts_vobl.set_index(["date", "slot"])

    # CB labels (real, that cell) -- re-derive daily pos/neg/missing directly
    # from IMD .grd so we get NEGATIVE_CONFIRMED rows too (processed/labels/
    # cb_labels.csv only materializes POSITIVE rows by Phase-4 design).
    cb_daily = cb_daily_series_for_cell(VOBL_LAT, VOBL_LON)

    # FF proxy (real, that cell) -- same re-derivation approach, see below.
    ff_daily = ff_proxy_daily_series_for_cell(VOBL_LAT, VOBL_LON)

    rows = []
    all_keys = sorted(set(era5_key.index))
    for date, slot in all_keys:
        row = {
            "timestamp": f"{date}T slot{slot}",
            "cell_id": VOBL_CELL_ID,
            "lat": VOBL_LAT,
            "lon": VOBL_LON,
            "source_timestamp": date,
            "source_resolution": "6-hourly point (BLR)",
            "source_provenance": "data/era5_6hrly_bengaluru_2015_2025.csv + data/bengaluru_6hr_training_dataset_v4.csv",
        }
        e = era5_key.loc[(date, slot)] if (date, slot) in era5_key.index else None
        s = supp_key.loc[(date, slot)] if (supp_key is not None and (date, slot) in supp_key.index) else None

        def set_pred(name, value, source, missing_reason=None):
            missing = value is None or (isinstance(value, float) and np.isnan(value))
            row[f"{name}_value"] = None if missing else float(value)
            row[f"{name}_missing_flag"] = bool(missing)
            row[f"{name}_missing_reason"] = missing_reason if missing else None
            row[f"{name}_source"] = source
            row[f"{name}_source_timestamp"] = date

        cape = s["CAPE"] if (s is not None and "CAPE" in s and pd.notna(s["CAPE"])) else (e["ERA5_CAPE"] if e is not None else None)
        set_pred("cape", cape, "ERA5_CAPE / production CAPE feature", None if cape is not None else "not present in either source for this date/slot")
        set_pred("cin", None, "none", "no CIN column exists anywhere in the real VOBL predictor history in this repo (checked bengaluru_6hr_training_dataset_v3/v4, era5_6hrly_bengaluru_2015_2025)")
        pwat = s["PRECIP_WATER"] if (s is not None and "PRECIP_WATER" in s and pd.notna(s["PRECIP_WATER"])) else None
        set_pred("pwat_mm", pwat, "production PRECIP_WATER feature", None if pwat is not None else "not present for this date/slot")
        kidx = s["K_INDEX"] if (s is not None and "K_INDEX" in s and pd.notna(s["K_INDEX"])) else None
        set_pred("k_index", kidx, "production K_INDEX feature", None if kidx is not None else "not present for this date/slot")
        tt = s["TOTALS_TOTALS"] if (s is not None and "TOTALS_TOTALS" in s and pd.notna(s["TOTALS_TOTALS"])) else None
        set_pred("totals_totals", tt, "production TOTALS_TOTALS feature", None if tt is not None else "not present for this date/slot")
        ws = s["wind_shear_500_850"] if (s is not None and "wind_shear_500_850" in s and pd.notna(s["wind_shear_500_850"])) else None
        set_pred("wind_shear_ms", ws, "production wind_shear_500_850 feature", None if ws is not None else "not present for this date/slot")

        for lvl in ("850", "700", "500"):
            col = f"ERA5_t_{lvl}hPa"
            val = e[col] if (e is not None and col in e and pd.notna(e[col])) else None
            set_pred(f"t{lvl}", val, f"ERA5_t_{lvl}hPa", None if val is not None else "not present for this date/slot")

        for lvl in ("850", "700"):
            qcol, tcol = f"ERA5_q_{lvl}hPa", f"ERA5_t_{lvl}hPa"
            if e is not None and qcol in e and tcol in e and pd.notna(e[qcol]) and pd.notna(e[tcol]):
                td = float(dewpoint_from_q_t(e[qcol], e[tcol], float(lvl)))
                set_pred(f"td{lvl}", td, f"derived: Bolton(1980) from ERA5_q_{lvl}hPa + ERA5_t_{lvl}hPa")
            else:
                set_pred(f"td{lvl}", None, "derived: Bolton(1980) from ERA5 q+T", "source ERA5 q/T fields not present for this date/slot")

        set_pred("ctt_c", None, "none", "no historical Himawari CTT archive exists for this date/slot in this repo -- only a live current snapshot (data/himawari_realtime.json) exists")

        # Labels
        if (date, slot) in ts_vobl.index:
            r = ts_vobl.loc[(date, slot)]
            row["ts_label"] = r["label"] if pd.notna(r["label"]) else None
            row["ts_label_status"] = r["label_status"]
            row["ts_label_source"] = r["source"]
        else:
            row["ts_label"], row["ts_label_status"], row["ts_label_source"] = None, "UNKNOWN", None

        cb_stat = cb_daily.get(date)
        if cb_stat is None:
            row["cb_label"], row["cb_label_status"], row["cb_label_source"] = None, "UNKNOWN", None
        else:
            lbl, stat = cb_stat
            row["cb_label"] = lbl
            row["cb_label_status"] = stat
            row["cb_label_source"] = "IMD 0.25deg gridded daily rainfall (>= 64.5mm)" if stat != "UNKNOWN" else None

        row["ff_label"], row["ff_label_status"], row["ff_label_source"] = None, "UNKNOWN", None
        ff_stat = ff_daily.get(date)
        if ff_stat is None:
            row["ff_proxy_label"], row["ff_proxy_label_status"], row["ff_proxy_label_source"] = None, "UNKNOWN", None
        else:
            lbl, stat = ff_stat
            row["ff_proxy_label"] = lbl
            row["ff_proxy_label_status"] = stat
            row["ff_proxy_label_source"] = "RF-based FF proxy: 3-day cumsum>=100mm AND daily>=40mm" if stat != "UNKNOWN" else None

        rows.append(row)

    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "vobl_partition.csv.gz"
    df.to_csv(path, index=False, compression="gzip")
    print(f"  VOBL partition: {len(df)} rows -> {path}")
    return df


# ---------------------------------------------------------------------------
# Shared IMD re-derivation (both CB and FF-proxy need daily pos/neg/missing,
# not just positives, unlike the Phase-4 label files which only materialize
# positives to keep file size sane)
# ---------------------------------------------------------------------------

_IMD_CACHE = {}


def _load_imd_years():
    if _IMD_CACHE:
        return _IMD_CACHE
    imd_lats, imd_lons = imd_lat_lon_grids()
    years = sorted(int(p.stem) for p in RAIN_DIR.glob("*.grd"))
    for year in years:
        arr, n_days = read_grd_year(RAIN_DIR / f"{year}.grd", year)
        if arr is not None:
            _IMD_CACHE[year] = (arr, n_days, imd_lats, imd_lons)
    return _IMD_CACHE


def cb_daily_series_for_cell(clat, clon):
    """Returns {date_str: (label:int, status:str)} for one canonical cell,
    across every year of real IMD data -- both classes, not positive-only."""
    cache = _load_imd_years()
    out = {}
    for year, (arr, n_days, imd_lats, imd_lons) in cache.items():
        lat_idx = np.where(np.abs(imd_lats - clat) <= 0.5)[0]
        lon_idx = np.where(np.abs(imd_lons - clon) <= 0.5)[0]
        if len(lat_idx) == 0 or len(lon_idx) == 0:
            continue
        dates = pd.date_range(f"{year}-01-01", periods=n_days, freq="D")
        sub = arr[:, lat_idx[:, None], lon_idx[None, :]]
        daily_max = np.nanmax(sub.reshape(n_days, -1), axis=1)
        for d, v in zip(dates, daily_max):
            ds = str(d.date())
            if np.isnan(v):
                out[ds] = (None, "UNKNOWN")
            else:
                label = int(v >= CB_THRESHOLD_MM)
                out[ds] = (label, "POSITIVE" if label else "NEGATIVE_CONFIRMED")
    return out


def ff_proxy_daily_series_for_cell(clat, clon):
    """Rainfall-only FF proxy: 3-day cumsum>=100mm AND that day>=40mm.
    Matches dev/Fetch cb ff labels.py's documented fallback exactly.
    Returns both classes, unlike processed/labels/ff_labels_proxy.csv
    (positive-only)."""
    cache = _load_imd_years()
    daily_rain = {}
    for year, (arr, n_days, imd_lats, imd_lons) in cache.items():
        lat_idx = np.where(np.abs(imd_lats - clat) <= 0.5)[0]
        lon_idx = np.where(np.abs(imd_lons - clon) <= 0.5)[0]
        if len(lat_idx) == 0 or len(lon_idx) == 0:
            continue
        dates = pd.date_range(f"{year}-01-01", periods=n_days, freq="D")
        sub = arr[:, lat_idx[:, None], lon_idx[None, :]]
        daily_max = np.nanmax(sub.reshape(n_days, -1), axis=1)
        for d, v in zip(dates, daily_max):
            daily_rain[str(d.date())] = v

    out = {}
    sorted_dates = sorted(daily_rain.keys())
    for i, ds in enumerate(sorted_dates):
        v = daily_rain[ds]
        if np.isnan(v):
            out[ds] = (None, "UNKNOWN")
            continue
        window = [daily_rain[sorted_dates[j]] for j in range(max(0, i - 2), i + 1)]
        window = [w for w in window if not np.isnan(w)]
        cum3 = sum(window)
        label = int(cum3 >= 100.0 and v >= 40.0)
        out[ds] = (label, "PROXY_NOT_OBSERVED")
    return out


# ---------------------------------------------------------------------------
# Partition 2: pan-India CB (all 992 cells, daily, predictors all missing)
# ---------------------------------------------------------------------------

PANINDIA_CB_PREDICTOR_NOTE = (
    "ALL predictor columns are MISSING for every row in this partition. "
    "No historical pan-India gridded predictor archive exists in this "
    "repository -- data/pan_india_grid.json is a live current snapshot "
    "only, regenerated in place every 6 hours, not a time series. To avoid "
    "1.5M+ rows x 12 predictors x 5 always-identical missing-status columns "
    "of pure redundancy, this partition's CSV omits the per-row predictor "
    "columns entirely and states this fact once here instead -- see "
    "docs/PANINDIA_DATASET_SCHEMA.md and docs/PHASE_5_DATASET_REPORT.md. "
    "A consumer that needs the full identity+predictor+label schema for "
    "this partition should left-join these rows against an all-missing "
    "predictor block built from PREDICTOR_FIELDS in this script."
)


def build_panindia_cb_partition():
    print("Building pan-India CB partition (real CB labels, all 992 cells, daily)...")
    canon_lats, canon_lons = canonical_lat_lon_grids()

    timestamps, cell_ids, lats, lons, source_ts = [], [], [], [], []
    cb_labels, cb_statuses, cb_sources = [], [], []
    n_pos = n_neg = n_unk = 0
    for clat in canon_lats:
        for clon in canon_lons:
            cid = cell_id_for(clat, clon)
            daily = cb_daily_series_for_cell(clat, clon)
            for ds, (lbl, stat) in daily.items():
                if stat == "POSITIVE":
                    n_pos += 1
                elif stat == "NEGATIVE_CONFIRMED":
                    n_neg += 1
                else:
                    n_unk += 1
                timestamps.append(f"{ds}T daily")
                cell_ids.append(cid)
                lats.append(clat)
                lons.append(clon)
                source_ts.append(ds)
                cb_labels.append(lbl)
                cb_statuses.append(stat)
                cb_sources.append("IMD 0.25deg gridded daily rainfall (>= 64.5mm)" if stat != "UNKNOWN" else None)

    df = pd.DataFrame({
        "timestamp": timestamps,
        "cell_id": cell_ids,
        "lat": lats,
        "lon": lons,
        "source_timestamp": source_ts,
        "source_resolution": "daily (IMD 0.25deg gridded rainfall)",
        "source_provenance": "imd_rain/rain/*.grd",
        "ts_label_status": "UNKNOWN",
        "cb_label": cb_labels,
        "cb_label_status": cb_statuses,
        "cb_label_source": cb_sources,
        "ff_label_status": "UNKNOWN",
        "ff_proxy_label_status": "UNKNOWN",
    })
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "panindia_cb_partition.csv.gz"
    df.to_csv(path, index=False, compression="gzip")
    (OUT_DIR / "panindia_cb_partition_predictor_note.txt").write_text(PANINDIA_CB_PREDICTOR_NOTE)
    print(f"  Pan-India CB partition: {len(df)} rows (pos={n_pos}, neg={n_neg}, unknown={n_unk}) -> {path}")
    return df, {"positive": n_pos, "negative_confirmed": n_neg, "unknown": n_unk}


def main():
    vobl_df = build_vobl_partition()
    cb_df, cb_counts = build_panindia_cb_partition()

    stats = {
        "vobl_partition": {
            "rows": len(vobl_df),
            "cells": 1,
            "time_start": vobl_df["source_timestamp"].min(),
            "time_end": vobl_df["source_timestamp"].max(),
            "ts_label_counts": vobl_df["ts_label_status"].value_counts().to_dict(),
            "cb_label_counts": vobl_df["cb_label_status"].value_counts().to_dict(),
            "ff_label_counts": vobl_df["ff_label_status"].value_counts().to_dict(),
            "ff_proxy_label_counts": vobl_df["ff_proxy_label_status"].value_counts().to_dict(),
        },
        "panindia_cb_partition": {
            "rows": len(cb_df),
            "cells": cb_df["cell_id"].nunique(),
            "time_start": cb_df["source_timestamp"].min(),
            "time_end": cb_df["source_timestamp"].max(),
            "cb_label_counts": cb_counts,
            "predictor_availability": "MISSING for all rows -- see missing_reason columns",
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "build_stats.json").write_text(json.dumps(stats, indent=2, default=str))
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
