"""
gfs_fetcher.py
==============
CSIR Thunderstorm Prediction System — Real-time GFS pipeline
Station: 43295 / VOBL, Bengaluru Airport  lat=12.97, lon=77.58

Fetches GFS 0.25° GRIB2 data from NOAA NOMADS for the upcoming slot and:
  1. Computes stability indices (CAPE, CIN, K-Index, LI, TT, PW, wind components)
     and appends one row to  data/upperair_realtime_43295.csv
  2. Fetches TMP at f006/f012/f018/f024 for diurnal Tmax/Tmin computation and
     writes all ERA5-schema fields + TMP columns to  data/gfs_realtime_43295.csv
  3. Generates  data/gfs_multiday_43295.json  (f024/f048 outlook)

Cycle-selection (t+12 rule, per pipeline_scoping_findings.md):
    Slot 0  (0001–0600 IST) → prev-day 06Z  f012  (valid 18Z prev day)
    Slot 1  (0601–1200 IST) → prev-day 12Z  f012  (valid 00Z)
    Slot 2  (1201–1800 IST) → prev-day 18Z  f012  (valid 06Z)
    Slot 3  (1801–2400 IST) → same-day  00Z  f012  (valid 12Z)

NOMADS quirks:
  - Requires Chrome User-Agent header (bare requests get 403)
  - Paths are UTC only — never IST dates
  - CAPE level: lev_entire_atmosphere_(considered_as_a_single_layer)
  - Wind: var_UGRD / var_VGRD

Requirements:
    pip install requests cfgrib xarray metpy pandas numpy eccodes scipy

Usage:
    python gfs_fetcher.py                     # auto-detect upcoming slot
    python gfs_fetcher.py --slot 2            # force slot 2
    python gfs_fetcher.py --now "2026-08-11 14:00"  # simulate IST time
    python gfs_fetcher.py --verify            # also run pygrib cross-check
    python gfs_fetcher.py --keep-grib         # keep downloaded GRIB2 file

Author: Aprameya + Atul, CSIR Thunderstorm Project
"""

import argparse
import gc
import json
import os
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
LAT, LON = 12.97, 77.58
BOX = 0.5                      # subset box half-width (degrees)
DATA_DIR = Path("data")
OUT_PATH_UA  = DATA_DIR / "upperair_realtime_43295.csv"
OUT_PATH_GFS = DATA_DIR / "gfs_realtime_43295.csv"
OUT_PATH_MULTIDAY = DATA_DIR / "gfs_multiday_43295.json"
GRIB_TMP = DATA_DIR / "_gfs_tmp.grib2"
GRIB_TMP_AUX = DATA_DIR / "_gfs_tmp_aux.grib2"

NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
FORECAST_HOUR = 12
POST_LATENCY_HOURS = 4
SLOT_START_HOURS = [0, 6, 12, 18]
SLOT_WINDOWS = {0: "0001-0600", 1: "0601-1200", 2: "1201-1800", 3: "1801-2400"}
IST = timezone(timedelta(hours=5, minutes=30))

# QC thresholds for CAPE consistency check
CAPE_NEAR_ZERO = 1.0
CAPE_SUSPECT_KINDEX = 30.0
CAPE_SUSPECT_TT = 44.0

def _c2k(val) -> float:
    """Celsius → Kelvin. Returns np.nan if val is None/NaN."""
    if val is None:
        return np.nan
    try:
        f = float(val)
        return np.nan if np.isnan(f) else f + 273.15
    except (TypeError, ValueError):
        return np.nan


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ── Slot / cycle resolution ───────────────────────────────────────────────────

def next_slot(now_ist: datetime):
    for h in SLOT_START_HOURS:
        candidate = now_ist.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now_ist:
            return SLOT_START_HOURS.index(h), candidate
    tomorrow = now_ist + timedelta(days=1)
    return 0, tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)


def resolve_source(slot_start_ist_dt: datetime):
    slot_start_utc = slot_start_ist_dt.astimezone(timezone.utc)
    nominal_valid = slot_start_utc.replace(minute=0, second=0, microsecond=0)
    cycle_utc = nominal_valid - timedelta(hours=FORECAST_HOUR)
    return cycle_utc, FORECAST_HOUR, nominal_valid, slot_start_utc


# ── NOMADS download ───────────────────────────────────────────────────────────

