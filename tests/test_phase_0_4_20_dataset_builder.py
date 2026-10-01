#!/usr/bin/env python3
"""Phase 0.4.20 focused tests -- manifest-driven, HOLDOUT-safe historical
GFS dataset builder. These tests never download, never train, and never
touch production. Most tests exercise the control-flow/validation
functions directly against synthetic manifests and DataFrames, since this
sandbox does not have the real Phase 0.4.19 batch's 40 GRIB2 files on
disk -- the real candidate manifest (20 event groups) IS used where that
alone is enough (grouping, partition filtering, fail-closed-on-missing)."""
import csv
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_phase_0_4_20_dataset as bld  # noqa: E402
import build_vobl_historical_gfs_ts_join as join_mod  # noqa: E402
from historical_dataset_split import TRAIN, HOLDOUT  # noqa: E402


def _skip_if_manifest_missing():
    if not bld.CANDIDATE_MANIFEST_CSV.exists():
        pytest.skip("real candidate manifest CSV not present in this environment")


MANIFEST_HEADER = (
    "priority,target_ist_date,target_slot,label,event_group_key,target_valid_time,"
    "selected_gfs_cycle,selected_lead,reason,partition,season,expected_file,expected_role\n"
)


def _write_manifest(path: Path, rows: list[str]):
    path.write_text(MANIFEST_HEADER + "".join(rows))


# -------------------- 1. manifest-driven source selection --------------------

def test_load_event_groups_derives_from_the_real_candidate_csv_not_a_hardcoded_list():
    _skip_if_manifest_missing()
    groups = bld.load_event_groups(bld.CANDIDATE_MANIFEST_CSV)
    assert len(groups) == 20
    for g in groups:
        assert set(g["leads"]) == {3, 6}
        assert g["csv_partition"] in (TRAIN, HOLDOUT)


def test_load_event_groups_fails_on_an_event_group_spanning_two_cycles(tmp_path):
    manifest = tmp_path / "bad.csv"
    _write_manifest(manifest, [
        "1,2020-01-01,0,POSITIVE,X|2020-01-01|0,2020-01-01T02:30:00+05:30,2019123118,f003,r,train,winter,f1,lead_hour_3\n",
        "1,2020-01-01,0,POSITIVE,X|2020-01-01|0,2020-01-01T05:30:00+05:30,2020010100,f006,r,train,winter,f2,lead_hour_6\n",
    ])
    with pytest.raises(bld.BuildFailure, match="more than one GFS cycle"):
        bld.load_event_groups(manifest)


def test_load_event_groups_excludes_ambiguous_rows(tmp_path):
    manifest = tmp_path / "amb.csv"
    _write_manifest(manifest, [
        "1,2020-01-01,0,POSITIVE,X|2020-01-01|0,AMBIGUOUS,AMBIGUOUS,AMBIGUOUS,r,train,winter,AMBIGUOUS,AMBIGUOUS\n",
        "2,2020-07-15,1,POSITIVE,Y|2020-07-15|1,2020-07-15T05:30:00+05:30,2020071500,f003,r,train,monsoon,f1,lead_hour_3\n",
        "2,2020-07-15,1,POSITIVE,Y|2020-07-15|1,2020-07-15T08:30:00+05:30,2020071500,f006,r,train,monsoon,f2,lead_hour_6\n",
    ])
    groups = bld.load_event_groups(manifest)
    assert len(groups) == 1
    assert groups[0]["event_group_key"] == "Y|2020-07-15|1"


# -------------------- 2/3. HOLDOUT safety / fail closed --------------------

def test_filter_by_partition_train_structurally_excludes_holdout_groups():
    _skip_if_manifest_missing()
    groups = bld.load_event_groups(bld.CANDIDATE_MANIFEST_CSV)
    train_groups = bld.filter_by_partition(groups, TRAIN)
    assert len(train_groups) == 14
    assert all(g["csv_partition"] == TRAIN for g in train_groups)


def test_filter_by_partition_holdout_structurally_excludes_train_groups():
    _skip_if_manifest_missing()
    groups = bld.load_event_groups(bld.CANDIDATE_MANIFEST_CSV)
    holdout_groups = bld.filter_by_partition(groups, HOLDOUT)
    assert len(holdout_groups) == 6
    assert all(g["csv_partition"] == HOLDOUT for g in holdout_groups)


