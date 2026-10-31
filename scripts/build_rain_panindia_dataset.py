#!/usr/bin/env python3
"""
scripts/build_rain_panindia_dataset.py
======================================
Training/evaluation table for SHORT-DURATION HEAVY RAINFALL (3 h) on every India land cell.

  labels    : NOAA CDR CMORPH V1.0 hourly 0.25 deg rainfall (satellite microwave + IR morphing, gauge-adjusted over land;
              s3://noaa-cdr-precip-cmorph-pds). 3 h accumulation = sum of the three hourly files covering (valid-3h, valid].
              A DRIFT cell (centred on a multiple of 0.25 deg) is the area-weighted mean of the 4 overlapping CMORPH pixels.
              This is a SATELLITE ESTIMATE, not gauge truth, and it cannot verify 'cloudburst' (>=100 mm/h at ~10 km).
  predictors: GFS 0.25 deg at init 00Z, forecast hours 9/15/21 (so the model's own 3 h window (valid-3h, valid] is a direct
              GFS accumulation window: 6-9, 12-15, 18-21), read by scripts/india_gfs.py (same reader as live).
  rows      : every India land cell x (init, lead). Monsoon (15 Jun - 15 Sep) 2024 and 2025.
Resumable; raw files deleted after use.
"""
from __future__ import annotations

import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import india_gfs as g  # noqa: E402
import json  # noqa: E402

CACHE = ROOT / "cache" / "rain_panindia"
LEADS = [9, 15, 21]
CM = "https://noaa-cdr-precip-cmorph-pds.s3.amazonaws.com/data/hourly/0.25deg/{y}/{m:02d}/{d:02d}/CMORPH_V1.0_ADJ_0.25deg-HLY_{y}{m:02d}{d:02d}{h:02d}.nc"


def dates():
    base = [datetime(2024, 6, 15, tzinfo=timezone.utc) + timedelta(days=6 * k) for k in range(16)]
    order = sorted(range(16), key=lambda k: (0 if k % 4 == 0 else 1 if k % 2 == 0 else 2, k))
    return [base[k].replace(year=y) for k in order for y in (2024, 2025)]


def cmorph_hour(t: datetime) -> np.ndarray:
    url = CM.format(y=t.year, m=t.month, d=t.day, h=t.hour)
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=g.HEADERS), timeout=90).read()
    tmp = CACHE / f"cm_{t:%Y%m%d%H}.nc"; tmp.write_bytes(raw)
    try:
        with h5py.File(tmp) as f:
            tb = f["time_bounds"][0]
            if int(tb[0]) != int(t.timestamp()):
                raise ValueError(f"CMORPH file window {tb} != requested hour {t}")
            a = f["cmorph"][0].astype("float64")
    finally:
        tmp.unlink(missing_ok=True)
    a = np.where(a < 0, np.nan, a * 0.01)           # raw int16 * 0.01 -> mm/hr; -999 missing
    return a


def cmorph_3h(valid: datetime) -> np.ndarray:
    return sum(cmorph_hour(valid - timedelta(hours=k)) for k in (3, 2, 1))     # hours (V-3, V-2, V-1) each cover [HH, HH+1)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    grid = json.load(open(ROOT / "data" / "india_grid_025.json"))["cells"]
    lat = np.array([c[1] for c in grid]); lon = np.array([c[2] for c in grid]); ids = [c[0] for c in grid]
    kk = np.rint((lat + 59.75) / 0.25).astype(int); jj = np.rint((lon - 0.25) / 0.25).astype(int)
    out = CACHE / "features.csv"; done = set()
    if out.exists():
        d0 = pd.read_csv(out, usecols=["init", "lead"]).drop_duplicates(); done = {(r.init, r.lead) for r in d0.itertuples()}
    jobs = [(datetime(d.year, d.month, d.day, 0, tzinfo=timezone.utc), L) for d in dates() for L in LEADS]
    jobs = [j for j in jobs if (j[0].strftime("%Y-%m-%dT%H:%M:%SZ"), j[1]) not in done]
    print("jobs", len(jobs), flush=True)
    lock, wl, hdr = threading.Lock(), threading.Lock(), [not out.exists()]

    def work(job):
        init, L = job; valid = init + timedelta(hours=L)
        cm = cmorph_3h(valid)
        lab = np.full(len(lat), np.nan)
        px = np.stack([cm[kk, jj], cm[kk, jj + 1], cm[kk + 1, jj], cm[kk + 1, jj + 1]])
        ok = np.isfinite(px).all(axis=0); lab[ok] = px.mean(axis=0)[ok]
        path = CACHE / f"tmp_{init:%Y%m%d%H}_{L}.grib2"
        try:
            prov = g.download_subset(init, L, path, workers=5)
            with lock:
                dec = g.decode(path, init, L); der = g.derive(dec)
        finally:
            path.unlink(missing_ok=True)
        la, lo = dec["lat"], dec["lon"]; ii = np.rint((la[0] - lat) / 0.25).astype(int); jx = np.rint((lon - lo[0]) / 0.25).astype(int)
        f = dec["fields"]; ex = lambda a: a[ii, jx]
        win = {(a["start"], a["end"]): a["values"] for a in dec["apcp"]}
        if (L - 3, L) not in win:
            raise g.GfsError(f"no direct GFS APCP window {L-3}-{L}: {list(win)}")
        row = {"init": np.full(len(lat), init.strftime("%Y-%m-%dT%H:%M:%SZ")), "lead": L, "cell": ids, "lat": lat, "lon": lon, "cmorph_3h": lab,
               "gfs_p3h": ex(win[(L - 3, L)]), "apcp_total": ex(win[(0, L)])}
        for k in ("cape", "cin", "lftx", "pwat", "rh2m", "rh850", "rh700", "rh500", "w850", "w700", "w500", "soilw0_10"):
            row[k] = ex(f[k])
        for k in ("k_index", "total_totals", "lapse_850_500", "ws10", "shear_850_500", "t2m_c", "mslp_hpa"):
            row[k] = ex(der[k])
        row["dewpoint_depression"] = ex(der["t2m_c"]) - ex(der["d2m_c"])
        df = pd.DataFrame(row).dropna(subset=["cmorph_3h"]).astype({c: "float32" for c in row if c not in ("init", "lead", "cell")})
        with wl:
            df.to_csv(out, mode="a", header=hdr[0], index=False); hdr[0] = False
        return init, L, len(df)

    n = 0
    with ThreadPoolExecutor(3) as ex:
        for r in ex.map(lambda j: _safe(work, j), jobs):
            n += 1
            if n % 6 == 0 or n == len(jobs): print(f"{n}/{len(jobs)} last={r[0]:%Y-%m-%d} f{r[1]} rows={r[2]}", flush=True)


def _safe(fn, j):
    err = None
    for _ in range(3):
        try: return fn(j)
        except Exception as e:  # noqa: BLE001
            err = e
    print("FAILED", j, err, flush=True); return j[0], j[1], 0


if __name__ == "__main__":
    main()
