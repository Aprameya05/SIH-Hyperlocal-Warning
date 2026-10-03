"""
backend/data_sources/metar.py
===============================
Reuses the existing, operational VOBL METAR implementation's OUTPUT.
fetch_metar.py at the repo root performs the actual aviationweather.gov
fetch and injects the parsed observation directly into forecast.json's
top-level "metar" key -- this adapter reads that key, it does not call
aviationweather.gov itself.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import BaseSourceAdapter, SourceMetadata, classify_freshness, compute_freshness_minutes

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FORECAST_JSON_PATH = REPO_ROOT / "forecast.json"

# METAR obs are issued roughly hourly (plus SPECIs); minutes-scale freshness.
LIVE_MAX_MINUTES = 90
CACHED_MAX_MINUTES = 6 * 60


class METARSource(BaseSourceAdapter):
    source_name = "metar"
    source_product = "NOAA/IMD METAR surface observation (VOBL 43295)"

    def __init__(self, forecast_json_path: Path = FORECAST_JSON_PATH):
        self.forecast_json_path = forecast_json_path

    def get_metadata(self) -> SourceMetadata:
        if not self.forecast_json_path.exists():
            return self.unavailable(f"{self.forecast_json_path} missing -- no forecast cycle has run yet")

        try:
            with open(self.forecast_json_path) as f:
                forecast = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return self.unavailable(f"{self.forecast_json_path} could not be parsed: {exc}")

        metar = forecast.get("metar")
        if not metar:
            return self.unavailable(f"{self.forecast_json_path} has no top-level 'metar' key")

        observation_time = metar.get("obs_time")
        acquisition_time = metar.get("fetched_utc")
        station = metar.get("station", "VOBL")

        if not observation_time:
            return self.unavailable(f"{self.forecast_json_path}['metar'] has no obs_time field")

        freshness = compute_freshness_minutes(observation_time)
        status = classify_freshness(freshness, LIVE_MAX_MINUTES, CACHED_MAX_MINUTES)

        return SourceMetadata(
            source_name=self.source_name,
            source_product=f"NOAA/IMD METAR surface observation ({station} 43295)",
            observation_time_utc=observation_time,
            acquisition_time_utc=acquisition_time or observation_time,
            valid_time_utc=observation_time,
            spatial_resolution="single station (point observation)",
            temporal_resolution="~hourly (plus SPECI on significant change)",
            status=status,
            freshness_minutes=freshness,
            provenance=f"{self.forecast_json_path}['metar'] (reused from fetch_metar.py)",
            quality_flags=[],
        )
