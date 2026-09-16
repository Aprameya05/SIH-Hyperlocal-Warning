"""
fetch_gfs_realtime.py
=====================
Real-time GFS data fetcher for CSIR Thunderstorm Nowcasting System.
Bengaluru Airport (Station 43295) — lat=12.97, lon=77.58

Based on Atul's pipeline scoping (t+12 approach):
  Slot 0 (0001-0600 IST): GFS prev-day 06Z, f012
  Slot 1 (0601-1200 IST): GFS prev-day 12Z, f012
  Slot 2 (1201-1800 IST): GFS prev-day 18Z, f012
  Slot 3 (1801-2400 IST): GFS same-day  00Z, f012

Usage:
  python fetch_gfs_realtime.py              # fetch all 4 slots for today
  python fetch_gfs_realtime.py --slot 2     # fetch only slot 2
  python fetch_gfs_realtime.py --date 2026-07-15

Output: data/gfs_realtime/gfs_YYYY-MM-DD.csv

Author: Aprameya + Atul, CSIR Thunderstorm Project
"""

import requests
import xarray as xr
import cfgrib
import pandas as pd
import numpy as np
import argparse
import tempfile
import os
import time
import warnings
from pathlib import Path
from datetime import datetime, timedelta, timezone

warnings.filterwarnings('ignore')

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE    = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
OUT_DIR = BASE / "data" / "gfs_realtime"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── LOCATION ──────────────────────────────────────────────────────────────────
LAT = 12.97
LON = 77.58

# ── SLOT CONFIG (Atul's t+12 table) ───────────────────────────────────────────
SLOT_GFS_CONFIG = {
    0: {"cycle_hour": 6,  "fhour": 12, "cycle_offset_days": -1},
    1: {"cycle_hour": 12, "fhour": 12, "cycle_offset_days": -1},
    2: {"cycle_hour": 18, "fhour": 12, "cycle_offset_days": -1},
    3: {"cycle_hour": 0,  "fhour": 12, "cycle_offset_days":  0},
}

SLOT_NAMES = {
    0: "0001-0600 IST",
    1: "0601-1200 IST",
    2: "1201-1800 IST",
    3: "1801-2400 IST",
}

# ── BUILD NOMADS URL ───────────────────────────────────────────────────────────
def build_nomads_url(cycle_date: datetime, cycle_hour: int, fhour: int) -> str:
    date_str  = cycle_date.strftime("%Y%m%d")
    cyc       = f"{cycle_hour:02d}"
    fhr       = f"f{fhour:03d}"

    url = (
        f"https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
        f"?dir=%2Fgfs.{date_str}%2F{cyc}%2Fatmos"
        f"&file=gfs.t{cyc}z.pgrb2.0p25.{fhr}"
        # Surface / near-surface variables
        f"&var_TMP=on"
        f"&var_DPT=on"
        f"&var_UGRD=on"
        f"&var_VGRD=on"
        f"&var_CAPE=on"
        f"&var_PRES=on"
        f"&var_SPFH=on"
        # Levels
        f"&lev_2_m_above_ground=on"
        f"&lev_10_m_above_ground=on"
        f"&lev_surface=on"
        f"&lev_500_mb=on"
        f"&lev_700_mb=on"
        f"&lev_850_mb=on"
        # Subregion around Bengaluru
        f"&subregion="
        f"&toplat=13.25"
        f"&leftlon=77.25"
        f"&rightlon=77.75"
        f"&bottomlat=12.75"
    )
    return url

# ── FETCH GRIB FILE ────────────────────────────────────────────────────────────
def fetch_grib(url: str, max_retries: int = 3) -> str:
    for attempt in range(1, max_retries + 1):
        try:
            print(f"    Downloading (attempt {attempt}/{max_retries})...")
            r = requests.get(url, timeout=120, stream=True)
            if r.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
                size_kb = os.path.getsize(f.name) / 1024
                print(f"    Downloaded: {size_kb:.0f} KB")
                if size_kb < 1:
                    # Too small — probably an HTML error page, not GRIB
                    with open(f.name, 'rb') as fcheck:
                        content = fcheck.read(200)
                    os.unlink(f.name)
                    raise RuntimeError(
                        f"File too small ({size_kb:.1f} KB) — NOMADS returned an error.\n"
                        f"First 200 bytes: {content}"
                    )
                return f.name
            else:
                print(f"    HTTP {r.status_code} — retrying in 30s...")
                time.sleep(30)
        except RuntimeError:
            raise
        except Exception as e:
            print(f"    Error: {e} — retrying in 30s...")
            time.sleep(30)
    raise RuntimeError(f"Failed after {max_retries} attempts.")

