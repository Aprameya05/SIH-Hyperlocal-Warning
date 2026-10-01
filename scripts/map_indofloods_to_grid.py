#!/usr/bin/env python3
"""
Phase 5 forensic script: map INDOFLOODS gauges to the canonical 992-cell
pan-India grid used by backend/pipeline.py.

Read-only with respect to all INDOFLOODS source files and the production
grid/pipeline code. Writes ONLY to processed/indofloods/.

Mapping method: point-in-cell using the gauge's own Latitude/Longitude from
metadata_indofloods.csv (the instrument location, i.e. the flood-observation
point), snapped to the nearest canonical grid cell. See
docs/INDOFLOODS_FORENSIC_INVENTORY.md section 4 for why point mapping (not a
catchment-polygon overlap/centroid method) is used, despite a documented
gauge-point-vs-catchment-centroid mismatch rate.

Deterministic: same input files -> byte-identical output on every run
(no randomness, no wall-clock-dependent values in the mapping logic or in the
written rows).
"""

import csv
import math
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
OUT_DIR = os.path.join(REPO_ROOT, "processed", "indofloods")

METADATA_CSV = os.path.join(DATA_DIR, "metadata_indofloods.csv")
FLOODEVENTS_CSV = os.path.join(DATA_DIR, "floodevents_indofloods.csv")
MAPPING_OUT = os.path.join(OUT_DIR, "indofloods_grid_mapping.csv")
EVENTS_OUT = os.path.join(OUT_DIR, "indofloods_grid_events.csv")

# Canonical grid definition, mirrored read-only from backend/pipeline.py
# (BOUNDS, APPLICATION_GRID_STEP, EXPECTED_APPLICATION_CELL_COUNT).
# NOT imported from pipeline.py to avoid any coupling to production code;
# values verified by inspection of data/pan_india_grid.json (grid_step_deg=1.0,
# bounds={S:6,N:37,W:68,E:98}, n_cells=992) and backend/pipeline.py's
# BOUNDS / APPLICATION_GRID_STEP / EXPECTED_APPLICATION_CELL_COUNT constants.
GRID_S = 6.0
GRID_N = 37.0
GRID_W = 68.0
GRID_E = 98.0
GRID_STEP = 1.0
EXPECTED_N_CELLS = 992


def build_valid_cell_ids():
    """Reproduce the exact set of (lat, lon) cell centers the canonical
    grid uses, so we can validate against it. Grid cell centers run from
    GRID_S to GRID_N-1 step 1.0 (32 values) and GRID_W to GRID_E-1 step 1.0
    (31 values) -> 32*31 = 992, matching EXPECTED_N_CELLS."""
    lats = [GRID_S + i * GRID_STEP for i in range(int(round((GRID_N - GRID_S) / GRID_STEP)) + 1)]
    lons = [GRID_W + i * GRID_STEP for i in range(int(round((GRID_E - GRID_W) / GRID_STEP)) + 1)]
    cells = {(round(la, 6), round(lo, 6)) for la in lats for lo in lons}
    assert len(cells) == EXPECTED_N_CELLS, (len(cells), EXPECTED_N_CELLS)
    return cells


VALID_CELLS = build_valid_cell_ids()


def cell_id_str(lat, lon):
    return f"{lat:.1f}_{lon:.1f}"


def map_point_to_cell(lat, lon):
    """Point-in-cell: floor to the 1-degree cell whose center is at
    (S + floor(lat-S), W + floor(lon-W)). Returns (cell_lat, cell_lon, status).
    """
    if lat is None or lon is None:
        return None, None, "INVALID_COORDS"
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None, None, "INVALID_COORDS"
    if math.isnan(lat) or math.isnan(lon):
        return None, None, "INVALID_COORDS"
    if not (GRID_S <= lat <= GRID_N and GRID_W <= lon <= GRID_E):
        return None, None, "OUTSIDE_GRID"
    cell_lat = GRID_S + math.floor(lat - GRID_S)
    cell_lon = GRID_W + math.floor(lon - GRID_W)
    cell_lat = round(cell_lat, 1)
    cell_lon = round(cell_lon, 1)
    if (round(cell_lat, 6), round(cell_lon, 6)) not in VALID_CELLS:
        return None, None, "OUTSIDE_GRID"
    return cell_lat, cell_lon, "MAPPED"


