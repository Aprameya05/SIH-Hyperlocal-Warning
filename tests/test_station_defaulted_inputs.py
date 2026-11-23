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


# ---- no silent zeros: every model input is either a real live value or explicitly flagged --------------------------------
import math  # noqa: E402
import re  # noqa: E402

from station_inputs import NEVER_COMPUTED_LIVE, feature_vector  # noqa: E402


def test_feature_vector_uses_nan_for_missing_and_keeps_real_zero():
    v = feature_vector({"a": 0.0, "b": 3, "c": None}, ["a", "b", "c", "d"])
    assert v[0] == 0.0 and v[1] == 3.0 and math.isnan(v[2]) and math.isnan(v[3])


def test_defaulted_inputs_includes_features_absent_from_the_inputs():
    assert defaulted_inputs(["CAPE", "zzz_new"], available={"CAPE": 1.0}) == ["zzz_new"]
    assert "moisture_depth" in defaulted_inputs(["moisture_depth", "CAPE"])
    assert defaulted_inputs(["CAPE"], available={"CAPE": 0.0}) == []          # a real zero is a value, not a default


def _produced_live_names():
    src = (REPO / "forecast_action.py").read_text(encoding="utf-8")
    names = set(re.findall(r'obs\["([A-Za-z0-9_]+)"\]\s*=', src))                       # compute_derived and GFS/upper-air overrides
    block = src[src.index("obs = {\n            \"month\": month"):src.index("data_source = \"model_defaults\"")]
    names |= set(re.findall(r'"([A-Za-z0-9_]+)"\s*:', block))                           # the climatological default dict keys
    gfs = src[src.index("gfs_cols = ["):src.index("for col in gfs_cols")]
    names |= set(re.findall(r'"([A-Za-z0-9_]+)"', gfs))
    return names


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_every_production_model_feature_is_live_or_explicitly_flagged(slot):
    """A new model feature that forecast_action never computes would silently become a constant. Fail until it is either
    computed there or added to station_inputs (so it is flagged, and the slot stays a fallback)."""
    joblib = pytest.importorskip("joblib")
    p = REPO / "models" / f"nowcast_slot{slot}_xgb_v6_temporal.pkl"
    if not p.exists():
        pytest.skip(f"{p.name} not present")
    art = joblib.load(p)
    cols = art.get("feature_cols") or art.get("features")
    produced = _produced_live_names()
    unaccounted = [c for c in cols if c not in produced and c not in STATION_OBS_FEATURES and c not in NEVER_COMPUTED_LIVE]
    assert unaccounted == [], f"features with no live source and no flag (silent zeros): {unaccounted}"
    # and the flagged list is exactly the non-live ones
    assert set(defaulted_inputs(cols)) <= set(cols)
    assert len(defaulted_inputs(cols)) == len([c for c in cols if c in STATION_OBS_FEATURES or c in NEVER_COMPUTED_LIVE])


def test_forecast_action_and_shap_no_longer_default_missing_features_to_zero():
    for f in ("forecast_action.py", "compute_realtime_shap.py"):
        src = (REPO / f).read_text(encoding="utf-8")
        assert "obs.get(c, 0.0)" not in src, f"{f} still turns a missing feature into 0.0"
        assert "feature_vector(obs, feature_cols)" in src
