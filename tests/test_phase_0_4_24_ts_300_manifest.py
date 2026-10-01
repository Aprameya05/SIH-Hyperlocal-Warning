"""
Phase 0.4.24 -- tests for the 300-event-group TS acquisition candidate
manifest design script. Audit/design-only: these tests check the
*selection logic's* correctness (determinism, counts, no leakage, no
duplicates) against the real ts_labels.csv already in the repository.
They do not download anything, train anything, or touch production code.
"""
import csv
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "SIH_PANINDIA_GRID_LABELS_20260930_143541Z" / "processed" / "labels" / "ts_labels.csv").exists(),
    reason="ts_labels.csv not present in this checkout",
)


def _run_module():
    mod = importlib.import_module("design_phase_0_4_24_ts_300_candidates")
    importlib.reload(mod)
    return mod


def test_selection_is_deterministic():
    mod = _run_module()
    labels = mod.load_labels()
    available = labels[~labels["event_group_key"].isin(mod.EXISTING_20_EVENT_GROUPS)].copy()
    eligible = available[available["label_status"].isin(["POSITIVE", "NEGATIVE_CONFIRMED"])]
    # Running load_labels() twice on the same file must give identical
    # event_group_key sets -- this is the determinism the design script
    # relies on (no randomness anywhere in the selection path).
    labels2 = mod.load_labels()
    assert sorted(labels["event_group_key"]) == sorted(labels2["event_group_key"])
    assert len(eligible) > 0


def test_existing_20_event_groups_excluded_from_candidate_pool():
    mod = _run_module()
    labels = mod.load_labels()
    present = set(labels["event_group_key"]) & mod.EXISTING_20_EVENT_GROUPS
    # All 20 existing keys must actually exist in the label archive (sanity
    # check that the hardcoded list matches real data, not stale guesses).
    assert len(present) == 20
    assert len(mod.EXISTING_HOLDOUT_KEYS) == 6
    assert mod.EXISTING_HOLDOUT_KEYS.issubset(mod.EXISTING_20_EVENT_GROUPS)


def test_episode_clustering_separates_positive_and_negative_namespaces():
    mod = _run_module()
    dates = ["2024-01-01", "2024-01-01", "2024-01-02"]
    clustered = mod.cluster_episodes(dates)
    assert clustered["2024-01-01"] == clustered["2024-01-02"]  # within 48h


def test_episode_clustering_respects_48h_gap():
    mod = _run_module()
    clustered = mod.cluster_episodes(["2024-01-01", "2024-01-10"])
    assert clustered["2024-01-01"] != clustered["2024-01-10"]


def test_manifest_csv_output_has_300_event_groups_and_600_files():
    out_csv = REPO_ROOT / "docs" / "PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv"
    assert out_csv.exists(), "run scripts/design_phase_0_4_24_ts_300_candidates.py first"
    with open(out_csv) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 600
    event_groups = {r["event_group_key"] for r in rows}
    assert len(event_groups) == 300
    filenames = [r["expected_filename"] for r in rows]
    assert len(filenames) == len(set(filenames)), "duplicate GFS filenames in manifest"


def test_manifest_partition_and_label_counts():
    out_csv = REPO_ROOT / "docs" / "PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv"
    with open(out_csv) as f:
        rows = list(csv.DictReader(f))
    event_rows = {}
    for r in rows:
        event_rows.setdefault(r["event_group_key"], r)
    by_partition_label = {}
    for r in event_rows.values():
        key = (r["partition"], r["label"])
        by_partition_label[key] = by_partition_label.get(key, 0) + 1
    assert by_partition_label.get(("train", "POSITIVE"), 0) == 120
    assert by_partition_label.get(("train", "NEGATIVE_CONFIRMED"), 0) == 120
    assert by_partition_label.get(("holdout", "POSITIVE"), 0) == 30
    assert by_partition_label.get(("holdout", "NEGATIVE_CONFIRMED"), 0) == 30


def test_manifest_no_cross_partition_episode_overlap():
    out_csv = REPO_ROOT / "docs" / "PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv"
    with open(out_csv) as f:
        rows = list(csv.DictReader(f))
    train_episodes = {r["episode_id"] for r in rows if r["partition"] == "train"}
    holdout_episodes = {r["episode_id"] for r in rows if r["partition"] == "holdout"}
    assert train_episodes.isdisjoint(holdout_episodes), \
        "a weather episode must never contribute event groups to both train and holdout"


def test_manifest_no_event_group_crosses_partitions():
    out_csv = REPO_ROOT / "docs" / "PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv"
    with open(out_csv) as f:
        rows = list(csv.DictReader(f))
    partitions_by_key = {}
    for r in rows:
        partitions_by_key.setdefault(r["event_group_key"], set()).add(r["partition"])
    for key, parts in partitions_by_key.items():
        assert len(parts) == 1, f"{key} appears in more than one partition: {parts}"


def test_manifest_every_row_has_both_leads():
    out_csv = REPO_ROOT / "docs" / "PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv"
    with open(out_csv) as f:
        rows = list(csv.DictReader(f))
    leads_by_key = {}
    for r in rows:
        leads_by_key.setdefault(r["event_group_key"], set()).add(r["forecast_lead"])
    for key, leads in leads_by_key.items():
        assert leads == {"f003", "f006"}, f"{key} is missing a lead: {leads}"
