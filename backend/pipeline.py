#!/usr/bin/env python3
"""
GFS Live Data Ingestion Pipeline
Fetches current GFS 0.25-degree GRIB2 data from NOAA NOMADS,
derives hazard probabilities from first-principles atmospheric indices,
and writes pan_india_grid.json consumed by the dashboard.

New in this version:
  - CTT drop rate: compares current CTT against previous run to compute
    rate of cloud-top cooling (C/hour). Rapid cooling signals explosive
    convective development.
  - Low-level convergence: computes horizontal divergence from 850 hPa U/V
    wind fields. Negative divergence (convergence) forces air upward and is
    a primary trigger for convective initiation.
  - QPE proxy: uses GFS APCP (6-hour accumulated precipitation, mm) as a
    Quantitative Precipitation Estimate. Active precipitation reinforces
    cloudburst and flash flood probability.

Run: python pipeline.py [--cycle 00|06|12|18] [--fhour 0|3|6]
GitHub Actions: runs automatically via .github/workflows/update_grid.yml
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
# CONFIG
# ---------------------------------------------------------------------------
OUT_DIR  = Path(__file__).parent.parent / "data"
OUT_FILE = OUT_DIR / "pan_india_grid.json"

GRID_STEP = 0.25         # degrees; 0.25 = GFS native resolution (~27 km)
BOUNDS    = {"S": 6, "N": 37, "W": 68, "E": 98}

# GFS variables requested from NOMADS filter
GFS_VARS = [
    "var_CAPE",    # Convective Available Potential Energy  (J/kg)
    "var_CIN",     # Convective Inhibition                 (J/kg)
    "var_PWAT",    # Precipitable Water                    (kg/m2)
    "var_UGRD",    # U-wind component (shear + convergence)
    "var_VGRD",    # V-wind component (shear + convergence)
    "var_TMP",     # Temperature at pressure levels
    "var_DPT",     # Dew-point temperature
    "var_RH",      # Relative Humidity
    "var_HGT",     # Geopotential Height
    "var_APCP",    # Accumulated Precipitation (QPE proxy)
]

PRESSURE_LEVELS = [
    "lev_850_mb", "lev_700_mb", "lev_500_mb",
    "lev_400_mb", "lev_300_mb", "lev_250_mb", "lev_200_mb",
    "lev_surface", "lev_2_m_above_ground",
]

NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"

# CTT drop rate: how old can the previous CTT file be before we skip the delta?
CTT_PREV_MAX_AGE_HOURS = 7.0


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def latest_gfs_cycle():
    """Return (date_str YYYYMMDD, cycle_str HH) for the most recent complete GFS run."""
    now = datetime.now(timezone.utc)
    cycles = [0, 6, 12, 18]
    for offset_h in range(0, 36, 6):
        candidate = now - timedelta(hours=offset_h)
        cycle_h = max(c for c in cycles if c <= candidate.hour)
        candidate_cycle = candidate.replace(hour=cycle_h, minute=0, second=0, microsecond=0)
        if (now - candidate_cycle).total_seconds() > 4 * 3600:
            return candidate_cycle.strftime("%Y%m%d"), f"{cycle_h:02d}"
    yesterday = now - timedelta(days=1)
    return yesterday.strftime("%Y%m%d"), "18"


def build_nomads_url(date_str: str, cycle: str, fhour: int) -> str:
    fhour_str = f"f{fhour:03d}"
    params = [
        f"file=gfs.t{cycle}z.pgrb2.0p25.{fhour_str}",
        f"subregion=&leftlon={BOUNDS['W']}&rightlon={BOUNDS['E']}"
        f"&toplat={BOUNDS['N']}&bottomlat={BOUNDS['S']}",
    ]
    for v in GFS_VARS:
        params.append(v + "=on")
    for lev in PRESSURE_LEVELS:
        params.append(lev + "=on")
    params.append(f"dir=%2Fgfs.{date_str}%2F{cycle}%2Fatmos")
    return NOMADS_BASE + "?" + "&".join(params)


def download_grib(url: str, dest: Path) -> bool:
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
# CTT DROP RATE: load previous CTT grid for temporal delta
# ---------------------------------------------------------------------------

def load_prev_ctt_map(ctt_path: Path) -> tuple:
    """
    Read ctt_grid.json from the previous run.
    Returns ({(lat, lon): ctt_c}, generated_at_utc_str) or ({}, None) on failure.
    """
    if not ctt_path.exists():
        return {}, None
    try:
        data = json.loads(ctt_path.read_text())
        prev_map = {}
        for cell in data.get("ctt_cells", []):
            if cell.get("ctt_c") is not None:
                prev_map[(cell["lat"], cell["lon"])] = cell["ctt_c"]
        return prev_map, data.get("generated_at_utc")
    except Exception as e:
        print(f"  Could not load previous CTT grid: {e}")
        return {}, None


def ctt_hours_elapsed(prev_ts_str: str) -> float:
    """Return hours between prev_ts_str (ISO) and now. Returns large number on parse error."""
    if not prev_ts_str:
        return 999.0
    try:
        prev_dt = datetime.fromisoformat(prev_ts_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - prev_dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


# ---------------------------------------------------------------------------
# LOW-LEVEL CONVERGENCE: horizontal divergence of 850 hPa wind
# ---------------------------------------------------------------------------

def compute_convergence_grid(u850_arr, v850_arr, lats_1d, lons_1d):
    """
    Compute horizontal divergence of (U850, V850) on the full grid using
    finite differences. Convergence = -divergence.

    Returns a 2D numpy array of convergence values (s^-1), same shape as input.
    Positive values indicate convergence (inflow), which forces upward motion.
    """
    import numpy as np

    if u850_arr is None or v850_arr is None:
        return None

    # Earth radius in metres
    R_EARTH = 6.371e6

    nlat, nlon = u850_arr.shape
    conv = np.zeros((nlat, nlon), dtype=np.float32)

    # Convert lat/lon to radians for metric spacing
    lats_rad = np.deg2rad(lats_1d)

    for j in range(1, nlat - 1):
        # Meridional (north-south) spacing in metres
        dlat_m = R_EARTH * abs(float(lats_rad[j + 1] - lats_rad[j - 1]))

        # Zonal (east-west) spacing in metres -- depends on latitude
        cos_lat = math.cos(float(lats_rad[j]))
        if abs(cos_lat) < 1e-6:
            cos_lat = 1e-6
        dlon_deg = abs(float(lons_1d[2] - lons_1d[0])) if nlon > 2 else GRID_STEP * 2
        dlon_m = R_EARTH * cos_lat * math.radians(dlon_deg)

        for i in range(1, nlon - 1):
            # dU/dx (zonal divergence)
            du_dx = (float(u850_arr[j, i + 1]) - float(u850_arr[j, i - 1])) / dlon_m
            # dV/dy (meridional divergence)
            dv_dy = (float(v850_arr[j + 1, i]) - float(v850_arr[j - 1, i])) / dlat_m
            # Convergence = -(du_dx + dv_dy)
            conv[j, i] = -(du_dx + dv_dy)

    # Fill borders with nearest interior value
    conv[0, :]  = conv[1, :]
    conv[-1, :] = conv[-2, :]
    conv[:, 0]  = conv[:, 1]
    conv[:, -1] = conv[:, -2]

    return conv


# ---------------------------------------------------------------------------
# INDEX DERIVATION FROM GRIB DATA
# ---------------------------------------------------------------------------

def read_grib_fields(grib_path: Path) -> dict:
    try:
        import cfgrib
        import numpy as np

        datasets = cfgrib.open_datasets(str(grib_path), indexing_time="valid_time")
        fields = {}
        for ds in datasets:
            for var in ds.data_vars:
                da = ds[var]
                arr = da.values
                while arr.ndim > 2:
                    arr = arr[0]
                level_type = da.attrs.get("GRIB_typeOfLevel", "unknown")
                if "level" in da.dims:
                    for lv in da.level.values:
                        slice_arr = da.sel(level=lv).values
                        while slice_arr.ndim > 2:
                            slice_arr = slice_arr[0]
                        fields[f"{var}_{level_type}_{int(lv)}"] = slice_arr
                else:
                    fields[f"{var}_{level_type}"] = arr
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
                name       = eccodes.codes_get(msg, "shortName")
                level_type = eccodes.codes_get(msg, "typeOfLevel")
                level      = eccodes.codes_get(msg, "level")
                ni         = eccodes.codes_get(msg, "Ni")
                nj         = eccodes.codes_get(msg, "Nj")
                values     = eccodes.codes_get_values(msg)
                arr = np.array(values).reshape(nj, ni)
                fields[f"{name}_{level_type}_{level}"] = arr
            finally:
                eccodes.codes_release(msg)
    return fields


def lat_lon_to_idx(lats_1d, lons_1d, target_lat, target_lon):
    import numpy as np
    lat_idx = int(np.argmin(np.abs(lats_1d - target_lat)))
    lon_idx = int(np.argmin(np.abs(lons_1d - target_lon)))
    return lat_idx, lon_idx


def extract_point(arr, lat_idx, lon_idx):
    try:
        if arr is None:
            return None
        val = float(arr[lat_idx, lon_idx])
        return None if math.isnan(val) or abs(val) > 1e10 else val
    except Exception:
        return None


def compute_k_index(T850, Td850, T700, T500, Td700):
    """K-Index = (T850 - T500) + Td850 - (T700 - Td700). Inputs in Kelvin."""
    if any(v is None for v in [T850, Td850, T700, T500, Td700]):
        return None
    return (T850 - T500) + Td850 - (T700 - Td700)


def compute_totals_totals(T850, Td850, T500):
    """Total Totals = (T850 + Td850) - 2*T500. Inputs in Kelvin."""
    if any(v is None for v in [T850, Td850, T500]):
        return None
    return (T850 + Td850) - 2 * T500


def compute_wind_shear(u850, v850, u200, v200):
    """Bulk shear 850-200 hPa in m/s."""
    if any(v is None for v in [u850, v850, u200, v200]):
        return None
    return math.sqrt((u200 - u850) ** 2 + (v200 - v850) ** 2)


def compute_ctt(fields, lats_1d, lons_1d, lat_idx, lon_idx):
    """
    Cloud Top Temperature proxy: highest pressure level where RH > 80%.
    Returns temperature in Celsius at that level, or None.
    """
    for lev_hpa in [250, 300, 400, 500, 700]:
        rh_arr = fields.get(f"r_isobaricInhPa_{lev_hpa}")
        t_arr  = fields.get(f"t_isobaricInhPa_{lev_hpa}")
        if rh_arr is None or t_arr is None:
            continue
        rh_val = extract_point(rh_arr, lat_idx, lon_idx)
        t_val  = extract_point(t_arr,  lat_idx, lon_idx)
        if rh_val is not None and rh_val >= 80.0 and t_val is not None:
            return t_val - 273.15
    return None


# ---------------------------------------------------------------------------
# HAZARD PROBABILITIES
# ---------------------------------------------------------------------------

def hazard_probabilities(cape, cin, ki, tt, pwat, shear_ms, ctt_c,
                          convergence=None, ctt_drop_rate=None, qpe_mm=None):
    """
    Convert atmospheric indices to thunderstorm / cloudburst / flash flood
    probabilities. All outputs are in [0, 1].

    New parameters vs previous version:
      convergence      -- 850 hPa horizontal convergence (s^-1, positive = inflow)
      ctt_drop_rate    -- rate of cloud-top cooling (C/hour, positive = cooling)
      qpe_mm           -- 6-hour accumulated precipitation from GFS APCP (mm)
    """
    # --- Thunderstorm ---
    ts_score = 0.0

    if cape is not None:
        ts_score += min(1.0, cape / 3000.0) * 0.30

    if ki is not None:
        ki_c = ki - 273.15 if ki > 200 else ki
        ts_score += min(1.0, max(0.0, (ki_c - 20) / 20.0)) * 0.22

    if tt is not None:
        tt_c = tt - 273.15 if tt > 200 else tt
        ts_score += min(1.0, max(0.0, (tt_c - 44) / 12.0)) * 0.18

    if shear_ms is not None:
        ts_score += min(1.0, shear_ms / 30.0) * 0.15

    # LOW-LEVEL CONVERGENCE: positive convergence (inflow) increases TS score
    # Typical strong convergence event: ~2e-4 s^-1
    if convergence is not None and convergence > 0:
        ts_score += min(1.0, convergence / 2e-4) * 0.10

    # CTT DROP RATE: rapid cooling of cloud tops signals explosive updraft
    # 5 C/hour cooling is a strong signal; 10+ is extreme
    if ctt_drop_rate is not None and ctt_drop_rate > 0:
        ts_score += min(1.0, ctt_drop_rate / 10.0) * 0.05

    if cin is not None:
        cin_penalty = min(0.15, abs(cin) / 1000.0 * 0.15)
        ts_score = max(0.0, ts_score - cin_penalty)

    ts_prob = min(1.0, ts_score)

    # --- Cloudburst ---
    cb_score = 0.0

    if pwat is not None:
        cb_score += min(1.0, max(0.0, (pwat - 30) / 35.0)) * 0.40

    if cape is not None:
        cb_score += min(1.0, cape / 2500.0) * 0.25

    if ctt_c is not None:
        cb_score += min(1.0, max(0.0, (-ctt_c - 10) / 30.0)) * 0.20

    # CTT DROP RATE: rapid cooling drives convective rainfall
    if ctt_drop_rate is not None and ctt_drop_rate > 0:
        cb_score += min(1.0, ctt_drop_rate / 10.0) * 0.08

    # QPE PROXY: active precipitation confirms moisture is falling
    # 10 mm / 6h is moderate; 50 mm / 6h is cloudburst-class
    if qpe_mm is not None and qpe_mm > 0:
        cb_score += min(1.0, qpe_mm / 50.0) * 0.07

    cb_prob = min(1.0, cb_score) * ts_prob

    # --- Flash Flood ---
    ff_score = 0.0

    if pwat is not None:
        ff_score += min(1.0, max(0.0, (pwat - 35) / 30.0)) * 0.45

    if cape is not None:
        ff_score += min(1.0, cape / 2000.0) * 0.22

    if ctt_c is not None:
        ff_score += min(1.0, max(0.0, (-ctt_c - 5) / 45.0)) * 0.18

    # QPE PROXY: rainfall already on ground amplifies flash flood risk
    if qpe_mm is not None and qpe_mm > 0:
        ff_score += min(1.0, qpe_mm / 30.0) * 0.10

    # LOW-LEVEL CONVERGENCE: persistent inflow sustains heavy rainfall
    if convergence is not None and convergence > 0:
        ff_score += min(1.0, convergence / 2e-4) * 0.05

    ff_prob = min(1.0, ff_score) * min(1.0, ts_prob + 0.1)

    return ts_prob, cb_prob, ff_prob


# ---------------------------------------------------------------------------
# CTT GRID OUTPUT
# ---------------------------------------------------------------------------

def write_ctt_grid(cells: list, out_path: Path):
    ctt_cells = [
        {"lat": c["lat"], "lon": c["lon"], "ctt_c": c["ctt_c"]}
        for c in cells if c.get("ctt_c") is not None
    ]
    out_path.write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "description": "Cloud Top Temperature (CTT) proxy from GFS pressure-level RH + temperature",
        "unit": "degC",
        "ctt_cells": ctt_cells,
    }, separators=(",", ":")))
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

    # --- Load previous CTT grid for drop rate computation ---
    ctt_prev_path = OUT_DIR / "ctt_grid.json"
    prev_ctt_map, prev_ctt_ts = load_prev_ctt_map(ctt_prev_path)
    prev_age_h = ctt_hours_elapsed(prev_ctt_ts)
    use_ctt_delta = len(prev_ctt_map) > 0 and prev_age_h <= CTT_PREV_MAX_AGE_HOURS
    if use_ctt_delta:
        print(f"  Previous CTT grid loaded ({len(prev_ctt_map)} cells, {prev_age_h:.1f}h old) -- drop rate active")
    else:
        print("  No usable previous CTT grid -- drop rate will be None for this run")

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

        # Build lat/lon index arrays from field shape
        sample_arr = next(iter(fields.values()))
        nlat, nlon = sample_arr.shape
        lats_1d = np.linspace(BOUNDS["N"], BOUNDS["S"], nlat)
        lons_1d = np.linspace(BOUNDS["W"], BOUNDS["E"], nlon)

        def field(*keys):
            for k in keys:
                if k in fields:
                    return fields[k]
            return None

        # --- Pre-compute 850 hPa convergence grid ---
        u850_arr = field("u_isobaricInhPa_850")
        v850_arr = field("v_isobaricInhPa_850")
        print("  Computing 850 hPa convergence grid...")
        conv_grid = compute_convergence_grid(u850_arr, v850_arr, lats_1d, lons_1d)
        if conv_grid is not None:
            print("  Convergence grid ready")
        else:
            print("  850 hPa U/V not found -- convergence will be None")

        # --- QPE: accumulated precipitation ---
        # APCP is stored as surface accumulated precip (kg/m2 = mm)
        apcp_arr = field(
            "tp_surface", "tp_surface_0",
            "APCP_surface", "acpcp_surface",
            "asnow_surface",  # fallback -- not ideal but avoids None
        )
        has_qpe = apcp_arr is not None
        print(f"  QPE (APCP) field: {'found' if has_qpe else 'not found -- QPE will be None'}")

        print("  Scoring ~15,000 grid cells...")
        cells = []
        lats = [round(v, 4) for v in np.arange(BOUNDS["S"], BOUNDS["N"] + GRID_STEP * 0.5, GRID_STEP)]
        lons = [round(v, 4) for v in np.arange(BOUNDS["W"], BOUNDS["E"] + GRID_STEP * 0.5, GRID_STEP)]

        for lat in lats:
            for lon in lons:
                li, lj = lat_lon_to_idx(lats_1d, lons_1d, lat, lon)

                # Core thermodynamic fields
                cape = extract_point(
                    field("cape_surface",
                          "cape_convectivelyAvailablePotentialEnergy_surface",
                          "CAPE_surface"), li, lj)
                cin  = extract_point(
                    field("cin_surface",
                          "cin_convectiveInhibition_surface",
                          "CIN_surface"), li, lj)
                pwat = extract_point(
                    field("pwat_atmosphereSingleLayer",
                          "pwat_entireAtmosphere",
                          "PWAT_atmosphereSingleLayer"), li, lj)

                T850  = extract_point(field("t_isobaricInhPa_850"), li, lj)
                T700  = extract_point(field("t_isobaricInhPa_700"), li, lj)
                T500  = extract_point(field("t_isobaricInhPa_500"), li, lj)
                Td850 = extract_point(field("d_isobaricInhPa_850", "dpt_isobaricInhPa_850"), li, lj)
                Td700 = extract_point(field("d_isobaricInhPa_700", "dpt_isobaricInhPa_700"), li, lj)

                u850  = extract_point(u850_arr, li, lj)
                v850  = extract_point(v850_arr, li, lj)
                u200  = extract_point(field("u_isobaricInhPa_200"), li, lj)
                v200  = extract_point(field("v_isobaricInhPa_200"), li, lj)

                ki    = compute_k_index(T850, Td850, T700, T500, Td700)
                tt    = compute_totals_totals(T850, Td850, T500)
                shear = compute_wind_shear(u850, v850, u200, v200)
                ctt_c = compute_ctt(fields, lats_1d, lons_1d, li, lj)

                # --- LOW-LEVEL CONVERGENCE ---
                convergence = None
                if conv_grid is not None:
                    convergence = extract_point(conv_grid, li, lj)

                # --- CTT DROP RATE ---
                ctt_drop_rate = None
                if use_ctt_delta and ctt_c is not None:
                    prev_ctt = prev_ctt_map.get((lat, lon))
                    if prev_ctt is not None:
                        # Positive drop rate = cloud top is getting colder (cooling)
                        # Rate in C/hour; prev_age_h is elapsed time since last run
                        delta = prev_ctt - ctt_c  # prev warmer - current colder = positive cooling
                        ctt_drop_rate = delta / prev_age_h if prev_age_h > 0.05 else None

                # --- QPE PROXY ---
                qpe_mm = None
                if has_qpe:
                    qpe_mm = extract_point(apcp_arr, li, lj)
                    # APCP can be negative (artifact) -- floor at 0
                    if qpe_mm is not None and qpe_mm < 0:
                        qpe_mm = 0.0

                ts_prob, cb_prob, ff_prob = hazard_probabilities(
                    cape, cin, ki, tt, pwat, shear, ctt_c,
                    convergence=convergence,
                    ctt_drop_rate=ctt_drop_rate,
                    qpe_mm=qpe_mm,
                )

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

                def _ki_c(ki):
                    if ki is None:
                        return None
                    return round(ki - 273.15, 1) if ki > 200 else round(ki, 1)

                def _tt_c(tt):
                    if tt is None:
                        return None
                    return round(tt - 273.15, 1) if tt > 200 else round(tt, 1)

                cells.append({
                    "lat": lat,
                    "lon": lon,
                    "thunderstorm_probability":  round(ts_prob, 4),
                    "cloudburst_probability":    round(cb_prob, 4),
                    "flash_flood_probability":   round(ff_prob, 4),
                    "thunderstorm_risk":         risk_label,
                    # Thermodynamic fields
                    "cape":           round(cape, 1)  if cape  is not None else None,
                    "cin":            round(cin, 1)   if cin   is not None else None,
                    "k_index":        _ki_c(ki),
                    "totals_totals":  _tt_c(tt),
                    "wind_shear_ms":  round(shear, 2) if shear is not None else None,
                    "pwat_mm":        round(pwat, 1)  if pwat  is not None else None,
                    "ctt_c":          round(ctt_c, 1) if ctt_c is not None else None,
                    # New fields
                    "convergence_s":     round(convergence, 6)    if convergence    is not None else None,
                    "ctt_drop_rate_c_hr": round(ctt_drop_rate, 2) if ctt_drop_rate is not None else None,
                    "qpe_mm":            round(qpe_mm, 1)         if qpe_mm        is not None else None,
                })

        # Summary statistics
        ts_vals = [c["thunderstorm_probability"]  for c in cells]
        cb_vals = [c["cloudburst_probability"]    for c in cells]
        ff_vals = [c["flash_flood_probability"]   for c in cells]

        def _stats(vals):
            return {
                "min":  round(min(vals), 4),
                "max":  round(max(vals), 4),
                "mean": round(sum(vals) / len(vals), 4),
            }

        output = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "gfs_cycle":        f"{date_str} {cycle}Z",
            "gfs_fhour":        fhour,
            "grid_step_deg":    GRID_STEP,
            "bounds":           BOUNDS,
            "n_cells":          len(cells),
            "features_active": {
                "ctt_drop_rate": use_ctt_delta,
                "convergence":   conv_grid is not None,
                "qpe":           has_qpe,
            },
            "summary": {
                "thunderstorm_probability": _stats(ts_vals),
                "cloudburst_probability":   _stats(cb_vals),
                "flash_flood_probability":  _stats(ff_vals),
            },
            "grid_cells": cells,
        }

        OUT_FILE.write_text(json.dumps(output, separators=(",", ":")))
        print(f"  Written {len(cells)} cells -> {OUT_FILE}")

        # Write CTT grid (used by next run for drop rate)
        write_ctt_grid(cells, OUT_DIR / "ctt_grid.json")

    print("Pipeline complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GFS hazard pipeline")
    parser.add_argument("--cycle", choices=["00", "06", "12", "18"], default=None)
    parser.add_argument("--fhour", type=int, default=0,
                        help="Forecast hour (0, 6, 12, ... default 0)")
    args = parser.parse_args()
    run(cycle_override=args.cycle, fhour=args.fhour)
