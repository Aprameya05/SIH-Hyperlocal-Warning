"""
tests/test_m0_forecast_integrity.py
===================================
M0 Objectives B, C(eligibility), D, E for backend/forecast_integrity.py.
Pure unit tests: no network, no model loading.
"""
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import forecast_integrity as fi  # noqa: E402
from backend.forecast_integrity import TemporalStatus as TS_  # noqa: E402

NOW = datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc)
ISSUED = "2026-10-31T02:52:12.020666+00:00"        # real artifacts use this shape
CB_INIT = "2026-10-30T18:00:00+00:00"
REPLAY_INIT = "2024-08-01T00:00:00Z"


def _cb_block(prob=0.31, status="MIXED_LIVE_SOURCES", lead_res=fi.LEAD_NOT_SPECIFIC, init=CB_INIT):
    return {"probability": prob, "risk_category": "MODERATE", "status": "TRAINED",
            "model_version": "panindia_cb_v1", "provenance": "DERIVED", "confidence": "MODERATE",
            "extra": {}, "value_type": "probability", "source_cycle": "2026-10-30",
            "source_status": status, "cb_init_time_utc": init,
            "lead_time_resolution": lead_res}


def _record(cell, lead, cb=None, ts=None, ff=None):
    return {"cell_id": cell, "lat": 13.0, "lon": 78.0, "init_time": REPLAY_INIT,
            "valid_time": f"2024-08-01T{lead:02d}:00:00Z", "lead_hours": lead,
            "TS": ts or {"probability": None, "risk_category": "NOT_AVAILABLE",
                         "status": "OUT_OF_DOMAIN_STATION_ONLY", "source_status": "NOT_AVAILABLE",
                         "extra": {}},
            "CB": cb or _cb_block(),
            "FF": ff or {"probability": None, "risk_category": "NOT_AVAILABLE", "status": "PU_RANKING",
                         "source_status": "FIXED_VALIDATION_FALLBACK", "source_cycle": "2024-08-01",
                         "extra": {"pu_ranking_score": 0.8}}}


def _artifact(records=None, cb_status="MIXED_LIVE_SOURCES", generated=ISSUED):
    recs = records or [_record("IND_13.0_78.0", lead) for lead in (2, 3, 4, 5, 6)]
    return {"generated_at_utc": generated, "init_time_utc": REPLAY_INIT, "source_cycle_date": "2024-08-01",
            "cb_live_fetch": {"status": cb_status, "init_time_utc": CB_INIT},
            "coverage_summary": {}, "records": recs}


# ---------------------------------------------------------------- time semantics (D)
@pytest.mark.parametrize("s", ["2026-10-30T21:46:28.474323+00:00", "2026-10-30T21:46:28Z",
                               "2026-10-30T21:46:28+00:00", "2026-10-30"])
def test_parse_utc_accepts_every_shape_used_in_artifacts(s):
    assert fi.parse_utc(s).tzinfo is not None


@pytest.mark.parametrize("bad", [None, "", "not-a-time", 42, "2026-13-45"])
def test_parse_utc_returns_none_never_guesses(bad):
    assert fi.parse_utc(bad) is None


def test_original_strptime_bug_is_covered():
    # The pre-M0 freshness code used strptime('%Y-%m-%dT%H:%M:%SZ'), which raises on this real value.
    with pytest.raises(ValueError):
        datetime.strptime(ISSUED, "%Y-%m-%dT%H:%M:%SZ")
    assert fi.parse_utc(ISSUED) is not None


def test_operational_lead_is_valid_minus_issue_not_valid_minus_init():
    # Real live-grid numbers: GFS cycle 18Z, f009 valid 03:00Z, issued 01:54Z.
    init, valid, issued = "2026-10-30T18:00:00Z", "2026-10-31T03:00:00Z", "2026-10-31T01:54:18Z"
    assert fi.nwp_lead_hours(valid, init) == 9.0
    assert fi.operational_lead_hours(valid, issued) == pytest.approx(1.095, abs=0.01)
    assert fi.operational_lead_hours(valid, issued) != fi.nwp_lead_hours(valid, init)


def test_operational_lead_is_negative_when_valid_time_already_passed():
    assert fi.operational_lead_hours("2024-08-01T02:00:00Z", ISSUED) < 0


def test_operational_lead_function_has_no_model_init_parameter():
    import inspect
    assert "init" not in " ".join(inspect.signature(fi.operational_lead_hours).parameters)


