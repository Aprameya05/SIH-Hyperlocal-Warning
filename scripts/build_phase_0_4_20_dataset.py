#!/usr/bin/env python3
"""
build_phase_0_4_20_dataset.py -- Phase 0.4.20 manifest-driven,
HOLDOUT-SAFE historical GFS dataset builder for the Phase 0.4.19 acquired
batch (40 files / 20 event groups, verified GREEN).

This script does NOT hardcode a cycle list (unlike
build_phase_0_4_16_historical_gfs_ts_dataset.py's CYCLES table). It
derives every (event_group_key, cycle, leads, partition, label) tuple
from docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv at run time, and
independently recomputes the GRIB-derived event_group_key and the
assign_partition()-derived TRAIN/HOLDOUT split for every row it builds --
the manifest CSV's own columns are a plan to check against, never trusted
blindly.

HOLDOUT SAFETY (the primary reason this script exists): `--partition` is
REQUIRED, not defaulted. In `--partition train` mode, HOLDOUT-tagged event
groups are filtered out of the candidate list BEFORE any GRIB file is
opened or even path-resolved -- this is a structural exclusion, not a
post-hoc label. If anything downstream (an independent recomputation of
assign_partition(), or the acquisition provenance manifest's own
partition field) ever disagrees that a file selected for a TRAIN build is
actually TRAIN, the whole build FAILS LOUDLY and writes nothing, rather
than silently excluding just that one row and continuing.

FAIL CLOSED: a missing GRIB file, a partition disagreement, a duplicate
event_group_key, or an unexpected label_status all abort the entire build
with no partial output written. This script never substitutes a
different cycle for a missing one, and never produces a dataset file
whose row count doesn't exactly match what was requested.

Reuses, never reimplements: `build_pilot()` (GRIB extraction + label
join + event-group-key computation) and `WANTED_FIELDS` from
scripts/build_vobl_historical_gfs_ts_join.py; `assign_partition()` and
`validate_no_event_group_key_crosses_partitions()` from
scripts/historical_dataset_split.py. The only new logic here is
manifest-driven candidate selection, the partition fail-closed gate, and
cross-batch validation generalized from
build_phase_0_4_16_historical_gfs_ts_dataset.py's own checks.

Usage (from the repository root):

    python scripts/build_phase_0_4_20_dataset.py --partition train
    python scripts/build_phase_0_4_20_dataset.py --partition holdout
    python scripts/build_phase_0_4_20_dataset.py --partition all   # inspection/export ONLY, never training input

    # D: drive storage (Phase 0.4.19.1):
    python scripts/build_phase_0_4_20_dataset.py --partition train --raw-root D:\\SIH-Historical-GFS\\raw

Does NOT download anything, does NOT train anything, does NOT modify
production code, and does NOT overwrite the existing Phase 0.4.16
16-row dataset (data/processed/historical_gfs_ts/historical_gfs_ts_dataset.csv).
"""
from __future__ import annotations

import argparse
import csv
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
    assign_partition, validate_no_event_group_key_crosses_partitions, TRAIN, HOLDOUT,
)

BUILDER_VERSION = "phase_0_4_20_v1"
DATASET_CONTRACT_DOC = "docs/PHASE_0_4_12_HISTORICAL_GFS_TS_DATASET_CONTRACT.md"

CANDIDATE_MANIFEST_CSV = REPO_ROOT / "docs" / "PHASE_0_4_17_ACQUISITION_MANIFEST.csv"
PROVENANCE_MANIFEST = REPO_ROOT / "data" / "external" / "historical_gfs" / "manifest.json"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "external" / "historical_gfs"

FEATURE_COLUMNS = [
    "cape", "cin", "pwat_mm", "u850", "v850", "u200", "v200",
    "wind_shear_850_200_ms", "t850_k", "t700_k", "t500_k",
    "rh850_pct", "rh700_pct", "k_index", "totals_totals",
    "gfs_prate_kg_m2_s", "gfs_precip_3h_interval_mm",
]
LABEL_FIELDS = ["label", "label_status"]
ALLOWED_LABEL_STATUSES = {"POSITIVE", "NEGATIVE_CONFIRMED", "UNKNOWN"}

