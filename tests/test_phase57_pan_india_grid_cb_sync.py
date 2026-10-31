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


def _realistic_unified(records, cb_init=None):
    """M0: the sync now only ingests integrity-checked data, so fixtures use the shape the real builder
    emits: a generated_at, per-record 2024 replay init for TS/FF, and CB as a DAILY aggregate with its
    own (current) cb_init_time_utc. Test bodies and assertions are unchanged."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    cb_init = cb_init or (now - timedelta(hours=7)).replace(minute=0, second=0, microsecond=0).isoformat()
    recs = []
    for r in records:
        r = json.loads(json.dumps(r))
        lead = r.get("lead_hours", 2)
        r["init_time"] = "2024-08-01T00:00:00Z"
        r["valid_time"] = f"2024-08-01T{lead:02d}:00:00Z"
        if "CB" in r:
            r["CB"].setdefault("risk_category", "LOW")
            r["CB"]["lead_time_resolution"] = "DAILY_AGGREGATE_NOT_LEAD_SPECIFIC"
            r["CB"]["cb_init_time_utc"] = cb_init
            r["CB"].setdefault("source_status", "LIVE_AWS_GFS")
        recs.append(r)
    return {"generated_at_utc": (now - timedelta(hours=1)).isoformat(), "init_time_utc": "2024-08-01T00:00:00Z",
            "cb_live_fetch": {"status": "LIVE_AWS_GFS", "init_time_utc": cb_init}, "records": recs}


def _run_sync(tmp_path, unified_records, grid_cells, cb_init=None):
    import importlib.util
    spec = importlib.util.spec_from_file_location("sync_mod", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    unified_path = tmp_path / "unified_forecast.json"
    grid_path = tmp_path / "pan_india_grid.json"
    unified_path.write_text(json.dumps(_realistic_unified(unified_records, cb_init)), encoding="utf-8")
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
    # M0: only the daily outlook is ever synced; a lead-slot CB value is never used.
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
    # M0: the score is computed from the fixed 2024-08-01 rainfall cycle and must say so
    assert cell["flash_flood_research_designation"] == "HISTORICAL_REPLAY"
    assert cell["flash_flood_research_basis_time_utc"] == "2024-08-01T00:00:00Z"


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


def test_nested_forecasts_grid_cells_list_is_synced_identically_to_the_top_level_mirror(tmp_path):
    """2026-10-10: a live browser check against the real production site
    found 'FF research coverage: UNAVAILABLE' displayed for IND_14.0_74.0
    even though that cell's real PU score (RESEARCH_ONLY_PU_SCORE_AVAILABLE)
    was present in data/pan_india_grid.json's top-level grid_cells array.
    Root cause: index.html's own loader comment states
    d.forecasts[primary].grid_cells -- not the top-level d.grid_cells -- is
    "the actual source of truth" the deployed frontend reads; the top-level
    array is only a compatibility-mirror fallback. The first version of this
    sync script updated only the top-level mirror, so the nested array the
    UI actually uses silently kept the old, unsynced cell objects. This test
    reproduces the real production artifact shape (a forecasts[] list, each
    entry carrying its own full grid_cells array) and asserts both the
    top-level mirror AND the nested array end up with identical, real
    CB/FF sync results -- not just one or the other."""
    unified_records = [
        {"cell_id": "IND_14.0_74.0", "lead_hours": 2,
         "CB": {"probability": 0.33, "model_version": "panindia_cb_v1", "source_status": "LIVE_AWS_GFS"},
         "FF": {"model_version": "RESEARCH_ONLY_model_c_logistic", "extra": {"pu_ranking_score": 1.0}}},
    ]
    top_level_cells = [
        {"cell_id": "IND_14.0_74.0", "lat": 14.0, "lon": 74.0,
         "cloudburst_probability": 0.0, "flash_flood_probability": 0.0004},
    ]
    nested_cells = [
        {"cell_id": "IND_14.0_74.0", "lat": 14.0, "lon": 74.0,
         "cloudburst_probability": 0.0, "flash_flood_probability": 0.0004},
    ]

    import importlib.util
    spec = importlib.util.spec_from_file_location("sync_mod", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    unified_path = tmp_path / "unified_forecast.json"
    grid_path = tmp_path / "pan_india_grid.json"
    unified_path.write_text(json.dumps(_realistic_unified(unified_records)), encoding="utf-8")
    grid_path.write_text(json.dumps({
        "grid_cells": top_level_cells,
        "forecasts": [
            {"is_primary": True, "forecast_lead_hours": 9, "grid_cells": nested_cells},
        ],
    }), encoding="utf-8")
    mod.UNIFIED_PATH = unified_path
    mod.GRID_PATH = grid_path
    mod.main()
    result = json.loads(grid_path.read_text(encoding="utf-8"))

    top_cell = result["grid_cells"][0]
    nested_cell = result["forecasts"][0]["grid_cells"][0]
    for cell, label in ((top_cell, "top-level mirror"), (nested_cell, "nested forecasts[].grid_cells")):
        assert cell["cloudburst_probability"] == 0.33, f"{label}: CB probability not synced"
        assert cell["flash_flood_research_status"] == "RESEARCH_ONLY_PU_SCORE_AVAILABLE", (
            f"{label}: FF research status not synced"
        )
        assert cell["flash_flood_research_pu_score"] == 1.0, f"{label}: FF PU score not synced"


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



def test_cb_sync_stamps_event_class_and_marks_the_value_not_lead_specific(tmp_path):
    result = _run_sync(tmp_path, [{"cell_id": "IND_19.0_73.0", "lead_hours": 2,
                                   "CB": {"probability": 0.42, "model_version": "panindia_cb_v1"}}],
                       [{"cell_id": "IND_19.0_73.0", "lat": 19.0, "lon": 73.0, "cloudburst_probability": 0.0}])
    cell = result["grid_cells"][0]
    assert cell["cloudburst_event_class"] == "HEAVY_RAIN_DAY_PROXY"
    assert cell["cloudburst_lead_specific"] is False
    assert cell["cloudburst_value_type"] == "MODEL_SCORE_UNCALIBRATED"
    assert cell["cloudburst_window_start_utc"] and cell["cloudburst_window_end_utc"]


def test_a_replayed_artifact_cannot_feed_the_live_grid(tmp_path):
    """CB whose model init is 2024-08-01 (the fixed validation fallback) must not overwrite the live grid."""
    result = _run_sync(tmp_path, [{"cell_id": "IND_19.0_73.0", "lead_hours": 2,
                                   "CB": {"probability": 0.9, "model_version": "panindia_cb_v1",
                                          "source_status": "FIXED_VALIDATION_FALLBACK"}}],
                       [{"cell_id": "IND_19.0_73.0", "lat": 19.0, "lon": 73.0, "cloudburst_probability": 0.07}],
                       cb_init="2024-08-01T00:00:00+00:00")
    cell = result["grid_cells"][0]
    assert cell["cloudburst_probability"] == 0.07
    assert "cloudburst_probability_source" not in cell


def test_sync_refreshes_hazard_value_types_on_a_full_grid(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("sync_mod", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    up, gp = tmp_path / "u.json", tmp_path / "g.json"
    up.write_text(json.dumps(_realistic_unified([{"cell_id": "IND_19.0_73.0", "lead_hours": 2,
                                                  "CB": {"probability": 0.42, "model_version": "m"}}])))
    cell = {"cell_id": "IND_19.0_73.0", "forecast_lead_hours": 9, "forecast_valid_at_utc": "2026-10-31T03:00:00+00:00",
            "cloudburst_probability": 0.0, "is_calibrated_probability": False}
    gp.write_text(json.dumps({"generated_at_utc": "2026-10-31T01:54:18+00:00", "grid_cells": [dict(cell)],
                              "forecasts": [{"is_primary": True, "forecast_lead_hours": 9, "grid_cells": [dict(cell)]}]}))
    mod.UNIFIED_PATH, mod.GRID_PATH = up, gp
    mod.main()
    out = json.loads(gp.read_text())
    assert out["hazard_value_types"]["cloudburst"]["MODEL_SCORE_UNCALIBRATED"] == 1
    assert out["hazard_value_types"]["thunderstorm"] == "HEURISTIC_RISK_SCORE"
