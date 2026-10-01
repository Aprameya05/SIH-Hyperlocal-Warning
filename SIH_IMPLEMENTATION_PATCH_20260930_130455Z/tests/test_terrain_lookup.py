#!/usr/bin/env python3
"""Regression tests for terrain_lookup.py. Run: python3 test_terrain_lookup.py"""

import json
import sys
import tempfile
from pathlib import Path

from terrain_lookup import TerrainGrid, TerrainSample, apply_terrain_to_ff

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_terrain_file(tmp, grid):
    p = Path(tmp) / "blr_terrain.json"
    data = {
        "generated_at": "2026-09-30T00:00:00Z",
        "bbox": {"lat_min": 12.5, "lat_max": 14.0, "lon_min": 77.0, "lon_max": 78.5},
        "resolution_deg": 0.01,
        "grid": grid,
    }
    with open(p, "w") as f:
        json.dump(data, f)
    return p


def test_flat_terrain_high_susceptibility():
    print("test_flat_terrain_high_susceptibility")
    with tempfile.TemporaryDirectory() as tmp:
        p = make_terrain_file(tmp, [{"lat": 13.0, "lon": 77.6, "elevation_m": 830,
                                      "slope_deg": 0.2, "flood_susceptibility": 0.95}])
        tg = TerrainGrid(p)
        s = tg.lookup(13.0, 77.6)
        check("available", s.available)
        check("flood_susceptibility ~0.95", s.flood_susceptibility == 0.95)
        ff = apply_terrain_to_ff(0.5, s)
        check("flat/high-susceptibility raises FF prob above input",
              ff > 0.5, ff)
        check("FF prob bounded <= 1.0", ff <= 1.0)


def test_steep_terrain_low_susceptibility():
    print("test_steep_terrain_low_susceptibility")
    with tempfile.TemporaryDirectory() as tmp:
        p = make_terrain_file(tmp, [{"lat": 13.0, "lon": 77.6, "elevation_m": 1200,
                                      "slope_deg": 22.0, "flood_susceptibility": 0.05}])
        tg = TerrainGrid(p)
        s = tg.lookup(13.0, 77.6)
        ff = apply_terrain_to_ff(0.5, s)
        check("steep/low-susceptibility lowers FF prob below input", ff < 0.5, ff)
        check("FF prob bounded >= 0.0", ff >= 0.0)


def test_missing_terrain_file_never_crashes():
    print("test_missing_terrain_file_never_crashes")
    tg = TerrainGrid(Path("/nonexistent/blr_terrain.json"))
    s = tg.lookup(13.0, 77.6)
    check("available is False when file missing", s.available is False)
    ff = apply_terrain_to_ff(0.42, s)
    check("ff_prob returned unmodified when terrain unavailable", ff == 0.42, ff)


def test_outside_bbox_reports_unavailable_not_fabricated():
    print("test_outside_bbox_reports_unavailable_not_fabricated")
    with tempfile.TemporaryDirectory() as tmp:
        p = make_terrain_file(tmp, [{"lat": 13.0, "lon": 77.6, "elevation_m": 830,
                                      "slope_deg": 1.0, "flood_susceptibility": 0.5}])
        tg = TerrainGrid(p)
        # Delhi -- far outside the Bengaluru DEM bbox.
        s = tg.lookup(28.6, 77.2)
        check("available is False for a pan-India cell outside Bengaluru DEM coverage",
              s.available is False)
        check("reason explains it is not pan-India coverage", "not pan-India" in s.reason or "bbox" in s.reason.lower(),
              s.reason)
        ff = apply_terrain_to_ff(0.3, s)
        check("ff_prob unmodified for out-of-coverage cell", ff == 0.3, ff)


def test_high_precip_high_susceptibility_stacks():
    print("test_high_precip_high_susceptibility_stacks")
    # A high model-derived ff_prob (already reflecting heavy precip signal)
    # combined with high terrain susceptibility should end up higher than
    # the same ff_prob with low susceptibility, but never exceed 1.0.
    with tempfile.TemporaryDirectory() as tmp:
        p = make_terrain_file(tmp, [
            {"lat": 13.0, "lon": 77.6, "elevation_m": 830, "slope_deg": 0.2, "flood_susceptibility": 0.95},
            {"lat": 13.1, "lon": 77.7, "elevation_m": 1300, "slope_deg": 25.0, "flood_susceptibility": 0.02},
        ])
        tg = TerrainGrid(p)
        s_high = tg.lookup(13.0, 77.6)
        s_low = tg.lookup(13.1, 77.7)
        ff_high_susc = apply_terrain_to_ff(0.9, s_high)
        ff_low_susc = apply_terrain_to_ff(0.9, s_low)
        check("high-susceptibility terrain yields >= low-susceptibility terrain for same input",
              ff_high_susc >= ff_low_susc, (ff_high_susc, ff_low_susc))
        check("bounded at 1.0 even with high precip + high susceptibility", ff_high_susc <= 1.0)


def test_low_precip_high_susceptibility_stays_low():
    print("test_low_precip_high_susceptibility_stays_low")
    with tempfile.TemporaryDirectory() as tmp:
        p = make_terrain_file(tmp, [{"lat": 13.0, "lon": 77.6, "elevation_m": 830,
                                      "slope_deg": 0.2, "flood_susceptibility": 0.95}])
        tg = TerrainGrid(p)
        s = tg.lookup(13.0, 77.6)
        ff = apply_terrain_to_ff(0.0, s)
        check("terrain never manufactures risk when model output is 0", ff == 0.0, ff)


if __name__ == "__main__":
    test_flat_terrain_high_susceptibility()
    test_steep_terrain_low_susceptibility()
    test_missing_terrain_file_never_crashes()
    test_outside_bbox_reports_unavailable_not_fabricated()
    test_high_precip_high_susceptibility_stacks()
    test_low_precip_high_susceptibility_stays_low()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed -> {FAILURES}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
