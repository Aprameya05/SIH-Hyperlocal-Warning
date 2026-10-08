"""
scripts/imd_rainfall_adapter.py
==================================
Phase 26 Part B1 -- a reusable, real-observed-rainfall feature adapter
built on `imd_rain/rain/{year}.grd`: the standard IMD 0.25deg gridded
daily rainfall product (confirmed real: 135 lon x 129 lat x 4-byte
float32 records, 365/366 records per year, 2015-2025 -- exactly matches
the documented IMD .grd layout and the file sizes on disk).

This data was already being read correctly elsewhere in this repo
(scripts/build_panindia_ff_labels.py, scripts/phase20_label_status.py,
scripts/prepare_indofloods_rainfall_context.py all parse the identical
byte layout) for POSITIVE/CONFIRMED_NEGATIVE/MISSING flood-day labeling
and for event-anchored rainfall context -- but nothing in the repo had
turned it into a reusable rain_1d/_3d/_5d/_10d HISTORY-WINDOW feature
adapter usable for an ARBITRARY target date (not just the 10 Phase 20
label dates or specific flood events). This module is that adapter.

category = OBSERVED_RAINFALL for every value produced here (real IMD
rain-gauge-interpolated gridded observation, NOT a GFS forecast and NOT
satellite QPE). Never confused with FORECAST_RAINFALL (GFS tp/acpcp) or
OBSERVED_QPE (IMERG, still credential-blocked).

Temporal alignment (Part B4): rain_Nd for target_date uses ONLY days
[target_date - N, target_date - 1] -- strictly before the target date,
never including target_date itself or any later day. This is enforced
structurally (see build_rainfall_history_row()'s date arithmetic) and
checked in tests/test_phase26_ff_rainfall_pipeline.py's leakage tests.

Spatial alignment (Part B5): each canonical 992-cell point is matched to
the IMD 0.25deg grid cells within +/-0.5deg (half the canonical 1deg
step) via the SAME nearest-window method scripts/phase20_label_status.py
already uses, then max-aggregated (matching the existing CB-label
convention, scripts/build_panindia_cb_labels.py's threshold check) --
not invented here, reused for consistency across this codebase. A cell
with its window entirely outside the IMD coverage bbox (6.5-38.5N,
66.5-100E -- essentially coastal/boundary cells) gets no IMD data and is
reported MISSING, never filled from a neighbor.
"""
from __future__ import annotations

import sys
from pathlib import Path

import functools

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
RAIN_DIR = REPO_ROOT / "imd_rain" / "rain"
AVAILABLE_YEARS = sorted(int(p.stem) for p in RAIN_DIR.glob("*.grd")) if RAIN_DIR.exists() else []

CANON_BOUNDS = {"S": 6, "N": 37, "W": 68, "E": 98}
CANON_STEP = 1.0
RAINFALL_HISTORY_COLS = ["rain_1d", "rain_3d", "rain_5d", "rain_10d", "rain_max1d_5d",
                          "rain_recent_vs_antecedent_ratio", "rain_accel_3d_minus_prior3d"]


def imd_lat_lon_grids():
    lats = np.arange(IMD_LAT_START, IMD_LAT_END + IMD_STEP / 2, IMD_STEP)
    lons = np.arange(IMD_LON_START, IMD_LON_END + IMD_STEP / 2, IMD_STEP)
    return lats, lons


def canonical_cells() -> list[dict]:
    lats = [round(v, 1) for v in np.arange(CANON_BOUNDS["S"], CANON_BOUNDS["N"] + CANON_STEP * 0.5, CANON_STEP)]
    lons = [round(v, 1) for v in np.arange(CANON_BOUNDS["W"], CANON_BOUNDS["E"] + CANON_STEP * 0.5, CANON_STEP)]
    return [{"cell_id": cell_id_for(la, lo), "lat": la, "lon": lo} for la in lats for lo in lons]


def _year_has_data(year: int) -> bool:
    return year in AVAILABLE_YEARS


@functools.lru_cache(maxsize=64)
def read_grd_day(year: int, day_of_year_0based: int) -> np.ndarray:
    """Reads exactly one day's (IMD_N_LAT, IMD_N_LON) real observed
    rainfall slab, byte-identical to the method already used in
    scripts/phase20_label_status.py (not reimplemented differently).
    Returns NaN at the missing-data sentinel (<=-900, IMD convention)."""
    path = RAIN_DIR / f"{year}.grd"
    n_days = 366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365
    rec_bytes = IMD_N_LAT * IMD_N_LON * 4
    expected = n_days * rec_bytes
    size = path.stat().st_size
    header = 0 if size == expected else (4 if size == expected + 4 else None)
    if header is None:
        raise RuntimeError(f"{path.name}: unexpected size {size}, expected {expected} or {expected + 4}")
    with open(path, "rb") as f:
        f.seek(header + day_of_year_0based * rec_bytes)
        buf = f.read(rec_bytes)
    arr = np.frombuffer(buf, dtype=np.float32).reshape(IMD_N_LAT, IMD_N_LON).copy()
    arr[arr <= -900] = np.nan
    arr.setflags(write=False)  # cached by lru_cache -- callers must only read
    return arr


