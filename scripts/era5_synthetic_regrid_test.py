#!/usr/bin/env python3
"""
Phase 0.3-B/C: SYNTHETIC/STRUCTURAL-TEST-ONLY regrid validation.

This script does NOT touch production code (backend/pipeline.py,
canonical_forecast_writer.py, forecast_action.py, regrid.py are read-only
references here, never imported for execution against real data). It
mirrors the nearest-cell mapping convention already used in
processed/indofloods/indofloods_grid_mapping.csv (point_in_cell) and the
canonical grid definition from backend/pipeline.py (BOUNDS, 1.0 deg step,
992 cells) to test whether an ERA5-shaped lat/lon array CAN be mapped onto
the 992 canonical cells.

No real ERA5 data was available in this sandbox (no CDS credentials, no
network egress to cds.climate.copernicus.eu -- see
docs/PHASE_0_3_ERA5_PILOT_VALIDATION.md). The array built below is entirely
SYNTHETIC: structurally shaped like an ERA5 0.25deg lat/lon grid over a
small test bounding box, filled with deterministic placeholder numbers.
It is NEVER written anywhere as if it were real ERA5 data.
"""

import csv
import json
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "processed", "era5_pilot")

# Canonical grid, mirrored read-only from backend/pipeline.py (same values
# scripts/map_indofloods_to_grid.py mirrors).
GRID_S, GRID_N, GRID_W, GRID_E, GRID_STEP = 6.0, 37.0, 68.0, 98.0, 1.0
EXPECTED_N_CELLS = 992


def canonical_cells():
    lats = [GRID_S + i * GRID_STEP for i in range(int(round((GRID_N - GRID_S) / GRID_STEP)) + 1)]
    lons = [GRID_W + i * GRID_STEP for i in range(int(round((GRID_E - GRID_W) / GRID_STEP)) + 1)]
    cells = [(round(la, 2), round(lo, 2)) for la in lats for lo in lons]
    assert len(cells) == EXPECTED_N_CELLS, (len(cells), EXPECTED_N_CELLS)
    return cells


def synthetic_era5_grid(lat_min, lat_max, lon_min, lon_max, step=0.25):
    """SYNTHETIC/STRUCTURAL-TEST-ONLY. Builds a fake ERA5-shaped grid: NOT
    real data, never claimed as real ERA5."""
    n_lat = int(round((lat_max - lat_min) / step)) + 1
    n_lon = int(round((lon_max - lon_min) / step)) + 1
    lats = [round(lat_min + i * step, 3) for i in range(n_lat)]
    lons = [round(lon_min + j * step, 3) for j in range(n_lon)]
    # deterministic placeholder field, structurally plausible range for t2m-like var
    values = [[round(280.0 + 0.1 * i + 0.05 * j, 3) for j in range(n_lon)] for i in range(n_lat)]
    return lats, lons, values


def nearest_index(coords, value):
    best_i, best_d = 0, abs(coords[0] - value)
    for i, c in enumerate(coords):
        d = abs(c - value)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cells = canonical_cells()

    # Small pilot bounding box (not all of India) -- just enough to cover a
    # handful of canonical cells and exercise the real regridding logic.
    lat_min, lat_max, lon_min, lon_max = 11.0, 15.0, 74.0, 79.0
    src_lats, src_lons, src_values = synthetic_era5_grid(lat_min, lat_max, lon_min, lon_max)

    pilot_cells = [c for c in cells if lat_min <= c[0] <= lat_max and lon_min <= c[1] <= lon_max]

    rows = []
    seen_cell_ids = set()
    dup_count = 0
    for (clat, clon) in pilot_cells:
        i = nearest_index(src_lats, clat)
        j = nearest_index(src_lons, clon)
        val = src_values[i][j]
        cell_id = f"{clat}_{clon}"
        if cell_id in seen_cell_ids:
            dup_count += 1
        seen_cell_ids.add(cell_id)
        rows.append({
            "cell_id": cell_id,
            "cell_lat": clat,
            "cell_lon": clon,
            "src_lat_matched": src_lats[i],
            "src_lon_matched": src_lons[j],
            "distance_deg": round(((clat - src_lats[i]) ** 2 + (clon - src_lons[j]) ** 2) ** 0.5, 4),
            "synthetic_value": val,
            "SYNTHETIC": True,
        })

    out_csv = os.path.join(OUT_DIR, "synthetic_era5_regrid_test.csv")
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    summary = {
        "SYNTHETIC_STRUCTURAL_TEST_ONLY": True,
        "real_era5_data_used": False,
        "pilot_bbox": {"lat_min": lat_min, "lat_max": lat_max, "lon_min": lon_min, "lon_max": lon_max},
        "canonical_cells_total": EXPECTED_N_CELLS,
        "canonical_cells_in_pilot_bbox": len(pilot_cells),
        "canonical_cells_mapped": len(rows),
        "all_pilot_cells_covered": len(rows) == len(pilot_cells),
        "duplicate_cell_ids": dup_count,
        "max_snap_distance_deg": max(r["distance_deg"] for r in rows),
        "timezone_handling": "not exercised (synthetic grid has no time dimension)",
        "missingness_handling": "not exercised (synthetic grid has no NaN cells)",
    }
    with open(os.path.join(OUT_DIR, "synthetic_era5_regrid_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