EXISTING_PHASE_0_4_16_OUT_CSV = REPO_ROOT / "data" / "processed" / "historical_gfs_ts" / "historical_gfs_ts_dataset.csv"


class BuildFailure(Exception):
    """Raised to abort the whole build loudly. Caught only at main()'s top
    level so it always exits non-zero with the message printed -- never
    swallowed to continue with partial output."""


def load_event_groups(candidate_csv: Path) -> list[dict]:
    """Groups the real candidate CSV by event_group_key and returns one
    entry per event group: {event_group_key, cycle, leads, csv_partition,
    csv_label, target_ist_date, season}. FAILS LOUDLY if any event group
    maps to more than one distinct cycle (would indicate a manifest bug,
    not something to silently average over)."""
    if not candidate_csv.exists():
        raise BuildFailure(f"candidate manifest not found: {candidate_csv}")

    by_group: dict[str, list[dict]] = {}
    with open(candidate_csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("selected_gfs_cycle") == "AMBIGUOUS":
                continue
            by_group.setdefault(row["event_group_key"], []).append(row)

    groups = []
    for egk, rows in by_group.items():
        cycles = {r["selected_gfs_cycle"] for r in rows}
        if len(cycles) != 1:
            raise BuildFailure(
                f"event_group_key {egk} maps to more than one GFS cycle in the candidate "
                f"manifest: {sorted(cycles)} -- refusing to guess which is correct"
            )
        partitions = {r["partition"] for r in rows}
        if len(partitions) != 1:
            raise BuildFailure(
                f"event_group_key {egk} has inconsistent partition values within the "
                f"candidate manifest itself: {sorted(partitions)}"
            )
        labels = {r["label"] for r in rows}
        if len(labels) != 1:
            raise BuildFailure(
                f"event_group_key {egk} has inconsistent label values within the "
                f"candidate manifest itself: {sorted(labels)}"
            )
        leads = sorted({int(r["selected_lead"].lstrip("f")) for r in rows})
        groups.append({
            "event_group_key": egk,
            "cycle": cycles.pop(),
            "leads": leads,
            "csv_partition": partitions.pop(),
            "csv_label": labels.pop(),
            "target_ist_date": rows[0]["target_ist_date"],
            "season": rows[0]["season"],
        })
    return groups


def verify_partition_agreement(groups: list[dict], provenance: dict) -> None:
    """Independently recomputes assign_partition() for every group and
    FAILS LOUDLY on any disagreement with either (a) the candidate CSV's
    own partition column, or (b) the acquisition provenance manifest's
    recorded partition for that group's files (when present). This is the
    authoritative cross-check the Phase 0.4.20 brief requires -- partition
    is never taken on faith from a single source."""
    for g in groups:
        recomputed = assign_partition(g["target_ist_date"])
        if recomputed != g["csv_partition"]:
            raise BuildFailure(
                f"PARTITION DISAGREEMENT for event_group_key {g['event_group_key']}: "
                f"candidate manifest says '{g['csv_partition']}', but assign_partition("
                f"'{g['target_ist_date']}') computes '{recomputed}'. Refusing to build."
            )
        for lead in g["leads"]:
            filename = join_mod.grib_filename_for(g["cycle"], lead)
            prov_entry = provenance.get(filename)
            if prov_entry is not None and prov_entry.get("partition") is not None:
                if prov_entry["partition"] != g["csv_partition"]:
                    raise BuildFailure(
                        f"PARTITION DISAGREEMENT for {filename} (event_group_key "
                        f"{g['event_group_key']}): acquisition provenance manifest says "
                        f"'{prov_entry['partition']}', candidate manifest says "
                        f"'{g['csv_partition']}'. Refusing to build."
                    )


def filter_by_partition(groups: list[dict], requested: str) -> list[dict]:
    """THE structural HOLDOUT-safety gate. For 'train', every group whose
    csv_partition is not exactly 'train' is dropped HERE, before any file
    path is resolved or any GRIB file is opened -- a HOLDOUT file is never
    even looked at during a TRAIN build. Symmetric for 'holdout'. 'all'
    passes everything through unfiltered (inspection/export only -- see
    the loud warning printed by main())."""
    if requested == "all":
        return list(groups)
    if requested not in (TRAIN, HOLDOUT):
        raise BuildFailure(f"invalid --partition value: {requested!r} (must be train, holdout, or all)")
    return [g for g in groups if g["csv_partition"] == requested]


def verify_raw_root_exists(raw_root: Path) -> None:
    """Preflight gate, run BEFORE any per-file existence check. Distinguishes
    "the raw_root directory itself does not exist" (almost always a
    command-line/shell argument problem -- e.g. an unquoted backslash path
    mangled by a POSIX-style shell such as Git Bash before Python ever saw
    it) from "the directory exists but specific files are missing" (an
    actual acquisition gap). Without this check, the first case previously
    surfaced as a wall of N identical-looking missing-file errors from
    verify_files_present(), which is indistinguishable at a glance from a
    real acquisition failure. This check changes no build logic -- it only
    makes the failure self-diagnosing."""
    resolved = raw_root.resolve()
    print(f"[phase_0_4_20] --raw-root argument received : {raw_root!r}", file=sys.stderr)
    print(f"[phase_0_4_20] --raw-root resolved to        : {resolved}", file=sys.stderr)
    if not raw_root.exists() or not raw_root.is_dir():
        raise BuildFailure(
            f"RAW_ROOT DIRECTORY NOT FOUND: {raw_root!r} (resolved: {resolved}) does not "
            "exist on disk, or is not a directory. This is NOT necessarily an acquisition "
            "failure -- it is usually the --raw-root argument arriving corrupted from the "
            "shell. In particular, an UNQUOTED Windows path containing backslashes (e.g. "
            "--raw-root D:\\SIH-Historical-GFS\\raw) is silently mangled by POSIX-style "
            "shells such as Git Bash/MSYS/MinGW, which treat backslash as an escape "
            "character and strip it before Python ever receives the string (verified: in "
            "such a shell, D:\\SIH-Historical-GFS\\raw becomes the single corrupted token "
            "D:SIH-Historical-GFSraw). Fix: quote the path (--raw-root \"D:\\SIH-Historical-"
            "GFS\\raw\") or use forward slashes (--raw-root D:/SIH-Historical-GFS/raw), both "
            "of which pass through every common Windows shell unchanged. If the printed "
            "'resolved to' path above does not match the real directory on your machine, "
            "that confirms the argument was corrupted before this script ran."
        )


def verify_ts_labels_path_exists(labels_path: Path) -> None:
    """Preflight gate for the OTHER external file dependency build_pilot()
    has besides the GRIB raw root: the real ts_labels.csv label archive.
    join_mod.build_pilot() itself does not raise on a missing labels file --
    it returns a {"status": "BLOCKED", ...} dict, which previously only
    surfaced deep inside build_one_group() as a generic "did not report
    BUILT_FROM_ACTUAL_FILES" failure. This check fails fast, at the same
    point and in the same style as verify_raw_root_exists(), and never
    guesses, copies, or substitutes a different label file -- if the
    resolved path doesn't exist, the build stops and says exactly which
    path it checked, so the real archive's location can be passed in via
    --ts-labels instead of being duplicated."""
    resolved = labels_path.resolve()
    print(f"[phase_0_4_20] --ts-labels argument received : {labels_path!r}", file=sys.stderr)
    print(f"[phase_0_4_20] --ts-labels resolved to        : {resolved}", file=sys.stderr)
    if not labels_path.exists() or not labels_path.is_file():
        raise BuildFailure(
            f"TS_LABELS.CSV NOT FOUND: {labels_path!r} (resolved: {resolved}) does not exist "
            "on disk, or is not a file. This script never generates, copies, or substitutes "
            "label data -- it only reads the real archive from wherever it actually is. If "
            "the canonical ts_labels.csv currently lives somewhere other than this default "
            "path (for example, inside a dated export/delivery bundle that was never merged "
            "into the repository's canonical processed/labels/ directory), pass its real "
            "location explicitly: --ts-labels \"<path to the existing ts_labels.csv>\". Do "
            "NOT copy or move the file to make this check pass -- point this flag at wherever "
            "it already is."
        )


def verify_files_present(groups: list[dict], raw_root: Path) -> None:
    """FAILS LOUDLY (naming the exact filename and event_group_key) if any
    required GRIB file is missing. Never substitutes a different cycle,
    never skips the group and continues -- per the Phase 0.4.20 brief,
    a missing file aborts the whole build."""
    missing = []
    for g in groups:
        for lead in g["leads"]:
            p = raw_root / join_mod.grib_filename_for(g["cycle"], lead)
            if not p.exists():
                missing.append((g["event_group_key"], p.name))
    if missing:
        detail = "; ".join(f"{fname} (event_group_key={egk})" for egk, fname in missing)
        raise BuildFailure(
            f"MISSING SOURCE FILE(S), refusing to build (no substitution, no partial dataset): {detail}"
        )


def verify_grib2_integrity(groups: list[dict], raw_root: Path) -> None:
    """Cheap header/trailer check before handing files to build_pilot() --
    catches an obviously truncated/corrupt file (e.g. a renamed .part)
    with a clear error rather than a confusing downstream eccodes
    exception."""
    bad = []
    for g in groups:
        for lead in g["leads"]:
            p = raw_root / join_mod.grib_filename_for(g["cycle"], lead)
            with open(p, "rb") as f:
                head = f.read(4)
                f.seek(-4, 2)
                tail = f.read(4)
            if head != b"GRIB" or tail != b"7777":
                bad.append(p.name)
    if bad:
        raise BuildFailure(f"GRIB2 integrity check failed (bad header/trailer) for: {bad}")


def build_one_group(group: dict, labels_path: Path, tmp_dir: Path) -> pd.DataFrame:
    out_csv = tmp_dir / f"{group['cycle']}.csv"
    out_manifest = tmp_dir / f"{group['cycle']}_manifest.json"
    result = join_mod.build_pilot(group["cycle"], group["leads"], out_csv, out_manifest,
                                   ts_labels_path=labels_path)
    if result.get("status") != "BUILT_FROM_ACTUAL_FILES":
        raise BuildFailure(
            f"build_pilot() did not report BUILT_FROM_ACTUAL_FILES for cycle {group['cycle']} "
            f"(event_group_key {group['event_group_key']}): {result}"
        )
    df = pd.read_csv(out_csv)
    df["source_cycle"] = group["cycle"]
    df["candidate_event_group_key"] = group["event_group_key"]
    df["candidate_partition"] = group["csv_partition"]
    df["candidate_label"] = group["csv_label"]
    return df


def validate_labels(combined: pd.DataFrame) -> None:
    """Requirement 6/11I: an UNKNOWN or UNMATCHED label (or any value
    outside the real archive's three defined statuses) must FAIL
    validation, never be silently treated as negative. This batch was
    selected (Phase 0.4.17) to only contain POSITIVE/NEGATIVE_CONFIRMED
    event groups, so anything else here means the real label archive has
    since diverged from what the candidate manifest expected."""
    statuses = set(combined["label_status"].unique())
    unexpected = statuses - ALLOWED_LABEL_STATUSES
    if unexpected:
        raise BuildFailure(
            f"unexpected label_status value(s) found: {sorted(unexpected)} -- refusing to "
            f"silently coerce any of these to negative"
        )
    non_decided = combined[combined["label_status"].isin(["UNKNOWN", "UNMATCHED"])] if "UNMATCHED" in combined["label_status"].unique() or "UNKNOWN" in combined["label_status"].unique() else combined.iloc[0:0]
    if len(non_decided):
        bad = non_decided[["source_file", "event_group_key", "label_status"]].to_dict("records")
        raise BuildFailure(
            f"this curated batch was selected to contain only POSITIVE/NEGATIVE_CONFIRMED "
            f"labels, but found UNKNOWN/UNMATCHED rows: {bad} -- the real label archive has "
            f"diverged from the candidate manifest's expectation; refusing to build"
        )
    # cross-check against the candidate manifest's own expected label too
    mismatches = combined[combined["label_status"] != combined["candidate_label"]]
    if len(mismatches):
        bad = mismatches[["source_file", "event_group_key", "label_status", "candidate_label"]].to_dict("records")
        raise BuildFailure(f"label mismatch vs candidate manifest expectation: {bad}")


def validate_event_group_keys(combined: pd.DataFrame, expected_groups: list[dict]) -> None:
    """Requirements 11D/G/H: every row's recomputed event_group_key must
    match the candidate manifest's own column for that row (D), no
    event_group_key appears under more than one distinct partition (G,
    reusing the Phase 0.4.12 split-safety check), and every event group
    has exactly its 2 leads (f003+f006) correctly attached to it (H)."""
    mismatches = combined[combined["event_group_key"] != combined["candidate_event_group_key"]]
    if len(mismatches):
        bad = mismatches[["source_file", "event_group_key", "candidate_event_group_key"]].to_dict("records")
        raise BuildFailure(f"recomputed event_group_key disagrees with candidate manifest: {bad}")

    split_check = validate_no_event_group_key_crosses_partitions(
        combined["event_group_key"].tolist(), combined["train_holdout_partition"].tolist()
    )
    if not split_check.ok:
        raise BuildFailure(f"event_group_key crosses partitions: {split_check.to_json()}")

    expected_leads = {g["event_group_key"]: set(g["leads"]) for g in expected_groups}
    for egk, grp in combined.groupby("event_group_key"):
        actual_leads = set(grp["forecast_lead_hours"].tolist())
        if actual_leads != expected_leads.get(egk):
            raise BuildFailure(
                f"event_group_key {egk}: expected leads {expected_leads.get(egk)}, got {actual_leads}"
            )

    dup_key = combined[["source_file", "cell_id", "forecast_lead_hours"]].apply(tuple, axis=1)
    n_dup = len(combined) - dup_key.nunique()
    if n_dup:
        raise BuildFailure(f"{n_dup} duplicate (source_file, cell_id, forecast_lead_hours) rows found")


def validate_holdout_contamination(combined: pd.DataFrame, requested_partition: str) -> dict:
    """Requirements 2/3/11E/11F: the structural check that proves (not
    just asserts) the output dataset contains zero rows from the wrong
    partition. train_holdout_partition here is RECOMPUTED by
    assign_partition() on each row's own ist_date -- not copied from the
    candidate manifest -- so this is an independent verification, not a
    repeat of the same check."""
    counts = combined["train_holdout_partition"].value_counts().to_dict()
    n_train = int(counts.get(TRAIN, 0))
    n_holdout = int(counts.get(HOLDOUT, 0))
    if requested_partition == TRAIN and n_holdout > 0:
        bad = combined[combined["train_holdout_partition"] == HOLDOUT][["source_file", "event_group_key"]].to_dict("records")
        raise BuildFailure(f"TRAIN build contains {n_holdout} HOLDOUT row(s), refusing to write output: {bad}")
    if requested_partition == HOLDOUT and n_train > 0:
        bad = combined[combined["train_holdout_partition"] == TRAIN][["source_file", "event_group_key"]].to_dict("records")
        raise BuildFailure(f"HOLDOUT build contains {n_train} TRAIN row(s), refusing to write output: {bad}")
    return {"n_train_rows": n_train, "n_holdout_rows": n_holdout}


def validate_leakage(combined: pd.DataFrame) -> dict:
    """Requirement 8: every predictor column must be classified
    FORECAST_DERIVED in the existing, reused leakage table, and no
    label/target field may appear among the predictor columns."""
    leakage_table = join_mod.FEATURE_LEAKAGE_CLASS
    bad_class = {c: leakage_table.get(c) for c in FEATURE_COLUMNS if leakage_table.get(c) != "FORECAST_DERIVED"}
    if bad_class:
        raise BuildFailure(f"non-FORECAST_DERIVED predictor column(s) found: {bad_class}")
    leaking = set(FEATURE_COLUMNS) & set(LABEL_FIELDS)
    if leaking:
        raise BuildFailure(f"label field(s) found inside the predictor column list: {leaking}")
    for col in combined.columns:
        if col in LABEL_FIELDS:
            continue
        if "label" in col.lower() and col not in (
            "label_source", "label_timestamp", "label_definition", "candidate_label"
        ):
            raise BuildFailure(f"unexpected label-like column name found outside the known label fields: {col}")
    return {"predictor_columns_checked": len(FEATURE_COLUMNS), "all_forecast_derived": True}


def reconcile_row_counts(combined: pd.DataFrame, groups: list[dict]) -> None:
    """Requirement 11N: the final row count must exactly equal 2 rows
    (f003+f006) per selected event group -- no more, no fewer."""
    expected_rows = sum(len(g["leads"]) for g in groups)
    if len(combined) != expected_rows:
        raise BuildFailure(
            f"row count does not reconcile with the manifest: expected {expected_rows} "
            f"(sum of leads per selected event group), got {len(combined)}"
        )


def load_provenance(provenance_path: Path) -> dict:
    if not provenance_path.exists():
        return {}
    try:
        entries = json.loads(provenance_path.read_text())
    except Exception:  # noqa: BLE001
        return {}
    return {e["file_name"]: e for e in entries}


def run_build(candidate_csv: Path, provenance_path: Path, raw_root: Path, labels_path: Path,
              partition: str, out_dir: Path, tmp_dir: Path) -> dict:
    groups_all = load_event_groups(candidate_csv)
    provenance = load_provenance(provenance_path)
    verify_partition_agreement(groups_all, provenance)

    groups = filter_by_partition(groups_all, partition)
    if not groups:
        raise BuildFailure(f"no event groups matched --partition {partition!r} -- nothing to build")

    verify_raw_root_exists(raw_root)
    verify_ts_labels_path_exists(labels_path)
    verify_files_present(groups, raw_root)
    verify_grib2_integrity(groups, raw_root)

    # Build with RAW_DIR pointed at raw_root for the duration of this
    # process only -- never mutates the repository's own default, never
    # writes a D:\ path into any file this script produces (every
    # provenance/manifest entry below records bare filenames only, same
    # as the Phase 0.4.19 acquisition provenance).
    original_raw_dir = join_mod.RAW_DIR
    join_mod.RAW_DIR = Path(raw_root)
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        frames = [build_one_group(g, labels_path, tmp_dir) for g in groups]
    finally:
        join_mod.RAW_DIR = original_raw_dir

    combined = pd.concat(frames, ignore_index=True)
    combined["train_holdout_partition"] = combined["ist_date"].apply(assign_partition)

    validate_labels(combined)
    validate_event_group_keys(combined, groups)
    contamination = validate_holdout_contamination(combined, partition)
    leakage = validate_leakage(combined)
    reconcile_row_counts(combined, groups)

    missingness = {}
    for col in FEATURE_COLUMNS:
        n_missing = int(combined[col].isna().sum()) if col in combined.columns else len(combined)
        missingness[col] = {"n_missing": n_missing, "n_total": len(combined),
                             "pct_missing": round(100.0 * n_missing / len(combined), 2) if len(combined) else None}

    prate_provenance_counts = combined["gfs_prate_stepType_used"].value_counts(dropna=False).to_dict()
    interval_available = int(combined["gfs_precip_3h_interval_mm"].notna().sum())
    interval_missing = int(combined["gfs_precip_3h_interval_mm"].isna().sum())

    n_positive = int((combined["label"] == 1).sum())
    n_negative = int((combined["label"] == 0).sum())
    n_unknown = int(combined["label_status"].eq("UNKNOWN").sum())

    source_files = []
    for g in groups:
        for lead in g["leads"]:
            fname = join_mod.grib_filename_for(g["cycle"], lead)
            prov = provenance.get(fname, {})
            source_files.append({
                "file_name": fname, "event_group_key": g["event_group_key"],
                "sha256": prov.get("sha256"), "size_bytes": prov.get("size_bytes"),
            })

    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / f"phase_0_4_20_{partition}.csv"
    out_manifest_path = out_dir / f"phase_0_4_20_{partition}_manifest.json"
    combined.to_csv(out_csv, index=False)

    report = {
        "builder_version": BUILDER_VERSION,
        "contract_doc": DATASET_CONTRACT_DOC,
        "generation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "requested_partition": partition,
        "raw_root": str(raw_root),
        "candidate_manifest": str(candidate_csv),
        "provenance_manifest": str(provenance_path),
        "label_archive_source": str(labels_path),
        "n_event_groups": len(groups),
        "n_rows": len(combined),
        "n_positive": n_positive,
        "n_negative": n_negative,
        "n_unknown": n_unknown,
        "n_train_rows": contamination["n_train_rows"],
        "n_holdout_rows": contamination["n_holdout_rows"],
        "feature_missingness": missingness,
        "precipitation_provenance": {
            "stepType_counts": {str(k): int(v) for k, v in prate_provenance_counts.items()},
            "interval_precip_available_rows": interval_available,
            "interval_precip_missing_rows": interval_missing,
        },
        "source_files": source_files,
        "leakage_check": leakage,
        "validation_status": "GREEN",
    }
    out_manifest_path.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    print(f"\nWrote {out_csv} ({len(combined)} rows, {len(groups)} event groups)")
    print(f"Wrote {out_manifest_path}")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--partition", required=True, choices=["train", "holdout", "all"],
                     help="REQUIRED. 'train' and 'holdout' structurally exclude the other "
                          "partition's files before any GRIB file is opened. 'all' is for "
                          "inspection/export only and must NEVER be used as model training input.")
    ap.add_argument("--candidate-manifest", default=str(CANDIDATE_MANIFEST_CSV))
    ap.add_argument("--provenance-manifest", default=str(PROVENANCE_MANIFEST))
    ap.add_argument("--raw-root", default=str(join_mod.RAW_DIR),
                     help=f"Directory the acquired GRIB2 files live in (default: {join_mod.RAW_DIR}). "
                          "Point this at a relocated drive (e.g. D:\\SIH-Historical-GFS\\raw) -- "
                          "never hardcoded here, always passed explicitly or left at the default.")
    ap.add_argument("--ts-labels", default=str(join_mod.TS_LABELS_PATH))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--tmp-dir", default=None)
    args = ap.parse_args(argv)

    if args.partition == "all":
        print("WARNING: --partition all includes HOLDOUT rows. This output must NEVER be used "
              "to train or tune a model -- it exists for inspection/export only.", file=sys.stderr)

    tmp_dir = Path(args.tmp_dir) if args.tmp_dir else Path(args.out_dir) / "_phase_0_4_20_per_cycle"

    try:
        run_build(
            candidate_csv=Path(args.candidate_manifest),
            provenance_path=Path(args.provenance_manifest),
            raw_root=Path(args.raw_root),
            labels_path=Path(args.ts_labels),
            partition=args.partition,
            out_dir=Path(args.out_dir),
            tmp_dir=tmp_dir,
        )
    except BuildFailure as e:
        print(f"\nBUILD FAILED: {e}", file=sys.stderr)
        print("PHASE_0_4_20_BUILD = RED", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
