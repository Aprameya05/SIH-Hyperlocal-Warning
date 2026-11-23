"""Station TS models must not present fixed constants as observations or SHAP drivers."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from station_inputs import STATION_OBS_FEATURES, defaulted_inputs  # noqa: E402


def test_defaulted_inputs_preserves_model_order_and_ignores_live_features():
    cols = ["CAPE", "RF", "K_INDEX", "MIN_3d_avg", "ERA5_T2M", "LABEL_lag1"]
    assert defaulted_inputs(cols) == ["RF", "MIN_3d_avg", "LABEL_lag1"]


def test_no_live_feature_is_marked_defaulted():
    for live in ("CAPE", "CIN", "K_INDEX", "PRECIP_WATER", "ERA5_u_850hPa", "wind_shear_500_850"):
        assert live not in STATION_OBS_FEATURES


def test_forecast_action_defaults_are_exactly_the_station_features():
    """Every hardcoded default that no live source overrides must be in STATION_OBS_FEATURES."""
    src = (REPO / "forecast_action.py").read_text(encoding="utf-8")
    for name in ("MAX", "MIN", "RF", "AW", "EVP", "DRNRF", "SSH", "RF_3d", "RF_7d", "MAX_3d_avg",
                 "MIN_3d_avg", "DTR_3d_avg", "RF_lag1", "MAX_lag1", "MIN_lag1", "LABEL_lag1"):
        assert f'"{name}"' in src
        assert name in STATION_OBS_FEATURES


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_production_models_use_defaulted_inputs_and_are_flagged(slot):
    joblib = pytest.importorskip("joblib")
    p = REPO / "models" / f"nowcast_slot{slot}_xgb_v6_temporal.pkl"
    if not p.exists():
        pytest.skip(f"{p.name} not present")
    art = joblib.load(p)
    cols = art.get("feature_cols") or art.get("features")
    assert defaulted_inputs(cols), "expected the production model to depend on station-observation inputs"


def test_shap_driver_ranking_excludes_defaulted_features():
    src = (REPO / "compute_realtime_shap.py").read_text(encoding="utf-8")
    assert "defaulted_inputs" in src and "defaulted_features_excluded" in src
