"""
fetch_himawari_realtime.py
==========================
Fetches Himawari-9 Band 13 (10.4μm IR) from NOAA S3 (anonymous),
crops to VOBL airport 50km radius, computes BT proximity signal,
and saves output JSON files matching fetch_gfs_realtime.py structure.

Output:
  data/himawari_realtime.json   — latest frame
  data/himawari_history.json    — last 6 frames (rolling)

Usage:
  python fetch_himawari_realtime.py

Cron (every 10 min):
  */10 * * * * cd /path/to/csir-repo && python fetch_himawari_realtime.py

Install:
  pip install boto3 requests numpy satpy
"""

import json
import math
import logging
import datetime
import tempfile
import os
from pathlib import Path

import numpy as np
import requests

# ── Logging (same style as fetch_gfs_realtime.py) ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("himawari")

# ── Constants ─────────────────────────────────────────────────────────────────
VOBL_LAT      = 13.1986
VOBL_LON      = 77.7066
RADIUS_KM     = 50.0
THRESHOLD_C   = -40.0          # storm detection threshold

# Himawari-9 fixed-grid (CGMS WMO 2018-05)
SAT_LON   = 140.7
H_SAT     = 35786023.0
R_EQ      = 6378137.0
R_POL     = 6356752.3142
CFAC      = 20466275
LFAC      = 20466275
COFF      = 5500.5
LOFF      = 5500.5

# Full-disk grid: 11000×11000, 10 segments × 1100 rows
ROWS_PER_SEG = 1100

# VOBL on the full-disk grid (pre-computed)
VOBL_COL  = 2524
VOBL_ROW  = 4726

# Segments that cover VOBL's 50km box:
#   seg 4 → rows 3300–4399  (VOBL top margin)
#   seg 5 → rows 4400–5499  (VOBL centre)
#   seg 6 → rows 5500–6599  (VOBL bottom margin)
SEGMENTS  = [4, 5, 6]

# Per-segment crop columns (same for all segs — VOBL is narrow E-W)
CROP_COL_MIN = 2505   # ~20 px west of VOBL (~40km)
CROP_COL_MAX = 2545   # ~20 px east of VOBL (~40km)

# S3
S3_BUCKET = "noaa-himawari9"
S3_REGION = "us-east-1"
S3_PREFIX = "AHI-L1b-FLDK"

# Output
OUT_DIR   = Path("data")
OUT_DIR.mkdir(exist_ok=True)
OUT_FILE  = OUT_DIR / "himawari_realtime.json"
HIST_FILE = OUT_DIR / "himawari_history.json"
KEEP_FRAMES = 6


# ── Coordinate helpers ────────────────────────────────────────────────────────

def latlon_to_himawari(lat_deg, lon_deg):
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sat = math.radians(SAT_LON)
    c_lat = math.atan((R_POL**2 / R_EQ**2) * math.tan(lat))
    rl = R_POL / math.sqrt(
        1.0 - ((R_EQ**2 - R_POL**2) / R_EQ**2) * math.cos(c_lat)**2)
    r1 = H_SAT - rl * math.cos(c_lat) * math.cos(lon - sat)
    r2 = -rl * math.cos(c_lat) * math.sin(lon - sat)
    r3 = rl * math.sin(c_lat)
    rn = math.sqrt(r1**2 + r2**2 + r3**2)
    ang_x = math.atan(-r2 / r1)
    ang_y = math.asin(-r3 / rn)
    col = COFF + math.degrees(ang_x) * (2**-16) * CFAC
    row = LOFF + math.degrees(ang_y) * (2**-16) * LFAC
    return col, row


def himawari_to_latlon(col, row):
    sat = math.radians(SAT_LON)
    x   = math.radians((col - COFF) / ((2**-16) * CFAC))
    y   = math.radians((row - LOFF) / ((2**-16) * LFAC))
    sd  = math.sqrt(
        (H_SAT * math.cos(x) * math.cos(y))**2
        - (math.cos(y)**2 + (R_EQ**2 / R_POL**2) * math.sin(y)**2)
        * (H_SAT**2 - R_EQ**2))
    sn  = (H_SAT * math.cos(x) * math.cos(y) - sd) / (
           math.cos(y)**2 + (R_EQ**2 / R_POL**2) * math.sin(y)**2)
    s1  = H_SAT - sn * math.cos(x) * math.cos(y)
    s2  = sn * math.sin(x) * math.cos(y)
    s3  = -sn * math.sin(y)
    sxy = math.sqrt(s1**2 + s2**2)
    lon = math.degrees(math.atan(s2 / s1)) + SAT_LON
    lat = math.degrees(math.atan((R_EQ**2 / R_POL**2) * (s3 / sxy)))
    return lat, lon


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2)**2)
    return R * 2 * math.asin(math.sqrt(a))


