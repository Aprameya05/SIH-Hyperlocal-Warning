#!/usr/bin/env python3
"""
INSAT-3D / 3DR Data Fetcher -- MOSDAC Integration
Fetches live water vapor (WV) channel data for Integrated Water Vapor (IWV) tracking.

Status: Integration code ready. Requires MOSDAC institutional credentials.
        Register at https://mosdac.gov.in to obtain API access.
        Once credentials are available, set MOSDAC_USER and MOSDAC_PASS
        as environment variables (or GitHub Secrets) and this runs as-is.

What this replaces:
  - Current system uses GFS PWAT (model-derived) as IWV proxy
  - INSAT-3DR WV channel gives satellite-observed IWV at ~4km, 30-minute updates
  - This is the highest-priority upgrade per the SIH problem statement

Data source: MOSDAC FTP / HTTPS server
  ftp://ftp.mosdac.gov.in/INSAT3D/
  Product: 3DIMG_<date>_<time>_L1C_ASIA_MER_BIMG.h5  (HDF5)
  WV channel: TIR2 (6.8 micron water vapor absorption band)
"""

import os
import sys
import json
import math
import tempfile
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("numpy not installed -- run: pip install numpy")
    sys.exit(1)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

MOSDAC_USER = os.environ.get("MOSDAC_USER", "")
MOSDAC_PASS = os.environ.get("MOSDAC_PASS", "")

# MOSDAC HTTPS API endpoint (subject to change -- check mosdac.gov.in docs)
MOSDAC_BASE = "https://mosdac.gov.in/live"

BOUNDS = {"S": 6, "N": 37, "W": 68, "E": 98}

# Output path (written next to pan_india_grid.json)
OUT_DIR  = Path(__file__).parent.parent / "data"
OUT_FILE = OUT_DIR / "insat3d_iwv.json"


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------

def make_auth_opener():
    """Build a urllib opener with HTTP Basic auth for MOSDAC."""
    if not MOSDAC_USER or not MOSDAC_PASS:
        raise RuntimeError(
            "MOSDAC_USER and MOSDAC_PASS environment variables not set.\n"
            "Register at https://mosdac.gov.in to get access."
        )
    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, MOSDAC_BASE, MOSDAC_USER, MOSDAC_PASS)
    auth_handler = urllib.request.HTTPBasicAuthHandler(password_mgr)
    return urllib.request.build_opener(auth_handler)


# ---------------------------------------------------------------------------
# PRODUCT DISCOVERY
# ---------------------------------------------------------------------------

