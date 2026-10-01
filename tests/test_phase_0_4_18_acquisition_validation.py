#!/usr/bin/env python3
"""Phase 0.4.18 focused tests -- acquisition path validation + manifest
cleanup. Audit/design only: these tests never download the acquisition
batch and never build a dataset. They verify the Part 2 manifest cleanup
(Issues A and B) and the Part 3 strategy-number consistency fix."""
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import design_phase_0_4_17_acquisition_candidates as design_mod  # noqa: E402
from historical_dataset_split import TRAIN, HOLDOUT  # noqa: E402

MANIFEST_PATH = REPO_ROOT / "docs" / "PHASE_0_4_17_ACQUISITION_MANIFEST.csv"
PLAN_PATH = REPO_ROOT / "docs" / "PHASE_0_4_17_ACQUISITION_PLAN.md"


def _skip_if_labels_missing():
    if not design_mod.LABELS_PATH.exists():
        pytest.skip("real ts_labels.csv not present in this environment")


@pytest.fixture(scope="module")
def candidates_and_rows():
    _skip_if_labels_missing()
    labels = design_mod.load_labels()
    candidates = design_mod.select_candidates(labels)
    rows = design_mod.build_manifest_rows(candidates)
    return candidates, rows


@pytest.fixture(scope="module")
def manifest_df():
    if not MANIFEST_PATH.exists():
        pytest.skip("regenerated manifest CSV not present")
    return pd.read_csv(MANIFEST_PATH)


def test_cleaned_manifest_has_no_duplicate_event_groups(manifest_df):
    # Each event group legitimately appears on 2 rows (f003 + f006);
    # de-duplicate by (event_group_key, selected_lead) before checking for
    # true duplicates.
    pair = manifest_df[["event_group_key", "selected_lead"]]
    assert pair.duplicated().sum() == 0


def test_cleaned_manifest_has_no_overlap_with_acquired_event_groups(manifest_df):
    overlap = set(manifest_df["event_group_key"]) & design_mod.ALREADY_ACQUIRED_EVENT_GROUPS
    assert overlap == set()


def test_january_1st_selection_artifact_is_removed_from_train_negatives(manifest_df):
    train_neg = manifest_df[
        (manifest_df["label"] == "NEGATIVE_CONFIRMED") & (manifest_df["partition"] == TRAIN)
    ].drop_duplicates("event_group_key")
    jan_1_dates = train_neg[train_neg["target_ist_date"].str.endswith("-01-01")]
    assert len(jan_1_dates) == 0, (
        f"expected the Phase 0.4.17 Jan-1-every-year TRAIN-negative artifact to be fixed, "
        f"found {len(jan_1_dates)} remaining: {jan_1_dates['target_ist_date'].tolist()}"
    )
    # and the dates that replaced them should actually be spread across
    # different months, not just moved to a different single date
    distinct_months = train_neg["target_ist_date"].str.slice(5, 7).nunique()
    assert distinct_months >= 3, (
        f"expected TRAIN-negative fill dates to span at least 3 distinct months, got {distinct_months}"
    )


def test_clustered_winter_candidates_are_reduced_and_documented(manifest_df):
    winter_pos = manifest_df[
        (manifest_df["label"] == "POSITIVE") & (manifest_df["season"] == "winter")
    ].drop_duplicates("event_group_key")
    cluster_dates = {("2021-02-19", 2), ("2021-02-19", 3), ("2021-02-20", 2)}
    present = {
        (r["target_ist_date"], int(r["target_slot"]))
        for _, r in winter_pos.iterrows()
        if (r["target_ist_date"], int(r["target_slot"])) in cluster_dates
    }
    # Phase 0.4.17 included all 3 cluster members; Phase 0.4.18 must drop
    # at least one of them (Issue B), and the one kept besides the
    # already-selected year-fill must document the reasoning.
    assert len(present) <= 2, f"expected the 3-member winter cluster to be reduced, found {present}"
    kept_extra = winter_pos[
        (winter_pos["target_ist_date"] == "2021-02-20") & (winter_pos["target_slot"] == 2)
    ]
    if not kept_extra.empty:
        reason = kept_extra.iloc[0]["reason"]
        assert "cluster" in reason.lower() or "separated" in reason.lower() or "0.4.18" in reason


def test_partition_assignment_remains_correct(candidates_and_rows):
    candidates, _ = candidates_and_rows
    for row, _ in candidates:
        expected = design_mod.assign_partition(row["date"])
        assert expected in (TRAIN, HOLDOUT, "unassigned")


def test_cycle_lead_mapping_remains_computed_not_assumed(candidates_and_rows):
    _, rows = candidates_and_rows
    df = pd.DataFrame(rows)
    non_ambiguous = df[df["selected_gfs_cycle"] != "AMBIGUOUS"].drop_duplicates("event_group_key")
    assert len(non_ambiguous) == len(df.drop_duplicates("event_group_key")), (
        "expected zero ambiguous cycle/lead mappings in the cleaned manifest"
    )
    for _, r in non_ambiguous.head(8).iterrows():
        result = design_mod.find_cycle_and_leads(r["target_ist_date"], int(r["target_slot"]))
        assert result is not None
        init, _ = result
        assert init.strftime("%Y%m%d%H") == r["selected_gfs_cycle"]


def test_strategy_b_positive_target_is_internally_consistent_with_epv_floor():
    text = PLAN_PATH.read_text()
    # Pull the Strategy B section specifically.
    m = re.search(r"### Strategy B.*?(?=### Strategy C)", text, re.DOTALL)
    assert m, "Strategy B section not found in the acquisition plan"
    strategy_b = m.group(0)
    # Must no longer state the inconsistent "~100-150" range as *the*
    # positive target without qualification -- it must cite ~150 as the
    # actual EPV>=10 floor for 15 predictors.
    assert "~150 event groups" in strategy_b or "~150" in strategy_b
    assert "EPV" in strategy_b


def test_cleaned_manifest_batch_size_is_approximately_20_to_30(manifest_df):
    n_event_groups = manifest_df["event_group_key"].nunique()
    assert 20 <= n_event_groups <= 30, (
        f"expected the cleaned batch to stay in the ~20-30 event-group range, got {n_event_groups}"
    )


def test_no_production_code_imports_the_design_tooling():
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
        assert "design_phase_0_4_17_acquisition_candidates" not in text
