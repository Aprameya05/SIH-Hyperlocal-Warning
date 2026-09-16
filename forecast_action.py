"""
forecast_action.py
==================
CSIR Thunderstorm Prediction System — Main Forecast Pipeline
Runs inside GitHub Actions to generate forecast.json
Called by .github/workflows/forecast_update.yml

Model priority per slot (per handoff doc, 2026-08-08):
  Slot 0 : nowcast_slot0_xgb_v4_ensemble       (CV AUROC 0.8484)
  Slot 1 : nowcast_slot1_xgb_v5_temporal        (30 lag features)
  Slot 2 : nowcast_slot2_xgb_v5_temporal        (30 lag features)
  Slot 3 : nowcast_slot3_xgb_v5_temporal        (30 lag features)
  Fallback: v4_calibrated → v3_calibrated → v2_calibrated

October post-monsoon threshold fix (DOY_sin suppression):
  Slot 2 base threshold lowered from 0.226 → 0.10 in October only.
  Effect: POD 0.379 → 0.621, FAR 0.167 → 0.474 on 2015-2025 test set.

Also generates:
  data/pipeline_health.json — data freshness + component status for dashboard

Author: Aprameya + team, CSIR Thunderstorm Project
"""

import json
import math
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

import joblib
import numpy as np
import pandas as pd

BASE   = Path(".")
MODELS = BASE / "models"
DATA   = BASE / "data"
IST    = timezone(timedelta(hours=5, minutes=30))

SLOT_NAMES  = {0: "0001-0600 IST", 1: "0601-1200 IST",
               2: "1201-1800 IST", 3: "1801-2400 IST"}
SLOT_LABELS = {0: "Late Night", 1: "Morning", 2: "Afternoon", 3: "Evening"}

# Base thresholds — overridden by October fix for Slot 2
BASE_THRESHOLDS = {0: 0.24, 1: 0.15, 2: 0.16, 3: 0.39}

# Monsoon regime threshold adjustment factors
# ACTIVE / CONVECTIVE_BURST → lower threshold (catch more TS)
# BREAK → raise threshold (suppress false alarms on suppressed days)
REGIME_THRESHOLD_FACTORS = {
    "CONVECTIVE_BURST": 0.80,
    "ACTIVE":           0.88,
    "ACTIVE_MODERATE":  0.95,
    "NEUTRAL":          1.00,
    "BREAK":            1.30,
}


def detect_monsoon_regime(cape: float, ki: float, t2m_c: float, month: int) -> str:
    """
    Rule-based monsoon regime detection for probability threshold adjustment.

    IMPORTANT — this is NOT the IMD active/break monsoon classification:
      The Rajeevan et al. (2010) active/break definition uses GPCP/TRMM rainfall
      anomalies over the monsoon core zone (central India, 18-28N, 65-88E).
      That classification has zero break spells at Bengaluru (South Peninsula)
      because the core-zone definition is physically inappropriate here.
      It was explicitly dropped from the model per feedback from Dr. Geeta Agnihotri.

    What we do instead:
      Station-level K-Index and CAPE thresholds derived from VOBL/43295 radiosonde
      climatology, which ARE valid for Bengaluru's local convective environment.
      The 'BREAK' label here means "low-moisture, stable conditions at this station",
      not an IMD-declared national monsoon break spell.
    """
    if ki >= 38 and cape >= 800:
        return "CONVECTIVE_BURST"
    elif ki >= 35 and cape >= 300 and month in [5, 6, 7, 8, 9]:
        return "ACTIVE"
    elif ki >= 32 and cape >= 100 and t2m_c >= 28:
        return "ACTIVE_MODERATE"
    elif cape < 100 and ki < 30 and month in [6, 7, 8, 9]:
        return "BREAK"
    else:
        return "NEUTRAL"

# Production model priority per slot
# v6 Himawari > v6 temporal > v5 temporal > v4 ensemble/calibrated > v3 > v2
SLOT_MODEL_PRIORITY = {
    0: ["nowcast_slot0_xgb_v6_himawari.pkl",
        "nowcast_slot0_xgb_v6_temporal.pkl",
        "nowcast_slot0_xgb_v4_ensemble.pkl",
        "nowcast_slot0_xgb_v4_calibrated.pkl",
        "nowcast_slot0_xgb_v3_calibrated.pkl",
        "nowcast_slot0_xgb_v2_calibrated.pkl"],
    1: ["nowcast_slot1_xgb_v6_himawari.pkl",
        "nowcast_slot1_xgb_v6_temporal.pkl",
        "nowcast_slot1_xgb_v5_temporal.pkl",
        "nowcast_slot1_xgb_v4_calibrated.pkl",
        "nowcast_slot1_xgb_v3_calibrated.pkl",
        "nowcast_slot1_xgb_v2_calibrated.pkl"],
    2: ["nowcast_slot2_xgb_v6_himawari.pkl",
        "nowcast_slot2_xgb_v6_temporal.pkl",
        "nowcast_slot2_xgb_v5_temporal.pkl",
        "nowcast_slot2_xgb_v4_calibrated.pkl",
        "nowcast_slot2_xgb_v3_calibrated.pkl",
        "nowcast_slot2_xgb_v2_calibrated.pkl"],
    3: ["nowcast_slot3_xgb_v6_himawari.pkl",
        "nowcast_slot3_xgb_v6_temporal.pkl",
        "nowcast_slot3_xgb_v5_temporal.pkl",
        "nowcast_slot3_xgb_v4_calibrated.pkl",
        "nowcast_slot3_xgb_v3_calibrated.pkl",
        "nowcast_slot3_xgb_v2_calibrated.pkl"],
}


# ── October post-monsoon threshold fix ───────────────────────────────────────

def get_slot2_threshold(month: int, base: float = 0.16) -> float:
    """Lower Slot 2 threshold in October to recover post-monsoon POD.
    DOY_sin suppresses probabilities in October; threshold fix corrects this.
    POD improves from 0.379 → 0.621; FAR rises from 0.167 → 0.474 (accepted).
    """
    return 0.10 if month == 10 else base


# ── Calibrator ───────────────────────────────────────────────────────────────

def apply_calibrator(artifact: dict, raw_prob: float) -> float:
    cal = artifact.get("calibrator")
    if cal is None:
        return raw_prob
    try:
        if artifact.get("calib_method") == "sigmoid":
            return float(cal.predict_proba(np.array([[raw_prob]]))[0][1])
        return float(cal.predict([raw_prob])[0])
    except Exception as e:
        print(f"  Calibrator failed ({e}), using raw prob")
        return raw_prob


# ── Feature vector construction ───────────────────────────────────────────────