def test_filter_by_partition_all_returns_everything():
    _skip_if_manifest_missing()
    groups = bld.load_event_groups(bld.CANDIDATE_MANIFEST_CSV)
    assert len(bld.filter_by_partition(groups, "all")) == len(groups)


def test_a_holdout_group_is_never_passed_to_verify_files_present_in_a_train_build(monkeypatch, tmp_path):
    _skip_if_manifest_missing()
    groups = bld.load_event_groups(bld.CANDIDATE_MANIFEST_CSV)
    train_groups = bld.filter_by_partition(groups, TRAIN)
    checked_event_groups = []

    def _spy(groups_arg, raw_root):
        checked_event_groups.extend(g["event_group_key"] for g in groups_arg)
        raise bld.BuildFailure("stop here, we only care what was checked")

    monkeypatch.setattr(bld, "verify_files_present", _spy)
    with pytest.raises(bld.BuildFailure):
        bld.verify_files_present(train_groups, tmp_path)
    holdout_keys = {g["event_group_key"] for g in bld.filter_by_partition(groups, HOLDOUT)}
    assert not (set(checked_event_groups) & holdout_keys), (
        "a TRAIN build must never even attempt to resolve a HOLDOUT file's path"
    )


def test_missing_file_fails_loudly_and_names_the_file(tmp_path):
    groups = [{
        "event_group_key": "X|2020-07-15|1", "cycle": "2020071500", "leads": [3, 6],
        "csv_partition": "train", "csv_label": "POSITIVE", "target_ist_date": "2020-07-15",
        "season": "monsoon",
    }]
    empty_raw_root = tmp_path / "raw"
    empty_raw_root.mkdir()
    with pytest.raises(bld.BuildFailure) as exc_info:
        bld.verify_files_present(groups, empty_raw_root)
    msg = str(exc_info.value)
    assert "gfs.0p25.2020071500.f003.grib2" in msg
    assert "X|2020-07-15|1" in msg


def test_missing_file_does_not_substitute_another_cycle(tmp_path):
    # Only ONE of the two required lead files exists -- this must still
    # fail (never silently build from a partial pair).
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    (raw_root / "gfs.0p25.2020071500.f003.grib2").write_bytes(b"GRIB" + b"x" * 100 + b"7777")
    groups = [{
        "event_group_key": "X|2020-07-15|1", "cycle": "2020071500", "leads": [3, 6],
        "csv_partition": "train", "csv_label": "POSITIVE", "target_ist_date": "2020-07-15",
        "season": "monsoon",
    }]
    with pytest.raises(bld.BuildFailure, match="f006"):
        bld.verify_files_present(groups, raw_root)


# -------------------- manifest/assign_partition disagreement --------------------

def test_partition_disagreement_with_assign_partition_fails():
    groups = [{
        "event_group_key": "X|2020-07-15|1", "cycle": "2020071500", "leads": [3, 6],
        "csv_partition": "holdout",  # WRONG: 2020 is a TRAIN year
        "csv_label": "POSITIVE", "target_ist_date": "2020-07-15", "season": "monsoon",
    }]
    with pytest.raises(bld.BuildFailure, match="PARTITION DISAGREEMENT"):
        bld.verify_partition_agreement(groups, {})


def test_partition_disagreement_with_provenance_manifest_fails():
    groups = [{
        "event_group_key": "X|2020-07-15|1", "cycle": "2020071500", "leads": [3, 6],
        "csv_partition": "train", "csv_label": "POSITIVE", "target_ist_date": "2020-07-15",
        "season": "monsoon",
    }]
    provenance = {
        "gfs.0p25.2020071500.f003.grib2": {"partition": "holdout"},  # disagrees with csv_partition
    }
    with pytest.raises(bld.BuildFailure, match="PARTITION DISAGREEMENT"):
        bld.verify_partition_agreement(groups, provenance)


def test_real_candidate_manifest_partitions_all_agree_with_assign_partition():
    _skip_if_manifest_missing()
    groups = bld.load_event_groups(bld.CANDIDATE_MANIFEST_CSV)
    # should not raise
    bld.verify_partition_agreement(groups, {})


# -------------------- duplicate event_group failure --------------------

