"""
tests/test_m0_forecast_api_regression.py
========================================
M0 Objective A regression tests for backend/unified_api.py GET /forecast.

Original failure (CRITICAL_BUGS #15): backend/unified_api.py read
ff_feature_adapter.FF_REALTIME_CEILING_NOTE, which was never defined, so every
valid GET /forecast raised AttributeError -> HTTP 500.
"""
import sys
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import backend.alerts as alerts_mod  # noqa: E402
from backend.unified_api import app  # noqa: E402
import ff_feature_adapter  # noqa: E402

VALID = {"cell_id": "IND_13.0_78.0", "lead_hours": 6}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts_mod, "DB_PATH", tmp_path / "m0_alerts.db")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_ff_realtime_ceiling_note_is_defined_and_explains_the_ceiling():
    note = ff_feature_adapter.FF_REALTIME_CEILING_NOTE
    assert isinstance(note, str) and len(note) > 40
    assert "IMD" in note and "real-time" in note
    assert "probability" in note  # states FF is never a probability


def test_valid_forecast_request_no_longer_returns_500(client):
    r = client.get("/forecast", params=VALID)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["ff_realtime_ceiling_note"] == ff_feature_adapter.FF_REALTIME_CEILING_NOTE


@pytest.mark.parametrize("params,code", [
    ({"cell_id": "IND_13.0_78.0"}, 422),                              # lead_hours missing
    ({"lead_hours": 6}, 422),                                         # cell_id missing
    ({"cell_id": "IND_13.0_78.0", "lead_hours": "x"}, 422),           # wrong type
    ({"cell_id": "IND_13.0_78.0", "lead_hours": 99}, 400),            # unsupported lead
    ({"cell_id": "NOPE", "lead_hours": 6}, 404),                      # unknown cell
    ({"cell_id": "IND_13.0_78.0", "lead_hours": 6, "init_time": "yesterday"}, 400),  # bad time
])
def test_invalid_requests_return_proper_client_errors_not_500(client, params, code):
    assert client.get("/forecast", params=params).status_code == code


def test_other_endpoints_unaffected(client):
    assert client.get("/health").status_code == 200
    assert client.get("/forecast/sources").status_code == 200
    assert client.get("/forecast/all").status_code == 200
    assert client.get("/forecast/alerts", params={"min_risk": "HIGH"}).status_code == 200
