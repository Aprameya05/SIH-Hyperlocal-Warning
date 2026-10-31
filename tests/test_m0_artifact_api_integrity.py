"""
tests/test_m0_artifact_api_integrity.py
=======================================
M0 Objectives B and C at the API/builder level.

Reproduces the audited failure: a legacy artifact stamped init_time=2024-08-01,
generated in 2026, with CB labelled TRAINED / MIXED_LIVE_SOURCES and one daily
value duplicated across the 2/3/4/5/6 h slots, producing HIGH "alert
candidates" dated 2024.
"""
import json
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import backend.alerts as alerts_mod  # noqa: E402
import backend.unified_api as api  # noqa: E402
from backend import forecast_integrity as fi  # noqa: E402

LEADS = (2, 3, 4, 5, 6)


def _legacy_artifact(generated_at=None, cb_init=None):
    now = datetime.now(timezone.utc)
    generated_at = generated_at or (now - timedelta(hours=1)).isoformat()      # '+00:00' + microseconds
    cb_init = cb_init or (now - timedelta(hours=7)).replace(minute=0, second=0, microsecond=0).isoformat()
    recs = []
    for cid in ("IND_13.0_78.0", "IND_20.0_75.0"):
        for lead in LEADS:
            recs.append({
                "cell_id": cid, "lat": 13.0, "lon": 78.0,
                "init_time": "2024-08-01T00:00:00Z",
                "valid_time": f"2024-08-01T{lead:02d}:00:00Z", "lead_hours": lead,
                "TS": {"probability": None, "risk_category": "NOT_AVAILABLE",
                       "status": "OUT_OF_DOMAIN_STATION_ONLY", "source_status": "NOT_AVAILABLE", "extra": {}},
                "CB": {"probability": 0.74, "risk_category": "HIGH", "status": "TRAINED",
                       "model_version": "panindia_cb_v1", "provenance": "DERIVED", "confidence": "MODERATE",
                       "extra": {}, "source_cycle": "2026-10-30", "source_status": "MIXED_LIVE_SOURCES",
                       "cb_init_time_utc": cb_init,
                       "cb_valid_time_utc": f"2024-08-01T{lead:02d}:00:00Z",
                       "lead_time_resolution": "DAILY_AGGREGATE_NOT_LEAD_SPECIFIC"},
                "FF": {"probability": None, "risk_category": "NOT_AVAILABLE", "status": "PU_RANKING",
                       "source_status": "FIXED_VALIDATION_FALLBACK", "source_cycle": "2024-08-01",
                       "extra": {"pu_ranking_score": 0.9}},
            })
    return {"artifact_type": "legacy", "generated_at_utc": generated_at,
            "init_time_utc": "2024-08-01T00:00:00Z", "source_cycle_date": "2024-08-01",
            "cb_live_fetch": {"status": "MIXED_LIVE_SOURCES", "init_time_utc": cb_init},
            "coverage_summary": {}, "records": recs}


@pytest.fixture
def legacy_client(tmp_path, monkeypatch):
    p = tmp_path / "unified_forecast.json"
    p.write_text(json.dumps(_legacy_artifact()))
    monkeypatch.setattr(api, "UNIFIED_FORECAST_PATH", p)
    monkeypatch.setattr(alerts_mod, "DB_PATH", tmp_path / "a.db")
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c, p


# ---------------------------------------------------------------- B: artifact
def test_forecast_all_never_serves_a_non_current_value_as_a_forecast(legacy_client):
    c, _ = legacy_client
    art = c.get("/forecast/all").json()
    bad = [(r["cell_id"], r["lead_hours"], hz) for r in art["records"] for hz in ("TS", "CB", "FF")
           if (r[hz].get("probability") is not None
               or (r[hz].get("extra") or {}).get("pu_ranking_score") is not None)
           and r[hz].get("temporal_status") != "CURRENT"]
    assert bad == []
    assert art["schema_version"] == 2
    assert art["forecast_horizon_support"]["lead_specific_forecasts_available"] is False


