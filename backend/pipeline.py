#!/usr/bin/env python3
"""
GFS Live Data Ingestion Pipeline
Fetches current GFS 0.25-degree GRIB2 data from NOAA NOMADS,
derives hazard probabilities from first-principles atmospheric indices,
and writes pan_india_grid.json consumed by the dashboard.

Run: python pipeline.py [--cycle 00|06|12|18] [--fhour 0|3|6]
Cron: 0 */6 * * * cd /path/to/project && python backend/pipeline.py
"""

import argparse
import json
import math
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG -- edit these to match your project layout
# ---------------------------------------------------------------------------
OUT_DIR = Path(__file__).parent.parent / "data"
OUT_FILE = OUT_DIR / "pan_india_grid.json"

GRID_STEP = 1.0          # degrees; matches existing dashboard grid
BOUNDS = {"S": 6, "N": 37, "W": 68, "E": 98}  # all-India coverage

# GFS variables we need (NOMADS filter parameter names)
GFS_VARS = [
    "var_CAPE",    # Convective Available Potential Energy  (J/kg)
    "var_CIN",     # Convective Inhibition                 (J/kg)
    "var_PWAT",    # Precipitable Water                    (kg/m2)
    "var_UGRD",    # U-wind component (used for shear)
    "var_VGRD",    # V-wind component (used for shear)
    "var_TMP",     # Temperature at pressure levels
    "var_DPT",     # Dew-point temperature
    "var_RH",      # Relative Humidity
    "var_HGT",     # Geopotential Height
]

# Pressure levels needed for index derivation
PRESSURE_LEVELS = [
    "lev_850_mb", "lev_700_mb", "lev_500_mb",
    "lev_400_mb", "lev_300_mb", "lev_250_mb",
    "lev_surface", "lev_2_m_above_ground",
]

# Nomads base URL template
NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def latest_gfs_cycle():
    """Return (date_str YYYYMMDD, cycle_str HH) for the most recent complete GFS run."""
    now = datetime.now(timezone.utc)
    # GFS runs at 00/06/12/18 UTC; each takes ~4h to complete and post
    cycles = [0, 6, 12, 18]
    for offset_h in range(0, 36, 6):
        candidate = now - timedelta(hours=offset_h)
        cycle_h = max(c for c in cycles if c <= candidate.hour)
        candidate_cycle = candidate.replace(hour=cycle_h, minute=0, second=0, microsecond=0)
        # assume available after 4h lag
        if (now - candidate_cycle).total_seconds() > 4 * 3600:
            return candidate_cycle.strftime("%Y%m%d"), f"{cycle_h:02d}"
    # fallback: yesterday 18Z
    yesterday = now - timedelta(days=1)
    return yesterday.strftime("%Y%m%d"), "18"


def build_nomads_url(date_str: str, cycle: str, fhour: int) -> str:
    fhour_str = f"f{fhour:03d}"
    params = [
        f"file=gfs.t{cycle}z.pgrb2.0p25.{fhour_str}",
        f"subregion=&leftlon={BOUNDS['W']}&rightlon={BOUNDS['E']}&toplat={BOUNDS['N']}&bottomlat={BOUNDS['S']}",
    ]
    for v in GFS_VARS:
        params.append(v + "=on")
    for lev in PRESSURE_LEVELS:
        params.append(lev + "=on")
    params.append(f"dir=%2Fgfs.{date_str}%2F{cycle}%2Fatmos")
    return NOMADS_BASE + "?" + "&".join(params)


