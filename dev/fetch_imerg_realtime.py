"""
fetch_imerg_realtime.py
=======================
Step 5 — GPM IMERG Early Run (30-min, 0.1° resolution) corroboration layer.

Fetches the latest available IMERG Early half-hourly precipitation rate
for the 50km bounding box around VOBL airport and writes a JSON summary
that the FastAPI /radar/proximity endpoint merges with the Himawari signal.

Latency: IMERG Early is available ~4 hours after observation time.
So at 15:00 IST we can fetch the 11:00 IST scene — useful as a
lagged corroboration, not real-time. This is clearly flagged in output.

VOBL bounding box (0.1° grid):
  lat: 12.7° – 13.7° N  (10 cells)
  lon: 77.2° – 78.2° E  (10 cells)

Sources:
  1. NASA GES DISC OPeNDAP (requires free Earthdata account)
     → Instructions to create token printed on first run
  2. AWS Open Data mirror (no-auth, when available)

Output:
  data/imerg_realtime/imerg_vobl_YYYYMMDD_HHmm.json
  data/imerg_realtime/imerg_latest.json

Install:
  pip install requests numpy h5py

Usage:
  python fetch_imerg_realtime.py
  python fetch_imerg_realtime.py --setup-auth    # print Earthdata token instructions
"""

import os, sys, json, math, logging, datetime, argparse, traceback
from pathlib import Path

import numpy as np
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("imerg")

# ── CONFIG ────────────────────────────────────────────────────────────────────
VOBL_LAT  = 13.1986
VOBL_LON  = 77.7066
RADIUS_KM = 50.0

# IMERG 0.1° grid bounding box (generous ±0.6°)
LAT_MIN = 12.5;  LAT_MAX = 13.8
LON_MIN = 77.0;  LON_MAX = 78.3

# IMERG Early latency: ~4 hours
# We look back up to 5 hours to find the latest available scene
MAX_LOOKBACK_HOURS = 5

# Earthdata token — set via env var or .earthdata_token file
EARTHDATA_TOKEN_ENV  = "EARTHDATA_TOKEN"
EARTHDATA_TOKEN_FILE = Path(".earthdata_token")

OUT_DIR = Path("data") / "imerg_realtime"
OUT_DIR.mkdir(parents=True, exist_ok=True)
KEEP_FRAMES = 12   # 6 hours of 30-min frames


# ─────────────────────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────────────────────

def get_earthdata_token() -> str | None:
    """Return Earthdata bearer token from env or file."""
    token = os.environ.get(EARTHDATA_TOKEN_ENV, "").strip()
    if token:
        return token
    if EARTHDATA_TOKEN_FILE.exists():
        token = EARTHDATA_TOKEN_FILE.read_text().strip()
        if token:
            return token
    return None


def print_auth_instructions():
    print("""
=== Earthdata Token Setup (one-time, free) ===

1. Create a free account at https://urs.earthdata.nasa.gov/
2. Log in → Profile → Generate Token
3. Copy the token and do ONE of:

   Option A — environment variable (recommended for production):
     set EARTHDATA_TOKEN=your_token_here      (Windows)
     export EARTHDATA_TOKEN=your_token_here   (Linux/Mac)

   Option B — token file (simple):
     echo your_token_here > .earthdata_token
     (in the same folder as this script)

Then re-run:  python fetch_imerg_realtime.py
""")


