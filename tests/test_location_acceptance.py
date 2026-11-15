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