def build_nomads_url(cycle_utc: datetime, fhour: int, extra_vars: bool = False) -> str:
    """Build NOMADS filter URL. extra_vars=True adds APCP for rainfall."""
    date_str = cycle_utc.strftime("%Y%m%d")
    cc = f"{cycle_utc.hour:02d}"
    fff = f"{fhour:03d}"
    filename = f"gfs.t{cc}z.pgrb2.0p25.f{fff}"
    params = {
        "file": filename,
        "lev_surface": "on",
        "lev_2_m_above_ground": "on",
        "lev_10_m_above_ground": "on",
        "lev_850_mb": "on",
        "lev_700_mb": "on",
        "lev_500_mb": "on",
        "lev_entire_atmosphere_(considered_as_a_single_layer)": "on",
        "var_TMP": "on",
        "var_RH": "on",
        "var_SPFH": "on",
        "var_PRES": "on",
        "var_CAPE": "on",
        "var_CIN": "on",
        "var_PWAT": "on",
        "var_UGRD": "on",
        "var_VGRD": "on",
        "var_DPT": "on",
        "subregion": "on",
        "leftlon": f"{LON - BOX:.2f}",
        "rightlon": f"{LON + BOX:.2f}",
        "toplat": f"{LAT + BOX:.2f}",
        "bottomlat": f"{LAT - BOX:.2f}",
        "dir": f"/gfs.{date_str}/{cc}/atmos",
    }
    if extra_vars:
        params["var_APCP"] = "on"
    return NOMADS_BASE + "?" + urlencode(params)


def download_grib(url: str, out_path: Path, max_retries: int = 3) -> None:
    """Download GRIB2 from NOMADS with retry logic.

    NOMADS quirks:
    - Requires Chrome User-Agent (bare requests get 403)
    - May return HTML error page (<1000 bytes) if cycle not yet posted
    - Retries with exponential backoff; tries the previous cycle on persistent failure
    """
    import time
    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            print(f"  Downloading (attempt {attempt}/{max_retries}): {url[:120]}...")
            r = requests.get(url, headers=HEADERS, timeout=180)
            r.raise_for_status()
            if len(r.content) < 1000:
                raise RuntimeError(
                    f"Response too small ({len(r.content)} bytes) — NOMADS returned HTML error, not GRIB2. "
                    "This cycle/hour may not be posted yet."
                )
            with open(out_path, "wb") as f:
                f.write(r.content)
            print(f"  Saved {len(r.content) / 1024:.0f} KB → {out_path}")
            return  # success
        except Exception as e:
            last_error = e
            wait = 30 * attempt  # 30s, 60s, 90s
            if attempt < max_retries:
                print(f"  ⚠ Attempt {attempt} failed: {e}  — retrying in {wait}s")
                time.sleep(wait)
            else:
                print(f"  ✗ All {max_retries} attempts failed for {url[:80]}")
    raise RuntimeError(f"NOMADS download failed after {max_retries} attempts: {last_error}")


# ── GRIB2 parsing (pure eccodes — no cfgrib/xarray, no GC memory corruption) ──

def _nearest_idx(lats: np.ndarray, lons: np.ndarray, lat: float, lon: float):
    """Return flat index of grid point nearest to (lat, lon)."""
    dist = (lats - lat) ** 2 + (lons - lon) ** 2
    return int(np.argmin(dist))


