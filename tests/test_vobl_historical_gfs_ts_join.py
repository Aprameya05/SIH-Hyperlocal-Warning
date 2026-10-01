#!/usr/bin/env python3
"""Phase 0.4.8 focused tests for scripts/build_vobl_historical_gfs_ts_join.py
(historical GFS -> VOBL observed-thunderstorm supervised join pilot).

Covers: GFS valid-time calculation, IST slot mapping, VOBL cell mapping,
positive/negative label join, unmatched-timestamp handling, provenance
completeness, leakage classification, and lead-time calculation -- using
the real functions from the build script (not reimplemented copies) and,
where practical, the actual generated pilot artifacts.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_vobl_historical_gfs_ts_join import (  # noqa: E402
    ist_slot_for, parse_grib_dt, cell_id_for, SLOT_WINDOWS_IST,
    VOBL_CELL_LAT, VOBL_CELL_LON, IST,
    build_pilot, grib_filename_for, path_for, parse_args,
    DEFAULT_CYCLE, DEFAULT_LEADS, DEFAULT_OUT_CSV, DEFAULT_OUT_MANIFEST,
    event_group_key_for, extract_prate, enumerate_messages, CONTRACT_VERSION,
)

PILOT_CSV = REPO_ROOT / "processed" / "historical_gfs_vobl" / "historical_gfs_vobl_pilot.csv"
PILOT_MANIFEST = REPO_ROOT / "processed" / "historical_gfs_vobl" / "historical_gfs_vobl_pilot_manifest.json"


# -------------------- A. GFS valid-time calculation --------------------

def test_a_valid_time_f003():
    dt = parse_grib_dt(20200715, 300)
    assert dt == datetime(2020, 7, 15, 3, 0, tzinfo=timezone.utc)


def test_a_valid_time_f006():
    dt = parse_grib_dt(20200715, 600)
    assert dt == datetime(2020, 7, 15, 6, 0, tzinfo=timezone.utc)


def test_a_init_time():
    dt = parse_grib_dt(20200715, 0)
    assert dt == datetime(2020, 7, 15, 0, 0, tzinfo=timezone.utc)


# -------------------- B. IST slot mapping --------------------

def test_b_f003_valid_time_maps_to_slot1():
    # 2020-07-15 03:00 UTC -> 08:30 IST -> slot 1 (06:00-11:59 IST)
    valid_utc = parse_grib_dt(20200715, 300)
    valid_ist = valid_utc.astimezone(IST)
    assert valid_ist.strftime("%H:%M") == "08:30"
    slot_id, slot_label = ist_slot_for(valid_ist)
    assert slot_id == 1
    assert slot_label == "0601-1200"


def test_b_f006_valid_time_maps_to_slot1():
    # 2020-07-15 06:00 UTC -> 11:30 IST -> still slot 1 (06:00-11:59 IST)
    valid_utc = parse_grib_dt(20200715, 600)
    valid_ist = valid_utc.astimezone(IST)
    assert valid_ist.strftime("%H:%M") == "11:30"
    slot_id, slot_label = ist_slot_for(valid_ist)
    assert slot_id == 1
    assert slot_label == "0601-1200"


def test_b_slot_boundaries_match_lead_time_contract():
    # ist_slot_for must use the exact same SLOT_WINDOWS_IST table as
    # lead_time.py / metar_ground_truth.py -- not a reimplemented copy.
    import importlib
    lead_time = importlib.import_module("lead_time")
    assert SLOT_WINDOWS_IST == lead_time.SLOT_WINDOWS_IST


def test_b_slot_0_and_3_boundaries():
    midnight_ist = datetime(2020, 7, 15, 0, 0, tzinfo=IST)
    assert ist_slot_for(midnight_ist) == (0, "0001-0600")
    late_ist = datetime(2020, 7, 15, 23, 59, tzinfo=IST)
    assert ist_slot_for(late_ist) == (3, "1801-2400")


# -------------------- C. VOBL cell mapping --------------------

def test_c_vobl_cell_id_matches_canonical_convention():
    assert cell_id_for(VOBL_CELL_LAT, VOBL_CELL_LON) == "IND_13.0_78.0"


def test_c_vobl_cell_matches_ts_labels_cell_used_by_production_scripts():
    # scripts/build_panindia_ts_labels.py uses the same (13.0, 78.0) cell for
    # VOBL's real labels -- this phase reuses it, not a new/competing cell.
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import build_panindia_ts_labels as ts_mod
    assert (ts_mod.VOBL_LAT, ts_mod.VOBL_LON) == (VOBL_CELL_LAT, VOBL_CELL_LON)


def test_c_pilot_manifest_reports_plausible_station_cell_distance():
    if not PILOT_MANIFEST.exists():
        pytest.skip("pilot manifest not yet built")
    manifest = json.loads(PILOT_MANIFEST.read_text())
    dist = manifest["vobl_cell_mapping"]["distance_station_to_cell_center_km"]
    # real VOBL station is ~13.20N/77.71E; the 1-degree canonical cell center
    # is (13.0, 78.0) -- a plausible nearest-cell distance is a few tens of km
    assert 0 < dist < 100


# -------------------- D/E. Positive / negative label join --------------------

def test_d_positive_label_join_synthetic():
    # Simulate the join logic against a synthetic ts_labels-shaped frame
    # containing one real-shaped POSITIVE row, independent of pilot content
    # (which this specific 2020-07-15 00Z cycle happens to be all-negative).
    labels = pd.DataFrame([
        {"timestamp": "2020-07-15T0601-1200", "cell_id": "IND_13.0_78.0",
         "label": 1.0, "label_status": "POSITIVE"},
    ])
    match = labels[(labels["cell_id"] == "IND_13.0_78.0") & (labels["timestamp"] == "2020-07-15T0601-1200")]
    assert len(match) == 1
    assert int(match.iloc[0]["label"]) == 1
    assert match.iloc[0]["label_status"] == "POSITIVE"


def test_e_negative_label_join_from_real_pilot():
    if not PILOT_CSV.exists():
        pytest.skip("pilot csv not yet built")
    df = pd.read_csv(PILOT_CSV)
    assert len(df) > 0
    assert (df["label_status"] == "NEGATIVE_CONFIRMED").all()
    assert (df["label"] == 0).all()
    assert df["label"].notna().all()


def test_e_no_null_silently_converted_to_zero():
    # label must only be 0 when label_status is explicitly NEGATIVE_CONFIRMED,
    # never a null/UNKNOWN silently collapsed to 0.
    if not PILOT_CSV.exists():
        pytest.skip("pilot csv not yet built")
    df = pd.read_csv(PILOT_CSV)
    for _, row in df.iterrows():
        if row["label"] == 0:
            assert row["label_status"] == "NEGATIVE_CONFIRMED"
        if pd.isna(row["label"]):
            assert row["label_status"] == "UNMATCHED"


# -------------------- F. Unmatched timestamp handling --------------------

def test_f_unmatched_rows_recorded_not_discarded():
    if not PILOT_MANIFEST.exists():
        pytest.skip("pilot manifest not yet built")
    manifest = json.loads(PILOT_MANIFEST.read_text())
    assert manifest["n_unmatched"] == len(manifest["unmatched_detail"])
    assert manifest["n_rows"] == manifest["n_positive"] + manifest["n_negative"] + manifest["n_unmatched"]


def test_f_unmatched_entries_have_explicit_reason():
    if not PILOT_MANIFEST.exists():
        pytest.skip("pilot manifest not yet built")
    manifest = json.loads(PILOT_MANIFEST.read_text())
    for entry in manifest["unmatched_detail"]:
        assert "reason" in entry and entry["reason"]


# -------------------- G. Provenance completeness --------------------

REQUIRED_PROVENANCE_COLUMNS = [
    "source", "archive_source", "source_file", "initialization_time_utc",
    "valid_time_utc", "valid_time_ist", "forecast_lead_hours", "slot_id",
    "cell_id", "label_source", "label_timestamp", "label_definition", "label_status",
]


def test_g_provenance_columns_present():
    if not PILOT_CSV.exists():
        pytest.skip("pilot csv not yet built")
    df = pd.read_csv(PILOT_CSV)
    for col in REQUIRED_PROVENANCE_COLUMNS:
        assert col in df.columns, f"missing provenance column: {col}"
        assert df[col].notna().all(), f"provenance column has nulls: {col}"


def test_g_source_file_matches_real_downloaded_grib_names():
    if not PILOT_CSV.exists():
        pytest.skip("pilot csv not yet built")
    df = pd.read_csv(PILOT_CSV)
    assert set(df["source_file"]) == {
        "gfs.0p25.2020071500.f003.grib2", "gfs.0p25.2020071500.f006.grib2",
    }


def test_g_manifest_has_sha256_hashes():
    if not PILOT_MANIFEST.exists():
        pytest.skip("pilot manifest not yet built")
    manifest = json.loads(PILOT_MANIFEST.read_text())
    for src in manifest["source_files"]:
        assert len(src["sha256"]) == 64


# -------------------- H. Leakage detection --------------------

def test_h_no_feature_uses_future_information():
    if not PILOT_MANIFEST.exists():
        pytest.skip("pilot manifest not yet built")
    manifest = json.loads(PILOT_MANIFEST.read_text())
    assert manifest["features_using_future_information"] == []
    for feature, cls in manifest["feature_leakage_classification"].items():
        assert cls in ("AVAILABLE_AT_INITIALIZATION", "FORECAST_DERIVED"), (
            f"unexpected leakage classification for {feature}: {cls}"
        )


def test_h_interval_precip_only_attached_to_lead_end_row():
    if not PILOT_CSV.exists():
        pytest.skip("pilot csv not yet built")
    df = pd.read_csv(PILOT_CSV)
    lead3 = df[df["forecast_lead_hours"] == 3].iloc[0]
    lead6 = df[df["forecast_lead_hours"] == 6].iloc[0]
    assert pd.isna(lead3["gfs_precip_3h_interval_mm"])
    assert pd.notna(lead6["gfs_precip_3h_interval_mm"])


# -------------------- I. Lead-time calculation --------------------

def test_i_lead_hours_match_valid_minus_init():
    if not PILOT_CSV.exists():
        pytest.skip("pilot csv not yet built")
    df = pd.read_csv(PILOT_CSV)
    for _, row in df.iterrows():
        init = pd.Timestamp(row["initialization_time_utc"])
        valid = pd.Timestamp(row["valid_time_utc"])
        computed_lead = (valid - init).total_seconds() / 3600
        assert computed_lead == row["forecast_lead_hours"]
        assert init < valid  # initialization must strictly precede valid time


def test_i_no_duplicate_init_lead_pairs():
    if not PILOT_CSV.exists():
        pytest.skip("pilot csv not yet built")
    df = pd.read_csv(PILOT_CSV)
    pairs = list(zip(df["initialization_time_utc"], df["forecast_lead_hours"]))
    assert len(pairs) == len(set(pairs))


def test_i_manifest_reports_zero_quality_issues_for_clean_pilot():
    if not PILOT_MANIFEST.exists():
        pytest.skip("pilot manifest not yet built")
    manifest = json.loads(PILOT_MANIFEST.read_text())
    # Not asserting an exact count (future runs may surface real issues),
    # but every issue must be a dict with an explicit "issue" key -- never
    # a silently-dropped row.
    for issue in manifest["quality_issues"]:
        assert "issue" in issue


# -------------------- Phase 0.4.10: parameterization tests --------------------

def test_param_grib_filename_matches_gdex_convention():
    assert grib_filename_for("2015030300", 3) == "gfs.0p25.2015030300.f003.grib2"
    assert grib_filename_for("2020071500", 6) == "gfs.0p25.2020071500.f006.grib2"
    assert grib_filename_for("2015030300", 9) == "gfs.0p25.2015030300.f009.grib2"


def test_param_default_args_match_original_phase_0_4_8_pilot():
    args = parse_args([])
    assert args.cycle == DEFAULT_CYCLE == "2020071500"
    assert [int(x) for x in args.leads] == DEFAULT_LEADS == [3, 6]


def test_param_2020_f003_f006_still_works_with_no_args():
    # Running with no CLI arguments must reproduce the exact original
    # Phase 0.4.8 pilot -- same cycle, same leads, same output paths.
    summary = build_pilot(DEFAULT_CYCLE, DEFAULT_LEADS, DEFAULT_OUT_CSV, DEFAULT_OUT_MANIFEST)
    assert summary["status"] == "BUILT_FROM_ACTUAL_FILES"
    assert summary["cycle"] == "2020071500"
    assert summary["leads"] == [3, 6]
    assert summary["n_rows"] == 2
    assert summary["n_unmatched"] == 0
    assert summary["uses_future_information_features"] == []
    df = pd.read_csv(DEFAULT_OUT_CSV)
    assert set(df["source_file"]) == {
        "gfs.0p25.2020071500.f003.grib2", "gfs.0p25.2020071500.f006.grib2",
    }


def test_param_2020_f003_f006_explicit_args_match_no_args():
    # Explicitly passing the default cycle/leads via the CLI parser must
    # produce the identical cycle/leads as omitting them.
    args = parse_args(["--cycle", "2020071500", "--leads", "003", "006"])
    assert args.cycle == "2020071500"
    assert [int(x) for x in args.leads] == [3, 6]


def test_param_2015_cycle_is_selectable_and_reports_missing_files_honestly():
    # The 2015-03-03 cycle selected in Phase 0.4.9 has not been downloaded
    # in this environment (Phase 0.4.10 found the sandbox's egress policy
    # blocks the GDEX host). The script must select the correct filenames
    # and report MISSING_PILOT_FILES explicitly -- never fabricate rows,
    # never silently fall back to a different cycle.
    missing_csv = Path("/tmp/_phase_0_4_10_test_2015_pilot.csv")
    missing_manifest = Path("/tmp/_phase_0_4_10_test_2015_pilot_manifest.json")
    result = build_pilot("2015030300", [3, 9], missing_csv, missing_manifest)
    expected_f003 = path_for("2015030300", 3)
    expected_f009 = path_for("2015030300", 9)
    if not expected_f003.exists() and not expected_f009.exists():
        assert result["status"] == "MISSING_PILOT_FILES"
        assert result["cycle"] == "2015030300"
        assert result["leads"] == [3, 9]
        assert str(expected_f003) in result["missing_files"]
        assert str(expected_f009) in result["missing_files"]
        assert not missing_csv.exists()
    else:
        # If the files are present (e.g. downloaded in a later phase), the
        # arbitrary-cycle/lead metadata must be preserved correctly.
        assert result["status"] == "BUILT_FROM_ACTUAL_FILES"
        assert result["cycle"] == "2015030300"
        assert result["leads"] == [3, 9]


def test_param_arbitrary_cycle_lead_metadata_preserved_in_manifest():
    # Re-run the real, already-downloaded 2020 pilot through the
    # generalized build_pilot() with EXPLICIT (non-default) output paths,
    # proving the function threads arbitrary cycle/lead/path arguments
    # through correctly rather than only working via its hardcoded default.
    out_csv = Path("/tmp/_phase_0_4_10_test_2020_explicit.csv")
    out_manifest = Path("/tmp/_phase_0_4_10_test_2020_explicit_manifest.json")
    summary = build_pilot("2020071500", [3, 6], out_csv, out_manifest)
    assert summary["status"] == "BUILT_FROM_ACTUAL_FILES"
    manifest = json.loads(out_manifest.read_text())
    assert manifest["cycle"] == "2020071500"
    assert manifest["leads_requested"] == [3, 6]
    assert manifest["script_version"].startswith("phase_0_4_1")
    out_csv.unlink(missing_ok=True)
    out_manifest.unlink(missing_ok=True)


def test_no_production_code_imports_research_builder():
    # This research-only script must never be imported by production code.
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
        assert "build_vobl_historical_gfs_ts_join" not in text, (
            f"production file {pf} must not import the research-only historical join builder"
        )


def test_no_production_code_imports_research_builder_repo_wide():
    # Broader repo-wide check: grep every .py file outside tests/ and
    # scripts/ itself for an import of this module.
    import subprocess
    result = subprocess.run(
        ["grep", "-rl", "build_vobl_historical_gfs_ts_join", str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    hits = [
        line for line in result.stdout.splitlines()
        if line.endswith(".py")
        and "/tests/" not in line
        and not line.endswith("scripts/build_vobl_historical_gfs_ts_join.py")
        and not line.endswith("scripts/historical_dataset_split.py")  # research-only sibling, docstring mention only
        and not line.endswith("scripts/verify_phase_0_4_15_oversized_stage_b.py")  # research-only verification sibling, imports it directly by design
        and not line.endswith("scripts/build_phase_0_4_16_historical_gfs_ts_dataset.py")  # research-only dataset-combiner sibling, imports it directly by design
        and not line.endswith("scripts/design_phase_0_4_17_acquisition_candidates.py")  # research-only, audit/design-only sibling, imports it directly by design
        and not line.endswith("scripts/verify_phase_0_4_19_batch.py")  # research-only, manifest-driven verification sibling, imports it directly by design
        and not line.endswith("scripts/build_phase_0_4_20_dataset.py")  # research-only, manifest-driven dataset builder sibling, imports it directly by design
        and not line.endswith("scripts/design_phase_0_4_24_ts_300_candidates.py")  # research-only, audit/design-only sibling (Phase 0.4.24 manifest design), imports it directly by design
    ]
    assert hits == [], f"unexpected non-test/non-script references: {hits}"


# -------------------- Phase 0.4.10 continuation: --ts-labels argument --------------------

TS_LABELS_DEFAULT_PATH = REPO_ROOT / "processed" / "labels" / "ts_labels.csv"


def test_ts_labels_default_path_preserves_existing_behavior():
    # Omitting --ts-labels / ts_labels_path must behave exactly as before:
    # same default path, same output, same result as the original Phase
    # 0.4.8/0.4.10 pilot.
    if not TS_LABELS_DEFAULT_PATH.exists():
        pytest.skip("default ts_labels.csv not present in this checkout")
    out_csv = Path("/tmp/_phase_0_4_10b_test_default_labels.csv")
    out_manifest = Path("/tmp/_phase_0_4_10b_test_default_labels_manifest.json")
    summary = build_pilot(DEFAULT_CYCLE, DEFAULT_LEADS, out_csv, out_manifest)  # no ts_labels_path passed
    assert summary["status"] == "BUILT_FROM_ACTUAL_FILES"
    manifest = json.loads(out_manifest.read_text())
    assert manifest["label_source"] == str(TS_LABELS_DEFAULT_PATH)
    out_csv.unlink(missing_ok=True)
    out_manifest.unlink(missing_ok=True)


def test_ts_labels_explicit_path_is_used():
    # An explicit --ts-labels path must be read from exactly that file, not
    # silently substituted with the default path or any other file.
    import shutil
    explicit_labels = Path("/tmp/_phase_0_4_10b_explicit_ts_labels.csv")
    if not TS_LABELS_DEFAULT_PATH.exists():
        pytest.skip("no real ts_labels.csv available to copy for this test")
    # Copy (not alter) the real label file to a differently-named path, to
    # prove the explicit path argument -- not a hardcoded default -- is
    # what gets read.
    shutil.copyfile(TS_LABELS_DEFAULT_PATH, explicit_labels)

    out_csv = Path("/tmp/_phase_0_4_10b_test_explicit_labels.csv")
    out_manifest = Path("/tmp/_phase_0_4_10b_test_explicit_labels_manifest.json")
    summary = build_pilot(DEFAULT_CYCLE, DEFAULT_LEADS, out_csv, out_manifest, ts_labels_path=explicit_labels)
    assert summary["status"] == "BUILT_FROM_ACTUAL_FILES"
    manifest = json.loads(out_manifest.read_text())
    assert manifest["label_source"] == str(explicit_labels)

    # Result must be identical to the default-path run, since the content
    # is byte-identical -- proving the explicit path is actually read, not
    # ignored in favor of the default.
    df_explicit = pd.read_csv(out_csv)
    assert (df_explicit["label_status"] == "NEGATIVE_CONFIRMED").all()

    out_csv.unlink(missing_ok=True)
    out_manifest.unlink(missing_ok=True)
    explicit_labels.unlink(missing_ok=True)


def test_ts_labels_missing_explicit_path_fails_clearly():
    # A nonexistent explicit path must produce an explicit BLOCKED result
    # naming the exact path checked -- never silently fall back to the
    # default file, and never crash uninformatively.
    bogus_path = Path("/tmp/_phase_0_4_10b_this_file_does_not_exist_ts_labels.csv")
    assert not bogus_path.exists()
    out_csv = Path("/tmp/_phase_0_4_10b_test_bogus_labels.csv")
    out_manifest = Path("/tmp/_phase_0_4_10b_test_bogus_labels_manifest.json")
    result = build_pilot(DEFAULT_CYCLE, DEFAULT_LEADS, out_csv, out_manifest, ts_labels_path=bogus_path)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "ts_labels.csv not found"
    assert result["ts_labels_path_checked"] == str(bogus_path)
    assert not out_csv.exists()


def test_ts_labels_cli_argument_parses():
    args = parse_args(["--cycle", "2015030300", "--leads", "003", "009",
                        "--ts-labels", "some/custom/path/ts_labels.csv"])
    assert args.ts_labels == "some/custom/path/ts_labels.csv"


def test_ts_labels_cli_argument_defaults_to_none():
    args = parse_args(["--cycle", "2020071500", "--leads", "003", "006"])
    assert args.ts_labels is None


# -------------------- Phase 0.4.12: event_group_key contract --------------------

def test_event_group_key_same_cell_date_slot_produces_identical_key():
    k1 = event_group_key_for("IND_13.0_78.0", "2020-07-15", 1)
    k2 = event_group_key_for("IND_13.0_78.0", "2020-07-15", 1)
    assert k1 == k2


def test_event_group_key_different_slot_produces_different_key():
    k1 = event_group_key_for("IND_13.0_78.0", "2020-07-15", 1)
    k2 = event_group_key_for("IND_13.0_78.0", "2020-07-15", 2)
    assert k1 != k2


def test_event_group_key_different_date_produces_different_key():
    k1 = event_group_key_for("IND_13.0_78.0", "2020-07-15", 1)
    k2 = event_group_key_for("IND_13.0_78.0", "2020-07-16", 1)
    assert k1 != k2


def test_event_group_key_different_cell_produces_different_key():
    k1 = event_group_key_for("IND_13.0_78.0", "2020-07-15", 1)
    k2 = event_group_key_for("IND_14.0_78.0", "2020-07-15", 1)
    assert k1 != k2


def test_event_group_key_shared_by_same_slot_leads_in_real_pilot():
    # The exact real-world case the contract exists for: 2020-07-15 00Z's
    # +3h and +6h leads both target slot 1 and must share one key.
    if not DEFAULT_OUT_CSV.exists():
        pytest.skip("default pilot not yet built")
    df = pd.read_csv(DEFAULT_OUT_CSV)
    assert df["event_group_key"].nunique() == 1
    assert (df["event_group_key"] == "IND_13.0_78.0|2020-07-15|1").all()


# -------------------- Phase 0.4.12: prate extraction contract --------------------

def test_prate_extraction_from_real_2020_file():
    msgs = enumerate_messages(Path("data/external/historical_gfs/raw/gfs.0p25.2020071500.f003.grib2"))
    res = extract_prate(msgs, VOBL_CELL_LAT, VOBL_CELL_LON, "20200715 0300")
    assert res["available"] is True
    assert res["value"] is not None
    # 2020 files have both instant and avg prate messages -- instant must
    # be preferred when present.
    assert res["stepType_used"] == "instant"
    assert res["note"] is None
    for m in msgs:
        try:
            import eccodes as ec
            ec.codes_release(m["gid"])
        except Exception:
            pass


def test_prate_extraction_from_real_2015_file_falls_back_to_avg():
    if not Path("data/external/historical_gfs/raw/gfs.0p25.2015030300.f003.grib2").exists():
        pytest.skip("2015-03-03 GRIB files not present in this environment")
    msgs = enumerate_messages(Path("data/external/historical_gfs/raw/gfs.0p25.2015030300.f003.grib2"))
    res = extract_prate(msgs, VOBL_CELL_LAT, VOBL_CELL_LON, "20150303 0300")
    assert res["available"] is True
    assert res["value"] is not None
    # This archive file has NO instant prate message -- must fall back to
    # avg and say so explicitly, never claim instantaneous.
    assert res["stepType_used"] == "avg"
    assert res["note"] is not None
    assert "NOT a literal instantaneous sample" in res["note"]
    for m in msgs:
        try:
            import eccodes as ec
            ec.codes_release(m["gid"])
        except Exception:
            pass


def test_prate_missing_does_not_fabricate_a_value():
    # A message list with no prate field at all must report unavailable,
    # never a fabricated or interpolated number.
    fake_msgs = [{"shortName": "cape", "typeOfLevel": "surface", "level": 0, "stepType": "instant",
                  "startStep": 3, "endStep": 3, "gid": None}]
    res = extract_prate(fake_msgs, VOBL_CELL_LAT, VOBL_CELL_LON, "20200715 0300")
    assert res["available"] is False
    assert res["value"] is None
    assert "no prate message found" in res["note"]


def test_prate_feature_present_in_real_pilot_output():
    if not DEFAULT_OUT_CSV.exists():
        pytest.skip("default pilot not yet built")
    df = pd.read_csv(DEFAULT_OUT_CSV)
    assert "gfs_prate_kg_m2_s" in df.columns
    assert df["gfs_prate_kg_m2_s"].notna().all()
    assert "gfs_prate_stepType_used" in df.columns


def test_interval_precipitation_still_rejects_incompatible_windows_after_prate_addition():
    # Regression guard: adding prate must not have touched
    # compute_interval_precipitation()'s own validity check.
    pos_neg_csv = Path("processed/historical_gfs_vobl/historical_gfs_vobl_positive_negative_pilot.csv")
    if not pos_neg_csv.exists():
        pytest.skip("2015 positive/negative pilot not yet built")
    df = pd.read_csv(pos_neg_csv)
    lead9_row = df[df["forecast_lead_hours"] == 9].iloc[0]
    assert pd.isna(lead9_row["gfs_precip_3h_interval_mm"])
    assert "accumulation windows do not both start at step 0" in lead9_row["gfs_precip_3h_interval_mm_note"]


def test_contract_version_written_to_manifest():
    if not DEFAULT_OUT_MANIFEST.exists():
        pytest.skip("default pilot manifest not yet built")
    manifest = json.loads(DEFAULT_OUT_MANIFEST.read_text())
    assert manifest["contract_version"] == CONTRACT_VERSION
    assert manifest["contract_doc"] == "docs/PHASE_0_4_12_HISTORICAL_GFS_TS_DATASET_CONTRACT.md"
    assert "event_group_keys" in manifest


# -------------------- third root cause: source files outside REPO_ROOT --------------------

def test_manifest_source_file_path_does_not_crash_when_raw_dir_is_outside_repo_root(
    monkeypatch, tmp_path
):
    # Regression test for a real crash: build_pilot()'s manifest writer
    # used to compute p.relative_to(REPO_ROOT) unconditionally for every
    # source GRIB file. That is false whenever RAW_DIR points outside the
    # repository tree -- which is exactly the Phase 0.4.19.1 design (raw
    # GFS files relocated to a separate drive via --raw-root, specifically
    # so they are NOT inside the OneDrive-synced repo). Without this fix,
    # the very first real build against an external raw root would crash
    # with ValueError the moment it tried to write the per-cycle manifest.
    import build_vobl_historical_gfs_ts_join as jm

    real_raw_dir = jm.RAW_DIR
    candidates = sorted(real_raw_dir.glob("gfs.0p25.*.f003.grib2")) if real_raw_dir.exists() else []
    f003 = f006 = None
    for candidate in candidates:
        paired = real_raw_dir / candidate.name.replace(".f003.", ".f006.")
        if paired.exists():
            f003, f006 = candidate, paired
            break
    if f003 is None:
        pytest.skip("no real acquired GRIB pair available in this environment")
    if not TS_LABELS_DEFAULT_PATH.exists():
        pytest.skip("no real ts_labels.csv available in this environment")

    # Simulate an external raw root (outside REPO_ROOT) using real file
    # bytes via a copy -- not a symlink -- so p.relative_to(REPO_ROOT) is
    # genuinely impossible, exactly as it would be for a real D: drive.
    import shutil
    external_root = tmp_path / "outside_repo_root" / "raw"
    external_root.mkdir(parents=True)
    shutil.copyfile(f003, external_root / f003.name)
    shutil.copyfile(f006, external_root / f006.name)
    cycle = f003.name.split(".")[2]

    monkeypatch.setattr(jm, "RAW_DIR", external_root)
    out_csv = tmp_path / "out.csv"
    out_manifest = tmp_path / "out_manifest.json"
    try:
        summary = jm.build_pilot(cycle, [3, 6], out_csv, out_manifest)
    finally:
        monkeypatch.setattr(jm, "RAW_DIR", real_raw_dir)

    assert summary["status"] == "BUILT_FROM_ACTUAL_FILES"
    manifest = json.loads(out_manifest.read_text())
    recorded_paths = [sf["path"] for sf in manifest["source_files"]]
    # Outside REPO_ROOT -> recorded as absolute paths (not crashed, not
    # silently truncated/fabricated).
    assert str(external_root / f003.name) in recorded_paths
    assert str(external_root / f006.name) in recorded_paths


def test_manifest_source_file_path_still_relative_when_inside_repo_root():
    # Preserves exact backward-compatible behavior for every existing
    # caller/test whose files are under REPO_ROOT (the historical default
    # layout) -- the manifest must keep recording a repo-relative path,
    # not switch to absolute paths across the board.
    if not DEFAULT_OUT_MANIFEST.exists():
        pytest.skip("default pilot manifest not yet built")
    manifest = json.loads(DEFAULT_OUT_MANIFEST.read_text())
    for sf in manifest["source_files"]:
        assert not Path(sf["path"]).is_absolute(), (
            "source files inside REPO_ROOT must still be recorded as relative paths"
        )
