"""
backend/data_sources
=====================
Phase 1 data-source abstraction layer for the DRIFT pipeline.

This package is a PARALLEL data-provenance layer introduced in Phase 1 of
the "make DRIFT genuinely live" effort (see docs/LIVE_OPERATIONAL_AUDIT.md
and docs/SIH26077_LIVE_REQUIREMENT_MATRIX.md for the audit this is built
against). It does NOT replace, import from, or get imported by the current
production inference path (`backend/pipeline.py`, `forecast_action.py`,
`pan_india_gfs_fetcher.py`). Each adapter here REUSES the existing,
already-working fetchers' *outputs* (the files they already write under
data/) where a working implementation already exists, and reports on them
through one consistent metadata contract -- it does not re-implement
network fetching or duplicate NOMADS/JMA/aviationweather.gov calls.

Design rule enforced throughout this package: an adapter must never report
LIVE merely because an output file exists on disk. It must read that
file's own embedded observation/acquisition timestamp and classify
freshness against an explicit budget, and it must return BLOCKED or
UNAVAILABLE with a concrete, human-readable reason whenever the underlying
source has no genuine live path (missing credentials, scaffold-only
implementation, unverified endpoint).
"""
from __future__ import annotations

import dataclasses
import enum
from datetime import datetime, timezone
from typing import Any, List, Optional


class SourceStatus(str, enum.Enum):
    """The only six states a source adapter may report."""

    LIVE = "LIVE"
    CACHED = "CACHED"
    STALE = "STALE"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    PROXY = "PROXY"


REQUIRED_FIELDS = (
    "source_name",
    "source_product",
    "observation_time_utc",
    "acquisition_time_utc",
    "valid_time_utc",
    "spatial_resolution",
    "temporal_resolution",
    "status",
    "freshness_minutes",
    "provenance",
    "quality_flags",
)

# Statuses for which the adapter is asserting the data is actually usable
# (as opposed to a reason why it is not). LIVE/CACHED/STALE/PROXY all
# require a real observation_time_utc; BLOCKED/UNAVAILABLE do not (there
# is nothing to timestamp).
_DATA_BEARING_STATUSES = (
    SourceStatus.LIVE,
    SourceStatus.CACHED,
    SourceStatus.STALE,
    SourceStatus.PROXY,
)
_REASON_ONLY_STATUSES = (SourceStatus.BLOCKED, SourceStatus.UNAVAILABLE)


class SourceMetadataError(ValueError):
    """Raised when a source adapter produces a malformed metadata contract."""


