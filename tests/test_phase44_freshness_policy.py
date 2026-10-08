"""
tests/test_phase44_freshness_policy.py
=========================================
2026-10-08: tests for scripts/freshness_policy.py -- the centralized
freshness-status computation introduced to stop ad hoc, inconsistent
age thresholds from being scattered across backend/unified_api.py,
index.html, and the artifact builder, and to guarantee freshness is
always computed from a real SOURCE timestamp, never from an
artifact's own rewrite time.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import freshness_policy as fp  # noqa: E402


def test_fresh_gfs_cycle_is_live():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    src = (now - timedelta(hours=1)).isoformat()
    r = fp.compute_freshness("GFS", src, now=now)
    assert r.freshness_status == "LIVE"
    assert r.age_hours == 1.0


def test_six_hour_old_gfs_cycle_is_recent_not_live():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    src = (now - timedelta(hours=6)).isoformat()
    r = fp.compute_freshness("GFS", src, now=now)
    assert r.freshness_status == "RECENT"


def test_two_day_old_gfs_cycle_is_stale_never_live():
    """The exact bug reported: a forecast ~48h old must never be LIVE."""
    now = datetime(2026, 10, 8, 17, 49, tzinfo=timezone.utc)
    src = (now - timedelta(hours=48)).isoformat()
    r = fp.compute_freshness("GFS", src, now=now)
    assert r.freshness_status == "STALE"
    assert r.freshness_status != "LIVE"
    assert r.age_hours == 48.0


def test_missing_source_timestamp_is_unavailable_never_fabricated():
    r = fp.compute_freshness("GFS", None)
    assert r.freshness_status == "UNAVAILABLE"
    assert r.age_hours is None


def test_unparseable_source_timestamp_is_unavailable():
    r = fp.compute_freshness("GFS", "not-a-timestamp")
    assert r.freshness_status == "UNAVAILABLE"


def test_fixed_validation_never_promoted_regardless_of_age():
    """A fixed validation cycle must stay FIXED_VALIDATION_FALLBACK even
    if its artifact was just rewritten (recent retrieved_at) -- age
    alone must never promote it to LIVE/RECENT/STALE."""
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    src = now.isoformat()  # "just now" by timestamp alone
    r = fp.compute_freshness("GFS", src, now=now, is_fixed_validation=True)
    assert r.freshness_status == "FIXED_VALIDATION_FALLBACK"


def test_source_specific_thresholds_differ():
    """METAR's live window is tighter than GFS's -- same age, different
    verdict, proving thresholds are genuinely per-source, not one
    global number silently reused everywhere."""
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    src = (now - timedelta(hours=3)).isoformat()
    gfs_result = fp.compute_freshness("GFS", src, now=now)
    metar_result = fp.compute_freshness("METAR", src, now=now)
    assert gfs_result.freshness_status == "LIVE"
    assert metar_result.freshness_status != "LIVE"


def test_is_alert_eligible_matches_phase10_rule():
    assert fp.is_alert_eligible("LIVE") is True
    assert fp.is_alert_eligible("RECENT") is True
    assert fp.is_alert_eligible("STALE") is False
    assert fp.is_alert_eligible("UNAVAILABLE") is False
    assert fp.is_alert_eligible("FIXED_VALIDATION_FALLBACK") is False


def test_age_never_negative_for_a_source_timestamp_in_the_future():
    """Clock skew or a mis-set source timestamp must never produce a
    negative age (which could otherwise masquerade as 'extra fresh')."""
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    src = (now + timedelta(minutes=5)).isoformat()
    r = fp.compute_freshness("GFS", src, now=now)
    assert r.age_seconds >= 0