# ─────────────────────────────────────────────────────────────────────────────
# IMERG URL BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_imerg_url(dt: datetime.datetime) -> str:
    """
    Build GES DISC OPeNDAP URL for IMERG Early V07 half-hourly file.

    File naming (confirmed from GES DISC):
      3B-HHR-E.MS.MRG.3IMERG.YYYYMMDD-SHHMMSS-EHHMMSS.mmmm.V07B.HDF5
      where mmmm = minutes since midnight

    OPeNDAP endpoint:
      https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/
        GPM_3IMERGHHE.07/YYYY/DDD/
          3B-HHR-E.MS.MRG.3IMERG.YYYYMMDD-SHHMMSS-EHHMMSS.mmmm.V07B.HDF5
    """
    # Snap to nearest 30-min window
    minute = (dt.minute // 30) * 30
    scene  = dt.replace(minute=minute, second=0, microsecond=0)

    start_str   = scene.strftime("%H%M%S")
    end_min     = (scene + datetime.timedelta(minutes=29, seconds=59))
    end_str     = end_min.strftime("%H%M%S")
    mmmm        = scene.hour * 60 + scene.minute
    doy         = scene.timetuple().tm_yday

    fname = (f"3B-HHR-E.MS.MRG.3IMERG."
             f"{scene.strftime('%Y%m%d')}-S{start_str}-E{end_str}"
             f".{mmmm:04d}.V07B.HDF5")

    base = "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGHHE.07"
    url  = f"{base}/{scene.year}/{doy:03d}/{fname}"
    return url, scene, fname


def build_aws_url(dt: datetime.datetime) -> tuple:
    """
    AWS Open Data mirror of IMERG — public, no auth needed.
    Bucket: s3://gesdisc-cumulus-prod-protected (requires token)
    Public HTTP mirror: https://opendap.earthdata.nasa.gov (also needs auth)

    NOTE: As of 2026, IMERG has no fully no-auth public mirror.
    This function returns the GES DISC direct URL as fallback.
    """
    return build_imerg_url(dt)


# ─────────────────────────────────────────────────────────────────────────────
# FETCH + PARSE HDF5
# ─────────────────────────────────────────────────────────────────────────────

def fetch_imerg_hdf5(token: str, now_utc: datetime.datetime) -> dict | None:
    """
    Fetch IMERG HDF5 via OPeNDAP, extract the VOBL bounding box,
    return a dict of precipitation stats.
    """
    try:
        import h5py, io
    except ImportError:
        log.error("h5py not installed: pip install h5py")
        return None

    headers = {"Authorization": f"Bearer {token}"}
    session = requests.Session()
    session.headers.update(headers)

    for lookback_hours in range(MAX_LOOKBACK_HOURS * 2):
        target = now_utc - datetime.timedelta(minutes=lookback_hours * 30)
        url, scene_dt, fname = build_imerg_url(target)
        log.info(f"  IMERG → {fname}")

        try:
            r = session.get(url, timeout=60, allow_redirects=True)
            if r.status_code == 200:
                log.info(f"  IMERG HIT {len(r.content)/1e6:.1f} MB")
                f = h5py.File(io.BytesIO(r.content), "r")

                # IMERG HDF5 structure:
                # /Grid/precipitationCal  shape (1, 3600, 1800)  [lon, lat]
                # /Grid/lat   shape (1800,)  -89.95 to 89.95
                # /Grid/lon   shape (3600,)  -179.95 to 179.95
                lat  = f["Grid/lat"][:]
                lon  = f["Grid/lon"][:]
                prec = f["Grid/precipitationCal"][0, :, :]   # (3600, 1800)

                # Find bounding box indices
                lat_mask = (lat >= LAT_MIN) & (lat <= LAT_MAX)
                lon_mask = (lon >= LON_MIN) & (lon <= LON_MAX)
                lat_idx  = np.where(lat_mask)[0]
                lon_idx  = np.where(lon_mask)[0]

                if len(lat_idx) == 0 or len(lon_idx) == 0:
                    log.error("  IMERG: bounding box not found in grid")
                    return None

                # prec is [lon, lat] — slice accordingly
                prec_box = prec[
                    lon_idx[0]:lon_idx[-1]+1,
                    lat_idx[0]:lat_idx[-1]+1
                ]   # shape (n_lon, n_lat)

                # Replace fill values
                prec_box = prec_box.astype(np.float32)
                prec_box[prec_box < 0] = np.nan

                # Stats
                n_valid    = int(np.sum(~np.isnan(prec_box)))
                mean_prec  = float(np.nanmean(prec_box)) if n_valid else np.nan
                max_prec   = float(np.nanmax(prec_box))  if n_valid else np.nan
                p90_prec   = float(np.nanpercentile(prec_box, 90)) if n_valid else np.nan
                n_heavy    = int(np.sum(prec_box > 10.0))  # >10 mm/hr = heavy
                n_extreme  = int(np.sum(prec_box > 30.0))  # >30 mm/hr = extreme

                # Latency note
                latency_h = (now_utc - scene_dt).total_seconds() / 3600

                ist_dt = scene_dt + datetime.timedelta(hours=5, minutes=30)

                result = {
                    "scene_time_utc":  scene_dt.strftime("%Y-%m-%dT%H:%M:00Z"),
                    "scene_time_ist":  ist_dt.strftime("%Y-%m-%d %H:%M IST"),
                    "latency_hours":   round(latency_h, 1),
                    "data_source":     "GPM IMERG Early V07B (NASA GES DISC)",
                    "resolution_deg":  0.1,
                    "box_lat":         [LAT_MIN, LAT_MAX],
                    "box_lon":         [LON_MIN, LON_MAX],
                    "n_cells":         int(prec_box.size),
                    "n_valid_cells":   n_valid,
                    "precip_mean_mm_hr":  round(mean_prec, 3) if not np.isnan(mean_prec) else None,
                    "precip_max_mm_hr":   round(max_prec, 3)  if not np.isnan(max_prec) else None,
                    "precip_p90_mm_hr":   round(p90_prec, 3)  if not np.isnan(p90_prec) else None,
                    "n_cells_heavy_10mmhr":   n_heavy,
                    "n_cells_extreme_30mmhr":  n_extreme,
                    "heavy_area_km2":     round(n_heavy   * 100, 1),  # 0.1° ≈ 10×10 km
                    "extreme_area_km2":   round(n_extreme * 100, 1),
                    "convection_flag":    1 if n_extreme > 0 else (1 if n_heavy > 2 else 0),
                    "fetched_at_utc": datetime.datetime.now(
                        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }

                log.info(f"  Precip max={max_prec:.1f} mm/hr  heavy_cells={n_heavy}  extreme={n_extreme}")
                return result

            elif r.status_code == 404:
                log.debug(f"  IMERG 404: {fname}")
                continue
            elif r.status_code == 401:
                log.error("  IMERG 401 — invalid or expired Earthdata token")
                print_auth_instructions()
                return None
            else:
                log.warning(f"  IMERG HTTP {r.status_code}")
                continue

        except Exception as e:
            log.warning(f"  IMERG attempt failed: {e}")
            continue

    log.warning("  IMERG: no scene found in lookback window")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# SAVE
# ─────────────────────────────────────────────────────────────────────────────

def save_imerg(result: dict):
    # Parse scene time for timestamp
    scene_str = result["scene_time_utc"].replace(":", "").replace("-", "")[:13]
    ts = scene_str[:8] + "_" + scene_str[9:13]

    jp = OUT_DIR / f"imerg_vobl_{ts}.json"
    with open(jp, "w") as f:
        json.dump(result, f, indent=2)

    with open(OUT_DIR / "imerg_latest.json", "w") as f:
        json.dump(result, f, indent=2)

    with open(OUT_DIR / "imerg_vobl_log.jsonl", "a") as f:
        f.write(json.dumps(result) + "\n")

    # Prune
    jsons = sorted(OUT_DIR.glob("imerg_vobl_2*.json"))
    if len(jsons) > KEEP_FRAMES:
        for old in jsons[:-KEEP_FRAMES]:
            old.unlink()

    log.info(f"  Saved → {jp}")
    log.info(f"  Latest→ {OUT_DIR}/imerg_latest.json")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup-auth", action="store_true",
                        help="Print Earthdata token setup instructions and exit")
    args = parser.parse_args()

    if args.setup_auth:
        print_auth_instructions()
        return 0

    log.info("=" * 65)
    log.info("  GPM IMERG Early — VOBL Precipitation Corroboration (Step 5)")
    log.info("=" * 65)

    now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    log.info(f"UTC now : {now_utc.strftime('%Y-%m-%d %H:%M')}")

    token = get_earthdata_token()
    if not token:
        log.error(
            "No Earthdata token found.\n"
            "Run:  python fetch_imerg_realtime.py --setup-auth\n"
            "to get setup instructions (free NASA account, 2 min)."
        )
        return 1

    log.info(f"Token   : {token[:8]}... (from "
             f"{'env' if os.environ.get(EARTHDATA_TOKEN_ENV) else 'file'})")

    result = fetch_imerg_hdf5(token, now_utc)
    if result is None:
        log.error("IMERG fetch failed — check token and network.")
        return 1

    log.info("\n── Results ──")
    for k, v in result.items():
        log.info(f"  {k:<35} {v}")

    save_imerg(result)
    log.info(f"\n✓ Done  (latency: {result['latency_hours']} h behind real-time)")
    return 0


if __name__ == "__main__":
    sys.exit(main())