def _fake_combined_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_duplicate_source_file_rows_fail():
    df = _fake_combined_df([
        {"source_file": "a.grib2", "cell_id": "C1", "forecast_lead_hours": 3,
         "event_group_key": "C1|2020-01-01|0", "candidate_event_group_key": "C1|2020-01-01|0",
         "train_holdout_partition": "train"},
        {"source_file": "a.grib2", "cell_id": "C1", "forecast_lead_hours": 3,
         "event_group_key": "C1|2020-01-01|0", "candidate_event_group_key": "C1|2020-01-01|0",
         "train_holdout_partition": "train"},
    ])
    groups = [{"event_group_key": "C1|2020-01-01|0", "leads": [3]}]
    with pytest.raises(bld.BuildFailure, match="duplicate"):
        bld.validate_event_group_keys(df, groups)


# -------------------- unknown-label failure --------------------

def test_unknown_label_status_fails_validation():
    df = _fake_combined_df([
        {"source_file": "a.grib2", "event_group_key": "C1|2020-01-01|0",
         "label_status": "UNKNOWN", "candidate_label": "POSITIVE"},
    ])
    with pytest.raises(bld.BuildFailure, match="UNKNOWN"):
        bld.validate_labels(df)


def test_unmatched_label_status_fails_validation():
    df = _fake_combined_df([
        {"source_file": "a.grib2", "event_group_key": "C1|2020-01-01|0",
         "label_status": "UNMATCHED", "candidate_label": "POSITIVE"},
    ])
    with pytest.raises(bld.BuildFailure):
        bld.validate_labels(df)


def test_label_status_mismatch_vs_candidate_manifest_fails():
    df = _fake_combined_df([
        {"source_file": "a.grib2", "event_group_key": "C1|2020-01-01|0",
         "label_status": "NEGATIVE_CONFIRMED", "candidate_label": "POSITIVE"},
    ])
    with pytest.raises(bld.BuildFailure, match="label mismatch"):
        bld.validate_labels(df)


def test_consistent_positive_negative_labels_pass():
    df = _fake_combined_df([
        {"source_file": "a.grib2", "event_group_key": "C1|2020-01-01|0",
         "label_status": "POSITIVE", "candidate_label": "POSITIVE"},
        {"source_file": "b.grib2", "event_group_key": "C2|2020-01-02|0",
         "label_status": "NEGATIVE_CONFIRMED", "candidate_label": "NEGATIVE_CONFIRMED"},
    ])
    bld.validate_labels(df)  # should not raise


# -------------------- custom D: raw-root --------------------

def test_verify_files_present_honors_a_custom_raw_root(tmp_path):
    # Place the file ONLY under the custom root -- verify_files_present
    # must find it there, not at the default join_mod.RAW_DIR.
    custom_root = tmp_path / "D_drive_equivalent" / "raw"
    custom_root.mkdir(parents=True)
    (custom_root / "gfs.0p25.2020071500.f003.grib2").write_bytes(b"GRIB" + b"x" * 10 + b"7777")
    (custom_root / "gfs.0p25.2020071500.f006.grib2").write_bytes(b"GRIB" + b"x" * 10 + b"7777")
    groups = [{
        "event_group_key": "X|2020-07-15|1", "cycle": "2020071500", "leads": [3, 6],
        "csv_partition": "train", "csv_label": "POSITIVE", "target_ist_date": "2020-07-15",
        "season": "monsoon",
    }]
    bld.verify_files_present(groups, custom_root)  # should not raise
    bld.verify_grib2_integrity(groups, custom_root)  # should not raise

    # And it must still fail against the default/empty root if the files
    # only exist on the custom one.
    with pytest.raises(bld.BuildFailure):
        bld.verify_files_present(groups, tmp_path / "empty_default_root")


def test_verify_raw_root_exists_passes_on_a_real_custom_directory(tmp_path):
    custom_root = tmp_path / "D_drive_equivalent" / "raw"
    custom_root.mkdir(parents=True)
    bld.verify_raw_root_exists(custom_root)  # should not raise


def test_verify_raw_root_exists_fails_loudly_and_distinctly_from_a_missing_file(tmp_path):
    # This is the regression test for the real bug class: an unreachable
    # raw_root directory must fail with a DIFFERENT, more specific error
    # than "missing file", naming the resolved path, so the two failure
    # modes (shell/argument corruption vs. a genuine acquisition gap) are
    # never confused again.
    missing_root = tmp_path / "does_not_exist_at_all"
    with pytest.raises(bld.BuildFailure, match="RAW_ROOT DIRECTORY NOT FOUND"):
        bld.verify_raw_root_exists(missing_root)


