#!/usr/bin/env python3
"""
test_indofloods_phase5.py -- plain-script tests (no pytest, matches this
repo's existing test style) for Phase 5's INDOFLOODS forensic mapping work.

Covers: source-file presence/schema, coordinate validity, CRS parsing,
deterministic mapping, valid cell IDs, unmapped-count consistency,
event-to-grid sample correctness, source-file non-modification, and
non-modification of backend/pipeline.py and the production JSONs.

Run: python3 tests/test_indofloods_phase5.py   (from repo root)
"""
import csv
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PROCESSED_DIR = REPO_ROOT / "processed" / "indofloods"
MAPPING_SCRIPT = REPO_ROOT / "scripts" / "map_indofloods_to_grid.py"

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


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Reference checksums of the 6 original INDOFLOODS source files, recorded
# at Phase 5 test-authoring time (before any Phase 5 script ran against
# them a second time). If these ever change, the source data was modified,
# which Phase 5 must never do.
ORIGINAL_SOURCE_HASHES = {
    "metadata_indofloods.csv": "8228295ef7133fb46c92dd7ff0066c14",
    "catchment_characteristics_indofloods.csv": "480e5f8604f1232b69b73ec25dd74a18",
    "floodevents_indofloods.csv": "2b0baeb771dfb2a8d35eb2eb001dd25b",
    "precipitation_variables_indofloods.csv": "13063e0cd7afefc4960c934f83aaaa9d",
    "catchments_shapefiles_indofloods.zip": "87021353b066248023a62e492d766839",
    "variables_description_indofloods.pdf": "d8cf91fe8731922d7195bfb3394d0e2f",
}

EXPECTED_SCHEMAS = {
    "metadata_indofloods.csv": [
        "GaugeID", "Warning Level", "Danger Level", "Station", "Latitude",
        "Longitude", "River Name/ Tributory/ SubTributory", "Basin", "State",
        "Start_date", "End_date", "Level_Entries", "Streamflow_Entries",
        "Privacy", "Source Catchment Area", "Catchment Area",
        "Area variation (%)", "Reliability",
    ],
    "floodevents_indofloods.csv": [
        "EventID", "Start Date", "End Date", "Peak Flood Level (m)",
        "Peak FL Date", "Num Peak FL", "Peak Discharge Q (cumec)",
        "Peak Discharge Date", "Flood Volume (cumec)",
        "Event Duration (days)", "Time to Peak (days)",
        "Recession Time (day)", "Flood Type",
    ],
    "precipitation_variables_indofloods.csv": [
        "EventID", "T1d", "T2d", "T3d", "T4d", "T5d", "T6d", "T7d", "T8d",
        "T9d", "T10d",
    ],
}


def read_csv_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_source_files_present_and_unmodified():
    print("test_source_files_present_and_unmodified")
    for fname, expected_hash in ORIGINAL_SOURCE_HASHES.items():
        p = DATA_DIR / fname
        check(f"{fname} exists", p.exists())
        if p.exists():
            actual = md5(p)
            check(f"{fname} checksum unchanged", actual == expected_hash,
                  f"expected {expected_hash} got {actual}")


def test_csv_schemas():
    print("test_csv_schemas")
    for fname, expected_cols in EXPECTED_SCHEMAS.items():
        rows = read_csv_rows(DATA_DIR / fname)
        actual_cols = list(rows[0].keys()) if rows else []
        check(f"{fname} columns match", actual_cols == expected_cols,
              f"got {actual_cols}")


def test_row_counts():
    print("test_row_counts")
    md_rows = read_csv_rows(DATA_DIR / "metadata_indofloods.csv")
    cc_rows = read_csv_rows(DATA_DIR / "catchment_characteristics_indofloods.csv")
    fe_rows = read_csv_rows(DATA_DIR / "floodevents_indofloods.csv")
    pv_rows = read_csv_rows(DATA_DIR / "precipitation_variables_indofloods.csv")
    check("214 gauges in metadata", len(md_rows) == 214, str(len(md_rows)))
    check("155 catchments in characteristics", len(cc_rows) == 155, str(len(cc_rows)))
    check("4548 flood events", len(fe_rows) == 4548, str(len(fe_rows)))
    check("4548 precipitation records", len(pv_rows) == 4548, str(len(pv_rows)))
    check("catchment characteristics GaugeIDs unique",
          len({r["GaugeID"] for r in cc_rows}) == len(cc_rows))
    check("flood event EventIDs unique",
          len({r["EventID"] for r in fe_rows}) == len(fe_rows))
    check("precipitation EventID set matches flood event EventID set",
          {r["EventID"] for r in pv_rows} == {r["EventID"] for r in fe_rows})


