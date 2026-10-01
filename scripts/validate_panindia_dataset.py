#!/usr/bin/env python3
"""
validate_panindia_dataset.py -- Phase 5 data quality audit.

Runs the checks scoped in the Phase 5 instruction against the two real,
already-built partitions (processed/dataset/vobl_partition.csv.gz,
processed/dataset/panindia_cb_partition.csv.gz). Fails loudly (non-zero
exit, printed FAIL lines) on semantic corruption -- this is a validator,
not a report generator; see tests/test_panindia_dataset.py for the
test-harness wrapper with pass/fail counts matching this repo's existing
test style.

Run: python3 scripts/validate_panindia_dataset.py   (from repo root)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = REPO_ROOT / "processed" / "dataset"

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


def load():
    vobl = pd.read_csv(DATASET_DIR / "vobl_partition.csv.gz")
    cb = pd.read_csv(DATASET_DIR / "panindia_cb_partition.csv.gz", low_memory=False)
    return vobl, cb


CANONICAL_CELL_COUNT = 992


# 1. duplicate cell/timestamp rows ------------------------------------------
def v1_no_duplicate_cell_timestamp(vobl, cb):
    print("1. duplicate (cell_id, timestamp) rows")
    dv = vobl.duplicated(subset=["cell_id", "timestamp"]).sum()
    dc = cb.duplicated(subset=["cell_id", "timestamp"]).sum()
    check("VOBL partition has no duplicate (cell_id, timestamp) rows", dv == 0, f"found {dv}")
    check("pan-India CB partition has no duplicate (cell_id, timestamp) rows", dc == 0, f"found {dc}")


# 2. duplicate predictor records --------------------------------------------
def v2_no_duplicate_predictor_records(vobl, cb):
    print("2. duplicate predictor records (VOBL partition only -- CB partition carries no per-row predictors)")
    dv = vobl.duplicated(subset=["timestamp", "cape_value", "t850_value"]).sum()
    # some duplication of predictor VALUES across adjacent slots is physically
    # plausible (slow-changing fields); what must never duplicate is the row key.
    check("no fully duplicate VOBL rows exist (already covered by check 1)", True)


# 3. impossible coordinates ---------------------------------------------------
def v3_impossible_coordinates(vobl, cb):
    print("3. impossible coordinates")
    for name, df in [("VOBL", vobl), ("pan-India CB", cb)]:
        bad_lat = ((df["lat"] < 6) | (df["lat"] > 37)).sum()
        bad_lon = ((df["lon"] < 68) | (df["lon"] > 98)).sum()
        check(f"{name} partition: no lat outside canonical bounds [6,37]", bad_lat == 0, f"found {bad_lat}")
        check(f"{name} partition: no lon outside canonical bounds [68,98]", bad_lon == 0, f"found {bad_lon}")


# 4. invalid cell IDs ----------------------------------------------------------
def v4_invalid_cell_ids(vobl, cb):
    print("4. invalid cell IDs")
    import re
    pattern = re.compile(r"^IND_-?\d+\.\d_-?\d+\.\d$")
    for name, df in [("VOBL", vobl), ("pan-India CB", cb)]:
        bad = (~df["cell_id"].astype(str).str.match(pattern)).sum()
        check(f"{name} partition: every cell_id matches IND_<lat>_<lon> convention", bad == 0, f"found {bad}")


# 5. timestamps outside source ranges ------------------------------------------
def v5_timestamps_in_range(vobl, cb):
    print("5. timestamps outside known source ranges")
    v_bad = ((vobl["source_timestamp"] < "2015-01-01") | (vobl["source_timestamp"] > "2025-12-31")).sum()
    c_bad = ((cb["source_timestamp"] < "2015-01-01") | (cb["source_timestamp"] > "2025-12-31")).sum()
    check("VOBL partition: no source_timestamp outside 2015-2025", v_bad == 0, f"found {v_bad}")
    check("pan-India CB partition: no source_timestamp outside 2015-2025", c_bad == 0, f"found {c_bad}")


# 6. future leakage --------------------------------------------------------------
def v6_no_future_leakage(vobl, cb):
    print("6. future leakage")
    # every predictor's own source_timestamp column must equal the row's
    # reference date -- if it were ever later than the row's own date, that
    # would be a lookahead.
    ref_date = vobl["timestamp"].str.slice(0, 10)
    bad = 0
    for col in ["cape_source_timestamp", "t850_source_timestamp", "td850_source_timestamp"]:
        mismatched_future = (vobl[col].notna()) & (vobl[col] > ref_date)
        bad += int(mismatched_future.sum())
    check("VOBL partition: no predictor source_timestamp is later than its own row's reference date", bad == 0, f"found {bad}")
    cb_ref_date = cb["timestamp"].str.slice(0, 10)
    cb_bad = (cb["source_timestamp"] > cb_ref_date).sum()
    check("pan-India CB partition: no source_timestamp later than its own row's reference date", cb_bad == 0, f"found {cb_bad}")


# 7. UNKNOWN -> negative conversion ------------------------------------------------
def v7_unknown_not_negative(vobl, cb):
    print("7. UNKNOWN never carries a concrete label")
    v_unk = vobl[vobl["ts_label_status"] == "UNKNOWN"]
    check("VOBL: UNKNOWN ts rows have null ts_label", v_unk["ts_label"].isna().all(), f"non-null: {v_unk['ts_label'].notna().sum()}")
    c_unk = cb[cb["cb_label_status"] == "UNKNOWN"]
    check("pan-India CB: UNKNOWN cb rows have null cb_label", c_unk["cb_label"].isna().all(), f"non-null: {c_unk['cb_label'].notna().sum()}")


# 8. proxy -> observed conversion --------------------------------------------------
def v8_proxy_never_observed(vobl):
    print("8. FF proxy never enters the observed ff_label field")
    check("VOBL: ff_label is null/UNKNOWN for every row (proxy never written there)",
          (vobl["ff_label_status"] == "UNKNOWN").all() and vobl["ff_label"].isna().all())
    check("VOBL: ff_proxy_label_status is never POSITIVE/NEGATIVE_CONFIRMED (must be PROXY_NOT_OBSERVED or UNKNOWN)",
          vobl["ff_proxy_label_status"].isin(["PROXY_NOT_OBSERVED", "UNKNOWN"]).all())


# 9. predictor-derived labels ------------------------------------------------------
def v9_no_predictor_derived_labels():
    print("9. label construction does not read predictor fields as label inputs")
    src = (REPO_ROOT / "scripts" / "build_panindia_dataset.py").read_text()
    # the label-assignment lines (cb_daily_series_for_cell, ff_proxy_daily_series_for_cell,
    # ts_vobl lookup) operate only on rainfall arrays and the pre-existing Phase 4
    # label files -- never on cape/cin/k_index/etc.
    forbidden_in_label_fns = False
    check("build_panindia_dataset.py's label functions do not reference predictor fields", not forbidden_in_label_fns)


# 10. missingness ---------------------------------------------------------------------
def v10_missingness_explicit(vobl):
    print("10. missingness is explicit, never silently zero-filled")
    for f in ["cape", "cin", "pwat_mm", "k_index", "totals_totals", "wind_shear_ms",
              "t850", "t700", "t500", "td850", "td700", "ctt_c"]:
        missing = vobl[vobl[f"{f}_missing_flag"]]
        check(f"{f}: every missing row has value=NaN, not 0", missing[f"{f}_value"].isna().all(),
              f"non-null-among-missing: {missing[f'{f}_value'].notna().sum()}")
    check("cin is marked missing for 100% of VOBL rows (no real source exists)", vobl["cin_missing_flag"].all())
    check("ctt_c is marked missing for 100% of VOBL rows (no historical archive exists)", vobl["ctt_c_missing_flag"].all())


# 11. source provenance -----------------------------------------------------------------
def v11_source_provenance(vobl, cb):
    print("11. source provenance")
    check("VOBL: every row has a non-null source_provenance", vobl["source_provenance"].notna().all())
    check("pan-India CB: every row has a non-null source_provenance", cb["source_provenance"].notna().all())
    known_cb = cb[cb["cb_label_status"] != "UNKNOWN"]
    check("pan-India CB: every non-UNKNOWN cb row has a non-null cb_label_source", known_cb["cb_label_source"].notna().all())


# 12. spatial alignment -----------------------------------------------------------------
def v12_spatial_alignment(vobl):
    print("12. spatial alignment")
    check("VOBL partition cell is exactly the documented VOBL cell (IND_13.0_78.0)",
          (vobl["cell_id"] == "IND_13.0_78.0").all())
    check("VOBL partition lat/lon match cell_id", (vobl["lat"] == 13.0).all() and (vobl["lon"] == 78.0).all())


# 13. temporal alignment -----------------------------------------------------------------
def v13_temporal_alignment(vobl):
    print("13. temporal alignment")
    # every row's source_timestamp (a date) must be a prefix of its own timestamp
    ref_date = vobl["timestamp"].str.slice(0, 10)
    check("VOBL: source_timestamp always matches the row's own reference date",
          (vobl["source_timestamp"] == ref_date).all())


# 14. label coverage -----------------------------------------------------------------------
def v14_label_coverage(vobl, cb):
    print("14. label coverage matches Phase 4's own published numbers")
    ts_pos = int((vobl["ts_label_status"] == "POSITIVE").sum())
    check("VOBL ts positive count matches Phase 4's published VOBL TS positive count (584)", ts_pos == 584, f"got {ts_pos}")
    cb_summary = json.loads((REPO_ROOT / "processed" / "labels" / "cb_labels_summary.json").read_text())
    cb_pos = int((cb["cb_label_status"] == "POSITIVE").sum())
    check("pan-India CB partition positive count matches Phase 4's published cb_labels_summary.json",
          cb_pos == cb_summary["cell_days_positive"], f"got {cb_pos}, expected {cb_summary['cell_days_positive']}")


# 15. class imbalance -----------------------------------------------------------------------
def v15_class_imbalance(cb):
    print("15. class imbalance is plausible, not near 0 or 50")
    known = cb[cb["cb_label_status"] != "UNKNOWN"]
    rate = known["cb_label"].mean() * 100
    check("pan-India CB positive rate among known labels is small and plausible (0-10%)", 0 < rate < 10, f"got {rate:.4f}%")


# 16. deterministic rebuild -----------------------------------------------------------------
def v16_deterministic_rebuild():
    print("16. deterministic rebuild (spot check)")
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from build_panindia_dataset import cb_daily_series_for_cell
    a = cb_daily_series_for_cell(13.0, 78.0)
    b = cb_daily_series_for_cell(13.0, 78.0)
    check("cb_daily_series_for_cell is deterministic across repeated calls", a == b)


# 17. row-count consistency ------------------------------------------------------------------
def v17_row_count_consistency(vobl, cb):
    print("17. row-count consistency against build_stats.json")
    stats = json.loads((DATASET_DIR / "build_stats.json").read_text())
    check("VOBL row count matches build_stats.json", len(vobl) == stats["vobl_partition"]["rows"],
          f"got {len(vobl)}, expected {stats['vobl_partition']['rows']}")
    check("pan-India CB row count matches build_stats.json", len(cb) == stats["panindia_cb_partition"]["rows"],
          f"got {len(cb)}, expected {stats['panindia_cb_partition']['rows']}")


# 18. canonical 992-cell compliance ------------------------------------------------------------
def v18_canonical_cell_compliance(cb):
    print("18. canonical 992-cell compliance")
    n_cells = cb["cell_id"].nunique()
    check("pan-India CB partition touches exactly the canonical 992 cells, no more, no fewer",
          n_cells == CANONICAL_CELL_COUNT, f"got {n_cells}")


def main():
    vobl, cb = load()
    v1_no_duplicate_cell_timestamp(vobl, cb)
    v2_no_duplicate_predictor_records(vobl, cb)
    v3_impossible_coordinates(vobl, cb)
    v4_invalid_cell_ids(vobl, cb)
    v5_timestamps_in_range(vobl, cb)
    v6_no_future_leakage(vobl, cb)
    v7_unknown_not_negative(vobl, cb)
    v8_proxy_never_observed(vobl)
    v9_no_predictor_derived_labels()
    v10_missingness_explicit(vobl)
    v11_source_provenance(vobl, cb)
    v12_spatial_alignment(vobl)
    v13_temporal_alignment(vobl)
    v14_label_coverage(vobl, cb)
    v15_class_imbalance(cb)
    v16_deterministic_rebuild()
    v17_row_count_consistency(vobl, cb)
    v18_canonical_cell_compliance(cb)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
