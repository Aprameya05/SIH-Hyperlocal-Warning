"""
tests/test_phase57_pan_india_grid_cb_sync.py
=====================================================
2026-10-10: directly inspecting the DEPLOYED production site
(https://sih-hyperlocal-warning.pages.dev/) and its real network
traffic showed that every searched location's cloudburst_probability
comes from data/pan_india_grid.json, populated by
backend/pipeline.py's old deterministic physics-baseline formula --
never data/unified_forecast.json (models/panindia_cb_v1, the real
trained, LODO-validated XGBoost CB model this whole pipeline exists
to serve). scripts/sync_unified_cb_into_pan_india_grid.py closes that
gap by overwriting each cell's cloudburst_probability with the real
calibrated value, joined on the identical cell_id string both files
already share.

Also verifies data/pan_india_grid.json is NOT written by
forecast_update.yml's GENERATED_FILES (it must stay the sole domain
of update_grid.yml / backend/pipeline.py per
docs/PIPELINE_OWNERSHIP.md -- re-adding it there would reintroduce the
exact two-writer race this project already fixed once, 2026-09-30).
"""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync_unified_cb_into_pan_india_grid.py"


def _run_sync(tmp_path, unified_records, grid_cells):
    import importlib.util
    spec = importlib.util.spec_from_file_location("sync_mod", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    unified_path = tmp_path / "unified_forecast.json"
    grid_path = tmp_path / "pan_india_grid.json"
    unified_path.write_text(json.dumps({"records": unified_records}), encoding="utf-8")
    grid_path.write_text(json.dumps({"grid_cells": grid_cells}), encoding="utf-8")

    mod.UNIFIED_PATH = unified_path
    mod.GRID_PATH = grid_path
    mod.main()
    return json.loads(grid_path.read_text(encoding="utf-8"))


def test_real_cb_probability_overwrites_the_physics_baseline_value(tmp_path):
    unified_records = [
        {"cell_id": "IND_19.0_73.0", "lead_hours": 2,
         "CB": {"probability": 0.42, "model_version": "panindia_cb_v1", "source_status": "LIVE_AWS_GFS"}},
    ]
    grid_cells = [
        {"cell_id": "IND_19.0_73.0", "lat": 19.0, "lon": 73.0, "cloudburst_probability": 0.0},
    ]
    result = _run_sync(tmp_path, unified_records, grid_cells)
    cell = result["grid_cells"][0]
    assert cell["cloudburst_probability"] == 0.42
    assert "panindia_cb_v1" in cell["cloudburst_probability_source"]
    assert cell["cloudburst_source_status"] == "LIVE_AWS_GFS"


def test_unavailable_cb_leaves_the_existing_value_untouched(tmp_path):
    unified_records = [
        {"cell_id": "IND_19.0_73.0", "lead_hours": 2,
         "CB": {"probability": None, "model_version": "panindia_cb_v1", "source_status": "UNAVAILABLE"}},
    ]
    grid_cells = [
        {"cell_id": "IND_19.0_73.0", "lat": 19.0, "lon": 73.0, "cloudburst_probability": 0.17},
    ]
    result = _run_sync(tmp_path, unified_records, grid_cells)
    cell = result["grid_cells"][0]
    assert cell["cloudburst_probability"] == 0.17
    assert "cloudburst_probability_source" not in cell


def test_cell_not_present_in_unified_artifact_is_left_untouched(tmp_path):
    grid_cells = [
        {"cell_id": "IND_6.0_68.0", "lat": 6.0, "lon": 68.0, "cloudburst_probability": 0.05},
    ]
    result = _run_sync(tmp_path, [], grid_cells)
    cell = result["grid_cells"][0]
    assert cell["cloudburst_probability"] == 0.05
    assert "cloudburst_probability_source" not in cell


def test_wrong_lead_hours_is_ignored():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sync_mod", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    lookup = mod.build_cb_lookup({"records": [
        {"cell_id": "IND_19.0_73.0", "lead_hours": 6, "CB": {"probability": 0.9}},
    ]})
    assert lookup == {}


def test_ff_research_status_marks_cells_with_a_real_pu_score(tmp_path):
    """2026-10-10: real production coverage audit found FF's genuine
    PU ranking score only exists for 71/992 cells (sparse real
    INDOFLOODS catchment coverage), honestly null for the rest in
    unified_forecast.json -- but none of that honest status ever
    reached pan_india_grid.json, the file the deployed frontend
    actually reads. These additive fields close that gap without
    touching flash_flood_probability itself."""
    unified_records = [
        {"cell_id": "IND_8.0_77.0", "lead_hours": 2,
         "FF": {"model_version": "RESEARCH_ONLY_model_c_logistic",
                "extra": {"pu_ranking_score": 1.0}}},
    ]
    grid_cells = [
        {"cell_id": "IND_8.0_77.0", "lat": 8.0, "lon": 77.0, "flash_flood_probability": 0.0001},
    ]
    result = _run_sync(tmp_path, unified_records, grid_cells)
    cell = result["grid_cells"][0]
    assert cell["flash_flood_probability"] == 0.0001, "the physics-baseline number itself must be untouched"
    assert cell["flash_flood_research_status"] == "RESEARCH_ONLY_PU_SCORE_AVAILABLE"
    assert cell["flash_flood_research_pu_score"] == 1.0


def test_ff_research_status_marks_cells_with_no_real_coverage(tmp_path):
    """A cell with no real PU score must be explicitly labeled
    NO_RESEARCH_COVERAGE, never silently left without any status at
    all (which the frontend could mistake for 'not yet checked').
    Realistic scenario: CB always has full coverage in production
    (confirmed against a real artifact: 992/992), so the unified
    artifact is never truly 'empty' -- only this specific cell lacks
    FF research coverage, matching the real 921/992 majority case."""
    unified_records = [
        {"cell_id": "IND_6.0_68.0", "lead_hours": 2,
         "CB": {"probability": 0.01, "model_version": "panindia_cb_v1", "source_status": "LIVE_AWS_GFS"}},
    ]
    grid_cells = [
        {"cell_id": "IND_6.0_68.0", "lat": 6.0, "lon": 68.0, "flash_flood_probability": 0.0},
    ]
    result = _run_sync(tmp_path, unified_records, grid_cells)
    cell = result["grid_cells"][0]
    assert cell["flash_flood_research_status"] == "NO_RESEARCH_COVERAGE"
    assert "flash_flood_research_pu_score" not in cell


def test_pan_india_grid_json_is_not_committed_by_forecast_update_workflow():
    """docs/PIPELINE_OWNERSHIP.md: data/pan_india_grid.json must stay the
    sole domain of update_grid.yml/backend/pipeline.py. Re-adding it to
    forecast_update.yml's GENERATED_FILES would reintroduce the exact
    two-writer race this project already fixed once (2026-09-30)."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "forecast_update.yml"
    text = workflow_path.read_text(encoding="utf-8")
    start = text.index('GENERATED_FILES="forecast.json')
    end = text.index("\n", start)
    generated_files_line = text[start:end]
    assert "pan_india_grid.json" not in generated_files_line
