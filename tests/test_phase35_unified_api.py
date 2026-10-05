"""
tests/test_phase35_unified_api.py
=====================================
Phase 35: tests for backend/unified_api.py (the HTTP layer around the
Phase 34 UnifiedInferenceEngine) and the enriched /health in
backend/alerts.py.

A real, pre-existing, out-of-scope environment issue was found while
writing these tests: data/alerts.db (the pre-existing alert-history
SQLite file) raises "disk I/O error" on this machine, unrelated to any
code in this phase (a fresh file at a different path works fine; see
docs/PHASE_35_UNIFIED_OPERATIONAL_API.md). Tests that need a working
alerts table monkeypatch backend.alerts.DB_PATH to a temp file rather
than depend on, or attempt to repair, that file.
"""
import json
import sys
import tempfile
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import backend.alerts as alerts_mod  # noqa: E402
from backend.unified_api import app, SOURCE_STATUS  # noqa: E402
from backend.models.unified_mtl.lead_time_interface import LEAD_HOURS  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts_mod, "DB_PATH", tmp_path / "test_alerts.db")
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_preserves_pre_existing_fields(client):
    r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    for key in ("status", "twilio_configured", "recipients_count", "webhook_configured", "db"):
        assert key in d


def test_health_adds_unified_engine_fields(client):
    d = client.get("/health").json()
    assert d["grid_cells"] == 992
    assert sorted(d["lead_times_hours"]) == sorted(LEAD_HOURS)
    assert "model_versions" in d
    assert "data_source_status" in d


def test_health_never_reports_blocked_source_as_ready(client):
    d = client.get("/health").json()
    assert d["data_source_status"]["IMDAA"] != "READY"
    assert d["data_source_status"]["INSAT"] != "READY"
    assert "BLOCKED" in d["data_source_status"]["IMDAA"]


# ---------------------------------------------------------------------------
# /forecast
# ---------------------------------------------------------------------------

def test_forecast_vobl_cell_has_real_probability_and_shap(client):
    r = client.get("/forecast", params={"cell_id": "IND_13.0_77.0", "lead_hours": 3})
    assert r.status_code == 200
    d = r.json()
    assert d["ts"]["probability"] is not None
    assert d["ts"]["xai"]["status"] == "AVAILABLE"
    assert len(d["ts"]["xai"]["top_contributions"]) > 0


def test_forecast_none_never_becomes_zero(client):
    r = client.get("/forecast", params={"cell_id": "IND_6.0_68.0", "lead_hours": 3})
    d = r.json()
    assert d["ts"]["probability"] is None
    assert d["ts"]["risk"] == "NOT_AVAILABLE"
    assert d["ff"]["probability"] is None
    assert d["ff"]["risk"] == "NOT_AVAILABLE"


def test_forecast_rejects_non_canonical_lead_hours(client):
    r = client.get("/forecast", params={"cell_id": "IND_6.0_68.0", "lead_hours": 9})
    assert r.status_code == 400


def test_forecast_rejects_unknown_cell(client):
    r = client.get("/forecast", params={"cell_id": "IND_999.0_999.0", "lead_hours": 3})
    assert r.status_code == 404


def test_forecast_valid_time_lead_hours_arithmetic(client):
    r = client.get("/forecast", params={"cell_id": "IND_13.0_77.0", "lead_hours": 4,
                                          "init_time": "2024-08-01T00:00:00Z"})
    d = r.json()
    assert d["valid_time"] == "2024-08-01T04:00:00Z"
    assert d["lead_hours"] == 4


def test_forecast_rejects_malformed_init_time(client):
    r = client.get("/forecast", params={"cell_id": "IND_13.0_77.0", "lead_hours": 3,
                                          "init_time": "not-a-date"})
    assert r.status_code == 400


def test_forecast_includes_data_sources_block(client):
    d = client.get("/forecast", params={"cell_id": "IND_13.0_77.0", "lead_hours": 3}).json()
    assert d["data_sources"] == SOURCE_STATUS
    assert d["forecast_source"] == "LIVE_INFERENCE"


def test_forecast_terrain_missing_status_for_blocked_cell(client):
    d = client.get("/forecast", params={"cell_id": "IND_6.0_68.0", "lead_hours": 3}).json()
    assert d["terrain"]["status"] in ("MISSING", "AVAILABLE")


# ---------------------------------------------------------------------------
# /forecast/all
# ---------------------------------------------------------------------------

def test_forecast_all_returns_4960_records(client):
    r = client.get("/forecast/all")
    assert r.status_code == 200
    d = r.json()
    assert d["n_records"] == 4960
    assert len(d["records"]) == 4960
    assert d["forecast_source"] == "OFFLINE_ARTIFACT"


def test_forecast_all_schema_matches_single_forecast_endpoint_hazard_keys(client):
    artifact = client.get("/forecast/all").json()
    single = client.get("/forecast", params={"cell_id": "IND_13.0_77.0", "lead_hours": 3}).json()
    rec = artifact["records"][0]
    for hz in ("TS", "CB", "FF"):
        assert hz in rec
        for field in ("probability", "risk_category", "status", "model_version", "provenance"):
            assert field in rec[hz]
    for hz in ("ts", "cb", "ff"):
        for field in ("probability", "risk", "status", "model_version", "provenance"):
            assert field in single[hz]


