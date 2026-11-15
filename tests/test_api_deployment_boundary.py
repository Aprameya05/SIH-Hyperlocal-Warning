"""The /v1 API is hosted separately from the static site: CORS deny-by-default, error handling, valid and invalid parameters,
and the static bundle never pretends to serve /v1 (a real 404.html is published)."""
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api_app import create_app

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = "https://sih-hyperlocal-warning.pages.dev"


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app([ORIGIN]))


def test_cors_allows_only_listed_origins_and_no_credentials(client):
    ok = client.get("/v1/meta", headers={"Origin": ORIGIN})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in ok.headers
    bad = client.get("/v1/meta", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers
    pre = client.options("/v1/meta", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 200 and "GET" in pre.headers["access-control-allow-methods"] and "POST" not in pre.headers["access-control-allow-methods"]
    assert client.post("/v1/meta", headers={"Origin": ORIGIN}).status_code == 405


def test_default_app_denies_all_cross_origin_and_wildcard_is_refused(monkeypatch):
    monkeypatch.delenv("DRIFT_CORS_ORIGINS", raising=False)
    c = TestClient(create_app())
    assert "access-control-allow-origin" not in c.get("/v1/meta", headers={"Origin": ORIGIN}).headers
    with pytest.raises(RuntimeError):
        create_app(["*"])


def test_unknown_paths_are_json_404_and_invalid_inputs_are_422(client):
    r = client.get("/v1/nonexistent"); assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    for p in ["/v1/resolve?lat=abc&lon=1", "/v1/resolve?lat=95&lon=77", "/v1/search?q=a", "/v1/search", "/v1/region", "/v1/explain?hazard=bogus&cell_id=x"]:
        assert client.get(p).status_code == 422, p
    assert client.get("/v1/location?cell_id=NOPE").status_code == 404
    assert client.get("/v1/compare?cells=NOPE").status_code == 404
    assert client.get("/v1/region?state=Atlantis").status_code == 404


def test_region_and_compare_with_valid_parameters(client):
    leh = client.get("/v1/resolve?lat=34.16&lon=77.58").json()["cell_id"]
    pb = client.get("/v1/resolve?lat=11.67&lon=92.75").json()["cell_id"]
    c = client.get(f"/v1/compare?cells={leh},{pb}&hour=3")
    assert c.status_code == 200
    locs = c.json()["locations"]
    assert [x["location"]["cell_id"] for x in locs] == [leh, pb]
    t = [x["fields"]["t2m_c"]["value"] for x in locs]
    assert t[0] is not None and t[1] is not None and t[0] < t[1] - 10          # Leh (4.4 km high) vs Andaman coast
    for x in locs:
        assert x["selected_hour"]["valid_time_utc"].endswith("Z") and x["selected_hour"]["status"] in ("CURRENT", "PAST_VALID_TIME", "STALE")
    r = client.get("/v1/region?state=Jammu%20%26%20Kashmir&hour=3")
    assert r.status_code == 200 and r.json()
    r2 = client.get("/v1/region?district=Leh(Ladakh)")
    assert r2.status_code == 200
    assert client.get("/v1/region?state=Jammu%20%26%20Kashmir&hour=99").status_code == 422
    assert client.get(f"/v1/compare?cells={leh},{pb}&hour=99").status_code == 422
    assert client.get(f"/v1/location?cell_id={leh}&hour=99").status_code == 422


def test_health_and_ready_report_real_dependencies(client):
    h = client.get("/v1/health").json()
    assert h["status"] in ("HEALTHY", "DEGRADED", "UNHEALTHY") and {"grid", "static_layer", "gfs_fields"} <= set(h["components"])
    assert h["components"]["grid"]["n_cells"] == 4643
    assert client.get("/v1/ready").json()["ready"] in (True, False)


def test_static_bundle_has_a_404_page_and_no_api(tmp_path):
    out = tmp_path / "pages_dist"
    subprocess.check_call([sys.executable, str(ROOT / "pipelines/publishing/build_pages_bundle.py"), str(out)], stdout=subprocess.DEVNULL)
    assert (out / "404.html").is_file() and "not deployed" in (out / "404.html").read_text()
    assert not (out / "v1").exists() and not list(out.glob("**/location_api*")) and not list(out.glob("**/*.py"))
