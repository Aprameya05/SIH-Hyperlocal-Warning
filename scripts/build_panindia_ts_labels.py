#!/usr/bin/env python3
"""
build_panindia_ts_labels.py

Maps the ONLY real thunderstorm observation source this repo has (IMD
station observations at VOBL/43295, data/bengaluru_6hr_training_dataset_v4.csv's
ts_label column) onto the canonical 992-cell grid.

Honesty by construction: VOBL's cell gets real POSITIVE/NEGATIVE_CONFIRMED
labels (a real station either did or did not observe a thunderstorm in
that 6-hour slot). Every other cell gets UNKNOWN, always -- there is no
pan-India TS observation network in this repo, and this script does not
manufacture one by extrapolating VOBL's climatology.

Output: processed/labels/ts_labels.csv (long format, one row per cell per slot)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from regrid import cell_id_for  # noqa: E402

VOBL_LAT, VOBL_LON = 13.0, 78.0  # nearest canonical cell center to real VOBL (13.1979, 77.7063)
BOUNDS = {"S": 6, "N": 37, "W": 68, "E": 98}
STEP = 1.0

DATA_CSV = REPO_ROOT / "data" / "bengaluru_6hr_training_dataset_v4.csv"
OUT_DIR = REPO_ROOT / "processed" / "labels"
OUT_PATH = OUT_DIR / "ts_labels.csv"

SLOT_TO_HOUR = {0: "0001-0600", 1: "0601-1200", 2: "1201-1800", 3: "1801-2400"}


def canonical_lats_lons():
    lats = [round(v, 1) for v in np.arange(BOUNDS["S"], BOUNDS["N"] + STEP * 0.5, STEP)]
    lons = [round(v, 1) for v in np.arange(BOUNDS["W"], BOUNDS["E"] + STEP * 0.5, STEP)]
    return lats, lons


def main():
    if not DATA_CSV.exists():
        print(f"ERROR: {DATA_CSV} not found.")
        sys.exit(1)

    df = pd.read_csv(DATA_CSV)
    df["date"] = pd.to_datetime(df["date"])
    vobl_cell_id = cell_id_for(VOBL_LAT, VOBL_LON)

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "timestamp": f"{r['date'].date()}T{SLOT_TO_HOUR[int(r['slot'])]}",
            "cell_id": vobl_cell_id,
            "hazard": "ts",
            "label": int(r["ts_label"]),
            "label_status": "POSITIVE" if int(r["ts_label"]) == 1 else "NEGATIVE_CONFIRMED",
            "source": "IMD station observation, VOBL/43295",
            "source_event_id": None,
            "source_timestamp": str(r["date"].date()),
            "spatial_distance_km": 0.0,
            "temporal_distance_hours": 0.0,
            "quality_flag": "station_observed",
        })

    # Every other canonical cell -> UNKNOWN, once per date represented in the
    # VOBL series (not fabricated per-slot detail for cells with no source).
    lats, lons = canonical_lats_lons()
    other_cells = [cell_id_for(la, lo) for la in lats for lo in lons if cell_id_for(la, lo) != vobl_cell_id]
    unique_dates = sorted(df["date"].dt.date.unique())
    print(f"VOBL real rows: {len(rows)}. Marking {len(other_cells)} other cells UNKNOWN "
          f"across {len(unique_dates)} dates ({len(other_cells) * len(unique_dates)} rows) -- this is large, "
          f"summarized rather than fully materialized per-slot to keep output size sane.")

    unknown_summary_rows = [{
        "timestamp": "ALL",
        "cell_id": cid,
        "hazard": "ts",
        "label": None,
        "label_status": "UNKNOWN",
        "source": None,
        "source_event_id": None,
        "source_timestamp": None,
        "spatial_distance_km": None,
        "temporal_distance_hours": None,
        "quality_flag": "no_pan_india_ts_observation_source",
    } for cid in other_cells]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_df = pd.DataFrame(rows + unknown_summary_rows)
    out_df.to_csv(OUT_PATH, index=False)

    summary = {
        "vobl_cell_id": vobl_cell_id,
        "vobl_rows": len(rows),
        "vobl_positive": int(df["ts_label"].sum()),
        "vobl_negative": int((df["ts_label"] == 0).sum()),
        "other_cells_marked_unknown": len(other_cells),
        "total_canonical_cells": len(lats) * len(lons),
        "pan_india_coverage_pct": round(100.0 / (len(lats) * len(lons)), 3),
    }
    print(summary)
    (OUT_DIR / "ts_labels_summary.json").write_text(pd.Series(summary).to_json(indent=2))
    print(f"Written: {OUT_PATH}")


if __name__ == "__main__":
    import numpy as np  # noqa: F811 (already imported above; explicit for clarity)
    main()