def read_metadata():
    rows = []
    with open(METADATA_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def build_mapping():
    os.makedirs(OUT_DIR, exist_ok=True)
    gauges = read_metadata()
    gauges_sorted = sorted(gauges, key=lambda r: r["GaugeID"])

    out_rows = []
    for r in gauges_sorted:
        gid = r["GaugeID"]
        lat_raw = r.get("Latitude", "")
        lon_raw = r.get("Longitude", "")
        try:
            lat = float(lat_raw) if lat_raw not in (None, "") else None
        except ValueError:
            lat = None
        try:
            lon = float(lon_raw) if lon_raw not in (None, "") else None
        except ValueError:
            lon = None

        cell_lat, cell_lon, status = map_point_to_cell(lat, lon)
        cid = cell_id_str(cell_lat, cell_lon) if status == "MAPPED" else ""

        out_rows.append({
            "source_id": gid,
            "source_type": "gauge",
            "lat": "" if lat is None else f"{lat:.6f}",
            "lon": "" if lon is None else f"{lon:.6f}",
            "cell_id": cid,
            "cell_lat": "" if cell_lat is None else f"{cell_lat:.1f}",
            "cell_lon": "" if cell_lon is None else f"{cell_lon:.1f}",
            "mapping_method": "point_in_cell(gauge_lat_lon)",
            "distance_km": "",  # not computed: point-in-cell has no distance/overlap metric
            "status": status,
        })

    fieldnames = ["source_id", "source_type", "lat", "lon", "cell_id",
                  "cell_lat", "cell_lon", "mapping_method", "distance_km", "status"]
    with open(MAPPING_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    return out_rows


def build_events(mapping_rows):
    gauge_to_cell = {r["source_id"]: r for r in mapping_rows}

    with open(FLOODEVENTS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        events = list(reader)

    events_sorted = sorted(events, key=lambda r: r["EventID"])

    out_rows = []
    for ev in events_sorted:
        eid = ev["EventID"]
        gid = eid.rsplit("-", 1)[0]
        m = gauge_to_cell.get(gid)
        if m is None:
            cell_id, cell_lat, cell_lon, status = "", "", "", "GAUGE_NOT_IN_METADATA"
        else:
            cell_id, cell_lat, cell_lon, status = m["cell_id"], m["cell_lat"], m["cell_lon"], m["status"]

        out_rows.append({
            "EventID": eid,
            "GaugeID": gid,
            "cell_id": cell_id,
            "cell_lat": cell_lat,
            "cell_lon": cell_lon,
            "mapping_status": status,
            "Start Date": ev.get("Start Date", ""),
            "End Date": ev.get("End Date", ""),
            "Flood Type": ev.get("Flood Type", ""),
        })

    fieldnames = ["EventID", "GaugeID", "cell_id", "cell_lat", "cell_lon",
                  "mapping_status", "Start Date", "End Date", "Flood Type"]
    with open(EVENTS_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    return out_rows


def summarize(mapping_rows, event_rows):
    total = len(mapping_rows)
    mapped = sum(1 for r in mapping_rows if r["status"] == "MAPPED")
    outside = sum(1 for r in mapping_rows if r["status"] == "OUTSIDE_GRID")
    invalid = sum(1 for r in mapping_rows if r["status"] == "INVALID_COORDS")

    cell_counts = {}
    for r in mapping_rows:
        if r["status"] == "MAPPED":
            cell_counts[r["cell_id"]] = cell_counts.get(r["cell_id"], 0) + 1
    duplicate_cells = {k: v for k, v in cell_counts.items() if v > 1}

    print(f"Total gauges: {total}")
    print(f"MAPPED: {mapped}")
    print(f"OUTSIDE_GRID: {outside}")
    print(f"INVALID_COORDS: {invalid}")
    print(f"Distinct cells occupied: {len(cell_counts)}")
    print(f"Cells with >1 gauge (not an error): {len(duplicate_cells)} -> {duplicate_cells}")

    total_events = len(event_rows)
    mapped_events = sum(1 for r in event_rows if r["mapping_status"] == "MAPPED")
    unmapped_events = total_events - mapped_events
    event_gauges = {r["GaugeID"] for r in event_rows}
    event_cells = {r["cell_id"] for r in event_rows if r["mapping_status"] == "MAPPED"}
    print(f"Total flood events: {total_events}")
    print(f"Events joined to a MAPPED cell: {mapped_events}")
    print(f"Events NOT joined to a mapped cell: {unmapped_events}")
    print(f"Distinct gauges with events: {len(event_gauges)}")
    print(f"Distinct grid cells with >=1 flood event: {len(event_cells)}")


def main():
    mapping_rows = build_mapping()
    event_rows = build_events(mapping_rows)
    summarize(mapping_rows, event_rows)


if __name__ == "__main__":
    main()