def extract_fields(grib_path: Path) -> tuple[dict, dict]:
    """
    Parse GRIB2 using the eccodes Python bindings directly.
    No cfgrib, no xarray — handles are opened and closed inside this function,
    so the eccodes C library never sees a double-free or corrupt pointer.
    Returns (surface_fields, profile) where profile is keyed by pressure level.
    """
    import eccodes

    surface_fields: dict = {}
    profile: dict = {}      # {850: {t_C, rh, u, v, q}, 700: ..., 500: ...}

    # shortName → (output_key, transform)
    SURFACE_MAP = {
        "cape":  ("cape",   lambda v: v),
        "cin":   ("cin",    lambda v: v),
        "pwat":  ("pwat",   lambda v: v),
        "sp":    ("sp_pa",  lambda v: v),
        "acpcp": ("apcp",   lambda v: v),
        "tp":    ("apcp",   lambda v: v),
        "apcp":  ("apcp",   lambda v: v),
        "2t":    ("t2m_C",  lambda v: v - 273.15),
        "t":     ("t2m_C",  lambda v: v - 273.15),   # caught only at 2m
        "2d":    ("d2m_C",  lambda v: v - 273.15),
        "d":     ("d2m_C",  lambda v: v - 273.15),   # caught only at 2m
        "2r":    ("rh2",    lambda v: v),
        "10u":   ("u10",    lambda v: v),
        "10v":   ("v10",    lambda v: v),
    }

    PROFILE_LEVELS = {850, 700, 500}
    # shortName → profile sub-key
    PROFILE_MAP = {
        "t":    "t_K",
        "r":    "rh",
        "u":    "u",
        "v":    "v",
        "q":    "q",
        "spfh": "q",
    }

    with open(str(grib_path), "rb") as f:
        while True:
            try:
                msg = eccodes.codes_grib_new_from_file(f)
            except eccodes.CodesInternalError:
                break
            if msg is None:
                break
            try:
                short = eccodes.codes_get(msg, "shortName", ktype=str)
                type_of_level = eccodes.codes_get(msg, "typeOfLevel", ktype=str)
                level = eccodes.codes_get(msg, "level", ktype=int)

                # Get lat/lon grid and find nearest point
                lats = eccodes.codes_get_array(msg, "latitudes")
                lons = eccodes.codes_get_array(msg, "longitudes")
                vals = eccodes.codes_get_array(msg, "values")
                idx  = _nearest_idx(lats, lons % 360, LAT, LON % 360)
                val  = float(vals[idx])

                # Surface / single-layer fields
                if type_of_level in ("surface", "atmosphereSingleLayer",
                                     "heightAboveGround", "meanSea"):
                    if short in SURFACE_MAP:
                        out_key, transform = SURFACE_MAP[short]
                        # For 't' and 'd'/'r' only take them at 2m height
                        if short in ("t", "d", "2r") and type_of_level == "heightAboveGround":
                            if level == 2 and out_key not in surface_fields:
                                surface_fields[out_key] = transform(val)
                        elif short in ("10u", "10v") and type_of_level == "heightAboveGround":
                            if level == 10 and out_key not in surface_fields:
                                surface_fields[out_key] = transform(val)
                        elif short not in ("t", "d", "2r", "10u", "10v"):
                            if out_key not in surface_fields:
                                surface_fields[out_key] = transform(val)

                # Pressure-level profile
                elif type_of_level == "isobaricInhPa" and level in PROFILE_LEVELS:
                    if short in PROFILE_MAP:
                        sub_key = PROFILE_MAP[short]
                        if level not in profile:
                            profile[level] = {}
                        if sub_key not in profile[level]:
                            profile[level][sub_key] = val

            except Exception:
                pass
            finally:
                eccodes.codes_release(msg)

    # Convert profile temperatures K→C and derive q where missing
    for lvl, p in profile.items():
        if "t_K" in p:
            p["t_C"] = p.pop("t_K") - 273.15
        else:
            p["t_C"] = np.nan
        p.setdefault("rh",  np.nan)
        p.setdefault("u",   np.nan)
        p.setdefault("v",   np.nan)
        if "q" not in p and not np.isnan(p.get("t_C", np.nan)) and not np.isnan(p.get("rh", np.nan)):
            tc = p["t_C"]
            rh = p["rh"] / 100.0
            es = 611.2 * np.exp(17.67 * tc / (tc + 243.5))
            e  = rh * es
            p["q"] = 0.622 * e / (lvl * 100.0 - 0.378 * e)
        p.setdefault("q", np.nan)

    sfc_list = list(surface_fields.keys())
    print(f"  Surface: {sfc_list}")
    print(f"  Profile levels: {sorted(profile.keys())}")
    return surface_fields, profile


