"""
tests/test_m0_forecast_endpoint_semantics.py
============================================
M0 Objectives A, D, E for GET /forecast: historical replay must not be mistakable
for a current forecast, operational lead comes from ISSUE time, and unavailable
inputs stay null.
"""
import sys
import warnings
from datetime import datetime, timezone
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


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts_mod, "DB_PATH", tmp_path / "a.db")
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c


def _get(client, cell="IND_13.0_78.0", lead=4, **kw):
    r = client.get("/forecast", params={"cell_id": cell, "lead_hours": lead, **kw})
    assert r.status_code == 200, r.text[:300]
    return r.json()


def test_default_request_is_explicitly_not_a_current_forecast(client):
    d = _get(client)
    assert d["forecast_status"] == "NO_CURRENT_FORECAST"
    assert d["forecast_source"] == "HISTORICAL_REPLAY_INFERENCE"
    assert d["forecast_horizon_support"]["lead_specific_forecasts_available"] is False
    assert d["forecast_horizon_support"]["supported_operational_horizons_hours"] == []
    for hz in ("ts", "cb", "ff"):
        assert d[hz]["probability"] is None and d[hz]["risk"] == "NOT_AVAILABLE"
        assert d[hz]["temporal_status"] in ("HISTORICAL_REPLAY", "NOT_LEAD_SPECIFIC")
        assert d[hz]["presentable_as_current_forecast"] is False


def test_replay_values_keep_their_original_timestamps_and_designation(client):
    d = _get(client, init_time="2024-08-01T00:00:00Z", lead=4)
    r = d["historical_replay"]
    assert r["designation"] == "HISTORICAL_REPLAY"
    assert r["init_time"] == "2024-08-01T00:00:00Z" and r["valid_time"] == "2024-08-01T04:00:00Z"
    assert r["ts"]["probability"] is not None          # the research output is preserved, not deleted
    assert d["valid_time"] == "2024-08-01T04:00:00Z" and "NOT the time of current data" in d["legacy_time_note"]


def test_an_unavailable_init_time_is_rejected_not_relabelled(client):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = client.get("/forecast", params={"cell_id": "IND_13.0_78.0", "lead_hours": 4, "init_time": now})
    assert r.status_code == 422
    assert "2024-08-01T00:00:00Z" in r.json()["detail"] and "no live lead-specific" in r.json()["detail"]


def test_operational_lead_is_measured_from_issue_time_not_model_init(client):
    d = _get(client, lead=4)
    for hz in ("ts", "cb", "ff"):
        # M0.1 (Q1/Q2/Q5): no availability time exists, and a 2024 valid time is past -> null + status, never negative
        assert d[hz]["operational_lead_hours"] is None
        assert d[hz]["operational_lead_status"] in ("NOT_APPLICABLE_PAST_VALID_TIME", "UNAVAILABLE_NO_AVAILABILITY_TIME")


def test_issue_time_is_request_time(client):
    before = datetime.now(timezone.utc)
    d = _get(client)
    issued = fi.parse_utc(d["issued_at_utc"])
    assert before.replace(microsecond=0) <= issued <= datetime.now(timezone.utc)


def test_ff_pu_score_is_not_exposed_as_a_current_value(client):
    d = _get(client)
    assert d["extra"]["ff_pu_ranking_score"] is None
    assert "ff_pu_ranking_score" in d["historical_replay"]


def test_explanations_are_not_shown_for_a_withheld_value(client):
    d = _get(client)
    for hz in ("ts", "cb"):
        assert d[hz]["xai"]["status"] in ("WITHHELD", "NOT_AVAILABLE")
        assert "top_contributions" not in d[hz]["xai"]


def test_missing_inputs_stay_null_never_zero(client):
    d = _get(client, cell="IND_6.0_68.0", lead=3)
    assert d["ts"]["probability"] is None and d["ff"]["probability"] is None
    assert d["ts"]["risk"] == "NOT_AVAILABLE"
    t = d["terrain"]
    if t["status"] == "MISSING":
        assert t["elevation_m"] is None and t["slope_deg"] is None


def test_unsupported_leads_are_rejected_or_reported_unavailable(client):
    assert client.get("/forecast", params={"cell_id": "IND_13.0_78.0", "lead_hours": 9}).status_code == 400
    assert _get(client, lead=2)["forecast_horizon_support"]["lead_specific_forecasts_available"] is False


def test_time_semantics_definitions_are_exposed_to_clients(client):
    d = _get(client)
    defs = d["time_semantics"]["definitions"]
    assert {"operational_lead_hours", "nwp_forecast_hour", "generated_at_utc", "forecast_available_time_utc"} <= set(defs)
    assert d["time_semantics"]["model_init_times_utc_by_hazard"]["TS"].startswith("2025-12-31")