def compute_derived(obs: dict, slot_id: int) -> dict:
    m   = int(obs.get("month", datetime.now().month))
    doy = int(obs.get("doy", datetime.now().timetuple().tm_yday))

    obs["DTR"]        = obs.get("MAX", 30.0) - obs.get("MIN", 20.0)
    obs["HA_flag"]    = 0
    obs["RF_nonzero"] = 1 if obs.get("RF", 0) > 0 else 0

    if m in [3, 4, 5]:       obs["SEASON"] = 1
    elif m in [6, 7, 8, 9]:  obs["SEASON"] = 2
    elif m in [10, 11]:       obs["SEASON"] = 3
    else:                     obs["SEASON"] = 0

    obs["MONTH_sin"]  = math.sin(2 * math.pi * m / 12)
    obs["MONTH_cos"]  = math.cos(2 * math.pi * m / 12)
    obs["DOY_sin"]    = math.sin(2 * math.pi * doy / 365)
    obs["DOY_cos"]    = math.cos(2 * math.pi * doy / 365)
    obs["doy_sin"]    = obs["DOY_sin"]
    obs["doy_cos"]    = obs["DOY_cos"]
    obs["slot_sin"]   = math.sin(2 * math.pi * slot_id / 4)
    obs["slot_cos"]   = math.cos(2 * math.pi * slot_id / 4)

    # Slot–month climatological prior (Slot 2 monsoon-season rates from training data)
    CLIM = {
        (4, 2): 0.129, (5, 2): 0.194, (6, 2): 0.096, (7, 2): 0.032,
        (8, 2): 0.052, (9, 2): 0.079, (10, 2): 0.077,
    }
    obs["slot_month_clim"] = CLIM.get((m, slot_id), 0.02)

    # Sync CAPE keys
    obs["ERA5_CAPE"] = obs.get("ERA5_CAPE") or obs.get("CAPE", 0.0)

    # Derived thermodynamic and moisture features
    q850 = obs.get("ERA5_q_850hPa", 0.013)
    q700 = obs.get("ERA5_q_700hPa", 0.009)
    q500 = obs.get("ERA5_q_500hPa", 0.003)
    t850 = obs.get("ERA5_t_850hPa", 293.0)
    t500 = obs.get("ERA5_t_500hPa", 268.0)
    u850 = obs.get("ERA5_u_850hPa", -3.0)
    v850 = obs.get("ERA5_v_850hPa",  2.0)
    u700 = obs.get("ERA5_u_700hPa",  2.0)
    v700 = obs.get("ERA5_v_700hPa",  1.0)
    u500 = obs.get("ERA5_u_500hPa",  5.0)
    v500 = obs.get("ERA5_v_500hPa",  2.0)
    CAPE = obs.get("CAPE", 0.0)
    K    = obs.get("K_INDEX", 30.0)
    LI   = obs.get("LIFTED_INDEX", -2.0)
    TT   = obs.get("TOTALS_TOTALS", 44.0)

    obs["cape_x_kindex"]      = CAPE * K
    obs["li_x_totals"]        = abs(LI) * TT
    obs["q_gradient_500_850"] = q850 - q500
    obs["thetae_850"]         = t850 + 2491 * q850
    obs["wind_shear_500_850"] = ((u500 - u850) ** 2 + (v500 - v850) ** 2) ** 0.5
    obs["wind_shear_700_850"] = ((u700 - u850) ** 2 + (v700 - v850) ** 2) ** 0.5
    obs["moisture_flux_850"]  = q850 * (u850 ** 2 + v850 ** 2) ** 0.5
    obs["moisture_flux_700"]  = q700 * (u700 ** 2 + v700 ** 2) ** 0.5
    obs["thickness_500_850"]  = t850 - t500
    obs["mid_level_drying"]   = q700 / (q850 + 1e-9)

    # ── Convective rotation parameters (SRH, EHI, BRN) ───────────────────────
    # SRH/EHI/BRN are written directly to gfs_realtime_43295.csv by gfs_fetcher.py.
    # Pass through if present; otherwise compute from wind components as fallback.
    if obs.get("SRH_0_3km") is None or obs.get("SRH_0_3km") != obs.get("SRH_0_3km"):
        # Fallback: estimate SRH from wind shear (simplified)
        shear_mag = obs["wind_shear_500_850"]
        srh_est   = shear_mag * 10.0   # rough proxy
        ehi_est   = (CAPE * max(srh_est, 0)) / 160000.0
        brn_est   = CAPE / max(0.5 * shear_mag**2, 0.1)
        obs["SRH_0_3km"] = round(srh_est, 2)
        obs["EHI"]       = round(ehi_est, 4)
        obs["BRN"]       = round(brn_est, 2)

    # Composite convective indices
    obs["cape_x_ehi"]    = CAPE * float(obs.get("EHI", 0) or 0)
    obs["srh_x_shear"]   = float(obs.get("SRH_0_3km", 0) or 0) * obs["wind_shear_500_850"]
    obs["brn_category"]  = (
        2 if obs.get("BRN", 999) is not None and float(obs.get("BRN", 999)) < 10 else
        1 if obs.get("BRN", 999) is not None and float(obs.get("BRN", 999)) < 45 else
        0
    )

    return obs


# ── Pipeline health tracker ───────────────────────────────────────────────────