def test_duplicated_cb_is_not_presented_as_five_independent_lead_predictions(legacy_client):
    c, _ = legacy_client
    art = c.get("/forecast/all").json()
    assert all(r["CB"]["probability"] is None for r in art["records"])
    assert {r["CB"]["status"] for r in art["records"]} == {"LEAD_TIME_NOT_SUPPORTED"}
    # the single daily value is kept exactly once per cell, with no lead claim
    for cid in ("IND_13.0_78.0", "IND_20.0_75.0"):
        cb = art["daily_outlook"][cid]["CB"]
        assert cb["probability"] == 0.74 and cb["lead_specific"] is False


def test_no_probability_is_manufactured_to_look_realistic(legacy_client):
    c, _ = legacy_client
    art = c.get("/forecast/all").json()
    vals = {v["CB"]["probability"] for v in art["daily_outlook"].values()}
    assert vals == {0.74}                                      # only the original value, unchanged


def test_historical_values_are_preserved_with_original_timestamps(legacy_client):
    c, _ = legacy_client
    art = c.get("/forecast/all").json()
    ff = art["records"][0]["FF"]
    assert ff["temporal_status"] == "HISTORICAL_REPLAY"
    assert ff["historical_reference"]["pu_ranking_score"] == 0.9
    assert ff["historical_reference"]["model_init_time_utc"] == "2024-08-01T00:00:00Z"
    assert ff["extra"]["pu_ranking_score"] is None


def test_legacy_source_file_on_disk_is_not_rewritten_by_reading(legacy_client):
    c, p = legacy_client
    before = p.read_text()
    c.get("/forecast/all")
    assert p.read_text() == before