def download_grib(url: str, dest: Path) -> bool:
    """Download GRIB2 file from NOMADS. Returns True on success."""
    print(f"  Downloading {url[:80]}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SIH-HyperLocalWarning/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            if resp.status != 200:
                print(f"  HTTP {resp.status} -- aborting")
                return False
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        size_kb = dest.stat().st_size // 1024
        print(f"  Downloaded {size_kb} KB")
        return True
    except Exception as e:
        print(f"  Download failed: {e}")
        return False


# ---------------------------------------------------------------------------
# INDEX DERIVATION FROM GRIB DATA
# ---------------------------------------------------------------------------

def read_grib_fields(grib_path: Path) -> dict:
    """
    Parse GRIB2 using cfgrib and return a dict of
    {short_name_leveltype_level: 2D numpy array}.
    Falls back to eccodes if cfgrib unavailable.
    """
    try:
        import cfgrib
        import numpy as np

        datasets = cfgrib.open_datasets(str(grib_path), indexing_time="valid_time")
        fields = {}
        for ds in datasets:
            for var in ds.data_vars:
                da = ds[var]
                # squeeze time dims
                arr = da.values
                while arr.ndim > 2:
                    arr = arr[0]
                # level info
                level_type = da.attrs.get("GRIB_typeOfLevel", "unknown")
                level_val = None
                if "level" in da.dims:
                    for lv in da.level.values:
                        slice_arr = da.sel(level=lv).values
                        while slice_arr.ndim > 2:
                            slice_arr = slice_arr[0]
                        key = f"{var}_{level_type}_{int(lv)}"
                        fields[key] = slice_arr
                else:
                    key = f"{var}_{level_type}"
                    fields[key] = arr
        return fields
    except ImportError:
        print("  cfgrib not found -- trying eccodes")
        return _read_grib_eccodes(grib_path)
    except Exception as e:
        print(f"  cfgrib parse error: {e}")
        return {}


def _read_grib_eccodes(grib_path: Path) -> dict:
    try:
        import eccodes
        import numpy as np
    except ImportError:
        print("  eccodes not found either -- cannot parse GRIB")
        return {}

    fields = {}
    with open(grib_path, "rb") as f:
        while True:
            msg = eccodes.codes_grib_new_from_file(f)
            if msg is None:
                break
            try:
                name = eccodes.codes_get(msg, "shortName")
                level_type = eccodes.codes_get(msg, "typeOfLevel")
                level = eccodes.codes_get(msg, "level")
                ni = eccodes.codes_get(msg, "Ni")
                nj = eccodes.codes_get(msg, "Nj")
                values = eccodes.codes_get_values(msg)
                arr = np.array(values).reshape(nj, ni)
                key = f"{name}_{level_type}_{level}"
                fields[key] = arr
            finally:
                eccodes.codes_release(msg)
    return fields


def lat_lon_to_idx(lats_1d, lons_1d, target_lat, target_lon):
    """Nearest-grid-point lookup."""
    import numpy as np
    lat_idx = int(np.argmin(np.abs(lats_1d - target_lat)))
    lon_idx = int(np.argmin(np.abs(lons_1d - target_lon)))
    return lat_idx, lon_idx


def extract_point(arr, lat_idx, lon_idx):
    """Safe point extraction from 2D array."""
    try:
        if arr is None:
            return None
        val = float(arr[lat_idx, lon_idx])
        return None if math.isnan(val) or abs(val) > 1e10 else val
    except Exception:
        return None


def compute_k_index(T850, Td850, T700, T500, Td700):
    """
    K-Index = (T850 - T500) + Td850 - (T700 - Td700)
    Inputs in Kelvin; returns K-Index in Kelvin-equivalent (same scale as Celsius differences).
    """
    if any(v is None for v in [T850, Td850, T700, T500, Td700]):
        return None
    return (T850 - T500) + Td850 - (T700 - Td700)


def compute_totals_totals(T850, Td850, T500):
    """Total Totals Index = (T850 + Td850) - 2*T500  (Kelvin)"""
    if any(v is None for v in [T850, Td850, T500]):
        return None
    return (T850 + Td850) - 2 * T500


def compute_wind_shear(u850, v850, u200, v200):
    """Bulk shear 850-200 hPa in m/s."""
    if any(v is None for v in [u850, v850, u200, v200]):
        return None
    du = u200 - u850
    dv = v200 - v850
    return math.sqrt(du * du + dv * dv)


def compute_ctt(fields, lats_1d, lons_1d, lat_idx, lon_idx):
    """
    Cloud Top Temperature proxy:
    Find the highest pressure level where RH > 80%.
    Return temperature at that level in Celsius.
    Returns None if no cloudy level found.
    """
    # Check levels from high (low P) to low (high P)
    for lev_hpa in [250, 300, 400, 500, 700]:
        rh_key = f"r_isobaricInhPa_{lev_hpa}"
        t_key = f"t_isobaricInhPa_{lev_hpa}"
        rh_arr = fields.get(rh_key)
        t_arr = fields.get(t_key)
        if rh_arr is None or t_arr is None:
            continue
        rh_val = extract_point(rh_arr, lat_idx, lon_idx)
        t_val = extract_point(t_arr, lat_idx, lon_idx)
        if rh_val is not None and rh_val >= 80.0 and t_val is not None:
            return t_val - 273.15  # Kelvin to Celsius
    return None


def hazard_probabilities(cape, cin, ki, tt, pwat, shear_ms, ctt_c):
    """
    Convert atmospheric indices to thunderstorm / cloudburst / flash flood probabilities
    using operationally-used threshold curves from WMO/IMD guidance.
    All outputs are in [0, 1].
    """
    # --- Thunderstorm ---
    ts_score = 0.0
    if cape is not None:
        # CAPE: 0 J/kg -> 0, 500 -> 0.3, 1500 -> 0.6, 3000+ -> 1.0
        ts_score += min(1.0, cape / 3000.0) * 0.35
    if ki is not None:
        # K-Index in K (equivalent to C differences): 20 -> low, 35 -> high
        ki_c = ki - 273.15 if ki > 200 else ki  # handle if returned in K
        ts_score += min(1.0, max(0.0, (ki_c - 20) / 20.0)) * 0.25
    if tt is not None:
        tt_c = tt - 273.15 if tt > 200 else tt
        ts_score += min(1.0, max(0.0, (tt_c - 44) / 12.0)) * 0.20
    if shear_ms is not None:
        ts_score += min(1.0, shear_ms / 30.0) * 0.15
    if cin is not None:
        # High CIN suppresses storms
        cin_penalty = min(0.15, abs(cin) / 1000.0 * 0.15)
        ts_score = max(0.0, ts_score - cin_penalty)
    ts_prob = min(1.0, ts_score)

    # --- Cloudburst ---
    cb_score = 0.0
    if pwat is not None:
        # Precipitable water: 30 mm -> low, 60 mm -> high
        cb_score += min(1.0, max(0.0, (pwat - 30) / 35.0)) * 0.45
    if cape is not None:
        cb_score += min(1.0, cape / 2500.0) * 0.30
    if ctt_c is not None:
        # Very cold cloud tops -> deep convection -> cloudburst risk
        # -30C or colder -> high; -10C -> low
        cb_score += min(1.0, max(0.0, (-ctt_c - 10) / 30.0)) * 0.25
    cb_prob = min(1.0, cb_score) * ts_prob  # CB only if storm

    # --- Flash Flood ---
    # Driven by CB intensity and PWAT
    ff_score = 0.0
    if pwat is not None:
        ff_score += min(1.0, max(0.0, (pwat - 35) / 30.0)) * 0.50
    if cape is not None:
        ff_score += min(1.0, cape / 2000.0) * 0.25
    if ctt_c is not None:
        ff_score += min(1.0, max(0.0, (-ctt_c - 5) / 45.0)) * 0.25
    ff_prob = min(1.0, ff_score) * min(1.0, ts_prob + 0.1)

    return ts_prob, cb_prob, ff_prob


# ---------------------------------------------------------------------------
# CTT GRID OUTPUT (separate thin file for CTT overlay)
# ---------------------------------------------------------------------------

def write_ctt_grid(cells: list, out_path: Path):
    """Write ctt_grid.json consumed by the CTT overlay in the dashboard."""
    ctt_cells = [
        {
            "lat": c["lat"],
            "lon": c["lon"],
            "ctt_c": c.get("ctt_c"),
        }
        for c in cells
        if c.get("ctt_c") is not None
    ]
    out_path.write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "description": "Cloud Top Temperature (CTT) proxy from GFS pressure-level RH + temperature",
        "unit": "degC",
        "ctt_cells": ctt_cells,
    }, indent=None))
    print(f"  CTT grid written: {len(ctt_cells)} cells -> {out_path}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def run(cycle_override: str = None, fhour: int = 0):
    import numpy as np

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    date_str, cycle = latest_gfs_cycle()
    if cycle_override:
        cycle = cycle_override
    print(f"GFS pipeline: {date_str} {cycle}Z f{fhour:03d}")

    url = build_nomads_url(date_str, cycle, fhour)

    with tempfile.TemporaryDirectory() as tmp:
        grib_path = Path(tmp) / "gfs_subset.grib2"
        if not download_grib(url, grib_path):
            print("FATAL: could not download GFS data -- aborting")
            sys.exit(1)

        print("  Parsing GRIB2 fields...")
        fields = read_grib_fields(grib_path)
        if not fields:
            print("FATAL: no fields parsed -- check cfgrib/eccodes installation")
            sys.exit(1)

        print(f"  Parsed {len(fields)} fields: {list(fields.keys())[:8]}...")

        # Build lat/lon arrays from field shape
        sample_arr = next(iter(fields.values()))
        nlat, nlon = sample_arr.shape
        lats_1d = np.linspace(BOUNDS["N"], BOUNDS["S"], nlat)
        lons_1d = np.linspace(BOUNDS["W"], BOUNDS["E"], nlon)

        # Helper: look up field by possible key names
        def field(*keys):
            for k in keys:
                if k in fields:
                    return fields[k]
            return None

        print("  Computing hazard indices for each grid cell...")
        cells = []
        lats = list(range(BOUNDS["S"], BOUNDS["N"] + 1, int(GRID_STEP)))
        lons = list(range(BOUNDS["W"], BOUNDS["E"] + 1, int(GRID_STEP)))

        for lat in lats:
            for lon in lons:
                li, lj = lat_lon_to_idx(lats_1d, lons_1d, lat, lon)

                # CAPE / CIN
                cape = extract_point(
                    field("cape_surface", "cape_convectivelyAvailablePotentialEnergy_surface",
                          "CAPE_surface"), li, lj)
                cin = extract_point(
                    field("cin_surface", "cin_convectiveInhibition_surface",
                          "CIN_surface"), li, lj)

                # Precipitable water
                pwat = extract_point(
                    field("pwat_atmosphereSingleLayer", "pwat_entireAtmosphere",
                          "PWAT_atmosphereSingleLayer"), li, lj)

                # Temperatures at key levels
                T850 = extract_point(field("t_isobaricInhPa_850"), li, lj)
                T700 = extract_point(field("t_isobaricInhPa_700"), li, lj)
                T500 = extract_point(field("t_isobaricInhPa_500"), li, lj)

                # Dew points
                Td850 = extract_point(field("d_isobaricInhPa_850", "dpt_isobaricInhPa_850"), li, lj)
                Td700 = extract_point(field("d_isobaricInhPa_700", "dpt_isobaricInhPa_700"), li, lj)

                # Wind components for shear
                u850 = extract_point(field("u_isobaricInhPa_850"), li, lj)
                v850 = extract_point(field("v_isobaricInhPa_850"), li, lj)
                u200 = extract_point(field("u_isobaricInhPa_200"), li, lj)
                v200 = extract_point(field("v_isobaricInhPa_200"), li, lj)

                ki = compute_k_index(T850, Td850, T700, T500, Td700)
                tt = compute_totals_totals(T850, Td850, T500)
                shear = compute_wind_shear(u850, v850, u200, v200)
                ctt_c = compute_ctt(fields, lats_1d, lons_1d, li, lj)

                ts_prob, cb_prob, ff_prob = hazard_probabilities(
                    cape, cin, ki, tt, pwat, shear, ctt_c)

                # Thunderstorm risk label
                if ts_prob >= 0.70:
                    risk_label = "SEVERE"
                elif ts_prob >= 0.45:
                    risk_label = "HIGH"
                elif ts_prob >= 0.25:
                    risk_label = "MODERATE"
                elif ts_prob >= 0.10:
                    risk_label = "LOW"
                else:
                    risk_label = "MINIMAL"

                cells.append({
                    "lat": lat,
                    "lon": lon,
                    "thunderstorm_probability": round(ts_prob, 4),
                    "cloudburst_probability": round(cb_prob, 4),
                    "flash_flood_probability": round(ff_prob, 4),
                    "cape": round(cape, 1) if cape is not None else None,
                    "cin": round(cin, 1) if cin is not None else None,
                    "k_index": round(ki - 273.15, 1) if ki is not None and ki > 200 else (round(ki, 1) if ki else None),
                    "totals_totals": round(tt - 273.15, 1) if tt is not None and tt > 200 else (round(tt, 1) if tt else None),
                    "wind_shear_ms": round(shear, 2) if shear is not None else None,
                    "pwat_mm": round(pwat, 1) if pwat is not None else None,
                    "ctt_c": round(ctt_c, 1) if ctt_c is not None else None,
                    "thunderstorm_risk": risk_label,
                })

        # Summary stats
        ts_vals = [c["thunderstorm_probability"] for c in cells]
        cb_vals = [c["cloudburst_probability"] for c in cells]
        ff_vals = [c["flash_flood_probability"] for c in cells]

        output = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "gfs_cycle": f"{date_str} {cycle}Z",
            "gfs_fhour": fhour,
            "grid_step_deg": GRID_STEP,
            "bounds": BOUNDS,
            "n_cells": len(cells),
            "summary": {
                "thunderstorm_probability": {
                    "min": round(min(ts_vals), 4),
                    "max": round(max(ts_vals), 4),
                    "mean": round(sum(ts_vals) / len(ts_vals), 4),
                },
                "cloudburst_probability": {
                    "min": round(min(cb_vals), 4),
                    "max": round(max(cb_vals), 4),
                    "mean": round(sum(cb_vals) / len(cb_vals), 4),
                },
                "flash_flood_probability": {
                    "min": round(min(ff_vals), 4),
                    "max": round(max(ff_vals), 4),
                    "mean": round(sum(ff_vals) / len(ff_vals), 4),
                },
            },
            "grid_cells": cells,
        }

        OUT_FILE.write_text(json.dumps(output, separators=(",", ":")))
        print(f"  Written {len(cells)} cells -> {OUT_FILE}")

        # Write separate CTT grid
        ctt_out = OUT_DIR / "ctt_grid.json"
        write_ctt_grid(cells, ctt_out)

    print("Pipeline complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GFS hazard pipeline")
    parser.add_argument("--cycle", choices=["00", "06", "12", "18"], default=None,
                        help="Override GFS cycle (default: auto-latest)")
    parser.add_argument("--fhour", type=int, default=0,
                        help="Forecast hour (0, 3, 6, ... default 0)")
    args = parser.parse_args()
    run(cycle_override=args.cycle, fhour=args.fhour)
