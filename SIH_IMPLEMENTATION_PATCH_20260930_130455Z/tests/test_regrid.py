#!/usr/bin/env python3
"""Regression tests for regrid.py, using synthetic fields with known
expected interpolation results. Run: python3 test_regrid.py"""

import sys
from regrid import regrid_point

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def test_nearest_exact_hit():
    print("test_nearest_exact_hit")
    lats = [10.0, 11.0, 12.0]
    lons = [70.0, 71.0, 72.0]
    values = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]]
    r = regrid_point("gfs", lats, lons, values, target_lat=11.0, target_lon=71.0, method="nearest")
    check("available", r.available)
    check("exact grid point returns exact value", r.value == 5.0, r.value)
    check("method_used is nearest", r.method_used == "nearest")


def test_bilinear_known_midpoint():
    print("test_bilinear_known_midpoint")
    # 2x2 grid, corners 0,10,10,20 -> midpoint should average to 10.0
    lats = [0.0, 1.0]
    lons = [0.0, 1.0]
    values = [[0.0, 10.0], [10.0, 20.0]]
    r = regrid_point("gfs", lats, lons, values, target_lat=0.5, target_lon=0.5, method="bilinear")
    check("available", r.available)
    check("method_used is bilinear", r.method_used == "bilinear", r.method_used)
    check("known bilinear midpoint == 10.0", abs(r.value - 10.0) < 1e-9, r.value)


def test_bilinear_quarter_point_known_value():
    print("test_bilinear_quarter_point_known_value")
    # Corners: (0,0)=0, (0,1)=10, (1,0)=0, (1,1)=10 -- pure X gradient.
    # At fx=0.25 the value should be exactly 2.5 regardless of fy.
    lats = [0.0, 1.0]
    lons = [0.0, 1.0]
    values = [[0.0, 10.0], [0.0, 10.0]]
    r = regrid_point("gfs", lats, lons, values, target_lat=0.7, target_lon=0.25, method="bilinear")
    check("available", r.available)
    check("known quarter-point value == 2.5", abs(r.value - 2.5) < 1e-9, r.value)


def test_bilinear_missing_neighbor_falls_back_to_nearest():
    print("test_bilinear_missing_neighbor_falls_back_to_nearest")
    lats = [0.0, 1.0]
    lons = [0.0, 1.0]
    values = [[0.0, None], [10.0, 20.0]]
    r = regrid_point("gfs", lats, lons, values, target_lat=0.5, target_lon=0.5, method="bilinear")
    check("available (fell back to nearest)", r.available)
    check("method_used is nearest, not bilinear", r.method_used == "nearest", r.method_used)
    check("fallback_reason explains why", r.fallback_reason is not None and "fell back" in r.fallback_reason)


def test_unsorted_source_forces_nearest():
    print("test_unsorted_source_forces_nearest")
    lats = [1.0, 0.0]  # descending -- not a valid bilinear source per this module's contract
    lons = [0.0, 1.0]
    values = [[10.0, 20.0], [0.0, 10.0]]
    r = regrid_point("gfs", lats, lons, values, target_lat=0.5, target_lon=0.5, method="bilinear")
    check("available", r.available)
    check("method_used falls back to nearest for unsorted coords", r.method_used == "nearest", r.method_used)


def test_max_snap_radius_rejects_far_point():
    print("test_max_snap_radius_rejects_far_point")
    lats = [10.0]
    lons = [70.0]
    values = [[5.0]]
    r = regrid_point("dem", lats, lons, values, target_lat=28.0, target_lon=77.0,
                      method="nearest", max_nearest_snap_deg=0.5)
    check("not available -- nearest point far beyond snap radius", r.available is False)
    check("distance_deg reported for diagnosis", r.distance_deg is not None and r.distance_deg > 0.5)


def test_empty_source_never_crashes():
    print("test_empty_source_never_crashes")
    r = regrid_point("himawari", [], [], [], target_lat=13.0, target_lon=77.6)
    check("available is False for empty source", r.available is False)
    check("value is None, not fabricated", r.value is None)


def test_unknown_source_name_rejected():
    print("test_unknown_source_name_rejected")
    r = regrid_point("radar", [1.0], [1.0], [[1.0]], target_lat=1.0, target_lon=1.0)
    check("unknown source name rejected explicitly", r.available is False)
    check("fallback_reason names the bad source", "radar" in r.fallback_reason)


if __name__ == "__main__":
    test_nearest_exact_hit()
    test_bilinear_known_midpoint()
    test_bilinear_quarter_point_known_value()
    test_bilinear_missing_neighbor_falls_back_to_nearest()
    test_unsorted_source_forces_nearest()
    test_max_snap_radius_rejects_far_point()
    test_empty_source_never_crashes()
    test_unknown_source_name_rejected()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed -> {FAILURES}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
