#!/usr/bin/env python3
"""Phase 0.4.19 focused tests -- batch acquisition + verification tooling
for the Phase 0.4.18-cleaned candidate manifest. These tests never hit the
network and never download anything: load_batch_targets/dry-run path only,
plus pure URL-construction and dedup checks."""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import acquire_phase_0_4_19_batch as acq  # noqa: E402
import verify_phase_0_4_19_batch as ver  # noqa: E402


def _skip_if_manifest_missing():
    if not acq.MANIFEST_CSV.exists():
        pytest.skip("candidate manifest CSV not present in this environment")


@pytest.fixture(scope="module")
def targets():
    _skip_if_manifest_missing()
    return acq.load_batch_targets()


def test_batch_has_exactly_the_unique_file_count_from_the_manifest(targets):
    import csv
    with open(acq.MANIFEST_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    expected_unique_files = len({r["expected_file"] for r in rows if r["selected_gfs_cycle"] != "AMBIGUOUS"})
    assert len(targets) == expected_unique_files


def test_no_duplicate_destination_filenames(targets):
    filenames = [t.filename for t in targets]
    assert len(filenames) == len(set(filenames))


def test_urls_use_the_confirmed_gdex_naming_convention(targets):
    for t in targets:
        yyyy = t.cycle[:4]
        yyyymmdd = t.cycle[:8]
        lead_num = int(t.lead.lstrip("f"))
        expected_url = (
            f"https://data.gdex.ucar.edu/d084001/{yyyy}/{yyyymmdd}/"
            f"gfs.0p25.{t.cycle}.f{lead_num:03d}.grib2"
        )
        assert t.url == expected_url


def test_url_builder_matches_a_known_already_acquired_file():
    # Cross-check against a real, already-downloaded Phase 0.4.16 file
    # (not invented): gfs.0p25.2020071500.f003.grib2
    url = acq.build_direct_url("2020071500", "003")
    assert url == "https://data.gdex.ucar.edu/d084001/2020/20200715/gfs.0p25.2020071500.f003.grib2"


def test_destination_paths_land_in_the_established_raw_directory(targets):
    for t in targets:
        assert t.dest.parent == acq.RAW_DIR
        assert t.dest.name == t.filename


def test_holdout_targets_are_tagged_and_separable_from_train(targets):
    train_files = {t.filename for t in targets if t.partition == "train"}
    holdout_files = {t.filename for t in targets if t.partition == "holdout"}
    assert train_files.isdisjoint(holdout_files)
    assert len(holdout_files) > 0
    assert len(train_files) > 0


def test_only_partition_filtering_is_exact(targets):
    train_only = [t for t in targets if t.partition == "train"]
    holdout_only = [t for t in targets if t.partition == "holdout"]
    assert len(train_only) + len(holdout_only) == len(targets)


def test_dry_run_builds_without_network_or_filesystem_writes(targets, tmp_path, monkeypatch, capsys):
    # main() in --dry-run mode (the default) must never call stream_download
    # or write to RAW_DIR.
    called = {"download": False}

    def _fail_if_called(*a, **kw):
        called["download"] = True
        raise AssertionError("stream_download must not be called in a dry run")

    monkeypatch.setattr(acq, "stream_download", _fail_if_called)
    monkeypatch.setattr(sys, "argv", ["acquire_phase_0_4_19_batch.py", "--dry-run"])
    acq.main()
    assert called["download"] is False


def test_already_verified_never_triggers_a_redownload(tmp_path):
    # A file whose on-disk size+sha256 matches its provenance record must
    # be reported SKIPPED_ALREADY_VERIFIED, never re-fetched.
    dest = tmp_path / "gfs.0p25.2020071500.f003.grib2"
    dest.write_bytes(b"not a real grib file, just bytes for the fingerprint test")
    sha = acq.sha256_of_file(dest)
    target = acq.DownloadTarget(
        event_group_key="X|2020-07-15|1", label="POSITIVE", partition="train",
        season="monsoon", target_ist_date="2020-07-15", cycle="2020071500",
        lead="f003", filename=dest.name, url="https://example.invalid/x", dest=dest,
    )
    provenance = {dest.name: {"size_bytes": dest.stat().st_size, "sha256": sha}}
    assert acq.already_verified(target, provenance) is True


def test_mismatched_fingerprint_is_not_treated_as_verified(tmp_path):
    dest = tmp_path / "gfs.0p25.2020071500.f003.grib2"
    dest.write_bytes(b"some bytes")
    target = acq.DownloadTarget(
        event_group_key="X|2020-07-15|1", label="POSITIVE", partition="train",
        season="monsoon", target_ist_date="2020-07-15", cycle="2020071500",
        lead="f003", filename=dest.name, url="https://example.invalid/x", dest=dest,
    )
    provenance = {dest.name: {"size_bytes": 999999, "sha256": "deadbeef"}}
    assert acq.already_verified(target, provenance) is False


def test_duplicate_fingerprint_detection_flags_real_collisions():
    provenance = {
        "a.grib2": {"sha256": "aaa"},
        "b.grib2": {"sha256": "aaa"},
        "c.grib2": {"sha256": "bbb"},
    }
    dupes = acq.detect_duplicate_fingerprints(provenance)
    assert len(dupes) == 1
    assert set(dupes[0]["filenames"]) == {"a.grib2", "b.grib2"}


def test_ambiguous_rows_are_excluded_from_the_download_batch(tmp_path):
    csv_path = tmp_path / "manifest_with_ambiguous.csv"
    csv_path.write_text(
        "priority,target_ist_date,target_slot,label,event_group_key,target_valid_time,"
        "selected_gfs_cycle,selected_lead,reason,partition,season,expected_file,expected_role\n"
        "1,2020-01-01,0,POSITIVE,X|2020-01-01|0,AMBIGUOUS,AMBIGUOUS,AMBIGUOUS,flagged,train,winter,AMBIGUOUS,AMBIGUOUS\n"
        "2,2020-07-15,1,POSITIVE,X|2020-07-15|1,2020-07-15T05:30:00+05:30,2020071500,f003,ok,train,monsoon,"
        "gfs.0p25.2020071500.f003.grib2,lead_hour_3\n"
    )
    targets = acq.load_batch_targets(csv_path)
    assert len(targets) == 1
    assert targets[0].filename == "gfs.0p25.2020071500.f003.grib2"


# -------------------- verifier (expectation recomputation) --------------------

def test_verifier_recomputes_expectations_independently_of_csv_columns():
    _skip_if_manifest_missing()
    targets = ver.load_expected_targets(ver.CANDIDATE_MANIFEST_CSV)
    assert len(targets) > 0
    for t in targets[:5]:
        # The recomputed event_group_key must agree with the CSV's own
        # column -- if it doesn't, that's a real manifest bug this
        # verifier is designed to catch, not something to paper over.
        assert t["expected_event_group_key"] == t["csv_event_group_key"]


def test_verifier_reports_file_not_found_rather_than_fabricating_a_pass(tmp_path):
    expected = {
        "filename": "gfs.0p25.2099010100.f003.grib2",
        "cycle": "2099010100", "lead_hours": 3,
        "expected_init_utc": "2099-01-01T00:00:00+00:00",
        "expected_valid_utc": "2099-01-01T03:00:00+00:00",
        "expected_ist_date": "2099-01-01", "expected_slot_id": 1,
        "expected_event_group_key": "X|2099-01-01|1", "csv_event_group_key": "X|2099-01-01|1",
        "csv_label": "POSITIVE", "csv_partition": "train",
    }
    res = ver.verify_one_file(tmp_path / expected["filename"], expected, {})
    assert res["status"] == "FILE_NOT_FOUND"
    assert res["failures"]


def test_no_production_code_imports_the_acquisition_or_verification_tooling():
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
        assert "acquire_phase_0_4_19_batch" not in text
        assert "verify_phase_0_4_19_batch" not in text
