#!/usr/bin/env python3
"""Regression tests for location_engine.py, using synthetic fixtures (no
real grid/forecast files touched). Run: python3 test_location_engine.py"""

import sys
from location_engine import (
    find_nearest_cell, vobl_applicability, build_location_bundle,
    VOBL_LAT, VOBL_LON, haversine_km,
)

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_grid():
    return {
        "grid_step_deg": 1.0,
        "generated_at_utc": "2026-09-30T12:00:00Z",
        "gfs_cycle": "2026-09-30 00Z", "gfs_fhour": 6,
        "cells": [
            {"lat": 13.0, "lon": 77.0, "thunderstorm_probability": 0.43,
             "cloudburst_probability": 0.18, "flash_flood_probability": 0.27,
             "flash_flood_probability_terrain_adjusted": 0.34,
             "terrain": {"available": True, "elevation_m": 800, "slope_deg": 1.0, "flood_susceptibility": 0.6},
             "cape": 1500, "cin": -20, "k_index": 34, "totals_totals": 46,
             "wind_shear_ms": 12, "pwat_mm": 42, "ctt_c": -55,
             "convergence_s": 1e-4, "ctt_drop_rate_c_hr": 3.2, "qpe_mm": 5.0},
            {"lat": 28.6, "lon": 77.2, "thunderstorm_probability": 0.10,
             "cloudburst_probability": 0.02, "flash_flood_probability": 0.01,
             "flash_flood_probability_terrain_adjusted": 0.01,
             "terrain": {"available": False, "reason": "outside DEM coverage bbox"},
             "cape": 300, "cin": -5, "k_index": 20, "totals_totals": 38,
             "wind_shear_ms": 5, "pwat_mm": 20, "ctt_c": -20,
             "convergence_s": None, "ctt_drop_rate_c_hr": None, "qpe_mm": 0.0},
        ],
    }


def make_forecast():
    return {
        "generated_at_ist": "2026-09-30 18:08 IST",
        "gfs_cycle": "2026-09-30 00Z f012",
        "peak_probability": 0.62, "peak_slot": 2,
        "slots": [
            {"slot": 0, "ts_probability": 0.05, "primary": False},
            {"slot": 2, "ts_probability": 0.62, "primary": True,
             "lead_time": {"available": True, "hours": 3.2, "valid_from": "x", "valid_to": "y"}},
        ],
    }


def make_himawari():
    return {"vobl_bt_celsius": -60.2, "min_bt_50km": -70.1, "bt_trend_1h": -4.5,
            "storm_detected": True, "fetched_at_utc": "2026-09-30T12:30:00Z"}


def test_nearest_cell_exact_and_snap():
    print("test_nearest_cell_exact_and_snap")
    grid = make_grid()
    m = find_nearest_cell(grid, 13.0, 77.0)
    check("exact cell match found", m.available and m.cell["lat"] == 13.0)
    check("distance_km near 0", m.distance_km < 1.0, m.distance_km)

    m2 = find_nearest_cell(grid, 0.0, 0.0)  # far from any cell
    check("far point rejected (beyond snap radius)", m2.available is False)


def test_vobl_domain_boundary():
    print("test_vobl_domain_boundary")
    a_in = vobl_applicability(VOBL_LAT, VOBL_LON)
    check("VOBL itself is in-domain", a_in.in_domain, a_in.distance_km)

    a_out = vobl_applicability(12.2958, 76.6394)  # Mysuru, ~130km away
    check("Mysuru is NOT in VOBL domain", a_out.in_domain is False, a_out.distance_km)
    check("reason explains station-specific / not applicable", "station-specific" in a_out.reason)


def test_bundle_at_vobl_shows_station_forecast_applicable():
    print("test_bundle_at_vobl_shows_station_forecast_applicable")
    grid = make_grid()
    fc = make_forecast()
    hw = make_himawari()
    bundle = build_location_bundle(VOBL_LAT, VOBL_LON, grid, fc, hw).to_json()
    check("VOBL in_domain True at VOBL coordinates", bundle["vobl"]["in_domain"] is True)
    check("station_forecast present", bundle["vobl"]["station_forecast"] is not None)
    check("Himawari available near VOBL", bundle["himawari"]["available"] is True)


