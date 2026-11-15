"""
predict_nowcast.py
==================
6-Hour Thunderstorm Nowcast for Bengaluru Airport (Station 43295).

Loads all 4 per-slot XGBoost models and runs a full day forecast.
Can be run in two modes:

  1. DEMO mode (default) — uses a real test case from 2023-04-29
     python predict_nowcast.py

  2. DATE mode — looks up a date from the training dataset
     python predict_nowcast.py --date 2023-10-11

  3. LIVE mode — paste in today's observations manually
     python predict_nowcast.py --live

Author: Aprameya, CSIR Thunderstorm Project
"""

import pandas as pd
import numpy as np
import joblib
import argparse
from pathlib import Path
from datetime import datetime

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE   = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
MODELS = BASE / "models"
DATA   = BASE / "data" / "bengaluru_6hr_training_dataset.csv"

SLOT_NAMES = {
    0: "0001-0600 IST",
    1: "0601-1200 IST",
    2: "1201-1800 IST",
    3: "1801-2400 IST",
}

SLOT_EMOJI = {0: "🌙", 1: "🌅", 2: "☀️ ", 3: "🌆"}

# ── LOAD ALL 4 SLOT MODELS ────────────────────────────────────────────────────
def load_models():
    models = {}
    for slot_id in range(4):
        path = MODELS / f"nowcast_slot{slot_id}_xgb_v2.pkl"
        if not path.exists():
            raise FileNotFoundError(
                f"Model not found: {path}\n"
                f"Run A3_slot_models.py first."
            )
        artifact = joblib.load(path)
        models[slot_id] = artifact
    return models

# ── COMPUTE DERIVED FEATURES ──────────────────────────────────────────────────
def compute_derived(obs: dict, slot_id: int) -> dict:
    """
    Compute all features that can be derived from raw observations.
    obs must contain: date, MAX, MIN, AW, RF, EVP, DRNRF, SSH,
                      RF_3d, RF_7d, MAX_3d_avg, MIN_3d_avg, DTR_3d_avg,
                      RF_lag1, MAX_lag1, MIN_lag1, LABEL_lag1,
                      CAPE, K_INDEX, LIFTED_INDEX, TOTALS_TOTALS, PRECIP_WATER,
                      ERA5_T2M, ERA5_D2M, ERA5_U10, ERA5_V10, ERA5_CAPE,
                      ERA5_SP, ERA5_t_500hPa, ERA5_t_700hPa, ERA5_t_850hPa,
                      ERA5_q_500hPa, ERA5_q_700hPa, ERA5_q_850hPa,
                      ERA5_u_500hPa, ERA5_u_700hPa, ERA5_u_850hPa,
                      ERA5_v_500hPa, ERA5_v_700hPa, ERA5_v_850hPa,
                      ts_label_lag1_slot, ts_any_yesterday
    """
    date = pd.Timestamp(obs['date'])
    doy  = date.dayofyear

    # Derived features
    obs['DTR']          = obs['MAX'] - obs['MIN']
    obs['HA_flag']      = 1 if obs.get('HA_flag', 0) else 0
    obs['RF_nonzero']   = 1 if obs['RF'] > 0 else 0

    # Season (1=pre-monsoon Mar-May, 2=monsoon Jun-Sep, 3=post-monsoon Oct-Nov, 0=winter)
    m = date.month
    if m in [3, 4, 5]:   obs['SEASON'] = 1
    elif m in [6,7,8,9]: obs['SEASON'] = 2
    elif m in [10, 11]:  obs['SEASON'] = 3
    else:                 obs['SEASON'] = 0

    # Cyclical encodings
    obs['MONTH_sin'] = np.sin(2 * np.pi * m / 12)
    obs['MONTH_cos'] = np.cos(2 * np.pi * m / 12)
    obs['DOY_sin']   = np.sin(2 * np.pi * doy / 365)
    obs['DOY_cos']   = np.cos(2 * np.pi * doy / 365)
    obs['doy_sin']   = obs['DOY_sin']
    obs['doy_cos']   = obs['DOY_cos']

    # Slot encodings
    obs['slot_sin'] = np.sin(2 * np.pi * slot_id / 4)
    obs['slot_cos'] = np.cos(2 * np.pi * slot_id / 4)

    # Slot-month climatology (from training data 2015-2022)
    CLIM = {
        (1,0):0.000,(1,1):0.000,(1,2):0.000,(1,3):0.000,
        (2,0):0.000,(2,1):0.000,(2,2):0.009,(2,3):0.013,
        (3,0):0.008,(3,1):0.004,(3,2):0.036,(3,3):0.028,
        (4,0):0.025,(4,1):0.008,(4,2):0.129,(4,3):0.100,
        (5,0):0.129,(5,1):0.036,(5,2):0.194,(5,3):0.181,
        (6,0):0.042,(6,1):0.004,(6,2):0.096,(6,3):0.092,
        (7,0):0.028,(7,1):0.004,(7,2):0.032,(7,3):0.044,
        (8,0):0.020,(8,1):0.008,(8,2):0.052,(8,3):0.060,
        (9,0):0.083,(9,1):0.029,(9,2):0.079,(9,3):0.067,
        (10,0):0.077,(10,1):0.024,(10,2):0.077,(10,3):0.077,
        (11,0):0.008,(11,1):0.008,(11,2):0.021,(11,3):0.017,
        (12,0):0.000,(12,1):0.000,(12,2):0.000,(12,3):0.000,
    }
    obs['slot_month_clim'] = CLIM.get((m, slot_id), 0.0)

    return obs

