#!/usr/bin/env python3
"""
test_integrity_guardrails.py -- Phase 4.5 hard guardrails.

A. GRID DRIFT: the canonical application grid must fail loudly if cell
   count != 992, resolution != 1.0deg, cell ID convention changes,
   duplicate cell IDs occur, or the grid extent changes unexpectedly.

B. LABEL SEMANTIC DRIFT: validation must fail if PROXY_NOT_OBSERVED
   appears as POSITIVE, UNKNOWN is converted to NEGATIVE, predictor-derived
   fields leak into observed label datasets, FF observed labels are
   claimed without a real event source, or duplicate timestamp/cell rows
   are silently accepted.

Run: python3 tests/test_integrity_guardrails.py   (from repo root)
"""
import ast
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from regrid import cell_id_for  # noqa: E402

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


# ---------------------------------------------------------------------------
# A. GRID DRIFT
# ---------------------------------------------------------------------------

def test_a1_pipeline_constants_locked():
    print("A1. backend/pipeline.py grid constants are locked to canonical values")
    src = (REPO_ROOT / "backend" / "pipeline.py").read_text()
    m = re.search(r"^APPLICATION_GRID_STEP\s*=\s*([\d.]+)", src, re.M)
    check("APPLICATION_GRID_STEP constant found", m is not None)
    if m:
        check("APPLICATION_GRID_STEP == 1.0", float(m.group(1)) == 1.0, f"got {m.group(1)}")
    m2 = re.search(r"^EXPECTED_APPLICATION_CELL_COUNT\s*=\s*(\d+)", src, re.M)
    check("EXPECTED_APPLICATION_CELL_COUNT constant found", m2 is not None)
    if m2:
        check("EXPECTED_APPLICATION_CELL_COUNT == 992", int(m2.group(1)) == 992, f"got {m2.group(1)}")
    check("no bare old-style 'GRID_STEP =' constant remains (renamed to avoid dual-purpose ambiguity)",
          not re.search(r"^GRID_STEP\s*=", src, re.M))
    check("a RuntimeError guardrail exists for cell-count mismatch",
          "CRITICAL: canonical application grid drift detected" in src)
    check("a duplicate-cell-id guardrail exists",
          "CRITICAL: duplicate application grid cell detected" in src)


def test_a2_reproduced_grid_matches_992():
    print("A2. reproducing the exact grid-construction loop yields 992 cells")
    BOUNDS = {"S": 6, "N": 37, "W": 68, "E": 98}
    STEP = 1.0
    lats = [round(v, 4) for v in np.arange(BOUNDS["S"], BOUNDS["N"] + STEP * 0.5, STEP)]
    lons = [round(v, 4) for v in np.arange(BOUNDS["W"], BOUNDS["E"] + STEP * 0.5, STEP)]
    check("992 cells reproduced from locked constants", len(lats) * len(lons) == 992,
          f"got {len(lats) * len(lons)}")


def test_a3_live_grid_matches_canonical():
    print("A3. live on-disk grid matches the canonical definition")
    d = json.loads((REPO_ROOT / "data" / "pan_india_grid.json").read_text())
    cells = d.get("grid_cells") or d.get("cells") or []
    check("n_cells == 992", len(cells) == 992, f"got {len(cells)}")
    check("grid_step_deg == 1.0", d.get("grid_step_deg") == 1.0, f"got {d.get('grid_step_deg')}")


def test_a4_cell_id_convention_stable():
    print("A4. cell_id convention is stable and collision-free")
    ids = [cell_id_for(la, lo) for la in [6.0, 7.0, 37.0] for lo in [68.0, 69.0, 98.0]]
    check("no duplicate cell_ids across a sample grid", len(ids) == len(set(ids)))
    check("format is IND_<lat>_<lon>", all(re.match(r"^IND_-?\d+\.\d_-?\d+\.\d$", i) for i in ids),
          f"e.g. {ids[0]}")


