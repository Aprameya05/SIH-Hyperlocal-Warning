#!/usr/bin/env python3
"""
scripts/build_ts_panindia_dataset.py
====================================
Builds the training/evaluation table for the PAN-INDIA thunderstorm model.

  labels    : METAR present weather (wxcodes) at Indian airports from the Iowa Environmental Mesonet archive.
              target = a thunderstorm (TS, or TS in the vicinity VCTS) reported by the station within +-1.5 h of the
              forecast valid time. A (station, window) with fewer than 2 reports is NOT labelled (missing, not 0).
  predictors: GFS 0.25 deg fields from the NOAA AWS archive for init cycle T0 at forecast hour f (valid = T0 + f),
              nearest grid point to the station + 5x5 neighbourhood max CAPE, read by scripts/india_gfs.py (the same
              reader the live pipeline uses).
  design    : cycles 00Z and 12Z, leads 6/12/18/24 h, every 3rd day of Apr-Jun in 2024 and 2025.
Resumable: a (init, lead) already in features.csv is skipped. Raw GRIB files are deleted after decoding.
"""
from __future__ import annotations

import io
import json
import re
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import india_gfs as g  # noqa: E402

CACHE = ROOT / "cache" / "ts_panindia"
LEADS = [6, 12, 18, 24]
CYCLES = [0, 12]
HALF_WINDOW_H = 1.5
UA = {"User-Agent": "SIH-Hyperlocal-DRIFT/2.0"}


def dates():
    """Every 3rd day of Apr-Jun. Ordered so 2024 and 2025 INTERLEAVE and the season is visited coarse-to-fine (stride 12,
    then 6, then 3 days): stopping the build at any point leaves a balanced, season-spanning sample in both years."""
    base = [datetime(2024, 4, 1, tzinfo=timezone.utc) + timedelta(days=3 * k) for k in range(31)]
    order = sorted(range(31), key=lambda k: (0 if k % 4 == 0 else 1 if k % 2 == 0 else 2, k))
    out = []
    for k in order:
        for yr in (2024, 2025):
            out.append(base[k].replace(year=yr))
    return out


def stations():
    p = CACHE / "stations.json"
    if not p.exists():
        raw = urllib.request.urlopen(urllib.request.Request("https://mesonet.agron.iastate.edu/geojson/network/IN__ASOS.geojson", headers=UA), timeout=60).read()
        p.write_bytes(raw)
    out = []
    for f in json.load(open(p))["features"]:
        pr, (lon, lat) = f["properties"], f["geometry"]["coordinates"]
        ab, ae = pr.get("archive_begin"), pr.get("archive_end")
        if not ab or ab > "2024-03-01" or (ae and ae < "2025-07-01"):
            continue
        if 6 <= lat <= 37.5 and 68 <= lon <= 98:
            out.append({"station": f["id"], "name": pr["sname"], "lat": lat, "lon": lon})
    return out


