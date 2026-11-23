"""Single source of truth for station-model inputs that have NO live data source.

The VOBL station thunderstorm models (models/nowcast_slot*_xgb_*.pkl) were trained with 19 features that come
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


def defaulted_inputs(feature_cols) -> list[str]:
    """Model features that are fixed constants at live inference, in model order."""
    return [c for c in feature_cols if c in STATION_OBS_FEATURES]
