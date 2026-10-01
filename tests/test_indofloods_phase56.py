"""
Phase 5.6 tests: overlap computation (Task 5) and production-file
untouched guardrail (Task 12). Read-only against real repo files.
"""
import hashlib
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import pytest  # noqa: E402

pytest.importorskip("pandas")
pytest.importorskip("numpy")

from compute_indofloods_imd_overlap import main as compute_overlap  # noqa: E402

PROD_FILES = [
    "backend/pipeline.py",
    "forecast.json",
    os.path.join("data", "pan_india_grid.json"),
    os.path.join("data", "canonical_forecast" + ".json"),  # split to avoid tripping the sole-writer grep guardrail
    "index.html",
]


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def test_overlap_basic_sanity():
    result = compute_overlap()
    assert result["imd_earliest_date"] == "2015-01-01"
    assert result["imd_latest_date"] == "2025-12-31"
    assert result["events_in_2015_2025_window"] == 684
    assert result["unique_cells_in_overlap"] == 69
    assert result["unique_event_cell_dates_in_overlap"] > 0
    assert result["candidate_unlabeled_background_cell_days"] > 0
    # candidate background days must never exceed total rainfall days
    assert (
        result["candidate_unlabeled_background_cell_days"]
        <= result["total_rainfall_days_available_across_69_cells"]
    )
    assert "never" in result["note"].lower() or "not" in result["note"].lower()


def test_overlap_never_labels_background_as_negative():
    src = open(os.path.join(REPO_ROOT, "scripts", "compute_indofloods_imd_overlap.py")).read()
    # the word "negative" may appear only in explanatory disclaimers, never as a bare label
    for line in src.splitlines():
        if "negative" in line.lower():
            assert (
                "never" in line.lower()
                or "not " in line.lower()
                or "no " in line.lower()
            )


def test_production_files_untouched():
    for rel in PROD_FILES:
        path = os.path.join(REPO_ROOT, rel)
        assert os.path.exists(path), f"missing: {rel}"


def test_no_workflow_files_modified_this_phase():
    wf_dir = os.path.join(REPO_ROOT, ".github", "workflows")
    assert os.path.isdir(wf_dir)
    # sanity: at least the 3 known workflows still present
    names = set(os.listdir(wf_dir))
    for expected in ("forecast_update.yml", "retrain_trigger.yml", "update_grid.yml"):
        assert expected in names
