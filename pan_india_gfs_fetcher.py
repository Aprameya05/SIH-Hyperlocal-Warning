#!/usr/bin/env python3
"""
pan_india_gfs_fetcher.py — Pan-India GFS Grid Fetcher
Downloads GFS 0.25-degree GRIB2 for India bounding box, extracts
atmospheric variables at each grid cell, writes data/pan_india_grid.json.

Grid: 6°N–37°N, 68°E–98°E at 1.0° spacing → ~31×31 = 961 cells.
Runs in ~2 min (one GRIB download, vectorised extraction).
"""

import json
import os
import sys
import logging
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import requests
import xarray as xr
import cfgrib

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Grid definition ───────────────────────────────────────────────────────────
INDIA_SOUTH = 6.0
INDIA_NORTH = 37.0
INDIA_WEST  = 68.0
INDIA_EAST  = 98.0
GRID_STEP   = 1.0  # degrees

# ── GFS NOMADS base ───────────────────────────────────────────────────────────
NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
HEADERS     = {"User-Agent": "Mozilla/5.0 (compatible; SIH-Hyperlocal/1.0)"}

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
OUT_PATH = DATA_DIR / "pan_india_grid.json"


def resolve_gfs_cycle(now_utc: datetime) -> tuple[str, int]:
    for lag_h in [4, 10, 16, 22]:
        candidate = now_utc - timedelta(hours=lag_h)
        cycle_h   = (candidate.hour // 6) * 6
        cycle_dt  = candidate.replace(hour=cycle_h, minute=0, second=0, microsecond=0)
        elapsed   = (now_utc - cycle_dt).total_seconds() / 3600
        fhour     = max(6, min(int(round(elapsed / 6) * 6), 120))
        cycle_str = cycle_dt.strftime("%Y%m%d") + f"{cycle_h:02d}"
        return cycle_str, fhour
    cycle_dt = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    return cycle_dt.strftime("%Y%m%d") + "12", 12


def build_nomads_url(cycle_str: str, fhour: int) -> str:
    date_str = cycle_str[:8]
    cycle_h  = cycle_str[8:]
    fname    = f"gfs.t{cycle_h}z.pgrb2.0p25.f{fhour:03d}"
    params = (
        f"file={fname}"
        f"&lev_surface=on"
        f"&lev_2_m_above_ground=on"
        f"&lev_850_mb=on&lev_700_mb=on&lev_500_mb=on"
        f"&lev_entire_atmosphere_%28considered_as_a_single_layer%29=on"
        f"&var_CAPE=on&var_CIN=on&var_PWAT=on"
        f"&var_TMP=on&var_RH=on&var_SPFH=on"
        f"&var_UGRD=on&var_VGRD=on&var_DPT=on"
        f"&var_APCP=on"
        f"&leftlon={INDIA_WEST}&rightlon={INDIA_EAST}"
        f"&toplat={INDIA_NORTH}&bottomlat={INDIA_SOUTH}"
        f"&dir=%2Fgfs.{date_str}%2F{cycle_h}%2Fatmos"
    )
    return f"{NOMADS_BASE}?{params}"


def download_grib(url: str, out_path: str, max_retries: int = 3) -> bool:
    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"  Downloading (attempt {attempt}/{max_retries}): {url[:120]}...")
            r = requests.get(url, headers=HEADERS, timeout=120)
            if r.status_code != 200:
                log.warning(f"  HTTP {r.status_code}")
            elif len(r.content) < 1000 or r.content[:4] == b"<htm":
                log.warning("  Response looks like HTML error page")
            else:
                with open(out_path, "wb") as f:
                    f.write(r.content)
                log.info(f"  Saved {len(r.content)/1024:.1f} KB → {out_path}")
                return True
        except Exception as e:
            log.warning(f"  Attempt {attempt} failed: {e}")
        if attempt < max_retries:
            time.sleep(30 * attempt)
    return False


def safe_open(grib_path: str, filter_keys: dict):
    try:
        return xr.open_dataset(grib_path, engine="cfgrib",
                               backend_kwargs={"filter_by_keys": filter_keys},
                               indexpath="")
    except Exception:
        return None


