#!/usr/bin/env python3
"""
pipelines/ingestion/gfs_reader.py
====================
One GFS 0.25 deg reader for BOTH live ingestion and historical training data, so a model is trained on exactly the
fields it is later applied to.

  * downloads ONLY the needed GRIB2 messages from the NOAA AWS Open Data mirror using the .idx byte ranges
  * decodes with eccodes and cross-checks every message against what was requested (init time, step, level); a message
    whose own metadata disagrees with the request is rejected -- producer labels are never trusted
  * crops to the India box, derives physical quantities (dew point, K-index, Total Totals, 850-500 hPa bulk shear,
    lapse rate, wind speed/direction)
  * records provenance per file: S3 Last-Modified (the genuine publication time of that file on the mirror),
    acquisition time, byte counts, APCP accumulation windows as stated by the GRIB messages themselves

Units are the GRIB units (K, %, m/s, J/kg, kg/m2 = mm, Pa, Pa/s, soil moisture m3/m3 fraction).
"""
from __future__ import annotations

import math
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import numpy as np

BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
HEADERS = {"User-Agent": "SIH-Hyperlocal-DRIFT/2.0 (NOAA GFS AWS Open Data client)"}
BBOX = {"S": 5.0, "N": 38.5, "W": 66.5, "E": 99.0}          # India + margin; cells are chosen later by land mask

# (key, idx variable, idx level text). Level text must match the .idx line EXACTLY.
SELECT = [
    ("cape", "CAPE", "surface"), ("cin", "CIN", "surface"), ("lftx", "LFTX", "surface"),
    ("pwat", "PWAT", "entire atmosphere (considered as a single layer)"), ("mslp", "PRMSL", "mean sea level"),
    ("t2m", "TMP", "2 m above ground"), ("d2m", "DPT", "2 m above ground"), ("rh2m", "RH", "2 m above ground"),
    ("u10", "UGRD", "10 m above ground"), ("v10", "VGRD", "10 m above ground"),
    ("t850", "TMP", "850 mb"), ("t700", "TMP", "700 mb"), ("t500", "TMP", "500 mb"),
    ("rh850", "RH", "850 mb"), ("rh700", "RH", "700 mb"), ("rh500", "RH", "500 mb"),
    ("u850", "UGRD", "850 mb"), ("v850", "VGRD", "850 mb"), ("u500", "UGRD", "500 mb"), ("v500", "VGRD", "500 mb"),
    ("w850", "VVEL", "850 mb"), ("w700", "VVEL", "700 mb"), ("w500", "VVEL", "500 mb"),
    ("soilw0_10", "SOILW", "0-0.1 m below ground"),
]
APCP = ("APCP", "surface")      # accumulation windows are read from each message ("A-B hour acc fcst")


class GfsError(RuntimeError):
    pass


def _get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={**HEADERS, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def file_url(init: datetime, fhour: int) -> str:
    return f"{BASE}/gfs.{init:%Y%m%d}/{init:%H}/atmos/gfs.t{init:%H}z.pgrb2.0p25.f{fhour:03d}"


def head(url: str) -> Optional[dict]:
    try:
        r = _get(url) if False else urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=HEADERS), timeout=30)
        lm = r.headers.get("Last-Modified")
        return {"last_modified_utc": parsedate_to_datetime(lm).astimezone(timezone.utc) if lm else None,
                "content_length": int(r.headers.get("Content-Length", 0))}
    except Exception:
        return None


def latest_cycle(cycles=(0, 12), max_back_days=3, now: Optional[datetime] = None, need_fhour=24):
    """Newest cycle (in `cycles`) whose f<need_fhour> file is fully published. Returns (init, {fhour: head}) or None."""
    now = now or datetime.now(timezone.utc)
    for d in range(max_back_days + 1):
        day = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        for h in sorted(cycles, reverse=True):
            init = day.replace(hour=h)
            if init > now:
                continue
            if head(file_url(init, need_fhour) + ".idx") and head(file_url(init, need_fhour)):
                return init
    return None