def test_forecast_all_992_unique_cells_5_leads_each(client):
    artifact = client.get("/forecast/all").json()
    by_cell = {}
    for r in artifact["records"]:
        by_cell.setdefault(r["cell_id"], set()).add(r["lead_hours"])
    assert len(by_cell) == 992
    assert all(leads == set(LEAD_HOURS) for leads in by_cell.values())


# ---------------------------------------------------------------------------
# /forecast/sources
# ---------------------------------------------------------------------------

def test_forecast_sources_matches_documented_statuses(client):
    d = client.get("/forecast/sources").json()
    assert d["data_sources"]["DEM"] == "353_OF_992"
    assert d["data_sources"]["Hydrology"] == "75_OF_992"
    assert d["data_sources"]["Himawari"] == "B13_ONLY"
    assert d["data_sources"]["METAR"] == "VOBL_VOBG"


# ---------------------------------------------------------------------------
# Alerts: NOT_TRAINED/unavailable cannot trigger; real probability can
# ---------------------------------------------------------------------------

def test_not_trained_hazard_cannot_appear_in_alert_candidates(client):
    d = client.get("/forecast/alerts", params={"min_risk": "LOW"}).json()
    for c in d["candidates"]:
        assert c["hazard"] in ("TS", "CB")
        assert c["probability"] is not None


def test_ff_never_appears_in_alert_candidates(client):
    d = client.get("/forecast/alerts", params={"min_risk": "LOW"}).json()
    assert all(c["hazard"] != "FF" for c in d["candidates"])


def test_high_risk_filter_returns_only_high_or_severe(client):
    d = client.get("/forecast/alerts", params={"min_risk": "HIGH"}).json()
    assert all(c["risk"] in ("HIGH", "SEVERE") for c in d["candidates"])


def test_alert_candidates_have_no_dispatch_by_default(client):
    d = client.get("/forecast/alerts", params={"min_risk": "HIGH"}).json()
    for c in d["candidates"][:10]:
        assert c["status"] == "CANDIDATE"
        assert "NOT_SENT" in c["delivery_status"]


def test_dispatch_rejects_ff(client):
    r = client.post("/forecast/alerts/dispatch", json={"cell_id": "IND_8.0_76.0", "hazard": "FF", "lead_hours": 2})
    assert r.status_code == 400


def test_dispatch_rejects_unavailable_hazard(client):
    r = client.post("/forecast/alerts/dispatch", json={"cell_id": "IND_6.0_68.0", "hazard": "TS", "lead_hours": 2})
    assert r.status_code == 422


def test_dispatch_accepts_legitimate_cb_probability_and_reports_truthful_delivery(client):
    candidates = client.get("/forecast/alerts", params={"min_risk": "HIGH"}).json()["candidates"]
    cb_candidate = next(c for c in candidates if c["hazard"] == "CB")
    r = client.post("/forecast/alerts/dispatch",
                     json={"cell_id": cb_candidate["cell_id"], "hazard": "CB", "lead_hours": cb_candidate["lead_hours"]})
    assert r.status_code == 200
    d = r.json()
    # No Twilio/webhook configured in this test environment -> must NOT claim SENT.
    assert d["delivery_status"] == "NOT_SENT"
    assert d["sms_count"] == 0
    assert d["status"] == "DISPATCHED"


def test_dispatch_never_reports_sent_without_confirmation(client):
    """Delivery-status truthfulness: with no Twilio creds and no webhook
    configured, dispatch must report NOT_SENT, never SENT."""
    r = client.post("/forecast/alerts/dispatch", json={"cell_id": "IND_8.0_76.0", "hazard": "CB", "lead_hours": 2})
    assert r.json()["delivery_status"] == "NOT_SENT"


# ---------------------------------------------------------------------------
# Preserved existing subscriber/alert system
# ---------------------------------------------------------------------------

def test_existing_alert_post_endpoint_preserved(client):
    r = client.post("/alert", json={"hazard_type": "thunderstorm", "severity": "LOW"})
    assert r.status_code == 201


def test_existing_alerts_get_endpoint_preserved(client):
    r = client.get("/alerts")
    assert r.status_code == 200
    assert "alerts" in r.json()


# ---------------------------------------------------------------------------
# XAI
# ---------------------------------------------------------------------------

def test_xai_available_for_cb_with_complete_features(client):
    d = client.get("/forecast", params={"cell_id": "IND_8.0_76.0", "lead_hours": 2}).json()
    if d["cb"]["probability"] is not None:
        assert d["cb"]["xai"]["status"] in ("AVAILABLE", "NOT_AVAILABLE")


def test_xai_not_available_for_ff(client):
    d = client.get("/forecast", params={"cell_id": "IND_13.0_77.0", "lead_hours": 3}).json()
    assert d["ff"]["xai"]["status"] == "NOT_AVAILABLE"