def fetch_metar(station: str, year: int) -> pd.DataFrame:
    f = CACHE / f"metar_{station}_{year}.csv"
    if not f.exists():
        url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=%s&data=wxcodes&year1=%d&month1=3&day1=31"
               "&year2=%d&month2=7&day2=2&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M&trace=T&direct=no&report_type=3&report_type=4") % (station, year, year)
        f.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read())
    df = pd.read_csv(f, dtype=str)
    df["valid"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
    return df.dropna(subset=["valid"])


TS_RE = re.compile(r"(^|\s)[-+]?(VC)?TS")          # TS, TSRA, +TSRA, VCTS ...


def label(df: pd.DataFrame, v: datetime):
    w = df[(df["valid"] >= v - timedelta(hours=HALF_WINDOW_H)) & (df["valid"] <= v + timedelta(hours=HALF_WINDOW_H))]
    if len(w) < 2:
        return None, None, len(w)
    codes = w["wxcodes"].fillna("M").replace("M", "")
    at = int(any(re.search(r"(^|\s)[-+]?TS", c) for c in codes))
    any_ = int(any(TS_RE.search(c) for c in codes))
    return any_, at, len(w)


def main():
    stn = stations()
    print("stations:", len(stn))
    metar = {}
    for s in stn:
        for yr in (2024, 2025):
            try:
                metar[(s["station"], yr)] = fetch_metar(s["station"], yr)
            except Exception as e:  # noqa: BLE001
                print("metar fail", s["station"], yr, e)
    out = CACHE / "features.csv"
    done = set()
    if out.exists():
        d0 = pd.read_csv(out, usecols=["init", "lead"]).drop_duplicates()
        done = {(r.init, r.lead) for r in d0.itertuples()}
    jobs = [(datetime(d.year, d.month, d.day, h, tzinfo=timezone.utc), L) for d in dates() for h in CYCLES for L in LEADS]
    jobs = [j for j in jobs if (j[0].strftime("%Y-%m-%dT%H:%M:%SZ"), j[1]) not in done]
    print("jobs to do:", len(jobs), flush=True)
    lock, write_lock = threading.Lock(), threading.Lock()
    hdr = [not out.exists()]

    def work(job):
        init, L = job
        valid = init + timedelta(hours=L)
        rows = []
        # labels first: skip the (large) download when no station has a usable label for this window
        labs = {}
        for s in stn:
            df = metar.get((s["station"], valid.year))
            if df is None:
                continue
            a, b, n = label(df, valid)
            if a is not None:
                labs[s["station"]] = (a, b, n)
        if not labs:
            return init, L, 0
        path = CACHE / f"tmp_{init:%Y%m%d%H}_{L}.grib2"
        try:
            prov = g.download_subset(init, L, path, workers=5)
            with lock:
                dec = g.decode(path, init, L)
                der = g.derive(dec)
        finally:
            path.unlink(missing_ok=True)
        la, lo = dec["lat"], dec["lon"]
        f = dec["fields"]
        ap = {(a["start"], a["end"]): a["values"] for a in dec["apcp"]}
        ap6 = ap.get((max(L - 6, 0), L)) if (max(L - 6, 0), L) in ap else None
        ap_tot = ap.get((0, L))
        for s in stn:
            if s["station"] not in labs:
                continue
            i = int(round((la[0] - s["lat"]) / 0.25)); j = int(round((s["lon"] - lo[0]) / 0.25))
            if not (0 <= i < len(la) and 0 <= j < len(lo)):
                continue
            nb = f["cape"][max(i - 2, 0):i + 3, max(j - 2, 0):j + 3]
            row = {"init": init.strftime("%Y-%m-%dT%H:%M:%SZ"), "lead": L, "valid": valid.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "station": s["station"], "lat": s["lat"], "lon": s["lon"], "ts_any": labs[s["station"]][0],
                   "ts_at": labs[s["station"]][1], "n_reports": labs[s["station"]][2], "published_at": prov["published_at_utc"]}
            for k in ("cape", "cin", "lftx", "pwat", "rh2m", "rh850", "rh700", "rh500", "w850", "w700", "w500", "soilw0_10"):
                row[k] = float(f[k][i, j])
            for k in ("k_index", "total_totals", "lapse_850_500", "ws10", "shear_850_500", "t2m_c", "d2m_c", "mslp_hpa"):
                row[k] = float(der[k][i, j])
            row["cape_nb_max"] = float(np.nanmax(nb)) if nb.size else np.nan
            row["apcp_6h"] = float(ap6[i, j]) if ap6 is not None else np.nan
            row["apcp_total"] = float(ap_tot[i, j]) if ap_tot is not None else np.nan
            rows.append(row)
        with write_lock:
            pd.DataFrame(rows).to_csv(out, mode="a", header=hdr[0], index=False)
            hdr[0] = False
        return init, L, len(rows)

    n = 0
    with ThreadPoolExecutor(3) as ex:
        for init, L, k in ex.map(lambda j: _safe(work, j), jobs):
            n += 1
            if n % 10 == 0 or n == len(jobs):
                print(f"{n}/{len(jobs)} last={init:%Y-%m-%d %H}Z f{L} rows={k}", flush=True)


def _safe(fn, j):
    for t in range(3):
        try:
            return fn(j)
        except Exception as e:  # noqa: BLE001
            err = e
    print("FAILED", j, err, flush=True)
    return j[0], j[1], 0


if __name__ == "__main__":
    main()
