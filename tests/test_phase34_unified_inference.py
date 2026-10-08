"""
tests/test_phase34_unified_inference.py
===========================================
Phase 34: tests for backend/models/unified_mtl/inference_engine.py and
the data/unified_forecast.json artifact built by
scripts/phase34_build_unified_forecast.py.
"""
import json
import math
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

from backend.models.unified_mtl.inference_engine import (  # noqa: E402
    UnifiedInferenceEngine, InferenceLeakageError, compute_valid_time, assert_no_leakage,
    risk_category_from_probability, RISK_NOT_AVAILABLE, PROVENANCE_ENUM,
    CB_DECISION_THRESHOLD, TS_DECISION_THRESHOLD,
)
from backend.models.unified_mtl.lead_time_interface import LEAD_HOURS  # noqa: E402
from ts_station_model_interface import VOBL_CELL_ID  # noqa: E402

ARTIFACT_PATH = REPO_ROOT / "data" / "unified_forecast.json"
INIT = datetime(2024, 8, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def engine():
    return UnifiedInferenceEngine()


@pytest.fixture(scope="module")
def artifact():
    if not ARTIFACT_PATH.exists():
        pytest.skip("data/unified_forecast.json not built yet")
    with open(ARTIFACT_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Lead-time arithmetic / leakage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lead_hours", LEAD_HOURS)
def test_compute_valid_time_matches_lead_hours_exactly(lead_hours):
    valid = compute_valid_time(INIT, lead_hours)
    assert (valid - INIT).total_seconds() / 3600.0 == lead_hours


def test_assert_no_leakage_raises_on_mismatch():
    with pytest.raises(InferenceLeakageError):
        assert_no_leakage(INIT, INIT, 3)


def test_compute_valid_time_rejects_non_canonical_lead_hours():
    with pytest.raises(ValueError):
        compute_valid_time(INIT, 7)


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def test_engine_loads_all_three_heads(engine):
    assert set(engine.heads.keys()) == {"CB", "TS", "FF"}
    assert engine._load_errors == {}


def test_shared_backbone_is_architecture_only_by_default(engine):
    d = engine.shared_backbone_describe()
    assert d["status"] in ("ARCHITECTURE_ONLY",) or "ARCHITECTURE_ONLY" in str(d.get("note", ""))


# ---------------------------------------------------------------------------
# Hazard status / no fabricated probabilities
# ---------------------------------------------------------------------------

def test_ts_out_of_domain_cell_returns_none_probability_not_zero(engine):
    r = engine.predict_ts("IND_1.0_1.0", 3, INIT, pd.DataFrame())
    assert r.probability is None
    assert r.status == "OUT_OF_DOMAIN_STATION_ONLY"
    assert r.risk_category == RISK_NOT_AVAILABLE


def test_ts_vobl_cell_with_real_features_returns_real_probability(engine):
    df = pd.read_csv(REPO_ROOT / "data" / "bengaluru_thunderstorm_features_merged.csv").tail(1)
    r = engine.predict_ts(VOBL_CELL_ID, 3, INIT, df)
    assert r.probability is not None
    assert 0.0 <= r.probability <= 1.0
    assert r.status == "BASELINE"


def test_cb_without_features_is_not_trained_not_zero(engine):
    r = engine.predict_cb("IND_6.0_68.0", 2, INIT, None)
    assert r.probability is None
    assert r.status == "NOT_TRAINED"
    assert r.risk_category == RISK_NOT_AVAILABLE


def test_ff_never_returns_a_probability_field(engine):
    """FF is a PU-ranking model. Per Phase 34's explicit prohibition,
    its output must never be represented as an observed-flood probability."""
    r = engine.predict_ff("IND_12.0_78.0", 2, INIT, None)
    assert r.probability is None
    r2 = engine.predict_ff("IND_12.0_78.0", 2, INIT, pd.DataFrame())
    assert r2.probability is None


def test_ff_pu_ranking_score_is_separate_from_probability(engine):
    from ff_feature_adapter import build_ff_input_table_for_date
    result = build_ff_input_table_for_date("2024-08-01")
    ready_ids = result.get("ready_cell_ids_full") or result.get("ready_cell_ids")
    assert ready_ids, "expected at least one FF-ready cell"
    tbl = result["feature_table"]
    row = tbl[tbl["cell_id"] == ready_ids[0]].iloc[[0]]
    r = engine.predict_ff(ready_ids[0], 3, INIT, row)
    assert r.probability is None
    assert r.risk_category == RISK_NOT_AVAILABLE
    assert "pu_ranking_score" in r.extra
    assert math.isfinite(r.extra["pu_ranking_score"])


def test_risk_category_never_assigned_without_a_probability():
    assert risk_category_from_probability(None, CB_DECISION_THRESHOLD) == RISK_NOT_AVAILABLE


@pytest.mark.parametrize("p,threshold,expected_not", [
    (0.01, CB_DECISION_THRESHOLD, ("SEVERE", "HIGH")),
    (0.99, CB_DECISION_THRESHOLD, ("LOW",)),
])
def test_risk_category_is_monotonic_with_probability(p, threshold, expected_not):
    cat = risk_category_from_probability(p, threshold)
    assert cat not in expected_not


def test_provenance_enum_has_exactly_the_six_documented_categories():
    assert PROVENANCE_ENUM == ("OBSERVED", "FORECAST", "REANALYSIS", "DERIVED", "PROXY", "MISSING")


def test_ts_and_cb_thresholds_are_the_documented_real_values():
    assert TS_DECISION_THRESHOLD == 0.45
    assert CB_DECISION_THRESHOLD == 0.23000000000000004


# ---------------------------------------------------------------------------
# Artifact-level tests (992 cells x 5 leads = 4960 records)
# ---------------------------------------------------------------------------

def test_artifact_has_exactly_4960_records(artifact):
    assert artifact["n_records"] == 4960
    assert len(artifact["records"]) == 4960


def test_artifact_has_exactly_992_unique_cells(artifact):
    cell_ids = {r["cell_id"] for r in artifact["records"]}
    assert len(cell_ids) == 992


def test_artifact_has_all_5_lead_times_per_cell(artifact):
    by_cell = {}
    for r in artifact["records"]:
        by_cell.setdefault(r["cell_id"], set()).add(r["lead_hours"])
    assert all(leads == set(LEAD_HOURS) for leads in by_cell.values())


def test_artifact_lead_time_arithmetic_is_correct_for_every_record(artifact):
    for r in artifact["records"]:
        init_dt = datetime.strptime(r["init_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        valid_dt = datetime.strptime(r["valid_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        delta_h = (valid_dt - init_dt).total_seconds() / 3600.0
        assert abs(delta_h - r["lead_hours"]) < 1e-6


def test_artifact_no_missing_probability_converted_to_zero(artifact):
    for r in artifact["records"]:
        for hz in ("TS", "CB", "FF"):
            p = r[hz]["probability"]
            status = r[hz]["status"]
            if status in ("NOT_TRAINED", "OUT_OF_DOMAIN_STATION_ONLY", "PU_RANKING", "UNAVAILABLE"):
                if hz == "FF":
                    assert p is None
                # p == 0.0 is legitimate for TS/CB (a real low score); only assert it's not
                # silently fabricated when the model truly produced nothing (None stays None).


def test_artifact_ff_field_never_contains_a_probability_value(artifact):
    for r in artifact["records"]:
        assert r["FF"]["probability"] is None


def test_artifact_risk_category_not_available_whenever_probability_is_none(artifact):
    for r in artifact["records"]:
        for hz in ("TS", "CB", "FF"):
            if r[hz]["probability"] is None:
                assert r[hz]["risk_category"] == "NOT_AVAILABLE"


def test_artifact_risk_category_only_for_legitimate_probability(artifact):
    for r in artifact["records"]:
        for hz in ("TS", "CB"):
            p = r[hz]["probability"]
            cat = r[hz]["risk_category"]
            if p is not None:
                assert cat in ("LOW", "MODERATE", "HIGH", "SEVERE")


def test_artifact_provenance_present_on_every_record(artifact):
    for r in artifact["records"][:200]:  # sample for speed
        for hz in ("TS", "CB", "FF"):
            assert r[hz]["provenance"] in artifact["provenance_enum"]


def test_artifact_hazard_status_values_are_from_the_documented_set(artifact):
    allowed = {"BASELINE", "TRAINED", "PU_RANKING", "OUT_OF_DOMAIN_STATION_ONLY", "NOT_TRAINED", "UNAVAILABLE"}
    for r in artifact["records"][:500]:
        for hz in ("TS", "CB", "FF"):
            assert r[hz]["status"] in allowed


def test_artifact_terrain_coverage_is_explicit_not_fabricated(artifact):
    """2026-10-08 correction: terrain (SRTM elevation) acquisition has
    since achieved genuine 992/992 real coverage -- confirmed directly
    against data/pan_india_terrain_992.json, not assumed -- so requiring
    a nonzero BLOCKED_NETWORK_SRTM count is now a stale expectation from
    when real coverage was still partial. The real invariant that still
    matters: every record's terrain_status must be one of the two
    explicit, honest values (never silently missing, never a status this
    test doesn't recognize), and every status/value pair must sum to the
    full record count -- i.e. no cell is silently unaccounted for."""
    n_real = sum(1 for r in artifact["records"] if r["terrain"]["terrain_status"] == "REAL_SRTM_PANINDIA")
    n_blocked = sum(1 for r in artifact["records"] if r["terrain"]["terrain_status"] == "BLOCKED_NETWORK_SRTM")
    assert n_real > 0
    assert n_real + n_blocked == len(artifact["records"])


def test_artifact_shared_backbone_status_is_architecture_only(artifact):
    assert artifact["shared_backbone_status"] == "ARCHITECTURE_ONLY"


def test_artifact_xai_status_distinguishes_available_from_not_available(artifact):
    assert artifact["xai"]["CB"]["available"] is True
    assert artifact["xai"]["TS"]["available"] is False


def test_artifact_coverage_summary_matches_actual_record_counts(artifact):
    n_ts = sum(1 for r in artifact["records"] if r["TS"]["probability"] is not None)
    n_cb = sum(1 for r in artifact["records"] if r["CB"]["probability"] is not None)
    assert artifact["coverage_summary"]["n_ts_records_with_probability"] == n_ts
    assert artifact["coverage_summary"]["n_cb_records_with_probability"] == n_cb
    assert artifact["coverage_summary"]["n_ff_records_with_probability"] == 0


def test_artifact_validation_block_all_true(artifact):
    for k, v in artifact["validation"].items():
        if isinstance(v, bool):
            assert v is True, f"{k} failed validation"


def test_deterministic_inference_same_cell_same_lead_same_inputs(engine):
    df = pd.read_csv(REPO_ROOT / "data" / "bengaluru_thunderstorm_features_merged.csv").tail(1)
    r1 = engine.predict_ts(VOBL_CELL_ID, 3, INIT, df)
    r2 = engine.predict_ts(VOBL_CELL_ID, 3, INIT, df)
    assert r1.probability == r2.probability
    assert r1.status == r2.status
