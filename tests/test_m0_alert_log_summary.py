"""
tests/test_m0_alert_log_summary.py
==================================
M0 Objective C: the alert-log scorecard must never report an undefined skill score as 0.
Audit finding (FRONTEND F2): POD 0% / CSI 0.000 were shown beside four logged HITs because
generate_alert_log.recompute_summary() returned 0.0 when the 30-day window held no events.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import generate_alert_log as gal  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))


def _ev(days_ago, outcome, lead=300):
    d = (datetime.now(IST) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return {"date": d, "verified": outcome != "PENDING", "outcome": outcome,
            "lead_time_min": lead if outcome == "HIT" else None}


def test_empty_window_scores_are_undefined_not_zero():
    old = [_ev(60, "HIT"), _ev(61, "HIT"), _ev(62, "MISS")]
    s = gal.recompute_summary(old)
    assert s["n_events"] == 0
    for k in ("pod", "far", "csi", "hss", "mean_lead_time_min", "mean_lead_time_hr"):
        assert s[k] is None, f"{k} must be null when the window is empty, got {s[k]!r}"
    assert s["n_events_in_log_total"] == 3              # the older records are still counted, not deleted
    assert "No recorded event falls inside the window" in s["note"]


def test_pending_events_are_excluded_from_scores():
    s = gal.recompute_summary([_ev(1, "PENDING")])
    assert s["n_events"] == 0 and s["pod"] is None


def test_normal_window_still_computes_real_values():
    s = gal.recompute_summary([_ev(1, "HIT"), _ev(2, "HIT"), _ev(3, "MISS"), _ev(4, "FALSE ALARM"),
                               _ev(5, "CORRECT REJECTION")])
    assert s["n_events"] == 5
    assert s["pod"] == round(2 / 3, 3)
    assert s["far"] == round(1 / 3, 3)
    assert s["csi"] == round(2 / 4, 3)
    assert s["mean_lead_time_min"] == 300


def test_zero_is_kept_when_it_is_a_real_score():
    s = gal.recompute_summary([_ev(1, "MISS"), _ev(2, "MISS")])      # real POD of 0: events existed, none detected
    assert s["pod"] == 0.0
    assert s["far"] is None                                          # no alerts issued -> FAR undefined


def test_no_hits_with_false_alarms_gives_zero_pod_and_defined_far():
    s = gal.recompute_summary([_ev(1, "FALSE ALARM"), _ev(2, "MISS")])
    assert s["pod"] == 0.0 and s["far"] == 1.0


def test_window_metadata_is_exposed():
    s = gal.recompute_summary([_ev(1, "HIT")])
    assert s["window_days"] == 30
