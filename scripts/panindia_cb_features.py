#!/usr/bin/env python3
"""
scripts/panindia_cb_features.py

Phase 21 -- feature engineering module for the pan-India cloudburst (CB)
model. Collapses the Phase 20 long-format (cycle, lead, cell) snapshot
dataset into one row per (cycle, cell_id) -- i.e. one row per cell-day --
by computing daily summary statistics across the 8 preserved instantaneous
snapshots (f006..f027), plus a small set of physically-motivated temporal
change features.

This module does NOT decide the daily-aggregation question by fiat for all
future work -- it implements option (B)/(C) from Phase 19/20's deferred
decision (daily summary statistics + temporal change features) as the
FIRST baseline's feature representation, documented here, while the
original 8-snapshot long-format dataset remains untouched and available
for a later snapshot-level or sequence model if that is preferred.

Every engineered feature is documented in FEATURE_DOCS below: formula,
source columns, units, and rationale.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
FULL_DATASET_PATH = REPO_ROOT / "data/external/historical_gfs/phase20_full_predictor_dataset.csv"

# Leads in temporal order (matches Phase 19/20 TARGET_LEADS_INSTANT).
LEAD_ORDER = ["f006", "f009", "f012", "f015", "f018", "f021", "f024", "f027"]
FIRST_LEAD, LAST_LEAD = "f006", "f024"  # f024 used for "end of day" change (f027 kept as a sample point, not the window edge, since f024 is the last FULL bucket boundary -- see Phase 19B)

# ---------------------------------------------------------------------------
# Feature documentation: every engineered feature has a formula, its
# source raw column(s), units, and a one-line physical rationale.
# ---------------------------------------------------------------------------
FEATURE_DOCS = {
    "cape_sfc_jkg_max":   dict(formula="max over f006..f027", source="cape_sfc_jkg", units="J/kg",
                                rationale="peak instability available during the day"),
    "cape_sfc_jkg_mean":  dict(formula="mean over f006..f027", source="cape_sfc_jkg", units="J/kg",
                                rationale="average instability regime"),
    "cin_sfc_jkg_min":    dict(formula="min over f006..f027", source="cin_sfc_jkg", units="J/kg",
                                rationale="strongest inhibition (most negative CIN) seen during the day"),
    "cin_sfc_jkg_mean":   dict(formula="mean over f006..f027", source="cin_sfc_jkg", units="J/kg",
                                rationale="average inhibition regime"),
    "t2m_k_max":          dict(formula="max over f006..f027", source="t2m_k", units="K",
                                rationale="peak daytime heating, a convective trigger proxy"),
    "t2m_k_mean":         dict(formula="mean over f006..f027", source="t2m_k", units="K",
                                rationale="average surface thermal regime"),
    "rh2m_pct_max":       dict(formula="max over f006..f027", source="rh2m_pct", units="%",
                                rationale="peak low-level moisture"),
    "rh2m_pct_mean":      dict(formula="mean over f006..f027", source="rh2m_pct", units="%",
                                rationale="average low-level moisture"),
    "rh2m_pct_min":       dict(formula="min over f006..f027", source="rh2m_pct", units="%",
                                rationale="driest moment, a dry-air-entrainment proxy"),
    "q2m_kgkg_max":       dict(formula="max over f006..f027", source="q2m_kgkg", units="kg/kg",
                                rationale="peak low-level specific humidity (absolute moisture, not RH-relative)"),
    "q2m_kgkg_mean":      dict(formula="mean over f006..f027", source="q2m_kgkg", units="kg/kg",
                                rationale="average absolute moisture"),
    "td2m_k_max":         dict(formula="max over f006..f027", source="td2m_k", units="K",
                                rationale="peak dewpoint, moisture availability for convection"),
    "td2m_k_mean":        dict(formula="mean over f006..f027", source="td2m_k", units="K",
                                rationale="average dewpoint regime"),
    "sp_pa_mean":         dict(formula="mean over f006..f027", source="sp_pa", units="Pa",
                                rationale="average surface pressure (elevation-dependent baseline)"),
    "mslp_pa_mean":       dict(formula="mean over f006..f027", source="mslp_pa", units="Pa",
                                rationale="average synoptic pressure regime"),
    "mslp_pa_min":        dict(formula="min over f006..f027", source="mslp_pa", units="Pa",
                                rationale="deepest pressure minimum seen -- a low-pressure-system proxy"),
    "pwat_mm_max":        dict(formula="max over f006..f027", source="pwat_mm", units="mm",
                                rationale="peak column-integrated water vapor (PWAT, never called IWV)"),
    "pwat_mm_mean":       dict(formula="mean over f006..f027", source="pwat_mm", units="mm",
                                rationale="average column moisture"),
    "wind_speed_850_ms_max":  dict(formula="max over f006..f027", source="wind_speed_850_ms", units="m/s",
                                    rationale="peak low-level jet / moisture-transport wind speed"),
    "wind_speed_850_ms_mean": dict(formula="mean over f006..f027", source="wind_speed_850_ms", units="m/s",
                                    rationale="average low-level wind speed"),
    "wind_speed_500_ms_max":  dict(formula="max over f006..f027", source="wind_speed_500_ms", units="m/s",
                                    rationale="peak mid-level wind speed"),
    "wind_speed_500_ms_mean": dict(formula="mean over f006..f027", source="wind_speed_500_ms", units="m/s",
                                    rationale="average mid-level wind speed"),
    "wind_speed_200_ms_max":  dict(formula="max over f006..f027", source="wind_speed_200_ms", units="m/s",
                                    rationale="peak upper-level (jet stream) wind speed"),
    "wind_speed_200_ms_mean": dict(formula="mean over f006..f027", source="wind_speed_200_ms", units="m/s",
                                    rationale="average upper-level wind speed"),
    "shear_mag_850_200_ms_max":  dict(formula="max over f006..f027", source="shear_mag_850_200_ms", units="m/s",
                                       rationale="peak deep-layer shear -- organized-convection potential"),
    "shear_mag_850_200_ms_mean": dict(formula="mean over f006..f027", source="shear_mag_850_200_ms", units="m/s",
                                       rationale="average deep-layer shear"),
    "gh850_gpm_mean":    dict(formula="mean over f006..f027", source="gh850_gpm", units="gpm",
                               rationale="average 850hPa height -- synoptic trough/ridge proxy"),
    "gh500_gpm_mean":    dict(formula="mean over f006..f027", source="gh500_gpm", units="gpm",
                               rationale="average 500hPa height -- mid-level trough/ridge proxy"),
    "gh200_gpm_mean":    dict(formula="mean over f006..f027", source="gh200_gpm", units="gpm",
                               rationale="average 200hPa height -- upper-level pattern proxy"),
    # Temporal change features (f024 minus f006 -- 18h evolution within day D)
    "cape_change_f006_to_f024":  dict(formula="cape_sfc_jkg[f024] - cape_sfc_jkg[f006]", source="cape_sfc_jkg",
                                       units="J/kg", rationale="instability growth (positive) or decay (negative) over the day"),
    "pwat_change_f006_to_f024":  dict(formula="pwat_mm[f024] - pwat_mm[f006]", source="pwat_mm",
                                       units="mm", rationale="moisture accumulation (positive) or depletion (negative) over the day"),
    "t2m_change_f006_to_f024":   dict(formula="t2m_k[f024] - t2m_k[f006]", source="t2m_k",
                                       units="K", rationale="diurnal heating signal captured within the available snapshots"),
    "cin_change_f006_to_f024":   dict(formula="cin_sfc_jkg[f024] - cin_sfc_jkg[f006]", source="cin_sfc_jkg",
                                       units="J/kg", rationale="inhibition erosion (toward 0, more negative->less negative) or strengthening over the day"),
    # Target-day constants (already daily; carried through unchanged)
    "target_day_precip_tp_mm":     dict(formula="passthrough (already target-day accumulated, Phase 19/20)",
                                         source="target_day_precip_tp_mm", units="mm",
                                         rationale="primary GFS-forecast precipitation signal for the day"),
    "target_day_precip_acpcp_mm":  dict(formula="passthrough", source="target_day_precip_acpcp_mm", units="mm",
                                         rationale="convective-precipitation component of the day's total"),
    # Prate ablation features (included only in the WITH-prate model variant)
    "prate_kgm2s_max":   dict(formula="max over f006..f027", source="prate_kgm2s", units="kg/m2/s",
                               rationale="peak instantaneous/avg precip rate -- ABLATION FEATURE, cross-vintage semantic heterogeneity (see Phase 21 doc Section on prate)"),
    "prate_kgm2s_mean":  dict(formula="mean over f006..f027", source="prate_kgm2s", units="kg/m2/s",
                               rationale="average precip rate -- ABLATION FEATURE, same caveat as above"),
    # 9000Pa ablation features (included only in the WITH-9000Pa model variant)
    "cape_9000pa_jkg_mean": dict(formula="mean over f006..f027 where available, else NaN", source="cape_9000pa_jkg",
                                  units="J/kg", rationale="ABLATION FEATURE -- cycle-vintage-dependent (2022071318, 2024073118 only); risk of vintage leakage, see Phase 21 doc"),
    "cin_9000pa_jkg_mean":  dict(formula="mean over f006..f027 where available, else NaN", source="cin_9000pa_jkg",
                                  units="J/kg", rationale="ABLATION FEATURE -- same caveat as cape_9000pa_jkg_mean"),
    "cape_9000pa_available": dict(formula="passthrough (constant per cycle)", source="cape_9000pa_available",
                                   units="boolean", rationale="explicit vintage-availability flag, included alongside the 9000Pa ablation features so the model can learn their reliability rather than be confused by silent NaNs"),
}


def load_full_dataset() -> pd.DataFrame:
    return pd.read_csv(FULL_DATASET_PATH)


def engineer_daily_features(df: pd.DataFrame, include_prate: bool, include_9000pa: bool) -> pd.DataFrame:
    """Collapses the (cycle, lead, cell_id) long-format dataframe into one
    row per (cycle, cell_id), with the documented summary + change
    features. include_prate / include_9000pa control whether the
    corresponding ablation feature groups are computed (for the Phase 21
    ablation study) -- excluding them means the columns are not created at
    all, not merely zeroed/NaN-filled, since the ablation question is
    "does including this feature group help," not "what if it were zero.\""""
    group_cols = ["cycle", "cell_id"]
    agg_spec = {
        "target_date": "first", "lat": "first", "lon": "first",
        "gfs_format_vintage": "first",
        "label_status": "first", "cb_label_daily": "first",
        "cb_label_source": "first", "cb_label_temporal_resolution": "first",
        "target_day_precip_tp_mm": "first", "target_day_precip_acpcp_mm": "first",
        "cape_sfc_jkg": ["max", "mean"],
        "cin_sfc_jkg": ["min", "mean"],
        "t2m_k": ["max", "mean"],
        "rh2m_pct": ["max", "mean", "min"],
        "q2m_kgkg": ["max", "mean"],
        "td2m_k": ["max", "mean"],
        "sp_pa": ["mean"],
        "mslp_pa": ["mean", "min"],
        "pwat_mm": ["max", "mean"],
        "wind_speed_850_ms": ["max", "mean"],
        "wind_speed_500_ms": ["max", "mean"],
        "wind_speed_200_ms": ["max", "mean"],
        "shear_mag_850_200_ms": ["max", "mean"],
        "gh850_gpm": ["mean"],
        "gh500_gpm": ["mean"],
        "gh200_gpm": ["mean"],
    }
    if include_prate:
        agg_spec["prate_kgm2s"] = ["max", "mean"]
        agg_spec["prate_stepType_used"] = "first"
    if include_9000pa:
        agg_spec["cape_9000pa_jkg"] = ["mean"]
        agg_spec["cin_9000pa_jkg"] = ["mean"]
        agg_spec["cape_9000pa_available"] = "first"

    grouped = df.groupby(group_cols).agg(agg_spec)
    new_cols = []
    for c in grouped.columns:
        if isinstance(c, tuple):
            base, fn = c
            # Single-aggregation columns (fn == "first") keep their base
            # name unsuffixed; multi-aggregation columns (max/mean/min)
            # keep the "<base>_<fn>" suffix to disambiguate.
            new_cols.append(base if fn == "first" else f"{base}_{fn}")
        else:
            new_cols.append(c)
    grouped.columns = new_cols
    grouped = grouped.reset_index()

    # Temporal change features: need f006 and f024 rows specifically.
    f006 = df[df["lead"] == FIRST_LEAD].set_index(group_cols)
    f024 = df[df["lead"] == LAST_LEAD].set_index(group_cols)
    for var, out_name in [("cape_sfc_jkg", "cape_change_f006_to_f024"),
                           ("pwat_mm", "pwat_change_f006_to_f024"),
                           ("t2m_k", "t2m_change_f006_to_f024"),
                           ("cin_sfc_jkg", "cin_change_f006_to_f024")]:
        change = (f024[var] - f006[var]).rename(out_name)
        grouped = grouped.merge(change, left_on=group_cols, right_index=True, how="left")

    # Tidy rename to match FEATURE_DOCS keys exactly.
    rename_map = {
        "cape_sfc_jkg_max": "cape_sfc_jkg_max", "cape_sfc_jkg_mean": "cape_sfc_jkg_mean",
        "cin_sfc_jkg_min": "cin_sfc_jkg_min", "cin_sfc_jkg_mean": "cin_sfc_jkg_mean",
        "t2m_k_max": "t2m_k_max", "t2m_k_mean": "t2m_k_mean",
        "rh2m_pct_max": "rh2m_pct_max", "rh2m_pct_mean": "rh2m_pct_mean", "rh2m_pct_min": "rh2m_pct_min",
        "q2m_kgkg_max": "q2m_kgkg_max", "q2m_kgkg_mean": "q2m_kgkg_mean",
        "td2m_k_max": "td2m_k_max", "td2m_k_mean": "td2m_k_mean",
        "sp_pa_mean": "sp_pa_mean",
        "mslp_pa_mean": "mslp_pa_mean", "mslp_pa_min": "mslp_pa_min",
        "pwat_mm_max": "pwat_mm_max", "pwat_mm_mean": "pwat_mm_mean",
        "wind_speed_850_ms_max": "wind_speed_850_ms_max", "wind_speed_850_ms_mean": "wind_speed_850_ms_mean",
        "wind_speed_500_ms_max": "wind_speed_500_ms_max", "wind_speed_500_ms_mean": "wind_speed_500_ms_mean",
        "wind_speed_200_ms_max": "wind_speed_200_ms_max", "wind_speed_200_ms_mean": "wind_speed_200_ms_mean",
        "shear_mag_850_200_ms_max": "shear_mag_850_200_ms_max", "shear_mag_850_200_ms_mean": "shear_mag_850_200_ms_mean",
        "gh850_gpm_mean": "gh850_gpm_mean", "gh500_gpm_mean": "gh500_gpm_mean", "gh200_gpm_mean": "gh200_gpm_mean",
    }
    if include_prate:
        rename_map["prate_kgm2s_max"] = "prate_kgm2s_max"
        rename_map["prate_kgm2s_mean"] = "prate_kgm2s_mean"
    if include_9000pa:
        rename_map["cape_9000pa_jkg_mean"] = "cape_9000pa_jkg_mean"
        rename_map["cin_9000pa_jkg_mean"] = "cin_9000pa_jkg_mean"

    return grouped