# ── BUILD FEATURE VECTOR ──────────────────────────────────────────────────────
def build_feature_vector(obs: dict, feature_cols: list) -> np.ndarray:
    vec = []
    missing = []
    for col in feature_cols:
        if col in obs:
            vec.append(float(obs[col]))
        else:
            vec.append(0.0)
            missing.append(col)
    if missing:
        print(f"  ⚠ Missing features (defaulted to 0): {missing}")
    return np.array(vec).reshape(1, -1)

# ── PRINT FORECAST ────────────────────────────────────────────────────────────
def print_forecast(date_str, results, actual_labels=None):
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║     CSIR THUNDERSTORM NOWCAST — Bengaluru Airport (43295)   ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print(f"║  Date : {date_str:<53}║")
    print(f"║  Run  : {datetime.now().strftime('%Y-%m-%d %H:%M IST'):<53}║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print(f"║  {'Slot':<6} {'Window':<16} {'Prob':>6}  {'Bar':<20} {'Call':<8}║")
    print("╠══════════════════════════════════════════════════════════════╣")

    alert_slots = []
    for slot_id, res in results.items():
        prob      = res['probability']
        predicted = res['predicted']
        thresh    = res['threshold']
        emoji     = SLOT_EMOJI[slot_id]

        # Probability bar
        filled = int(prob * 20)
        bar    = "█" * filled + "░" * (20 - filled)

        # Call
        if predicted:
            call = "⚠ YES"
            alert_slots.append(slot_id)
        else:
            call = "  NO "

        # Actual label if available
        actual_str = ""
        if actual_labels is not None:
            actual = actual_labels.get(slot_id)
            if actual == 1:
                actual_str = " ✓HIT" if predicted else " ✗MISS"
            else:
                actual_str = " ✗FA " if predicted else " ✓TN "

        print(f"║  {emoji} {slot_id}  {SLOT_NAMES[slot_id]:<16} "
              f"{prob*100:>5.1f}%  {bar}  {call}{actual_str:<5}║")

    print("╠══════════════════════════════════════════════════════════════╣")

    if alert_slots:
        windows = [SLOT_NAMES[s] for s in alert_slots]
        print(f"║  🔴 ALERT: Thunderstorm likely in {len(alert_slots)} window(s)          ║")
        peak = max(alert_slots, key=lambda s: results[s]['probability'])
        print(f"║     Peak risk: {SLOT_NAMES[peak]:<47}║")
    else:
        print(f"║  🟢 ALL CLEAR: No thunderstorm predicted today              ║")

    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    # Probability summary
    print("  Slot probabilities:")
    for slot_id, res in results.items():
        bar = "█" * int(res['probability'] * 40)
        print(f"  Slot {slot_id}  {res['probability']*100:5.1f}%  {bar}")
    print()

# ── DEMO MODE — real test case 2023-04-29 ────────────────────────────────────
DEMO_OBS = {
    "date":         "2023-04-29",
    "MAX":          34.5,   "MIN":          22.2,
    "AW":           3.0,    "RF":           0.0,
    "EVP":          6.0,    "DRNRF":        0.0,    "SSH": 426.0,
    "RF_3d":        0.0,    "RF_7d":        6.8,
    "MAX_3d_avg":   33.27,  "MIN_3d_avg":   22.97,  "DTR_3d_avg": 10.3,
    "RF_lag1":      0.0,    "MAX_lag1":     33.5,   "MIN_lag1":   22.0,
    "LABEL_lag1":   0,
    "CAPE":         177.25, "K_INDEX":      39.38,
    "LIFTED_INDEX": -6.15,  "TOTALS_TOTALS":46.78,  "PRECIP_WATER": 40.55,
    "ERA5_T2M":     300.12, "ERA5_D2M":     294.73,
    "ERA5_U10":     -3.96,  "ERA5_V10":     2.39,
    "ERA5_CAPE":    177.25, "ERA5_SP":      91197.0,
    "ERA5_t_500hPa":268.44, "ERA5_t_700hPa":283.67, "ERA5_t_850hPa":293.79,
    "ERA5_q_500hPa":0.00235,"ERA5_q_700hPa":0.00947,"ERA5_q_850hPa":0.01404,
    "ERA5_u_500hPa":5.16,   "ERA5_u_700hPa":0.44,   "ERA5_u_850hPa":-7.54,
    "ERA5_v_500hPa":2.34,   "ERA5_v_700hPa":-0.14,  "ERA5_v_850hPa":4.21,
    "ts_label_lag1_slot": 0,
    "ts_any_yesterday":   0,
}

# Actual labels for this demo date (from our test set)
DEMO_ACTUAL = {0: 0, 1: 0, 2: 1, 3: 1}   # TS occurred in Slots 2 and 3

# ── LIVE INPUT ────────────────────────────────────────────────────────────────
def get_live_input():
    print("\n=== LIVE INPUT MODE ===")
    print("Enter today's observations (press Enter to skip optional fields)\n")
    obs = {}
    obs['date'] = input("Date (YYYY-MM-DD): ").strip() or datetime.now().strftime("%Y-%m-%d")

    print("\n-- Surface observations (from IMD 0830 report) --")
    obs['MAX']   = float(input("MAX temperature (°C): "))
    obs['MIN']   = float(input("MIN temperature (°C): "))
    obs['AW']    = float(input("Avg wind speed (km/h): ") or 0)
    obs['RF']    = float(input("Rainfall last 24h (mm): ") or 0)
    obs['EVP']   = float(input("Evaporation (mm) [optional, default 5]: ") or 5)
    obs['DRNRF'] = float(input("Duration of rainfall (mins) [optional, default 0]: ") or 0)
    obs['SSH']   = float(input("Sunshine duration (mins) [optional, default 300]: ") or 300)

    print("\n-- Rolling/lag features (leave blank to use defaults) --")
    obs['RF_3d']      = float(input("3-day rainfall sum (mm) [default 0]: ") or 0)
    obs['RF_7d']      = float(input("7-day rainfall sum (mm) [default 0]: ") or 0)
    obs['MAX_3d_avg'] = float(input("3-day avg MAX temp [default = today MAX]: ") or obs['MAX'])
    obs['MIN_3d_avg'] = float(input("3-day avg MIN temp [default = today MIN]: ") or obs['MIN'])
    obs['DTR_3d_avg'] = float(input("3-day avg DTR [default = today DTR]: ") or (obs['MAX']-obs['MIN']))
    obs['RF_lag1']    = float(input("Yesterday's rainfall (mm) [default 0]: ") or 0)
    obs['MAX_lag1']   = float(input("Yesterday's MAX temp [default = today MAX]: ") or obs['MAX'])
    obs['MIN_lag1']   = float(input("Yesterday's MIN temp [default = today MIN]: ") or obs['MIN'])
    obs['LABEL_lag1'] = int(input("Did yesterday have a thunderstorm? (1/0) [default 0]: ") or 0)

    print("\n-- Stability indices (from IGRA/ERA5) --")
    obs['CAPE']          = float(input("CAPE (J/kg) [default 500]: ") or 500)
    obs['K_INDEX']       = float(input("K-Index [default 35]: ") or 35)
    obs['LIFTED_INDEX']  = float(input("Lifted Index [default -2]: ") or -2)
    obs['TOTALS_TOTALS'] = float(input("Totals-Totals [default 45]: ") or 45)
    obs['PRECIP_WATER']  = float(input("Precipitable water (mm) [default 40]: ") or 40)

    print("\n-- ERA5 fields (from daily ERA5 file) --")
    obs['ERA5_T2M']  = float(input("ERA5 T2M (K) [default 299]: ") or 299)
    obs['ERA5_D2M']  = float(input("ERA5 D2M (K) [default 293]: ") or 293)
    obs['ERA5_U10']  = float(input("ERA5 U10 (m/s) [default -2]: ") or -2)
    obs['ERA5_V10']  = float(input("ERA5 V10 (m/s) [default 1]: ") or 1)
    obs['ERA5_CAPE'] = obs['CAPE']
    obs['ERA5_SP']   = float(input("ERA5 surface pressure (Pa) [default 91500]: ") or 91500)

    print("  (Using default ERA5 pressure level values — add manually if available)")
    obs['ERA5_t_500hPa'] = 268.0
    obs['ERA5_t_700hPa'] = 283.0
    obs['ERA5_t_850hPa'] = 293.0
    obs['ERA5_q_500hPa'] = 0.003
    obs['ERA5_q_700hPa'] = 0.009
    obs['ERA5_q_850hPa'] = 0.013
    obs['ERA5_u_500hPa'] = 5.0
    obs['ERA5_u_700hPa'] = 2.0
    obs['ERA5_u_850hPa'] = -3.0
    obs['ERA5_v_500hPa'] = 2.0
    obs['ERA5_v_700hPa'] = 1.0
    obs['ERA5_v_850hPa'] = 2.0

    obs['ts_label_lag1_slot'] = obs['LABEL_lag1']
    obs['ts_any_yesterday']   = obs['LABEL_lag1']

    return obs

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="CSIR 6-Hour Thunderstorm Nowcast")
    parser.add_argument('--date',   type=str, help="Date to look up from dataset (YYYY-MM-DD)")
    parser.add_argument('--live',   action='store_true', help="Enter observations manually")
    args = parser.parse_args()

    print("\nLoading slot models...")
    models = load_models()
    print("All 4 slot models loaded.")

    # ── Get observations ──────────────────────────────────────────────────────
    actual_labels = None

    if args.live:
        obs_base = get_live_input()

    elif args.date:
        print(f"\nLooking up {args.date} in dataset...")
        df = pd.read_csv(DATA, parse_dates=['date'])
        day_data = df[df['date'] == args.date]
        if len(day_data) == 0:
            print(f"Date {args.date} not found in dataset.")
            print("Use --live to enter observations manually.")
            return
        row = day_data.iloc[0]
        FEATURE_COLS = [c for c in df.columns if c not in
                        ['date','year','month','slot','slot_label','ts_label']]
        obs_base = {c: row[c] for c in FEATURE_COLS}
        obs_base['date'] = args.date
        actual_labels = {int(r['slot']): int(r['ts_label'])
                         for _, r in day_data.iterrows()}
        print(f"Found. Actual labels: {actual_labels}")

    else:
        print("\nRunning DEMO mode — date: 2023-04-29 (real thunderstorm day)")
        print("(Run with --date YYYY-MM-DD or --live for other inputs)\n")
        obs_base     = DEMO_OBS.copy()
        actual_labels = DEMO_ACTUAL

    # ── Run all 4 slot models ─────────────────────────────────────────────────
    results = {}
    for slot_id in range(4):
        artifact     = models[slot_id]
        model        = artifact['model']
        feature_cols = artifact['feature_cols']
        threshold    = artifact['threshold']

        # Build slot-specific observation dict
        obs = obs_base.copy()
        obs = compute_derived(obs, slot_id)

        # Build feature vector
        X = build_feature_vector(obs, feature_cols)

        # Predict
        prob      = float(model.predict_proba(X)[0][1])
        predicted = prob >= threshold

        results[slot_id] = {
            'probability': prob,
            'predicted':   predicted,
            'threshold':   threshold,
        }

    # ── Print forecast ────────────────────────────────────────────────────────
    date_str = obs_base.get('date', 'Unknown')
    print_forecast(date_str, results, actual_labels)

    # ── JSON output for Satvik ────────────────────────────────────────────────
    print("  JSON output (for API testing):")
    print("  [")
    for slot_id, res in results.items():
        comma = "," if slot_id < 3 else ""
        print(f'    {{"date": "{date_str}", "slot": {slot_id}, '
              f'"slot_label": "{SLOT_NAMES[slot_id]}", '
              f'"ts_probability": {res["probability"]:.4f}, '
              f'"ts_predicted": {"true" if res["predicted"] else "false"}, '
              f'"threshold_used": {res["threshold"]}}}{comma}')
    print("  ]")

if __name__ == "__main__":
    main()
