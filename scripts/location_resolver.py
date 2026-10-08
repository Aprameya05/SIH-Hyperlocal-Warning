#!/usr/bin/env python3
"""
scripts/location_resolver.py
================================
2026-10-08: universal location resolution for arbitrary Indian
locations -- free, public, no API key. Fixes the "any searched
location silently gets VOBL/Bengaluru data" problem by actually
geocoding the query and mapping the result onto the real canonical
992-cell grid, rather than defaulting to a hardcoded place.

Source: OpenStreetMap Nominatim (nominatim.openstreetmap.org), the
standard free/public OSM geocoder. Usage policy requires a descriptive
User-Agent and caps request rate at ~1/sec -- both honored here
(MIN_REQUEST_INTERVAL_S + a real Sleep, not a cosmetic header). Results
are cached to disk (keyed by the normalized query) so repeat lookups
for the same place never re-hit the network, and so a user-facing
request never blocks on a live geocode call for a place already
resolved.

This module does ONE thing: text/coords -> (lat, lon, display_name,
canonical cell_id, distance_km). It does not fetch weather or run any
model -- callers combine this with the existing forecast/inference
path for whatever cell_id comes back.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# NOTE: regrid.py's own cell_id_for(lat, lon) looks like the right tool
# here but is NOT -- despite its docstring, it just formats the INPUT
# coordinates to 1 decimal place (f"IND_{lat:.1f}_{lon:.1f}"); it does
# not snap to the real 992-cell grid at all, whose cells are centered
# on INTEGER lat/lon (confirmed directly against
# data/pan_india_common_grid_992.json, and the same convention the
# 2026-10-06 VOBL cell-mapping fix established: nearest-CENTER
# distance, not floor-binning). Discovered and worked around here
# rather than silently producing wrong cell_ids for arbitrary
# locations the way the VOBL bug did for one hardcoded one.
COMMON_GRID_PATH = REPO_ROOT / "data" / "pan_india_common_grid_992.json"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "SIH-Hyperlocal-DRIFT/1.0 (https://github.com/Aprameya05/SIH-Hyperlocal-Warning)"
MIN_REQUEST_INTERVAL_S = 1.1  # Nominatim usage policy: max 1 request/second
CACHE_PATH = REPO_ROOT / "data" / "_location_geocode_cache.json"

# India bounding box, matching the canonical grid (regrid.py), used to
# reject a geocode result that falls outside the grid this product
# actually covers -- never silently substitutes a cell for a location
# the grid doesn't reach.
INDIA_BOUNDS = {"S": 6.0, "N": 37.0, "W": 68.0, "E": 98.0}

_last_request_time = 0.0


@dataclass
class ResolvedLocation:
    query: str
    found: bool
    display_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    cell_id: Optional[str] = None
    distance_to_cell_km: Optional[float] = None
    in_india_grid_bounds: Optional[bool] = None
    source: str = "nominatim_osm"
    from_cache: bool = False
    error: Optional[str] = None


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _cache_key(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    except Exception:
        pass  # caching is an optimization, never a hard requirement


_CANONICAL_CELLS_CACHE: Optional[list] = None


def _load_canonical_cells() -> list:
    global _CANONICAL_CELLS_CACHE
    if _CANONICAL_CELLS_CACHE is None:
        with open(COMMON_GRID_PATH, encoding="utf-8") as f:
            doc = json.load(f)
        _CANONICAL_CELLS_CACHE = [{"cell_id": c["cell_id"], "lat": c["lat"], "lon": c["lon"]}
                                   for c in doc["cells"]]
    return _CANONICAL_CELLS_CACHE


def _nearest_canonical_cell(lat: float, lon: float) -> tuple[str, float]:
    """Finds the true nearest REGISTERED cell among the real 992 cells
    in data/pan_india_common_grid_992.json, by actual distance -- never
    a formula that could invent a cell_id that isn't one of the real
    992 (which is what naively formatting lat/lon to the grid's
    nominal spacing would risk near grid edges)."""
    cells = _load_canonical_cells()
    best = min(cells, key=lambda c: _haversine_km(lat, lon, c["lat"], c["lon"]))
    distance_km = _haversine_km(lat, lon, best["lat"], best["lon"])
    return best["cell_id"], round(distance_km, 2)


def geocode(query: str, use_cache: bool = True, session: Optional[requests.Session] = None) -> ResolvedLocation:
    """Resolves a free-text location query (e.g. "Mumbai", "Chennai,
    India", "12.97,77.59") to real coordinates and the real nearest
    canonical cell. Never returns VOBL/Bengaluru for an unrelated
    query -- a query that doesn't resolve returns found=False, not a
    fallback location."""
    global _last_request_time
    query = (query or "").strip()
    if not query:
        return ResolvedLocation(query=query, found=False, error="empty query")

    if use_cache:
        cache = _load_cache()
        key = _cache_key(query)
        if key in cache:
            data = dict(cache[key])
            data["from_cache"] = True
            return ResolvedLocation(**data)

    sess = session or requests
    elapsed = time.time() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL_S:
        time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)

    try:
        resp = sess.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "in"},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        _last_request_time = time.time()
        resp.raise_for_status()
        results = resp.json()
    except Exception as exc:
        return ResolvedLocation(query=query, found=False, error=f"{type(exc).__name__}: {exc}")

    if not results:
        return ResolvedLocation(query=query, found=False, error="no geocoding match for this query")

    r0 = results[0]
    lat, lon = float(r0["lat"]), float(r0["lon"])
    in_bounds = (INDIA_BOUNDS["S"] <= lat <= INDIA_BOUNDS["N"]) and (INDIA_BOUNDS["W"] <= lon <= INDIA_BOUNDS["E"])
    cell_id, dist = (_nearest_canonical_cell(lat, lon) if in_bounds else (None, None))

    result = ResolvedLocation(
        query=query, found=True, display_name=r0.get("display_name"),
        latitude=lat, longitude=lon, cell_id=cell_id, distance_to_cell_km=dist,
        in_india_grid_bounds=in_bounds, from_cache=False,
        error=None if in_bounds else "geocoded location falls outside the canonical 992-cell grid bounds",
    )

    if use_cache:
        cache = _load_cache()
        cache[_cache_key(query)] = {k: v for k, v in asdict(result).items() if k != "from_cache"}
        _save_cache(cache)

    return result


if __name__ == "__main__":
    for q in sys.argv[1:] or ["Bengaluru", "Mumbai", "Delhi", "Chennai"]:
        r = geocode(q)
        print(json.dumps(asdict(r), indent=2))
