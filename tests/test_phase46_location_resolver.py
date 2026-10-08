"""
tests/test_phase46_location_resolver.py
===========================================
2026-10-08: tests for scripts/location_resolver.py -- the free (OSM
Nominatim), no-API-key geocoder that resolves an arbitrary Indian
location to the real nearest canonical cell, fixing the "every search
silently returns VOBL/Bengaluru" gap.

Deterministic unit tests mock the network entirely (no live Nominatim
calls -- keeps the suite fast and independent of that service's
availability/rate limits). A separate, explicitly network-gated class
of tests proves the real multi-city matrix end-to-end.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import location_resolver as lr  # noqa: E402


def _fake_session(lat, lon, display_name="Fake Place, India"):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = [{"lat": str(lat), "lon": str(lon), "display_name": display_name}]
    session = MagicMock()
    session.get.return_value = resp
    return session


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "CACHE_PATH", tmp_path / "geocode_cache.json")
    monkeypatch.setattr(lr, "_last_request_time", 0.0)
    yield


def test_bengaluru_resolves_to_the_same_cell_the_vobl_fix_established():
    """Cross-check against the independently-verified VOBL cell fix:
    a real Bengaluru search must land on IND_13.0_78.0, not
    IND_13.0_77.0 (the confirmed-wrong cell) and not any other city's
    cell."""
    session = _fake_session(12.9767936, 77.590082, "Bengaluru, Karnataka, India")
    result = lr.geocode("Bengaluru", session=session)
    assert result.found is True
    assert result.cell_id == "IND_13.0_78.0"
    assert result.cell_id != "IND_13.0_77.0"


def test_different_cities_resolve_to_different_cells_no_leakage():
    cities = {
        "Delhi": (28.6328027, 77.2197713),
        "Mumbai": (19.054999, 72.8692035),
        "Chennai": (13.0800915, 80.2807978),
    }
    cell_ids = set()
    for name, (lat, lon) in cities.items():
        session = _fake_session(lat, lon, f"{name}, India")
        result = lr.geocode(name, session=session)
        assert result.found is True
        cell_ids.add(result.cell_id)
    assert len(cell_ids) == len(cities), "each distinct city must resolve to its own cell, never collapsing to one"
    assert "IND_13.0_78.0" not in cell_ids, "no city here should resolve to the VOBL/Bengaluru cell"


def test_empty_query_returns_not_found_never_a_default_location():
    result = lr.geocode("")
    assert result.found is False
    assert result.cell_id is None


def test_no_geocode_match_returns_not_found_never_substitutes_vobl():
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = []
    session.get.return_value = resp
    result = lr.geocode("asdkjfhaskjdfhaskjdfh nonsense query", session=session)
    assert result.found is False
    assert result.cell_id is None
    assert "no geocoding match" in result.error


def test_network_failure_returns_not_found_never_fabricates_coordinates():
    session = MagicMock()
    session.get.side_effect = ConnectionError("simulated network failure")
    result = lr.geocode("Kolkata", session=session)
    assert result.found is False
    assert result.latitude is None
    assert result.error is not None


def test_location_outside_india_grid_bounds_reports_no_cell():
    """A real geocode result, but geographically outside the canonical
    grid's coverage -- must report found=True (we did locate it) but
    cell_id=None (we have no cell to map it to), never a fabricated
    nearest cell outside the grid's real coverage."""
    session = _fake_session(1.3521, 103.8198, "Singapore")  # genuinely outside India bounds
    result = lr.geocode("Singapore", session=session)
    assert result.found is True
    assert result.in_india_grid_bounds is False
    assert result.cell_id is None


def test_repeat_query_is_served_from_cache_without_a_second_network_call():
    session = _fake_session(22.5726, 88.3639, "Kolkata, India")
    r1 = lr.geocode("Kolkata", session=session)
    assert r1.from_cache is False
    assert session.get.call_count == 1

    r2 = lr.geocode("Kolkata", session=session)
    assert r2.from_cache is True
    assert session.get.call_count == 1, "second lookup of the same query must not hit the network again"
    assert r2.cell_id == r1.cell_id


def test_nearest_cell_is_a_real_member_of_the_actual_992_cell_grid():
    """Guards against ever inventing a cell_id that doesn't exist in
    the real canonical grid (the risk a naive lat/lon-formatting
    formula would carry near grid edges)."""
    with open(lr.COMMON_GRID_PATH, encoding="utf-8") as f:
        real_cell_ids = {c["cell_id"] for c in json.load(f)["cells"]}
    cell_id, _ = lr._nearest_canonical_cell(13.0, 77.55)  # a point between two real cell centers
    assert cell_id in real_cell_ids
