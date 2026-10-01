#!/usr/bin/env python3
"""
verify_phase_0_4_15_oversized_stage_b.py -- Phase 0.4.15 LOCAL, IN-PLACE
verification of the 6 Stage B GFS files that exceed this session's 400 MB
cloud-staging limit (Phase 0.4.14 Part C blocker).

Run this directly on the machine that holds the real files -- it never
copies, stages, or moves anything. It opens each file exactly where it
sits under data/external/historical_gfs/raw/ and reads it with eccodes,
streaming -- no file is ever loaded whole into memory.

Reuses, rather than reimplements, the repository's own semantics:
  - enumerate_messages() / find_message()  (GRIB message-level reads)
  - ist_slot_for()                         (6-hour IST slot assignment)
  - event_group_key_for()                  (Phase 0.4.12 grouping key)
  - parse_grib_dt()                        (GRIB dataDate/dataTime -> datetime)
  - sha256_of()                            (streaming SHA256)
  - WANTED_FIELDS                          (the required-field list)
all imported from scripts/build_vobl_historical_gfs_ts_join.py, plus
assign_partition() from scripts/historical_dataset_split.py. Nothing here
re-derives GRIB semantics from scratch.

Usage (from the repository root):

    python scripts/verify_phase_0_4_15_oversized_stage_b.py

Optional:

    python scripts/verify_phase_0_4_15_oversized_stage_b.py \
        --raw-dir data/external/historical_gfs/raw \
        --manifest data/external/historical_gfs/manifest.json \
        --labels SIH_PANINDIA_GRID_LABELS_20260930_143541Z/processed/labels/ts_labels.csv \
        --json-out /tmp/phase_0_4_15_results.json

Exit code 0 if every file passes every check; 1 otherwise. Never asserts
GREEN by assumption -- every field is independently read from the file in
hand and compared against the expected value; a mismatch is a hard
failure, not a warning.

Does NOT build any dataset row, does NOT compute gfs_prate_kg_m2_s or
gfs_precip_3h_interval_mm, does NOT modify any file it reads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_vobl_historical_gfs_ts_join as join_mod  # noqa: E402
from historical_dataset_split import assign_partition, TRAIN  # noqa: E402

CELL_ID = "IND_13.0_78.0"

# Expected grid, per Phase 0.4.15 Part 2 -- never assumed, only compared.
EXPECTED_GRID = {
    "gridType": "regular_ll",
    "Ni": 1440,
    "Nj": 721,
    "lat_first": 90.0,
    "lat_last": -90.0,
    "lon_first": 0.0,
    "lon_last": 359.75,
    "jScansPositively": 0,
}

# The 6 oversized Stage B files and their Phase 0.4.13/0.4.14-derived
# expectations. The script independently recomputes every one of these
# fields from GRIB metadata -- this table is the CHECK TARGET, not a
# shortcut; a disagreement is reported as a failure, not silently accepted.
TARGET_FILES = [
    {
        "filename": "gfs.0p25.2021072406.f003.grib2",
        "expected_init_utc": "2021-07-24T06:00:00+00:00",
        "expected_lead_hours": 3,
        "expected_valid_utc": "2021-07-24T09:00:00+00:00",
        "expected_ist_date": "2021-07-24",
        "expected_slot_id": 2,
        "expected_event_group_key": f"{CELL_ID}|2021-07-24|2",
        "label_date": "2021-07-24", "label_slot_label": "1201-1800", "expected_label_status": "POSITIVE",
    },
    {
        "filename": "gfs.0p25.2021072406.f006.grib2",
        "expected_init_utc": "2021-07-24T06:00:00+00:00",
        "expected_lead_hours": 6,
        "expected_valid_utc": "2021-07-24T12:00:00+00:00",
        "expected_ist_date": "2021-07-24",
        "expected_slot_id": 2,
        "expected_event_group_key": f"{CELL_ID}|2021-07-24|2",
        "label_date": "2021-07-24", "label_slot_label": "1201-1800", "expected_label_status": "POSITIVE",
    },
    {
        "filename": "gfs.0p25.2022081512.f003.grib2",
        "expected_init_utc": "2022-08-15T12:00:00+00:00",
        "expected_lead_hours": 3,
        "expected_valid_utc": "2022-08-15T15:00:00+00:00",
        "expected_ist_date": "2022-08-15",
        "expected_slot_id": 3,
        "expected_event_group_key": f"{CELL_ID}|2022-08-15|3",
        "label_date": "2022-08-15", "label_slot_label": "1801-2400", "expected_label_status": "NEGATIVE_CONFIRMED",
    },
    {
        "filename": "gfs.0p25.2022081512.f006.grib2",
        "expected_init_utc": "2022-08-15T12:00:00+00:00",
        "expected_lead_hours": 6,
        "expected_valid_utc": "2022-08-15T18:00:00+00:00",
        "expected_ist_date": "2022-08-15",
        "expected_slot_id": 3,
        "expected_event_group_key": f"{CELL_ID}|2022-08-15|3",
        "label_date": "2022-08-15", "label_slot_label": "1801-2400", "expected_label_status": "NEGATIVE_CONFIRMED",
    },
    {
        "filename": "gfs.0p25.2023110612.f003.grib2",
        "expected_init_utc": "2023-11-06T12:00:00+00:00",
        "expected_lead_hours": 3,
        "expected_valid_utc": "2023-11-06T15:00:00+00:00",
        "expected_ist_date": "2023-11-06",
        "expected_slot_id": 3,
        "expected_event_group_key": f"{CELL_ID}|2023-11-06|3",
        "label_date": "2023-11-06", "label_slot_label": "1801-2400", "expected_label_status": "POSITIVE",
    },
    {
        "filename": "gfs.0p25.2023110612.f006.grib2",
        "expected_init_utc": "2023-11-06T12:00:00+00:00",
        "expected_lead_hours": 6,
        "expected_valid_utc": "2023-11-06T18:00:00+00:00",
        "expected_ist_date": "2023-11-06",
        "expected_slot_id": 3,
        "expected_event_group_key": f"{CELL_ID}|2023-11-06|3",
        "label_date": "2023-11-06", "label_slot_label": "1801-2400", "expected_label_status": "POSITIVE",
    },
]

EXISTING_PILOT_KEYS = {
    f"{CELL_ID}|2015-03-03|1",
    f"{CELL_ID}|2015-03-03|2",
    f"{CELL_ID}|2020-07-15|1",
}


def load_manifest(manifest_path: Path) -> dict:
    """file_name -> manifest entry"""
    if not manifest_path.exists():
        return {}
    entries = json.loads(manifest_path.read_text())
    return {e["file_name"]: e for e in entries}


def load_label_row(labels_path: Path, date: str, slot_label: str) -> dict | None:
    import csv
    timestamp = f"{date}T{slot_label}"
    with open(labels_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row["timestamp"] == timestamp and row["cell_id"] == CELL_ID
                    and row["hazard"] == "ts"):
                return row
    return None


def verify_one_file(path: Path, expected: dict, manifest: dict) -> dict:
    result = {"filename": path.name, "failures": [], "warnings": []}

    # --- 1. File integrity ---
    if not path.exists():
        result["failures"].append("file does not exist")
        return result
    size_bytes = path.stat().st_size
    result["size_bytes"] = size_bytes
    result["non_empty"] = size_bytes > 0
    if size_bytes == 0:
        result["failures"].append("file is empty")
        return result

    sha256 = join_mod.sha256_of(path)
    result["sha256"] = sha256

    manifest_entry = manifest.get(path.name)
    if manifest_entry is None:
        result["failures"].append("no manifest.json entry found for this filename")
    else:
        result["manifest_sha256"] = manifest_entry.get("sha256")
        result["manifest_size_bytes"] = manifest_entry.get("size_bytes")
        if manifest_entry.get("sha256") != sha256:
            result["failures"].append(
                f"SHA256 mismatch: computed {sha256} != manifest {manifest_entry.get('sha256')}"
            )
        if manifest_entry.get("size_bytes") != size_bytes:
            result["failures"].append(
                f"size mismatch: computed {size_bytes} != manifest {manifest_entry.get('size_bytes')}"
            )

    # --- 2. GRIB2 integrity + grid metadata ---
    with open(path, "rb") as f:
        head = f.read(4)
        f.seek(-4, 2)
        tail = f.read(4)
    result["grib_header_ok"] = (head == b"GRIB")
    result["grib_trailer_ok"] = (tail == b"7777")
    if head != b"GRIB":
        result["failures"].append(f"GRIB header missing/wrong: got {head!r}")
    if tail != b"7777":
        result["failures"].append(f"GRIB 7777 trailer missing/wrong: got {tail!r}")

    try:
        msgs = join_mod.enumerate_messages(path)
    except Exception as e:
        result["failures"].append(f"eccodes message enumeration raised: {e}")
        return result

    result["n_messages"] = len(msgs)
    if len(msgs) == 0:
        result["failures"].append("zero GRIB messages enumerated")
        return result

    import eccodes as ec
    gid0 = msgs[0]["gid"]
    try:
        grid = {
            "gridType": ec.codes_get(gid0, "gridType"),
            "Ni": ec.codes_get(gid0, "Ni"),
            "Nj": ec.codes_get(gid0, "Nj"),
            "lat_first": ec.codes_get(gid0, "latitudeOfFirstGridPointInDegrees"),
            "lat_last": ec.codes_get(gid0, "latitudeOfLastGridPointInDegrees"),
            "lon_first": ec.codes_get(gid0, "longitudeOfFirstGridPointInDegrees"),
            "lon_last": ec.codes_get(gid0, "longitudeOfLastGridPointInDegrees"),
            "jScansPositively": ec.codes_get(gid0, "jScansPositively"),
        }
    except Exception as e:
        result["failures"].append(f"could not read grid metadata: {e}")
        grid = {}
    result["grid"] = grid
    for key, expected_val in EXPECTED_GRID.items():
        actual_val = grid.get(key)
        if actual_val != expected_val:
            result["failures"].append(
                f"grid.{key} mismatch: expected {expected_val}, got {actual_val}"
            )

    # --- 3. Init time + forecast lead (from GRIB, not filename) ---
    init_dt = join_mod.parse_grib_dt(msgs[0]["dataDate"], msgs[0]["dataTime"])
    result["init_dt_utc"] = init_dt.isoformat()

    cape_msg = join_mod.find_message(msgs, "cape", "surface", 0)
    if cape_msg is None:
        result["failures"].append("could not find a cape@surface/0 message to read forecast lead from")
        lead_hours = None
    else:
        lead_hours = cape_msg["endStep"]
    result["lead_hours_from_grib"] = lead_hours

    if lead_hours is not None:
        valid_dt = init_dt + timedelta(hours=lead_hours)
        valid_ist = valid_dt.astimezone(join_mod.IST)
        slot_id, slot_label = join_mod.ist_slot_for(valid_ist)
        ist_date = valid_ist.strftime("%Y-%m-%d")
        egk = join_mod.event_group_key_for(CELL_ID, ist_date, slot_id)

        result["valid_dt_utc"] = valid_dt.isoformat()
        result["valid_dt_ist"] = valid_ist.isoformat()
        result["ist_date"] = ist_date
        result["slot_id"] = slot_id
        result["slot_label"] = slot_label
        result["event_group_key"] = egk

        # --- 4. Compare against expected targets ---
        if init_dt.isoformat() != expected["expected_init_utc"]:
            result["failures"].append(
                f"init mismatch: expected {expected['expected_init_utc']}, got {init_dt.isoformat()}"
            )
        if lead_hours != expected["expected_lead_hours"]:
            result["failures"].append(
                f"lead mismatch: expected {expected['expected_lead_hours']}, got {lead_hours}"
            )
        if valid_dt.isoformat() != expected["expected_valid_utc"]:
            result["failures"].append(
                f"valid_time mismatch: expected {expected['expected_valid_utc']}, got {valid_dt.isoformat()}"
            )
        if ist_date != expected["expected_ist_date"]:
            result["failures"].append(
                f"IST date mismatch: expected {expected['expected_ist_date']}, got {ist_date}"
            )
        if slot_id != expected["expected_slot_id"]:
            result["failures"].append(
                f"slot mismatch: expected {expected['expected_slot_id']}, got {slot_id}"
            )
        if egk != expected["expected_event_group_key"]:
            result["failures"].append(
                f"event_group_key mismatch: expected {expected['expected_event_group_key']}, got {egk}"
            )
        if egk in EXISTING_PILOT_KEYS:
            result["failures"].append(f"event_group_key {egk} collides with an existing pilot key")

    # --- 5. Required field presence ---
    field_presence = {}
    for shortName, typeOfLevel, level in join_mod.WANTED_FIELDS:
        m = join_mod.find_message(msgs, shortName, typeOfLevel, level)
        key = f"{shortName}@{typeOfLevel}/{level}"
        field_presence[key] = m is not None
        if m is None:
            result["failures"].append(f"missing required field: {key}")
    result["field_presence"] = field_presence

    # --- 6. Precipitation semantics (reporting only) ---
    def msg_summary(m):
        return {k: m[k] for k in (
            "shortName", "typeOfLevel", "level", "stepType",
            "startStep", "endStep", "forecastTime", "units")}

    tp_msgs = [m for m in msgs if m["shortName"] == "tp"]
    prate_msgs = [m for m in msgs if m["shortName"] == "prate"]
    result["tp_messages"] = [msg_summary(m) for m in tp_msgs]
    result["prate_messages"] = [msg_summary(m) for m in prate_msgs]
    if not tp_msgs:
        result["warnings"].append("no tp message found in this file")
    if not prate_msgs:
        result["warnings"].append("no prate message found in this file")

    for m in msgs:
        ec.codes_release(m["gid"])

    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", default=str(REPO_ROOT / "data" / "external" / "historical_gfs" / "raw"))
    ap.add_argument("--manifest", default=str(REPO_ROOT / "data" / "external" / "historical_gfs" / "manifest.json"))
    ap.add_argument("--labels", default=str(
        REPO_ROOT / "SIH_PANINDIA_GRID_LABELS_20260930_143541Z" / "processed" / "labels" / "ts_labels.csv"))
    ap.add_argument("--json-out", default=None, help="optional path to dump full JSON results")
    ap.add_argument("--files", nargs="*", default=None,
                    help="optional subset of filenames to check (default: all 6 oversized Stage B files)")
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    manifest_path = Path(args.manifest)
    labels_path = Path(args.labels)

    manifest = load_manifest(manifest_path)
    targets = TARGET_FILES
    if args.files:
        targets = [t for t in TARGET_FILES if t["filename"] in args.files]

    all_results = []
    all_pass = True
    seen_sha256 = {}

    for expected in targets:
        path = raw_dir / expected["filename"]
        print(f"\n=== {expected['filename']} ===")
        res = verify_one_file(path, expected, manifest)

        # label cross-check
        label_row = load_label_row(labels_path, expected["label_date"], expected["label_slot_label"])
        if label_row is None:
            res["failures"].append(
                f"no label row found for {expected['label_date']}T{expected['label_slot_label']} "
                f"cell={CELL_ID} hazard=ts"
            )
        else:
            res["label_status"] = label_row["label_status"]
            res["label_source"] = label_row["source"]
            if label_row["label_status"] != expected["expected_label_status"]:
                res["failures"].append(
                    f"label_status mismatch: expected {expected['expected_label_status']}, "
                    f"got {label_row['label_status']}"
                )
            if "IMD station observation, VOBL/43295" not in label_row["source"]:
                res["failures"].append(f"unexpected label source: {label_row['source']}")

        # year split
        partition = assign_partition(expected["expected_ist_date"])
        res["partition"] = partition
        if partition != TRAIN:
            res["failures"].append(f"partition mismatch: expected train, got {partition}")

        # duplicate SHA256 check (within this run + against the file list)
        sha = res.get("sha256")
        if sha:
            if sha in seen_sha256:
                res["failures"].append(
                    f"SHA256 {sha} duplicates {seen_sha256[sha]} -- possible duplicate/corrupt download"
                )
            else:
                seen_sha256[sha] = expected["filename"]

        status = "PASS" if not res["failures"] else "FAIL"
        print(f"  status: {status}")
        if res["failures"]:
            for f in res["failures"]:
                print(f"  FAIL: {f}")
        for w in res.get("warnings", []):
            print(f"  WARN: {w}")

        all_results.append(res)
        if res["failures"]:
            all_pass = False

    gate = "GREEN" if all_pass else "RED"
    print(f"\nOVERSIZED_STAGE_B_LOCAL_VERIFICATION = {gate}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(all_results, indent=2, default=str))
        print(f"Full JSON results written to {args.json_out}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
