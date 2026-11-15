"""Final security contracts: no PII / credentials in the bundle or any API response; alert paths fail closed."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from pipelines.publishing import build_pages_bundle as bpd  # noqa: E402

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)
PHONE = re.compile(r"(?<![\d.])\+?91[\s-]?\d{10}(?!\d)|\+\d{11,13}")
SECRET = re.compile(r"AKIA[0-9A-Z]{16}|AC[0-9a-f]{32}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY|"
                    r"(?i:(api[_-]?key|secret|token|password|auth_token)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9/_\-+=]{16,})")


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    d = tmp_path_factory.mktemp("sec") / "d"
    bpd.build(d)
    return d


def test_no_pii_in_any_published_text_file(dist):
    for f in dist.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".geojson", ".html", ".js", ".css", ".txt", ".md"):
            t = f.read_text(errors="ignore").replace('placeholder="ops@airline.com"', "")   # the only e-mail-like string: a form placeholder
            m = EMAIL.search(t)
            # library licence/attribution addresses in vendored code are not subscriber data
            if "vendor" in f.parts:
                continue
            assert not m, f"e-mail-like string in published {f.relative_to(dist)}"
            assert not PHONE.search(t), f"phone-like number in published {f.relative_to(dist)}"


def test_no_credentials_or_private_material_in_the_bundle(dist):
    for f in dist.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".geojson", ".html", ".js", ".css", ".txt") and "vendor" not in f.parts:
            m = SECRET.search(f.read_text(errors="ignore"))
            assert not m, f"credential-like string in published {f.relative_to(dist)}: {m.group(0)[:12]}..."
    names = [p.name.lower() for p in dist.rglob("*") if p.is_file()]
    assert not [n for n in names if n.endswith((".pkl", ".csv", ".env", ".pem", ".key", ".db", ".grib2"))]
    assert not [n for n in names if "subscriber" in n or "secret" in n or "credential" in n]


def test_tracked_subscribers_file_is_empty():
    assert json.loads((ROOT / "data/subscribers.json").read_text()) == []


def test_no_api_get_route_returns_a_subscriber_or_secret(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.unified_api as api
    import backend.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "DB_PATH", tmp_path / "a.db")
    monkeypatch.setenv("ALERT_RECIPIENTS", "someone@example.com,+919999999999")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "x" * 40)
    seen = 0
    with TestClient(api.app, raise_server_exceptions=False) as c:
        for r in api.app.routes:
            if "GET" in getattr(r, "methods", ()) and "{" not in r.path and r.path not in ("/openapi.json",):
                resp = c.get(r.path, params={"cell_id": "IND_13.0_78.0", "lead_hours": 4})
                seen += 1
                assert not EMAIL.search(resp.text.replace("example.com", "")) or "someone@" not in resp.text, r.path
                assert "someone@example.com" not in resp.text and "+919999999999" not in resp.text and "x" * 40 not in resp.text, r.path
    assert seen >= 5


def test_every_automated_alert_path_is_fail_closed_by_construction():
    from backend import alert_policy as ap
    assert ap.PRODUCTION_POLICY.supported_alert_horizons == {} and ap.PRODUCTION_POLICY.alertable_semantics == {}
    ok, why = ap.evaluate({"probability": 0.99, "temporal_status": "CURRENT", "valid_time_utc": "2099-01-01T00:00:00Z"}, hazard="TS")
    assert ok is False and "NO_AVAILABILITY_TIME" in why
    for rel in ("send_alerts.py", "backend/dispatch_alerts.py", "backend/alerts.py", "pipelines/validation/guard_automated_alerts.py"):
        src = (ROOT / rel).read_text()
        assert "alert_policy" in src or "WITHHELD" in src, rel


def test_simulation_code_has_no_send_or_network_capability():
    for rel in ("frontend/assets/drift_sim.js", "frontend/assets/drift_app.js"):
        src = (ROOT / rel).read_text()
        for bad in ("XMLHttpRequest", "sendBeacon", "WebSocket", "method: 'POST'", "twilio", "ntfy", "navigator.share", "new Notification"):
            assert bad.lower() not in src.lower(), (rel, bad)
    # the only fetches in the command centre are GETs of published JSON
    assert re.findall(r"fetch\(([^,)]+)", (ROOT / "frontend/assets/drift_app.js").read_text()) == ["u"]


def test_bundle_headroom_is_at_least_15_percent(dist):
    big = max(f.stat().st_size for f in dist.rglob("*") if f.is_file())
    assert big <= 0.85 * bpd.MAX_FILE_BYTES, f"{big/2**20:.1f} MiB"