def test_bundle_far_from_vobl_marks_station_not_applicable():
    print("test_bundle_far_from_vobl_marks_station_not_applicable")
    grid = make_grid()
    fc = make_forecast()
    hw = make_himawari()
    bundle = build_location_bundle(28.6, 77.2, grid, fc, hw).to_json()
    check("VOBL in_domain False far away", bundle["vobl"]["in_domain"] is False)
    check("station_forecast still retrievable for context but domain flag says not applicable",
          bundle["vobl"]["station_forecast"] is not None and bundle["vobl"]["in_domain"] is False)
    check("Himawari unavailable far from VOBL", bundle["himawari"]["available"] is False)
    check("pan_india still available for a matched cell", bundle["pan_india"]["available"] is True)


def test_bundle_never_reuses_bengaluru_terrain_for_delhi_cell():
    print("test_bundle_never_reuses_bengaluru_terrain_for_delhi_cell")
    grid = make_grid()
    bundle = build_location_bundle(28.6, 77.2, grid, None, None).to_json()
    terrain = bundle["pan_india"]["terrain"]
    check("Delhi-area cell terrain explicitly unavailable, not Bengaluru's values",
          terrain["available"] is False)


def test_missing_data_sources_never_crash():
    print("test_missing_data_sources_never_crash")
    bundle = build_location_bundle(13.0, 77.0, None, None, None).to_json()
    check("pan_india explicitly unavailable when grid not loaded", bundle["pan_india"]["available"] is False)
    check("vobl block still present with an honest reason", "reason" in bundle["vobl"])
    check("himawari explicitly unavailable when not loaded", bundle["himawari"]["available"] is False)
    check("gfs explicitly unavailable when pan_india unmatched", bundle["gfs"]["available"] is False)


def test_lead_time_surfaced_from_primary_slot():
    print("test_lead_time_surfaced_from_primary_slot")
    grid = make_grid()
    fc = make_forecast()
    bundle = build_location_bundle(VOBL_LAT, VOBL_LON, grid, fc, None).to_json()
    check("lead_time pulled from primary slot", bundle["lead_time"] is not None and bundle["lead_time"]["hours"] == 3.2)


def test_haversine_known_distance():
    print("test_haversine_known_distance")
    # VOBL to Mysuru palace is roughly 130-140 km.
    d = haversine_km(VOBL_LAT, VOBL_LON, 12.2958, 76.6394)
    check("VOBL-Mysuru haversine distance in plausible 100-160km band", 100 < d < 160, d)


def test_against_real_on_disk_grid_file():
    print("test_against_real_on_disk_grid_file (catches key-name mismatches against the real file)")
    import json
    from pathlib import Path
    grid_path = Path("data") / "pan_india_grid.json"
    if not grid_path.exists():
        print("  [SKIP] data/pan_india_grid.json not present in this environment")
        return
    with open(grid_path) as f:
        real_grid = json.load(f)
    # VOBL coordinates should always match a real cell in a pan-India grid.
    m = find_nearest_cell(real_grid, VOBL_LAT, VOBL_LON)
    check("real on-disk grid_cells key is read correctly (not silently empty)",
          m.available is True, m.reason)
    if m.available:
        check("matched cell has a thunderstorm_probability field", "thunderstorm_probability" in m.cell)


if __name__ == "__main__":
    test_nearest_cell_exact_and_snap()
    test_vobl_domain_boundary()
    test_bundle_at_vobl_shows_station_forecast_applicable()
    test_bundle_far_from_vobl_marks_station_not_applicable()
    test_bundle_never_reuses_bengaluru_terrain_for_delhi_cell()
    test_missing_data_sources_never_crash()
    test_lead_time_surfaced_from_primary_slot()
    test_haversine_known_distance()
    test_against_real_on_disk_grid_file()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed -> {FAILURES}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