def latest_insat3d_product_url() -> str:
    """
    Return the URL of the most recent INSAT-3DR full-disk L1C HDF5 file.
    MOSDAC updates every 30 minutes; we pick the most recent slot.
    """
    now = datetime.now(timezone.utc)
    # Round down to the nearest 30-min slot, subtract one slot for safety
    slot_minutes = (now.minute // 30) * 30
    slot_time = now.replace(minute=slot_minutes, second=0, microsecond=0) - timedelta(minutes=30)
    date_str = slot_time.strftime("%d%b%Y").upper()   # e.g. 17SEP2026
    time_str = slot_time.strftime("%H%M")             # e.g. 0900

    # MOSDAC filename pattern (may vary by product version):
    filename = f"3DIMG_{date_str}_{time_str}_L1C_ASIA_MER_BIMG.h5"
    url = f"{MOSDAC_BASE}/INSAT3D/{date_str}/{filename}"
    return url, filename


# ---------------------------------------------------------------------------
# HDF5 PARSING
# ---------------------------------------------------------------------------

def extract_iwv_from_hdf5(h5_path: Path) -> dict:
    """
    Parse INSAT-3D L1C HDF5 file and extract a lat-lon grid of
    brightness temperature (TIR2 WV channel) as an IWV proxy.

    Returns: {(lat, lon): iwv_proxy_value}
    Higher TIR2 brightness temp = drier atmosphere = lower IWV.
    Convention: invert and scale so output is comparable to PWAT in mm.
    """
    try:
        import h5py
    except ImportError:
        print("h5py not installed -- run: pip install h5py")
        sys.exit(1)

    iwv_map = {}
    with h5py.File(str(h5_path), "r") as f:
        # Typical INSAT-3D L1C dataset path -- may vary by file version
        # Check f.keys() and f['IMG_TIR2'].attrs for exact path
        tir2_key = "IMG_TIR2"
        if tir2_key not in f:
            available = list(f.keys())
            print(f"  TIR2 key not found. Available: {available}")
            # Try first numeric dataset as fallback
            for k in available:
                if hasattr(f[k], "shape") and len(f[k].shape) == 2:
                    tir2_key = k
                    print(f"  Using {k} as fallback")
                    break

        ds = f[tir2_key]
        data = ds[:]  # 2D array

        # Geolocation: INSAT-3D L1C stores lat/lon as separate datasets
        lat_key = "Latitude"
        lon_key = "Longitude"
        if lat_key not in f or lon_key not in f:
            # Fall back to computing from header attributes
            lats_1d = np.linspace(BOUNDS["N"], BOUNDS["S"], data.shape[0])
            lons_1d = np.linspace(BOUNDS["W"], BOUNDS["E"], data.shape[1])
            lats_2d, lons_2d = np.meshgrid(lats_1d, lons_1d, indexing="ij")
        else:
            lats_2d = f[lat_key][:]
            lons_2d = f[lon_key][:]

        # Scale factor / offset from attributes
        scale = float(ds.attrs.get("scale_factor", 0.01))
        offset = float(ds.attrs.get("add_offset", 0.0))
        fill = float(ds.attrs.get("_FillValue", -9999))

        # Convert digital number to brightness temperature (K)
        bt = data.astype(float) * scale + offset

        # Subset to India bounding box and downsample to 0.25-degree grid
        step = 0.25
        lats_grid = np.arange(BOUNDS["S"], BOUNDS["N"] + step * 0.5, step)
        lons_grid = np.arange(BOUNDS["W"], BOUNDS["E"] + step * 0.5, step)

        for target_lat in lats_grid:
            for target_lon in lons_grid:
                # Find nearest pixel
                dist = np.abs(lats_2d - target_lat) + np.abs(lons_2d - target_lon)
                li, lj = np.unravel_index(np.argmin(dist), dist.shape)
                bt_val = bt[li, lj]
                if bt_val == fill or np.isnan(bt_val) or bt_val < 100:
                    continue
                # Convert WV brightness temperature to approximate IWV proxy (mm)
                # Cold WV channel BT = deep moist layer = high IWV
                # Approximate: IWV ~ max(0, (270 - BT) * 1.8)
                iwv = max(0.0, (270.0 - bt_val) * 1.8)
                iwv_map[(round(float(target_lat), 2), round(float(target_lon), 2))] = round(iwv, 2)

    return iwv_map


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def run():
    if not MOSDAC_USER:
        print("MOSDAC credentials not set. Set MOSDAC_USER and MOSDAC_PASS.")
        print("Falling back to GFS PWAT (already handled by pipeline.py).")
        return

    url, filename = latest_insat3d_product_url()
    print(f"Fetching INSAT-3DR product: {filename}")
    print(f"URL: {url}")

    opener = make_auth_opener()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / filename
        try:
            with opener.open(url, timeout=120) as resp:
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
            size_kb = dest.stat().st_size // 1024
            print(f"  Downloaded {size_kb} KB")
        except Exception as e:
            print(f"  Download failed: {e}")
            print("  Check credentials and product URL format.")
            return

        print("  Extracting IWV grid from HDF5...")
        iwv_map = extract_iwv_from_hdf5(dest)

    if not iwv_map:
        print("  No IWV data extracted -- check HDF5 structure")
        return

    cells = [
        {"lat": lat, "lon": lon, "iwv_mm": iwv}
        for (lat, lon), iwv in sorted(iwv_map.items())
    ]

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "INSAT-3DR TIR2 (6.8 micron WV channel) via MOSDAC",
        "product": filename,
        "note": "IWV proxy derived from WV brightness temperature. Not calibrated PWAT.",
        "n_cells": len(cells),
        "cells": cells,
    }
    OUT_FILE.write_text(json.dumps(output, separators=(",", ":")))
    print(f"  Written {len(cells)} cells -> {OUT_FILE}")


if __name__ == "__main__":
    run()