# ── S3 fetch ──────────────────────────────────────────────────────────────────

def build_s3_key(scene_dt: datetime.datetime, seg: int) -> str:
    """
    AHI-L1b-FLDK/YYYY/MM/DD/HHmm/
      HS_H09_YYYYMMDD_HHmm_B13_FLDK_R20_S{seg:02d}10.DAT.bz2
    """
    folder = scene_dt.strftime("%Y/%m/%d/%H%M")
    fname  = scene_dt.strftime(
        f"HS_H09_%Y%m%d_%H%M_B13_FLDK_R20_S{seg:02d}10.DAT.bz2")
    return f"{S3_PREFIX}/{folder}/{fname}"


def fetch_segments_s3(scene_dt: datetime.datetime):
    try:
        import boto3, tempfile, os
        from botocore import UNSIGNED
        from botocore.config import Config
        from satpy import Scene
        s3 = boto3.client(
            "s3", region_name=S3_REGION,
            config=Config(signature_version=UNSIGNED,
                          connect_timeout=15, read_timeout=180))
        tmpdir = tempfile.mkdtemp()
        files = []
        for seg in SEGMENTS:
            key = build_s3_key(scene_dt, seg)
            local = os.path.join(tmpdir, os.path.basename(key))
            log.info(f"  S3 GET s3://{S3_BUCKET}/{key}")
            try:
                s3.download_file(S3_BUCKET, key, local)
                size = os.path.getsize(local)
                log.info(f"  ✓ seg {seg}  {size/1e6:.1f} MB")
                files.append(local)
            except Exception as e:
                log.warning(f"  seg {seg}: {e}")
        if not files:
            return None
        scn = Scene(filenames=files, reader='ahi_hsd')
        scn.load(['B13'])
        bt = scn['B13'].values
        lons, lats = scn['B13'].attrs['area'].get_lonlats()
        return {"bt": bt, "lons": lons, "lats": lats}
    except Exception as e:
        log.error(f"S3 fetch error: {e}")
        return None


def fetch_segments_jaxa(scene_dt: datetime.datetime):
    """
    JAXA P-Tree HTTP fallback — downloads to tempdir, parses with satpy.
    Returns same {"bt", "lons", "lats"} format as fetch_segments_s3.
    """
    import requests as req
    from satpy import Scene

    BASE = "https://www.eorc.jaxa.jp/ptree/userspace/FULL/GEO/HIMAWARI/B13/FLDK"
    tmpdir = tempfile.mkdtemp()
    files  = []

    for seg in SEGMENTS:
        dpath = scene_dt.strftime("%Y/%m/%d/%H")
        fname = scene_dt.strftime(
            f"HS_H09_%Y%m%d_%H%M_B13_FLDK_R20_S{seg:02d}10.DAT.bz2")
        url   = f"{BASE}/{dpath}/{fname}"
        local = os.path.join(tmpdir, fname)
        log.info(f"  JAXA GET {url}")
        try:
            r = req.get(url, timeout=180)
            if r.status_code == 200:
                with open(local, "wb") as f:
                    f.write(r.content)
                log.info(f"  ✓ seg {seg}  {len(r.content)/1e6:.1f} MB")
                files.append(local)
            elif r.status_code == 404:
                log.warning(f"  seg {seg}: 404")
            else:
                log.warning(f"  seg {seg}: HTTP {r.status_code}")
        except Exception as e:
            log.warning(f"  JAXA seg {seg}: {e}")

    if not files:
        return None

    try:
        scn = Scene(filenames=files, reader='ahi_hsd')
        scn.load(['B13'])
        bt   = scn['B13'].values
        lons, lats = scn['B13'].attrs['area'].get_lonlats()
        return {"bt": bt, "lons": lons, "lats": lats}
    except Exception as e:
        log.error(f"  JAXA satpy parse error: {e}")
        return None


# ── Analysis (works on satpy lat/lon output) ──────────────────────────────────