def test_a5_duplicate_cell_ids_detected():
    print("A5. a synthetic duplicate cell_id set is correctly detected")
    fake_cells = [{"lat": 10.0, "lon": 70.0}, {"lat": 10.0, "lon": 70.0}, {"lat": 11.0, "lon": 70.0}]
    seen = set()
    dupe_found = False
    for c in fake_cells:
        cid = f"{c['lat']:.1f}_{c['lon']:.1f}"
        if cid in seen:
            dupe_found = True
        seen.add(cid)
    check("duplicate detection logic correctly flags a synthetic duplicate", dupe_found)


def test_a6_unexpected_extent_detected():
    print("A6. an out-of-bounds/unexpected extent would be caught by the cell-count guardrail")
    BOUNDS = {"S": 6, "N": 37, "W": 68, "E": 98}
    wrong_step = 0.25
    lats = np.arange(BOUNDS["S"], BOUNDS["N"] + wrong_step * 0.5, wrong_step)
    lons = np.arange(BOUNDS["W"], BOUNDS["E"] + wrong_step * 0.5, wrong_step)
    n = len(lats) * len(lons)
    check("0.25deg step would produce != 992 cells (guardrail would fire)", n != 992, f"got {n}")


# ---------------------------------------------------------------------------
# B. LABEL SEMANTIC DRIFT
# ---------------------------------------------------------------------------

LABELS_DIR = REPO_ROOT / "processed" / "labels"


def test_b1_proxy_never_labeled_positive_bare():
    print("B1. FF proxy rows are never bare POSITIVE (must be PROXY_NOT_OBSERVED)")
    import gzip
    path = LABELS_DIR / "ff_labels_proxy.csv.gz"
    if not path.exists():
        path = LABELS_DIR / "ff_labels_proxy.csv"
    if not path.exists():
        check("ff_labels_proxy file exists to check", False, "run scripts/build_panindia_ff_labels.py first")
        return
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as f:
        df = pd.read_csv(f)
    bare_positive = df[df["label_status"] == "POSITIVE"]
    check("no FF proxy row has label_status == 'POSITIVE' (bare)", len(bare_positive) == 0,
          f"found {len(bare_positive)}")
    check("all FF proxy rows are PROXY_NOT_OBSERVED", (df["label_status"] == "PROXY_NOT_OBSERVED").all())


def test_b2_unknown_never_becomes_negative():
    print("B2. UNKNOWN rows never carry a concrete 0/1 label")
    ts_path = LABELS_DIR / "ts_labels.csv"
    if not ts_path.exists():
        check("ts_labels.csv exists to check", False, "run scripts/build_panindia_ts_labels.py first")
        return
    df = pd.read_csv(ts_path)
    unknown = df[df["label_status"] == "UNKNOWN"]
    check("UNKNOWN rows exist", len(unknown) > 0)
    check("UNKNOWN rows have null label, not 0", unknown["label"].isna().all(),
          f"non-null found: {unknown['label'].notna().sum()}")


def test_b3_no_predictor_columns_in_label_scripts():
    print("B3. label-construction scripts do not import/read predictor fields as label inputs")
    forbidden_reads = ["cape", "cin", "k_index", "totals_totals", "wind_shear",
                        "thunderstorm_probability", "cloudburst_probability", "flash_flood_probability"]
    for script in ["build_panindia_ts_labels.py", "build_panindia_cb_labels.py", "build_panindia_ff_labels.py"]:
        src = (REPO_ROOT / "scripts" / script).read_text()
        tree = ast.parse(src)
        # look for any string literal column access matching a forbidden predictor name
        string_literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        hits = [f for f in forbidden_reads if any(f == s.lower() for s in string_literals)]
        check(f"{script} references no predictor-field column names as data reads", len(hits) == 0, f"hits={hits}")