@pytest.mark.parametrize("init,valid_end,issued,now,expected", [
    ("2026-10-30T18:00:00Z", "2026-10-31T09:00:00Z", "2026-10-31T02:00:00Z", NOW, TS_.CURRENT),
    ("2026-10-30T18:00:00Z", "2026-10-31T02:00:00Z", "2026-10-31T02:00:00Z", NOW, TS_.EXPIRED),
    ("2026-10-30T06:00:00Z", "2026-11-01T09:00:00Z", "2026-10-30T10:00:00Z", NOW, TS_.STALE),  # issued 17 h ago
    ("2024-08-01T00:00:00Z", "2024-08-01T06:00:00Z", "2026-10-31T02:00:00Z", NOW, TS_.HISTORICAL_REPLAY),
    (None, "2026-10-31T09:00:00Z", "2026-10-31T02:00:00Z", NOW, TS_.UNAVAILABLE),
    ("2026-10-30T18:00:00Z", "2026-10-31T09:00:00Z", None, NOW, TS_.UNAVAILABLE),
])
def test_classify_time_scenarios(init, valid_end, issued, now, expected):
    assert fi.classify_time(model_init=init, valid_end=valid_end, issued_at=issued, now=now) == expected


# ---------------------------------------------------------------- artifact integrity (B)
def test_duplicated_daily_cb_is_withheld_from_every_lead_slot():
    art = fi.sanitize_artifact(_artifact(), now=NOW)
    for rec in art["records"]:
        cb = rec["CB"]
        assert cb["probability"] is None and cb["risk_category"] == "NOT_AVAILABLE"
        assert cb["status"] == "LEAD_TIME_NOT_SUPPORTED"
        assert cb["temporal_status"] == TS_.NOT_LEAD_SPECIFIC
        assert cb["presentable_as_current_forecast"] is False
    assert art["forecast_horizon_support"]["lead_specific_forecasts_available"] is False
    assert art["forecast_horizon_support"]["supported_operational_horizons_hours"] == []


def test_daily_value_is_preserved_once_with_honest_window_and_no_lead_claim():
    art = fi.sanitize_artifact(_artifact(), now=NOW)
    cb = art["daily_outlook"]["IND_13.0_78.0"]["CB"]
    assert cb["probability"] == 0.31 and cb["lead_specific"] is False
    assert cb["resolution"] == "DAILY_AGGREGATE"
    assert cb["window_start_utc"] == "2026-10-31T00:00:00Z"   # init 18Z + 6 h
    assert cb["window_end_utc"] == "2026-10-31T21:00:00Z"     # init 18Z + 27 h
    assert cb["event_class"] == "HEAVY_RAIN_DAY_PROXY"
    assert "NOT a verified hourly cloudburst" in cb["event_definition"]
    assert cb["value_type"] == "MODEL_SCORE_UNCALIBRATED"
    assert cb["temporal_status"] == TS_.CURRENT


def test_historical_replay_is_not_masked_by_a_2026_generation_timestamp():
    art = fi.sanitize_artifact(_artifact(), now=NOW)
    ff = art["records"][0]["FF"]
    assert ff["temporal_status"] == TS_.HISTORICAL_REPLAY
    assert ff["extra"]["pu_ranking_score"] is None            # not shown as current
    ref = ff["historical_reference"]
    assert ref["pu_ranking_score"] == 0.8                     # original preserved
    assert ref["model_init_time_utc"] == "2024-08-01T00:00:00Z"   # original timestamp kept
    assert ref["designation"] == TS_.HISTORICAL_REPLAY
    assert ff["operational_lead_hours"] < 0


def test_trained_mixed_live_label_is_replaced_for_withheld_values():
    art = fi.sanitize_artifact(_artifact(), now=NOW)
    assert all(r["CB"]["source_status"] == "NOT_LEAD_SPECIFIC" for r in art["records"])
    assert all(r["CB"]["status"] != "TRAINED" for r in art["records"])


def test_fixed_validation_fallback_is_always_historical_even_with_recent_looking_init():
    rec = _record("IND_1.0_1.0", 2, cb=_cb_block(status="FIXED_VALIDATION_FALLBACK", lead_res="HOURLY"))
    art = fi.sanitize_artifact(_artifact([rec], cb_status="FIXED_VALIDATION_FALLBACK"), now=NOW)
    assert art["records"][0]["CB"]["temporal_status"] == TS_.HISTORICAL_REPLAY
    assert art["records"][0]["CB"]["probability"] is None
    assert art["daily_outlook"] == {}
    assert art["artifact_mode"] == "HISTORICAL_REPLAY_ONLY"


def test_genuinely_current_lead_specific_value_is_kept():
    cb = _cb_block(prob=0.4, lead_res="HOURLY", init="2026-10-31T00:00:00+00:00")
    rec = _record("IND_1.0_1.0", 2, cb=cb)
    rec["valid_time"] = "2026-10-31T06:00:00Z"
    art = fi.sanitize_artifact(_artifact([rec]), now=NOW)
    out = art["records"][0]["CB"]
    assert out["probability"] == 0.4 and out["temporal_status"] == TS_.CURRENT
    assert out["operational_lead_hours"] == pytest.approx(3.12, abs=0.05)   # valid - issued, not valid - init
    assert out["nwp_lead_hours"] == 6.0
    assert art["integrity"]["counts"]["current_lead_specific"] == 1


