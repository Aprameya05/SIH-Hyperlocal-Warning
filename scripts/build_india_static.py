#!/usr/bin/env python3
"""
scripts/build_india_static.py
=============================
Static (non-forecast) geography for every DRIFT land cell -> data/india_static_025.json

  terrain   : AWS Terrain Tiles "terrarium" (SRTM-derived), zoom 7 (~1.2 km/px at the equator). Per cell: mean/min/max
              elevation, relief (max-min), mean and 90th-percentile slope computed from the elevation gradient at the
              true metric pixel spacing. Slope at ~1.2 km pixels under-represents steep local terrain: it is a
              catchment-scale index, not a hillslope measurement.
  gauges    : INDOFLOODS gauge locations (processed/indofloods/indofloods_grid_mapping.csv, 106 gauges) and their recorded
              flood events (data/floodevents_indofloods.csv). Per cell: gauge inside the cell, nearest gauge and its
              distance, and its recorded event counts (total / rapid-onset = time to peak <= 1 day).
              These are OBSERVED historical events at 106 river gauges. They are not a river network, not live gauge
              levels, and say nothing about cells far from a gauge (nearest-gauge distance is reported, null beyond 100 km).
Missing stays missing: a cell with no terrain pixels or no gauge within range has null, never 0.
"""
from __future__ import annotations

import io
import json
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "terrain"
Z = 7
URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def tile_xy(lat, lon, z=Z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def fetch(xy):
    x, y = xy
    f = CACHE / f"{Z}_{x}_{y}.png"
    if not f.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        for k in range(3):
            try:
                f.write_bytes(urllib.request.urlopen(urllib.request.Request(URL.format(z=Z, x=x, y=y), headers={"User-Agent": "SIH-DRIFT/2.0"}), timeout=60).read())
                break
            except Exception:
                if k == 2:
                    raise
    a = np.asarray(Image.open(f).convert("RGB")).astype("float32")
    return (a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256) - 32768.0


def hav(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742.0 * math.asin(math.sqrt(a))


def main():
    grid = json.load(open(ROOT / "data" / "india_grid_025.json"))
    cells = grid["cells"]
    lat = [c[1] for c in cells]; lon = [c[2] for c in cells]
    x0, y1 = tile_xy(max(lat) + 0.2, min(lon) - 0.2); x1, y0 = tile_xy(min(lat) - 0.2, max(lon) + 0.2)
    tx = range(int(x0), int(x1) + 1); ty = range(int(y1), int(y0) + 1)
    keys = [(x, y) for y in ty for x in tx]
    print("tiles:", len(keys))
    with ThreadPoolExecutor(8) as ex:
        tiles = dict(zip(keys, ex.map(fetch, keys)))
    W = (len(tx)) * 256; H = len(ty) * 256
    elev = np.full((H, W), np.nan, dtype="float32")
    for (x, y), a in tiles.items():
        elev[(y - int(y1)) * 256:(y - int(y1) + 1) * 256, (x - int(x0)) * 256:(x - int(x0) + 1) * 256] = a
    # pixel (row, col) of lat/lon in the mosaic
    def px(la, lo):
        x, y = tile_xy(la, lo)
        return (y - int(y1)) * 256, (x - int(x0)) * 256
    # metric spacing and gradient
    m_per_px_eq = 40075016.686 / (256 * 2 ** Z)
    gy, gx = np.gradient(elev)
    # latitude of each row for metric scaling
    rows = np.arange(H)
    ytile = rows / 256 + int(y1)
    lat_row = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * ytile / 2 ** Z))))
    scale = (m_per_px_eq * np.cos(np.radians(lat_row)))[:, None]
    slope = np.degrees(np.arctan(np.hypot(gx, gy) / scale))

    # gauges + events
    gm = pd.read_csv(ROOT / "processed/indofloods/indofloods_grid_mapping.csv")
    gm = gm[gm["source_type"] == "gauge"].dropna(subset=["lat", "lon"])
    ev = pd.read_csv(ROOT / "data/floodevents_indofloods.csv")
    ev["gauge"] = ev["EventID"].str.replace(r"-\d+$", "", regex=True)
    ev["rapid"] = pd.to_numeric(ev["Time to Peak (days)"], errors="coerce") <= 1
    stats = ev.groupby("gauge").agg(events=("EventID", "count"), rapid=("rapid", "sum"), first=("Start Date", "min"), last=("Start Date", "max"))
    gauges = [(r.source_id, r.lat, r.lon) for r in gm.itertuples()]

    cols = ["elev_mean_m", "elev_min_m", "elev_max_m", "relief_m", "slope_mean_deg", "slope_p90_deg", "gauge_in_cell",
            "nearest_gauge_id", "nearest_gauge_km", "nearest_gauge_events", "nearest_gauge_rapid_events"]
    out = []
    for c in cells:
        la, lo = c[1], c[2]
        r0, c0 = px(la + 0.125, lo - 0.125); r1, c1 = px(la - 0.125, lo + 0.125)
        sl_e = elev[int(r0):int(math.ceil(r1)), int(c0):int(math.ceil(c1))]; sl_s = slope[int(r0):int(math.ceil(r1)), int(c0):int(math.ceil(c1))]
        ok = np.isfinite(sl_e)
        if ok.sum() < 20:
            terr = [None] * 6
        else:
            e = sl_e[ok]; s = sl_s[np.isfinite(sl_s)]
            terr = [round(float(e.mean())), round(float(e.min())), round(float(e.max())), round(float(e.max() - e.min())),
                    round(float(s.mean()), 1), round(float(np.percentile(s, 90)), 1)]
        inside = any(abs(g[1] - la) <= 0.125 and abs(g[2] - lo) <= 0.125 for g in gauges)
        best = min(((hav(la, lo, g[1], g[2]), g[0]) for g in gauges), key=lambda t: t[0])
        if best[0] <= 100.0:
            if best[1] in stats.index:
                st = stats.loc[best[1]]
                gz = [best[1], round(best[0], 1), int(st.events), int(st.rapid)]
            else:       # a mapped gauge with no events in the INDOFLOODS event file: 0 recorded events (a record, not a gap)
                gz = [best[1], round(best[0], 1), 0, 0]
        else:
            gz = [None, round(best[0], 1) if best[0] <= 100 else None, None, None]
        out.append(terr + [inside] + gz)
    doc = {"meta": {"version": 1, "grid": "data/india_grid_025.json", "n_cells": len(cells),
                    "terrain_source": "AWS Terrain Tiles terrarium zoom 7 (SRTM-derived) -- elevation, relief, slope from gradient at ~1.2 km",
                    "gauge_source": "INDOFLOODS gauge locations and flood events (106 gauges; historical, not live)",
                    "limits": ["slope at ~1.2 km pixels is a catchment-scale index", "gauges are sparse: nearest-gauge distance is given and nulled beyond 100 km",
                               "no river network, soil moisture observations or live gauge levels are included"]},
           "columns": cols, "cells": [c[0] for c in cells], "values": out}
    (ROOT / "data" / "india_static_025.json").write_text(json.dumps(doc, separators=(",", ":")))
    nn = sum(1 for o in out if o[0] is None)
    print(f"cells={len(out)} no-terrain={nn} gauge_in_cell={sum(1 for o in out if o[6])} within100km={sum(1 for o in out if o[7])}")


if __name__ == "__main__":
    main()
