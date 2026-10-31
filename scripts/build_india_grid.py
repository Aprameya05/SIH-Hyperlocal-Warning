#!/usr/bin/env python3
"""
scripts/build_india_grid.py
===========================
Builds DRIFT's national land grid:  data/india_grid_025.json  and  data/india_places.json

Grid design
  * 0.25 deg x 0.25 deg cells whose CENTRES lie exactly on GFS 0.25 deg grid points (multiples of 0.25), so a cell maps to
    exactly one source grid point: the output resolution is the SOURCE resolution, never finer.
  * a cell is a DRIFT location only if >= 50 % of its area is Indian land (Natural Earth 10 m admin-0, India point of
    view, full resolution -- not the simplified display outline). Ocean and neighbouring-country cells are excluded.
  * stable id  IN025_<lat*100 as 4 digits>_<lon*100 as 5 digits>   e.g. IN025_1300_07775  (13.00N 77.75E)
  * state: datameet states (2014 vintage: includes Telangana; Ladakh is still inside J&K); district: geoBoundaries
    ADM2 2021 (LGD). Assignment is by the cell CENTRE; a cell whose centre falls in no polygon takes the nearest within
    0.3 deg and is flagged admin_method="nearest". Cell attributes describe the centre point, not the cell's whole area.

Gazetteer: GeoNames cities15000 (CC BY 4.0), cities inside the India land boundary, each mapped to its cell.

Geographic source files are read from cache/geo (not committed: ~60 MB); this script documents how to refetch them.
"""
from __future__ import annotations

import io
import json
import math
import zipfile
from pathlib import Path

import numpy as np
from shapely.geometry import Point, box, shape
from shapely.prepared import prep
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
GEO = ROOT / "cache" / "geo"
STEP = 0.25
MIN_LAND_FRACTION = 0.5


def cell_id(lat: float, lon: float) -> str:
    return f"IN025_{int(round(lat * 100)):04d}_{int(round(lon * 100)):05d}"


def main():
    india = [shape(f["geometry"]) for f in json.load(open(GEO / "ne_10m_admin_0_countries_ind.geojson"))["features"]
             if f["properties"].get("ADM0_A3") == "IND"][0]
    pi = prep(india)
    minx, miny, maxx, maxy = india.bounds
    states = [(f["properties"]["ST_NM"], shape(f["geometry"])) for f in json.load(open(GEO / "datameet_states.geojson"))["features"]]
    dist = [(f["properties"]["shapeName"], shape(f["geometry"])) for f in json.load(open(GEO / "gb_adm2.geojson"))["features"]]
    st_tree, di_tree = STRtree([g for _, g in states]), STRtree([g for _, g in dist])

    lat0, lat1 = math.floor(miny / STEP) * STEP, math.ceil(maxy / STEP) * STEP
    lon0, lon1 = math.floor(minx / STEP) * STEP, math.ceil(maxx / STEP) * STEP
    state_names, district_names, cells = [], [], []

    def assign(tree, items, pt):
        hit = tree.query(pt, predicate="intersects")
        if len(hit):
            return int(hit[0]), "contains"
        k = int(tree.nearest(pt))
        return (k, "nearest") if items[k][1].distance(pt) <= 0.3 else (None, "none")

    for lat in np.arange(lat0, lat1 + 1e-9, STEP):
        for lon in np.arange(lon0, lon1 + 1e-9, STEP):
            b = box(lon - STEP / 2, lat - STEP / 2, lon + STEP / 2, lat + STEP / 2)
            if not pi.intersects(b):
                continue
            frac = 1.0 if pi.contains(b) else india.intersection(b).area / b.area
            if frac < MIN_LAND_FRACTION:
                continue
            pt = Point(lon, lat)
            si, sm = assign(st_tree, states, pt)
            di, dm = assign(di_tree, dist, pt)
            sname = states[si][0] if si is not None else None
            dname = dist[di][0] if di is not None else None
            if sname is not None and sname not in state_names:
                state_names.append(sname)
            key = (dname, sname)
            if dname is not None and key not in district_names:
                district_names.append(key)
            cells.append([cell_id(lat, lon), round(float(lat), 2), round(float(lon), 2), round(float(frac), 3),
                          state_names.index(sname) if sname else None, district_names.index(key) if dname else None,
                          "nearest" if "nearest" in (sm, dm) else "contains"])
    doc = {
        "meta": {"version": 1, "resolution_deg": STEP, "id_scheme": "IN025_<lat*100:04d>_<lon*100:05d>",
                 "n_cells": len(cells), "min_land_fraction": MIN_LAND_FRACTION,
                 "cell_definition": "0.25 x 0.25 deg box centred on a GFS 0.25 deg grid point; one source point per cell",
                 "boundary_source": "Natural Earth 10m admin-0 countries, India point of view (public domain)",
                 "state_source": "datameet/maps states (2014 vintage: Telangana present; Ladakh inside Jammu & Kashmir)",
                 "district_source": "geoBoundaries gbOpen IND ADM2 2021 (LGD directory, ODbL)",
                 "admin_assignment": "cell centre; 'nearest' flag when the centre is in no polygon (within 0.3 deg)",
                 "bounds": {"S": float(lat0), "N": float(lat1), "W": float(lon0), "E": float(lon1)}},
        "columns": ["id", "lat", "lon", "land_fraction", "state", "district", "admin_method"],
        "states": state_names, "districts": [{"name": d, "state": s} for d, s in district_names], "cells": cells,
    }
    (ROOT / "data" / "india_grid_025.json").write_text(json.dumps(doc, separators=(",", ":")))

    # ---- gazetteer
    by_pt = {(c[1], c[2]): c for c in cells}
    places = []
    with zipfile.ZipFile(GEO / "cities15000.zip") as z:
        for ln in z.read("cities15000.txt").decode("utf8").splitlines():
            p = ln.split("\t")
            if p[8] != "IN":
                continue
            lat, lon, pop = float(p[4]), float(p[5]), int(p[14] or 0)
            if not pi.contains(Point(lon, lat)):
                continue
            clat, clon = round(round(lat / STEP) * STEP, 2), round(round(lon / STEP) * STEP, 2)
            c = by_pt.get((clat, clon))
            if c is None:       # a coastal city whose snapped cell is mostly sea: use the nearest land cell within 0.5 deg
                near = [(abs(k[0] - lat) + abs(k[1] - lon), v) for k, v in by_pt.items() if abs(k[0] - lat) <= 0.5 and abs(k[1] - lon) <= 0.5]
                if not near:
                    continue
                c = min(near, key=lambda x: x[0])[1]
            places.append({"name": p[1], "ascii": p[2], "lat": round(lat, 4), "lon": round(lon, 4), "pop": pop,
                           "cell": c[0], "snapped": (c[1], c[2]) != (clat, clon)})
    places.sort(key=lambda x: -x["pop"])
    (ROOT / "data" / "india_places.json").write_text(json.dumps({"source": "GeoNames cities15000 (CC BY 4.0)", "n": len(places),
                                                               "places": places}, separators=(",", ":")))
    print(f"cells={len(cells)} states={len(state_names)} districts={len(district_names)} places={len(places)}")
    print("nearest-admin cells:", sum(1 for c in cells if c[6] == "nearest"), " no state:", sum(1 for c in cells if c[4] is None))


if __name__ == "__main__":
    main()
