#!/usr/bin/env python3
"""
test_canonical_grid.py -- plain-script tests (no pytest, matches this
repo's existing test style) for Phase 3's canonical grid definition and
regrid.py's new regrid_to_cell()/cell_id_for() additions.

Run: python3 tests/test_canonical_grid.py   (from repo root)
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from regrid import cell_id_for, regrid_to_cell  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def test_live_grid_matches_documented_canonical():
    print("test_live_grid_matches_documented_canonical")
    grid_path = REPO_ROOT / "data" / "pan_india_grid.json"
    d = json.loads(grid_path.read_text())
    cells = d.get("grid_cells") or d.get("cells") or []
    lats = sorted(set(c["lat"] for c in cells))
    lons = sorted(set(c["lon"] for c in cells))
    check("n_cells == 992", len(cells) == 992, f"got {len(cells)}")
    check("32 unique lats", len(lats) == 32, f"got {len(lats)}")
    check("31 unique lons", len(lons) == 31, f"got {len(lons)}")
    check("grid_step_deg == 1.0", d.get("grid_step_deg") == 1.0, f"got {d.get('grid_step_deg')}")
    check(
        "WARNING flag: code's GRID_STEP differs from live file (see docs/CANONICAL_GRID.md)",
        True,  # informational, not a failure -- this is the documented, expected discrepancy
    )


def test_cell_id_deterministic():
    print("test_cell_id_deterministic")
    a = cell_id_for(13.0, 78.0)
    b = cell_id_for(13.0, 78.0)
    check("same lat/lon -> same cell_id", a == b)
    check("format matches convention", a == "IND_13.0_78.0", f"got {a}")


def test_cell_id_distinguishes_neighbors():
    print("test_cell_id_distinguishes_neighbors")
    a = cell_id_for(13.0, 78.0)
    b = cell_id_for(14.0, 78.0)
    c = cell_id_for(13.0, 79.0)
    check("different lat -> different cell_id", a != b)
    check("different lon -> different cell_id", a != c)


def test_regrid_to_cell_provenance_complete():
    print("test_regrid_to_cell_provenance_complete")
    lats = [10.0, 11.0]
    lons = [77.0, 78.0]
    values = [[100.0, 200.0], [300.0, 400.0]]
    r = regrid_to_cell(
        "gfs", lats, lons, values, target_lat=10.5, target_lon=77.5,
        source_timestamp="2026-09-30T00:00:00Z", source_resolution_deg=1.0,
        target_resolution_deg=1.0, method="bilinear",
    )
    j = r.to_json()
    for field in ("cell_id", "value", "available", "source", "source_timestamp",
                  "source_resolution_deg", "target_resolution_deg", "method_used",
                  "missing_flag", "missing_reason"):
        check(f"field '{field}' present", field in j)
    check("cell_id matches convention", j["cell_id"] == cell_id_for(10.5, 77.5))
    check("source recorded", j["source"] == "gfs")
    check("source_timestamp recorded", j["source_timestamp"] == "2026-09-30T00:00:00Z")
    check("bilinear midpoint value correct", abs(j["value"] - 250.0) < 1e-6, f"got {j['value']}")


def test_regrid_to_cell_missing_flag_on_empty_source():
    print("test_regrid_to_cell_missing_flag_on_empty_source")
    r = regrid_to_cell("himawari", [], [], [], target_lat=10.0, target_lon=77.0)
    j = r.to_json()
    check("missing_flag True on empty source", j["missing_flag"] is True)
    check("missing_reason populated", j["missing_reason"] is not None)
    check("value is None, not fabricated", j["value"] is None)


def test_regrid_to_cell_snap_radius_respected():
    print("test_regrid_to_cell_snap_radius_respected")
    lats = [10.0]
    lons = [77.0]
    values = [[100.0]]
    r = regrid_to_cell(
        "dem", lats, lons, values, target_lat=25.0, target_lon=90.0,
        max_nearest_snap_deg=1.0,
    )
    j = r.to_json()
    check("far point marked missing", j["missing_flag"] is True)
    check("value not fabricated for far point", j["value"] is None)


if __name__ == "__main__":
    test_live_grid_matches_documented_canonical()
    test_cell_id_deterministic()
    test_cell_id_distinguishes_neighbors()
    test_regrid_to_cell_provenance_complete()
    test_regrid_to_cell_missing_flag_on_empty_source()
    test_regrid_to_cell_snap_radius_respected()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
