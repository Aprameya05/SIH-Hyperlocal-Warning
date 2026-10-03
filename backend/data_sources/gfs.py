"""
backend/data_sources/gfs.py
============================
Reuses the existing, operational GFS/NOMADS pipeline's OUTPUT (it does not
re-implement NOMADS fetching -- see gfs_fetcher.py and
pan_india_gfs_fetcher.py at the repo root for the real fetch logic, both of
which this adapter treats as upstream producers of the files it reads).

Two outputs exist in production:
  - data/gfs_realtime_43295.csv   (VOBL point fetch, appended each cycle)
  - data/pan_india_grid.json      (pan-India 992-cell grid fetch)

This adapter reports on whichever is requested via `scope`. It never makes
a network call itself.
"""
from __future__ import annotations

import csv
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

VOBL_CSV_PATH = REPO_ROOT / "data" / "gfs_realtime_43295.csv"
PAN_INDIA_GRID_PATH = REPO_ROOT / "data" / "pan_india_grid.json"

# GFS is cycle-gated (00/06/12/18Z, 0.25deg, 3-hourly output steps). A cycle
# is published ~4-5 hours after its nominal time; budgets reflect that.
LIVE_MAX_MINUTES = 8 * 60      # within one cycle + publication latency
CACHED_MAX_MINUTES = 24 * 60   # one stale cycle behind is "cached", not yet "stale"


def _last_csv_row(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-1] if rows else None


class GFSSource(BaseSourceAdapter):
    """
    scope="vobl"      -> data/gfs_realtime_43295.csv (station point fetch)
    scope="pan_india" -> data/pan_india_grid.json (992-cell grid fetch)
    """

    source_name = "gfs"
    source_product = "NOAA NOMADS GFS 0.25deg"

    def __init__(self, scope: str = "vobl", vobl_csv_path: Path = VOBL_CSV_PATH,
                 pan_india_grid_path: Path = PAN_INDIA_GRID_PATH):
        if scope not in ("vobl", "pan_india"):
            raise ValueError(f"GFSSource: unknown scope {scope!r}, expected 'vobl' or 'pan_india'")
        self.scope = scope
        self.vobl_csv_path = vobl_csv_path
        self.pan_india_grid_path = pan_india_grid_path

    def get_metadata(self) -> SourceMetadata:
        if self.scope == "vobl":
            return self._vobl_metadata()
        return self._pan_india_metadata()

    def _vobl_metadata(self) -> SourceMetadata:
        row = _last_csv_row(self.vobl_csv_path)
        if row is None:
            return self.unavailable(
                f"{self.vobl_csv_path} missing or empty -- no VOBL GFS fetch has run yet",
                product="NOAA NOMADS GFS 0.25deg (VOBL point)",
            )

        acquisition_time = row.get("fetched_at_utc") or row.get("fetched_at")
        cycle = row.get("gfs_cycle")  # e.g. "2026-10-02 18Z f012"
        observation_time = self._cycle_to_iso(cycle) if cycle else acquisition_time

        if not acquisition_time:
            return self.unavailable(
                f"{self.vobl_csv_path} last row has no fetched_at_utc/fetched_at column",
                product="NOAA NOMADS GFS 0.25deg (VOBL point)",
            )

        freshness = compute_freshness_minutes(observation_time)
        status = classify_freshness(freshness, LIVE_MAX_MINUTES, CACHED_MAX_MINUTES)

        return SourceMetadata(
            source_name=self.source_name,
            source_product="NOAA NOMADS GFS 0.25deg (VOBL point)",
            observation_time_utc=observation_time,
            acquisition_time_utc=self._normalize(acquisition_time),
            valid_time_utc=observation_time,
            spatial_resolution="0.25 deg (single point, nearest-neighbor VOBL)",
            temporal_resolution="cycle-gated, 00/06/12/18Z, 3-hourly steps",
            status=status,
            freshness_minutes=freshness,
            provenance=f"{self.vobl_csv_path} (reused from gfs_fetcher.py, last row)",
            quality_flags=["forecast_field_not_observation"],
        )

    def _pan_india_metadata(self) -> SourceMetadata:
        if not self.pan_india_grid_path.exists():
            return self.unavailable(
                f"{self.pan_india_grid_path} missing -- no pan-India GFS grid fetch has run yet",
                product="NOAA NOMADS GFS 0.25deg (pan-India 992-cell grid)",
            )
        try:
            with open(self.pan_india_grid_path) as f:
                grid = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return self.unavailable(
                f"{self.pan_india_grid_path} could not be parsed: {exc}",
                product="NOAA NOMADS GFS 0.25deg (pan-India 992-cell grid)",
            )

        observation_time = grid.get("generated_at_utc")
        if not observation_time:
            return self.unavailable(
                f"{self.pan_india_grid_path} has no generated_at_utc field",
                product="NOAA NOMADS GFS 0.25deg (pan-India 992-cell grid)",
            )

        freshness = compute_freshness_minutes(observation_time)
        status = classify_freshness(freshness, LIVE_MAX_MINUTES, CACHED_MAX_MINUTES)
        n_cells = grid.get("n_cells")
        step_deg = grid.get("grid_step_deg")

        return SourceMetadata(
            source_name=self.source_name,
            source_product="NOAA NOMADS GFS 0.25deg (pan-India 992-cell grid)",
            observation_time_utc=self._normalize(observation_time),
            acquisition_time_utc=self._normalize(observation_time),
            valid_time_utc=self._normalize(observation_time),
            spatial_resolution=f"{step_deg} deg grid, {n_cells} cells (native GFS 0.25deg)",
            temporal_resolution="cycle-gated, 00/06/12/18Z",
            status=status,
            freshness_minutes=freshness,
            provenance=f"{self.pan_india_grid_path} (reused from pan_india_gfs_fetcher.py)",
            quality_flags=["1deg_grid_is_not_hyperlocal"],
        )

    @staticmethod
    def _cycle_to_iso(cycle: str) -> Optional[str]:
        """'2026-10-02 18Z f012' -> '2026-10-02T18:00:00+00:00' (cycle init time, not valid time)."""
        try:
            date_part, rest = cycle.split(" ", 1)
            hour_part = rest.split(" ")[0]  # "18Z"
            hour = int(hour_part.rstrip("Z"))
            return f"{date_part}T{hour:02d}:00:00+00:00"
        except (ValueError, IndexError, AttributeError):
            return None

    @staticmethod
    def _normalize(ts: str) -> str:
        """Best-effort: ensure a bare 'YYYY-MM-DD HH:MM' string reads as UTC ISO-8601."""
        ts = ts.strip()
        if "T" not in ts and " " in ts:
            ts = ts.replace(" ", "T", 1)
        if not ts.endswith("Z") and "+" not in ts:
            ts = ts + "Z"
        return ts
