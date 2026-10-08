#!/usr/bin/env python3
"""
scripts/freshness_policy.py
==============================
Single authoritative source of data-freshness thresholds and the
status vocabulary (2026-10-08 pass): "ARTIFACT GENERATION TIME must
never be confused with METEOROLOGICAL DATA FRESHNESS" is a hard
correctness requirement, not a UI nicety -- a forecast whose underlying
GFS cycle is 48 hours old must never read as current merely because
the artifact file that wraps it was rewritten five minutes ago.

Before this module, freshness thresholds were scattered and
inconsistent: index.html's isLiveData used ageHours<14 against
generated_at (artifact age, not data age); backend/unified_api.py's
alert gate checked LIVE_SOURCE_STATUSES membership with no explicit
age window at all. This module gives every caller (backend, artifact
builder, alerts, frontend via an exposed endpoint) the SAME thresholds
and the SAME status computation, so "LIVE" means the same thing
everywhere.

Freshness must always be computed from the real source/init timestamp
of the underlying data (e.g. a GFS cycle's init_time), never from when
a wrapping artifact file was last written.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# Status vocabulary, ordered from freshest to least -- any caller that
# needs "is this fresh enough to alert on" should compare against this
# order, not hardcode a status-name list.
STATUS_ORDER = ["LIVE", "RECENT", "STALE", "UNAVAILABLE", "FIXED_VALIDATION_FALLBACK"]

# Per-source thresholds, in minutes, as (live_max, recent_max). Beyond
# recent_max is STALE. These are the ONLY numbers that should ever
# define these boundaries -- change them here, not at each call site.
# GFS cycles every 6h; a cycle is normally available within ~4-5h of
# its init time, so 240min/480min gives real operational headroom
# without calling a merely-slightly-delayed cycle stale.
THRESHOLDS_MINUTES = {
    "GFS": (240, 480),
    "NOMADS_GFS": (240, 480),
    "METAR": (60, 180),
    "IMERG": (60, 240),
    "HIMAWARI": (30, 90),
    "DEFAULT": (60, 360),
}


@dataclass
class FreshnessResult:
    source: str
    source_timestamp_utc: Optional[str]
    retrieved_at_utc: str
    age_seconds: Optional[float]
    age_minutes: Optional[float]
    age_hours: Optional[float]
    freshness_status: str
    fallback_status: Optional[str] = None


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        cleaned = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def compute_freshness(source: str, source_timestamp_utc: Optional[str],
                       now: Optional[datetime] = None,
                       is_fixed_validation: bool = False) -> FreshnessResult:
    """The one function every caller should use to turn a real source
    timestamp into a freshness status. Never infers freshness from an
    artifact's generated_at/rewrite time -- source_timestamp_utc must be
    the underlying data's own init/observation time.

    is_fixed_validation=True short-circuits straight to
    FIXED_VALIDATION_FALLBACK regardless of age -- a fixed historical
    validation cycle must never be promoted to LIVE/RECENT/STALE no
    matter how "fresh" its artifact write time looks."""
    now = now or datetime.now(timezone.utc)
    retrieved_at = now.isoformat()

    if is_fixed_validation:
        return FreshnessResult(
            source=source, source_timestamp_utc=source_timestamp_utc, retrieved_at_utc=retrieved_at,
            age_seconds=None, age_minutes=None, age_hours=None,
            freshness_status="FIXED_VALIDATION_FALLBACK",
            fallback_status="research/validation data only -- never current conditions",
        )

    parsed = _parse_iso(source_timestamp_utc) if source_timestamp_utc else None
    if parsed is None:
        return FreshnessResult(
            source=source, source_timestamp_utc=source_timestamp_utc, retrieved_at_utc=retrieved_at,
            age_seconds=None, age_minutes=None, age_hours=None,
            freshness_status="UNAVAILABLE",
            fallback_status="no valid source timestamp -- cannot assess freshness",
        )

    age_seconds = max(0.0, (now - parsed).total_seconds())
    age_minutes = age_seconds / 60.0
    age_hours = age_minutes / 60.0

    live_max, recent_max = THRESHOLDS_MINUTES.get(source, THRESHOLDS_MINUTES["DEFAULT"])
    if age_minutes <= live_max:
        status = "LIVE"
    elif age_minutes <= recent_max:
        status = "RECENT"
    else:
        status = "STALE"

    return FreshnessResult(
        source=source, source_timestamp_utc=source_timestamp_utc, retrieved_at_utc=retrieved_at,
        age_seconds=round(age_seconds, 1), age_minutes=round(age_minutes, 1), age_hours=round(age_hours, 2),
        freshness_status=status,
    )


def is_alert_eligible(freshness_status: str) -> bool:
    """Centralizes the alert-eligibility rule (Phase 10): only LIVE and
    RECENT may ever trigger a real dispatch. STALE/UNAVAILABLE/
    FIXED_VALIDATION_FALLBACK never do, regardless of source."""
    return freshness_status in ("LIVE", "RECENT")
