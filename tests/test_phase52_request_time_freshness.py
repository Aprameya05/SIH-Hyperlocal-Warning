"""
tests/test_phase52_request_time_freshness.py
=====================================================
Phase 9 (freshness enforcement): before this fix, every endpoint in
backend/unified_api.py that reads data/unified_forecast.json served
each record's source_status exactly as it was frozen at artifact-
GENERATION time, with no re-check against wall-clock "now" at REQUEST
time. If the scheduled GitHub Actions run stopped firing for, say, 2
days (cron outage, manual pause), every endpoint would keep reporting
"LIVE_AWS_GFS" on data that was genuinely 2 days stale -- exactly what
"never present an old artifact as LIVE" forbids. The old
LIVE_SOURCE_STATUSES comment even said so explicitly: "UNAVAILABLE are
never eligible, regardless of generated_at_utc" -- regardless was an
honest description of the gap, not a design choice.

This test verifies _reapply_request_time_freshness downgrades a LIVE
status to STALE once the artifact is older than
ARTIFACT_STALE_AFTER_HOURS, measured against wall-clock now, and
leaves a fresh artifact's status untouched.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_fresh_artifact_keeps_its_live_status():
    from unified_api import _reapply_request_time_freshness, ARTIFACT_STALE_AFTER_HOURS
    artifact = {"generated_at_utc": _iso(datetime.now(timezone.utc) - timedelta(hours=1))}
    hz = {"source_status": "LIVE_AWS_GFS", "probability": 0.3}
    out = _reapply_request_time_freshness(artifact, hz)
    assert out["source_status"] == "LIVE_AWS_GFS"
    assert "original_source_status" not in out


def test_old_artifact_downgrades_live_status_to_stale():
    from unified_api import _reapply_request_time_freshness, ARTIFACT_STALE_AFTER_HOURS
    old_dt = datetime.now(timezone.utc) - timedelta(hours=ARTIFACT_STALE_AFTER_HOURS + 5)
    artifact = {"generated_at_utc": _iso(old_dt)}
    hz = {"source_status": "LIVE_AWS_GFS", "probability": 0.3}
    out = _reapply_request_time_freshness(artifact, hz)
    assert out["source_status"] == "STALE"
    assert out["original_source_status"] == "LIVE_AWS_GFS"
    assert out["artifact_age_hours"] > ARTIFACT_STALE_AFTER_HOURS


def test_non_live_status_is_never_touched():
    """FIXED_VALIDATION_FALLBACK/UNAVAILABLE must never be relabeled --
    this function only ever downgrades a LIVE claim, never upgrades or
    alters an already-honest non-live status."""
    from unified_api import _reapply_request_time_freshness
    old_dt = datetime.now(timezone.utc) - timedelta(days=30)
    artifact = {"generated_at_utc": _iso(old_dt)}
    for status in ("FIXED_VALIDATION_FALLBACK", "UNAVAILABLE", "NOT_AVAILABLE", None):
        hz = {"source_status": status}
        out = _reapply_request_time_freshness(artifact, hz)
        assert out.get("source_status") == status
        assert "original_source_status" not in out


def test_missing_generated_at_utc_degrades_gracefully():
    from unified_api import _reapply_request_time_freshness
    hz = {"source_status": "LIVE_AWS_GFS"}
    out = _reapply_request_time_freshness({}, hz)
    assert out == hz


def test_stale_alert_eligibility_is_rejected_by_the_existing_gate():
    """The real end-to-end requirement: an artifact too old to call LIVE
    must also fail the alert-eligibility check, not just display
    differently -- LIVE_SOURCE_STATUSES membership is the actual gate
    backend/unified_api.py's dispatch endpoint uses."""
    from unified_api import _reapply_request_time_freshness, LIVE_SOURCE_STATUSES, ARTIFACT_STALE_AFTER_HOURS
    old_dt = datetime.now(timezone.utc) - timedelta(hours=ARTIFACT_STALE_AFTER_HOURS + 1)
    artifact = {"generated_at_utc": _iso(old_dt)}
    hz = {"source_status": "LIVE_AWS_GFS", "probability": 0.5}
    out = _reapply_request_time_freshness(artifact, hz)
    assert out["source_status"] not in LIVE_SOURCE_STATUSES