def analyse(result: dict) -> dict:
    """
    Compute proximity signal from satpy output dict {"bt", "lons", "lats"}.
    bt   : 2D float array (K) — full stitched scene from satpy
    lons : 2D float array (degrees) — same shape as bt
    lats : 2D float array (degrees) — same shape as bt

    Uses real haversine distances from the satpy lat/lon grid —
    no pixel-scale approximations.
    """
    bt   = result["bt"].astype(np.float32)
    lons = result["lons"]
    lats = result["lats"]

    # Haversine distance from every pixel to VOBL (vectorised)
    dlat = np.radians(lats - VOBL_LAT)
    dlon = np.radians(lons - VOBL_LON)
    a    = (np.sin(dlat / 2)**2
            + np.cos(np.radians(VOBL_LAT)) * np.cos(np.radians(lats))
            * np.sin(dlon / 2)**2)
    dist_km = 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))

    # VOBL pixel — nearest grid point to airport coords
    vobl_idx  = np.unravel_index(np.nanargmin(dist_km), dist_km.shape)
    vobl_bt_k = float(bt[vobl_idx]) if not np.isnan(bt[vobl_idx]) else None
    vobl_bt_c = round(vobl_bt_k - 273.15, 2) if vobl_bt_k is not None else None

    # 50km mask
    mask_50 = dist_km <= RADIUS_KM
    bt_50   = bt.copy()
    bt_50[~mask_50] = np.nan

    valid     = bt_50[~np.isnan(bt_50)]
    min_bt_k  = float(np.nanmin(bt_50))  if len(valid) else None
    mean_bt_k = float(np.nanmean(bt_50)) if len(valid) else None
    min_bt_c  = round(min_bt_k  - 273.15, 2) if min_bt_k  is not None else None
    mean_bt_c = round(mean_bt_k - 273.15, 2) if mean_bt_k is not None else None

    # Cold pixels within 50km
    thresh_k      = THRESHOLD_C + 273.15
    cold_mask     = mask_50 & (bt < thresh_k) & ~np.isnan(bt)
    cold_count    = int(np.sum(cold_mask))
    storm_detected = cold_count > 0

    # Distance to nearest cold pixel
    nearest_km = None
    if storm_detected:
        nearest_km = round(float(dist_km[cold_mask].min()), 2)

    return {
        "vobl_bt_celsius":       vobl_bt_c,
        "min_bt_50km":           min_bt_c,
        "mean_bt_50km":          mean_bt_c,
        "cold_pixels_count":     cold_count,
        "storm_detected":        storm_detected,
        "nearest_pixel_dist_km": nearest_km,
        "threshold_celsius":     THRESHOLD_C,
    }


# ── Scene time resolution ─────────────────────────────────────────────────────

