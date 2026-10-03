"""
backend/data_sources/dem.py
=============================
Reuses the existing SRTM/AWS Terrarium implementation's OUTPUT.
fetch_dem_terrain.py at the repo root does the actual AWS Terrarium tile
fetch -- this adapter reads data/blr_terrain.json, it does not re-fetch
tiles itself.

DEM/elevation is static -- terrain does not change -- so this is never
"LIVE" in the time-sensitive sense the other sources use. It is CACHED
(real data, on disk, within the regeneration budget the existing fetcher
already enforces via MAX_AGE_DAYS) or STALE (older than that budget and
not yet regenerated). It is explicitly VOBL-only per the existing
implementation and the live operational audit -- this adapter does not
pretend a pan-India/national drainage raster exists.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import BaseSourceAdapter, SourceMetadata, classify_freshness, compute_freshness_minutes

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TERRAIN_PATH = REPO_ROOT / "data" / "blr_terrain.json"

# Matches fetch_dem_terrain.py's own MAX_AGE_DAYS=30 regeneration budget.
# DEM has no meaningful "LIVE" state (terrain is static), so the "live"
# budget here is intentionally generous and really means "freshly
# regenerated", not "real-time".
LIVE_MAX_MINUTES = 7 * 24 * 60     # regenerated within the last week
CACHED_MAX_MINUTES = 30 * 24 * 60  # matches fetch_dem_terrain.py MAX_AGE_DAYS


class DEMSource(BaseSourceAdapter):
    source_name = "dem"
    source_product = "SRTM via AWS Terrarium (elevation + slope)"

    def __init__(self, terrain_path: Path = TERRAIN_PATH):
        self.terrain_path = terrain_path

    def get_metadata(self) -> SourceMetadata:
        if not self.terrain_path.exists():
            return self.unavailable(f"{self.terrain_path} missing -- no DEM fetch has run yet")

        try:
            with open(self.terrain_path) as f:
                terrain = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return self.unavailable(f"{self.terrain_path} could not be parsed: {exc}")

        observation_time = terrain.get("generated_at")
        resolution_deg = terrain.get("resolution_deg")
        n_points = terrain.get("n_points")

        if not observation_time:
            return self.unavailable(f"{self.terrain_path} has no generated_at field")

        freshness = compute_freshness_minutes(observation_time)
        status = classify_freshness(freshness, LIVE_MAX_MINUTES, CACHED_MAX_MINUTES)
        # DEM is static data -- "LIVE" is a misleading word for terrain that
        # doesn't change. Downgrade a freshness-only LIVE verdict to CACHED;
        # STALE (overdue for regeneration, per the existing fetcher's own
        # MAX_AGE_DAYS policy) is still meaningful and kept as-is.
        from . import SourceStatus
        if status == SourceStatus.LIVE:
            status = SourceStatus.CACHED

        return SourceMetadata(
            source_name=self.source_name,
            source_product=self.source_product,
            observation_time_utc=self._normalize(observation_time),
            acquisition_time_utc=self._normalize(observation_time),
            valid_time_utc=None,
            spatial_resolution=f"{resolution_deg} deg, {n_points} points (~30m native SRTM, aggregated)",
            temporal_resolution="static (terrain does not change); regenerated periodically",
            status=status,
            freshness_minutes=freshness,
            provenance=f"{self.terrain_path} (reused from fetch_dem_terrain.py)",
            quality_flags=["vobl_only_not_pan_india", "no_national_drainage_raster", "heuristic_multiplier_not_trained_feature"],
        )

    @staticmethod
    def _normalize(ts: str) -> str:
        ts = ts.strip()
        if "T" not in ts and " " in ts:
            ts = ts.replace(" ", "T", 1)
        if not ts.endswith("Z") and "+" not in ts:
            ts = ts + "Z"
        return ts