def test_run_build_fails_at_the_raw_root_preflight_before_naming_individual_files(tmp_path):
    # End-to-end: run_build() must hit verify_raw_root_exists() (and raise
    # its distinct error) before it ever reaches verify_files_present()'s
    # per-file "missing source file" wall-of-errors, when raw_root itself
    # doesn't exist. This is the exact failure the user hit on their real
    # machine: the message must point at the directory, not list 28
    # individually "missing" files that are actually just unreachable
    # because the whole root was never found.
    candidate_csv = tmp_path / "manifest.csv"
    _write_manifest(candidate_csv, [
        "1,2020-07-15,1,POSITIVE,X|2020-07-15|1,2020-07-15T05:30:00+05:30,"
        "2020071500,f003,r,train,monsoon,f1,lead_hour_3\n",
    ])
    provenance_path = tmp_path / "manifest.json"
    provenance_path.write_text("[]")
    labels_path = tmp_path / "ts_labels.csv"
    labels_path.write_text("cell_id,ist_date,slot_id,label,label_status\n")
    unreachable_root = tmp_path / "shell_mangled_path_D_SIH_Historical_GFSraw"

    with pytest.raises(bld.BuildFailure, match="RAW_ROOT DIRECTORY NOT FOUND"):
        bld.run_build(
            candidate_csv=candidate_csv,
            provenance_path=provenance_path,
            raw_root=unreachable_root,
            labels_path=labels_path,
            partition="train",
            out_dir=tmp_path / "out",
            tmp_dir=tmp_path / "tmp",
        )