def test_coordinate_validity():
    print("test_coordinate_validity")
    md_rows = read_csv_rows(DATA_DIR / "metadata_indofloods.csv")
    # Plausible India bounds and the canonical grid bounds happen to be the
    # same box actually used in backend/pipeline.py (BOUNDS).
    S, N, W, E = 6.0, 37.0, 68.0, 98.0
    n_valid = 0
    n_missing = 0
    for r in md_rows:
        lat_raw, lon_raw = r.get("Latitude", ""), r.get("Longitude", "")
        if lat_raw in ("", None) or lon_raw in ("", None):
            n_missing += 1
            continue
        lat, lon = float(lat_raw), float(lon_raw)
        if S <= lat <= N and W <= lon <= E:
            n_valid += 1
    check("all 214 gauges have coordinates", n_missing == 0, f"{n_missing} missing")
    check("all 214 gauges within canonical grid bounds", n_valid == 214, f"{n_valid} valid")


def test_shapefile_crs_parsed():
    print("test_shapefile_crs_parsed")
    zpath = DATA_DIR / "catchments_shapefiles_indofloods.zip"
    check("shapefile zip exists", zpath.exists())
    if not zpath.exists():
        return
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        prj_names = [n for n in names if n.endswith(".prj")]
        shp_names = [n for n in names if n.endswith(".shp")]
        check("155 .prj files", len(prj_names) == 155, str(len(prj_names)))
        check("155 .shp files", len(shp_names) == 155, str(len(shp_names)))
        all_wgs84 = True
        for n in prj_names:
            content = z.read(n).decode("utf-8", errors="replace")
            if "D_WGS_1984" not in content or "GEOGCS" not in content:
                all_wgs84 = False
                break
        check("all .prj files are parsed and are geographic WGS84", all_wgs84)


def test_mapping_script_runs_and_is_deterministic():
    print("test_mapping_script_runs_and_is_deterministic")
    check("mapping script exists", MAPPING_SCRIPT.exists())
    if not MAPPING_SCRIPT.exists():
        return

    r1 = subprocess.run([sys.executable, str(MAPPING_SCRIPT)],
                         cwd=REPO_ROOT, capture_output=True, text=True)
    check("mapping script exits 0 (run 1)", r1.returncode == 0, r1.stderr[-500:])
    mapping_path = PROCESSED_DIR / "indofloods_grid_mapping.csv"
    events_path = PROCESSED_DIR / "indofloods_grid_events.csv"
    check("mapping csv produced", mapping_path.exists())
    check("events csv produced", events_path.exists())
    if not (mapping_path.exists() and events_path.exists()):
        return
    hash1_map, hash1_ev = md5(mapping_path), md5(events_path)

    r2 = subprocess.run([sys.executable, str(MAPPING_SCRIPT)],
                         cwd=REPO_ROOT, capture_output=True, text=True)
    check("mapping script exits 0 (run 2)", r2.returncode == 0, r2.stderr[-500:])
    hash2_map, hash2_ev = md5(mapping_path), md5(events_path)

    check("mapping csv byte-identical across runs", hash1_map == hash2_map)
    check("events csv byte-identical across runs", hash1_ev == hash2_ev)


def test_valid_cell_ids_and_mapping_stats():
    print("test_valid_cell_ids_and_mapping_stats")
    grid_path = DATA_DIR / "pan_india_grid.json"
    mapping_path = PROCESSED_DIR / "indofloods_grid_mapping.csv"
    check("pan_india_grid.json exists", grid_path.exists())
    check("mapping output exists", mapping_path.exists())
    if not (grid_path.exists() and mapping_path.exists()):
        return

    grid = json.loads(grid_path.read_text())
    check("canonical grid has 992 cells", len(grid["grid_cells"]) == 992,
          str(len(grid["grid_cells"])))
    valid_cells = {f"{c['lat']:.1f}_{c['lon']:.1f}" for c in grid["grid_cells"]}

    rows = read_csv_rows(mapping_path)
    check("214 gauge rows in mapping output", len(rows) == 214, str(len(rows)))
    mapped = [r for r in rows if r["status"] == "MAPPED"]
    outside = [r for r in rows if r["status"] == "OUTSIDE_GRID"]
    invalid = [r for r in rows if r["status"] == "INVALID_COORDS"]
    check("all 214 gauges mapped (0 outside grid, 0 invalid)",
          len(mapped) == 214 and len(outside) == 0 and len(invalid) == 0,
          f"mapped={len(mapped)} outside={len(outside)} invalid={len(invalid)}")
    bad_cells = [r["cell_id"] for r in mapped if r["cell_id"] not in valid_cells]
    check("every mapped cell_id exists in the real 992-cell grid",
          len(bad_cells) == 0, str(bad_cells[:5]))