def latest_scene_dt(now_utc: datetime.datetime) -> datetime.datetime:
    """Round down to nearest 10-min slot, allow 5-min posting lag."""
    # Back off 5 min to let S3 finish posting before we fetch
    t     = now_utc - datetime.timedelta(minutes=5)
    minute = (t.minute // 10) * 10
    return t.replace(minute=minute, second=0, microsecond=0)


def try_scene(scene_dt: datetime.datetime):
    """Try S3, then JAXA. Returns {"bt", "lons", "lats"} or None."""
    log.info(f"\nFetching scene: {scene_dt.strftime('%Y-%m-%d %H:%M UTC')}")
    result = fetch_segments_s3(scene_dt)
    if result is None:
        log.info("S3 failed — trying JAXA fallback...")
        result = fetch_segments_jaxa(scene_dt)
    return result


# ── Output helpers ────────────────────────────────────────────────────────────

def build_output(signal: dict, scene_dt: datetime.datetime) -> dict:
    ist_dt = scene_dt + datetime.timedelta(hours=5, minutes=30)
    return {
        "timestamp_utc":          scene_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "timestamp_ist":          ist_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "vobl_bt_celsius":        signal["vobl_bt_celsius"],
        "min_bt_50km":            signal["min_bt_50km"],
        "mean_bt_50km":           signal["mean_bt_50km"],
        "cold_pixels_count":      signal["cold_pixels_count"],
        "storm_detected":         signal["storm_detected"],
        "nearest_pixel_dist_km":  signal["nearest_pixel_dist_km"],
        "threshold_celsius":      signal["threshold_celsius"],
        "data_source":            "Himawari-9 Band 13 (10.4um) via NOAA AWS S3",
    }


def compute_bt_trend(new_min_bt, new_ts_str: str, history: list):
    """
    bt_trend_1h: change in min_bt_50km vs the frame closest to 60 min ago.
    Negative = cooling = anvil/storm growing toward airport.
    Returns None if history is too sparse or gap > 75 min.
    """
    if new_min_bt is None or not history:
        return None
    try:
        t_now = datetime.datetime.fromisoformat(new_ts_str)
    except Exception:
        return None
    target = t_now - datetime.timedelta(minutes=60)
    best, best_gap = None, float("inf")
    for fr in history:
        ts = fr.get("timestamp_utc", "")
        try:
            ft = datetime.datetime.fromisoformat(ts)
            gap = abs((ft - target).total_seconds())
            if gap < best_gap:
                best_gap, best = gap, fr
        except Exception:
            continue
    if best is None or best_gap > 4500:   # >75-min gap
        return None
    old_bt = best.get("min_bt_50km")
    if old_bt is None:
        return None
    return round(float(new_min_bt) - float(old_bt), 2)


def save_outputs(record: dict):
    # Load existing history BEFORE appending (to compute trend from prior frames)
    history = []
    if HIST_FILE.exists():
        try:
            with open(HIST_FILE) as f:
                history = json.load(f)
        except Exception:
            history = []

    # Compute bt_trend_1h and inject into record
    bt_trend = compute_bt_trend(
        record.get("min_bt_50km"),
        record.get("timestamp_utc", ""),
        history,
    )
    record["bt_trend_1h"] = bt_trend
    trend_str = f"{bt_trend:+.2f}°C/h" if bt_trend is not None else "N/A (insufficient history)"
    log.info(f"  bt_trend_1h = {trend_str}  "
             f"({'COOLING ❄' if bt_trend is not None and bt_trend < -2 else 'WARMING' if bt_trend is not None and bt_trend > 2 else 'STABLE'})")

    # Latest frame (with trend)
    with open(OUT_FILE, "w") as f:
        json.dump(record, f, indent=2)
    log.info(f"Saved → {OUT_FILE}")

    # Append and trim history
    history.append(record)
    history = history[-KEEP_FRAMES:]   # keep last 6

    with open(HIST_FILE, "w") as f:
        json.dump(history, f, indent=2)
    log.info(f"Saved → {HIST_FILE}  ({len(history)} frames)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("  fetch_himawari_realtime.py — VOBL Storm Proximity")
    log.info("=" * 60)

    now_utc  = datetime.datetime.utcnow()
    log.info(f"UTC now : {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")

    scene_dt = latest_scene_dt(now_utc)
    log.info(f"Target  : {scene_dt.strftime('%Y-%m-%d %H:%M UTC')} "
             f"(segs {SEGMENTS})")

    # Try latest slot, fall back one slot if needed
    result = try_scene(scene_dt)
    if result is None:
        fallback = scene_dt - datetime.timedelta(minutes=10)
        log.warning(f"Latest slot empty — trying {fallback.strftime('%H:%M')} UTC")
        result = try_scene(fallback)
        if result is None:
            # Both S3 and JAXA unavailable (common in CI environments).
            # Write a "data unavailable" placeholder with today's timestamp so
            # himawari_realtime.json never stays stale from a previous run.
            log.error(
                "\n✗ Both sources failed for both slots.\n"
                "  Writing placeholder record so himawari_realtime.json stays current."
            )
            now_utc = datetime.datetime.utcnow()
            ist_dt  = now_utc + datetime.timedelta(hours=5, minutes=30)
            placeholder = {
                "timestamp_utc":         now_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                "timestamp_ist":         ist_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "vobl_bt_celsius":       None,
                "min_bt_50km":           None,
                "mean_bt_50km":          None,
                "cold_pixels_count":     0,
                "storm_detected":        False,
                "nearest_pixel_dist_km": None,
                "threshold_celsius":     THRESHOLD_C,
                "data_source":           "Himawari-9 — UNAVAILABLE (S3/JAXA unreachable)",
                "bt_trend_1h":           None,
            }
            OUT_DIR.mkdir(exist_ok=True)
            with open(OUT_FILE, "w") as f:
                json.dump(placeholder, f, indent=2)
            log.info(f"Placeholder written → {OUT_FILE}")
            return 1

    bt = result["bt"]
    log.info(f"\nBT shape: {bt.shape}  "
             f"range [{np.nanmin(bt):.1f}, {np.nanmax(bt):.1f}] K")

    signal = analyse(result)
    record = build_output(signal, scene_dt)

    log.info("\n── Output ──")
    for k, v in record.items():
        log.info(f"  {k:<30} {v}")

    save_outputs(record)

    log.info(f"\n✓ Done — storm_detected={record['storm_detected']}  "
             f"min_bt={record['min_bt_50km']}°C  "
             f"nearest={record['nearest_pixel_dist_km']} km")
    return 0


def run_diag():
    import socket
    print("\n=== Network Diagnostic ===\n")
    for name, host in [
        ("NOAA S3",  "noaa-himawari9.s3.amazonaws.com"),
        ("JAXA",     "www.eorc.jaxa.jp"),
    ]:
        try:
            socket.create_connection((host, 443), timeout=8)
            print(f"  ✓ {name:<10} {host} — REACHABLE")
        except Exception as e:
            print(f"  ✗ {name:<10} {host} — BLOCKED ({e})")
    print()


if __name__ == "__main__":
    import sys
    if "--diag" in sys.argv:
        run_diag()
    else:
        sys.exit(main())