def _parse_idx(text: str):
    out = []
    for ln in text.strip().splitlines():
        p = ln.split(":")
        out.append({"n": int(p[0]), "off": int(p[1]), "var": p[3], "level": p[4], "fcst": p[5]})
    for i, m in enumerate(out):
        m["end"] = out[i + 1]["off"] - 1 if i + 1 < len(out) else None
    return out


def _fetch_range(url, a, b, retries=3):
    for k in range(retries):
        try:
            return _get(url, {"Range": f"bytes={a}-{'' if b is None else b}"}, timeout=120).read()
        except Exception:
            if k == retries - 1:
                raise
            time.sleep(1.5 * (k + 1))


def download_subset(init: datetime, fhour: int, out_path: Path, workers: int = 6) -> dict:
    """Fetch the selected messages into one GRIB2 file. Returns provenance."""
    url = file_url(init, fhour)
    idx = _parse_idx(_get(url + ".idx").read().decode())
    want = []
    for key, var, lev in SELECT:
        hit = [m for m in idx if m["var"] == var and m["level"] == lev and "ens" not in m["fcst"]]
        if len(hit) != 1:
            raise GfsError(f"{key}: expected exactly one .idx message for {var}:{lev} f{fhour:03d}, found {len(hit)}")
        want.append((key, hit[0]))
    if fhour > 0:
        for m in idx:
            if m["var"] == APCP[0] and m["level"] == APCP[1] and "acc fcst" in m["fcst"]:
                want.append(("apcp", m))
    with ThreadPoolExecutor(workers) as ex:
        blobs = list(ex.map(lambda km: _fetch_range(url, km[1]["off"], km[1]["end"]), want))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".part")
    with open(tmp, "wb") as f:
        for b in blobs:
            f.write(b)
    tmp.replace(out_path)
    h = head(url) or {}
    return {"url": url, "bytes": sum(len(b) for b in blobs), "n_messages": len(want),
            "published_at_utc": h.get("last_modified_utc"), "acquired_at_utc": datetime.now(timezone.utc)}


