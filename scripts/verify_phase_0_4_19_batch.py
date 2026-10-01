#!/usr/bin/env python3
"""
verify_phase_0_4_19_batch.py -- Phase 0.4.19 manifest-driven verification
of the acquired Phase 0.4.18 candidate batch.

Generalizes the per-file check logic already proven in Phase 0.4.15's
verify_phase_0_4_15_oversized_stage_b.py (grid metadata, GRIB2 integrity,
init/lead/valid-time/IST-slot/event_group_key recomputation, required
field presence, tp/prate metadata, label cross-check, partition check,
duplicate-SHA256 detection) -- but reads its list of expected targets from
docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv instead of a hardcoded Python
list, since this batch's 20 event groups (40 files) are too many to
hand-maintain as a literal table the way Phase 0.4.15's 6-file list was.

Every "expected" value is still independently RECOMPUTED from the real
label archive and the repository's own ist_slot_for()/event_group_key_for()
functions -- the manifest CSV's own columns are treated as a plan to
check against, not as ground truth to assume. A GRIB file whose actual
init/lead/valid-time disagrees with what the manifest *intended* to
acquire is reported as a failure, not silently accepted.

Run this on the machine that holds the real downloaded files -- it never
copies, stages, or moves anything, and it streams each file through
eccodes rather than loading it whole into memory.

Usage (from the repository root):

    python scripts/verify_phase_0_4_19_batch.py
    python scripts/verify_phase_0_4_19_batch.py --json-out /tmp/phase_0_4_19_results.json
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_vobl_historical_gfs_ts_join as join_mod  # noqa: E402
from historical_dataset_split import assign_partition, TRAIN, HOLDOUT  # noqa: E402

CELL_ID = "IND_13.0_78.0"
CANDIDATE_MANIFEST_CSV = REPO_ROOT / "docs" / "PHASE_0_4_17_ACQUISITION_MANIFEST.csv"
RAW_DIR = REPO_ROOT / "data" / "external" / "historical_gfs" / "raw"
PROVENANCE_MANIFEST = REPO_ROOT / "data" / "external" / "historical_gfs" / "manifest.json"
LABELS_PATH = REPO_ROOT / "SIH_PANINDIA_GRID_LABELS_20260930_143541Z" / "processed" / "labels" / "ts_labels.csv"

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

# Event groups already acquired in Phase 0.4.16 -- a 0.4.19-batch file must
# never collide with one of these.
PRIOR_EVENT_GROUP_KEYS = {
    f"{CELL_ID}|2015-03-03|1", f"{CELL_ID}|2015-03-03|2",
    f"{CELL_ID}|2020-07-15|1",
    f"{CELL_ID}|2016-01-15|1", f"{CELL_ID}|2017-04-16|2",
    f"{CELL_ID}|2019-12-10|0", f"{CELL_ID}|2021-07-24|2",
    f"{CELL_ID}|2022-08-15|3", f"{CELL_ID}|2023-11-06|3",
}


def load_expected_targets(manifest_csv: Path) -> list[dict]:
    """Reads the candidate CSV and, for each row, independently recomputes
    the expected init/lead/valid/IST/slot/event_group_key/partition from
    the manifest's own (target_ist_date, target_slot, selected_gfs_cycle,
    selected_lead) columns using the SAME functions production research
    tooling already uses -- it does not just copy the CSV's
    target_valid_time/event_group_key columns through unchecked."""
    targets = []
    with open(manifest_csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("selected_gfs_cycle") == "AMBIGUOUS":
                continue
            cycle = row["selected_gfs_cycle"]
            lead_hours = int(row["selected_lead"].lstrip("f"))
            init_dt = join_mod.parse_grib_dt(int(cycle[:8]), int(cycle[8:10]) * 100)
            valid_dt = init_dt + timedelta(hours=lead_hours)
            valid_ist = valid_dt.astimezone(join_mod.IST)
            slot_id, _ = join_mod.ist_slot_for(valid_ist)
            ist_date = valid_ist.strftime("%Y-%m-%d")
            egk = join_mod.event_group_key_for(CELL_ID, ist_date, slot_id)
            targets.append({
                "filename": row["expected_file"],
                "cycle": cycle,
                "lead_hours": lead_hours,
                "expected_init_utc": init_dt.isoformat(),
                "expected_valid_utc": valid_dt.isoformat(),
                "expected_ist_date": ist_date,
                "expected_slot_id": slot_id,
                "expected_event_group_key": egk,
                "csv_event_group_key": row["event_group_key"],
                "csv_label": row["label"],
                "csv_partition": row["partition"],
            })
    return targets


def load_label_row(labels_path: Path, date: str, slot_id: int):
    import build_vobl_historical_gfs_ts_join as jm
    slot_label = jm.SLOT_LABEL_STRING[slot_id]
    timestamp = f"{date}T{slot_label}"
    with open(labels_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row["timestamp"] == timestamp and row["cell_id"] == CELL_ID
                    and row["hazard"] == "ts"):
                return row
    return None


def verify_one_file(path: Path, expected: dict, provenance: dict) -> dict:
    result = {"filename": path.name, "failures": [], "warnings": []}

    if not path.exists():
        result["status"] = "FILE_NOT_FOUND"
        result["failures"].append("file does not exist (not yet acquired)")
        return result

    size_bytes = path.stat().st_size
    result["size_bytes"] = size_bytes
    if size_bytes == 0:
        result["failures"].append("file is empty")
        return result

    sha256 = join_mod.sha256_of(path)
    result["sha256"] = sha256

    prov_entry = provenance.get(path.name)
    if prov_entry is None:
        result["warnings"].append("no acquisition-provenance entry found for this filename")
    else:
        if prov_entry.get("sha256") != sha256:
            result["failures"].append(
                f"local SHA256 fingerprint mismatch vs recorded provenance: "
                f"{sha256} != {prov_entry.get('sha256')} (file changed since acquisition?)"
            )
        if prov_entry.get("size_bytes") != size_bytes:
            result["failures"].append(
                f"size mismatch vs recorded provenance: {size_bytes} != {prov_entry.get('size_bytes')}"
            )
        if prov_entry.get("partition") != expected["csv_partition"]:
            result["failures"].append(
                f"provenance partition ({prov_entry.get('partition')}) disagrees with the "
                f"candidate manifest ({expected['csv_partition']})"
            )

    with open(path, "rb") as f:
        head = f.read(4)
        f.seek(-4, 2)
        tail = f.read(4)
    if head != b"GRIB":
        result["failures"].append(f"GRIB header missing/wrong: got {head!r}")
    if tail != b"7777":
        result["failures"].append(f"GRIB 7777 trailer missing/wrong: got {tail!r}")

    try:
        msgs = join_mod.enumerate_messages(path)
    except Exception as e:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
        result["failures"].append(f"could not read grid metadata: {e}")
        grid = {}
    result["grid"] = grid
    for key, expected_val in EXPECTED_GRID.items():
        if grid.get(key) != expected_val:
            result["failures"].append(f"grid.{key} mismatch: expected {expected_val}, got {grid.get(key)}")

    init_dt = join_mod.parse_grib_dt(msgs[0]["dataDate"], msgs[0]["dataTime"])
    result["init_dt_utc"] = init_dt.isoformat()
    if init_dt.isoformat() != expected["expected_init_utc"]:
        result["failures"].append(
            f"init mismatch: expected {expected['expected_init_utc']}, got {init_dt.isoformat()}"
        )

    cape_msg = join_mod.find_message(msgs, "cape", "surface", 0)
    lead_hours = cape_msg["endStep"] if cape_msg is not None else None
    result["lead_hours_from_grib"] = lead_hours
    if cape_msg is None:
        result["failures"].append("could not find cape@surface/0 to read forecast lead from")
    elif lead_hours != expected["lead_hours"]:
        result["failures"].append(f"lead mismatch: expected {expected['lead_hours']}, got {lead_hours}")

    if lead_hours is not None:
        valid_dt = init_dt + timedelta(hours=lead_hours)
        valid_ist = valid_dt.astimezone(join_mod.IST)
        slot_id, slot_label = join_mod.ist_slot_for(valid_ist)
        ist_date = valid_ist.strftime("%Y-%m-%d")
        egk = join_mod.event_group_key_for(CELL_ID, ist_date, slot_id)
        result.update({
            "valid_dt_utc": valid_dt.isoformat(), "ist_date": ist_date,
            "slot_id": slot_id, "event_group_key": egk,
        })
        if valid_dt.isoformat() != expected["expected_valid_utc"]:
            result["failures"].append(
                f"valid_time mismatch: expected {expected['expected_valid_utc']}, got {valid_dt.isoformat()}"
            )
        if ist_date != expected["expected_ist_date"]:
            result["failures"].append(f"IST date mismatch: expected {expected['expected_ist_date']}, got {ist_date}")
        if slot_id != expected["expected_slot_id"]:
            result["failures"].append(f"slot mismatch: expected {expected['expected_slot_id']}, got {slot_id}")
        if egk != expected["expected_event_group_key"]:
            result["failures"].append(
                f"event_group_key mismatch: expected {expected['expected_event_group_key']}, got {egk}"
            )
        if egk != expected["csv_event_group_key"]:
            result["failures"].append(
                f"recomputed event_group_key ({egk}) disagrees with the candidate manifest's "
                f"own column ({expected['csv_event_group_key']})"
            )
        if egk in PRIOR_EVENT_GROUP_KEYS:
            result["failures"].append(f"event_group_key {egk} collides with an already-acquired Phase 0.4.16 event group")

        partition = assign_partition(ist_date)
        result["partition"] = partition
        if partition != expected["csv_partition"]:
            result["failures"].append(
                f"partition mismatch: recomputed {partition}, manifest says {expected['csv_partition']}"
            )

        label_row = load_label_row(LABELS_PATH, ist_date, slot_id)
        if label_row is None:
            result["failures"].append(f"no label row found for {ist_date} slot {slot_id}")
        else:
            result["label_status"] = label_row["label_status"]
            if label_row["label_status"] != expected["csv_label"]:
                result["failures"].append(
                    f"label_status mismatch: manifest says {expected['csv_label']}, "
                    f"actual label archive says {label_row['label_status']}"
                )

    field_presence = {}
    for shortName, typeOfLevel, level in join_mod.WANTED_FIELDS:
        m = join_mod.find_message(msgs, shortName, typeOfLevel, level)
        key = f"{shortName}@{typeOfLevel}/{level}"
        field_presence[key] = m is not None
        if m is None:
            result["failures"].append(f"missing required field: {key}")
    result["field_presence"] = field_presence

    tp_msgs = [m for m in msgs if m["shortName"] == "tp"]
    prate_msgs = [m for m in msgs if m["shortName"] == "prate"]
    result["tp_messages"] = [{k: m[k] for k in ("stepType", "startStep", "endStep")} for m in tp_msgs]
    result["prate_messages"] = [{k: m[k] for k in ("stepType", "startStep", "endStep")} for m in prate_msgs]
    if not tp_msgs:
        result["warnings"].append("no tp message found in this file")
    if not prate_msgs:
        result["warnings"].append("no prate message found in this file")

    for m in msgs:
        ec.codes_release(m["gid"])

    result["status"] = "PASS" if not result["failures"] else "FAIL"
    return result


def load_provenance(provenance_path: Path = PROVENANCE_MANIFEST) -> dict:
    provenance_path = Path(provenance_path)
    if not provenance_path.exists():
        return {}
    try:
        entries = json.loads(provenance_path.read_text())
    except Exception:  # noqa: BLE001
        return {}
    return {e["file_name"]: e for e in entries}


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidate-manifest", default=str(CANDIDATE_MANIFEST_CSV))
    ap.add_argument("--raw-root", "--raw-dir", dest="raw_root", default=str(RAW_DIR),
                     help=f"Directory GRIB2 files are read from (default: {RAW_DIR}). Must match "
                          "whatever --raw-root was used for acquisition (e.g. a relocated "
                          "D:\\SIH-Historical-GFS\\raw). --raw-dir is accepted as an alias for "
                          "backward compatibility with the Phase 0.4.19 original flag name.")
    ap.add_argument("--provenance-manifest", default=str(PROVENANCE_MANIFEST),
                     help=f"Path to the acquisition provenance manifest.json (default: {PROVENANCE_MANIFEST}).")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    expected_targets = load_expected_targets(Path(args.candidate_manifest))
    provenance = load_provenance(Path(args.provenance_manifest))
    raw_dir = Path(args.raw_root)
    print(f"Verifying against raw-file storage root: {raw_dir}")
    print(f"Provenance manifest: {args.provenance_manifest}\n")

    all_results = []
    seen_sha256: dict[str, str] = {}
    n_not_found = 0
    n_pass = 0
    n_fail = 0

    for expected in expected_targets:
        path = raw_dir / expected["filename"]
        res = verify_one_file(path, expected, provenance)
        sha = res.get("sha256")
        if sha:
            if sha in seen_sha256 and seen_sha256[sha] != res["filename"]:
                res["failures"].append(f"duplicate SHA256 vs {seen_sha256[sha]} -- possible duplicate/corrupt download")
                res["status"] = "FAIL"
            else:
                seen_sha256[sha] = res["filename"]

        print(f"{res['filename']}: {res['status']}")
        for fmsg in res["failures"]:
            print(f"  FAIL: {fmsg}")
        for w in res.get("warnings", []):
            print(f"  WARN: {w}")

        all_results.append(res)
        if res["status"] == "FILE_NOT_FOUND":
            n_not_found += 1
        elif res["status"] == "PASS":
            n_pass += 1
        else:
            n_fail += 1

    total = len(expected_targets)
    print(f"\n{n_pass}/{total} PASS, {n_fail} FAIL, {n_not_found} not yet acquired")
    gate = "GREEN" if (n_fail == 0 and n_not_found == 0) else (
        "YELLOW" if n_fail == 0 else "RED"
    )
    print(f"PHASE_0_4_19_BATCH_VERIFICATION = {gate}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(all_results, indent=2, default=str))

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
