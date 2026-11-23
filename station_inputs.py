"""Single source of truth for station-model inputs that have NO live data source.

The VOBL station thunderstorm models (models/nowcast_slot*_xgb_*.pkl) were trained with 20 station-observation features (plus 8 more that the live code never computes; see NEVER_COMPUTED_LIVE) that come
from IMD station observations / previous-day labels (daily max/min temperature, rainfall, rolling windows,
lagged labels). No live feed for them exists in this repository, so at inference they are fixed climatological
constants. Such values must never be presented as observations, nor ranked as explanatory "drivers".
"""
from __future__ import annotations

STATION_OBS_FEATURES = frozenset({
    "MAX", "MIN", "AW", "RF", "EVP", "DRNRF", "SSH",
    "RF_3d", "RF_7d", "MAX_3d_avg", "MIN_3d_avg", "DTR_3d_avg",
    "RF_lag1", "MAX_lag1", "MIN_lag1", "LABEL_lag1",
    "RF_nonzero", "ts_label_lag1_slot", "ts_any_yesterday",
    "DTR",  # derived from MAX - MIN (both constants)
})


# Features the production models were trained with but that forecast_action.py never computes at inference. Before this list existed
# they silently became 0.0 through obs.get(c, 0.0). They are constants-by-omission and are treated exactly like the station features.
NEVER_COMPUTED_LIVE = frozenset({
    "moisture_depth", "low_level_convergence", "wet_bulb_potential_temp", "rf_std_7d", "lapse_rate_850_500",
    "is_october_slot2", "monsoon_break_flag", "cape_trend",
})


def defaulted_inputs(feature_cols, available=None) -> list[str]:
    """Model features that are NOT real live values at inference, in model order.

    A feature is a constant if it is a station-observation feature, is on the never-computed list, or (when `available`, the
    inference input mapping, is given) is simply absent from it."""
    return [c for c in feature_cols
            if c in STATION_OBS_FEATURES or c in NEVER_COMPUTED_LIVE or (available is not None and c not in available)]


def feature_vector(obs, feature_cols) -> list[float]:
    """Model input row. A feature with no value is NaN (a missing value the model can route), never a fabricated 0.0."""
    nan = float("nan")
    return [nan if obs.get(c) is None else float(obs[c]) for c in feature_cols]