def decode(path: Path, init: datetime, fhour: int) -> dict:
    """Decode a subset file; verify every message against the request. Returns {lat, lon, fields{key: 2D}, apcp[], meta}."""
    from eccodes import (codes_get, codes_get_array, codes_get_values, codes_grib_new_from_file, codes_release)
    fields, apcp, lat, lon = {}, [], None, None
    keymap = {(v, l): k for k, v, l in SELECT}
    lev_name = {"surface": "surface", "meanSea": "mean sea level", "heightAboveGround": None, "isobaricInhPa": None,
                "atmosphere": "entire atmosphere (considered as a single layer)", "depthBelowLand": None}
    with open(path, "rb") as f:
        while True:
            g = codes_grib_new_from_file(f)
            if g is None:
                break
            try:
                short, tol, level = codes_get(g, "shortName"), codes_get(g, "typeOfLevel"), int(codes_get(g, "level"))
                dd, dt = int(codes_get(g, "dataDate")), int(codes_get(g, "dataTime"))
                m_init = datetime.strptime(f"{dd:08d}{dt:04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
                if m_init != init:
                    raise GfsError(f"message init {m_init} != requested {init} ({short})")
                step_end = int(codes_get(g, "endStep")); step_start = int(codes_get(g, "startStep"))
                if step_end != fhour:
                    raise GfsError(f"message end step {step_end} != requested f{fhour} ({short})")
                Ni, Nj = int(codes_get(g, "Ni")), int(codes_get(g, "Nj"))
                if lat is None:
                    la1, la2 = codes_get(g, "latitudeOfFirstGridPointInDegrees"), codes_get(g, "latitudeOfLastGridPointInDegrees")
                    lo1, di = codes_get(g, "longitudeOfFirstGridPointInDegrees"), codes_get(g, "iDirectionIncrementInDegrees")
                    dj = codes_get(g, "jDirectionIncrementInDegrees")
                    if abs(di - 0.25) > 1e-6 or abs(dj - 0.25) > 1e-6:
                        raise GfsError(f"unexpected grid increment {di}/{dj}")
                    lat_all = la1 + (np.arange(Nj) * (-dj if la1 > la2 else dj))
                    lon_all = (lo1 + np.arange(Ni) * di) % 360.0
                    ly = (lat_all >= BBOX["S"] - 1e-6) & (lat_all <= BBOX["N"] + 1e-6)
                    lx = (lon_all >= BBOX["W"] - 1e-6) & (lon_all <= BBOX["E"] + 1e-6)
                    lat, lon = lat_all[ly], lon_all[lx]
                    sel = (ly, lx)
                vals = codes_get_values(g).reshape(Nj, Ni)[sel[0]][:, sel[1]].astype("float32")
                vals[np.abs(vals) > 1e19] = np.nan
                if short in ("soilw", "vsw"):
                    vals[vals > 10.0] = np.nan          # GFS marks water/ice points with a 9999 sentinel: missing, not a value
                if short == "tp":
                    dup = [x for x in apcp if x["start"] == step_start and x["end"] == step_end]
                    if dup:       # the same window listed twice in the .idx: accept only if the fields are identical
                        if not np.array_equal(dup[0]["values"], vals, equal_nan=True):
                            raise GfsError(f"conflicting duplicate APCP window {step_start}-{step_end}")
                        continue
                    apcp.append({"start": step_start, "end": step_end, "values": vals})
                    continue
                # map by (idx var name, level text) via GRIB keys
                key = None
                for (v, l), k in keymap.items():
                    if _grib_matches(short, tol, level, v, l):
                        key = k
                        break
                if key is None:
                    raise GfsError(f"unrecognised message {short}/{tol}/{level}")
                if key in fields:
                    raise GfsError(f"duplicate message for {key}")
                fields[key] = vals
            finally:
                codes_release(g)
    missing = [k for k, _, _ in SELECT if k not in fields]
    if missing:
        raise GfsError(f"missing fields after decode: {missing}")
    if fhour > 0 and not apcp:
        raise GfsError("no APCP messages")
    return {"lat": lat, "lon": lon, "fields": fields, "apcp": apcp, "init": init, "fhour": fhour,
            "valid": init + timedelta(hours=fhour)}


_SHORT = {"CAPE": "cape", "CIN": "cin", "LFTX": "lftx", "PWAT": "pwat", "PRMSL": "prmsl", "TMP": "t", "DPT": "dpt",
          "RH": "r", "UGRD": "u", "VGRD": "v", "VVEL": "w", "SOILW": "soilw"}


def _grib_matches(short, tol, level, var, levtext) -> bool:
    exp_short = {"CAPE": ("cape",), "CIN": ("cin",), "LFTX": ("lftx", "4lftx"), "PWAT": ("pwat",), "PRMSL": ("prmsl",),
                 "TMP": ("t", "2t"), "DPT": ("dpt", "2d"), "RH": ("r", "2r"), "UGRD": ("u", "10u"), "VGRD": ("v", "10v"),
                 "VVEL": ("w",), "SOILW": ("soilw", "vsw")}[var]
    if short not in exp_short:
        return False
    m = re.match(r"(\d+) mb", levtext)
    if m:
        return tol == "isobaricInhPa" and level == int(m.group(1))
    m = re.match(r"(\d+) m above ground", levtext)
    if m:
        return tol == "heightAboveGround" and level == int(m.group(1))
    if levtext == "surface":
        return tol == "surface"
    if levtext == "mean sea level":
        return tol == "meanSea"
    if levtext.startswith("entire atmosphere"):
        return tol in ("atmosphere", "atmosphereSingleLayer")
    if levtext == "0-0.1 m below ground":
        return tol == "depthBelowLandLayer" or tol == "depthBelowLand"
    return False


# ---------------------------------------------------------------- derived physics
def dewpoint_c(t_k, rh_pct):
    t = t_k - 273.15
    rh = np.clip(rh_pct, 1.0, 100.0) / 100.0
    a, b = 17.625, 243.04
    g = np.log(rh) + a * t / (b + t)
    return b * g / (a - g)


R_EARTH = 6371000.0


def _grid_gradients(field, lat, lon):
    """Centred finite differences on the regular lat/lon grid with spherical metric: (d/dx, d/dy) per metre.
    `lat` may be descending (GFS); np.gradient is given the signed coordinate so the sign is correct."""
    phi = np.radians(lat)[:, None]
    dy = R_EARTH * np.gradient(np.radians(lat))[:, None]                      # metres, signed (negative if lat descends)
    dx = R_EARTH * np.cos(phi) * np.gradient(np.radians(lon))[None, :]
    return np.gradient(field, axis=1) / dx, np.gradient(field, axis=0) / dy


def divergence(u, v, lat, lon):
    dudx, _ = _grid_gradients(u, lat, lon)
    _, dvdy = _grid_gradients(v, lat, lon)
    return dudx + dvdy                                                          # 1/s; convergence = -divergence


def derive_dynamics(dec: dict) -> dict:
    """Grid-based diagnostics that need neighbours (cannot be computed from a single cell value).
    conv850/conv10: horizontal convergence in 1e-5 s^-1 (positive = convergence = lift forcing).
    pwat_grad: magnitude of the horizontal PWAT gradient in mm per 100 km.  q850/q700: specific humidity g/kg."""
    f, la, lo = dec["fields"], dec["lat"], dec["lon"]
    out = {"conv850": -divergence(f["u850"], f["v850"], la, lo) * 1e5, "conv10": -divergence(f["u10"], f["v10"], la, lo) * 1e5}
    gx, gy = _grid_gradients(f["pwat"], la, lo)
    out["pwat_grad"] = np.hypot(gx, gy) * 1e5                                    # mm/m -> mm/100 km
    for name, p_hpa, t, rh in (("q850", 850.0, f["t850"], f["rh850"]), ("q700", 700.0, f["t700"], f["rh700"])):
        tc = t - 273.15
        es = 6.112 * np.exp(17.67 * tc / (tc + 243.5))                           # hPa, Bolton (1980)
        e = np.clip(rh, 0, 100) / 100.0 * es
        out[name] = 1000.0 * 0.622 * e / (p_hpa - 0.378 * e)
    return out


def derive(dec: dict) -> dict:
    f = dec["fields"]
    out = {}
    t850, t700, t500 = f["t850"] - 273.15, f["t700"] - 273.15, f["t500"] - 273.15
    td850, td700 = dewpoint_c(f["t850"], f["rh850"]), dewpoint_c(f["t700"], f["rh700"])
    out["k_index"] = (t850 - t500) + td850 - (t700 - td700)                    # George (1960)
    out["total_totals"] = (t850 - t500) + (td850 - t500)                        # vertical + cross totals
    out["lapse_850_500"] = (t850 - t500) / 3.5                                  # K/km, approx 3.5 km layer thickness
    out["ws10"] = np.hypot(f["u10"], f["v10"])
    out["wd10"] = (np.degrees(np.arctan2(-f["u10"], -f["v10"])) + 360.0) % 360.0   # meteorological "from" direction
    out["shear_850_500"] = np.hypot(f["u500"] - f["u850"], f["v500"] - f["v850"])  # bulk difference, NOT 0-6 km shear
    out["t2m_c"] = f["t2m"] - 273.15
    out["d2m_c"] = f["d2m"] - 273.15
    out["mslp_hpa"] = f["mslp"] / 100.0
    return out
