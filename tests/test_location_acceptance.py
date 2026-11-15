"""20 named Indian cities + 8 rural/coastal/mountain coordinates: the cell resolves correctly and its values equal what eccodes reads
independently from the same raw GRIB (needs network: re-downloads one forecast hour; skipped when AWS is unreachable)."""
import pytest

from scripts.utilities import run_location_acceptance as acc


def test_named_locations_resolve_and_match_the_raw_grib_independently():
    pytest.importorskip("eccodes")
    try:
        res = acc.run(hour_index=3, with_raw=True)
    except Exception as e:  # noqa: BLE001 - offline CI
        if "urlopen" in repr(e) or "URLError" in type(e).__name__ or "timed out" in str(e):
            pytest.skip(f"AWS GFS mirror unreachable: {e}")
        raise
    rows = res["locations"]
    assert len(rows) >= 26
    covered = [r for r in rows if r.get("status") != "NOT_COVERED"]
    assert len(covered) >= 25
    names = {r["location"] for r in rows}
    assert {"Bengaluru", "Mumbai", "Delhi", "Chennai", "Kolkata", "Hyderabad", "Pune", "Jaipur", "Ahmedabad", "Guwahati", "Shillong", "Srinagar", "Leh", "Dehradun",
            "Kochi", "Visakhapatnam", "Bhopal", "Lucknow", "Bhubaneswar", "Port Blair"} <= names
    for r in covered:
        for var, c in r["independent_grib_check"].items():
            assert c["source_point_is_cell_centre"], (r["location"], var, c)
            assert c["equal"], (r["location"], var, c)
        assert r["forecast"]["status"] in ("CURRENT", "PAST_VALID_TIME", "STALE")
    # different places really have different underlying physics
    pw = {round(r["returned_variables"]["pwat"], 1) for r in covered}
    t2 = {round(r["returned_variables"]["t2m_c"], 1) for r in covered}
    assert len(pw) >= 20 and len(t2) >= 20


def test_underlying_records_differ_by_location_and_follow_geography():
    """Offline: selecting different places must change the underlying source values (not just ids), and static
    attributes must follow real geography (Leh high and cold-ish terrain vs Port Blair at sea level)."""
    from backend.location_service import Store
    s = Store()
    places = {"Leh": (34.16, 77.58), "Port Blair": (11.62, 92.73), "Delhi": (28.61, 77.21), "Bengaluru": (12.97, 77.59), "Guwahati": (26.14, 91.74), "Jaisalmer": (26.92, 70.91)}
    recs = {}
    for n, (la, lo) in places.items():
        r = s.resolve(la, lo)
        if not r["covered"]:
            continue
        recs[n] = s.record(r["cell_id"], 3)
    assert len(recs) >= 5
    for var in ("t2m_c", "pwat", "rh2m"):
        vals = {n: r["fields"][var]["value"] for n, r in recs.items() if r["fields"][var]["value"] is not None}
        assert len(set(vals.values())) >= max(3, len(vals) - 1), (var, vals)
    assert len({r["location"]["cell_id"] for r in recs.values()}) == len(recs)
    if "Leh" in recs and "Port Blair" in recs:
        assert recs["Leh"]["static"]["elev_mean_m"] > 2500
        assert recs["Port Blair"]["static"]["elev_mean_m"] < 500
        assert recs["Leh"]["fields"]["t2m_c"]["value"] < recs["Port Blair"]["fields"]["t2m_c"]["value"]
        assert recs["Leh"]["fields"]["pwat"]["value"] < recs["Port Blair"]["fields"]["pwat"]["value"]


TWENTY = ["Leh", "Srinagar", "Delhi", "Jaipur", "Mumbai", "Pune", "Bengaluru", "Chennai", "Hyderabad", "Kochi", "Thiruvananthapuram", "Mangaluru",
          "Panjim", "Kolkata", "Bhubaneswar", "Guwahati", "Shillong", "Patna", "Lucknow", "Port Blair"]       # "Goa" is a state: Panjim is its capital city
VARS = ["cape", "cin", "t2m_c", "pwat", "rh2m", "ws10", "d2m_c", "mslp_hpa"]


def test_twenty_locations_api_equals_published_artifact_cell_by_cell():
    """Offline, repeatable: for each location the API record equals the value read DIRECTLY from data/india_fields_latest.json at the
    resolved cell (not through any DRIFT lookup code), timestamps equal the artifact's, distinct places differ, and locations that fall outside
    the land mask are reported as not covered (never forced onto a neighbouring cell)."""
    import json
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.location_api import router
    app = FastAPI(); app.include_router(router); c = TestClient(app)
    art = json.loads((Path(__file__).resolve().parent.parent / "data" / "india_fields_latest.json").read_text())
    cell_pos = {cid: i for i, cid in enumerate(art["cells"])}
    H = 3
    covered, uncovered, rows = {}, {}, {}
    for name in TWENTY:
        hit = next(x for x in c.get("/v1/search", params={"q": name}).json()["results"] if x["type"] == "place" and x["name"].lower() == name.lower())
        r = c.get("/v1/resolve", params={"lat": hit["lat"], "lon": hit["lon"]}).json()
        if not r["covered"]:
            assert r["reason"] and "cell_id" not in r, name
            uncovered[name] = r
            continue
        cid = r["cell_id"]; covered[name] = cid
        rec = c.get("/v1/location", params={"cell_id": cid, "hour": H}).json()
        assert rec["selected_hour"]["valid_time_utc"] == art["meta"]["files"][H]["valid_time_utc"]
        assert rec["selected_hour"]["published_at_utc"] == art["meta"]["files"][H]["published_at_utc"]
        assert rec["cycle"]["model_init_utc"] == art["meta"]["cycle_init_utc"]
        assert rec["location"]["cell_id"] == cid == art["cells"][cell_pos[cid]]
        vals = {}
        for v in VARS:
            raw = art["fields"][v][H][cell_pos[cid]]
            api = rec["fields"][v]["value"]
            assert (raw is None and api is None) or (raw is not None and api is not None and abs(raw - api) <= 0.5 * 10 ** -art["registry"][v]["decimals"] + 1e-9), (name, v, raw, api)
            vals[v] = api
        rows[name] = vals
    assert len(covered) + len(uncovered) == 20 and len(covered) >= 17
    # coastal gazetteer points whose 0.25 deg cell is mostly sea are NOT covered; they must offer the nearest covered cell, not substitute it silently
    for n, r in uncovered.items():
        assert r["nearest_covered_cell"]["cell_id"] in cell_pos and r["nearest_covered_cell"]["distance_deg"] > 0
    assert {"Kochi", "Mangaluru", "Panjim"} >= set(uncovered), uncovered.keys()   # coastal gazetteer points in cells that are < 50% land
    assert len(set(covered.values())) == len(covered)
    for v in ("t2m_c", "pwat", "rh2m"):
        assert len({round(r[v], 1) for r in rows.values() if r[v] is not None}) >= len(rows) - 3, v
    assert rows["Leh"]["t2m_c"] < rows["Port Blair"]["t2m_c"] and rows["Leh"]["pwat"] < rows["Thiruvananthapuram"]["pwat"]


def test_search_ranks_exact_matches_first():
    from backend.location_service import Store
    s = Store()
    assert s.search("Goa", 3)[0] == {"type": "state", "name": "Goa"}      # not the Assam town Goalpara
    assert s.search("Delhi", 1)[0]["name"] == "Delhi"