@dataclasses.dataclass
class SourceMetadata:
    """
    The consistent metadata contract every adapter in this package must
    return from get_metadata(). Field meanings:

    source_name           e.g. "gfs", "himawari", "metar"
    source_product        e.g. "NOAA NOMADS GFS 0.25deg", "JMA Himawari-9 AHI"
    observation_time_utc  when the underlying observation/forecast-init
                           actually occurred (ISO-8601 UTC string), None
                           for BLOCKED/UNAVAILABLE
    acquisition_time_utc  when THIS system fetched/ingested it, None for
                           BLOCKED/UNAVAILABLE
    valid_time_utc        for forecast fields, the time the value is valid
                           for (may equal observation_time_utc for a pure
                           observation); None if not applicable
    spatial_resolution    free-text, e.g. "0.25 deg", "single station",
                           "~2km IR subset"
    temporal_resolution   free-text, e.g. "3-hourly", "10-min cadence"
    status                one of SourceStatus
    freshness_minutes     minutes between observation_time_utc and now;
                           None when not applicable (BLOCKED/UNAVAILABLE)
    provenance            file path / function / reason this value traces
                           back to -- always required, even for BLOCKED
    quality_flags         list of short strings flagging known caveats
                           (e.g. ["proxy_not_direct_observation"])
    """

    source_name: str
    source_product: str
    observation_time_utc: Optional[str]
    acquisition_time_utc: Optional[str]
    valid_time_utc: Optional[str]
    spatial_resolution: str
    temporal_resolution: str
    status: SourceStatus
    freshness_minutes: Optional[float]
    provenance: str
    quality_flags: List[str] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.status, SourceStatus):
            try:
                self.status = SourceStatus(self.status)
            except ValueError as exc:
                raise SourceMetadataError(
                    f"invalid status {self.status!r} for source {self.source_name!r}; "
                    f"must be one of {[s.value for s in SourceStatus]}"
                ) from exc

        if not self.source_name or not isinstance(self.source_name, str):
            raise SourceMetadataError("source_name is required and must be a non-empty string")
        if not self.source_product or not isinstance(self.source_product, str):
            raise SourceMetadataError("source_product is required and must be a non-empty string")

        if self.quality_flags is None:
            self.quality_flags = []
        if not isinstance(self.quality_flags, list):
            raise SourceMetadataError(f"{self.source_name}: quality_flags must be a list")

        if not self.provenance or not isinstance(self.provenance, str):
            raise SourceMetadataError(f"{self.source_name}: provenance is required and must be a non-empty string")

        if self.status in _DATA_BEARING_STATUSES and not self.observation_time_utc:
            raise SourceMetadataError(
                f"{self.source_name}: status {self.status.value} requires a real observation_time_utc "
                f"(a source is never LIVE/CACHED/STALE/PROXY without a timestamp to prove it)"
            )
        if self.status in _REASON_ONLY_STATUSES and self.observation_time_utc:
            raise SourceMetadataError(
                f"{self.source_name}: status {self.status.value} must not carry an observation_time_utc "
                f"(there is no data to timestamp)"
            )

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SourceMetadata":
        missing = [f for f in REQUIRED_FIELDS if f not in d]
        if missing:
            raise SourceMetadataError(f"malformed source metadata, missing required fields: {missing}")
        extra = {k: v for k, v in d.items() if k in REQUIRED_FIELDS}
        return cls(**extra)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (with or without trailing Z) to a UTC-aware datetime."""
    if not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_freshness_minutes(observation_time_utc: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """Minutes elapsed between observation_time_utc and now. None if the timestamp is missing/unparseable."""
    try:
        obs = parse_utc(observation_time_utc)
    except (ValueError, TypeError):
        return None
    if obs is None:
        return None
    now = now or utcnow()
    return max(0.0, (now - obs).total_seconds() / 60.0)


def classify_freshness(
    freshness_minutes: Optional[float],
    live_max_minutes: float,
    cached_max_minutes: float,
) -> SourceStatus:
    """
    Classify LIVE vs CACHED vs STALE purely from an elapsed-time budget.
    This does NOT decide BLOCKED/PROXY/UNAVAILABLE -- callers must rule
    those cases out first (missing credentials, scaffold-only source,
    known proxy substitution). A None freshness always means UNAVAILABLE:
    there is no basis to call anything live.
    """
    if freshness_minutes is None:
        return SourceStatus.UNAVAILABLE
    if freshness_minutes <= live_max_minutes:
        return SourceStatus.LIVE
    if freshness_minutes <= cached_max_minutes:
        return SourceStatus.CACHED
    return SourceStatus.STALE


class BaseSourceAdapter:
    """
    Common adapter interface. Every concrete adapter in this package
    subclasses this and implements get_metadata().

    Subclasses should prefer reading the existing production fetcher's
    already-written output file over re-fetching anything themselves --
    this package is a provenance/reporting layer over the existing
    pipeline in Phase 1, not a second set of network clients.
    """

    source_name: str = "unknown"
    source_product: str = "unknown"

    def get_metadata(self) -> SourceMetadata:
        raise NotImplementedError

    def blocked(
        self,
        reason: str,
        product: Optional[str] = None,
        quality_flags: Optional[List[str]] = None,
    ) -> SourceMetadata:
        """A source that is blocked by a credential/registration/access gate."""
        return SourceMetadata(
            source_name=self.source_name,
            source_product=product or self.source_product,
            observation_time_utc=None,
            acquisition_time_utc=None,
            valid_time_utc=None,
            spatial_resolution="n/a",
            temporal_resolution="n/a",
            status=SourceStatus.BLOCKED,
            freshness_minutes=None,
            provenance=reason,
            quality_flags=quality_flags or [],
        )

    def unavailable(
        self,
        reason: str,
        product: Optional[str] = None,
        quality_flags: Optional[List[str]] = None,
    ) -> SourceMetadata:
        """A source with no current data to report (missing output file, parse failure, etc.)."""
        return SourceMetadata(
            source_name=self.source_name,
            source_product=product or self.source_product,
            observation_time_utc=None,
            acquisition_time_utc=None,
            valid_time_utc=None,
            spatial_resolution="n/a",
            temporal_resolution="n/a",
            status=SourceStatus.UNAVAILABLE,
            freshness_minutes=None,
            provenance=reason,
            quality_flags=quality_flags or [],
        )


__all__ = [
    "SourceStatus",
    "SourceMetadata",
    "SourceMetadataError",
    "BaseSourceAdapter",
    "REQUIRED_FIELDS",
    "utcnow",
    "parse_utc",
    "compute_freshness_minutes",
    "classify_freshness",
]
