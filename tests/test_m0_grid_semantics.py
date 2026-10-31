"""
tests/test_m0_grid_semantics.py
===============================
M0 Objectives D, E, F for data/pan_india_grid.json via backend.forecast_integrity.annotate_grid:
operational lead from issue time, nulls stay null and are reported, honest hazard value types.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.forecast_integrity import annotate_grid, GRID_PREDICTORS  # noqa: E402

ISSUED = "2026-10-31T01:54:18.290085+00:00"
SEVEN_NULL = {"k_index", "totals_totals", "wind_shear_ms", "pwat_mm", "ctt_c", "convergence_s",
              "ctt_drop_rate_c_hr"}


def _cell(cid, valid, **kw):
    base = {"cell_id": cid, "forecast_lead_hours": 9, "forecast_valid_at_utc": valid,
            "cape": 120.0, "cin": -10.0, "qpe_mm": 0.0,
            "k_index": None, "totals_totals": None, "wind_shear_ms": None, "pwat_mm": None,
            "ctt_c": None, "convergence_s": None, "ctt_drop_rate_c_hr": None,
            "is_calibrated_probability": False,
            "cloudburst_probability_source": "physics_baseline"}
    base.update(kw)
    return base


def _doc(valid="2026-10-31T03:00:00+00:00", n=3, **cell_kw):
    cells = [_cell(f"IND_{i}.0_70.0", valid, **cell_kw) for i in range(n)]
    return {"generated_at_utc": ISSUED, "gfs_cycle_init_utc": "2026-10-30T18:00:00+00:00",
            "forecasts": [{"forecast_lead_hours": 9, "is_primary": True, "grid_cells": copy.deepcopy(cells)}],
            "grid_cells": copy.deepcopy(cells)}


def test_operational_lead_is_valid_minus_issued_and_differs_from_nwp_hour():
    d = annotate_grid(_doc())
    c = d["forecasts"][0]["grid_cells"][0]
    # M0.1 (Q1): the grid records no forecast availability time, so the operational lead is null + explicit status
    assert c["nwp_lead_hours"] == 9
    assert c["operational_lead_hours"] is None
    assert c["operational_lead_status"] == "UNAVAILABLE_NO_AVAILABILITY_TIME"
    assert d["lead_semantics"]["primary_meets_2_to_6h_target"] is None


def test_a_valid_time_in_the_window_is_not_a_2_to_6h_lead_without_availability_time():
    """Previously valid-minus-generation was treated as the lead. Without a recorded availability time it is not."""
    d = annotate_grid(_doc(valid="2026-10-31T06:30:00+00:00"))
    assert d["lead_semantics"]["primary_meets_2_to_6h_target"] is None
    assert d["forecasts"][0]["grid_cells"][0]["operational_lead_hours"] is None


def test_unavailable_predictors_are_reported_and_stay_null():
    d = annotate_grid(_doc())
    assert set(d["unavailable_predictors"]) == SEVEN_NULL
    for k in SEVEN_NULL:
        assert d["predictor_availability"][k]["status"] == "UNAVAILABLE_ALL_CELLS"
        assert all(c[k] is None for c in d["grid_cells"])           # never zero-filled
    assert d["predictor_availability"]["cape"]["status"] == "AVAILABLE"
    assert "never zero" in d["predictor_availability_note"]


def test_partially_available_predictor_is_partial_not_unavailable():
    d = _doc()
    d["forecasts"][0]["grid_cells"][0]["pwat_mm"] = 41.0
    out = annotate_grid(d)
    assert out["predictor_availability"]["pwat_mm"]["status"] == "PARTIAL"
    assert "pwat_mm" not in out["unavailable_predictors"]


def test_existing_fields_are_untouched_additive_only():
    d = _doc()
    before = copy.deepcopy(d["grid_cells"][0])
    annotate_grid(d)
    after = d["grid_cells"][0]
    assert {k: after[k] for k in before} == before


def test_heuristic_scores_are_never_labelled_calibrated_probabilities():
    d = annotate_grid(_doc())
    t = d["hazard_value_types"]
    assert t["thunderstorm"] == "HEURISTIC_RISK_SCORE" and t["flash_flood"] == "HEURISTIC_RISK_SCORE"
    assert t["calibrated_probability_cells"] == 0
    assert "NOT a calibrated" in d["hazard_definitions"]["thunderstorm"]
    assert "NOT a flood probability" in d["hazard_definitions"]["flash_flood"]


def test_cloudburst_definition_separates_heavy_rain_from_verified_cloudburst():
    d = annotate_grid(_doc(cloudburst_probability_source="panindia_cb_v1 (Phase 21)"))
    assert d["hazard_value_types"]["cloudburst"]["MODEL_SCORE_UNCALIBRATED"] == 3
    cb = d["hazard_definitions"]["cloudburst"]
    assert "heavy-rain DAY" in cb and "not a verified hourly cloudburst" in cb
    assert "extreme short-duration rainfall" in cb
    assert "IMD/NDMA warning" in d["hazard_definitions"]["official_warning"]


def test_time_semantics_block_and_missing_times_degrade_to_null():
    d = annotate_grid(_doc())
    ts = d["time_semantics"]
    assert ts["model_init_time_utc"] == "2026-10-30T18:00:00Z"
    assert ts["artifact_generated_at_utc"] == "2026-10-31T01:54:18Z"
    assert ts["data_acquired_at_utc"] is None and ts["forecast_available_time_utc"] is None
    d2 = annotate_grid({"forecasts": [], "grid_cells": [{"cell_id": "x"}]})       # nothing known
    assert d2["grid_cells"][0]["operational_lead_hours"] is None
    assert d2["lead_semantics"]["primary_meets_2_to_6h_target"] is None


def test_annotation_is_idempotent():
    once = annotate_grid(_doc())
    twice = annotate_grid(copy.deepcopy(once))
    assert once == twice


def test_empty_grid_does_not_crash():
    d = annotate_grid({})
    assert d["unavailable_predictors"] == [] and d["predictor_availability"]["cape"]["n_cells"] == 0


def test_real_committed_grid_reports_exactly_the_seven_audited_unavailable_predictors():
    path = REPO_ROOT / "data" / "pan_india_grid.json"
    if not path.exists():
        pytest.skip("grid not present")
    g = json.load(open(path))
    primary_before = [c for f in g["forecasts"] if f["is_primary"] for c in f["grid_cells"]]
    null_before = {k: sum(1 for c in primary_before if c.get(k) is None) for k in GRID_PREDICTORS}
    out = annotate_grid(g)
    assert set(out["unavailable_predictors"]) == SEVEN_NULL                 # the audit finding, now explicit
    primary_after = [c for f in out["forecasts"] if f["is_primary"] for c in f["grid_cells"]]
    assert {k: sum(1 for c in primary_after if c.get(k) is None) for k in GRID_PREDICTORS} == null_before
    assert out["lead_semantics"]["primary_meets_2_to_6h_target"] is None    # no availability time -> no lead claim
    assert out["lead_semantics"]["operational_lead_hours"] is None
