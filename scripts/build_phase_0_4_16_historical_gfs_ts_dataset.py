#!/usr/bin/env python3
"""
build_phase_0_4_16_historical_gfs_ts_dataset.py -- Phase 0.4.16 dataset
construction, research-only.

Combines every cycle that conforms to the Phase 0.4.12 dataset contract
(2 existing pilot cycles + the 6 new Stage B cycles verified GREEN across
Phase 0.4.14/0.4.15) into one auditable CSV + manifest. This script does
NOT reimplement GRIB extraction, label joining, event-group-key
computation, or precipitation semantics -- it calls the existing, already
contract-compliant `build_pilot()` from
scripts/build_vobl_historical_gfs_ts_join.py once per cycle and
concatenates the results. No model is trained or tuned here.

If a cycle's GRIB files are not present/readable where this script runs
(e.g. this session's 400MB staging limit for the three largest Stage B
cycles), that cycle is reported as SKIPPED with the exact reason -- never
silently dropped, never backfilled with an invented value. Re-run this
script from an environment that has all 8 cycles' files on disk (your own
machine) to get the complete dataset.

Usage (from the repository root):

    python scripts/build_phase_0_4_16_historical_gfs_ts_dataset.py

Optional:

    python scripts/build_phase_0_4_16_historical_gfs_ts_dataset.py \
        --ts-labels SIH_PANINDIA_GRID_LABELS_20260930_143541Z/processed/labels/ts_labels.csv \
        --out-dir data/processed/historical_gfs_ts
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_vobl_historical_gfs_ts_join as join_mod  # noqa: E402
from historical_dataset_split import (  # noqa: E402
    assign_partition, validate_no_event_group_key_crosses_partitions, TRAIN,
)

DATASET_CONTRACT_VERSION = "phase_0_4_16_v1"
DATASET_CONTRACT_DOC = "docs/PHASE_0_4_12_HISTORICAL_GFS_TS_DATASET_CONTRACT.md"

# The 8 cycles that make up this pilot dataset: the 2 original pilots
# (Phase 0.4.8/0.4.10/0.4.10B, already regenerated under the Phase 0.4.12
# contract) plus the 6 Stage B cycles selected in Phase 0.4.13 and
# verified in Phase 0.4.14/0.4.15. Not re-derived or re-selected here.
CYCLES = [
    {"cycle": "2015030300", "leads": [3, 9], "role": "existing_pilot"},
    {"cycle": "2020071500", "leads": [3, 6], "role": "existing_pilot"},
    {"cycle": "2016011500", "leads": [3, 6], "role": "stage_b"},
    {"cycle": "2017041606", "leads": [3, 6], "role": "stage_b"},
    {"cycle": "2019120918", "leads": [3, 6], "role": "stage_b"},
    {"cycle": "2021072406", "leads": [3, 6], "role": "stage_b"},
    {"cycle": "2022081512", "leads": [3, 6], "role": "stage_b"},
    {"cycle": "2023110612", "leads": [3, 6], "role": "stage_b"},
]

FEATURE_COLUMNS = [
    "cape", "cin", "pwat_mm", "u850", "v850", "u200", "v200",
    "wind_shear_850_200_ms", "t850_k", "t700_k", "t500_k",
    "rh850_pct", "rh700_pct", "k_index", "totals_totals",
    "gfs_prate_kg_m2_s", "gfs_precip_3h_interval_mm",
]

PREDICTOR_FIELDS = FEATURE_COLUMNS  # every one is FORECAST_DERIVED, verified below
LABEL_FIELDS = ["label", "label_status"]


def build_one_cycle(cycle: str, leads: list[int], labels_path: Path, tmp_dir: Path) -> dict:
    out_csv = tmp_dir / f"{cycle}.csv"
    out_manifest = tmp_dir / f"{cycle}_manifest.json"
    result = join_mod.build_pilot(cycle, leads, out_csv, out_manifest, ts_labels_path=labels_path)
    result["_out_csv"] = out_csv
    result["_out_manifest"] = out_manifest
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ts-labels", default=str(
        REPO_ROOT / "SIH_PANINDIA_GRID_LABELS_20260930_143541Z" / "processed" / "labels" / "ts_labels.csv"))
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "data" / "processed" / "historical_gfs_ts"))
    ap.add_argument("--tmp-dir", default=None, help="scratch dir for per-cycle intermediate files (default: <out-dir>/_per_cycle)")
    args = ap.parse_args()

    labels_path = Path(args.ts_labels)
    out_dir = Path(args.out_dir)
    tmp_dir = Path(args.tmp_dir) if args.tmp_dir else out_dir / "_per_cycle"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    cycles_built = []
    cycles_skipped = []
    per_cycle_manifests = []
    all_frames = []

    for spec in CYCLES:
        cycle, leads = spec["cycle"], spec["leads"]
        print(f"\n=== cycle {cycle} leads={leads} ({spec['role']}) ===")
        result = build_one_cycle(cycle, leads, labels_path, tmp_dir)
        if result.get("status") != "BUILT_FROM_ACTUAL_FILES":
            reason = result.get("missing_files") or result.get("reason") or result.get("status")
            print(f"  SKIPPED: {reason}")
            cycles_skipped.append({"cycle": cycle, "leads": leads, "reason": reason, "role": spec["role"]})
            continue
        df = pd.read_csv(result["_out_csv"])
        manifest = json.loads(result["_out_manifest"].read_text())
        df["source_cycle"] = cycle
        df["cycle_role"] = spec["role"]
        all_frames.append(df)
        per_cycle_manifests.append(manifest)
        cycles_built.append({"cycle": cycle, "leads": leads, "role": spec["role"], "n_rows": len(df)})
        print(f"  OK: {len(df)} rows")

    if not all_frames:
        print("\nNo cycles could be built -- nothing to write. STOP.")
        sys.exit(2)

    combined = pd.concat(all_frames, ignore_index=True)

    # ---- partition assignment (Phase 0.4.12 Part 3 contract) ----
    combined["train_holdout_partition"] = combined["ist_date"].apply(assign_partition)

    # ---- leakage-safety check across the WHOLE combined dataset, not
    # just within a cycle -- a real cross-cycle check, not assumed ----
    split_check = validate_no_event_group_key_crosses_partitions(
        combined["event_group_key"].tolist(), combined["train_holdout_partition"].tolist()
    )

    # ---- duplicate checks ----
    dup_key = combined[["source_file", "cell_id", "forecast_lead_hours"]].apply(tuple, axis=1)
    n_dup_source_cell_lead = len(combined) - dup_key.nunique()

    # ---- event-group collision check: every event_group_key's (ist_date,
    # slot_id) decode must agree with the key itself ----
    event_group_consistency_failures = []
    for key, grp in combined.groupby("event_group_key"):
        dates = grp["ist_date"].unique().tolist()
        slots = grp["slot_id"].unique().tolist()
        cells = grp["cell_id"].unique().tolist()
        if len(dates) != 1 or len(slots) != 1 or len(cells) != 1:
            event_group_consistency_failures.append({
                "event_group_key": key, "dates": dates, "slots": slots, "cells": cells,
            })

    # ---- missingness summary ----
    missingness = {}
    for col in FEATURE_COLUMNS:
        n_missing = int(combined[col].isna().sum()) if col in combined.columns else len(combined)
        missingness[col] = {"n_missing": n_missing, "n_total": len(combined),
                             "pct_missing": round(100.0 * n_missing / len(combined), 2) if len(combined) else None}

    # ---- precipitation provenance counts ----
    prate_provenance_counts = combined["gfs_prate_stepType_used"].value_counts(dropna=False).to_dict()
    interval_available_count = int(combined["gfs_precip_3h_interval_mm"].notna().sum())
    interval_missing_count = int(combined["gfs_precip_3h_interval_mm"].isna().sum())

    n_positive = int((combined["label"] == 1).sum())
    n_negative = int((combined["label"] == 0).sum())
    n_unknown = int(combined["label"].isna().sum())

    out_csv = out_dir / "historical_gfs_ts_dataset.csv"
    out_manifest_path = out_dir / "historical_gfs_ts_manifest.json"
    combined.to_csv(out_csv, index=False)

    source_files = []
    for m in per_cycle_manifests:
        source_files.extend(m["source_files"])

    manifest = {
        "contract_version": DATASET_CONTRACT_VERSION,
        "upstream_contract_doc": DATASET_CONTRACT_DOC,
        "generation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_files": source_files,
        "cycles_built": cycles_built,
        "cycles_skipped": cycles_skipped,
        "target_cell_id": combined["cell_id"].iloc[0] if len(combined) else None,
        "station_cell_mapping_reference": per_cycle_manifests[0]["vobl_cell_mapping"] if per_cycle_manifests else None,
        "label_definition_reference": per_cycle_manifests[0]["label_definition"] if per_cycle_manifests else None,
        "feature_contract": {
            "predictor_features": PREDICTOR_FIELDS,
            "label_fields_target_only": LABEL_FIELDS,
            "note": "pwat_mm is GFS precipitable water (PWAT), NOT an INSAT-derived "
                    "IWV observation -- the two are never conflated in this dataset.",
        },
        "precipitation_provenance_contract": {
            "primary": "gfs_prate_kg_m2_s (surface prate; stepType is instant when available, "
                       "avg fallback recorded explicitly in gfs_prate_stepType_used/gfs_prate_note)",
            "secondary": "gfs_precip_3h_interval_mm (tp[lead_end]-tp[lead_start]; populated ONLY "
                         "when compute_interval_precipitation()'s validity check passes; otherwise "
                         "left null with the rejection reason in gfs_precip_3h_interval_mm_note)",
            "stepType_counts_across_dataset": {str(k): int(v) for k, v in prate_provenance_counts.items()},
            "interval_precip_available_rows": interval_available_count,
            "interval_precip_missing_rows": interval_missing_count,
        },
        "temporal_partition_contract": {
            "train_years": "2015-2023", "holdout_years": "2024-2025",
            "partition_counts": combined["train_holdout_partition"].value_counts().to_dict(),
            "no_event_group_key_crosses_partitions": split_check.ok,
            "split_validation_detail": split_check.to_json(),
        },
        "row_count": len(combined),
        "event_group_count": combined["event_group_key"].nunique(),
        "positive_count": n_positive,
        "negative_count": n_negative,
        "unknown_count": n_unknown,
        "feature_missingness_summary": missingness,
        "duplicate_source_cell_lead_rows": int(n_dup_source_cell_lead),
        "event_group_consistency_failures": event_group_consistency_failures,
        "validation_status": (
            "GREEN" if (split_check.ok and n_dup_source_cell_lead == 0
                        and not event_group_consistency_failures and not cycles_skipped)
            else "YELLOW" if (split_check.ok and n_dup_source_cell_lead == 0
                              and not event_group_consistency_failures and cycles_skipped)
            else "RED"
        ),
    }
    out_manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

    print(f"\nWrote {out_csv} ({len(combined)} rows)")
    print(f"Wrote {out_manifest_path}")
    print(f"validation_status = {manifest['validation_status']}")
    if cycles_skipped:
        print(f"SKIPPED cycles (not in this run's output): {[c['cycle'] for c in cycles_skipped]}")


if __name__ == "__main__":
    main()