# ── PARSE GRIB → BENGALURU POINT ──────────────────────────────────────────────
def parse_grib(grib_path: str) -> dict:
    row = {}

    # Open all messages in the file
    try:
        datasets = cfgrib.open_datasets(grib_path, errors='ignore')
    except Exception as e:
        raise RuntimeError(f"cfgrib failed to open file: {e}")

    def get_nearest(ds, varname, level=None):
        try:
            if level is not None:
                ds = ds.sel(isobaricInhPa=level)
            pt = ds.sel(latitude=LAT, longitude=LON, method="nearest")
            return float(pt[varname].values)
        except Exception:
            return np.nan

    for ds in datasets:
        dims = list(ds.dims)
        coords = list(ds.coords)
        vars_in = list(ds.data_vars)

        # Surface / near-surface
        if "isobaricInhPa" not in dims:
            for v in vars_in:
                vl = v.lower()
                if vl in ("t2m", "tmp") and "ERA5_T2M" not in row:
                    val = get_nearest(ds, v)
                    if not np.isnan(val):
                        row["ERA5_T2M"] = val

                elif vl in ("d2m", "dpt") and "ERA5_D2M" not in row:
                    val = get_nearest(ds, v)
                    if not np.isnan(val):
                        row["ERA5_D2M"] = val

                elif vl in ("u10", "ugrd") and "ERA5_U10" not in row:
                    val = get_nearest(ds, v)
                    if not np.isnan(val):
                        row["ERA5_U10"] = val

                elif vl in ("v10", "vgrd") and "ERA5_V10" not in row:
                    val = get_nearest(ds, v)
                    if not np.isnan(val):
                        row["ERA5_V10"] = val

                elif vl in ("cape",) and "ERA5_CAPE" not in row:
                    val = get_nearest(ds, v)
                    if not np.isnan(val):
                        row["ERA5_CAPE"] = val

                elif vl in ("sp", "pres", "prmsl") and "ERA5_SP" not in row:
                    val = get_nearest(ds, v)
                    if not np.isnan(val):
                        row["ERA5_SP"] = val

        # Pressure levels
        elif "isobaricInhPa" in dims:
            for lev in [500, 700, 850]:
                for v in vars_in:
                    vl = v.lower()
                    key_t = f"ERA5_t_{lev}hPa"
                    key_q = f"ERA5_q_{lev}hPa"
                    key_u = f"ERA5_u_{lev}hPa"
                    key_v = f"ERA5_v_{lev}hPa"

                    if vl in ("t", "tmp") and key_t not in row:
                        val = get_nearest(ds, v, lev)
                        if not np.isnan(val):
                            row[key_t] = val

                    elif vl in ("q", "spfh") and key_q not in row:
                        val = get_nearest(ds, v, lev)
                        if not np.isnan(val):
                            row[key_q] = val

                    elif vl in ("u", "ugrd") and key_u not in row:
                        val = get_nearest(ds, v, lev)
                        if not np.isnan(val):
                            row[key_u] = val

                    elif vl in ("v", "vgrd") and key_v not in row:
                        val = get_nearest(ds, v, lev)
                        if not np.isnan(val):
                            row[key_v] = val

    return row

# ── FETCH ONE SLOT ─────────────────────────────────────────────────────────────
def fetch_slot(op_date: datetime, slot_id: int) -> dict:
    cfg        = SLOT_GFS_CONFIG[slot_id]
    cycle_date = op_date + timedelta(days=cfg["cycle_offset_days"])
    cycle_hour = cfg["cycle_hour"]
    fhour      = cfg["fhour"]

    print(f"\n  Slot {slot_id} ({SLOT_NAMES[slot_id]})")
    print(f"  GFS: {cycle_date.strftime('%Y-%m-%d')} {cycle_hour:02d}Z  f{fhour:03d}")

    url       = build_nomads_url(cycle_date, cycle_hour, fhour)
    grib_path = fetch_grib(url)
    row       = parse_grib(grib_path)
    os.unlink(grib_path)

    row["date"]       = op_date.strftime("%Y-%m-%d")
    row["slot"]       = slot_id
    row["slot_label"] = SLOT_NAMES[slot_id]
    row["gfs_cycle"]  = f"{cycle_date.strftime('%Y-%m-%d')} {cycle_hour:02d}Z f{fhour:03d}"

    expected = [
        "ERA5_T2M","ERA5_D2M","ERA5_U10","ERA5_V10","ERA5_CAPE","ERA5_SP",
        "ERA5_t_500hPa","ERA5_t_700hPa","ERA5_t_850hPa",
        "ERA5_q_500hPa","ERA5_q_700hPa","ERA5_q_850hPa",
        "ERA5_u_500hPa","ERA5_u_700hPa","ERA5_u_850hPa",
        "ERA5_v_500hPa","ERA5_v_700hPa","ERA5_v_850hPa",
    ]
    missing = [v for v in expected if v not in row or pd.isna(row.get(v))]

    if missing:
        print(f"  ⚠ Still missing: {missing}")
    else:
        print(f"  ✓ All 18 variables extracted")

    # Print sample values
    for key in ["ERA5_T2M","ERA5_CAPE","ERA5_t_850hPa","ERA5_q_850hPa"]:
        val = row.get(key, "missing")
        if isinstance(val, float):
            print(f"  {key}: {val:.4f}")
        else:
            print(f"  {key}: {val}")

    return row

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str)
    parser.add_argument("--slot", type=int, choices=[0,1,2,3])
    args = parser.parse_args()

    if args.date:
        op_date = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        op_date = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        op_date = op_date.replace(tzinfo=None)

    print("=" * 60)
    print("GFS Real-Time Fetcher — CSIR Thunderstorm Nowcast")
    print("=" * 60)
    print(f"Operational date : {op_date.strftime('%Y-%m-%d')} IST")

    slots_to_fetch = [args.slot] if args.slot is not None else [0,1,2,3]
    rows = []

    for slot_id in slots_to_fetch:
        try:
            row = fetch_slot(op_date, slot_id)
            rows.append(row)
        except Exception as e:
            print(f"  ✗ Slot {slot_id} failed: {e}")
            rows.append({
                "date": op_date.strftime("%Y-%m-%d"),
                "slot": slot_id,
                "slot_label": SLOT_NAMES[slot_id],
                "error": str(e),
            })

    out_file = OUT_DIR / f"gfs_{op_date.strftime('%Y-%m-%d')}.csv"
    df = pd.DataFrame(rows)
    df.to_csv(out_file, index=False)

    print(f"\n{'='*60}")
    print(f"Saved → {out_file}")
    print(f"\nFull output:")
    print(df.to_string())

if __name__ == "__main__":
    main()