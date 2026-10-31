"""P0 gates: no automated alert path can fire on historical/heuristic/unsupported values; CI gates fail closed."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import alert_policy as ap  # noqa: E402


def _forecast(prob=0.9):
    return {"generated_at": "2026-10-31 03:15 IST", "alert_active": True,
            "slots": [{"slot": "evening", "ts_probability": prob, "source": "model",
                       "lead_time": {"valid_from": "2099-01-01T00:00:00Z"}}]}


def test_production_policy_is_empty_so_nothing_is_eligible():
    assert ap.PRODUCTION_POLICY.supported_alert_horizons == {} and ap.PRODUCTION_POLICY.alertable_semantics == {}
    res = ap.evaluate_forecast_alerts(_forecast())
    assert res["eligible_slots"] == []
    assert {"NO_AVAILABILITY_TIME", "HORIZON_NOT_REGISTERED", "VALUE_TYPE_NOT_ALERTABLE"} <= set(res["reason_counts"])


def test_automated_guard_withholds_and_says_why(tmp_path):
    f = tmp_path / "forecast.json"
    f.write_text(json.dumps(_forecast()))
    out = subprocess.run([sys.executable, str(ROOT / "scripts/automated_alert_guard.py"), str(f)],
                         capture_output=True, text=True, check=True).stdout
    assert "WITHHELD" in out and "no recorded forecast availability time" in out


def test_send_alerts_cannot_fire_without_policy(monkeypatch):
    src = (ROOT / "send_alerts.py").read_text()
    assert "alert_policy.evaluate_forecast_alerts" in src and "and policy_ok" in src


def test_dispatch_alerts_never_sends():
    src = (ROOT / "backend/dispatch_alerts.py").read_text()
    assert "WITHHELD" in src and "twilio" not in src.lower().replace("withheld", "")


def test_index_html_has_no_imd_branded_automatic_alert():
    html = (ROOT / "index.html").read_text()
    assert "IMD Alert" not in html and "autoBrowserAlertsEnabled = false" in html


def test_workflows_use_fail_closed_gates():
    fu = (ROOT / ".github/workflows/forecast_update.yml").read_text()
    ug = (ROOT / ".github/workflows/update_grid.yml").read_text()
    assert "ci_validate_vobl_forecast.py" in fu and "ci_validate_pan_india_grid.py" in fu
    assert "automated_alert_guard.py" in fu and "ci_validate_pan_india_grid.py" in ug


@pytest.mark.parametrize("script,arg", [("ci_validate_pan_india_grid.py", "grid"), ("ci_validate_vobl_forecast.py", "fc")])
def test_gates_reject_an_invalid_file(tmp_path, script, arg):
    f = tmp_path / "x.json"
    f.write_text(json.dumps({"forged": True}))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / script), str(f)], capture_output=True, text=True)
    assert r.returncode != 0