def _cell_window_idx(clat: float, clon: float, imd_lats, imd_lons):
    lat_idx = np.where(np.abs(imd_lats - clat) <= CANON_STEP / 2)[0]
    lon_idx = np.where(np.abs(imd_lons - clon) <= CANON_STEP / 2)[0]
    return lat_idx, lon_idx


def cell_daily_rainfall_mm(clat: float, clon: float, date: pd.Timestamp,
                            imd_lats=None, imd_lons=None) -> float | None:
    """Real observed rainfall (mm) for one canonical cell on one day, or
    None if outside IMD coverage, missing data, or the year isn't on
    disk -- never a fabricated value."""
    if not _year_has_data(date.year):
        return None
    if imd_lats is None or imd_lons is None:
        imd_lats, imd_lons = imd_lat_lon_grids()
    lat_idx, lon_idx = _cell_window_idx(clat, clon, imd_lats, imd_lons)
    if len(lat_idx) == 0 or len(lon_idx) == 0:
        return None  # outside IMD native coverage bbox entirely
    day_of_year_0based = (date - pd.Timestamp(f"{date.year}-01-01")).days
    day_arr = read_grd_day(date.year, day_of_year_0based)
    sub = day_arr[lat_idx[:, None], lon_idx[None, :]]
    if not np.any(~np.isnan(sub)):
        return None
    return float(np.nanmax(sub))  # same max-aggregation convention as build_panindia_cb_labels.py


def build_rainfall_history_row(cell_id: str, lat: float, lon: float, target_date: str) -> dict:
    """The core Part B1/B4 function: builds ONE row of the
    RAINFALL_HISTORY_COLS feature set for one cell/date, using ONLY
    days strictly BEFORE target_date (never target_date itself, never a
    future day -- no leakage). Any day with no real IMD value leaves the
    whole window's dependent features MISSING rather than treating the
    missing day as 0mm (0mm would be indistinguishable from a real dry
    day, which would silently bias every downstream FF feature low)."""
    target = pd.Timestamp(target_date)
    imd_lats, imd_lons = imd_lat_lon_grids()

    daily = {}
    for days_before in range(1, 11):  # 1 .. 10 days before target_date, exclusive of target_date
        d = target - pd.Timedelta(days=days_before)
        daily[days_before] = cell_daily_rainfall_mm(lat, lon, d, imd_lats, imd_lons)

    row = {"cell_id": cell_id, "target_date": target_date, "units": "mm",
           "source": "IMD gridded daily rainfall (imd_rain/rain/{year}.grd, 0.25deg)",
           "provenance_category": "OBSERVED_RAINFALL"}

    def _sum_or_none(days_before_list):
        vals = [daily[d] for d in days_before_list]
        if any(v is None for v in vals):
            return None
        return float(sum(vals))

    rain_1d = _sum_or_none([1])
    rain_3d = _sum_or_none([1, 2, 3])
    rain_5d = _sum_or_none([1, 2, 3, 4, 5])
    rain_10d = _sum_or_none(list(range(1, 11)))
    prior3d_vals = [daily[d] for d in (4, 5, 6)]
    prior3d = None if any(v is None for v in prior3d_vals) else float(sum(prior3d_vals))

    row["rain_1d"] = rain_1d
    row["rain_3d"] = rain_3d
    row["rain_5d"] = rain_5d
    row["rain_10d"] = rain_10d
    vals_1_5 = [daily[d] for d in range(1, 6)]
    row["rain_max1d_5d"] = None if any(v is None for v in vals_1_5) else float(max(vals_1_5))
    row["rain_recent_vs_antecedent_ratio"] = (
        None if (rain_3d is None or prior3d is None or prior3d == 0)
        else float(rain_3d / prior3d)
    )
    row["rain_accel_3d_minus_prior3d"] = (
        None if (rain_3d is None or prior3d is None) else float(rain_3d - prior3d)
    )
    row["n_missing_days_in_window"] = sum(1 for v in daily.values() if v is None)
    row["missingness"] = "COMPLETE" if row["n_missing_days_in_window"] == 0 else (
        "PARTIAL" if row["n_missing_days_in_window"] < 10 else "MISSING"
    )
    return row


def build_rainfall_history_table(target_date: str, cell_ids: list[str] | None = None) -> pd.DataFrame:
    """Builds the full-grid (or a subset's) rainfall-history table for
    one target_date. Never extrapolates spatially or temporally --
    every row's values come only from real IMD grid cells within that
    canonical cell's own footprint and only from days before target_date."""
    cells = canonical_cells()
    if cell_ids is not None:
        wanted = set(cell_ids)
        cells = [c for c in cells if c["cell_id"] in wanted]
    rows = [build_rainfall_history_row(c["cell_id"], c["lat"], c["lon"], target_date) for c in cells]
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import json
    df = build_rainfall_history_table("2024-08-01")
    print(f"n_cells: {len(df)}")
    print(df["missingness"].value_counts().to_dict())
    print(df[df["missingness"] == "COMPLETE"].head(3).to_string())
