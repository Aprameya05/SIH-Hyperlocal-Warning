#!/usr/bin/env python3
"""
location_engine.py — coordinate-to-forecast spatial lookup, Python reference
implementation. The browser port (assets/location_engine.js) mirrors this
logic exactly so both sides agree on cell selection and VOBL-domain rules;
this file is the testable source of truth and is also usable server-side
(e.g. from a future API) without duplicating the algorithm.

Preserves every existing architecture untouched: this module only reads
already-generated outputs (data/pan_india_grid.json, forecast.json,
data/blr_terrain.json, data/himawari_realtime.json) and performs a
nearest-cell / domain-membership lookup. It does not call any model, does
not retrain anything, and does not invent an ensemble across architectures.

Coordinate-to-cell algorithm: nearest-neighbor on the pan-India grid's own
regular lat/lon spacing (grid_step_deg from the grid file itself, currently
1.0 deg for data/pan_india_grid.json), capped by MAX_CELL_SNAP_DEG so a
point far outside India does not silently snap to the nearest edge cell.
This is deterministic and documented, matching this turn's "use
deterministic nearest-cell... do not invent values" requirement.

VOBL domain: the VOBL XGBoost/CB/FF station models are station-specific by
construction (trained on WMO 43295 observations only) and never claim to
generalize spatially. VOBL_DOMAIN_RADIUS_KM defines the only radius within
which this engine will present the VOBL station forecast as applicable to
the user's selected location; outside it, the station forecast is still
retrievable (for explicit "VOBL station context" display) but is always
labeled station-specific / not applicable to the selected location, never
silently substituted for a local prediction.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

VOBL_LAT = 13.1979
VOBL_LON = 77.7063
VOBL_DOMAIN_RADIUS_KM = 25.0  # station representativeness radius, documented estimate

MAX_CELL_SNAP_DEG = 1.5  # ~165km; beyond this a "nearest cell" is not a meaningful match

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass
class GridCellMatch:
    available: bool
    cell: Optional[dict] = None
    distance_deg: Optional[float] = None
    distance_km: Optional[float] = None
    grid_step_deg: Optional[float] = None
    reason: str = ""


def find_nearest_cell(grid: dict, lat: float, lon: float) -> GridCellMatch:
    """grid: parsed data/pan_india_grid.json. Nearest-neighbor over its cells,
    with an explicit snap radius so out-of-domain points are reported, not
    silently matched to a distant edge cell."""
    # backend/pipeline.py writes the key "grid_cells" (verified against its
    # actual output line: `"grid_cells": cells`). This was originally coded
    # against "cells" by mistake, which would have silently matched nothing
    # against the real file -- caught and fixed against the actual on-disk
    # data/pan_india_grid.json before shipping.
    cells = grid.get("grid_cells") or grid.get("cells") or []
    if not cells:
        return GridCellMatch(False, reason="pan-India grid has no cells loaded")

    best, best_d = None, None
    for c in cells:
        d = math.hypot(c["lat"] - lat, c["lon"] - lon)
        if best_d is None or d < best_d:
            best_d, best = d, c

    if best is None or best_d > MAX_CELL_SNAP_DEG:
        return GridCellMatch(False, distance_deg=best_d,
                              grid_step_deg=grid.get("grid_step_deg"),
                              reason=f"nearest pan-India cell is {best_d:.2f} deg away — outside grid coverage")

    return GridCellMatch(
        True, cell=best, distance_deg=round(best_d, 4),
        distance_km=round(haversine_km(lat, lon, best["lat"], best["lon"]), 1),
        grid_step_deg=grid.get("grid_step_deg"),
        reason="nearest-neighbor match within pan-India grid coverage",
    )


@dataclass
class VoblApplicability:
    in_domain: bool
    distance_km: float
    radius_km: float
    reason: str


def vobl_applicability(lat: float, lon: float) -> VoblApplicability:
    d = haversine_km(lat, lon, VOBL_LAT, VOBL_LON)
    in_domain = d <= VOBL_DOMAIN_RADIUS_KM
    reason = (
        f"{d:.1f} km from IMD VOBL 43295 -- within the {VOBL_DOMAIN_RADIUS_KM:.0f} km "
        f"station representativeness radius" if in_domain else
        f"{d:.1f} km from IMD VOBL 43295 -- outside the {VOBL_DOMAIN_RADIUS_KM:.0f} km "
        f"station representativeness radius; VOBL XGBoost is station-specific and not "
        f"applicable to this location"
    )
    return VoblApplicability(in_domain, round(d, 1), VOBL_DOMAIN_RADIUS_KM, reason)


@dataclass
class LocationBundle:
    """The full, honestly-labeled multi-architecture bundle for one
    lat/lon. Every field traces to a real source; unavailable fields are
    explicitly marked, never fabricated or reused from another location."""
    latitude: float
    longitude: float
    pan_india: dict          # {available, ts, cb, ff, ff_terrain_adjusted, terrain, cell, source, timestamp}
    vobl: dict               # {applicable, in_domain, distance_km, station_forecast_or_None, source}
    himawari: dict           # {available, ctt_c, ctt_drop_rate, timestamp, source, note}
    gfs: dict                # {available, cape, cin, k_index, totals_totals, source}
    lead_time: Optional[dict]
    generated_at: Optional[str]

    def to_json(self) -> dict:
        return {
            "latitude": self.latitude, "longitude": self.longitude,
            "pan_india": self.pan_india, "vobl": self.vobl,
            "himawari": self.himawari, "gfs": self.gfs,
            "lead_time": self.lead_time, "generated_at": self.generated_at,
        }


def build_location_bundle(lat: float, lon: float,
                           pan_india_grid: Optional[dict],
                           forecast: Optional[dict],
                           himawari: Optional[dict]) -> LocationBundle:
    """
    Pure function: given already-loaded JSON (no I/O here so it is testable
    with synthetic fixtures and directly portable to the browser), returns
    the honest, source-labeled multi-architecture bundle for (lat, lon).
    """
    # --- Pan-India Hazard Engine ---
    pan_india_out = {"available": False, "reason": "pan-India grid not loaded"}
    if pan_india_grid is not None:
        match = find_nearest_cell(pan_india_grid, lat, lon)
        if match.available:
            c = match.cell
            pan_india_out = {
                "available": True,
                "source": "Pan-India Hazard Engine (backend/pipeline.py::hazard_probabilities)",
                "ts_probability": c.get("thunderstorm_probability"),
                "cb_probability": c.get("cloudburst_probability"),
                "ff_probability": c.get("flash_flood_probability"),
                "ff_probability_terrain_adjusted": c.get("flash_flood_probability_terrain_adjusted"),
                "terrain": c.get("terrain"),
                # Field names below use .get() with a fallback to the older
                # on-disk schema (pwat/apcp_mm) where the new fields
                # (ctt_c/terrain/convergence_s/ctt_drop_rate_c_hr/qpe_mm/
                # flash_flood_probability_terrain_adjusted) don't exist yet
                # because the file predates this turn's backend/pipeline.py
                # change and hasn't been regenerated by a live pipeline run.
                # Genuinely absent fields stay None -- never backfilled with
                # a guess.
                "atmospheric": {
                    "cape": c.get("cape"), "cin": c.get("cin"),
                    "k_index": c.get("k_index"), "totals_totals": c.get("totals_totals"),
                    "wind_shear_ms": c.get("wind_shear_ms"),
                    "pwat_mm": c.get("pwat_mm", c.get("pwat")),
                    "ctt_c": c.get("ctt_c"), "convergence_s": c.get("convergence_s"),
                    "ctt_drop_rate_c_hr": c.get("ctt_drop_rate_c_hr"),
                    "qpe_mm": c.get("qpe_mm", c.get("apcp_mm")),
                },
                "cell": {"lat": c["lat"], "lon": c["lon"]},
                "distance_km": match.distance_km,
                "grid_step_deg": match.grid_step_deg,
                "generated_at_utc": pan_india_grid.get("generated_at_utc"),
                "gfs_cycle": pan_india_grid.get("gfs_cycle"),
                "gfs_fhour": pan_india_grid.get("gfs_fhour"),
            }
        else:
            pan_india_out = {"available": False, "reason": match.reason}

    # --- VOBL XGBoost (station-specific) ---
    vobl_app = vobl_applicability(lat, lon)
    vobl_out = {
        "in_domain": vobl_app.in_domain,
        "distance_km": vobl_app.distance_km,
        "radius_km": vobl_app.radius_km,
        "reason": vobl_app.reason,
        "source": "IMD VOBL Station 43295 / VOBL XGBoost (forecast_action.py)",
        "station_forecast": None,
    }
    if forecast is not None and "slots" in forecast:
        # Always retrievable for explicit station-context display, but the
        # frontend must only present it as "your forecast" when in_domain.
        vobl_out["station_forecast"] = {
            "slots": forecast.get("slots"),
            "peak_probability": forecast.get("peak_probability"),
            "peak_slot": forecast.get("peak_slot"),
            "generated_at_ist": forecast.get("generated_at_ist"),
            "gfs_cycle": forecast.get("gfs_cycle"),
        }

    # --- Himawari / CTT context ---
    # Himawari's real coverage today is a ~50km radius crop around VOBL
    # (fetch_himawari_realtime.py); report unavailable outside that, never
    # fabricate a value for a distant cell.
    himawari_out = {"available": False, "reason": "no Himawari data loaded"}
    if himawari is not None:
        d_km = haversine_km(lat, lon, VOBL_LAT, VOBL_LON)
        if d_km <= 50.0:
            himawari_out = {
                "available": True,
                "source": "HIMAWARI-9 (fetch_himawari_realtime.py) -- NOT INSAT",
                "vobl_bt_celsius": himawari.get("vobl_bt_celsius"),
                "min_bt_50km": himawari.get("min_bt_50km"),
                "bt_trend_1h": himawari.get("bt_trend_1h"),
                "storm_detected": himawari.get("storm_detected"),
                "timestamp": himawari.get("fetched_at_utc") or himawari.get("timestamp"),
                "distance_km_from_vobl": round(d_km, 1),
            }
        else:
            himawari_out = {"available": False, "distance_km_from_vobl": round(d_km, 1),
                             "reason": f"{d_km:.0f} km from VOBL -- outside Himawari's ~50 km real-time crop radius"}

    # --- GFS atmospheric context (from the pan-India cell, if matched) ---
    gfs_out = {"available": False, "reason": "no pan-India cell matched"}
    if pan_india_out.get("available"):
        atmo = pan_india_out["atmospheric"]
        gfs_out = {
            "available": True,
            "source": "GFS (NOAA NOMADS, backend/pipeline.py)",
            **atmo,
        }

    lead_time = None
    generated_at = None
    if forecast is not None and forecast.get("slots"):
        generated_at = forecast.get("generated_at_ist")
        # lead_time is per-slot in forecast.json (see lead_time.py); surface
        # the primary (slot 2) one as the headline value if present.
        for s in forecast["slots"]:
            if s.get("primary") and "lead_time" in s:
                lead_time = s["lead_time"]
                break

    return LocationBundle(
        latitude=lat, longitude=lon,
        pan_india=pan_india_out, vobl=vobl_out,
        himawari=himawari_out, gfs=gfs_out,
        lead_time=lead_time, generated_at=generated_at,
    )
