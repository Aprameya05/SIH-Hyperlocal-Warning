#!/usr/bin/env python3
"""Phase 0.4.12 focused tests for scripts/historical_dataset_split.py
(year-based split contract + event_group_key grouping-constraint
validator)."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from historical_dataset_split import (  # noqa: E402
    assign_partition, validate_no_event_group_key_crosses_partitions,
    assign_and_validate, year_of_ist_date,
    TRAIN, HOLDOUT, UNASSIGNED, DEFAULT_TRAIN_YEARS, DEFAULT_HOLDOUT_YEARS,
)


def test_year_of_ist_date():
    assert year_of_ist_date("2020-07-15") == 2020
    assert year_of_ist_date("2015-03-03") == 2015


def test_assign_partition_train_range():
    assert assign_partition("2015-03-03") == TRAIN
    assert assign_partition("2023-12-31") == TRAIN


def test_assign_partition_holdout_range():
    assert assign_partition("2024-01-01") == HOLDOUT
    assert assign_partition("2025-12-31") == HOLDOUT


def test_assign_partition_unassigned_outside_both_ranges():
    assert assign_partition("2014-01-01") == UNASSIGNED
    assert assign_partition("2026-01-01") == UNASSIGNED


def test_assign_partition_is_deterministic():
    # Same input must always produce the same output -- no randomness.
    results = {assign_partition("2020-07-15") for _ in range(20)}
    assert results == {TRAIN}


def test_assign_partition_custom_year_bounds():
    assert assign_partition("2019-01-01", train_years=(2018, 2019), holdout_years=(2020, 2020)) == TRAIN
    assert assign_partition("2020-01-01", train_years=(2018, 2019), holdout_years=(2020, 2020)) == HOLDOUT


# -------------------- grouping-constraint validator --------------------

def test_split_validator_passes_when_no_key_crosses_partitions():
    keys = ["A|2020-07-15|1", "A|2020-07-15|1", "B|2015-03-03|2"]
    partitions = [TRAIN, TRAIN, HOLDOUT]
    result = validate_no_event_group_key_crosses_partitions(keys, partitions)
    assert result.ok
    assert result.n_rows == 3
    assert result.n_event_group_keys == 2
    assert result.violating_keys == {}


def test_split_validator_rejects_a_key_crossing_partitions():
    # The exact scenario the contract exists to prevent: the same event
    # (same cell/date/slot) assigned to two different partitions.
    keys = ["A|2020-07-15|1", "A|2020-07-15|1"]
    partitions = [TRAIN, HOLDOUT]
    result = validate_no_event_group_key_crosses_partitions(keys, partitions)
    assert not result.ok
    assert "A|2020-07-15|1" in result.violating_keys
    assert result.violating_keys["A|2020-07-15|1"] == [HOLDOUT, TRAIN]


def test_split_validator_length_mismatch_raises():
    import pytest
    with pytest.raises(ValueError):
        validate_no_event_group_key_crosses_partitions(["A"], ["train", "holdout"])


def test_assign_and_validate_end_to_end_with_real_pilot_shaped_rows():
    # Mirrors the exact row shape scripts/build_vobl_historical_gfs_ts_join.py
    # produces: two leads of one cycle sharing the same event_group_key.
    rows = [
        {"ist_date": "2020-07-15", "event_group_key": "IND_13.0_78.0|2020-07-15|1"},
        {"ist_date": "2020-07-15", "event_group_key": "IND_13.0_78.0|2020-07-15|1"},
        {"ist_date": "2015-03-03", "event_group_key": "IND_13.0_78.0|2015-03-03|1"},
        {"ist_date": "2015-03-03", "event_group_key": "IND_13.0_78.0|2015-03-03|2"},
    ]
    result = assign_and_validate(rows)
    assert result["partitions"] == [TRAIN, TRAIN, TRAIN, TRAIN]
    assert result["validation"]["ok"] is True
    assert result["validation"]["n_event_group_keys"] == 3  # the 2020 pair shares one key


def test_default_year_bounds_match_contract_document():
    assert DEFAULT_TRAIN_YEARS == (2015, 2023)
    assert DEFAULT_HOLDOUT_YEARS == (2024, 2025)


def test_no_production_code_imports_research_split_utility():
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
        assert "historical_dataset_split" not in text, (
            f"production file {pf} must not import the research-only split utility"
        )