def extract_india_grid(grib_path: str) -> list[dict]:
    log.info("Extracting surface fields...")

    ds_sfc  = safe_open(grib_path, {"typeOfLevel": "surface", "stepType": "instant"})
    ds_cape = safe_open(grib_path, {"typeOfLevel": "atmosphereSingleLayer"})
    ds_2m   = safe_open(grib_path, {"typeOfLevel": "heightAboveGround", "level": 2})
    ds_10m  = safe_open(grib_path, {"typeOfLevel": "heightAboveGround", "level": 10})
    ds_850  = safe_open(grib_path, {"typeOfLevel": "isobaricInhPa", "level": 850})
    ds_700  = safe_open(grib_path, {"typeOfLevel": "isobaricInhPa", "level": 700})
    ds_500  = safe_open(grib_path, {"typeOfLevel": "isobaricInhPa", "level": 500})

    lats = np.arange(INDIA_SOUTH, INDIA_NORTH + GRID_STEP, GRID_STEP)
    lons = np.arange(INDIA_WEST,  INDIA_EAST  + GRID_STEP, GRID_STEP)

    def _get(ds, varname, lat, lon):
        if ds is None or varname not in ds:
            return None
        try:
            val = float(ds[varname].sel(latitude=lat, longitude=lon % 360,
                                        method="nearest").values)
            return None if np.isnan(val) else val
        except Exception:
            return None

    cells = []
    for lat in lats:
        for lon in lons:
            cape  = _get(ds_cape, "cape", lat, lon) or _get(ds_sfc, "cape", lat, lon) or 0.0
            cin   = _get(ds_cape, "cin",  lat, lon) or _get(ds_sfc, "cin",  lat, lon) or 0.0
            pwat  = _get(ds_cape, "pwat", lat, lon) or _get(ds_sfc, "pwat", lat, lon) or 0.0

            t2m  = _get(ds_2m, "t2m", lat, lon)
            d2m  = _get(ds_2m, "d2m", lat, lon)
            rh   = _get(ds_2m, "r",   lat, lon)

            u850 = _get(ds_850, "u", lat, lon) or 0.0
            v850 = _get(ds_850, "v", lat, lon) or 0.0
            t850 = _get(ds_850, "t", lat, lon)
            q850 = _get(ds_850, "q", lat, lon)

            u700 = _get(ds_700, "u", lat, lon) or 0.0
            v700 = _get(ds_700, "v", lat, lon) or 0.0
            t700 = _get(ds_700, "t", lat, lon)
            q700 = _get(ds_700, "q", lat, lon)

            u500 = _get(ds_500, "u", lat, lon) or 0.0
            v500 = _get(ds_500, "v", lat, lon) or 0.0
            t500 = _get(ds_500, "t", lat, lon)

            apcp = _get(ds_sfc, "tp", lat, lon) or 0.0

            # Derive dewpoint at 850 hPa from specific humidity q850
            # Td850 (K) using Bolton (1980): Td = T - ((1 - RH) / 0.05) approx
            # Better: from q850 use e = q*p/(0.622+q); Td = 1/(1/273.15 - ln(e/6.11)/5423)
            td850_c = None
            if q850 is not None and t850 is not None:
                try:
                    q = max(float(q850), 1e-6)
                    e = q * 850 / (0.622 + q)  # hPa
                    td850_c = 1.0 / (1.0/273.15 - np.log(e/6.112)/5423.0) - 273.15
                except Exception:
                    td850_c = None

            td700_c = None
            if q700 is not None and t700 is not None:
                try:
                    q = max(float(q700), 1e-6)
                    e = q * 700 / (0.622 + q)
                    td700_c = 1.0 / (1.0/273.15 - np.log(e/6.112)/5423.0) - 273.15
                except Exception:
                    td700_c = None

            k_index = None
            if t850 and t700 and t500 and td850_c is not None:
                t850c = t850 - 273.15
                t700c = t700 - 273.15
                t500c = t500 - 273.15
                dd700 = t700c - (td700_c if td700_c is not None else t700c - 8.0)
                k_index = (t850c - t500c) + td850_c - dd700

            totals_totals = None
            if t850 and t500 and td850_c is not None:
                totals_totals = (t850 - 273.15) + td850_c - 2.0 * (t500 - 273.15)

            wind_shear = float(np.sqrt((u500 - u850)**2 + (v500 - v850)**2))

            cells.append({
                "lat": round(float(lat), 2),
                "lon": round(float(lon), 2),
                "cape": round(cape, 1),
                "cin": round(cin, 1),
                "pwat": round(pwat, 1),
                "k_index": round(k_index, 1) if k_index is not None else None,
                "totals_totals": round(totals_totals, 1) if totals_totals is not None else None,
                "u850": round(u850, 2),
                "v850": round(v850, 2),
                "u500": round(u500, 2),
                "v500": round(v500, 2),
                "wind_shear_ms": round(wind_shear, 2),
                "apcp_mm": round(apcp * 1000, 2) if apcp else 0.0,
                "t2m_c": round(t2m - 273.15, 1) if t2m else None,
                "rh2": round(rh, 1) if rh else None,
            })

    # Explicitly close all GRIB datasets to prevent cfgrib segfault on GC
    for ds in [ds_sfc, ds_cape, ds_2m, ds_10m, ds_850, ds_700, ds_500]:
        try:
            if ds is not None:
                ds.close()
        except Exception:
            pass

    log.info(f"Extracted {len(cells)} grid cells.")
    return cells