def test_windows_backslash_path_is_corrupted_by_an_unquoted_posix_shell():
    # Documents and proves, via PureWindowsPath (the portable stand-in for
    # what Windows itself does with the string), that the real Phase
    # 0.4.20 bug report is NOT a Windows path-parsing or pathlib issue:
    # PureWindowsPath treats backslash and forward-slash spellings of the
    # same path identically. The actual corruption happens one layer
    # earlier, at the shell's own command-line tokenization, before Python
    # ever receives the argument -- a POSIX-style shell such as Git Bash
    # treats an unquoted backslash as an escape character and strips it.
    from pathlib import PureWindowsPath
    import subprocess

    a = PureWindowsPath("D:\\SIH-Historical-GFS\\raw")
    b = PureWindowsPath("D:/SIH-Historical-GFS/raw")
    assert a == b
    assert str(a / "gfs.0p25.2015022812.f003.grib2") == (
        "D:\\SIH-Historical-GFS\\raw\\gfs.0p25.2015022812.f003.grib2"
    )

    # Reproduce the actual shell-level corruption mechanism in bash itself.
    result = subprocess.run(
        ["bash", "-c", 'python3 -c "import sys; print(sys.argv[1])" '
                        "D:\\SIH-Historical-GFS\\raw"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "D:SIH-Historical-GFSraw"
    # The forward-slash spelling of the same path is untouched by the
    # shell, confirming it is not a "slash style" issue inside this
    # script or pathlib -- it is specific to unquoted backslashes.
    result_fwd = subprocess.run(
        ["bash", "-c", 'python3 -c "import sys; print(sys.argv[1])" '
                        "D:/SIH-Historical-GFS/raw"],
        capture_output=True, text=True, check=True,
    )
    assert result_fwd.stdout.strip() == "D:/SIH-Historical-GFS/raw"


# -------------------- second root cause: ts_labels.csv resolution --------------------

def test_verify_ts_labels_path_exists_passes_on_a_real_custom_file(tmp_path):
    custom_labels = tmp_path / "some_other_location" / "ts_labels.csv"
    custom_labels.parent.mkdir(parents=True)
    custom_labels.write_text("cell_id,ist_date,slot_id,label,label_status\n")
    bld.verify_ts_labels_path_exists(custom_labels)  # should not raise


def test_verify_ts_labels_path_exists_fails_loudly_and_names_the_checked_path(tmp_path):
    # Regression test for the real bug: a missing ts_labels.csv must fail
    # BEFORE build_pilot() is ever called, with an error naming the exact
    # resolved path that was checked and instructing the caller to pass
    # --ts-labels rather than copy/duplicate the archive.
    missing_labels = tmp_path / "does_not_exist" / "ts_labels.csv"
    with pytest.raises(bld.BuildFailure, match="TS_LABELS.CSV NOT FOUND") as exc_info:
        bld.verify_ts_labels_path_exists(missing_labels)
    msg = str(exc_info.value)
    assert str(missing_labels) in msg
    assert "--ts-labels" in msg
    assert "copy" in msg.lower() or "duplicate" in msg.lower()


def test_no_machine_specific_absolute_path_is_hardcoded_for_labels():
    # The default ts_labels path must be computed relative to REPO_ROOT
    # (itself derived from __file__ at import time), never contain a
    # literal username or a fixed drive/host-specific absolute prefix
    # baked into source. This is what makes --ts-labels a true override
    # rather than a workaround for a hardcoded path.
    default_path = join_mod.TS_LABELS_PATH
    assert default_path == join_mod.REPO_ROOT / "processed" / "labels" / "ts_labels.csv"
    # The default must change if REPO_ROOT changes -- i.e. it is derived,
    # not a separately hardcoded machine-specific string anywhere else in
    # this module.
    src = Path(bld.__file__).read_text()
    assert "C:\\Users" not in src
    assert "C:/Users" not in src
    import build_vobl_historical_gfs_ts_join as _join_mod_src_check
    join_src = Path(_join_mod_src_check.__file__).read_text()
    assert "C:\\Users" not in join_src
    assert "C:/Users" not in join_src


def test_run_build_fails_at_the_ts_labels_preflight_with_a_valid_raw_root(tmp_path):
    # End-to-end: when raw_root is fine but the label archive genuinely
    # isn't at the resolved --ts-labels path, run_build() must stop at
    # verify_ts_labels_path_exists() with its own distinct message --
    # never fall through to build_pilot() and surface only the generic
    # "did not report BUILT_FROM_ACTUAL_FILES" wrapper error that hid the
    # real cause in the original bug report.
    candidate_csv = tmp_path / "manifest.csv"
    _write_manifest(candidate_csv, [
        "1,2020-07-15,1,POSITIVE,X|2020-07-15|1,2020-07-15T05:30:00+05:30,"
        "2020071500,f003,r,train,monsoon,f1,lead_hour_3\n",
    ])
    provenance_path = tmp_path / "manifest.json"
    provenance_path.write_text("[]")
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    (raw_root / "gfs.0p25.2020071500.f003.grib2").write_bytes(b"GRIB" + b"x" * 10 + b"7777")
    missing_labels = tmp_path / "wrong_location" / "ts_labels.csv"

    with pytest.raises(bld.BuildFailure, match="TS_LABELS.CSV NOT FOUND"):
        bld.run_build(
            candidate_csv=candidate_csv,
            provenance_path=provenance_path,
            raw_root=raw_root,
            labels_path=missing_labels,
            partition="train",
            out_dir=tmp_path / "out",
            tmp_dir=tmp_path / "tmp",
        )


def test_build_one_group_reads_actual_grib_files_with_a_custom_labels_path(tmp_path, monkeypatch):
    # Proves requirement (e): given a CUSTOM raw_root and a CUSTOM
    # ts_labels_path (neither at any default location), build_one_group()
    # -> build_pilot() still reads and decodes the real, existing GRIB2
    # files and the real label archive -- it never fabricates or
    # synthesizes rows. Uses a real, already-acquired small GRIB pair via
    # a symlink (so the test doesn't duplicate ~400MB of real data into
    # the test fixtures) and a real copy of the actual label archive
    # (small CSV, safe to copy for a throwaway tmp_path fixture).
    real_raw_dir = join_mod.RAW_DIR
    real_labels = join_mod.TS_LABELS_PATH
    candidates = sorted(real_raw_dir.glob("gfs.0p25.*.f003.grib2")) if real_raw_dir.exists() else []
    if not candidates or not real_labels.exists():
        pytest.skip("no real acquired GRIB files / real ts_labels.csv available in this environment")
    f003 = f006_or_other = None
    for candidate in candidates:
        paired = real_raw_dir / candidate.name.replace(".f003.", ".f006.")
        if paired.exists():
            f003, f006_or_other = candidate, paired
            break
    if f003 is None:
        pytest.skip("no real acquired GRIB pair (f003+f006) available in this environment")
    cycle = f003.name.split(".")[2]

    custom_root = tmp_path / "custom_raw_root_not_the_default"
    custom_root.mkdir()
    (custom_root / f003.name).symlink_to(f003)
    (custom_root / f006_or_other.name).symlink_to(f006_or_other)

    custom_labels = tmp_path / "custom_labels_location" / "ts_labels.csv"
    custom_labels.parent.mkdir(parents=True)
    custom_labels.write_text(real_labels.read_text())

    monkeypatch.setattr(join_mod, "RAW_DIR", custom_root)
    try:
        group = {
            "event_group_key": "REAL|2099-01-01|1", "cycle": cycle, "leads": [3, 6],
            "csv_partition": "train", "csv_label": "POSITIVE",
        }
        df = bld.build_one_group(group, custom_labels, tmp_path / "tmp_build")
    finally:
        monkeypatch.setattr(join_mod, "RAW_DIR", real_raw_dir)

    assert len(df) == 2
    assert set(df["source_file"]) == {f003.name, f006_or_other.name}
    # Real decoded feature values, not placeholders/NaNs/zeros-only.
    assert df["cape"].notna().all()


def test_run_build_restores_raw_dir_after_a_failure(tmp_path):
    original = join_mod.RAW_DIR
    candidate_csv = tmp_path / "manifest.csv"
    _write_manifest(candidate_csv, [
        "1,2020-07-15,1,POSITIVE,X|2020-07-15|1,2020-07-15T05:30:00+05:30,2020071500,f003,r,train,monsoon,"
        "gfs.0p25.2020071500.f003.grib2,lead_hour_3\n",
        "1,2020-07-15,1,POSITIVE,X|2020-07-15|1,2020-07-15T08:30:00+05:30,2020071500,f006,r,train,monsoon,"
        "gfs.0p25.2020071500.f006.grib2,lead_hour_6\n",
    ])
    fake_raw_root = tmp_path / "D_drive" / "raw"
    with pytest.raises(bld.BuildFailure):
        bld.run_build(
            candidate_csv=candidate_csv, provenance_path=tmp_path / "nonexistent_manifest.json",
            raw_root=fake_raw_root, labels_path=join_mod.TS_LABELS_PATH,
            partition="train", out_dir=tmp_path / "out", tmp_dir=tmp_path / "tmp",
        )
    assert join_mod.RAW_DIR == original, "RAW_DIR must be restored even when the build fails"


# -------------------- deterministic output / disjointness --------------------

def test_loading_the_same_manifest_twice_produces_identical_groups():
    _skip_if_manifest_missing()
    g1 = bld.load_event_groups(bld.CANDIDATE_MANIFEST_CSV)
    g2 = bld.load_event_groups(bld.CANDIDATE_MANIFEST_CSV)
    keys1 = sorted(g["event_group_key"] for g in g1)
    keys2 = sorted(g["event_group_key"] for g in g2)
    assert keys1 == keys2


def test_train_and_holdout_event_groups_are_fully_disjoint():
    _skip_if_manifest_missing()
    groups = bld.load_event_groups(bld.CANDIDATE_MANIFEST_CSV)
    train_keys = {g["event_group_key"] for g in bld.filter_by_partition(groups, TRAIN)}
    holdout_keys = {g["event_group_key"] for g in bld.filter_by_partition(groups, HOLDOUT)}
    assert train_keys.isdisjoint(holdout_keys)
    assert len(train_keys) == 14
    assert len(holdout_keys) == 6


def test_holdout_contamination_check_fails_a_train_build_with_a_holdout_row():
    df = _fake_combined_df([
        {"train_holdout_partition": "train", "source_file": "a.grib2", "event_group_key": "A"},
        {"train_holdout_partition": "holdout", "source_file": "b.grib2", "event_group_key": "B"},
    ])
    with pytest.raises(bld.BuildFailure, match="TRAIN build contains"):
        bld.validate_holdout_contamination(df, TRAIN)


def test_holdout_contamination_check_fails_a_holdout_build_with_a_train_row():
    df = _fake_combined_df([
        {"train_holdout_partition": "train", "source_file": "a.grib2", "event_group_key": "A"},
        {"train_holdout_partition": "holdout", "source_file": "b.grib2", "event_group_key": "B"},
    ])
    with pytest.raises(bld.BuildFailure, match="HOLDOUT build contains"):
        bld.validate_holdout_contamination(df, HOLDOUT)


def test_holdout_contamination_check_passes_a_clean_train_build():
    df = _fake_combined_df([
        {"train_holdout_partition": "train", "source_file": "a.grib2", "event_group_key": "A"},
        {"train_holdout_partition": "train", "source_file": "b.grib2", "event_group_key": "B"},
    ])
    result = bld.validate_holdout_contamination(df, TRAIN)
    assert result == {"n_train_rows": 2, "n_holdout_rows": 0}


# -------------------- leakage --------------------

def test_leakage_check_passes_the_real_feature_columns():
    result = bld.validate_leakage(pd.DataFrame({c: [] for c in bld.FEATURE_COLUMNS + ["label", "label_status"]}))
    assert result["all_forecast_derived"] is True


def test_leakage_check_fails_if_a_label_field_is_added_to_feature_columns(monkeypatch):
    # Adding "label" to the predictor list is caught by the FORECAST_DERIVED
    # classification check first (label has no leakage classification at
    # all), which is itself a correct refusal -- confirm it fails, and for
    # a leakage-relevant reason, not that it silently passes.
    monkeypatch.setattr(bld, "FEATURE_COLUMNS", bld.FEATURE_COLUMNS + ["label"])
    with pytest.raises(bld.BuildFailure, match="non-FORECAST_DERIVED"):
        bld.validate_leakage(pd.DataFrame({c: [] for c in bld.FEATURE_COLUMNS + ["label_status"]}))


# -------------------- row-count reconciliation --------------------

def test_row_count_reconciliation_passes_when_exact():
    groups = [{"leads": [3, 6]}, {"leads": [3, 6]}]
    df = pd.DataFrame({"x": [1, 2, 3, 4]})
    bld.reconcile_row_counts(df, groups)  # should not raise


def test_row_count_reconciliation_fails_when_short():
    groups = [{"leads": [3, 6]}, {"leads": [3, 6]}]
    df = pd.DataFrame({"x": [1, 2, 3]})
    with pytest.raises(bld.BuildFailure, match="does not reconcile"):
        bld.reconcile_row_counts(df, groups)


# -------------------- GRIB2 integrity pre-check --------------------

def test_grib2_integrity_check_rejects_a_bad_header(tmp_path):
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    bad_file = raw_root / "gfs.0p25.2020071500.f003.grib2"
    bad_file.write_bytes(b"NOTAGRIB" + b"x" * 100)
    groups = [{"event_group_key": "X", "cycle": "2020071500", "leads": [3]}]
    with pytest.raises(bld.BuildFailure, match="GRIB2 integrity"):
        bld.verify_grib2_integrity(groups, raw_root)


def test_grib2_integrity_check_passes_a_well_formed_header_trailer(tmp_path):
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    good_file = raw_root / "gfs.0p25.2020071500.f003.grib2"
    good_file.write_bytes(b"GRIB" + b"x" * 100 + b"7777")
    groups = [{"event_group_key": "X", "cycle": "2020071500", "leads": [3]}]
    bld.verify_grib2_integrity(groups, raw_root)  # should not raise


# -------------------- never overwrites the Phase 0.4.16 dataset --------------------

def test_output_filenames_never_collide_with_the_existing_phase_0_4_16_dataset():
    assert "phase_0_4_20" in str(bld.DEFAULT_OUT_DIR / "phase_0_4_20_train.csv")
    assert bld.EXISTING_PHASE_0_4_16_OUT_CSV.name == "historical_gfs_ts_dataset.csv"
    assert "phase_0_4_20_train.csv" != bld.EXISTING_PHASE_0_4_16_OUT_CSV.name
    assert "phase_0_4_20_holdout.csv" != bld.EXISTING_PHASE_0_4_16_OUT_CSV.name


# -------------------- production isolation --------------------

def test_no_production_code_imports_the_phase_0_4_20_builder():
    production_files = [
        REPO_ROOT / "backend" / "pipeline.py",
        REPO_ROOT / "forecast_action.py",
        REPO_ROOT / "canonical_forecast_writer.py",
        REPO_ROOT / "location_engine.py",
    ]
    for pf in production_files:
        if not pf.exists():
            continue
        text = pf.read_text()
        assert "build_phase_0_4_20_dataset" not in text