def get_feature_columns(include_prate: bool, include_9000pa: bool) -> list[str]:
    base = [
        "cape_sfc_jkg_max", "cape_sfc_jkg_mean", "cin_sfc_jkg_min", "cin_sfc_jkg_mean",
        "t2m_k_max", "t2m_k_mean", "rh2m_pct_max", "rh2m_pct_mean", "rh2m_pct_min",
        "q2m_kgkg_max", "q2m_kgkg_mean", "td2m_k_max", "td2m_k_mean",
        "sp_pa_mean", "mslp_pa_mean", "mslp_pa_min", "pwat_mm_max", "pwat_mm_mean",
        "wind_speed_850_ms_max", "wind_speed_850_ms_mean",
        "wind_speed_500_ms_max", "wind_speed_500_ms_mean",
        "wind_speed_200_ms_max", "wind_speed_200_ms_mean",
        "shear_mag_850_200_ms_max", "shear_mag_850_200_ms_mean",
        "gh850_gpm_mean", "gh500_gpm_mean", "gh200_gpm_mean",
        "cape_change_f006_to_f024", "pwat_change_f006_to_f024",
        "t2m_change_f006_to_f024", "cin_change_f006_to_f024",
        "target_day_precip_tp_mm", "target_day_precip_acpcp_mm",
    ]
    if include_prate:
        base += ["prate_kgm2s_max", "prate_kgm2s_mean"]
    if include_9000pa:
        base += ["cape_9000pa_jkg_mean", "cin_9000pa_jkg_mean", "cape_9000pa_available"]
    return base