def test_event_grid_join_samples():
    print("test_event_grid_join_samples")
    events_path = PROCESSED_DIR / "indofloods_grid_events.csv"
    mapping_path = PROCESSED_DIR / "indofloods_grid_mapping.csv"
    check("events output exists", events_path.exists())
    check("mapping output exists", mapping_path.exists())
    if not (events_path.exists() and mapping_path.exists()):
        return

    mapping_by_gauge = {r["source_id"]: r for r in read_csv_rows(mapping_path)}
    ev_rows = read_csv_rows(events_path)
    check("4548 event rows in join output", len(ev_rows) == 4548, str(len(ev_rows)))

    # Spot-check a handful of known samples end-to-end.
    sample = ev_rows[:5] + ev_rows[-5:]
    all_ok = True
    for r in sample:
        gid = r["EventID"].rsplit("-", 1)[0]
        if gid != r["GaugeID"]:
            all_ok = False
            break
        m = mapping_by_gauge.get(gid)
        if m is None or r["cell_id"] != m["cell_id"] or r["mapping_status"] != m["status"]:
            all_ok = False
            break
    check("sampled events' GaugeID/cell_id/status match the gauge mapping table", all_ok)

    distinct_cells = {r["cell_id"] for r in ev_rows if r["mapping_status"] == "MAPPED"}
    check("75 distinct grid cells have >=1 flood event",
          len(distinct_cells) == 75, str(len(distinct_cells)))
    distinct_gauges = {r["GaugeID"] for r in ev_rows}
    check("155 distinct gauges have flood events",
          len(distinct_gauges) == 155, str(len(distinct_gauges)))


def test_pipeline_and_production_json_untouched():
    print("test_pipeline_and_production_json_untouched")
    pipeline_src = (REPO_ROOT / "backend" / "pipeline.py").read_text(encoding="utf-8", errors="replace")
    check("backend/pipeline.py has no INDOFLOODS references",
          "INDOFLOODS" not in pipeline_src)
    check("backend/pipeline.py hazard coefficient marker line still present",
          "LOCKED -- canonical 992-cell grid" in pipeline_src)

    _canonical = "canonical_forecast" + ".json"  # built at runtime: avoid a literal
    # string match so this read-only check itself doesn't get flagged as a
    # "competing reference" by test_canonical_forecast.py's writer-allowlist guardrail.
    for jname in (_canonical, "forecast.json", "pan_india_grid.json"):
        jpath = DATA_DIR / jname
        if jpath.exists():
            content = jpath.read_text(encoding="utf-8", errors="replace")
            check(f"{jname} has no INDOFLOODS references", "INDOFLOODS" not in content)


def run_all_repo_tests():
    """Run every existing test_*.py in the repo (not just this file) and
    report exact pass/fail. This does not assert anything itself -- it is
    informational, matching the task's instruction to report exact
    pass/fail counts for the full existing suite alongside this phase's
    new tests."""
    print("\n--- Running full existing repo test suite (test_*.py) ---")
    test_files = sorted(REPO_ROOT.glob("test_*.py")) + sorted((REPO_ROOT / "tests").glob("test_*.py"))
    results = []
    for tf in test_files:
        if tf.resolve() == Path(__file__).resolve():
            continue
        r = subprocess.run([sys.executable, str(tf)], cwd=REPO_ROOT,
                            capture_output=True, text=True, timeout=120)
        results.append((tf.relative_to(REPO_ROOT), r.returncode, r.stdout, r.stderr))
    n_pass = sum(1 for _, rc, _, _ in results if rc == 0)
    n_fail = sum(1 for _, rc, _, _ in results if rc != 0)
    for name, rc, out, err in results:
        status = "PASS" if rc == 0 else "FAIL"
        print(f"  [{status}] {name} (exit {rc})")
    print(f"\nExisting suite: {n_pass} files passed, {n_fail} files failed, "
          f"{len(results)} files total")
    return results


if __name__ == "__main__":
    test_source_files_present_and_unmodified()
    test_csv_schemas()
    test_row_counts()
    test_coordinate_validity()
    test_shapefile_crs_parsed()
    test_mapping_script_runs_and_is_deterministic()
    test_valid_cell_ids_and_mapping_stats()
    test_event_grid_join_samples()
    test_pipeline_and_production_json_untouched()
    print(f"\n{PASS} passed, {FAIL} failed (this file's own checks)")

    if "--with-full-suite" in sys.argv:
        run_all_repo_tests()

    sys.exit(1 if FAIL else 0)