def compute_indices(surface_fields: dict, profile: dict) -> dict:
    """Compute stability indices matching the training feature schema."""
    import metpy.calc as mpcalc
    from metpy.units import units

    have_profile = all(lvl in profile for lvl in (850, 700, 500))
    have_surface = ("sp_pa" in surface_fields and "t2m_C" in surface_fields
                    and "rh2" in surface_fields)

    base = {
        "CAPE":          surface_fields.get("cape", np.nan),
        "CIN":           surface_fields.get("cin", np.nan),
        "K_INDEX":       np.nan,
        "LIFTED_INDEX":  np.nan,
        "TOTALS_TOTALS": np.nan,
        "PRECIP_WATER":  surface_fields.get("pwat", np.nan),
        "ERA5_u_500hPa": profile.get(500, {}).get("u", np.nan),
        "ERA5_v_500hPa": profile.get(500, {}).get("v", np.nan),
        "ERA5_u_850hPa": profile.get(850, {}).get("u", np.nan),
        "ERA5_v_850hPa": profile.get(850, {}).get("v", np.nan),
        "ERA5_u_700hPa": profile.get(700, {}).get("u", np.nan),
        "ERA5_v_700hPa": profile.get(700, {}).get("v", np.nan),
    }

    if not (have_profile and have_surface):
        print("  Warning: incomplete profile — K/LI/TT will be NaN")
        return base

    try:
        sp_hpa = surface_fields["sp_pa"] / 100.0
        td2m_C = float(mpcalc.dewpoint_from_relative_humidity(
            surface_fields["t2m_C"] * units.degC,
            surface_fields["rh2"] * units.percent,
        ).magnitude)

        p_levels = np.array([sp_hpa, 850, 700, 500])
        t_levels = np.array([
            surface_fields["t2m_C"],
            profile[850]["t_C"],
            profile[700]["t_C"],
            profile[500]["t_C"],
        ])

        td_levels = [td2m_C]
        for lvl in (850, 700, 500):
            td_levels.append(float(mpcalc.dewpoint_from_relative_humidity(
                profile[lvl]["t_C"] * units.degC,
                profile[lvl]["rh"] * units.percent,
            ).magnitude))
        td_levels = np.array(td_levels)

        p  = p_levels * units.hPa
        t  = t_levels * units.degC
        td = td_levels * units.degC

        k      = mpcalc.k_index(p, t, td)
        tt     = mpcalc.total_totals_index(p, t, td)
        parcel = mpcalc.parcel_profile(p, t[0], td[0])
        li     = mpcalc.lifted_index(p, t, parcel)

        base.update({
            "K_INDEX":       float(k.magnitude),
            "LIFTED_INDEX":  float(np.atleast_1d(li.magnitude)[0]),
            "TOTALS_TOTALS": float(tt.magnitude),
        })
    except Exception as e:
        print(f"  Warning: index computation failed: {e}")

    # ── Convective rotation parameters (SRH, EHI, BRN) ───────────────────────
    # Computed from GFS wind profile at 850/700/500 hPa.
    # Storm motion: Davies-Jones (1984) simplified — 7.5 m/s to right of mean shear.
    # SRH: Storm-Relative Helicity (m²/s²) in 0–3 km layer (850 hPa proxy)
    # EHI: Energy-Helicity Index = (CAPE × SRH) / 160000
    # BRN: Bulk Richardson Number = CAPE / (0.5 × wind_shear²)
    try:
        u850 = float(profile.get(850, {}).get("u", np.nan) or np.nan)
        v850 = float(profile.get(850, {}).get("v", np.nan) or np.nan)
        u700 = float(profile.get(700, {}).get("u", np.nan) or np.nan)
        v700 = float(profile.get(700, {}).get("v", np.nan) or np.nan)
        u500 = float(profile.get(500, {}).get("u", np.nan) or np.nan)
        v500 = float(profile.get(500, {}).get("v", np.nan) or np.nan)
        cape = float(surface_fields.get("cape", 0) or 0)

        if all(np.isfinite([u850, v850, u700, v700, u500, v500])):
            # Mean wind in 0-6 km layer (850-500 hPa proxy)
            u_mean = (u850 + u700 + u500) / 3.0
            v_mean = (v850 + v700 + v500) / 3.0

            # Storm motion: 30° to right of shear vector, 75% of shear magnitude
            du_shear = u500 - u850
            dv_shear = v500 - v850
            shear_mag = np.sqrt(du_shear**2 + dv_shear**2)
            # Right-mover: rotate shear 30° right
            angle_rad = np.radians(-30)
            us = (u_mean + 0.75 * (du_shear * np.cos(angle_rad) - dv_shear * np.sin(angle_rad)))
            vs = (v_mean + 0.75 * (du_shear * np.sin(angle_rad) + dv_shear * np.cos(angle_rad)))

            # SRH = ∫(V_s - V_wind) × dV  (simplified trapezoid over 850-500 hPa)
            # Using 850 hPa as 0-3 km proxy
            srh = (
                (u850 - us) * (v700 - vs) - (u700 - us) * (v850 - vs)
            ) * 0.5 + (
                (u700 - us) * (v500 - vs) - (u500 - us) * (v700 - vs)
            ) * 0.5

            # EHI (Energy-Helicity Index)
            ehi = (cape * max(srh, 0)) / 160000.0

            # BRN (Bulk Richardson Number)
            brn_shear_denom = 0.5 * shear_mag**2
            brn = (cape / brn_shear_denom) if brn_shear_denom > 0.1 else np.nan

            base.update({
                "SRH_0_3km":  round(float(srh), 2),
                "EHI":        round(float(ehi), 4),
                "BRN":        round(float(brn), 2) if np.isfinite(brn) else np.nan,
                "wind_shear_500_850_ms": round(float(shear_mag), 2),
            })
            print(f"  Convective params: SRH={srh:.1f} m²/s²  EHI={ehi:.3f}  BRN={brn:.1f}  shear={shear_mag:.1f} m/s")
        else:
            base.update({"SRH_0_3km": np.nan, "EHI": np.nan, "BRN": np.nan, "wind_shear_500_850_ms": np.nan})
    except Exception as e:
        print(f"  Warning: SRH/EHI/BRN computation failed: {e}")
        base.update({"SRH_0_3km": np.nan, "EHI": np.nan, "BRN": np.nan, "wind_shear_500_850_ms": np.nan})

    return base