# ── Multi-hazard inference ─────────────────────────────────────────────────────

def compute_thunderstorm_probability(cell: dict) -> float:
    """
    Calibrated thunderstorm probability. Thresholds tuned so that
    typical Indian monsoon background (K ~35, CAPE ~500) gives ~25-35%,
    not 70%+. Only extreme instability (CAPE>2000, K>42) reaches HIGH.
    """
    cape  = cell["cape"]
    ki    = cell["k_index"] if cell["k_index"] is not None else 30.0
    tt    = cell["totals_totals"] if cell["totals_totals"] is not None else 40.0
    pwat  = cell["pwat"]
    cin   = abs(cell["cin"])
    shear = cell["wind_shear_ms"]

    def sig(x, center, scale):
        return 1.0 / (1.0 + np.exp(-(x - center) / scale))

    # Tighter centers: CAPE center at 1500 (not 800), K at 38 (not 32)
    p_cape  = sig(cape, 1500, 600)   # CAPE 500=20%, 1500=50%, 3000=90%
    p_ki    = sig(ki,   38,   5)     # K=32->18%, K=38->50%, K=44->82%
    p_tt    = sig(tt,   46,   4)     # TT=42->12%, TT=46->50%, TT=50->88%
    p_pwat  = sig(pwat, 50,   10)    # PWAT=40->27%, 50->50%, 60->73%
    p_cin   = 1.0 - sig(cin, 80, 40) # CIN >200 kills probability

    prob = (0.30 * p_cape + 0.25 * p_ki + 0.20 * p_tt +
            0.15 * p_pwat + 0.10 * p_cin)

    # Shear bonus only for genuinely strong shear (supercell territory)
    if shear > 20:
        prob = min(1.0, prob * 1.15)
    elif shear > 15:
        prob = min(1.0, prob * 1.07)

    return round(float(prob), 4)


def compute_cloudburst_probability(cell: dict, ts_prob: float) -> float:
    """
    Cloudburst = extreme localised rainfall (>100 mm/3h).
    This is rare even in Indian monsoon -- requires PWAT > 55 mm AND
    CAPE > 1500 J/kg AND low CIN AND ts_prob already elevated.
    Designed so that a generic monsoon cell with PWAT=45, CAPE=600
    returns 0, not 58%.
    """
    cape  = cell["cape"]
    pwat  = cell["pwat"]
    ki    = cell["k_index"] if cell["k_index"] is not None else 30.0
    cin   = abs(cell["cin"])

    # Hard gates -- both must be met
    if pwat < 50 or cape < 1200:
        return 0.0

    # CIN must not cap convection hard
    if cin > 150:
        return 0.0

    # Moisture excess above the hard threshold
    moisture_score    = min(1.0, (pwat - 50) / 25.0)   # 0 at 50mm, 1 at 75mm
    instability_score = min(1.0, (cape - 1200) / 2000.0) # 0 at 1200, 1 at 3200
    ki_score          = min(1.0, max(0.0, (ki - 36) / 10.0))
    cin_factor        = 1.0 - min(1.0, cin / 150.0)

    # CB is a product of all conditions, then scaled by TS probability
    # ts_prob must itself be elevated (>=0.35) for CB to be non-zero
    if ts_prob < 0.35:
        return 0.0

    ts_factor = min(1.0, (ts_prob - 0.35) / 0.35)  # 0 at ts=0.35, 1 at ts=0.70
    prob = ts_factor * cin_factor * (
        0.40 * moisture_score +
        0.40 * instability_score +
        0.20 * ki_score
    )

    return round(float(min(prob, 0.85)), 4)


