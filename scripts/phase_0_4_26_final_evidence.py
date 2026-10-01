#!/usr/bin/env python3
"""
phase_0_4_26_final_evidence.py -- Gate 1 deterministic final-assembly and
evidence generator for the Phase 0.4.26 TS acquisition.

WHY THIS EXISTS (root cause of the stale/wrong evidence bug this replaces):
notebooks/PHASE_0_4_26_TS_ACQUISITION_AND_DATASET.ipynb's Section 17/18
cells used to treat `event_group_key` as a unique row key. It is not --
every event group intentionally produces exactly two rows (forecast_lead
f003 and f006, confirmed in build_phase_0_4_20_dataset.py and
build_vobl_historical_gfs_ts_join.py). That bug flagged every legitimate
f003/f006 pair as a "duplicate" and every f003 row's deliberately-null
gfs_precip_3h_interval_mm as "incomplete". It also ran inline in the
notebook against whatever stale in-memory variables happened to be in the
kernel, which is how a failed run's numbers (317/251/66,
final_validation=FAILED) ended up persisted to
MyDrive/SIH-Historical-GFS/logs/phase_0_4_26_final_evidence_report.json
and could be mistaken for current truth.

This script is the single authoritative replacement. It:
  - reads ONLY the authoritative manifest (docs/PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv)
    and the persisted per-batch CSVs under processed_batches/ -- never a
    notebook variable, never in-memory state from a prior cell.
  - is FAIL CLOSED: on any real data-integrity violation (unexpected
    event group, wrong row count, wrong lead pair, cross-partition group,
    label disagreement, unknown label) it raises and writes NOTHING --
    no train/holdout CSV, no evidence JSON. A missing event group is NOT
    a violation; it is expected, explicitly counted, and reported.
  - writes evidence to a NEW, versioned filename
    (phase_0_4_26_final_evidence_v2.json), never overwriting or deleting
    the old stale file, and the evidence itself carries a schema_version,
    a manifest SHA256, and a content fingerprint of the assembled dataset
    so a stale copy is self-detectably stale rather than silently trusted.
  - reuses FEATURE_COLUMNS from build_phase_0_4_20_dataset.py rather than
    redefining it, and reuses TRAIN/HOLDOUT/validate_no_event_group_key_crosses_partitions
    from historical_dataset_split.py.

Does NOT change the manifest, does NOT change labels, does NOT redownload
or re-extract anything, does NOT call build_phase_0_4_20_dataset.py. It
only assembles and validates what has already been persisted.

Usage:
    python3 scripts/phase_0_4_26_final_evidence.py \\
        --manifest docs/PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv \\
        --processed-batches-dir /content/drive/MyDrive/SIH-Historical-GFS/processed_batches \\
        --train-out /content/drive/MyDrive/SIH-Historical-GFS/train/phase_0_4_26_train.csv \\
        --holdout-out /content/drive/MyDrive/SIH-Historical-GFS/holdout/phase_0_4_26_holdout.csv \\
        --evidence-out /content/drive/MyDrive/SIH-Historical-GFS/logs/phase_0_4_26_final_evidence_v2.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_phase_0_4_20_dataset as builder_mod  # noqa: E402 -- reuse FEATURE_COLUMNS, not redefine it
from historical_dataset_split import (  # noqa: E402
    TRAIN, HOLDOUT, validate_no_event_group_key_crosses_partitions,
)

EVIDENCE_SCHEMA_VERSION = "phase_0_4_26_final_evidence_v2"
FEATURE_COLUMNS = builder_mod.FEATURE_COLUMNS
PER_LEAD_ONLY_COLUMN = "gfs_precip_3h_interval_mm"
ROW_FEATURE_COLUMNS = [c for c in FEATURE_COLUMNS if c != PER_LEAD_ONLY_COLUMN]
EXPECTED_LEADS = {3, 6}
VALID_LABEL_STATUSES = {"POSITIVE", "NEGATIVE_CONFIRMED"}  # UNKNOWN/UNMATCHED are never acceptable here


class AssemblyFailure(Exception):
    """Raised to abort the whole assembly loudly, mirroring
    build_phase_0_4_20_dataset.py's BuildFailure contract: caught only at
    main()'s top level, always exits non-zero with the message printed,
    never swallowed to continue with partial output."""


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_of_dataframe(df: pd.DataFrame) -> str:
    """Deterministic content fingerprint: sort rows and columns into a
    canonical order first, so the same data always hashes the same way
    regardless of which batch directory it was read from or in what order."""
    if df.empty:
        return hashlib.sha256(b"EMPTY").hexdigest()
    ordered = df.sort_values(["event_group_key", "forecast_lead_hours"]).reset_index(drop=True)
    ordered = ordered.reindex(sorted(ordered.columns), axis=1)
    csv_bytes = ordered.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(csv_bytes).hexdigest()


def load_manifest(manifest_path: Path) -> pd.DataFrame:
    if not manifest_path.exists():
        raise AssemblyFailure(f"authoritative manifest not found: {manifest_path}")
    df = pd.read_csv(manifest_path)
    required_cols = {"event_group_key", "label", "partition", "forecast_lead"}
    missing = required_cols - set(df.columns)
    if missing:
        raise AssemblyFailure(f"manifest {manifest_path} is missing required column(s): {sorted(missing)}")
    return df


def load_acquired_rows(processed_batches_dir: Path) -> pd.DataFrame:
    """Reads every persisted per-batch dataset CSV. Deliberately does NOT
    read batch_candidate_manifest.csv (that is an intermediate input to
    build_phase_0_4_20_dataset.py, not an acquisition output)."""
    if not processed_batches_dir.exists():
        raise AssemblyFailure(f"processed batches directory not found: {processed_batches_dir}")
    frames = []
    for batch_dir in sorted(processed_batches_dir.glob("batch_*")):
        if not batch_dir.is_dir():
            continue
        for csv_path in sorted(batch_dir.glob("*.csv")):
            if csv_path.name == "batch_candidate_manifest.csv":
                continue
            frames.append(pd.read_csv(csv_path))
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    required_cols = {"event_group_key", "forecast_lead_hours", "label", "label_status", "train_holdout_partition"}
    missing = required_cols - set(combined.columns)
    if missing:
        raise AssemblyFailure(f"persisted batch CSVs are missing required column(s): {sorted(missing)}")
    return combined


def manifest_authoritative_groups(manifest_df: pd.DataFrame) -> dict:
    """One entry per event_group_key: {partition, label_status, leads}.
    FAILS LOUDLY if the manifest itself is internally inconsistent for a
    group (should never happen -- the manifest is never modified by this
    script or by anything upstream of it in Gate 1)."""
    groups = {}
    for egk, rows in manifest_df.groupby("event_group_key"):
        partitions = set(rows["partition"].unique())
        if len(partitions) != 1:
            raise AssemblyFailure(f"manifest itself has inconsistent partition for {egk}: {sorted(partitions)}")
        labels = set(rows["label"].unique())
        if len(labels) != 1:
            raise AssemblyFailure(f"manifest itself has inconsistent label for {egk}: {sorted(labels)}")
        leads = sorted(int(x.lstrip("f")) for x in rows["forecast_lead"].unique())
        if leads != sorted(EXPECTED_LEADS):
            raise AssemblyFailure(f"manifest itself does not have the expected lead pair for {egk}: {leads}")
        groups[egk] = {"partition": partitions.pop(), "label_status": labels.pop(), "leads": set(leads)}
    return groups


def assemble_and_validate(manifest_df: pd.DataFrame, acquired_df: pd.DataFrame) -> dict:
    """The core, filesystem-free assembly + validation pass. Returns a
    dict: {"evidence": {...}, "train_df": DataFrame, "holdout_df": DataFrame}.
    Raises AssemblyFailure on any real data-integrity violation. A missing
    event group is never a violation -- it is counted in the evidence and
    the assembly proceeds with exactly the groups that were acquired."""
    authoritative = manifest_authoritative_groups(manifest_df)
    authoritative_keys = set(authoritative.keys())

    failures = []

    if acquired_df.empty:
        acquired_keys = set()
    else:
        acquired_keys = set(acquired_df["event_group_key"].unique())

    unexpected_groups = sorted(acquired_keys - authoritative_keys)
    if unexpected_groups:
        failures.append(f"{len(unexpected_groups)} acquired event group(s) do not appear in the authoritative "
                         f"manifest at all: {unexpected_groups}")

    missing_groups = sorted(authoritative_keys - acquired_keys)  # expected, NOT a failure

    # Per-group structural + label + partition validation, for every group that was actually acquired.
    row_count_bad = []
    lead_pair_bad = []
    partition_bad = []
    label_bad = []
    unknown_label_bad = []
    completeness_bad = []
    interval_bad = []

    acquired_keys_present = acquired_keys & authoritative_keys
    for egk in sorted(acquired_keys_present):
        group_rows = acquired_df[acquired_df["event_group_key"] == egk]

        if len(group_rows) != 2:
            row_count_bad.append({"event_group_key": egk, "n_rows": int(len(group_rows))})
            continue  # the checks below assume exactly 2 rows; skip them for this group

        actual_leads = set(int(x) for x in group_rows["forecast_lead_hours"].unique())
        if len(group_rows["forecast_lead_hours"].unique()) != 2 or actual_leads != EXPECTED_LEADS:
            lead_pair_bad.append({"event_group_key": egk, "leads_found": sorted(actual_leads)})
            continue

        acquired_partitions = set(group_rows["train_holdout_partition"].unique())
        expected_partition = authoritative[egk]["partition"]
        if len(acquired_partitions) != 1 or acquired_partitions != {expected_partition}:
            partition_bad.append({
                "event_group_key": egk, "expected": expected_partition,
                "found": sorted(acquired_partitions),
            })

        acquired_label_statuses = set(group_rows["label_status"].unique())
        expected_label_status = authoritative[egk]["label_status"]
        if len(acquired_label_statuses) != 1 or acquired_label_statuses != {expected_label_status}:
            label_bad.append({
                "event_group_key": egk, "expected": expected_label_status,
                "found": sorted(acquired_label_statuses),
            })
        if any(s not in VALID_LABEL_STATUSES for s in acquired_label_statuses):
            unknown_label_bad.append({"event_group_key": egk, "label_statuses": sorted(acquired_label_statuses)})

        # Feature completeness: every feature except the per-lead-only one must be
        # non-null on every row of this group.
        missing_feature_cols = [c for c in FEATURE_COLUMNS if c not in group_rows.columns]
        if missing_feature_cols:
            completeness_bad.append({"event_group_key": egk, "missing_columns": missing_feature_cols})
        else:
            if group_rows[ROW_FEATURE_COLUMNS].isnull().any().any():
                completeness_bad.append({"event_group_key": egk, "reason": "null value in a required feature column"})
            # gfs_precip_3h_interval_mm is EXPECTED null on the earlier lead and
            # REQUIRED non-null on the later lead -- this is the exact check the
            # old Section 18 logic got wrong by treating the expected null as
            # generic incompleteness.
            later_lead_row = group_rows.loc[group_rows["forecast_lead_hours"].idxmax()]
            if pd.isna(later_lead_row[PER_LEAD_ONLY_COLUMN]):
                interval_bad.append({"event_group_key": egk})

    if row_count_bad:
        failures.append(f"{len(row_count_bad)} event group(s) do not have exactly 2 rows: {row_count_bad}")
    if lead_pair_bad:
        failures.append(f"{len(lead_pair_bad)} event group(s) do not have exactly the f003+f006 lead pair: {lead_pair_bad}")
    if partition_bad:
        failures.append(f"{len(partition_bad)} event group(s) have a partition that disagrees with the "
                         f"authoritative manifest, or a group whose own rows disagree with each other: {partition_bad}")
    if label_bad:
        failures.append(f"{len(label_bad)} event group(s) have a label_status that disagrees with the "
                         f"authoritative manifest: {label_bad}")
    if unknown_label_bad:
        failures.append(f"{len(unknown_label_bad)} event group(s) have an UNKNOWN/UNMATCHED label_status, "
                         f"which is never acceptable in the final dataset: {unknown_label_bad}")
    if completeness_bad:
        failures.append(f"{len(completeness_bad)} event group(s) have incomplete required features "
                         f"(excluding the expected-null {PER_LEAD_ONLY_COLUMN} on the earlier lead): {completeness_bad}")
    if interval_bad:
        failures.append(f"{len(interval_bad)} event group(s) are missing {PER_LEAD_ONLY_COLUMN} on their "
                         f"LATER lead, where it is required, not merely allowed to be null: {interval_bad}")

    # Build the clean subset (only groups that passed every per-group check) for the
    # cross-partition check and for the actual train/holdout output.
    bad_keys = {d["event_group_key"] for d in (row_count_bad + lead_pair_bad + partition_bad + label_bad
                                                + unknown_label_bad + completeness_bad + interval_bad)}
    clean_keys = sorted(acquired_keys_present - bad_keys)
    clean_df = acquired_df[acquired_df["event_group_key"].isin(clean_keys)].copy()

    if not clean_df.empty:
        split_check = validate_no_event_group_key_crosses_partitions(
            clean_df["event_group_key"].tolist(), clean_df["train_holdout_partition"].tolist()
        )
        if not split_check.ok:
            failures.append(f"event_group_key crosses train/holdout partitions in the clean subset: {split_check.to_json()}")

    if failures:
        raise AssemblyFailure(
            "Phase 0.4.26 final assembly FAILED -- " + " | ".join(failures)
        )

    # Partition is taken from the authoritative manifest, never recomputed from
    # ist_date here -- that recomputation already happened once, independently,
    # inside build_phase_0_4_20_dataset.py per batch, and agreement with it was
    # just checked above (partition_bad). This assembly step trusts the manifest.
    train_df = clean_df[clean_df["event_group_key"].map(lambda k: authoritative[k]["partition"]) == TRAIN].copy()
    holdout_df = clean_df[clean_df["event_group_key"].map(lambda k: authoritative[k]["partition"]) == HOLDOUT].copy()
    train_df = train_df.sort_values(["event_group_key", "forecast_lead_hours"]).reset_index(drop=True)
    holdout_df = holdout_df.sort_values(["event_group_key", "forecast_lead_hours"]).reset_index(drop=True)

    n_positive = int((clean_df["label_status"] == "POSITIVE").sum())
    n_negative = int((clean_df["label_status"] == "NEGATIVE_CONFIRMED").sum())

    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "generated_by": "scripts/phase_0_4_26_final_evidence.py",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "self_describing_note": (
            "This file supersedes any older phase_0_4_26_final_evidence_report.json. "
            "That older filename is a DIFFERENT, EARLIER, now-superseded evidence format "
            "produced by a buggy notebook cell and must not be read as current truth. "
            "This file's schema_version and dataset_content_sha256 are the only fields "
            "that should be trusted to confirm which run a given number came from."
        ),
        "target_groups_total": len(authoritative_keys),
        "acquired_groups_total": len(acquired_keys_present),
        "acquired_groups_clean_total": len(clean_keys),
        "missing_groups_total": len(missing_groups),
        "missing_groups": missing_groups,
        "unexpected_groups_total": len(unexpected_groups),
        "unexpected_groups": unexpected_groups,
        "train_groups": sorted(train_df["event_group_key"].unique().tolist()) if not train_df.empty else [],
        "holdout_groups": sorted(holdout_df["event_group_key"].unique().tolist()) if not holdout_df.empty else [],
        "train_groups_total": int(train_df["event_group_key"].nunique()) if not train_df.empty else 0,
        "holdout_groups_total": int(holdout_df["event_group_key"].nunique()) if not holdout_df.empty else 0,
        "rows_total": int(len(clean_df)),
        "rows_train": int(len(train_df)),
        "rows_holdout": int(len(holdout_df)),
        "positive_rows": n_positive,
        "negative_rows": n_negative,
        "rows_per_event_group_validation": "PASSED -- every acquired, clean event group has exactly 2 rows",
        "lead_pair_validation": "PASSED -- every acquired, clean event group has exactly leads {3, 6} (f003+f006)",
        "partition_validation": "PASSED -- every acquired, clean event group's partition matches the authoritative manifest, and no event_group_key crosses train/holdout",
        "label_validation": "PASSED -- every acquired, clean event group's label_status matches the authoritative manifest, none UNKNOWN/UNMATCHED",
        "feature_completeness_status": (
            f"PASSED -- all {len(ROW_FEATURE_COLUMNS)} always-required feature columns are non-null on every row; "
            f"{PER_LEAD_ONLY_COLUMN} is null on the earlier lead by design (per the existing precipitation "
            f"contract) and required non-null on the later lead, both confirmed"
        ),
        "acquisition_failures": missing_groups,
        "dataset_content_sha256": sha256_of_dataframe(clean_df),
    }

    return {"evidence": evidence, "train_df": train_df, "holdout_df": holdout_df}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "docs" / "PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv")
    parser.add_argument("--processed-batches-dir", type=Path, required=True)
    parser.add_argument("--train-out", type=Path, required=True)
    parser.add_argument("--holdout-out", type=Path, required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        manifest_df = load_manifest(args.manifest)
        acquired_df = load_acquired_rows(args.processed_batches_dir)
        result = assemble_and_validate(manifest_df, acquired_df)
    except AssemblyFailure as e:
        print("PHASE 0.4.26 FINAL ASSEMBLY: FAILED", file=sys.stderr)
        print(str(e), file=sys.stderr)
        print("Writing NOTHING -- no train/holdout CSV, no evidence JSON. Fix the underlying "
              "data/manifest issue and re-run.", file=sys.stderr)
        return 1

    evidence = result["evidence"]
    evidence["manifest_path"] = str(args.manifest)
    evidence["manifest_sha256"] = sha256_of_file(args.manifest)

    args.train_out.parent.mkdir(parents=True, exist_ok=True)
    args.holdout_out.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_out.parent.mkdir(parents=True, exist_ok=True)
    result["train_df"].to_csv(args.train_out, index=False)
    result["holdout_df"].to_csv(args.holdout_out, index=False)
    args.evidence_out.write_text(json.dumps(evidence, indent=2, sort_keys=True))

    print("PHASE 0.4.26 FINAL ASSEMBLY: PASSED")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    print(f"\nWrote {args.train_out} ({evidence['rows_train']} rows)")
    print(f"Wrote {args.holdout_out} ({evidence['rows_holdout']} rows)")
    print(f"Wrote {args.evidence_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