def build_pipeline_health(now: datetime, gfs_df: pd.DataFrame,
                          himawari: dict, metar_available: bool,
                          upper_air: dict) -> dict:
    """Build data/pipeline_health.json for the dashboard Data Health page."""
    now_utc = datetime.now(timezone.utc)

    def staleness(ts_str: str, warn_minutes: int = 90) -> str:
        if not ts_str:
            return "UNKNOWN"
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                ts = datetime.strptime(ts_str[:19], fmt[:len(ts_str[:19])])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_min = (now_utc - ts).total_seconds() / 60
                return "FRESH" if age_min < warn_minutes else f"STALE ({int(age_min)}m)"
            except Exception:
                continue
        return "UNKNOWN"

    gfs_cycle = gfs_df.iloc[0]["gfs_cycle"] if len(gfs_df) > 0 else "N/A"
    gfs_fetched = gfs_df.iloc[0].get("fetched_at_utc", "") if len(gfs_df) > 0 else ""

    return {
        "generated_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_ist": now.strftime("%Y-%m-%d %H:%M IST"),
        "components": {
            "gfs": {
                "status":      "OK" if len(gfs_df) > 0 else "MISSING",
                "cycle":       gfs_cycle,
                "fetched_utc": gfs_fetched,
                "staleness":   staleness(str(gfs_fetched), warn_minutes=360),
                "rows":        len(gfs_df),
            },
            "himawari9": {
                "status":       "OK" if himawari else "MISSING",
                "timestamp_utc": himawari.get("timestamp_utc", ""),
                "storm_detected": himawari.get("storm_detected", False),
                "min_bt_50km":    himawari.get("min_bt_50km"),
                "staleness":      staleness(
                    str(himawari.get("timestamp_utc", "")), warn_minutes=30
                ),
            },
            "upper_air": {
                "status":         "OK" if upper_air else "MISSING",
                "slots_available": list(upper_air.keys()),
                "n_slots":         len(upper_air),
            },
            "metar": {
                "status": "OK" if metar_available else "MISSING",
            },
        },
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, choices=[0, 1, 2, 3], default=None,
                    help="Force a specific slot for debugging")
    args = ap.parse_args()

    now      = datetime.now(IST)
    month    = now.month
    doy      = now.timetuple().tm_yday
    date_str = now.strftime("%Y-%m-%d")

    print("=" * 65)
    print("  forecast_action.py — CSIR Thunderstorm Nowcasting System")
    print("=" * 65)
    print(f"  Run time (IST): {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"  Date: {date_str}  Month: {month}  DOY: {doy}")

    # ── Load upper-air data ───────────────────────────────────────────────────
    upper_air = {}
    ua_path   = DATA / "upperair_realtime_43295.csv"
    if ua_path.exists():
        ua_df    = pd.read_csv(ua_path)
        ua_today = ua_df[ua_df["date"] == date_str]
        for _, row in ua_today.iterrows():
            upper_air[int(row["slot"])] = row.to_dict()
        print(f"\n  Upper-air loaded: slots {list(upper_air.keys())}")
    else:
        print("\n  Upper-air: NOT FOUND — using model defaults")

    # ── Load GFS realtime data ────────────────────────────────────────────────
    gfs_df   = pd.DataFrame()
    gfs_path = DATA / "gfs_realtime_43295.csv"
    if gfs_path.exists():
        gfs_df = pd.read_csv(gfs_path)
        # Only use today's data
        if "date" in gfs_df.columns:
            gfs_df = gfs_df[gfs_df["date"] == date_str].reset_index(drop=True)
        cycle_info = gfs_df.iloc[0]["gfs_cycle"] if len(gfs_df) > 0 else "N/A"
        print(f"  GFS loaded: {len(gfs_df)} row(s), cycle: {cycle_info}")
    else:
        print("  GFS: NOT FOUND — using climatology defaults")

    # ── CAPE tendency from GFS history ───────────────────────────────────────
    cape_tendency = None   # J/kg/h — positive = instability growing
    gfs_hist_path = DATA / "gfs_history_43295.json"
    if gfs_hist_path.exists():
        try:
            with open(gfs_hist_path) as f:
                gfs_hist = json.load(f)
            if len(gfs_hist) >= 2:
                c_new = float(gfs_hist[-1].get("CAPE", 0) or 0)
                c_old = float(gfs_hist[-2].get("CAPE", 0) or 0)
                t_new_str = gfs_hist[-1].get("fetched_at", "")
                t_old_str = gfs_hist[-2].get("fetched_at", "")
                if t_new_str and t_old_str:
                    t_new_dt = datetime.fromisoformat(t_new_str.replace("Z", "+00:00"))
                    t_old_dt = datetime.fromisoformat(t_old_str.replace("Z", "+00:00"))
                    dt_h = (t_new_dt - t_old_dt).total_seconds() / 3600.0
                    if dt_h > 0:
                        cape_tendency = round((c_new - c_old) / dt_h, 1)
                        print(f"  CAPE tendency: {cape_tendency:+.1f} J/kg/h "
                              f"(prev={c_old:.0f} → now={c_new:.0f})")
        except Exception as e:
            print(f"  CAPE tendency error: {e}")

    # ── Monsoon regime pre-detection for threshold adjustment ─────────────────
    if len(gfs_df) > 0:
        _r = gfs_df.iloc[0]
        _t2m_k = float(_r.get("ERA5_T2M", 302.0) or 302.0)
        _t2m_c = _t2m_k - 273.15 if _t2m_k > 200 else _t2m_k
        _cape_pre = float(_r.get("CAPE", 0.0) or 0.0)
        _ki_pre   = float(_r.get("K_INDEX", 30.0) or 30.0)
    else:
        _t2m_c, _cape_pre, _ki_pre = 28.0, 0.0, 30.0

    monsoon_regime       = detect_monsoon_regime(_cape_pre, _ki_pre, _t2m_c, month)
    regime_thresh_factor = REGIME_THRESHOLD_FACTORS.get(monsoon_regime, 1.0)
    print(f"  Monsoon regime (pre-detection): {monsoon_regime} → "
          f"threshold factor ×{regime_thresh_factor:.2f}")

    # ── Slot loop ─────────────────────────────────────────────────────────────
    slots_output = []
    results      = {}

    for slot_id in range(4):
        # Apply October threshold fix for Slot 2
        if slot_id == 2:
            threshold_op = get_slot2_threshold(month, BASE_THRESHOLDS[2])
            if month == 10:
                print(f"\n  Slot 2: October threshold fix active → {threshold_op}")
        else:
            threshold_op = BASE_THRESHOLDS[slot_id]

        # Apply monsoon regime adjustment (clamped 0.05–0.90)
        threshold_op = round(min(max(threshold_op * regime_thresh_factor, 0.05), 0.90), 3)

        # Find best available model
        model_path = None
        model_name = "none"
        for candidate in SLOT_MODEL_PRIORITY[slot_id]:
            p = MODELS / candidate
            if p.exists():
                model_path = p
                model_name = candidate
                break

        if model_path is None:
            clim = {0: 0.037, 1: 0.011, 2: 0.063, 3: 0.059}
            prob = clim[slot_id]
            results[slot_id] = prob
            print(f"\n  Slot {slot_id}: ⚠ No model found — using climatology ({prob*100:.1f}%)")
            slots_output.append({
                "slot": slot_id, "label": SLOT_LABELS[slot_id],
                "time": SLOT_NAMES[slot_id],
                "ts_probability": round(prob, 4),
                "ts_predicted":   prob >= threshold_op,
                "threshold":      threshold_op,
                "primary":        slot_id == 2,
                "source":         "climatology",
                "model_used":     "none",
            })
            continue

        print(f"\n  Slot {slot_id}: loading {model_name}")
        try:
            artifact     = joblib.load(model_path)
            # Support both artifact schemas:
            #   old: {'model': ..., 'feature_cols': ..., 'threshold': ...}
            #   new: {'calibrated': ..., 'features': ..., 'slot': ..., 'auroc': ...}
            model        = artifact.get("model") or artifact.get("calibrated")
            feature_cols = artifact.get("feature_cols") or artifact.get("features") or []
            if model is None:
                raise KeyError(f"No model key found. Keys: {list(artifact.keys())}")
            if not feature_cols:
                raise KeyError(f"No feature_cols/features key found. Keys: {list(artifact.keys())}")
            # Operational threshold always takes precedence (includes October fix)
            threshold = threshold_op
        except Exception as e:
            print(f"  ✗ Load failed: {e}")
            clim = {0: 0.037, 1: 0.011, 2: 0.063, 3: 0.059}
            prob = clim[slot_id]
            results[slot_id] = prob
            slots_output.append({
                "slot": slot_id, "label": SLOT_LABELS[slot_id],
                "time": SLOT_NAMES[slot_id],
                "ts_probability": round(prob, 4),
                "ts_predicted":   prob >= threshold_op,
                "threshold":      threshold_op,
                "primary":        slot_id == 2,
                "source":         "climatology_model_error",
                "model_used":     model_name,
                "error":          str(e),
            })
            continue

        # Build feature vector — start with climatological defaults
        obs = {
            "month": month, "doy": doy,
            "MAX": 30.0, "MIN": 20.0, "RF": 0.0, "AW": 3.0,
            "EVP": 5.0, "DRNRF": 0.0, "SSH": 300.0,
            "RF_3d": 0.0, "RF_7d": 0.0, "MAX_3d_avg": 30.0,
            "MIN_3d_avg": 20.0, "DTR_3d_avg": 10.0,
            "RF_lag1": 0.0, "MAX_lag1": 30.0, "MIN_lag1": 20.0,
            "LABEL_lag1": 0,
            "CAPE": 0.0, "CIN": 0.0, "K_INDEX": 30.0,
            "LIFTED_INDEX": -2.0, "TOTALS_TOTALS": 44.0, "PRECIP_WATER": 40.0,
            "ERA5_T2M": 299.0, "ERA5_D2M": 293.0,
            "ERA5_U10": -2.0, "ERA5_V10": 1.0,
            "ERA5_CAPE": 0.0, "ERA5_SP": 91500.0,
            "ERA5_t_500hPa": 268.0, "ERA5_t_700hPa": 283.0, "ERA5_t_850hPa": 293.0,
            "ERA5_q_500hPa": 0.003, "ERA5_q_700hPa": 0.009, "ERA5_q_850hPa": 0.013,
            "ERA5_u_500hPa": 5.0, "ERA5_u_700hPa": 2.0, "ERA5_u_850hPa": -3.0,
            "ERA5_v_500hPa": 2.0, "ERA5_v_700hPa": 1.0, "ERA5_v_850hPa": 2.0,
            "ts_label_lag1_slot": 0, "ts_any_yesterday": 0,
        }
        data_source = "model_defaults"

        # Override with live GFS fields
        if len(gfs_df) > 0:
            row = gfs_df.iloc[0]
            gfs_cols = [
                "ERA5_T2M", "ERA5_D2M", "ERA5_U10", "ERA5_V10",
                "ERA5_CAPE", "ERA5_SP",
                "ERA5_t_500hPa", "ERA5_t_700hPa", "ERA5_t_850hPa",
                "ERA5_q_500hPa", "ERA5_q_700hPa", "ERA5_q_850hPa",
                "ERA5_u_500hPa", "ERA5_u_700hPa", "ERA5_u_850hPa",
                "ERA5_v_500hPa", "ERA5_v_700hPa", "ERA5_v_850hPa",
                "K_INDEX", "TOTALS_TOTALS", "LIFTED_INDEX", "CAPE",
                "CIN", "PRECIP_WATER",
                "SRH_0_3km", "EHI", "BRN", "wind_shear_500_850_ms",
            ]
            for col in gfs_cols:
                if col in row.index and pd.notna(row[col]):
                    obs[col] = float(row[col])
            data_source = "gfs"

        # Override with upper-air (higher precision than GFS surface-only)
        if slot_id in upper_air:
            ua = upper_air[slot_id]
            ua_cols = [
                "CAPE", "CIN", "K_INDEX", "LIFTED_INDEX", "TOTALS_TOTALS",
                "PRECIP_WATER", "ERA5_u_500hPa", "ERA5_v_500hPa",
                "ERA5_u_850hPa", "ERA5_v_850hPa",
            ]
            for col in ua_cols:
                if col in ua and pd.notna(ua.get(col)):
                    obs[col] = float(ua[col])
            data_source = "gfs+upperair"

        obs = compute_derived(obs, slot_id)

        # Predict
        X   = np.array([[float(obs.get(c, 0.0)) for c in feature_cols]])
        raw = float(model.predict_proba(X)[0][1])
        cal = apply_calibrator(artifact, raw)
        results[slot_id] = cal

        # Determine model version label
        if "v6" in model_name:
            model_ver = "v6_himawari" if "himawari" in model_name else "v6_temporal"
        elif "v5" in model_name:
            model_ver = "v5_temporal"
        elif "v4_ensemble" in model_name:
            model_ver = "v4_ensemble"
        elif "v4" in model_name:
            model_ver = "v4_calibrated"
        elif "v3" in model_name:
            model_ver = "v3_calibrated"
        else:
            model_ver = "v2_calibrated"

        slots_output.append({
            "slot":              slot_id,
            "label":             SLOT_LABELS[slot_id],
            "time":              SLOT_NAMES[slot_id],
            "ts_probability":    round(float(cal), 4),
            "ts_predicted":      float(cal) >= threshold,
            "threshold":         threshold,
            "primary":           slot_id == 2,
            "source":            data_source,
            "model_used":        model_name,
            "model_version":     model_ver,
            "raw_probability":   round(float(raw), 4),
            "cape":              round(obs.get("CAPE", 0), 1),
            "k_index":           round(obs.get("K_INDEX", 0), 1),
            "lifted_index":      round(obs.get("LIFTED_INDEX", 0), 2),
            "totals_totals":     round(obs.get("TOTALS_TOTALS", 0), 1),
            "regime_adjustment": regime_thresh_factor,
        })
        print(f"  Slot {slot_id}: {model_ver}  raw={raw*100:.1f}%  cal={cal*100:.1f}%  "
              f"threshold={threshold}  predicted={'YES' if float(cal) >= threshold else 'NO'}")

    # ── Load previous forecast for trend delta ────────────────────────────────
    prev_probs = {}
    forecast_path = BASE / "forecast.json"
    if forecast_path.exists():
        try:
            with open(forecast_path) as f:
                prev = json.load(f)
            for s in prev.get("slots", []):
                prev_probs[s["slot"]] = s.get("ts_probability", 0.0)
        except Exception:
            pass

    # Inject trend delta into each slot
    TREND_STABLE_BAND = 0.02   # ±2pp = STABLE
    for s in slots_output:
        prev_p = prev_probs.get(s["slot"])
        if prev_p is not None:
            delta = round(s["ts_probability"] - prev_p, 4)
            s["prob_delta"] = delta
            s["trend"] = ("RISING" if delta > TREND_STABLE_BAND
                          else "FALLING" if delta < -TREND_STABLE_BAND
                          else "STABLE")
        else:
            s["prob_delta"] = None
            s["trend"] = "UNKNOWN"

    # ── Aggregate results ─────────────────────────────────────────────────────
    alert_active     = any(s["ts_predicted"] for s in slots_output)
    peak_slot        = max(results, key=results.get)
    peak_probability = results[peak_slot]
    met_slot         = next((s for s in slots_output if s["slot"] == 2), slots_output[0])

    print(f"\n  Alert: {'ACTIVE ⚡' if alert_active else 'None'}  "
          f"Peak: Slot {peak_slot} ({peak_probability*100:.1f}%)")

    # ── Wind fallback chain ───────────────────────────────────────────────────
    _ua_wind = (upper_air.get(2) or upper_air.get(3) or
                upper_air.get(1) or upper_air.get(0) or {})

    def _wind(key: str) -> float:
        v = _ua_wind.get(key)
        try:
            f = float(v)
            return 0.0 if math.isnan(f) else f
        except (TypeError, ValueError):
            return 0.0

    # ── Build forecast dict ───────────────────────────────────────────────────
    forecast = {
        "date":             date_str,
        "generated_at":     now.strftime("%Y-%m-%d %H:%M IST"),
        "alert_active":     alert_active,
        "peak_slot":        peak_slot,
        "peak_probability": round(float(peak_probability), 4),
        "model_version":    "v5_temporal_v4_ensemble",  # production ensemble
        "slots":            slots_output,
        "met_parameters": {
            "ua_cape_jkg":      met_slot.get("cape", 0),
            "ua_cape_raw":      met_slot.get("cape", 0),
            "ua_k_index":       met_slot.get("k_index", 0),
            "ua_lifted_index":  met_slot.get("lifted_index", 0),
            "ua_totals_totals": met_slot.get("totals_totals", 0),
            "ERA5_u_500hPa":    _wind("ERA5_u_500hPa"),
            "ERA5_v_500hPa":    _wind("ERA5_v_500hPa"),
            "ERA5_u_850hPa":    _wind("ERA5_u_850hPa"),
            "ERA5_v_850hPa":    _wind("ERA5_v_850hPa"),
            "instability_level": (
                "Extreme"  if met_slot.get("cape", 0) >= 3000 else
                "High"     if met_slot.get("cape", 0) >= 1500 else
                "Moderate" if met_slot.get("cape", 0) >= 500  else
                "Marginal" if met_slot.get("cape", 0) >= 100  else
                "Stable"
            ),
        },
    }

    # ── Himawari satellite ────────────────────────────────────────────────────
    himawari         = {}
    himawari_history = []
    for p, var in [(DATA / "himawari_realtime.json", "himawari"),
                   (DATA / "himawari_history.json",  "himawari_history")]:
        if p.exists():
            try:
                with open(p) as f:
                    if var == "himawari":
                        himawari = json.load(f)
                        print(f"  Himawari: storm_detected={himawari.get('storm_detected')}  "
                              f"min_bt={himawari.get('min_bt_50km')}°C")
                    else:
                        himawari_history = json.load(f)
            except Exception as e:
                print(f"  Himawari load error ({p.name}): {e}")

    forecast["satellite"] = {
        "himawari9": {
            "timestamp_utc":         himawari.get("timestamp_utc"),
            "timestamp_ist":         himawari.get("timestamp_ist"),
            "vobl_bt_celsius":       himawari.get("vobl_bt_celsius"),
            "min_bt_50km":           himawari.get("min_bt_50km"),
            "mean_bt_50km":          himawari.get("mean_bt_50km"),
            "cold_pixels_count":     himawari.get("cold_pixels_count", 0),
            "storm_detected":        himawari.get("storm_detected", False),
            "alert_level":           himawari.get("alert_level", "GREEN"),
            "nearest_pixel_dist_km": himawari.get("nearest_pixel_dist_km"),
            "threshold_celsius":     himawari.get("threshold_celsius", -40.0),
            "bt_trend_1h":           himawari.get("bt_trend_1h"),
            "data_source":           "Himawari-9 Band 13 (10.4µm) via NOAA AWS S3",
            "available":             bool(himawari),
        },
        "history": himawari_history[-6:] if himawari_history else [],
    }

    # ── Verification ─────────────────────────────────────────────────────────
    verification = {}
    verif_path   = BASE / "results" / "verification_report.json"
    if verif_path.exists():
        try:
            with open(verif_path) as f:
                verification = json.load(f)
            print(f"  Verification: POD={verification.get('pod')}  HSS={verification.get('hss')}")
        except Exception as e:
            print(f"  Verification load error: {e}")

    # ── GFS Tmax / Tmin / Rainfall ────────────────────────────────────────────
    gfs_row = gfs_df.iloc[0] if len(gfs_df) > 0 else {}
    gfs_valid_date_str = now.strftime("%d %b %Y")
    gfs_cycle_str      = str(gfs_row.get("gfs_cycle", "00Z")) if len(gfs_df) > 0 else "N/A"

    if len(gfs_df) > 0:
        # Prefer real multi-hour TMP columns written by gfs_fetcher.py
        tmp_vals = []
        for col in ["TMP_f006", "TMP_f012", "TMP_f018", "TMP_f024"]:
            if col in gfs_df.columns and pd.notna(gfs_df.iloc[0][col]):
                v = float(gfs_df.iloc[0][col])
                if v > 200:   # sanity: must be Kelvin
                    tmp_vals.append(v)

        if len(tmp_vals) >= 2:
            gfs_tmax_k = max(tmp_vals)
            gfs_tmin_k = min(tmp_vals)
            print(f"  Tmax={gfs_tmax_k-273.15:.1f}°C  Tmin={gfs_tmin_k-273.15:.1f}°C  "
                  f"(from {len(tmp_vals)} real TMP columns)")
        else:
            # Fallback: ERA5_T2M ± climatological spread
            base_k = float(gfs_row.get("ERA5_T2M", 302.0))
            # Convert from K if needed, handle Celsius input defensively
            if base_k < 200:
                base_k += 273.15
            gfs_tmax_k = base_k + 5.0
            gfs_tmin_k = base_k - 5.0
            print(f"  Tmax/Tmin: using ERA5_T2M fallback (±5K spread)")

        gfs_apcp_mm = float(gfs_row.get("APCP_surface",
                            gfs_row.get("APCP_24h",
                            gfs_row.get("RF", 0.0))))
    else:
        gfs_tmax_k  = 307.0    # ~33.8°C — Bengaluru Aug climatology
        gfs_tmin_k  = 295.0    # ~21.8°C
        gfs_apcp_mm = 0.0

    forecast["gfs_tmax_c"]      = round(float(gfs_tmax_k - 273.15), 1)
    forecast["gfs_tmin_c"]      = round(float(gfs_tmin_k - 273.15), 1)
    forecast["gfs_rainfall_mm"] = round(float(gfs_apcp_mm), 1)
    forecast["gfs_valid_date"]  = gfs_valid_date_str
    forecast["gfs_cycle"]       = gfs_cycle_str

    # ── Instability score & convective initiation ─────────────────────────────
    cape_now = float(gfs_row.get("CAPE", 0)) if len(gfs_df) > 0 else 0
    ki_now   = float(gfs_row.get("K_INDEX", 30)) if len(gfs_df) > 0 else 30
    li_now   = float(gfs_row.get("LIFTED_INDEX", 0)) if len(gfs_df) > 0 else 0
    tt_now   = float(gfs_row.get("TOTALS_TOTALS", 44)) if len(gfs_df) > 0 else 44

    # Use upper-air for better CAPE/K if available
    if upper_air:
        ua_best = (upper_air.get(2) or upper_air.get(3) or
                   upper_air.get(1) or upper_air.get(0))
        if ua_best:
            cape_now = float(ua_best.get("CAPE", cape_now) or cape_now)
            ki_now   = float(ua_best.get("K_INDEX", ki_now) or ki_now)
            li_now   = float(ua_best.get("LIFTED_INDEX", li_now) or li_now)
            tt_now   = float(ua_best.get("TOTALS_TOTALS", tt_now) or tt_now)

    cape_score = min(cape_now / 2000.0 * 40, 40)
    ki_score   = max(0, min((ki_now - 20) / 20.0 * 30, 30))
    li_score   = max(0, min((-li_now) / 6.0 * 20, 20))
    tt_score   = max(0, min((tt_now - 40) / 10.0 * 10, 10))
    instability_score = round(cape_score + ki_score + li_score + tt_score, 1)

    PEAK_START = 13.0
    PEAK_END   = 18.0
    now_hour_ist = now.hour + now.minute / 60.0

    if now_hour_ist < PEAK_START:
        hours_to_peak      = PEAK_START - now_hour_ist
        initiation_status  = "PRE-CONVECTIVE"
        initiation_message = f"Peak convective window in {hours_to_peak:.1f}h (1300–1800 IST)"
    elif PEAK_START <= now_hour_ist <= PEAK_END:
        hours_to_peak      = 0
        initiation_status  = "CONVECTIVE WINDOW ACTIVE"
        initiation_message = "Currently in peak thunderstorm window (1300–1800 IST)"
    else:
        hours_to_peak      = 24 - now_hour_ist + PEAK_START
        initiation_status  = "POST-CONVECTIVE"
        initiation_message = f"Next peak window in {hours_to_peak:.1f}h (tomorrow 1300 IST)"

    initiation_risk = (
        "HIGH"     if instability_score >= 70 else
        "MODERATE" if instability_score >= 45 else
        "LOW"      if instability_score >= 25 else
        "MINIMAL"
    )

    forecast["convective_initiation"] = {
        "instability_score":  instability_score,
        "initiation_status":  initiation_status,
        "initiation_message": initiation_message,
        "initiation_risk":    initiation_risk,
        "hours_to_peak":      round(hours_to_peak, 1),
        "cape_now":           round(cape_now, 1),
        "ki_now":             round(ki_now, 2),
        "li_now":             round(li_now, 2),
        "tt_now":             round(tt_now, 2),
        "srh_0_3km":          round(float(obs.get("SRH_0_3km", 0) or 0), 2) if len(gfs_df) > 0 else None,
        "ehi":                round(float(obs.get("EHI", 0) or 0), 4)       if len(gfs_df) > 0 else None,
        "brn":                round(float(obs.get("BRN", 0) or 0), 2)       if len(gfs_df) > 0 else None,
        "cape_tendency_jkgh": cape_tendency,
        "cape_trend":         ("BUILDING" if cape_tendency is not None and cape_tendency > 50
                               else "WEAKENING" if cape_tendency is not None and cape_tendency < -50
                               else "STEADY"),
        "monsoon_regime":     monsoon_regime,
        "regime_thresh_factor": regime_thresh_factor,
        "peak_window_ist":    "1300–1800 IST",
        "computed_at":        now.strftime("%Y-%m-%d %H:%M IST"),
    }
    print(f"  Instability: score={instability_score}  risk={initiation_risk}  "
          f"status={initiation_status}")

    # ── Multi-day outlook ─────────────────────────────────────────────────────
    multiday_outlook = []
    multiday_path    = DATA / "gfs_multiday_43295.json"
    if multiday_path.exists():
        try:
            with open(multiday_path) as f:
                multiday_data = json.load(f)
            multiday_data = [r for r in multiday_data
                             if str(r.get("date", "9999")) >= date_str]
            for day_row in multiday_data:
                d_cape = float(day_row.get("CAPE", 0) or 0)
                d_ki   = float(day_row.get("K_INDEX", 30) or 30)
                d_li   = float(day_row.get("LIFTED_INDEX", 0) or 0)
                d_tt   = float(day_row.get("TOTALS_TOTALS", 44) or 44)
                d_score = round(
                    min(d_cape / 2000.0 * 40, 40)
                    + max(0, min((d_ki - 20) / 20.0 * 30, 30))
                    + max(0, min((-d_li) / 6.0 * 20, 20))
                    + max(0, min((d_tt - 40) / 10.0 * 10, 10)),
                    1,
                )
                d_prob = min(round(d_score / 100.0 * 0.6, 3), 0.95)
                risk   = ("HIGH" if d_score >= 70 else "MODERATE" if d_score >= 45
                          else "LOW" if d_score >= 25 else "MINIMAL")
                multiday_outlook.append({
                    "date":                 day_row.get("date"),
                    "day_label":            day_row.get("day_label"),
                    "cape":                 round(d_cape, 1),
                    "k_index":              round(d_ki, 2),
                    "lifted_index":         round(d_li, 2),
                    "totals_totals":        round(d_tt, 2),
                    "instability_score":    d_score,
                    "ts_probability_slot2": d_prob,
                    "risk_level":           risk,
                    "peak_window":          "1300–1800 IST",
                })
        except Exception as e:
            print(f"  Multiday outlook error: {e}")
    forecast["multiday_outlook"] = multiday_outlook

    # ── Historical analogs ────────────────────────────────────────────────────
    analogs = []
    try:
        features_path = DATA / "bengaluru_thunderstorm_features_merged.csv"
        if not features_path.exists():
            features_path = BASE / "bengaluru_thunderstorm_features_merged.csv"
        if features_path.exists():
            df_ana = pd.read_csv(features_path, parse_dates=["date"])
            months_window = [(month - 1) % 12 or 12, month, (month % 12) + 1]
            df_f   = df_ana[df_ana["date"].dt.month.isin(months_window)].copy()
            if "CAPE" in df_f.columns and "K_INDEX" in df_f.columns:
                df_f = df_f.dropna(subset=["CAPE", "K_INDEX"])
                cape_rng = df_f["CAPE"].max() - df_f["CAPE"].min() + 1e-9
                ki_rng   = df_f["K_INDEX"].max() - df_f["K_INDEX"].min() + 1e-9
                li_rng   = df_f["LIFTED_INDEX"].max() - df_f["LIFTED_INDEX"].min() + 1e-9 \
                           if "LIFTED_INDEX" in df_f.columns else 1.0
                df_f["_score"] = (
                    2.0 * (df_f["CAPE"] - cape_now).abs() / cape_rng
                    + 1.5 * (df_f["K_INDEX"] - ki_now).abs() / ki_rng
                    + (1.0 * (df_f["LIFTED_INDEX"] - li_now).abs() / li_rng
                       if "LIFTED_INDEX" in df_f.columns else 0)
                )
                for _, r in df_f.nsmallest(5, "_score").iterrows():
                    analogs.append({
                        "date":         str(r["date"])[:10],
                        "cape":         round(float(r.get("CAPE", 0)), 1),
                        "k_index":      round(float(r.get("K_INDEX", 0)), 1),
                        "lifted_index": round(float(r.get("LIFTED_INDEX", 0)), 2)
                                        if "LIFTED_INDEX" in r.index else None,
                        "thunderstorm": bool(r.get("LABEL", 0)),
                        "month":        int(r["date"].month),
                    })
                print(f"  Analogs: {len(analogs)} days, "
                      f"{sum(1 for a in analogs if a['thunderstorm'])} with TS")
    except Exception as e:
        print(f"  Analog search error: {e}")

    forecast["analogs"] = {
        "top_5":       analogs,
        "ts_rate":     round(sum(1 for a in analogs if a["thunderstorm"]) / len(analogs), 2)
                       if analogs else None,
        "query_cape":  round(cape_now, 1),
        "query_ki":    round(ki_now, 2),
        "query_li":    round(li_now, 2),
        "computed_at": now.strftime("%Y-%m-%d %H:%M IST"),
    }

    # ── Airport impact ────────────────────────────────────────────────────────
    SLOT_DEPARTURES   = {0: 8, 1: 45, 2: 52, 3: 38}
    DISRUPTION_FACTOR = 0.60
    impact_slots      = []
    total_disrupted   = 0
    total_departures  = 0

    for s in slots_output:
        sid      = s["slot"]
        prob     = s.get("ts_probability", 0) or 0
        deps     = SLOT_DEPARTURES.get(sid, 0)
        disrupted = round(prob * deps * DISRUPTION_FACTOR)
        total_disrupted  += disrupted
        total_departures += deps
        impact_slots.append({
            "slot":           sid,
            "label":          s.get("label", ""),
            "ts_probability": round(prob, 4),
            "departures":     deps,
            "disrupted_est":  disrupted,
            "impact_pct":     round(prob * DISRUPTION_FACTOR * 100, 1),
        })

    overall_risk = ("HIGH"     if total_disrupted >= 20 else
                    "MODERATE" if total_disrupted >= 8  else
                    "LOW"      if total_disrupted >= 2  else
                    "MINIMAL")
    forecast["airport_impact"] = {
        "total_departures_today": total_departures,
        "total_disrupted_est":    total_disrupted,
        "disruption_pct":         round(total_disrupted / total_departures * 100, 1)
                                  if total_departures else 0,
        "overall_risk":           overall_risk,
        "disruption_factor":      DISRUPTION_FACTOR,
        "slots":                  impact_slots,
        "computed_at":            now.strftime("%Y-%m-%d %H:%M IST"),
    }
    print(f"  Airport: {total_disrupted} disrupted / {total_departures} departures → {overall_risk}")

    # ── Synoptic regime ───────────────────────────────────────────────────────
    try:
        t2m_c = (float(gfs_row.get("ERA5_T2M", 302)) - 273.15) if len(gfs_df) > 0 else 28.0
        if t2m_c > 100:   # still in Kelvin
            t2m_c -= 273.15

        if ki_now >= 38 and cape_now >= 800:
            r = ("R5", "Pre-Monsoon Convective Burst", 52.1, 0.773, "red",
                 "Severe convective instability — highest TS rate regime")
        elif ki_now >= 35 and cape_now >= 300 and month in [5, 6, 7, 8, 9]:
            r = ("R2", "Moist Monsoon", 9.3, 0.934, "yellow",
                 "Monsoonal westerly surge with high skill forecast")
        elif ki_now >= 32 and cape_now >= 100 and t2m_c >= 28:
            r = ("R4", "Strong Solar Heating", 9.8, 0.900, "orange",
                 "Strong surface heating with mid-level moisture")
        elif cape_now < 100 and ki_now < 30 and month in [6, 7, 8, 9]:
            r = ("R3", "Break Monsoon", 10.2, 0.798, "blue",
                 "Break-monsoon stratiform clouding, suppressed convection")
        else:
            r = ("R1", "Hot Pre-Monsoon / Stable", 1.2, 1.000, "green",
                 "Dry thermal low baseline, low storm occurrence")

        forecast["synoptic_regime"] = {
            "regime_id":   r[0], "regime_name": r[1],
            "ts_rate":     r[2], "auroc":       r[3],
            "description": r[5], "color":       r[4],
            "cape_used":   round(cape_now, 1), "ki_used":  round(ki_now, 2),
            "t2m_c":       round(t2m_c, 1),   "month":    month,
            "computed_at": now.strftime("%Y-%m-%d %H:%M IST"),
        }
        print(f"  Synoptic regime: {r[0]} — {r[1]} (TS rate: {r[2]}%)")
    except Exception as e:
        print(f"  Regime detection error: {e}")
        forecast["synoptic_regime"] = {}

    # ── Verification metrics ──────────────────────────────────────────────────
    slot2_30d = verification.get("metrics_30day", {}).get("2", {})
    forecast["verification"] = {
        "pod":           round(float(slot2_30d.get("POD", 0)), 3) if slot2_30d else None,
        "far":           round(float(slot2_30d.get("FAR", 0)), 3) if slot2_30d else None,
        "hss":           round(float(slot2_30d.get("HSS", 0)), 3) if slot2_30d else None,
        "brier":         round(float(slot2_30d.get("Brier", 0)), 4) if slot2_30d else None,
        "csi":           round(float(slot2_30d.get("CSI", 0)), 3) if slot2_30d else None,
        "n_days":        slot2_30d.get("n_days"),
        "n_ts":          slot2_30d.get("n_ts"),
        "date_verified": verification.get("generated_at"),
        "slot":          2,
        "window":        "30-day",
        "available":     bool(slot2_30d),
        "all_slots_30d": {
            str(k): {
                "pod":   round(float(v.get("POD", 0)), 3),
                "far":   round(float(v.get("FAR", 0)), 3),
                "hss":   round(float(v.get("HSS", 0)), 3),
                "brier": round(float(v.get("Brier", 0)), 4),
            }
            for k, v in verification.get("metrics_30day", {}).items()
        },
    }

    # ── Probability trend vs previous run ────────────────────────────────────
    prev_probs = {}
    if Path("forecast.json").exists():
        try:
            with open("forecast.json") as f:
                prev = json.load(f)
            for s in prev.get("slots", []):
                prev_probs[s["slot"]] = s.get("ts_probability", 0) or 0
        except Exception:
            pass

    for s in forecast["slots"]:
        sid    = s["slot"]
        curr   = s.get("ts_probability") or 0
        prev_p = prev_probs.get(sid, curr)
        diff   = round(curr - prev_p, 3)
        s["trend"]            = "up" if diff > 0.01 else "down" if diff < -0.01 else "stable"
        s["trend_diff"]       = diff
        s["prev_probability"] = round(prev_p, 3)

    # ── Load SHAP if available (computed separately by compute_realtime_shap.py) ──
    shap_path = DATA / "realtime_shap.json"
    if shap_path.exists():
        try:
            with open(shap_path) as f:
                forecast["realtime_shap"] = json.load(f)
            print(f"  SHAP: loaded from {shap_path}")
        except Exception as e:
            print(f"  SHAP load error: {e}")

    # ── Load METAR if available ───────────────────────────────────────────────
    metar_available   = False
    metar_ts_override = False
    forecast_metar_exists = False
    if Path("forecast.json").exists():
        try:
            with open("forecast.json") as f:
                prev_fc = json.load(f)
            if "metar" in prev_fc:
                forecast["metar"] = prev_fc["metar"]
                metar_available   = True
                forecast_metar_exists = True
        except Exception:
            pass
    if not forecast_metar_exists:
        print("  METAR: not yet available (will be injected by fetch_metar.py)")

    # METAR TS override — if METAR confirms active thunderstorm, force alert
    if metar_available and isinstance(forecast.get("metar"), dict):
        if forecast["metar"].get("thunderstorm_present"):
            metar_ts_override = True
            alert_active      = True   # force alert regardless of model
            forecast["alert_active"] = True
            # Boost current-window slot probability to at minimum 0.85
            for s in forecast["slots"]:
                if s.get("ts_probability", 0) < 0.85:
                    s["ts_probability_pre_metar"] = s["ts_probability"]
                    s["ts_probability"]  = 0.85
                    s["ts_predicted"]    = True
                    s["metar_override"]  = True
            forecast["metar_ts_override"] = True
            print(f"  ⚡ METAR ACTIVE TS — override: alert_active forced TRUE, "
                  f"slot probs floored at 0.85")

    # ── SIGMET bulletin ───────────────────────────────────────────────────────
    sigmet_text = None
    if alert_active or metar_ts_override:
        valid_start = now.strftime("%H%M")
        valid_end   = (now + timedelta(hours=6)).strftime("%H%M")
        date_sig    = now.strftime("%d/%b/%Y").upper()
        intensity   = ("SEVERE"   if peak_probability >= 0.70 else
                       "MODERATE" if peak_probability >= 0.40 else
                       "LIGHT")
        tops_fl     = ("FL400" if intensity == "SEVERE" else
                       "FL350" if intensity == "MODERATE" else "FL300")
        sigmet_text = (
            f"VCBB SIGMET X01 VALID {valid_start}/{valid_end} UTC {date_sig}\n"
            f"VOBL FIR THUNDERSTORM FCST\n"
            f"TS {intensity} OBS/FCST AT {valid_start} UTC\n"
            f"TOP {tops_fl} CB\n"
            f"MOV NE 10KT\n"
            f"INTENSITY {'INTSF' if cape_tendency is not None and cape_tendency > 50 else 'NC'}\n"
            f"FCST AT {valid_end} UTC TS {intensity} STNR\n"
            f"NC="
        )
        print(f"  SIGMET generated ({intensity}) valid {valid_start}–{valid_end} UTC")
    forecast["sigmet_bulletin"] = sigmet_text

    # ── Pipeline health ───────────────────────────────────────────────────────
    pipeline_health = build_pipeline_health(now, gfs_df, himawari, metar_available, upper_air)
    forecast["pipeline_health"] = pipeline_health

    health_path = DATA / "pipeline_health.json"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    with open(health_path, "w") as f:
        json.dump(pipeline_health, f, indent=2)
    print(f"  Pipeline health → {health_path}")

    # ── Write forecast.json ───────────────────────────────────────────────────
    with open("forecast.json", "w") as f:
        json.dump(forecast, f, indent=2)

    print("\n" + "=" * 65)
    print(f"  forecast.json written — alert={alert_active}  "
          f"peak=Slot{peak_slot} {peak_probability*100:.1f}%")
    print(f"  Tmax={forecast['gfs_tmax_c']}°C  Tmin={forecast['gfs_tmin_c']}°C  "
          f"Rain={forecast['gfs_rainfall_mm']}mm")
    print(f"  Wind u500={forecast['met_parameters']['ERA5_u_500hPa']}  "
          f"u850={forecast['met_parameters']['ERA5_u_850hPa']}")
    print("=" * 65)

    # ── Append to forecast_log.csv (needed by verify_today + skill_scores) ────
    try:
        import csv
        log_path = DATA / "forecast_log.csv"
        log_cols = [
            "date", "slot", "ts_probability", "ts_predicted", "threshold",
            "model_used", "model_version", "source",
            "cape", "k_index", "lifted_index", "totals_totals",
            "monsoon_regime", "regime_adjustment", "cape_tendency_jkgh",
            "alert_active", "generated_at",
        ]
        write_header = not log_path.exists()
        with open(log_path, "a", newline="") as csvf:
            writer = csv.DictWriter(csvf, fieldnames=log_cols, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            for s in slots_output:
                writer.writerow({
                    "date":               date_str,
                    "slot":               s["slot"],
                    "ts_probability":     s.get("ts_probability", 0),
                    "ts_predicted":       int(s.get("ts_predicted", False)),
                    "threshold":          s.get("threshold", 0),
                    "model_used":         s.get("model_used", ""),
                    "model_version":      s.get("model_version", ""),
                    "source":             s.get("source", ""),
                    "cape":               s.get("cape", 0),
                    "k_index":            s.get("k_index", 0),
                    "lifted_index":       s.get("lifted_index", 0),
                    "totals_totals":      s.get("totals_totals", 0),
                    "monsoon_regime":     monsoon_regime,
                    "regime_adjustment":  regime_thresh_factor,
                    "cape_tendency_jkgh": cape_tendency if cape_tendency is not None else "",
                    "alert_active":       int(alert_active),
                    "generated_at":       now.strftime("%Y-%m-%d %H:%M IST"),
                })
        print(f"  Forecast log appended → {log_path}")
    except Exception as e:
        print(f"  Forecast log error (non-fatal): {e}")

    # ── Alert history log ─────────────────────────────────────────────────────
    try:
        alert_hist_path = DATA / "alert_history.json"
        alert_history = []
        if alert_hist_path.exists():
            with open(alert_hist_path) as f:
                alert_history = json.load(f)

        if alert_active:
            peak_s = next((s for s in slots_output if s["slot"] == peak_slot), {})
            alert_history.append({
                "date":             date_str,
                "generated_at":     now.strftime("%Y-%m-%d %H:%M IST"),
                "peak_slot":        peak_slot,
                "peak_slot_label":  SLOT_LABELS.get(peak_slot, ""),
                "peak_probability": round(float(peak_probability), 4),
                "monsoon_regime":   monsoon_regime,
                "cape":             peak_s.get("cape", 0),
                "k_index":          peak_s.get("k_index", 0),
                "metar_override":   metar_ts_override,
                "sigmet_intensity": (
                    "SEVERE"   if peak_probability >= 0.70 else
                    "MODERATE" if peak_probability >= 0.40 else
                    "LIGHT"    if peak_probability > 0    else None
                ),
            })
            # Keep last 90 alert events
            alert_history = alert_history[-90:]
            with open(alert_hist_path, "w") as f:
                json.dump(alert_history, f, indent=2)
            print(f"  Alert history updated → {alert_hist_path}  "
                  f"({len(alert_history)} events)")
        else:
            print(f"  No alert — history unchanged ({len(alert_history)} past events)")

        forecast["alert_history_count"] = len(alert_history)
        forecast["recent_alerts"] = alert_history[-5:]   # last 5 for dashboard
    except Exception as e:
        print(f"  Alert history error (non-fatal): {e}")


if __name__ == "__main__":
    # ── Outer safety net ──────────────────────────────────────────────────────
    # If main() raises an unhandled exception, we still write a minimal
    # forecast.json with a crash flag so the GitHub Actions commit step
    # always has something to push and the dashboard shows an error banner
    # rather than serving a 22-day-stale file indefinitely.
    try:
        main()
    except Exception as _fatal:
        import traceback, sys
        tb = traceback.format_exc()
        print("\n" + "=" * 65)
        print("  FATAL: forecast_action.py crashed — writing emergency forecast.json")
        print(tb)
        print("=" * 65)

        now_utc = datetime.now(timezone.utc)
        IST_OFF = timedelta(hours=5, minutes=30)
        now_ist = now_utc + IST_OFF

        emergency = {
            "generated_at_utc":  now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generated_at_ist":  now_ist.strftime("%Y-%m-%d %H:%M IST"),
            "pipeline_status":   "CRASH",
            "crash_traceback":   tb,
            "alert_active":      False,
            "peak_slot":         None,
            "peak_probability":  0.0,
            "slots": [],
            "convective_initiation": {
                "monsoon_regime": "UNKNOWN",
                "cape_now": 0,
                "k_index_now": 0,
            },
            "gfs_tmax_c": None,
            "gfs_tmin_c": None,
            "gfs_rainfall_mm": None,
            "sigmet_bulletin": None,
            "met_parameters": {},
        }

        # Write pipeline_health crash record
        try:
            health_path = Path("data") / "pipeline_health.json"
            health_path.parent.mkdir(parents=True, exist_ok=True)
            with open(health_path, "w") as _f:
                json.dump({
                    "generated_at_utc": emergency["generated_at_utc"],
                    "generated_at_ist": emergency["generated_at_ist"],
                    "pipeline_status": "CRASH",
                    "error": str(_fatal),
                    "components": {},
                }, _f, indent=2)
        except Exception:
            pass

        with open("forecast.json", "w") as _f:
            json.dump(emergency, _f, indent=2)
        print("  Emergency forecast.json written — CI will commit and push this.")
        sys.exit(1)   # Non-zero exit so GitHub Actions marks the step as failed (visible in logs)