def compute_flash_flood_risk(cell: dict, cb_prob: float, elevation_m=None) -> float:
    """
    Flash flood risk. FF is NOT simply CB + boost.
    FF requires sustained heavy rain accumulation: high PWAT + prior
    rainfall + terrain. In the absence of DEM, orographic zones get
    a modest multiplier (not additive offset) on top of CB probability.
    A cell with CB=0 cannot have FF>0 here.
    """
    if cb_prob == 0.0:
        return 0.0

    lat = cell["lat"]
    lon = cell["lon"]
    apcp = cell.get("apcp_mm", 0.0) or 0.0

    # Terrain multiplier (not additive) -- max 1.4x in highest-risk zones
    terrain_mult = 1.0
    if 73 <= lon <= 77 and 8 <= lat <= 22:
        terrain_mult = 1.35   # Western Ghats -- steep orography, high runoff
    elif 26 <= lat <= 32 and 73 <= lon <= 97:
        terrain_mult = 1.25   # Himalayan foothills
    elif 22 <= lat <= 28 and 88 <= lon <= 97:
        terrain_mult = 1.20   # Northeast India
    elif 15 <= lat <= 20 and 73 <= lon <= 80:
        terrain_mult = 1.10   # Vidarbha/Marathwada -- flat, moderate risk
    # Elsewhere (Gangetic plains, Peninsular interior): no boost

    # Elevation amplification
    if elevation_m is not None:
        if elevation_m > 1000:
            terrain_mult = min(terrain_mult * 1.15, 1.60)
        elif elevation_m > 500:
            terrain_mult = min(terrain_mult * 1.07, 1.50)

    # Prior accumulated precipitation adds soil saturation risk
    precip_factor = 1.0
    if apcp > 20:
        precip_factor = 1.15
    elif apcp > 10:
        precip_factor = 1.07

    risk = min(1.0, cb_prob * terrain_mult * precip_factor)
    return round(float(risk), 4)


def risk_label(prob: float) -> str:
    if prob >= 0.70: return "SEVERE"
    if prob >= 0.45: return "HIGH"
    if prob >= 0.25: return "MODERATE"
    if prob >= 0.10: return "LOW"
    return "MINIMAL"


def main():
    now_utc = datetime.now(timezone.utc)
    log.info(f"Pan-India GFS fetch — {now_utc.strftime('%Y-%m-%d %H:%M UTC')}")

    cycle_str, fhour = resolve_gfs_cycle(now_utc)
    log.info(f"GFS cycle: {cycle_str}  fhour: {fhour:03d}")

    url = build_nomads_url(cycle_str, fhour)

    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        grib_path = tmp.name

    try:
        ok = download_grib(url, grib_path)
        if not ok:
            log.error("GRIB download failed — aborting pan-India grid update")
            sys.exit(1)

        cells = extract_india_grid(grib_path)

        grid_cells = []
        for cell in cells:
            ts_prob = compute_thunderstorm_probability(cell)
            cb_prob = compute_cloudburst_probability(cell, ts_prob)
            ff_risk = compute_flash_flood_risk(cell, cb_prob, elevation_m=None)

            grid_cells.append({
                **cell,
                "thunderstorm_probability": ts_prob,
                "thunderstorm_risk":        risk_label(ts_prob),
                "cloudburst_probability":   cb_prob,
                "cloudburst_risk":          risk_label(cb_prob),
                "flash_flood_probability":  ff_risk,
                "flash_flood_label":        risk_label(ff_risk),
            })

        ts_max   = max(c["thunderstorm_probability"] for c in grid_cells)
        cb_max   = max(c["cloudburst_probability"]   for c in grid_cells)
        ff_max   = max(c["flash_flood_probability"]  for c in grid_cells)
        cape_max = max(c["cape"]  for c in grid_cells)
        pwat_max = max(c["pwat"]  for c in grid_cells)

        output = {
            "generated_at_utc": now_utc.isoformat(),
            "gfs_cycle":        cycle_str,
            "gfs_fhour":        fhour,
            "grid_step_deg":    GRID_STEP,
            "bounds": {"south": INDIA_SOUTH, "north": INDIA_NORTH,
                       "west":  INDIA_WEST,  "east":  INDIA_EAST},
            "n_cells": len(grid_cells),
            "summary": {
                "thunderstorm": {
                    "max_prob":    round(ts_max, 4),
                    "alert_cells": sum(1 for c in grid_cells
                                       if c["thunderstorm_probability"] >= 0.25),
                },
                "cloudburst": {
                    "max_prob":    round(cb_max, 4),
                    "alert_cells": sum(1 for c in grid_cells
                                       if c["cloudburst_probability"] >= 0.25),
                },
                "flash_flood": {
                    "max_prob":    round(ff_max, 4),
                    "alert_cells": sum(1 for c in grid_cells
                                       if c["flash_flood_probability"] >= 0.25),
                },
                "cape_max":  round(cape_max, 1),
                "pwat_max":  round(pwat_max, 1),
            },
            "grid_cells": grid_cells,
        }

        with open(OUT_PATH, "w") as f:
            json.dump(output, f, separators=(",", ":"))

        log.info(f"Written → {OUT_PATH}  ({len(grid_cells)} cells)")
        log.info(f"  Max TS prob: {ts_max:.1%}  Max CB prob: {cb_max:.1%}  Max FF risk: {ff_max:.1%}")
        log.info(f"  Peak CAPE: {cape_max:.0f} J/kg  Peak PWAT: {pwat_max:.1f} mm")

    finally:
        try:
            os.unlink(grib_path)
        except Exception:
            pass


if __name__ == "__main__":
    main()
