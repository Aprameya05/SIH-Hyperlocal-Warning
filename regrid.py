#!/usr/bin/env python3
"""
regrid.py — common geographic regridding layer for reconciling sources at
different native resolutions onto one target grid.

Why this exists: the PS-to-code audit (2026-09-30) found GFS (0.25 deg),
the pan-India hazard grid (1.0 deg), Himawari CTT (native satellite pixels,
~2km, cropped/masked around VOBL), and DEM terrain (0.01 deg, Bengaluru-only)
each reconciled with each other by ad hoc nearest-pixel lookups written
separately in three places (backend/pipeline.py::lat_lon_to_idx/extract_point,
fetch_himawari_realtime.py's haversine argmin, terrain_lookup.py's own
nearest-neighbor loop) with no shared abstraction and no interpolation
method beyond nearest-neighbor anywhere in the repo (confirmed by
`grep -rn "scipy.interpolate|griddata|xesmf|regrid"` = 0 hits before this
file). This module does NOT replace those call sites (that would be a
larger refactor than this pass's mandate) -- it provides the shared
abstraction so INSAT/IMDAA and any future unified-grid work can plug into
one interpolation layer instead of writing a fourth ad hoc lookup.

Target grid: the existing pan-India hazard grid's own definition
(backend/pipeline.py: BOUNDS = {S:6, N:37, W:68, E:98}). As of the Phase 4.5
integrity fix (2026-09-30), backend/pipeline.py locks two separate,
non-ambiguous constants: APPLICATION_GRID_STEP = 1.0 (the canonical
992-cell output/application grid -- see docs/CANONICAL_GRID.md) and
SOURCE_GRID_STEP_FALLBACK_DEG = 0.25 (GFS native source-grid spacing,
used only as a fallback estimate inside compute_convergence_grid, never
for the output grid). Earlier revisions of this docstring described a
single ambiguous "GRID_STEP" that could be either value depending on
context -- that was the actual bug (see docs/PHASE_4_5_INTEGRITY_AUDIT.md).
Callers of this module pass the step they need; this module does not
hardcode which one is "the" target.

Supported interpolation methods:
  - "nearest": always safe, works for any source density, appropriate for
    categorical/non-smooth fields (e.g. flood_susceptibility across a
    ridgeline, or CTT near a cloud edge where interpolation would blur a
    real discontinuity).
  - "bilinear": only used when the source is confirmed to be a regular
    lat/lon grid with at least 2x2 real (non-NaN) neighbors bracketing the
    query point; falls back to nearest-neighbor with an explicit
    fallback_reason when that is not the case, rather than extrapolating.

Every RegridResult carries source resolution, target resolution, method
actually used (which may differ from the method requested if a safe
fallback occurred), and a provenance/limitations note -- so no caller can
silently report an interpolated field as an original source resolution
observation.

Only GFS, Himawari and DEM are wired to real data today. INSAT and IMDAA
have no live data (see the PS audit); this module exposes the same
interface for them so that once real (non-fabricated) data exists, the only
new code needed is a source reader, not a new interpolation scheme.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

SUPPORTED_SOURCES = ("gfs", "himawari", "dem", "insat", "imdaa")


@dataclass
class RegridResult:
    value: Optional[float]
    available: bool
    method_used: str  # "nearest" | "bilinear" | "none"
    method_requested: str
    fallback_reason: Optional[str]
    source_resolution_deg: Optional[float]
    target_resolution_deg: Optional[float]
    distance_deg: Optional[float]

    def to_json(self) -> dict:
        return {
            "value": self.value,
            "available": self.available,
            "method_used": self.method_used,
            "method_requested": self.method_requested,
            "fallback_reason": self.fallback_reason,
            "source_resolution_deg": self.source_resolution_deg,
            "target_resolution_deg": self.target_resolution_deg,
            "distance_deg": round(self.distance_deg, 4) if self.distance_deg is not None else None,
        }


def _find_bracket(coords: Sequence[float], value: float) -> Optional[Tuple[int, int, float]]:
    """For a sorted 1D coordinate array, return (lo_idx, hi_idx, frac) such
    that coords[lo] <= value <= coords[hi] and frac in [0,1] is value's
    fractional position between them. None if value is outside the array's
    range (never extrapolates)."""
    n = len(coords)
    if n == 0:
        return None
    if value < coords[0] or value > coords[-1]:
        return None
    if n == 1:
        return 0, 0, 0.0
    for i in range(n - 1):
        lo, hi = coords[i], coords[i + 1]
        if lo <= value <= hi:
            frac = 0.0 if hi == lo else (value - lo) / (hi - lo)
            return i, i + 1, frac
    return n - 1, n - 1, 0.0


def regrid_point(source_name: str,
                  source_lats: Sequence[float],
                  source_lons: Sequence[float],
                  source_values,  # 2D indexable [lat_idx][lon_idx] -> float or None
                  target_lat: float,
                  target_lon: float,
                  source_resolution_deg: Optional[float] = None,
                  target_resolution_deg: Optional[float] = None,
                  method: str = "nearest",
                  max_nearest_snap_deg: Optional[float] = None) -> RegridResult:
    """
    Look up / interpolate one (target_lat, target_lon) point from a 2D
    source field on a regular (not necessarily uniform-spaced) lat/lon grid.

    source_lats, source_lons: 1D coordinate arrays, ascending order required
    for bilinear; nearest-neighbor tolerates any order.
    source_values: indexable as source_values[i][j] for lat index i, lon
    index j (matches the numpy 2D array convention already used by
    backend/pipeline.py's extract_point()).
    """
    if source_name not in SUPPORTED_SOURCES:
        return RegridResult(None, False, "none", method,
                             f"unknown source {source_name!r}, expected one of {SUPPORTED_SOURCES}",
                             source_resolution_deg, target_resolution_deg, None)

    if not source_lats or not source_lons:
        return RegridResult(None, False, "none", method,
                             f"no {source_name} grid coordinates provided", source_resolution_deg,
                             target_resolution_deg, None)

    if method == "bilinear":
        lats_sorted = list(source_lats) == sorted(source_lats)
        lons_sorted = list(source_lons) == sorted(source_lons)
        if lats_sorted and lons_sorted:
            lat_bracket = _find_bracket(list(source_lats), target_lat)
            lon_bracket = _find_bracket(list(source_lons), target_lon)
            if lat_bracket is not None and lon_bracket is not None:
                i0, i1, fy = lat_bracket
                j0, j1, fx = lon_bracket
                try:
                    v00 = source_values[i0][j0]
                    v01 = source_values[i0][j1]
                    v10 = source_values[i1][j0]
                    v11 = source_values[i1][j1]
                    vals = [v00, v01, v10, v11]
                    if all(v is not None and not (isinstance(v, float) and math.isnan(v)) for v in vals):
                        top = v00 * (1 - fx) + v01 * fx
                        bot = v10 * (1 - fx) + v11 * fx
                        value = top * (1 - fy) + bot * fy
                        return RegridResult(float(value), True, "bilinear", method, None,
                                             source_resolution_deg, target_resolution_deg, None)
                    # Missing neighbor(s): fall through to nearest-neighbor
                    # below rather than interpolating across a data gap.
                except (IndexError, TypeError):
                    pass
            fallback_reason = "target point outside source bracket or missing neighbor -- fell back to nearest-neighbor"
        else:
            fallback_reason = "source coordinates not strictly ascending -- bilinear requires a sorted regular grid; fell back to nearest-neighbor"
    else:
        fallback_reason = None

    # Nearest-neighbor (default method, and the fallback for bilinear).
    best_i, best_j, best_d = None, None, None
    for i, la in enumerate(source_lats):
        for j, lo in enumerate(source_lons):
            d = math.hypot(la - target_lat, lo - target_lon)
            if best_d is None or d < best_d:
                best_d, best_i, best_j = d, i, j

    if best_i is None:
        return RegridResult(None, False, "none", method, "empty source grid",
                             source_resolution_deg, target_resolution_deg, None)

    if max_nearest_snap_deg is not None and best_d > max_nearest_snap_deg:
        return RegridResult(None, False, "none", method,
                             f"nearest {source_name} sample is {best_d:.3f} deg away, "
                             f"beyond {max_nearest_snap_deg} deg snap radius",
                             source_resolution_deg, target_resolution_deg, best_d)

    try:
        value = source_values[best_i][best_j]
    except (IndexError, TypeError):
        value = None

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return RegridResult(None, False, "none", method,
                             f"nearest {source_name} cell has no data at that point",
                             source_resolution_deg, target_resolution_deg, best_d)

    return RegridResult(float(value), True, "nearest", method, fallback_reason,
                         source_resolution_deg, target_resolution_deg, best_d)


# ---------------------------------------------------------------------------
# Phase 3 (canonical grid) addition -- CELL-AWARE WRAPPER WITH FULL PROVENANCE
# ---------------------------------------------------------------------------
#
# regrid_point() above is unchanged (8 existing tests depend on its exact
# signature/behavior). This wrapper adds the three provenance fields Phase 3
# requires that RegridResult does not carry: source, source_timestamp, and
# target_cell_id -- by calling regrid_point() and attaching them, not by
# reimplementing any interpolation logic.

@dataclass
class CellRegridResult:
    cell_id: str
    value: Optional[float]
    available: bool
    source: str
    source_timestamp: Optional[str]
    source_resolution_deg: Optional[float]
    target_resolution_deg: Optional[float]
    method_used: str
    method_requested: str
    missing_flag: bool
    missing_reason: Optional[str]
    distance_deg: Optional[float]

    def to_json(self) -> dict:
        return {
            "cell_id": self.cell_id,
            "value": self.value,
            "available": self.available,
            "source": self.source,
            "source_timestamp": self.source_timestamp,
            "source_resolution_deg": self.source_resolution_deg,
            "target_resolution_deg": self.target_resolution_deg,
            "method_used": self.method_used,
            "method_requested": self.method_requested,
            "missing_flag": self.missing_flag,
            "missing_reason": self.missing_reason,
            "distance_deg": round(self.distance_deg, 4) if self.distance_deg is not None else None,
        }


def cell_id_for(lat: float, lon: float) -> str:
    """Canonical cell_id convention for the 992-cell grid (docs/CANONICAL_GRID.md).
    Deterministic from lat/lon -- not stored in production pan_india_grid.json,
    used only by the new pan-India label-engine scripts (Phase 4)."""
    return f"IND_{lat:.1f}_{lon:.1f}"


def regrid_to_cell(source_name: str,
                    source_lats: Sequence[float],
                    source_lons: Sequence[float],
                    source_values,
                    target_lat: float,
                    target_lon: float,
                    source_timestamp: Optional[str] = None,
                    source_resolution_deg: Optional[float] = None,
                    target_resolution_deg: Optional[float] = None,
                    method: str = "nearest",
                    max_nearest_snap_deg: Optional[float] = None) -> CellRegridResult:
    """Cell-aware wrapper around regrid_point() carrying the full provenance
    set Phase 3 requires: source, source_timestamp, source_resolution,
    method, target_cell_id, missing flag, missing reason."""
    result = regrid_point(
        source_name, source_lats, source_lons, source_values,
        target_lat, target_lon,
        source_resolution_deg=source_resolution_deg,
        target_resolution_deg=target_resolution_deg,
        method=method,
        max_nearest_snap_deg=max_nearest_snap_deg,
    )
    return CellRegridResult(
        cell_id=cell_id_for(target_lat, target_lon),
        value=result.value,
        available=result.available,
        source=source_name,
        source_timestamp=source_timestamp,
        source_resolution_deg=result.source_resolution_deg,
        target_resolution_deg=result.target_resolution_deg,
        method_used=result.method_used,
        method_requested=result.method_requested,
        missing_flag=not result.available,
        missing_reason=result.fallback_reason,
        distance_deg=result.distance_deg,
    )
