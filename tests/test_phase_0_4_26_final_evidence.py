"""
Regression tests for scripts/phase_0_4_26_final_evidence.py -- the Gate 1
deterministic final-assembly replacement for the buggy notebook Section
17/18 logic (which incorrectly treated event_group_key as a unique row
key). These are the specific cases called out in the Gate 1 close-out:

  - group_A + f003, group_A + f006                       -> PASS
  - group_A + f003, group_A + f003 (duplicate lead)       -> FAIL
  - only one row for group_A                              -> FAIL
  - group_A's two rows disagree on partition               -> FAIL
  - gfs_precip_3h_interval_mm null on the earlier lead     -> PASS (expected, not incompleteness)

Pure pandas, no filesystem, no notebook, no Drive -- exercises
assemble_and_validate() directly so these run anywhere.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from phase_0_4_26_final_evidence import (  # noqa: E402
    AssemblyFailure, FEATURE_COLUMNS, PER_LEAD_ONLY_COLUMN, assemble_and_validate,
    manifest_authoritative_groups, sha256_of_dataframe,
)

ROW_FEATURE_COLUMNS = [c for c in FEATURE_COLUMNS if c != PER_LEAD_ONLY_COLUMN]


def _manifest_row(event_group_key, label, partition, forecast_lead):
    return {
        "event_group_key": event_group_key, "label": label, "partition": partition,
        "forecast_lead": forecast_lead, "target_ist_date": event_group_key.split("|")[1],
    }


def make_manifest(groups):
    """groups: list of (event_group_key, label_status, partition) -- always emits both f003/f006 rows."""
    rows = []
    for egk, label, partition in groups:
        rows.append(_manifest_row(egk, label, partition, "f003"))
        rows.append(_manifest_row(egk, label, partition, "f006"))
    return pd.DataFrame(rows)


def _acquired_row(event_group_key, lead_hours, label_status, partition, interval_value):
    row = {
        "event_group_key": event_group_key,
        "forecast_lead_hours": lead_hours,
        "label": 1 if label_status == "POSITIVE" else 0,
        "label_status": label_status,
        "train_holdout_partition": partition,
    }
    for col in ROW_FEATURE_COLUMNS:
        row[col] = 1.0
    row[PER_LEAD_ONLY_COLUMN] = interval_value
    return row


def make_valid_pair(event_group_key, label_status="POSITIVE", partition="train"):
    """A correct, complete event group: f003 (interval null, as always expected) + f006 (interval set)."""
    return [
        _acquired_row(event_group_key, 3, label_status, partition, interval_value=None),
        _acquired_row(event_group_key, 6, label_status, partition, interval_value=2.5),
    ]


def test_valid_f003_f006_pair_passes():
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    acquired_df = pd.DataFrame(make_valid_pair("IND_13.0_78.0|2024-01-01|1"))

    result = assemble_and_validate(manifest_df, acquired_df)

    assert result["evidence"]["acquired_groups_clean_total"] == 1
    assert result["evidence"]["rows_total"] == 2
    assert result["evidence"]["missing_groups"] == []
    assert len(result["train_df"]) == 2


def test_duplicate_lead_fails():
    # group_A + f003, group_A + f003 (same lead twice) instead of f003+f006.
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    rows = [
        _acquired_row("IND_13.0_78.0|2024-01-01|1", 3, "POSITIVE", "train", interval_value=None),
        _acquired_row("IND_13.0_78.0|2024-01-01|1", 3, "POSITIVE", "train", interval_value=None),
    ]
    acquired_df = pd.DataFrame(rows)

    with pytest.raises(AssemblyFailure, match="lead pair"):
        assemble_and_validate(manifest_df, acquired_df)


def test_only_one_row_for_group_fails():
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    acquired_df = pd.DataFrame([
        _acquired_row("IND_13.0_78.0|2024-01-01|1", 3, "POSITIVE", "train", interval_value=None),
    ])

    with pytest.raises(AssemblyFailure, match="exactly 2 rows"):
        assemble_and_validate(manifest_df, acquired_df)


def test_two_rows_different_partitions_fails():
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    rows = [
        _acquired_row("IND_13.0_78.0|2024-01-01|1", 3, "POSITIVE", "train", interval_value=None),
        _acquired_row("IND_13.0_78.0|2024-01-01|1", 6, "POSITIVE", "holdout", interval_value=2.5),
    ]
    acquired_df = pd.DataFrame(rows)

    with pytest.raises(AssemblyFailure, match="partition"):
        assemble_and_validate(manifest_df, acquired_df)


def test_expected_null_interval_on_earlier_lead_passes():
    # This is the exact bug: the old Section 18 logic flagged the f003 row's
    # gfs_precip_3h_interval_mm=None as "incomplete feature row". It is not.
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    acquired_df = pd.DataFrame(make_valid_pair("IND_13.0_78.0|2024-01-01|1"))

    result = assemble_and_validate(manifest_df, acquired_df)

    assert "FAILED" not in result["evidence"]["feature_completeness_status"]
    f003_row = acquired_df[acquired_df["forecast_lead_hours"] == 3].iloc[0]
    assert pd.isna(f003_row[PER_LEAD_ONLY_COLUMN])


def test_missing_interval_on_later_lead_fails():
    # The later lead (f006) is REQUIRED to have gfs_precip_3h_interval_mm -- if it's
    # also null, that is a real completeness failure, not the expected-null case.
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    rows = [
        _acquired_row("IND_13.0_78.0|2024-01-01|1", 3, "POSITIVE", "train", interval_value=None),
        _acquired_row("IND_13.0_78.0|2024-01-01|1", 6, "POSITIVE", "train", interval_value=None),
    ]
    acquired_df = pd.DataFrame(rows)

    with pytest.raises(AssemblyFailure, match="LATER lead"):
        assemble_and_validate(manifest_df, acquired_df)


def test_missing_event_group_is_reported_not_a_failure():
    # Two groups in the manifest, only one acquired -- must NOT raise, must report
    # the missing one explicitly (the Gate 1 "no silent dropping" requirement).
    manifest_df = make_manifest([
        ("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train"),
        ("IND_13.0_78.0|2015-01-03|1", "NEGATIVE_CONFIRMED", "train"),
    ])
    acquired_df = pd.DataFrame(make_valid_pair("IND_13.0_78.0|2024-01-01|1"))

    result = assemble_and_validate(manifest_df, acquired_df)

    assert result["evidence"]["missing_groups"] == ["IND_13.0_78.0|2015-01-03|1"]
    assert result["evidence"]["missing_groups_total"] == 1
    assert result["evidence"]["acquired_groups_clean_total"] == 1


def test_unexpected_event_group_not_in_manifest_fails():
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    rows = make_valid_pair("IND_13.0_78.0|2024-01-01|1") + make_valid_pair("IND_99.0_99.0|1999-01-01|0")
    acquired_df = pd.DataFrame(rows)

    with pytest.raises(AssemblyFailure, match="do not appear in the authoritative manifest"):
        assemble_and_validate(manifest_df, acquired_df)


def test_label_disagreement_with_manifest_fails():
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    acquired_df = pd.DataFrame(make_valid_pair("IND_13.0_78.0|2024-01-01|1", label_status="NEGATIVE_CONFIRMED"))

    with pytest.raises(AssemblyFailure, match="label_status that disagrees"):
        assemble_and_validate(manifest_df, acquired_df)


def test_unknown_label_status_fails():
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    rows = [
        _acquired_row("IND_13.0_78.0|2024-01-01|1", 3, "UNKNOWN", "train", interval_value=None),
        _acquired_row("IND_13.0_78.0|2024-01-01|1", 6, "UNKNOWN", "train", interval_value=2.5),
    ]
    acquired_df = pd.DataFrame(rows)

    with pytest.raises(AssemblyFailure):
        assemble_and_validate(manifest_df, acquired_df)


def test_no_event_group_crosses_train_holdout_in_final_output():
    manifest_df = make_manifest([
        ("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train"),
        ("IND_13.0_78.0|2025-01-01|1", "NEGATIVE_CONFIRMED", "holdout"),
    ])
    rows = make_valid_pair("IND_13.0_78.0|2024-01-01|1", partition="train")
    rows += make_valid_pair("IND_13.0_78.0|2025-01-01|1", "NEGATIVE_CONFIRMED", partition="holdout")
    acquired_df = pd.DataFrame(rows)

    result = assemble_and_validate(manifest_df, acquired_df)

    train_keys = set(result["train_df"]["event_group_key"])
    holdout_keys = set(result["holdout_df"]["event_group_key"])
    assert train_keys.isdisjoint(holdout_keys)
    assert train_keys == {"IND_13.0_78.0|2024-01-01|1"}
    assert holdout_keys == {"IND_13.0_78.0|2025-01-01|1"}


def test_deterministic_fingerprint_is_order_independent():
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    rows = make_valid_pair("IND_13.0_78.0|2024-01-01|1")
    df_a = pd.DataFrame(rows)
    df_b = pd.DataFrame(list(reversed(rows)))  # same data, different row order

    assert sha256_of_dataframe(df_a) == sha256_of_dataframe(df_b)


def test_manifest_authoritative_groups_rejects_internally_inconsistent_manifest():
    bad_manifest = pd.DataFrame([
        _manifest_row("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train", "f003"),
        _manifest_row("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "holdout", "f006"),  # inconsistent partition
    ])
    with pytest.raises(AssemblyFailure, match="inconsistent partition"):
        manifest_authoritative_groups(bad_manifest)


def test_evidence_never_claims_green_with_a_missing_interval_mislabel():
    # Full end-to-end sanity: a dataset that is entirely correct except for the
    # exact historical bug (treating the expected null as incompleteness) must
    # never resurface that bug -- PASS, with the completeness status explicitly
    # mentioning the per-lead-only column by name, not a generic "all present".
    manifest_df = make_manifest([("IND_13.0_78.0|2024-01-01|1", "POSITIVE", "train")])
    acquired_df = pd.DataFrame(make_valid_pair("IND_13.0_78.0|2024-01-01|1"))
    result = assemble_and_validate(manifest_df, acquired_df)
    assert PER_LEAD_ONLY_COLUMN in result["evidence"]["feature_completeness_status"]