# ── pygrib cross-check ────────────────────────────────────────────────────────

def _pygrib_value(grib_path: Path, name: str) -> float | None:
    try:
        import pygrib
        grbs = pygrib.open(str(grib_path))
        matches = grbs.select(name=name)
        if not matches:
            grbs.close()
            return None
        grb = matches[0]
        val, lats, lons = grb.data(lat1=LAT - 0.1, lat2=LAT + 0.1, lon1=LON - 0.1, lon2=LON + 0.1)
        grbs.close()
        return float(np.nanmean(val))
    except Exception:
        return None


def assess_cape(indices: dict, grib_path: Path) -> tuple[str, float | None]:
    cape = indices.get("CAPE", np.nan)
    k    = indices.get("K_INDEX", np.nan)
    tt   = indices.get("TOTALS_TOTALS", np.nan)

    zero_cape = not np.isnan(cape) and cape < CAPE_NEAR_ZERO
    suspect   = ((not np.isnan(k) and k >= CAPE_SUSPECT_KINDEX) or
                 (not np.isnan(tt) and tt >= CAPE_SUSPECT_TT))

    if not (zero_cape and suspect):
        return "OK", None

    cc = _pygrib_value(grib_path, "Convective available potential energy")
    if cc is None:
        return "SUSPECT_NO_CROSSCHECK", None
    return ("SUSPECT_CONFIRMED_ZERO" if cc < CAPE_NEAR_ZERO else "SUSPECT_MISMATCH", cc)


# ── Multi-hour TMP fetch for diurnal range ────────────────────────────────────

def fetch_tmp_multiday(cycle_utc: datetime) -> dict:
    """Fetch TMP at f006/f012/f018/f024 to get real Tmax/Tmin diurnal range.
    Returns dict: {TMP_f006: K, TMP_f012: K, TMP_f018: K, TMP_f024: K}
    Also fetches f024/f048 for multi-day outlook."""
    tmp_values = {}
    fhours_trange = [6, 12, 18, 24]

    for fh in fhours_trange:
        url = build_nomads_url(cycle_utc, fh)
        grib = GRIB_TMP_AUX.with_suffix(f".f{fh:03d}.grib2")
        try:
            download_grib(url, grib)
            ds_2m = _open_group(grib, "heightAboveGround")
            if ds_2m is not None:
                pt = ds_2m.sel(latitude=LAT, longitude=LON % 360, method="nearest")
                for src in ("t2m", "t"):
                    if src in pt:
                        try:
                            val = float(pt[src].values)
                        except Exception:
                            try:
                                val = float(pt[src].sel(heightAboveGround=2).values)
                            except Exception:
                                continue
                        if not np.isnan(val):
                            tmp_values[f"TMP_f{fh:03d}"] = val
                            break
                ds_2m.close()
        except Exception as e:
            print(f"  TMP f{fh:03d}: fetch failed — {e}")
        finally:
            try:
                grib.unlink(missing_ok=True)
            except Exception:
                pass

    return tmp_values


