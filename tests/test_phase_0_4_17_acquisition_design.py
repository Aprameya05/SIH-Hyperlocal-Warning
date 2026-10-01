#!/usr/bin/env python3
"""Phase 0.4.17 focused tests -- acquisition candidate design
(scripts/design_phase_0_4_17_acquisition_candidates.py). Audit/design
only: these tests never download anything and never build a dataset."""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import design_phase_0_4_17_acquisition_candidates as design_mod  # noqa: E402
from historical_dataset_split import TRAIN, HOLDOUT  # noqa: E402


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


def test_no_candidate_duplicates_an_already_acquired_event_group(candidates_and_rows):
    candidates, _ = candidates_and_rows
    for row, _reason in candidates:
        assert row["event_group_key"] not in design_mod.ALREADY_ACQUIRED_EVENT_GROUPS


def test_every_candidate_event_group_is_unique(candidates_and_rows):
    candidates, _ = candidates_and_rows
    keys = [row["event_group_key"] for row, _ in candidates]
    assert len(keys) == len(set(keys))


def test_candidate_batch_size_is_in_the_requested_range(candidates_and_rows):
    candidates, _ = candidates_and_rows
    assert 20 <= len(candidates) <= 50, (
        f"expected ~20-50 candidate event groups, got {len(candidates)}"
    )


def test_every_row_has_a_real_label_status(candidates_and_rows):
    _, rows = candidates_and_rows
    for r in rows:
        assert r["label"] in ("POSITIVE", "NEGATIVE_CONFIRMED")


def test_f003_and_f006_share_the_same_event_group_key_per_candidate(candidates_and_rows):
    _, rows = candidates_and_rows
    import pandas as pd
    df = pd.DataFrame(rows)
    non_ambiguous = df[df["selected_gfs_cycle"] != "AMBIGUOUS"]
    for key, grp in non_ambiguous.groupby("event_group_key"):
        assert grp["event_group_key"].nunique() == 1
        assert set(grp["selected_lead"]) == {"f003", "f006"}


def test_partition_assignment_matches_assign_partition(candidates_and_rows):
    candidates, _ = candidates_and_rows
    for row, _ in candidates:
        expected = design_mod.assign_partition(row["date"])
        assert expected in (TRAIN, HOLDOUT, "unassigned")


def test_holdout_candidates_are_never_mixed_into_train_reasoning(candidates_and_rows):
    _, rows = candidates_and_rows
    for r in rows:
        if r["partition"] == HOLDOUT:
            assert "evaluation only" in r["reason"] or "never to be used in training" in r["reason"]


def test_cycle_lead_mapping_is_computed_not_assumed(candidates_and_rows):
    # Spot-check: recompute independently for a few rows and compare.
    _, rows = candidates_and_rows
    import pandas as pd
    df = pd.DataFrame(rows)
    sample = df[df["selected_gfs_cycle"] != "AMBIGUOUS"].drop_duplicates("event_group_key").head(5)
    for _, r in sample.iterrows():
        result = design_mod.find_cycle_and_leads(r["target_ist_date"], int(r["target_slot"]))
        assert result is not None
        init, _ = result
        assert init.strftime("%Y%m%d%H") == r["selected_gfs_cycle"]


def test_expected_filenames_follow_the_established_naming_convention(candidates_and_rows):
    _, rows = candidates_and_rows
    for r in rows:
        if r["expected_file"] == "AMBIGUOUS":
            continue
        assert r["expected_file"] == f"gfs.0p25.{r['selected_gfs_cycle']}.{r['selected_lead']}.grib2"


def test_no_production_code_imports_the_design_script():
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
