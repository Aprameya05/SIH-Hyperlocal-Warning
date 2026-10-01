#!/usr/bin/env python3
"""
terrain_lookup.py — nearest-neighbor lookup into the existing DEM/drainage
terrain grid (data/blr_terrain.json, produced by fetch_dem_terrain.py) for
use as a flash-flood risk modifier in backend/pipeline.py::hazard_probabilities().

Scope, honestly stated: fetch_dem_terrain.py only covers the Bengaluru/VOBL
bounding box (12.5-14.0N, 77.0-78.5E) at 0.01 deg (~1.1 km) resolution — it is
NOT pan-India. This module therefore returns TerrainSample(available=False)
for any (lat, lon) outside that bbox or when the terrain file is missing,
rather than fabricating a value. It never claims pan-India terrain coverage.

This is a lookup, not a trained model: it reports what data/blr_terrain.json
already computed (elevation_m, slope_deg, flood_susceptibility — see
fetch_dem_terrain.py::compute_flood_susceptibility for that heuristic's own
formula) at the nearest available grid point. Lookup method is nearest-
neighbor on the regular 0.01 deg grid (max snap distance capped below);
this is not the general-purpose bilinear-capable regridder (see regrid.py,
Priority 3) because the terrain grid here is a small, fixed, already-regular
grid where nearest-neighbor is the scientifically appropriate choice for a
susceptibility field that is not smooth across ridgelines.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_TERRAIN_PATH = Path(__file__).parent / "data" / "blr_terrain.json"

# A lookup further than this from the nearest terrain sample is not
# considered a meaningful match, even if it happens to be the closest point
# in a sparse/partial grid — prevents silently stretching a Bengaluru-local
# DEM sample across a distant pan-India cell.
MAX_SNAP_DEG = 0.02  # ~2.2 km, i.e. within ~2 grid cells of the 0.01 deg DEM


@dataclass
class TerrainSample:
    available: bool
    elevation_m: Optional[float] = None
    slope_deg: Optional[float] = None
    flood_susceptibility: Optional[float] = None
    source: str = "data/blr_terrain.json (SRTM via fetch_dem_terrain.py)"
    resolution_deg: Optional[float] = None
    distance_deg: Optional[float] = None
    reason: str = ""

    def to_json(self) -> dict:
        return {
            "available": self.available,
            "elevation_m": self.elevation_m,
            "slope_deg": self.slope_deg,
            "flood_susceptibility": self.flood_susceptibility,
            "source": self.source,
            "resolution_deg": self.resolution_deg,
            "distance_deg": round(self.distance_deg, 4) if self.distance_deg is not None else None,
            "reason": self.reason,
        }


class TerrainGrid:
    """Loads data/blr_terrain.json once and serves nearest-neighbor lookups."""

    def __init__(self, path: Path = DEFAULT_TERRAIN_PATH):
        self.path = Path(path)
        self.loaded = False
        self.bbox = None
        self.resolution_deg = None
        self._points = []  # list of (lat, lon, elevation_m, slope_deg, flood_susceptibility)
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
            self.bbox = data.get("bbox")
            self.resolution_deg = data.get("resolution_deg")
            for cell in data.get("grid", []):
                lat, lon = cell.get("lat"), cell.get("lon")
                if lat is None or lon is None:
                    continue
                self._points.append((
                    float(lat), float(lon),
                    cell.get("elevation_m"), cell.get("slope_deg"),
                    cell.get("flood_susceptibility"),
                ))
            self.loaded = len(self._points) > 0
        except Exception:
            self.loaded = False

    def lookup(self, lat: float, lon: float) -> TerrainSample:
        if not self.loaded:
            return TerrainSample(available=False, reason=f"{self.path} not found or unreadable")

        if self.bbox is not None:
            if not (self.bbox["lat_min"] - MAX_SNAP_DEG <= lat <= self.bbox["lat_max"] + MAX_SNAP_DEG and
                    self.bbox["lon_min"] - MAX_SNAP_DEG <= lon <= self.bbox["lon_max"] + MAX_SNAP_DEG):
                return TerrainSample(available=False,
                                      reason="outside DEM coverage bbox (Bengaluru/VOBL only, not pan-India)")

        best = None
        best_d = None
        for plat, plon, elev, slope, fs in self._points:
            d = math.hypot(plat - lat, plon - lon)
            if best_d is None or d < best_d:
                best_d, best = d, (plat, plon, elev, slope, fs)

        if best is None or best_d > MAX_SNAP_DEG:
            return TerrainSample(available=False, distance_deg=best_d,
                                  reason=f"nearest DEM sample is {best_d:.3f} deg away — beyond {MAX_SNAP_DEG} deg snap radius")

        _, _, elev, slope, fs = best
        return TerrainSample(
            available=True,
            elevation_m=elev, slope_deg=slope, flood_susceptibility=fs,
            resolution_deg=self.resolution_deg,
            distance_deg=best_d,
            reason="nearest-neighbor match within DEM coverage",
        )


_SINGLETON: Optional[TerrainGrid] = None


def get_terrain_grid(path: Path = DEFAULT_TERRAIN_PATH) -> TerrainGrid:
    global _SINGLETON
    if _SINGLETON is None or _SINGLETON.path != Path(path):
        _SINGLETON = TerrainGrid(path)
    return _SINGLETON


def apply_terrain_to_ff(ff_prob: float, terrain: TerrainSample) -> float:
    """
    Physically-interpretable, bounded terrain modifier for flash-flood
    probability. Documented formula (not an arbitrary multiplier):

      ff_prob_terrain = ff_prob * (0.70 + 0.60 * flood_susceptibility)

    flood_susceptibility in [0,1] (from fetch_dem_terrain.py) already blends
    slope (low slope -> water ponds, higher susceptibility) and relative
    elevation (lower relative elevation -> higher susceptibility). At
    susceptibility=0 (steep/high ground) this scales ff_prob down to 0.70x;
    at susceptibility=1 (flat/low ground) it scales ff_prob up to 1.30x.
    The 0.70-1.30 band was chosen so terrain can meaningfully shift the
    score without terrain alone ever manufacturing risk where the
    atmospheric model output was 0, and the result is always clipped to
    [0, 1]. When terrain is unavailable for this cell, ff_prob is returned
    unmodified — terrain unavailability is never treated as low or high risk.
    """
    if not terrain.available or terrain.flood_susceptibility is None:
        return ff_prob
    factor = 0.70 + 0.60 * max(0.0, min(1.0, terrain.flood_susceptibility))
    return round(min(1.0, max(0.0, ff_prob * factor)), 4)
