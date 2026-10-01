#!/usr/bin/env python3
"""Phase 0.4.15 focused tests for scripts/verify_phase_0_4_15_oversized_stage_b.py.

These run against the small (<400MB) Stage B files already present in this
repository's data/external/historical_gfs/raw/ (the 2016/2017/2019 files
fully verified in Phase 0.4.14) -- they do NOT touch the 6 oversized files
this script exists to check (those are >400MB and not committed to the
repo). The point of these tests is to lock in that the verification
LOGIC itself is correct: it must pass a known-good file and must report
a failure (not silently pass) when an expectation is deliberately wrong.
The 6 oversized files still need to be checked by actually running this
script on the machine that holds them -- these tests cannot substitute
for that.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pytest  # noqa: E402

import verify_phase_0_4_15_oversized_stage_b as v  # noqa: E402

RAW_DIR = REPO_ROOT / "data" / "external" / "historical_gfs" / "raw"
MANIFEST_PATH = REPO_ROOT / "data" / "external" / "historical_gfs" / "manifest.json"
LABELS_PATH = (
    REPO_ROOT / "SIH_PANINDIA_GRID_LABELS_20260930_143541Z" / "processed" / "labels" / "ts_labels.csv"
)

KNOWN_GOOD_2016_F003 = RAW_DIR / "gfs.0p25.2016011500.f003.grib2"

EXPECTED_2016_F003 = {
    "filename": "gfs.0p25.2016011500.f003.grib2",
    "expected_init_utc": "2016-01-15T00:00:00+00:00",
    "expected_lead_hours": 3,
    "expected_valid_utc": "2016-01-15T03:00:00+00:00",
    "expected_ist_date": "2016-01-15",
    "expected_slot_id": 1,
    "expected_event_group_key": "IND_13.0_78.0|2016-01-15|1",
}


def _skip_if_file_missing():
    if not KNOWN_GOOD_2016_F003.exists() or not MANIFEST_PATH.exists():
        pytest.skip("reference Stage B file or manifest.json not present in this environment")


def test_verify_one_file_passes_on_known_good_file():
    _skip_if_file_missing()
    manifest = v.load_manifest(MANIFEST_PATH)
    res = v.verify_one_file(KNOWN_GOOD_2016_F003, EXPECTED_2016_F003, manifest)
    assert res["failures"] == []
    assert res["grib_header_ok"] is True
    assert res["grib_trailer_ok"] is True
    assert res["sha256"] == res["manifest_sha256"]


def test_verify_one_file_fails_on_wrong_expected_slot():
    _skip_if_file_missing()
    manifest = v.load_manifest(MANIFEST_PATH)
    bad_expected = dict(EXPECTED_2016_F003)
    bad_expected["expected_slot_id"] = 2
    bad_expected["expected_event_group_key"] = "IND_13.0_78.0|2016-01-15|2"
    res = v.verify_one_file(KNOWN_GOOD_2016_F003, bad_expected, manifest)
    assert res["failures"] != []
    assert any("slot mismatch" in f for f in res["failures"])


def test_verify_one_file_fails_on_wrong_expected_lead():
    _skip_if_file_missing()
    manifest = v.load_manifest(MANIFEST_PATH)
    bad_expected = dict(EXPECTED_2016_F003)
    bad_expected["expected_lead_hours"] = 6
    bad_expected["expected_valid_utc"] = "2016-01-15T06:00:00+00:00"
    res = v.verify_one_file(KNOWN_GOOD_2016_F003, bad_expected, manifest)
    assert any("lead mismatch" in f or "valid_time mismatch" in f for f in res["failures"])


def test_verify_one_file_reports_manifest_sha_mismatch():
    _skip_if_file_missing()
    manifest = v.load_manifest(MANIFEST_PATH)
    tampered_manifest = {
        k: (dict(val, sha256="0" * 64) if k == EXPECTED_2016_F003["filename"] else val)
        for k, val in manifest.items()
    }
    res = v.verify_one_file(KNOWN_GOOD_2016_F003, EXPECTED_2016_F003, tampered_manifest)
    assert any("SHA256 mismatch" in f for f in res["failures"])


def test_verify_one_file_grid_matches_expected_global_grid():
    _skip_if_file_missing()
    manifest = v.load_manifest(MANIFEST_PATH)
    res = v.verify_one_file(KNOWN_GOOD_2016_F003, EXPECTED_2016_F003, manifest)
    assert res["grid"]["gridType"] == "regular_ll"
    assert res["grid"]["Ni"] == 1440
    assert res["grid"]["Nj"] == 721
    assert not any("grid." in f for f in res["failures"])


def test_load_label_row_reads_real_csv_without_regenerating():
    if not LABELS_PATH.exists():
        pytest.skip("real ts_labels.csv not present in this environment")
    row = v.load_label_row(LABELS_PATH, "2016-01-15", "0601-1200")
    assert row is not None
    assert row["label_status"] == "NEGATIVE_CONFIRMED"
    assert row["cell_id"] == "IND_13.0_78.0"


def test_no_production_code_imports_the_verification_script():
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
        assert "verify_phase_0_4_15_oversized_stage_b" not in text