def test_stale_artifact_downgrades_at_read_time():
    art = fi.sanitize_artifact(_artifact(generated="2026-10-30T10:00:00+00:00"), now=NOW)
    assert art["daily_outlook"]["IND_13.0_78.0"]["CB"]["temporal_status"] in (TS_.STALE, TS_.EXPIRED)
    assert art["daily_outlook"]["IND_13.0_78.0"]["CB"]["presentable_as_current_forecast"] is False


def test_missing_generated_at_makes_everything_unavailable_not_current():
    art = fi.sanitize_artifact(_artifact(generated=None), now=NOW)
    assert all(r["CB"]["probability"] is None for r in art["records"])
    assert all(r["FF"]["temporal_status"] != TS_.CURRENT for r in art["records"])


def test_partial_data_missing_cb_live_fetch_block():
    a = _artifact()
    del a["cb_live_fetch"]
    art = fi.sanitize_artifact(a, now=NOW)
    assert art["time_semantics"]["model_init_times_utc"]["CB_daily_outlook"] is None
    assert all(r["CB"]["probability"] is None for r in art["records"])


def test_nulls_stay_null_and_are_not_zeroed():
    rec = _record("IND_1.0_1.0", 2, cb=_cb_block(prob=None))
    art = fi.sanitize_artifact(_artifact([rec]), now=NOW)
    assert art["records"][0]["CB"]["probability"] is None
    assert art["records"][0]["TS"]["probability"] is None
    assert "IND_1.0_1.0" not in art["daily_outlook"]


def test_sanitize_is_idempotent_and_does_not_mutate_input():
    a = _artifact()
    before = copy.deepcopy(a)
    once = fi.sanitize_artifact(a, now=NOW)
    twice = fi.sanitize_artifact(once, now=NOW)
    assert a == before
    assert twice == once


def test_time_semantics_block_defines_every_term_and_flags_legacy_fields():
    ts = fi.sanitize_artifact(_artifact(), now=NOW)["time_semantics"]
    for k in ("model_init_time_utc", "data_acquired_at_utc", "artifact_generated_at_utc",
              "issued_at_utc", "valid_time_utc", "nwp_lead_hours", "operational_lead_hours"):
        assert k in ts["definitions"]
    assert ts["data_acquired_at_utc"] is None and ts["published_at_utc"] is None
    assert "2024-08-01" in ts["legacy_field_note"] and "REPLAY" in ts["legacy_field_note"]


# ---------------------------------------------------------------- real committed artifact
def test_real_legacy_artifact_cannot_masquerade_as_current_lead_forecasts():
    path = REPO_ROOT / "data" / "unified_forecast.json"
    if not path.exists():
        pytest.skip("data/unified_forecast.json not present")
    art = fi.sanitize_artifact(json.load(open(path)))
    presentable = [(r["cell_id"], r["lead_hours"], hz) for r in art["records"] for hz in fi.HAZARDS
                   if r[hz].get("probability") is not None and r[hz].get("temporal_status") != TS_.CURRENT]
    assert presentable == []        # no value with a probability is non-current
    assert art["integrity"]["counts"]["current_lead_specific"] == 0
    assert art["forecast_horizon_support"]["lead_slots_2_to_6h"].startswith("UNAVAILABLE")
    assert all(r["record_class"] == "LEAD_SLOT" for r in art["records"])


# ---------------------------------------------------------------- alert eligibility (C)
def test_alert_eligibility_is_single_source_of_truth():
    ok = {"probability": 0.8, "temporal_status": TS_.CURRENT}
    future = "2026-11-01T00:00:00Z"
    assert fi.eligible_for_alert(ok, hazard="TS", valid_time=future, now=NOW) == (True, [])
    ok_, why = fi.eligible_for_alert(ok, hazard="FF", valid_time=future, now=NOW)
    assert not ok_ and any("FF" in w for w in why)
    ok_, why = fi.eligible_for_alert({"probability": 0.9, "temporal_status": TS_.HISTORICAL_REPLAY},
                                     hazard="CB", valid_time=future, now=NOW)
    assert not ok_ and any("HISTORICAL_REPLAY" in w for w in why)
    ok_, why = fi.eligible_for_alert(ok, hazard="CB", valid_time="2024-08-01T02:00:00Z", now=NOW)
    assert not ok_ and any("not in the future" in w for w in why)
    ok_, _ = fi.eligible_for_alert({"probability": None, "temporal_status": TS_.CURRENT},
                                   hazard="CB", valid_time=future, now=NOW)
    assert not ok_
