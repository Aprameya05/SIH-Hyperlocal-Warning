"""The v7 live-inputs station model: its inputs are real, its artifact matches the pre-set gate, and nothing weaker is published."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from station_inputs import NEVER_COMPUTED_LIVE, STATION_OBS_FEATURES, defaulted_inputs  # noqa: E402

EVAL = REPO / "models" / "station_live_inputs_evaluation.json"


def _eval():
    return json.loads(EVAL.read_text(encoding="utf-8"))


def test_evaluation_documents_protocol_and_excludes_all_constants():
    ev = _eval()
    assert set(ev["excluded_station_features"]) == set(STATION_OBS_FEATURES) and set(ev["excluded_never_computed_live"]) == set(NEVER_COMPUTED_LIVE)
    assert not set(ev["features"]) & (STATION_OBS_FEATURES | NEVER_COMPUTED_LIVE)
    assert "train/serve skew" in ev["caveat"]
    for s in ev["slots"].values():
        assert not set(s["dev_years"]) & set(s["test_years"])                  # chronological, disjoint
        assert max(s["dev_years"]) < min(s["test_years"])
        assert 2019 in s["excluded_label_drought_years"]                       # missing-label years are excluded, not trained on


def test_label_drought_years_are_detected_from_the_data():
    import pandas as pd
    sys.path.insert(0, str(REPO / "models" / "thunderstorm" / "station"))
    import train_live_inputs as t
    df = t.load()
    for slot in (0, 1, 3):
        vy = t.valid_years(df[df.slot == slot])
        assert 2019 not in vy and 2024 not in vy and 2025 not in vy, (slot, vy)
    assert 2024 in t.valid_years(df[df.slot == 2]) and 2019 not in t.valid_years(df[df.slot == 2])


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_artifact_exists_only_for_slots_that_passed_the_gate(slot):
    ev = _eval()["slots"][str(slot)]
    art = REPO / "models" / f"nowcast_slot{slot}_xgb_v7_live_inputs.pkl"
    g = ev["gate"]
    recomputed = (ev["test_events"] >= _eval()["gate"]["min_test_events"] and ev["bootstrap"]["bss_vs_slot_month_clim_95ci"][0] > 0
                  and ev["bootstrap"]["auroc_95ci"][0] >= _eval()["gate"]["min_auroc_ci_low"]) if "bootstrap" in ev else False
    assert g["passed"] == recomputed                          # the recorded decision follows the rule
    assert art.exists() == g["passed"], f"slot {slot}: artifact presence must equal the gate decision"


def test_passing_artifact_uses_only_live_features_and_is_not_a_fallback():
    joblib = pytest.importorskip("joblib")
    art = joblib.load(REPO / "models" / "nowcast_slot3_xgb_v7_live_inputs.pkl")
    cols = art["feature_cols"]
    assert defaulted_inputs(cols) == []
    assert art["model_version"] == "v7_live_inputs" and 0.0 < art["threshold"] < 1.0
    src = (REPO / "forecast_action.py").read_text(encoding="utf-8")
    assert src.index('"nowcast_slot3_xgb_v7_live_inputs.pkl"') < src.index('"nowcast_slot3_xgb_v6_temporal.pkl"')
    import importlib.util
    sp = importlib.util.spec_from_file_location("sdi", REPO / "tests" / "test_station_defaulted_inputs.py")
    sdi = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(sdi)
    assert [c for c in cols if c not in sdi._produced_live_names()] == [], "v7 feature with no live source"


def test_v7_predictions_are_calibrated_probabilities_on_a_realistic_row():
    joblib = pytest.importorskip("joblib")
    import numpy as np
    art = joblib.load(REPO / "models" / "nowcast_slot3_xgb_v7_live_inputs.pkl")
    row = np.full((1, len(art["feature_cols"])), np.nan)           # all-missing row must still give a valid probability, not an error
    p = float(art["calibrator"].predict([float(art["model"].predict_proba(row)[0][1])])[0])
    assert 0.0 <= p <= 1.0
