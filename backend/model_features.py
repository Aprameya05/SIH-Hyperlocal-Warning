"""
backend/model_features.py -- the ONE place where model input features are built from the national fields artifact.

Used by the prediction scripts (all cells, vectorised) and by the explanation API (one cell), so a model is always
explained with exactly the numbers it was scored with. Feature names/definitions match the training tables
(pipelines/feature_engineering/*), which read the same GFS fields through the same reader.
"""
from __future__ import annotations

import numpy as np

TS_BASE = ("cape", "cin", "lftx", "pwat", "rh2m", "rh850", "rh700", "rh500", "w850", "w700", "w500", "soilw0_10", "k_index", "total_totals",
           "lapse_850_500", "ws10", "shear_850_500", "t2m_c", "mslp_hpa")
RAIN_BASE = ("pwat", "cape", "cin", "lftx", "k_index", "total_totals", "lapse_850_500", "rh2m", "rh850", "rh700", "rh500", "w850", "w700", "w500",
             "ws10", "shear_850_500", "t2m_c", "mslp_hpa", "soilw0_10")

UNITS = {"cape": "J/kg", "cin": "J/kg", "lftx": "K", "pwat": "mm", "rh2m": "%", "rh850": "%", "rh700": "%", "rh500": "%", "w850": "Pa/s", "w700": "Pa/s",
         "w500": "Pa/s", "soilw0_10": "m3/m3", "k_index": "degC", "total_totals": "degC", "lapse_850_500": "K/km", "ws10": "m/s", "shear_850_500": "m/s",
         "t2m_c": "degC", "mslp_hpa": "hPa", "dewpoint_depression": "degC", "apcp_6h": "mm", "apcp_total": "mm", "gfs_p3h": "mm",
         "valid_hour_sin": "", "valid_hour_cos": ""}
LABEL = {"cape": "CAPE", "cin": "CIN", "lftx": "lifted index", "pwat": "precipitable water", "rh2m": "2 m humidity", "rh850": "850 hPa humidity", "rh700": "700 hPa humidity",
         "rh500": "500 hPa humidity", "w850": "850 hPa vertical velocity", "w700": "700 hPa vertical velocity", "w500": "500 hPa vertical velocity",
         "soilw0_10": "soil moisture (model)", "k_index": "K-index", "total_totals": "Total Totals", "lapse_850_500": "850-500 hPa lapse rate", "ws10": "10 m wind speed",
         "shear_850_500": "850-500 hPa bulk shear", "t2m_c": "2 m temperature", "mslp_hpa": "sea-level pressure", "dewpoint_depression": "dew-point depression",
         "apcp_6h": "6 h rainfall", "apcp_total": "rainfall since init", "gfs_p3h": "GFS 3 h rainfall", "valid_hour_sin": "time of day (sin)", "valid_hour_cos": "time of day (cos)"}


def _col(doc, name, fhour):
    k = doc["meta"]["fhours"].index(fhour)
    return np.array([np.nan if v is None else v for v in doc["fields"][name][k]], dtype="float64")


def build_feature_matrix(doc: dict, features: list, lead: int, kind: str) -> np.ndarray:
    """(n_cells, n_features) matrix for `lead` hours after init. kind: 'thunderstorm' or 'rainfall'."""
    from datetime import datetime, timedelta, timezone
    fh = doc["meta"]["fhours"]
    init = datetime.fromisoformat(doc["meta"]["cycle_init_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)
    n = len(doc["cells"])
    base = TS_BASE if kind == "thunderstorm" else RAIN_BASE
    cols = {k: _col(doc, k, lead) for k in base}
    cum = _col(doc, "apcp_cum", lead)
    cols["dewpoint_depression"] = cols["t2m_c"] - _col(doc, "d2m_c", lead)
    cols["apcp_total"] = cum
    if kind == "thunderstorm":
        cols["apcp_6h"] = cum - _col(doc, "apcp_cum", lead - 6) if (lead - 6) in fh else cum
        v = init + timedelta(hours=lead); hh = v.hour + v.minute / 60
        cols["valid_hour_sin"], cols["valid_hour_cos"] = np.full(n, np.sin(2 * np.pi * hh / 24)), np.full(n, np.cos(2 * np.pi * hh / 24))
    else:
        cols["gfs_p3h"] = _col(doc, "precip_3h", lead)
    return np.column_stack([cols[k] for k in features])


def describe(feature: str, value, contribution: float) -> str:
    """Plain-language, value-bound statement of one attribution. Never narrates a missing input as if it were known."""
    name = LABEL.get(feature, feature)
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return f"{name} is unavailable here; the model's handling of the missing value {'raised' if contribution > 0 else 'lowered'} the score by {abs(contribution):.2f}"
    unit = UNITS.get(feature, "")
    shown = f"{value:.0f}" if abs(value) >= 100 else f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{name} = {shown}{(' ' + unit) if unit else ''} {'raised' if contribution > 0 else 'lowered'} the score (attribution {contribution:+.2f}, log-odds)"