def fetch_multiday_outlook(cycle_utc: datetime, today_str: str) -> list[dict]:
    """Fetch f024/f048 stability for multi-day outlook.
    Returns list of {date, day_label, CAPE, K_INDEX, LIFTED_INDEX, TOTALS_TOTALS}."""
    outlook = []
    for fh, label in [(24, "Tomorrow"), (48, "Day+2")]:
        url = build_nomads_url(cycle_utc, fh)
        grib = GRIB_TMP_AUX.with_suffix(f".f{fh:03d}.grib2")
        try:
            download_grib(url, grib)
            sfc, prof = extract_fields(grib)
            idx = compute_indices(sfc, prof)
            valid_dt = cycle_utc + timedelta(hours=fh)
            valid_date = (valid_dt + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
            outlook.append({
                "date":          valid_date,
                "day_label":     label,
                "CAPE":          round(float(idx.get("CAPE", 0) or 0), 1),
                "K_INDEX":       round(float(idx.get("K_INDEX", 30) or 30), 2),
                "LIFTED_INDEX":  round(float(idx.get("LIFTED_INDEX", 0) or 0), 2),
                "TOTALS_TOTALS": round(float(idx.get("TOTALS_TOTALS", 44) or 44), 2),
                "PRECIP_WATER":  round(float(idx.get("PRECIP_WATER", 0) or 0), 1),
                "gfs_fhour":     fh,
            })
            print(f"  Multiday f{fh:03d} ({label}): CAPE={idx.get('CAPE', 'N/A')} K={idx.get('K_INDEX', 'N/A')}")
        except Exception as e:
            print(f"  Multiday f{fh:03d}: failed — {e}")
        finally:
            try:
                grib.unlink(missing_ok=True)
            except Exception:
                pass
    return outlook


# ── CSV writers ───────────────────────────────────────────────────────────────

def write_upperair_csv(row: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    if OUT_PATH_UA.exists():
        existing = pd.read_csv(OUT_PATH_UA)
        if "slot" in existing.columns:
            existing = existing[~((existing["date"] == row["date"]) &
                                  (existing["slot"] == row["slot"]))]
        else:
            # Old CSV missing slot column — drop today and rewrite fresh
            existing = existing[existing["date"] != row["date"]]
        out = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    else:
        out = pd.DataFrame([row])
    out.to_csv(OUT_PATH_UA, index=False)
    print(f"\n  Upper-air CSV: {OUT_PATH_UA}")


def write_gfs_realtime_csv(row: dict) -> None:
    """Write/update gfs_realtime_43295.csv — this is what forecast_action.py reads."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if OUT_PATH_GFS.exists():
        existing = pd.read_csv(OUT_PATH_GFS)
        if "slot" in existing.columns:
            existing = existing[~((existing["date"] == row["date"]) &
                                  (existing["slot"] == row["slot"]))]
        else:
            # Old CSV missing slot column — drop today and rewrite fresh
            existing = existing[existing["date"] != row["date"]]
        out = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    else:
        out = pd.DataFrame([row])
    # Keep only today's rows to prevent stale data accumulation
    today_str = row["date"]
    out = out[out["date"] == today_str]
    out.to_csv(OUT_PATH_GFS, index=False)
    print(f"  GFS realtime CSV: {OUT_PATH_GFS}")


def write_multiday_json(outlook: list[dict], today_str: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    # Merge with existing, filtering out stale entries
    existing = []
    if OUT_PATH_MULTIDAY.exists():
        try:
            with open(OUT_PATH_MULTIDAY) as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []  # guardian may have written a dict placeholder; reset
            existing = [e for e in existing if str(e.get("date", "")) >= today_str]
        except Exception:
            existing = []

    # Replace dates covered by new outlook
    new_dates = {r["date"] for r in outlook}
    existing = [e for e in existing if e.get("date") not in new_dates]
    combined = sorted(existing + outlook, key=lambda x: x.get("date", ""))

    with open(OUT_PATH_MULTIDAY, "w") as f:
        json.dump(combined, f, indent=2)
    print(f"  Multiday JSON: {OUT_PATH_MULTIDAY} ({len(combined)} days)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, choices=[0, 1, 2, 3], default=None)
    ap.add_argument("--now", type=str, default=None,
                    help="Simulate IST time (YYYY-MM-DD HH:MM)")
    ap.add_argument("--verify", action="store_true",
                    help="Run pygrib cross-check on CAPE")
    ap.add_argument("--keep-grib", action="store_true",
                    help="Keep downloaded GRIB2 file")
    ap.add_argument("--skip-multiday", action="store_true",
                    help="Skip f024/f048 multiday fetch (faster)")
    ap.add_argument("--skip-trange", action="store_true",
                    help="Skip multi-hour TMP fetch for diurnal range")
    args = ap.parse_args()

    utc_now = datetime.now(timezone.utc)
    now_ist = (utc_now.astimezone(IST) if args.now is None
               else datetime.strptime(args.now, "%Y-%m-%d %H:%M").replace(tzinfo=IST))
    today_str = now_ist.strftime("%Y-%m-%d")

    if args.slot is not None:
        slot = args.slot
        slot_start = now_ist.replace(
            hour=SLOT_START_HOURS[args.slot], minute=0, second=0, microsecond=0)
        if slot_start.date() < now_ist.date():
            slot_start += timedelta(days=1)
    else:
        slot, slot_start = next_slot(now_ist)

    cycle_utc, fhour, valid_utc, slot_start_utc = resolve_source(slot_start)
    posting_est  = cycle_utc + timedelta(hours=POST_LATENCY_HOURS)
    buffer_hours = (slot_start_utc - posting_est) / timedelta(hours=1)

    print("=" * 65)
    print("  GFS Fetcher — CSIR Thunderstorm Nowcasting System")
    print("=" * 65)
    print(f"  Now (IST)   : {now_ist:%Y-%m-%d %H:%M}")
    print(f"  Target slot : {slot} ({SLOT_WINDOWS[slot]} IST)")
    print(f"  Source cycle: GFS {cycle_utc:%Y-%m-%d %H}Z f{fhour:03d} (valid {valid_utc:%Y-%m-%d %H}Z)")
    print(f"  Posting est.: {posting_est:%Y-%m-%d %H:%M} UTC  (buffer: {buffer_hours:.1f}h)")
    if buffer_hours < 1:
        print("  ⚠ Buffer under 1h — NOMADS may not have this cycle yet!")

    # ── Step 1: Fetch main slot GRIB ─────────────────────────────────────────
    print("\n  Step 1: Fetch main slot GRIB")
    url = build_nomads_url(cycle_utc, fhour, extra_vars=True)
    try:
        download_grib(url, GRIB_TMP)
    except RuntimeError as _e:
        # Primary cycle not available — try the previous 6Z cycle with a higher forecast hour
        prev_cycle_utc = cycle_utc - timedelta(hours=6)
        prev_fhour = fhour + 6
        print(f"\n  Primary cycle failed ({_e})")
        print(f"  Falling back to previous cycle: {prev_cycle_utc:%Y-%m-%d %H}Z f{prev_fhour:03d}")
        url = build_nomads_url(prev_cycle_utc, prev_fhour, extra_vars=True)
        download_grib(url, GRIB_TMP, max_retries=2)  # Fewer retries on fallback
        cycle_utc = prev_cycle_utc
        fhour = prev_fhour
        valid_utc = cycle_utc + timedelta(hours=fhour)
        print(f"  Using fallback cycle: GFS {cycle_utc:%Y-%m-%d %H}Z f{fhour:03d} (valid {valid_utc:%Y-%m-%d %H}Z)")

    print("\n  Step 2: Parse GRIB2")
    surface_fields, profile = extract_fields(GRIB_TMP)
    print(f"  Surface: {list(surface_fields.keys())}")
    print(f"  Profile levels: {list(profile.keys())}")

    if args.verify:
        print("\n  --- pygrib cross-check ---")
        for name in ("Convective available potential energy",
                     "Convective inhibition",
                     "Precipitable water"):
            val = _pygrib_value(GRIB_TMP, name)
            print(f"  {name}: {val:.2f}" if val is not None else f"  {name}: not found / pygrib unavailable")

    indices = compute_indices(surface_fields, profile)
    qc_flag, qc_cc = assess_cape(indices, GRIB_TMP)

    if qc_flag == "OK":
        print("  CAPE QC: OK")
    elif qc_flag == "SUSPECT_MISMATCH":
        print(f"  CAPE QC: ⚠ SUSPECT_MISMATCH — cfgrib={indices.get('CAPE'):.1f} pygrib={qc_cc:.1f}")
    else:
        print(f"  CAPE QC: {qc_flag} (cross-check={qc_cc})")

    if not args.keep_grib:
        GRIB_TMP.unlink(missing_ok=True)

    # ── Step 3: Multi-hour TMP for diurnal range ──────────────────────────────
    tmp_cols = {}
    if not args.skip_trange:
        print("\n  Step 3: Fetch TMP f006/f012/f018/f024 for diurnal range")
        tmp_cols = fetch_tmp_multiday(cycle_utc)
        print(f"  TMP fetched: {list(tmp_cols.keys())}")
    else:
        print("\n  Step 3: Skipping TMP multi-hour fetch (--skip-trange)")

    # ── Step 4: Write upper-air CSV ───────────────────────────────────────────
    ua_row = {
        "date":                 slot_start.strftime("%Y-%m-%d"),
        "slot":                 slot,
        "ist_window":           SLOT_WINDOWS[slot],
        "valid_time_utc":       valid_utc.strftime("%Y-%m-%d %H:%M"),
        "source_cycle_utc":     cycle_utc.strftime("%Y-%m-%d %H:%M"),
        "source_fhour":         fhour,
        "fetched_at_utc":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        **indices,
        "CAPE_QC_FLAG":         qc_flag,
        "CAPE_PYGRIB_CROSSCHECK": qc_cc if qc_cc is not None else "",
    }
    write_upperair_csv(ua_row)

    # ── Step 5: Write gfs_realtime_43295.csv ─────────────────────────────────
    # This is what forecast_action.py reads — include ERA5-schema fields + TMP columns
    gfs_row = {
        "date":          slot_start.strftime("%Y-%m-%d"),
        "slot":          slot,
        "gfs_cycle":     f"{cycle_utc.strftime('%Y-%m-%d')} {cycle_utc.hour:02d}Z f{fhour:03d}",
        "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        # Stability indices (training schema names)
        "CAPE":          indices.get("CAPE"),
        "CIN":           indices.get("CIN"),
        "K_INDEX":       indices.get("K_INDEX"),
        "LIFTED_INDEX":  indices.get("LIFTED_INDEX"),
        "TOTALS_TOTALS": indices.get("TOTALS_TOTALS"),
        "PRECIP_WATER":  indices.get("PRECIP_WATER"),
        # ERA5-schema wind fields
        "ERA5_u_500hPa": indices.get("ERA5_u_500hPa"),
        "ERA5_v_500hPa": indices.get("ERA5_v_500hPa"),
        "ERA5_u_850hPa": indices.get("ERA5_u_850hPa"),
        "ERA5_v_850hPa": indices.get("ERA5_v_850hPa"),
        "ERA5_u_700hPa": indices.get("ERA5_u_700hPa"),
        "ERA5_v_700hPa": indices.get("ERA5_v_700hPa"),
        # Surface fields
        # ERA5_T2M / ERA5_D2M / ERA5_t_*hPa: training data uses Kelvin — convert back from Celsius
        "ERA5_T2M":  (_c2k(surface_fields.get("t2m_C"))),
        "ERA5_D2M":  (_c2k(surface_fields.get("d2m_C"))),
        "ERA5_U10":  surface_fields.get("u10", np.nan),
        "ERA5_V10":  surface_fields.get("v10", np.nan),
        "ERA5_CAPE": surface_fields.get("cape", np.nan),
        "ERA5_SP":   surface_fields.get("sp_pa", np.nan),
        # Pressure-level T fields — Kelvin (training convention)
        "ERA5_t_500hPa": (_c2k(profile.get(500, {}).get("t_C"))),
        "ERA5_t_700hPa": (_c2k(profile.get(700, {}).get("t_C"))),
        "ERA5_t_850hPa": (_c2k(profile.get(850, {}).get("t_C"))),
        "ERA5_q_500hPa": profile.get(500, {}).get("q", np.nan),
        "ERA5_q_700hPa": profile.get(700, {}).get("q", np.nan),
        "ERA5_q_850hPa": profile.get(850, {}).get("q", np.nan),
        # Rainfall
        "APCP_surface": surface_fields.get("apcp", 0.0),
        # Convective rotation parameters
        "SRH_0_3km":           indices.get("SRH_0_3km"),
        "EHI":                 indices.get("EHI"),
        "BRN":                 indices.get("BRN"),
        "wind_shear_500_850_ms": indices.get("wind_shear_500_850_ms"),
        # Multi-hour TMP for diurnal range (Kelvin)
        **tmp_cols,
    }
    write_gfs_realtime_csv(gfs_row)

    # ── Step 6: Multi-day outlook ─────────────────────────────────────────────
    if not args.skip_multiday:
        print("\n  Step 6: Fetch multi-day outlook (f024/f048)")
        outlook = fetch_multiday_outlook(cycle_utc, today_str)
        # Always call write_multiday_json even on empty outlook so the staleness
        # filter removes entries older than today_str from the JSON.
        # Without this, a failed fetch leaves stale future-day entries forever.
        write_multiday_json(outlook or [], today_str)
        if not outlook:
            print("  ⚠ Multiday fetch returned no data — stale entries cleaned, JSON reset to empty")
    else:
        print("\n  Step 6: Skipping multiday fetch (--skip-multiday)")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print(f"  Slot {slot} | {SLOT_WINDOWS[slot]} IST")
    for k in ("CAPE", "CIN", "K_INDEX", "LIFTED_INDEX", "TOTALS_TOTALS",
              "PRECIP_WATER", "ERA5_u_500hPa", "ERA5_v_500hPa",
              "ERA5_u_850hPa", "ERA5_v_850hPa", "CAPE_QC_FLAG"):
        print(f"  {k}: {ua_row.get(k)}")
    if tmp_cols:
        tmp_k = list(tmp_cols.values())
        tmax_c = round(max(tmp_k) - 273.15, 1)
        tmin_c = round(min(tmp_k) - 273.15, 1)
        print(f"  Diurnal range: Tmax={tmax_c}°C  Tmin={tmin_c}°C")
    print("=" * 65)

    # Force GC before interpreter shutdown to avoid cfgrib/eccodes
    # "double free or corruption" crash during C library teardown
    gc.collect()


if __name__ == "__main__":
    main()
