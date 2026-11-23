#!/usr/bin/env python3
"""Himawari-9 band-13 (10.4 um) cloud-top brightness temperature and its cooling rate on DRIFT's 0.25 deg cells.

WHAT THIS IS (and is not)
  * An OBSERVATION: Himawari-9 AHI L1b band 13 brightness temperature (BT), anonymous AWS bucket noaa-himawari9,
    10-minute full-disk scans at 2 km (nadir). It is not a forecast and not model-derived.
  * BT equals cloud-top temperature (CTT) only for opaque cloud. Over clear sky it is the surface skin temperature and for
    thin cirrus it is warmer than the cloud top. It is therefore published as brightness temperature, never as a retrieved CTT.
  * Per 0.25 deg cell: p10 and minimum BT of the ~15x15 pixels inside it (the cold tail is what matters for deep convection),
    the fraction of pixels colder than 235 K, and the p10 change over 30 and 60 minutes (K/h; negative = cooling).
  * Viewing geometry is recorded per cell (satellite zenith angle). Himawari views western India at 70-80 deg, where pixels
    are large and parallax shifts cold tops, so data there is flagged, not hidden.

All-or-nothing, atomic output: data/india_satellite_ctt_latest.json is replaced only by a document that passes
pipelines/validation/validate_satellite_ctt.py; a failure leaves the previous artifact untouched (and its age is visible).
Usage: python pipelines/ingestion/ingest_himawari_ctt.py [--out PATH] [--now ISO]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

BUCKET, PREFIX, REGION = "noaa-himawari9", "AHI-L1b-FLDK", "us-east-1"
SEGMENTS = (2, 3, 4, 5)            # HSD segments (550 lines each) that contain 5.5-38.5 N for lon 66-99 E
BAND = "B13"
BT_MIN_K, BT_MAX_K = 150.0, 340.0  # physically plausible 10.4 um brightness temperature
COLD_K = 235.0                     # cold-cloud threshold used for the cold-pixel fraction
CELL_DEG = 0.25
BBOX = (5.5, 38.5, 66.0, 99.0)     # lat0, lat1, lon0, lon1
SUB_LON, EARTH_R, SAT_H = 140.7, 6378.137, 35785.863   # km (geostationary Himawari-9)
SCHEMA = "drift_satellite_ctt_v1"
OUT = ROOT / "data" / "india_satellite_ctt_latest.json"
GRID = ROOT / "data" / "india_grid_025.json"


# ------------------------------------------------------------------ pure functions (unit tested) -----------------------
def hsd_key(scene: datetime, seg: int, band: str = BAND) -> str:
    return f"{PREFIX}/{scene:%Y/%m/%d/%H%M}/HS_H09_{scene:%Y%m%d_%H%M}_{band}_FLDK_R20_S{seg:02d}10.DAT.bz2"


def view_zenith_deg(lat, lon) -> np.ndarray:
    """Satellite zenith angle for a geostationary satellite over (0, SUB_LON): exact spherical-earth geometry."""
    la, dlo = np.radians(np.asarray(lat, dtype="float64")), np.radians(np.asarray(lon, dtype="float64") - SUB_LON)
    cg = np.cos(la) * np.cos(dlo)                                   # cos(central angle)
    sg = np.sqrt(np.clip(1.0 - cg * cg, 0.0, None))
    z = np.degrees(np.arctan2(sg, cg - EARTH_R / (EARTH_R + SAT_H)))
    return np.where(cg > EARTH_R / (EARTH_R + SAT_H), z, np.nan)    # beyond the limb the satellite cannot see the point


def bin_to_cells(lat, lon, bt, cell_index: dict[tuple[int, int], int], n_cells: int):
    """Aggregate pixel BT to cells. Returns dict of float arrays (NaN where a cell has no valid pixel)."""
    lat, lon, bt = (np.asarray(a).ravel() for a in (lat, lon, bt))
    ok = np.isfinite(bt) & np.isfinite(lat) & np.isfinite(lon) & (bt >= BT_MIN_K) & (bt <= BT_MAX_K)
    ok &= (lat >= BBOX[0]) & (lat <= BBOX[1]) & (lon >= BBOX[2]) & (lon <= BBOX[3])
    iy, ix = np.rint(lat[ok] / CELL_DEG).astype(np.int64), np.rint(lon[ok] / CELL_DEG).astype(np.int64)
    key = iy * 100000 + ix
    order = np.argsort(key, kind="stable")
    key, b = key[order], bt[ok][order].astype("float64")
    uniq, start, cnt = np.unique(key, return_index=True, return_counts=True)
    out = {k: np.full(n_cells, np.nan) for k in ("p10", "min", "cold_frac", "n")}
    for u, s0, c in zip(uniq, start, cnt):
        j = cell_index.get((int(u // 100000), int(u % 100000)))
        if j is None:
            continue
        v = b[s0:s0 + c]
        out["p10"][j], out["min"][j], out["cold_frac"][j], out["n"][j] = np.percentile(v, 10), v.min(), float((v < COLD_K).mean()), c
    return out


def cooling_rate_k_per_h(p10_now, p10_prev, minutes: float) -> np.ndarray:
    """Change of the cell p10 BT per hour; negative = cooling (cloud top rising). NaN where either frame is missing."""
    return (np.asarray(p10_now, dtype="float64") - np.asarray(p10_prev, dtype="float64")) / (minutes / 60.0)


def newest_complete_scene(listing_keys, now: datetime, max_age_h: float = 6.0):
    """Newest 10-minute scene (UTC) for which ALL needed segments of the band exist in `listing_keys`."""
    have = {}
    for k in listing_keys:
        m = re.search(r"HS_H09_(\d{8})_(\d{4})_" + BAND + r"_FLDK_R20_S(\d{2})10\.DAT\.bz2$", k)
        if m:
            have.setdefault(m.group(1) + m.group(2), set()).add(int(m.group(3)))
    best = None
    for stamp, segs in have.items():
        if set(SEGMENTS) <= segs:
            t = datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            if now - timedelta(hours=max_age_h) <= t <= now and (best is None or t > best):
                best = t
    return best


def to_json_array(a, nd=2):
    return [None if not np.isfinite(x) else round(float(x), nd) for x in a]


# ------------------------------------------------------------------ IO ----------------------------------------------------
def load_grid():
    g = json.load(open(GRID, encoding="utf-8"))
    cells = g["cells"]
    ids = [c[0] for c in cells]
    idx = {(int(round(c[1] / CELL_DEG)), int(round(c[2] / CELL_DEG))): i for i, c in enumerate(cells)}
    return ids, np.array([c[1] for c in cells]), np.array([c[2] for c in cells]), idx


def s3_client():
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config
    return boto3.client("s3", region_name=REGION, config=Config(signature_version=UNSIGNED, connect_timeout=15, read_timeout=180))


def list_scene_keys(s3, now: datetime):
    keys = []
    for back in (0, 1):
        t = now - timedelta(hours=back)
        r = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{PREFIX}/{t:%Y/%m/%d/%H}", MaxKeys=1000)  # 6 scenes x 10 segs x 16 bands fits in 1000? paged below
        keys += [o["Key"] for o in r.get("Contents", [])]
        while r.get("IsTruncated"):
            r = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{PREFIX}/{t:%Y/%m/%d/%H}", ContinuationToken=r["NextContinuationToken"])
            keys += [o["Key"] for o in r.get("Contents", [])]
    return [k for k in keys if f"_{BAND}_" in k]


def read_frame(s3, scene: datetime, tmp: str):
    """Download the needed segments of one scene and decode them with satpy. Returns (lat, lon, bt, provenance)."""
    from satpy import Scene
    files, prov = [], []
    for seg in SEGMENTS:
        key = hsd_key(scene, seg)
        local = os.path.join(tmp, os.path.basename(key))
        head = s3.head_object(Bucket=BUCKET, Key=key)
        s3.download_file(BUCKET, key, local)
        files.append(local)
        prov.append({"key": key, "bytes": int(head["ContentLength"]), "last_modified_utc": head["LastModified"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    scn = Scene(filenames=files, reader="ahi_hsd")
    scn.load([BAND])
    da = scn[BAND]
    lons, lats = da.attrs["area"].get_lonlats()
    return np.asarray(lats), np.asarray(lons), np.asarray(da.values, dtype="float64"), prov


def build(now: datetime) -> dict:
    ids, clat, clon, cidx = load_grid()
    n = len(ids)
    s3 = s3_client()
    scene0 = newest_complete_scene(list_scene_keys(s3, now), now)
    if scene0 is None:
        raise SystemExit("no complete Himawari-9 B13 scene for the needed segments in the last 6 h")
    frames, prov = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        for label, minutes_back in (("t0", 0), ("tm30", 30), ("tm60", 60)):
            sc = scene0 - timedelta(minutes=minutes_back)
            try:
                lat, lon, bt, p = read_frame(s3, sc, tmp)
            except Exception as e:              # a missing earlier frame only disables the rate, never the observation
                if label == "t0":
                    raise
                print(f"WARNING: frame {label} ({sc:%H%M}Z) unavailable: {e}")
                continue
            frames[label] = bin_to_cells(lat, lon, bt, cidx, n)
            prov[label] = {"scene_nominal_utc": sc.strftime("%Y-%m-%dT%H:%M:%SZ"), "files": p}
    f0 = frames["t0"]
    rate = {m: (cooling_rate_k_per_h(f0["p10"], frames[k]["p10"], mm) if k in frames else np.full(n, np.nan))
            for m, k, mm in (("30", "tm30", 30), ("60", "tm60", 60))}
    z = view_zenith_deg(clat, clon)
    return {
        "meta": {
            "schema": SCHEMA, "kind": "SATELLITE_OBSERVATION", "source": "Himawari-9 AHI L1b band 13 (10.4 um), anonymous AWS bucket noaa-himawari9",
            "quantity": "BRIGHTNESS_TEMPERATURE_K", "is_observation": True, "is_forecast": False, "is_retrieved_cloud_top_temperature": False,
            "interpretation": "Equals cloud-top temperature only for opaque cloud; clear sky shows the surface; thin cirrus reads warmer than its top.",
            "scene_nominal_utc": scene0.strftime("%Y-%m-%dT%H:%M:%SZ"), "observed_at_note": "scan of the India segments takes place within the 10 minutes after the nominal time",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "pixel_size_km_nadir": 2.0, "cell_deg": CELL_DEG,
            "cell_statistic": "p10 and minimum of valid pixel BT whose centre lies in the cell (nearest 0.25 deg centre)", "cold_threshold_k": COLD_K,
            "rate_definition": "p10 BT(now) - p10 BT(earlier), per hour; negative = cooling", "valid_bt_range_k": [BT_MIN_K, BT_MAX_K],
            "quality": "view_zenith_deg > 70 means large pixels and parallax: treat values as coarse", "n_cells": n,
            "cells_sha256": hashlib.sha256("|".join(ids).encode()).hexdigest(), "frames": prov,
            "missing_data": "null = no valid pixel (or no earlier frame for the rate); never replaced by a default",
        },
        "cells": ids,
        "ctt_p10_k": to_json_array(f0["p10"]), "ctt_min_k": to_json_array(f0["min"]), "cold_frac_235k": to_json_array(f0["cold_frac"], 3),
        "n_pixels": [None if not np.isfinite(x) else int(x) for x in f0["n"]],
        "dtdt_30min_k_per_h": to_json_array(rate["30"]), "dtdt_60min_k_per_h": to_json_array(rate["60"]),
        "view_zenith_deg": to_json_array(z, 1),
    }


def write_atomic(doc: dict, out: Path):
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--now", help="override 'now' (ISO UTC), for tests/backfill")
    a = ap.parse_args(argv)
    now = datetime.strptime(a.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if a.now else datetime.now(timezone.utc)
    doc = build(now)
    from pipelines.validation.validate_satellite_ctt import problems
    bad = problems(doc, now=now, max_age_minutes=180)
    if bad:
        print("REFUSING to publish an invalid satellite artifact; previous artifact left untouched:", *bad, sep="\n  - ")
        return 1
    write_atomic(doc, Path(a.out))
    ok = sum(x is not None for x in doc["ctt_p10_k"])
    print(f"wrote {a.out}: scene {doc['meta']['scene_nominal_utc']}, {ok}/{doc['meta']['n_cells']} cells with BT, "
          f"{sum(x is not None for x in doc['dtdt_60min_k_per_h'])} with a 60-min rate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
