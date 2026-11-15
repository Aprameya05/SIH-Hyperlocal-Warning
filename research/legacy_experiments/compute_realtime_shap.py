"""
compute_realtime_shap.py
========================
CSIR Thunderstorm Nowcasting System — Real-Time SHAP Explainability

Computes SHAP values for today's GFS inputs against all production slot models.
Saves results to data/realtime_shap.json — forecast_action.py reads this file
(never calls this script via subprocess; it is a separate GitHub Actions step).

Designed to be robust: individual slot failures do not abort the run.
Exit code is always 0 so Actions continues after this step even on partial errors.

Usage:
  python compute_realtime_shap.py
  python compute_realtime_shap.py --slot 2    # compute for one slot only

Output: data/realtime_shap.json
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BASE   = Path(".")
MODELS = BASE / "models"
DATA   = BASE / "data"
OUT    = DATA / "realtime_shap.json"

IST = timezone(timedelta(hours=5, minutes=30))

# Production model priority — must match forecast_action.py
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


def find_model(slot_id: int) -> Path | None:
    for candidate in SLOT_MODEL_PRIORITY[slot_id]:
        p = MODELS / candidate
        if p.exists():
            return p
    return None


def build_obs(gfs_row: pd.Series | None, slot_id: int) -> dict:
    now = datetime.now(IST)
    m   = now.month
    doy = now.timetuple().tm_yday

    obs = {
        "month": m, "doy": doy,
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

    if gfs_row is not None:
        for col in gfs_row.index:
            if col in obs and pd.notna(gfs_row[col]):
                obs[col] = float(gfs_row[col])
        for col in ["K_INDEX", "TOTALS_TOTALS", "LIFTED_INDEX", "CAPE",
                    "CIN", "PRECIP_WATER"]:
            if col in gfs_row.index and pd.notna(gfs_row[col]):
                obs[col] = float(gfs_row[col])

    # Derived features (mirror forecast_action.py compute_derived)
    obs["DTR"]        = obs["MAX"] - obs["MIN"]
    obs["HA_flag"]    = 0
    obs["RF_nonzero"] = 1 if obs["RF"] > 0 else 0
    obs["SEASON"]     = (1 if m in [3,4,5] else 2 if m in [6,7,8,9]
                         else 3 if m in [10,11] else 0)
    obs["MONTH_sin"]  = math.sin(2 * math.pi * m / 12)
    obs["MONTH_cos"]  = math.cos(2 * math.pi * m / 12)
    obs["DOY_sin"]    = math.sin(2 * math.pi * doy / 365)
    obs["DOY_cos"]    = math.cos(2 * math.pi * doy / 365)
    obs["doy_sin"]    = obs["DOY_sin"]
    obs["doy_cos"]    = obs["DOY_cos"]
    obs["slot_sin"]   = math.sin(2 * math.pi * slot_id / 4)
    obs["slot_cos"]   = math.cos(2 * math.pi * slot_id / 4)
    CLIM = {(4,2):0.129,(5,2):0.194,(6,2):0.096,(7,2):0.032,
            (8,2):0.052,(9,2):0.079,(10,2):0.077}
    obs["slot_month_clim"] = CLIM.get((m, slot_id), 0.02)
    obs["ERA5_CAPE"] = obs.get("ERA5_CAPE") or obs["CAPE"]

    q850 = obs["ERA5_q_850hPa"]; q700 = obs["ERA5_q_700hPa"]
    q500 = obs["ERA5_q_500hPa"]; t850 = obs["ERA5_t_850hPa"]
    t500 = obs["ERA5_t_500hPa"]; u850 = obs["ERA5_u_850hPa"]
    v850 = obs["ERA5_v_850hPa"]; u700 = obs["ERA5_u_700hPa"]
    v700 = obs["ERA5_v_700hPa"]; u500 = obs["ERA5_u_500hPa"]
    v500 = obs["ERA5_v_500hPa"]
    CAPE = obs["CAPE"]; K = obs["K_INDEX"]
    LI = obs["LIFTED_INDEX"]; TT = obs["TOTALS_TOTALS"]

    obs["cape_x_kindex"]      = CAPE * K
    obs["li_x_totals"]        = abs(LI) * TT
    obs["q_gradient_500_850"] = q850 - q500
    obs["thetae_850"]         = t850 + 2491 * q850
    obs["wind_shear_500_850"] = ((u500-u850)**2 + (v500-v850)**2)**0.5
    obs["wind_shear_700_850"] = ((u700-u850)**2 + (v700-v850)**2)**0.5
    obs["moisture_flux_850"]  = q850 * (u850**2 + v850**2)**0.5
    obs["moisture_flux_700"]  = q700 * (u700**2 + v700**2)**0.5
    obs["thickness_500_850"]  = t850 - t500
    obs["mid_level_drying"]   = q700 / (q850 + 1e-9)

    return obs


def compute_shap_for_slot(slot_id: int, gfs_row: pd.Series | None) -> dict | None:
    model_path = find_model(slot_id)
    if model_path is None:
        print(f"    Slot {slot_id}: no model found — skipping")
        return None

    artifact     = joblib.load(model_path)
    model        = artifact.get("model") or artifact.get("calibrated")
    feature_cols = artifact.get("feature_cols") or artifact.get("features") or []
    threshold    = artifact.get("threshold", 0.16)
    if model is None or not feature_cols:
        print(f"    Slot {slot_id}: missing model/features in artifact. Keys: {list(artifact.keys())}")
        return None

    obs = build_obs(gfs_row, slot_id)
    X   = np.array([[float(obs.get(c, 0.0)) for c in feature_cols]])
    df  = pd.DataFrame(X, columns=feature_cols)

    try:
        import shap
    except ImportError:
        print("    shap not installed — pip install shap")
        return None

    explainer   = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(df)

    # Handle binary and multi-output SHAP values
    if isinstance(shap_values, list):
        sv = shap_values[1][0]           # class 1 for binary
    elif hasattr(shap_values, "ndim") and shap_values.ndim == 3:
        sv = shap_values[0, :, 1]        # (n_samples, n_features, n_classes)
    else:
        sv = shap_values[0]

    prob = float(model.predict_proba(X)[0][1])

    base_val = explainer.expected_value
    if isinstance(base_val, (list, np.ndarray)):
        base_val = float(base_val[1])
    else:
        base_val = float(base_val)

    feature_shap = sorted(zip(feature_cols, sv.tolist()),
                          key=lambda x: abs(x[1]), reverse=True)

    return {
        "slot":       slot_id,
        "model_used": model_path.name,
        "prob":       round(prob, 4),
        "threshold":  threshold,
        "base_value": round(base_val, 4),
        "top_features": [
            {
                "feature":   f,
                "shap":      round(v, 5),
                "value":     round(float(obs.get(f, 0.0)), 4),
                "direction": "increases_risk" if v > 0 else "decreases_risk",
            }
            for f, v in feature_shap[:12]   # top 12 for richer dashboard display
        ],
        "computed_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, choices=[0, 1, 2, 3], default=None)
    args = ap.parse_args()

    print("=" * 60)
    print("  compute_realtime_shap.py — Real-Time SHAP")
    print("=" * 60)

    # Load GFS data (use today's date, first row)
    now      = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")
    gfs_path = DATA / "gfs_realtime_43295.csv"
    gfs_row  = None

    if gfs_path.exists():
        gfs_df = pd.read_csv(gfs_path)
        if "date" in gfs_df.columns:
            gfs_df = gfs_df[gfs_df["date"] == date_str]
        if len(gfs_df) > 0:
            gfs_row = gfs_df.iloc[0]
            print(f"  GFS loaded: cycle={gfs_row.get('gfs_cycle', 'N/A')}")
        else:
            print("  GFS: no rows for today — using defaults")
    else:
        print("  GFS: file not found — using defaults")

    slots_to_run = [args.slot] if args.slot is not None else [0, 1, 2, 3]
    results = {}

    # Load existing results (preserve other slots if only running one)
    if OUT.exists():
        try:
            with open(OUT) as f:
                results = json.load(f)
        except Exception:
            results = {}

    for slot_id in slots_to_run:
        print(f"\n  Computing SHAP for Slot {slot_id}...")
        try:
            result = compute_shap_for_slot(slot_id, gfs_row)
            if result:
                results[str(slot_id)] = result
                top = result["top_features"][0]
                print(f"  ✓ Top: {top['feature']} "
                      f"(SHAP={top['shap']:.4f}, {top['direction']})")
            else:
                print(f"  ✗ Slot {slot_id}: no result")
        except Exception as e:
            print(f"  ✗ Slot {slot_id} error: {type(e).__name__}: {e}")
            # Don't re-raise — keep partial results

    DATA.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Saved → {OUT}  ({len(results)} slot(s))")

    # Always exit 0 so GitHub Actions doesn't abort the workflow
    sys.exit(0)


if __name__ == "__main__":
    main()
