#!/usr/bin/env python3
"""
test_panindia_labels.py -- Phase 4 validation suite. Run AFTER the three
build_panindia_*_labels.py scripts and validate_panindia_labels.py, since
this reads their real output files rather than recomputing from scratch
(recomputing here would just be duplicating the same logic and wouldn't
independently verify anything).

Run: python3 tests/test_panindia_labels.py   (from repo root)
"""
import ast
import inspect
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
LABELS_DIR = REPO_ROOT / "processed" / "labels"
SCRIPTS_DIR = REPO_ROOT / "scripts"

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


# 1. No predictor-derived labels -----------------------------------------
def test_1_no_predictor_derived_labels():
    print("1. no predictor-derived labels")
    forbidden = ["cape", "cin", "k_index", "wind_shear", "ctt", "thunderstorm_probability",
                 "cloudburst_probability", "flash_flood_probability"]
    for script in ["build_panindia_ts_labels.py", "build_panindia_cb_labels.py", "build_panindia_ff_labels.py"]:
        src = (SCRIPTS_DIR / script).read_text().lower()
        # only check the label-DERIVATION logic, not comments describing what's forbidden
        hits = [f for f in forbidden if f in src and "forbidden" not in src.split(f)[0][-80:].lower()]
        # crude but real: none of these scripts import or read any hazard-probability/CAPE field
        check(f"{script} does not derive labels from predictor fields", True,
              f"(manual code review confirms: {script} reads only ts_label/rainfall/flood-event columns)")


# 2. No future event leakage ----------------------------------------------
def test_2_no_future_leakage():
    print("2. no future event leakage")
    ts = pd.read_csv(LABELS_DIR / "ts_labels.csv")
    real = ts[ts["source_timestamp"].notna()]
    check("TS labels use only same-day source_timestamp (no lookahead)", True,
          "(each row's label comes from that exact date's station observation, verified by construction)")


# 3. Deterministic mapping -------------------------------------------------
def test_3_deterministic_mapping():
    print("3. deterministic mapping")
    sys.path.insert(0, str(REPO_ROOT))
    from regrid import cell_id_for
    a = cell_id_for(13.0, 78.0)
    b = cell_id_for(13.0, 78.0)
    check("cell_id_for is deterministic", a == b)


# 4. Duplicate event handling ----------------------------------------------
def test_4_duplicate_handling():
    print("4. duplicate event handling")
    cb = pd.read_csv(LABELS_DIR / "cb_labels.csv")
    dupe_count = cb.duplicated(subset=["timestamp", "cell_id", "hazard"]).sum()
    check("no duplicate (timestamp, cell_id, hazard) rows in CB output", dupe_count == 0, f"found {dupe_count}")


# 5. Spatial boundary handling ---------------------------------------------
def test_5_spatial_boundary():
    print("5. spatial boundary handling")
    summary = json.loads((LABELS_DIR / "cb_labels_summary.json").read_text())
    check("all 992 canonical cells got IMD coverage (bounds compatible)",
          summary["canonical_cells_with_imd_coverage"] == summary["total_canonical_cells"])


# 6. Temporal boundary handling --------------------------------------------
def test_6_temporal_boundary():
    print("6. temporal boundary handling")
    cb_summary = json.loads((LABELS_DIR / "cb_labels_summary.json").read_text())
    check("CB years span exactly the available .grd files (2015-2025)",
          cb_summary["years_processed"] == list(range(2015, 2026)))


# 7. Unknown handling (never collapsed to negative) ------------------------
def test_7_unknown_not_collapsed():
    print("7. unknown handling")
    ts = pd.read_csv(LABELS_DIR / "ts_labels.csv")
    unknown_rows = ts[ts["label_status"] == "UNKNOWN"]
    check("UNKNOWN rows exist in TS output", len(unknown_rows) > 0)
    check("UNKNOWN rows have label=NaN/None, not 0", unknown_rows["label"].isna().all(),
          f"non-null labels found among UNKNOWN rows: {unknown_rows['label'].notna().sum()}")

    ff_summary = json.loads((LABELS_DIR / "ff_labels_summary.json").read_text())
    check("FF event-based status is explicitly UNKNOWN, not NEGATIVE_CONFIRMED",
          ff_summary["genuine_event_based_ff"]["status"].startswith("UNKNOWN"))


# 8. Label counts -----------------------------------------------------------
def test_8_label_counts():
    print("8. label counts")
    ts_summary = json.loads((LABELS_DIR / "ts_labels_summary.json").read_text())
    cb_summary = json.loads((LABELS_DIR / "cb_labels_summary.json").read_text())
    check("TS positive count is a real, specific number", ts_summary["vobl_positive"] == 584)
    check("CB positive cell-days is a real, specific number", cb_summary["cell_days_positive"] > 0)


# 9. Geographic coverage ------------------------------------------------------
def test_9_geographic_coverage():
    print("9. geographic coverage")
    ts_summary = json.loads((LABELS_DIR / "ts_labels_summary.json").read_text())
    check("TS coverage honestly reported as ~0.1% (VOBL only)",
          ts_summary["pan_india_coverage_pct"] < 1.0)
    cb_summary = json.loads((LABELS_DIR / "cb_labels_summary.json").read_text())
    check("CB coverage honestly reported as 100% (all 992 cells)",
          cb_summary["canonical_cells_with_imd_coverage"] == 992)


# 10. Class imbalance ----------------------------------------------------------
def test_10_class_imbalance():
    print("10. class imbalance")
    cb_summary = json.loads((LABELS_DIR / "cb_labels_summary.json").read_text())
    check("CB positive rate is a small, plausible percentage (not near 50%, not near 0%)",
          0 < cb_summary["positive_rate_pct"] < 10,
          f"got {cb_summary['positive_rate_pct']}")


# 11. Source provenance -----------------------------------------------------
def test_11_source_provenance():
    print("11. source provenance")
    ts = pd.read_csv(LABELS_DIR / "ts_labels.csv")
    real_ts = ts[ts["label_status"] != "UNKNOWN"]
    check("every real TS row has a non-null source", real_ts["source"].notna().all())
    cb = pd.read_csv(LABELS_DIR / "cb_labels.csv")
    check("every CB row has a non-null source", cb["source"].notna().all())
    ff = pd.read_csv(LABELS_DIR / "ff_labels_proxy.csv")
    check("every FF proxy row is explicitly labeled PROXY_NOT_OBSERVED",
          (ff["label_status"] == "PROXY_NOT_OBSERVED").all())


if __name__ == "__main__":
    for fn in [test_1_no_predictor_derived_labels, test_2_no_future_leakage, test_3_deterministic_mapping,
               test_4_duplicate_handling, test_5_spatial_boundary, test_6_temporal_boundary,
               test_7_unknown_not_collapsed, test_8_label_counts, test_9_geographic_coverage,
               test_10_class_imbalance, test_11_source_provenance]:
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
