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


def test_past_valid_time_is_never_a_lead_and_never_negative():
    """M0.1 (owner decision Q2/Q5): a past or invalid lead returns null + an explicit status, never a negative number."""
    assert fi.operational_lead_hours("2024-08-01T02:00:00Z", ISSUED) is None
    m = fi.lead_metadata("2026-10-30T18:00:00Z", "2024-08-01T02:00:00Z", ISSUED)
    assert m["operational_lead_hours"] is None and m["operational_lead_status"] == "NOT_APPLICABLE_PAST_VALID_TIME"


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
    assert ff["operational_lead_hours"] is None                  # never a negative lead (Q2/Q5)
    assert ff["operational_lead_status"] == "NOT_APPLICABLE_PAST_VALID_TIME"


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


def _lead_specific_record(cid, lead, prob, head="synthetic_cb_lead_head"):
    """SYNTHETIC lead-specific slot: distinct NWP hour, valid time, input fingerprint and probability per lead."""
    init = "2026-10-31T00:00:00+00:00"
    valid = (datetime(2026, 10, 31, 0, tzinfo=timezone.utc) + timedelta(hours=lead + 2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _record(cid, lead, cb=_cb_block(prob=prob, lead_res="HOURLY", init=init))
    r["valid_time"] = valid
    r["CB"].update({"valid_time_utc": valid, "value_type": "CALIBRATED_PROBABILITY", "event_class": "SYNTHETIC_EVENT",
                    "lead_basis": {"model_head": head, "nwp_forecast_hour": lead + 2,
                                   "input_fingerprint": f"fp-{lead}", "inference_id": f"inf-{lead}"}})
    return r


@pytest.fixture
def synthetic_contract(monkeypatch):
    """Exercises the lead-specific CONTRACT with an injected capable head. It proves the verifier's rules only,
    not that DRIFT can forecast by lead (the production registry is empty)."""
    monkeypatch.setattr(fi, "LEAD_CAPABLE_HEADS", frozenset({"synthetic_cb_lead_head"}))
    monkeypatch.setattr(fi, "ALLOW_SYNTHETIC_FIXTURES", True)


def _synthetic_artifact(leads=(2, 3, 4, 5, 6)):
    a = _artifact([_lead_specific_record("IND_13.0_78.0", L, 0.1 * L) for L in leads],
                  generated="2026-10-31T02:52:12+00:00")
    a["cb_live_fetch"] = {"status": "LIVE_AWS_GFS", "init_time_utc": "2026-10-31T00:00:00+00:00"}
    a["forecast_available_time_utc"] = "2026-10-31T03:10:00+00:00"
    a["forecast_available_provenance"] = "SYNTHETIC_TEST"
    return a


def test_genuinely_current_lead_specific_values_are_kept_with_distinct_leads(synthetic_contract):
    art = fi.sanitize_artifact(_synthetic_artifact(), now=NOW)
    cbs = [r["CB"] for r in sorted(art["records"], key=lambda r: r["lead_hours"])]
    assert all(b["temporal_status"] == TS_.CURRENT for b in cbs)
    assert len({b["probability"] for b in cbs}) == 5                       # distinct predictions
    assert len({b["valid_time_utc"] for b in cbs}) == 5                    # distinct valid times
    assert [b["nwp_forecast_hour"] for b in cbs] == [4.0, 5.0, 6.0, 7.0, 8.0]
    leads = [b["operational_lead_hours"] for b in cbs]
    assert all(isinstance(x, float) and x > 0 for x in leads) and leads == sorted(leads)  # from AVAILABILITY time
    assert art["forecast_horizon_support"]["lead_specific_forecasts_available"] is True
    assert art["integrity"]["counts"]["current_lead_specific"] >= 5


def test_the_same_synthetic_artifact_is_rejected_by_the_production_policy():
    """Without the injected head/synthetic switch (production) nothing is lead-specific and the fixture is invalid."""
    art = fi.sanitize_artifact(_synthetic_artifact(), now=NOW)
    assert all(r["CB"]["probability"] is None for r in art["records"])
    assert {r["CB"]["temporal_status"] for r in art["records"]} <= {TS_.INVALID_PROVENANCE, TS_.NOT_LEAD_SPECIFIC}


def test_hourly_label_without_a_capable_head_or_basis_is_not_lead_specific():
    r = _record("IND_1.0_1.0", 2, cb=_cb_block(prob=0.4, lead_res="HOURLY", init="2026-10-31T00:00:00+00:00"))
    art = fi.sanitize_artifact(_artifact([r]), now=NOW)
    assert art["records"][0]["CB"]["probability"] is None
    assert art["records"][0]["CB"]["temporal_status"] == TS_.NOT_LEAD_SPECIFIC     # the label is never sufficient


def test_duplicated_fingerprint_head_not_registered_is_withheld(synthetic_contract):
    r = _lead_specific_record("IND_13.0_78.0", 3, 0.3, head="some_other_head")
    art = fi.sanitize_artifact(_synthetic_artifact([2]) | {"records": [r]}, now=NOW)
    assert art["records"][0]["CB"]["probability"] is None


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
    for k in ("model_init_time_utc", "data_acquired_at_utc", "generated_at_utc",
              "forecast_available_time_utc", "valid_time_utc", "nwp_forecast_hour", "operational_lead_hours"):
        assert k in ts["definitions"]
    assert ts["data_acquired_at_utc"] is None and ts["forecast_available_time_utc"] is None
    assert ts["forecast_available_status"] == "NOT_SUPPLIED_BY_PIPELINE"        # never invented
    assert "2024-08-01" in ts["legacy_field_note"] and "replay" in ts["legacy_field_note"].lower()


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
def test_alert_eligibility_is_one_gate_and_production_registries_are_empty(synthetic_contract):
    """Owner decisions Q14/Q18: every alert goes through backend/alert_policy.evaluate; production registries are
    empty so nothing is eligible; a test policy with explicit registries shows the contract can be satisfied."""
    from backend import alert_policy as ap
    cand = {"probability": 0.8, "temporal_status": TS_.CURRENT, "value_type": "CALIBRATED_PROBABILITY",
            "event_class": "SYNTHETIC_EVENT", "lead_specific": True, "operational_lead_hours": 3.5,
            "operational_lead_status": "OK", "forecast_available_time_utc": "2026-10-31T02:30:00+00:00",
            "forecast_available_provenance": "SYNTHETIC_TEST", "generated_at_utc": "2026-10-31T02:20:00+00:00",
            "valid_time_utc": "2026-11-01T00:00:00Z", "synthetic_fixture": True}
    ok, why = ap.evaluate(cand, hazard="TS", now=NOW)                                  # production policy
    assert not ok and {"HORIZON_NOT_REGISTERED", "SEMANTICS_NOT_REGISTERED", "SYNTHETIC_ARTIFACT_IN_PRODUCTION",
                       "AVAILABILITY_PROVENANCE_NOT_OBSERVED"} <= set(why)
    pol = ap.AlertPolicy(supported_alert_horizons={"TS": (2, 6)},
                         alertable_semantics={"TS": {("SYNTHETIC_EVENT", "CALIBRATED_PROBABILITY")}}, allow_synthetic=True)
    assert ap.evaluate(cand, hazard="TS", now=NOW, policy=pol) == (True, [])
    # a missing availability time is ineligible and is never inferred
    no_avail = dict(cand, forecast_available_time_utc=None)
    ok, why = ap.evaluate(no_avail, hazard="TS", now=NOW, policy=pol)
    assert not ok and "NO_AVAILABILITY_TIME" in why
    for hz in ("FF",):
        assert "HAZARD_NEVER_ALERTABLE" in ap.evaluate(cand, hazard=hz, now=NOW, policy=pol)[1]
    for bad in ("HEURISTIC_RISK_SCORE", "MODEL_SCORE_UNCALIBRATED", "RESEARCH_ONLY_PU_RANKING"):
        assert "VALUE_TYPE_NOT_ALERTABLE" in ap.evaluate(dict(cand, value_type=bad), hazard="TS", now=NOW, policy=pol)[1]
    assert "DAILY_OUTLOOK_NOT_ALERTABLE" in ap.evaluate(dict(cand, is_daily_outlook=True), hazard="TS", now=NOW, policy=pol)[1]
    assert "OPERATIONAL_LEAD_NOT_POSITIVE" in ap.evaluate(dict(cand, operational_lead_hours=0), hazard="TS", now=NOW, policy=pol)[1]
    assert "LEAD_OUTSIDE_SUPPORTED_HORIZON" in ap.evaluate(dict(cand, operational_lead_hours=9), hazard="TS", now=NOW, policy=pol)[1]


# ---------------------------------------------------------------- request-time refresh (D)
def test_request_time_refresh_downgrades_a_once_current_daily_outlook():
    built = fi.sanitize_artifact(_artifact(), now=NOW)
    assert built["daily_outlook"]["IND_13.0_78.0"]["CB"]["temporal_status"] == TS_.CURRENT
    later = NOW + timedelta(hours=30)               # past the window end (init + 27 h)
    out = fi.refresh_request_time_status(built, now=later)
    cb = out["daily_outlook"]["IND_13.0_78.0"]["CB"]
    assert cb["temporal_status"] in (TS_.EXPIRED, TS_.STALE) and cb["presentable_as_current_forecast"] is False
    assert built["daily_outlook"]["IND_13.0_78.0"]["CB"]["temporal_status"] == TS_.CURRENT   # input untouched


def test_request_time_refresh_withholds_a_lead_value_whose_valid_time_passed(synthetic_contract):
    built = fi.sanitize_artifact(_synthetic_artifact([2]), now=NOW)
    assert built["records"][0]["CB"]["probability"] is not None
    out = fi.refresh_request_time_status(built, now=NOW + timedelta(hours=8))      # valid time (06:00Z) has passed
    blk = out["records"][0]["CB"]
    assert blk["probability"] is None and blk["temporal_status"] in (TS_.EXPIRED, TS_.STALE)
    assert blk["historical_reference"]["probability"] is not None                   # original preserved


def test_request_time_refresh_never_upgrades_a_withheld_value():
    built = fi.sanitize_artifact(_artifact(), now=NOW)
    out = fi.refresh_request_time_status(built, now=NOW)
    assert all(r["FF"]["temporal_status"] == TS_.HISTORICAL_REPLAY for r in out["records"])
    assert all(r["CB"]["probability"] is None for r in out["records"])


def test_local_explanations_are_not_left_attached_to_a_withheld_value():
    ts = {"probability": 0.0022, "risk_category": "LOW", "status": "BASELINE", "source_status": "RECENT",
          "extra": {"xai": {"status": "AVAILABLE", "top_contributions": [{"feature": "TT", "contribution": 1.0}]}},
          "xai": {"status": "AVAILABLE", "top_contributions": [{"feature": "TT", "contribution": 1.0}]}}
    art = fi.sanitize_artifact(_artifact([_record("IND_13.0_77.0", 2, ts=ts)]), now=NOW)
    out = art["records"][0]["TS"]
    assert out["probability"] is None and out["xai"]["status"] == "WITHHELD"
    assert out["extra"]["xai"]["status"] == "WITHHELD"
    assert out["historical_reference"]["xai"]["top_contributions"][0]["feature"] == "TT"   # preserved