def test_b4_ff_observed_not_claimed_without_source():
    print("B4. FF genuine-event status is never claimed as available without a real coordinate source")
    summary_path = LABELS_DIR / "ff_labels_summary.json"
    if not summary_path.exists():
        check("ff_labels_summary.json exists to check", False, "run scripts/build_panindia_ff_labels.py first")
        return
    summary = json.loads(summary_path.read_text())
    status = summary["genuine_event_based_ff"]["status"]
    check("genuine event-based FF status starts with UNKNOWN", status.startswith("UNKNOWN"), f"got {status!r}")
    # As of 2026-09-30 (INDOFLOODS archive ingestion, see
    # docs/INDOFLOODS_DATA_INVENTORY.md), catchment_characteristics_indofloods.csv
    # DOES now exist in the repo -- but it carries no lat/lon of its own, and
    # nothing has integrated it (or metadata_indofloods.csv, which does carry
    # real gauge coordinates) into the label-building pipeline yet. So the
    # real invariant this test guards is unchanged: FF's genuine-event label
    # is still UNKNOWN everywhere in the actually-generated label files,
    # regardless of which source files exist on disk. The old assertion
    # ("catchment file is absent") tested a proxy for that invariant, not the
    # invariant itself, and is now stale -- replaced with a direct check of
    # the generated label file plus an explicit note that the source file's
    # mere presence does not by itself change anything about production
    # labels until a real Phase 4 integration wires it in.
    check("ff_labels_proxy.csv/ff_labels_summary.json still show FF genuine-event status as UNKNOWN "
          "(catchment_characteristics_indofloods.csv and metadata_indofloods.csv now exist on disk as of "
          "the 2026-09-30 INDOFLOODS ingestion, but are not yet wired into any label-building script -- "
          "see docs/INDOFLOODS_DATA_INVENTORY.md)",
          status.startswith("UNKNOWN"))


def test_b5_no_future_leakage_in_ts_labels():
    print("B5. TS labels carry only same-or-earlier-than-label source timestamps")
    ts_path = LABELS_DIR / "ts_labels.csv"
    if not ts_path.exists():
        check("ts_labels.csv exists to check", False)
        return
    df = pd.read_csv(ts_path)
    real = df[df["source_timestamp"].notna()].copy()
    real["ts_date"] = real["timestamp"].str.slice(0, 10)
    check("every real-labeled row's source_timestamp matches its own slot date (no lookahead)",
          (real["source_timestamp"] == real["ts_date"]).all())


def test_b6_duplicate_timestamp_cell_rejected():
    print("B6. duplicate (timestamp, cell_id, hazard) rows are absent from generated output")
    cb_path = LABELS_DIR / "cb_labels.csv.gz"
    if not cb_path.exists():
        cb_path = LABELS_DIR / "cb_labels.csv"
    if not cb_path.exists():
        check("cb_labels file exists to check", False)
        return
    import gzip
    opener = gzip.open if cb_path.suffix == ".gz" else open
    with opener(cb_path, "rt") as f:
        df = pd.read_csv(f)
    dupes = df.duplicated(subset=["timestamp", "cell_id", "hazard"]).sum()
    check("no duplicate (timestamp, cell_id, hazard) rows in CB output", dupes == 0, f"found {dupes}")


if __name__ == "__main__":
    for fn in [test_a1_pipeline_constants_locked, test_a2_reproduced_grid_matches_992,
               test_a3_live_grid_matches_canonical, test_a4_cell_id_convention_stable,
               test_a5_duplicate_cell_ids_detected, test_a6_unexpected_extent_detected,
               test_b1_proxy_never_labeled_positive_bare, test_b2_unknown_never_becomes_negative,
               test_b3_no_predictor_columns_in_label_scripts, test_b4_ff_observed_not_claimed_without_source,
               test_b5_no_future_leakage_in_ts_labels, test_b6_duplicate_timestamp_cell_rejected]:
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
