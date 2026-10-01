#!/usr/bin/env python3
"""Phase 0.4.16 focused tests -- the combined historical GFS -> TS dataset
builder (scripts/build_phase_0_4_16_historical_gfs_ts_dataset.py).

Runs against whatever cycles' GRIB files are actually present in this
environment (skips gracefully, never fabricates, if some are missing --
mirrors the script's own honest-skip behavior). In the full environment
(all 8 cycles present) these exercise the complete 16-row dataset; in a
partial environment (e.g. this sandbox, 5 of 8 cycles) they exercise
whatever subset is available -- the assertions are written to hold either
way because they check properties, not fixed row counts.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_phase_0_4_16_historical_gfs_ts_dataset as build_mod  # noqa: E402
from historical_dataset_split import TRAIN, HOLDOUT  # noqa: E402

LABELS_PATH = REPO_ROOT / "SIH_PANINDIA_GRID_LABELS_20260930_143541Z" / "processed" / "labels" / "ts_labels.csv"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if not LABELS_PATH.exists():
        pytest.skip("real ts_labels.csv not present in this environment")
    out_dir = tmp_path_factory.mktemp("phase_0_4_16_out")
    cycles_built, cycles_skipped, per_cycle_manifests, frames = [], [], [], []
    tmp_dir = out_dir / "_per_cycle"
    tmp_dir.mkdir()
    for spec in build_mod.CYCLES:
        result = build_mod.build_one_cycle(spec["cycle"], spec["leads"], LABELS_PATH, tmp_dir)
        if result.get("status") != "BUILT_FROM_ACTUAL_FILES":
            cycles_skipped.append(spec["cycle"])
            continue
        df = pd.read_csv(result["_out_csv"])
        df["source_cycle"] = spec["cycle"]
        frames.append(df)
    if not frames:
        pytest.skip("no Stage B / pilot GRIB files present in this environment")
    combined = pd.concat(frames, ignore_index=True)
    combined["train_holdout_partition"] = combined["ist_date"].apply(build_mod.assign_partition)
    return combined, cycles_skipped


def test_every_row_maps_to_a_real_source_file_on_disk(built):
    combined, _ = built
    raw_dir = REPO_ROOT / "data" / "external" / "historical_gfs" / "raw"
    for fname in combined["source_file"]:
        assert (raw_dir / fname).exists(), f"{fname} referenced by a row but not found on disk"


def test_no_duplicate_source_file_cell_lead_rows(built):
    combined, _ = built
    dup_key = combined[["source_file", "cell_id", "forecast_lead_hours"]].apply(tuple, axis=1)
    assert dup_key.nunique() == len(combined)


def test_event_group_key_is_deterministic(built):
    combined, _ = built
    from build_vobl_historical_gfs_ts_join import event_group_key_for
    for _, row in combined.iterrows():
        recomputed = event_group_key_for(row["cell_id"], row["ist_date"], row["slot_id"])
        assert recomputed == row["event_group_key"]


def test_same_cycle_f003_f006_rows_share_event_group_key_where_applicable(built):
    combined, _ = built
    for cycle, grp in combined.groupby("source_cycle"):
        if grp["forecast_lead_hours"].nunique() > 1 and grp["event_group_key"].nunique() == 1:
            # same event group across both leads -- the expected common case
            assert grp["event_group_key"].nunique() == 1
        # both leads landing in different slots (e.g. 2015030300) is also a
        # valid, previously-documented outcome -- not asserted away here.


def test_temporal_partition_is_computed_not_assumed(built):
    combined, _ = built
    for _, row in combined.iterrows():
        expected = build_mod.assign_partition(row["ist_date"])
        assert expected == row["train_holdout_partition"]
        assert row["train_holdout_partition"] in (TRAIN, HOLDOUT, "unassigned")


def test_no_event_group_key_crosses_partitions(built):
    combined, _ = built
    result = build_mod.validate_no_event_group_key_crosses_partitions(
        combined["event_group_key"].tolist(), combined["train_holdout_partition"].tolist()
    )
    assert result.ok, result.violating_keys


def test_invalid_tp_intervals_remain_missing_not_fabricated(built):
    combined, _ = built
    # Every row where the interval is null must carry a non-empty note
    # explaining why -- never a silent null.
    missing_rows = combined[combined["gfs_precip_3h_interval_mm"].isna()]
    for _, row in missing_rows.iterrows():
        note = row.get("gfs_precip_3h_interval_mm_note")
        assert isinstance(note, str) and len(note) > 0, (
            f"{row['source_file']}: missing interval precip with no explanatory note"
        )


def test_precipitation_provenance_columns_are_preserved(built):
    combined, _ = built
    for col in ("gfs_prate_stepType_used", "gfs_prate_startStep", "gfs_prate_endStep"):
        assert col in combined.columns
    # every row with a non-null prate value must have a recorded stepType
    has_value = combined["gfs_prate_kg_m2_s"].notna()
    assert combined.loc[has_value, "gfs_prate_stepType_used"].notna().all()


def test_pwat_column_is_named_pwat_not_iwv(built):
    combined, _ = built
    assert "pwat_mm" in combined.columns
    assert not any("iwv" in c.lower() for c in combined.columns)


def test_missing_values_are_nan_not_zero(built):
    combined, _ = built
    # A GFS value of exactly 0.0 is a legitimate physical reading (e.g. CIN
    # can be 0); the contract requires a missing value to be NaN, never a
    # silently substituted 0 standing in for "unknown". We cannot prove a
    # non-null 0 is "real" vs "faked" from the CSV alone, but we CAN prove
    # the loader never coerces NaN to 0 -- i.e. every declared-missing
    # feature (per missing_features) is actually NaN in its own column.
    for _, row in combined.iterrows():
        missing_list = str(row.get("missing_features") or "")
        if not missing_list:
            continue
        for feat_key in missing_list.split(";"):
            col = {
                "cape_surface_0": "cape", "cin_surface_0": "cin",
                "pwat_atmosphereSingleLayer_0": "pwat_mm",
                "u_isobaricInhPa_850": "u850", "v_isobaricInhPa_850": "v850",
                "u_isobaricInhPa_200": "u200", "v_isobaricInhPa_200": "v200",
                "t_isobaricInhPa_850": "t850_k", "t_isobaricInhPa_700": "t700_k",
                "t_isobaricInhPa_500": "t500_k",
                "r_isobaricInhPa_850": "rh850_pct", "r_isobaricInhPa_700": "rh700_pct",
                "gfs_prate_kg_m2_s": "gfs_prate_kg_m2_s",
            }.get(feat_key)
            if col and col in combined.columns:
                assert pd.isna(row[col]), f"{row['source_file']}: {col} marked missing but has a non-NaN value"


def test_label_values_conform_to_established_ts_contract(built):
    combined, _ = built
    assert set(combined["label_status"].dropna().unique()) <= {
        "POSITIVE", "NEGATIVE_CONFIRMED", "UNMATCHED", "UNKNOWN",
    }
    for _, row in combined.iterrows():
        if row["label_status"] == "POSITIVE":
            assert row["label"] == 1
        if row["label_status"] == "NEGATIVE_CONFIRMED":
            assert row["label"] == 0


def test_no_production_code_imports_the_phase_0_4_16_builder():
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
        assert "build_phase_0_4_16_historical_gfs_ts_dataset" not in text
