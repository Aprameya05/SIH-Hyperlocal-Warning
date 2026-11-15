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
