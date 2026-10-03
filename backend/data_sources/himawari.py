"""
backend/data_sources/himawari.py
==================================
Reuses the existing, operational Himawari-9 implementation's OUTPUT
(fetch_himawari_realtime.py at the repo root does the actual JMA/AWS S3
fetch -- this adapter reads what it already writes to
data/himawari_realtime.json, it does not re-fetch anything itself).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import (
    BaseSourceAdapter,
    SourceMetadata,
    SourceStatus,
    classify_freshness,
    compute_freshness_minutes,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REALTIME_PATH = REPO_ROOT / "data" / "himawari_realtime.json"

# Himawari-9 full-disk native cadence is ~10 min; production fetch runs on
# the same cadence as the forecast cycle. A wider live window than GFS is
# still appropriate since this is a near-real-time satellite product.
LIVE_MAX_MINUTES = 45
CACHED_MAX_MINUTES = 6 * 60


class HimawariSource(BaseSourceAdapter):
    source_name = "himawari"
    source_product = "JMA Himawari-9 AHI (IR, sub-km subset)"

    def __init__(self, realtime_path: Path = REALTIME_PATH):
        self.realtime_path = realtime_path

    def get_metadata(self) -> SourceMetadata:
        if not self.realtime_path.exists():
            return self.unavailable(f"{self.realtime_path} missing -- no Himawari fetch has run yet")

        try:
            with open(self.realtime_path) as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return self.unavailable(f"{self.realtime_path} could not be parsed: {exc}")

        observation_time = record.get("timestamp_utc")
        data_source = record.get("data_source", "")

        if not observation_time:
            return self.unavailable(f"{self.realtime_path} has no timestamp_utc field")

        # The existing fetcher honestly labels its own fallback/failure modes
        # in the data_source field (e.g. "stale_fallback", "unavailable").
        # Never override that honesty by inferring LIVE from freshness alone.
        quality_flags = ["ctt_not_confirmed_wired_to_pipeline"]
        if data_source and data_source.lower() not in ("live", "s3", "jaxa", "aws_s3"):
            quality_flags.append(f"fetcher_reported_data_source={data_source}")

        freshness = compute_freshness_minutes(observation_time)
        status = classify_freshness(freshness, LIVE_MAX_MINUTES, CACHED_MAX_MINUTES)

        # If the fetcher itself says this record came from a stale fallback,
        # that overrides a freshness-only classification -- the file's own
        # admission of staleness is authoritative.
        if data_source and "stale" in data_source.lower():
            status = SourceStatus.STALE

        return SourceMetadata(
            source_name=self.source_name,
            source_product=self.source_product,
            observation_time_utc=self._normalize(observation_time),
            acquisition_time_utc=self._normalize(observation_time),
            valid_time_utc=self._normalize(observation_time),
            spatial_resolution="~2km IR subset (50km radius around VOBL)",
            temporal_resolution="~10 min native cadence",
            status=status,
            freshness_minutes=freshness,
            provenance=f"{self.realtime_path} (reused from fetch_himawari_realtime.py)",
            quality_flags=quality_flags,
        )

    @staticmethod
    def _normalize(ts: str) -> str:
        ts = ts.strip()
        if not ts.endswith("Z") and "+" not in ts:
            ts = ts + "Z"
        return ts
