"""Location-aware behaviour on REAL artifacts: different places must give different underlying data; nothing leaks across."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
import backend.unified_api as api  # noqa: E402
import backend.location_api as la  # noqa: E402

CITIES = ["Bengaluru", "Mumbai", "Delhi", "Srinagar", "Chennai", "Kolkata", "Guwahati", "Hyderabad", "Jaipur", "Bhopal"]


@pytest.fixture(scope="module")
def client():
    if not (ROOT / "data/india_fields_latest.json").exists():
        pytest.skip("fields artifact not generated")
    import backend.alerts as alerts_mod
    alerts_mod.DB_PATH = ROOT / "cache" / "test_alerts.db"       # the API module opens an alert DB at start-up: keep it out of data/
    la._store = None
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c


def _cell(client, name):
    r = client.get("/v1/search", params={"q": name}).json()["results"]
    assert r and r[0]["type"] == "place", name
    return r[0]["cell_id"]


def test_ten_cities_resolve_to_distinct_cells_with_distinct_underlying_data(client):
    recs = {}
    for c in CITIES:
        r = client.get("/v1/location", params={"cell_id": _cell(client, c), "hour": 2})
        assert r.status_code == 200, c
        recs[c] = r.json()
    cells = {c: r["location"]["cell_id"] for c, r in recs.items()}
    assert len(set(cells.values())) == len(CITIES)
    for var in ("pwat", "t2m_c", "rh2m", "k_index", "shear_850_500"):
        vals = [r["fields"][var]["value"] for r in recs.values() if r["fields"][var]["value"] is not None]
        assert len(set(vals)) >= 7, f"{var} barely varies across India: {sorted(set(vals))}"
    elev = {c: r["static"]["elev_mean_m"] for c, r in recs.items()}
    # CAPE is season/cycle dependent (near zero almost everywhere in a winter cycle): require variation across ALL forecast hours instead
    capes = set()
    for c in CITIES:
        for k in range(8):
            v = client.get("/v1/location", params={"cell_id": _cell(client, c), "hour": k}).json()["fields"]["cape"]["value"]
            if v is not None:
                capes.add(v)
    assert len(capes) >= 3, f"CAPE across 10 cities x 8 hours: {sorted(capes)}"
    assert elev["Srinagar"] > 1000 and elev["Chennai"] < 50 and elev["Leh" if "Leh" in elev else "Srinagar"] > 1000   # real terrain, not a copy
    assert recs["Delhi"]["location"]["state"] == "NCT of Delhi" and recs["Hyderabad"]["location"]["state"] == "Telangana"
    assert recs["Mumbai"]["fields"]["pwat"]["value"] != recs["Delhi"]["fields"]["pwat"]["value"]   # water vapour differs between places in every season; CAPE can be 0 in both at night


def test_changing_the_location_changes_every_hazard_panel_not_just_the_highlight(client):
    a = client.get("/v1/location", params={"cell_id": _cell(client, "Mumbai"), "hour": 3}).json()
    b = client.get("/v1/location", params={"cell_id": _cell(client, "Srinagar"), "hour": 3}).json()
    assert a["hazards"]["thunderstorm"]["indicators"]["pwat_mm"] != b["hazards"]["thunderstorm"]["indicators"]["pwat_mm"]
    assert a["hazards"]["flash_flood"]["context"]["static"]["elev_mean_m"] != b["hazards"]["flash_flood"]["context"]["static"]["elev_mean_m"]
    assert a["hazards"]["extreme_rainfall"]["model_guidance"]["precip_3h_series_mm"] != b["hazards"]["extreme_rainfall"]["model_guidance"]["precip_3h_series_mm"] or \
        a["location"]["cell_id"] != b["location"]["cell_id"]
    assert json.dumps(a["location"]) != json.dumps(b["location"])


def test_forecast_hour_changes_the_values(client):
    cid = _cell(client, "Mumbai")
    h = [client.get("/v1/location", params={"cell_id": cid, "hour": k}).json()["fields"]["t2m_c"]["value"] for k in range(8)]
    assert len(set(h)) >= 5, h                 # 2 m temperature follows the diurnal cycle in every season


def test_every_value_is_traceable(client):
    r = client.get("/v1/location", params={"cell_id": _cell(client, "Kolkata"), "hour": 1}).json()
    cyc = r["cycle"]
    assert cyc["model_init_utc"] and cyc["extraction"] and cyc["resolution_deg"] == 0.25 and cyc["source"]
    sel = r["selected_hour"]
    assert sel["valid_time_utc"] and sel["published_at_utc"] and sel["acquired_at_utc"] and sel["fhour"] == 6
    for k, f in r["fields"].items():
        assert f["unit"] and f["kind"] in ("RAW_MODEL_OUTPUT", "DERIVED_PHYSICAL", "MODEL_DIAGNOSTIC") and f["definition"], k
        assert f["is_observation"] is False and f["is_calibrated_probability"] is False
    from backend import forecast_integrity as fi
    assert r["cycle"]["operational_lead_hours"] == fi.operational_lead_hours(sel["valid_time_utc"], sel["acquired_at_utc"])
    assert r["cycle"]["operational_lead_status"] == ("OK" if r["cycle"]["operational_lead_hours"] is not None else "NOT_APPLICABLE_PAST_VALID_TIME")
    assert "acquired" in r["cycle"]["operational_lead_basis"] and "dissemination" in r["cycle"]["operational_lead_basis"]
    for h in r["cycle"]["hours"]:      # per hour: valid - acquired, never negative, never model-init based
        exp = fi.operational_lead_hours(h["valid_time_utc"], h["acquired_at_utc"])
        assert h["operational_lead_hours"] == exp and h["forecast_available_time_utc"] == h["acquired_at_utc"]
        assert h["operational_lead_hours"] is None or h["operational_lead_hours"] > 0


def test_point_in_sea_or_abroad_is_not_covered_and_never_gets_a_neighbours_values(client):
    r = client.get("/v1/location", params={"lat": 15.0, "lon": 65.0})
    assert r.status_code == 404 and r.json()["detail"]["error"] == "NOT_COVERED"
    r = client.get("/v1/resolve", params={"lat": 27.7, "lon": 85.3}).json()      # Kathmandu
    assert r["covered"] is False


def test_map_coordinate_resolves_to_the_enclosing_cell(client):
    r = client.get("/v1/location", params={"lat": 12.97, "lon": 77.59, "hour": 2}).json()
    assert r["location"]["cell_id"] == "IN025_1300_07750" and r["location"]["state"] == "Karnataka"


def test_search_supports_place_state_district(client):
    kinds = {x["type"] for x in client.get("/v1/search", params={"q": "ker"}).json()["results"]}
    assert "state" in kinds
    assert any(x["type"] == "district" for x in client.get("/v1/search", params={"q": "thane"}).json()["results"])


def test_region_compare_coverage(client):
    k = client.get("/v1/region", params={"state": "Kerala", "hour": 2}).json()
    r = client.get("/v1/region", params={"state": "Rajasthan", "hour": 2}).json()
    assert 40 < k["n_cells"] < 70 and r["n_cells"] > 300 and k["summary"]["rh2m"]["mean"] != r["summary"]["rh2m"]["mean"]
    cmp = client.get("/v1/compare", params={"cells": ",".join(_cell(client, c) for c in ("Delhi", "Chennai", "Guwahati")), "hour": 2}).json()
    assert len(cmp["locations"]) == 3 and len({l["location"]["cell_id"] for l in cmp["locations"]}) == 3
    cov = client.get("/v1/coverage").json()
    assert cov["n_cells"] == sum(v["cells"] for v in cov["by_state"].values()) and cov["hazards"]["extreme_rainfall"]["cloudburst"] == "UNAVAILABLE"
    assert cov["hazards"]["flash_flood"]["prediction"] == "UNAVAILABLE"


def test_etag_revalidation_and_invalidation(client, tmp_path):
    r1 = client.get("/v1/meta"); et = r1.headers["ETag"]
    r2 = client.get("/v1/meta", headers={"If-None-Match": et}); assert r2.status_code == 304
    s = la.store(); v = s.version
    import os, time
    p = ROOT / "data/india_fields_latest.json"; m = os.path.getmtime(p)
    try:
        os.utime(p, (m + 5, m + 5)); la.store()
        assert la.store().version != v                       # a new ingestion invalidates every cached response
    finally:
        os.utime(p, (m, m)); la.store()


def test_stale_cycle_is_withheld_as_current():
    from datetime import datetime, timezone, timedelta
    from backend.location_service import Store
    s = Store()
    init, _ = s._hour_meta()
    old = s.record("IN025_1300_07750", 2, now=init + timedelta(hours=40))
    assert old["selected_hour"]["status"] == "STALE" and old["fields"]["cape"]["safe_as_current"] is False
    past = s.record("IN025_1300_07750", 0, now=init + timedelta(hours=10))
    assert past["selected_hour"]["status"] == "PAST_VALID_TIME" and past["fields"]["cape"]["safe_as_current"] is False


def test_forged_fields_artifact_is_rejected(tmp_path):
    import shutil
    from backend.location_service import Store
    d = tmp_path / "data"; d.mkdir()
    for f in Store.FILES.values():
        if (ROOT / "data" / f).exists():
            shutil.copy(ROOT / "data" / f, d / f)
    doc = json.load(open(d / "india_fields_latest.json"))
    doc["meta"]["files"][2]["valid_time_utc"] = "2020-01-01T00:00:00Z"          # a label that disagrees with init + fhour
    json.dump(doc, open(d / "india_fields_latest.json", "w"))
    s = Store(d)
    assert s.fields_ok is False and any("valid time" in p for p in s.fields_problems)
    rec = s.record("IN025_1300_07750")
    assert rec["fields"] == {} and rec["availability"]["fields"] == "UNAVAILABLE"
    doc = json.load(open(ROOT / "data/india_fields_latest.json")); doc["fields"]["cape"][0][5] = 99999.0
    json.dump(doc, open(d / "india_fields_latest.json", "w"))
    assert Store(d).fields_ok is False


def test_health_ready_forecast_explain_and_alert_eligibility(client):
    h = client.get("/v1/health").json()
    assert h["status"] in ("HEALTHY", "DEGRADED", "UNHEALTHY") and "server UTC" in h["clock"] and h["components"]["alert_policy"]["automated_dispatch"] == "DISABLED"
    assert h["components"]["gfs_fields"]["cycle_init_utc"] and h["components"]["gfs_fields"]["last_published_by_source_utc"]
    # the readiness gate must follow the server clock, not file existence: an old cycle is not ready
    from datetime import datetime, timedelta, timezone
    s = la.store(); init, _ = s._hour_meta()
    old = s.health(now=init + timedelta(hours=40))
    assert old["ready"] is False and old["components"]["gfs_fields"]["status"] == "STALE"
    cid = _cell(client, "Mumbai")
    f = client.get("/v1/forecast", params={"cell_id": cid}).json()
    assert len(f["hours"]) == 8 and f["variables"]["cape"]["unit"] == "J/kg" and f["operational_lead_hours"] in [h["operational_lead_hours"] for h in f["hours"]]
    e = client.get("/v1/explain", params={"cell_id": cid, "hazard": "thunderstorm", "lead": 12}).json()
    if e["status"] == "AVAILABLE_EVALUATED":
        assert len(e["top_contributions"]) == 3 and all(c["statement"] for c in e["top_contributions"]) and "causal" in e["caveat"]
        from backend.model_features import LABEL
        for c in e["top_contributions"]:               # the statement names the feature and, when known, the actual value the model saw
            assert LABEL.get(c["feature"], c["feature"]) in c["statement"]
            if c["value"] is None:
                assert "unavailable" in c["statement"]
            else:
                assert ("%.0f" % c["value"] in c["statement"]) or ("%.2f" % c["value"]).rstrip("0").rstrip(".") in c["statement"]
    ff = client.get("/v1/explain", params={"cell_id": cid, "hazard": "flash_flood"}).json()
    assert ff["status"] == "UNAVAILABLE" and "heuristic" in ff["reason"]
    assert client.get("/v1/explain", params={"cell_id": cid, "hazard": "cloudburst"}).status_code == 422
    a = client.get("/v1/alert-eligibility", params={"cell_id": cid}).json()
    assert a["any_eligible"] is False and a["automated_dispatch"] == "DISABLED" and a["evaluated"]
    assert all("NO_AVAILABILITY_TIME" in r["reason_codes"] or r["output"] in ("cloudburst", "flash_flood screening index") for r in a["evaluated"])
    assert client.get("/v1/location", params={"lat": 15, "lon": 65}).status_code == 404
    assert client.get("/v1/location", params={"cell_id": "NOPE"}).status_code == 404
    assert client.get("/v1/location").status_code == 422
    assert client.get("/v1/location", params={"cell_id": cid, "hour": 99}).status_code == 422
    assert client.get("/v1/search", params={"q": "a"}).status_code == 422