def test_missing_artifact_returns_503_not_a_500_or_stale_data(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "UNIFIED_FORECAST_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(alerts_mod, "DB_PATH", tmp_path / "a.db")     # never touch data/alerts.db
    with TestClient(api.app, raise_server_exceptions=False) as c:
        assert c.get("/forecast/all").status_code == 503
        assert c.get("/forecast/alerts").status_code == 503


def test_stale_artifact_daily_outlook_is_not_presentable_as_current(tmp_path, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat()
    p = tmp_path / "u.json"
    p.write_text(json.dumps(_legacy_artifact(generated_at=old, cb_init=(
        datetime.now(timezone.utc) - timedelta(hours=46)).isoformat())))
    monkeypatch.setattr(api, "UNIFIED_FORECAST_PATH", p)
    monkeypatch.setattr(alerts_mod, "DB_PATH", tmp_path / "a.db")
    with TestClient(api.app, raise_server_exceptions=False) as c:
        art = c.get("/forecast/all").json()
    for v in art["daily_outlook"].values():
        assert v["CB"]["temporal_status"] != "CURRENT" and v["CB"]["presentable_as_current_forecast"] is False


def test_old_freshness_helper_now_handles_the_real_timestamp_shape():
    old = (datetime.now(timezone.utc) - timedelta(hours=api.ARTIFACT_STALE_AFTER_HOURS + 5)).isoformat()
    assert "+00:00" in old                                      # the shape real artifacts use
    out = api._reapply_request_time_freshness({"generated_at_utc": old},
                                              {"source_status": "LIVE_AWS_GFS", "probability": 0.3})
    assert out["source_status"] == "STALE"                      # before M0 this silently stayed LIVE


# ---------------------------------------------------------------- C: alerts
def test_2024_dated_high_cb_candidates_no_longer_appear(legacy_client):
    c, _ = legacy_client
    d = c.get("/forecast/alerts", params={"min_risk": "HIGH"}).json()
    assert [x for x in d["candidates"] if x["candidate_class"] == "MODEL_GENERATED_CANDIDATE"] == []
    assert not any((x.get("valid_time") or "").startswith("2024") for x in d["candidates"])
    assert d["n_alert_eligible"] == 0


def test_every_candidate_is_classified_and_never_official_or_verified(legacy_client):
    c, _ = legacy_client
    d = c.get("/forecast/alerts", params={"min_risk": "HIGH"}).json()
    assert d["n_candidates"] == 2
    for x in d["candidates"]:
        assert x["candidate_class"] == "MODEL_GENERATED_DAILY_OUTLOOK_CANDIDATE"
        assert x["is_official_warning"] is False and x["verified"] is False
        assert x["lead_hours"] is None and x["operational_lead_hours"] is None
        assert x["alert_eligible"] is False and x["alert_eligibility_reasons"]
        assert x["event_class"] == "HEAVY_RAIN_DAY_PROXY"


def test_listing_and_dispatch_agree_nothing_is_dispatchable(legacy_client):
    c, _ = legacy_client
    for lead in LEADS:
        r = c.post("/forecast/alerts/dispatch",
                   json={"cell_id": "IND_13.0_78.0", "hazard": "CB", "lead_hours": lead})
        assert r.status_code in (200, 422)
        if r.status_code == 200:
            assert r.json()["status"] == "SKIPPED_NO_ALERT"
            assert r.json()["delivery_status"].startswith("NOT_SENT")
        else:
            assert "refusing to dispatch" in r.json()["detail"]


def test_ff_dispatch_still_refused(legacy_client):
    c, _ = legacy_client
    r = c.post("/forecast/alerts/dispatch", json={"cell_id": "IND_13.0_78.0", "hazard": "FF", "lead_hours": 4})
    assert r.status_code == 400


# ---------------------------------------------------------------- builder validation guard
def test_builder_validation_rejects_a_historical_value_presented_as_current():
    import phase34_build_unified_forecast as builder
    art = fi.sanitize_artifact(_legacy_artifact())
    assert builder.validate_artifact(art)["no_historical_or_duplicated_value_presented_as_current_lead_forecast"] is True
    # regression: someone re-introduces a 2024 value with a probability and no CURRENT status
    broken = json.loads(json.dumps(art))
    broken["records"][0]["CB"]["probability"] = 0.9
    broken["records"][0]["CB"]["temporal_status"] = "HISTORICAL_REPLAY"
    v = builder.validate_artifact(broken)
    assert v["no_historical_or_duplicated_value_presented_as_current_lead_forecast"] is False


def test_builder_validation_requires_time_semantics_block():
    import phase34_build_unified_forecast as builder
    v = builder.validate_artifact(_legacy_artifact())          # unsanitised legacy shape
    assert v["time_semantics_block_present"] is False


# ---------------------------------------------------------------- positive path (replaces coverage of the skipped phase35 test)
def test_a_genuinely_current_lead_specific_candidate_is_listed_eligible_and_dispatches_truthfully(tmp_path, monkeypatch):
    """The gate must still ACCEPT a legitimate value: current init, lead-specific, future valid time,
    live source. With no Twilio/webhook configured the truthful result is NOT_SENT, never SENT."""
    now = datetime.now(timezone.utc)
    init = (now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    valid = (now + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    art = _legacy_artifact(generated_at=(now - timedelta(minutes=30)).isoformat(), cb_init=init.isoformat())
    for r in art["records"]:
        r["valid_time"] = valid.strftime("%Y-%m-%dT%H:%M:%SZ")
        r["init_time"] = init.strftime("%Y-%m-%dT%H:%M:%SZ")
        r["CB"]["lead_time_resolution"] = "HOURLY"          # genuinely lead-specific
        r["CB"]["source_status"] = "LIVE_AWS_GFS"
        r["CB"]["cb_valid_time_utc"] = valid.strftime("%Y-%m-%dT%H:%M:%SZ")
        r["FF"]["source_status"] = "UNAVAILABLE"
        r["FF"]["extra"] = {}
    p = tmp_path / "u.json"
    p.write_text(json.dumps(art))
    monkeypatch.setattr(api, "UNIFIED_FORECAST_PATH", p)
    monkeypatch.setattr(alerts_mod, "DB_PATH", tmp_path / "a.db")
    monkeypatch.setattr(api, "RECIPIENTS", [])
    with TestClient(api.app, raise_server_exceptions=False) as c:
        listing = c.get("/forecast/alerts", params={"min_risk": "HIGH"}).json()
        lead_specific = [x for x in listing["candidates"] if x["candidate_class"] == "MODEL_GENERATED_CANDIDATE"]
        assert lead_specific and all(x["alert_eligible"] for x in lead_specific)
        assert all(x["is_official_warning"] is False and x["verified"] is False for x in lead_specific)
        assert all(x["operational_lead_hours"] > 0 for x in lead_specific)       # lead measured from ISSUE time
        x = lead_specific[0]
        r = c.post("/forecast/alerts/dispatch",
                   json={"cell_id": x["cell_id"], "hazard": "CB", "lead_hours": x["lead_hours"]})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "DISPATCHED" and d["delivery_status"] == "NOT_SENT" and d["sms_count"] == 0
