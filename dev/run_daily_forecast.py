"""
run_daily_forecast.py
=====================
One-command operational pipeline for the CSIR Thunderstorm Nowcast System.
Bengaluru Airport (Station 43295).

What this script does in sequence:
  1. Determines today's operational date (IST)
  2. Fetches GFS real-time atmospheric data for all 4 slots
  3. Fetches upper-air stability indices from GFS via MetPy
  4. Runs the 6-hour nowcast prediction using calibrated v3 models
  5. Saves forecast to cumulative daily log
  6. Prints the operational forecast

Usage:
  python run_daily_forecast.py              # run for today
  python run_daily_forecast.py --date 2026-07-16   # run for specific date
  python run_daily_forecast.py --slots 2 3  # fetch only specific slots

Cron schedule (UTC, per Atul's pipeline scoping):
  15 10 * * * python run_daily_forecast.py --slots 0
  15 16 * * * python run_daily_forecast.py --slots 1
  15 22 * * * python run_daily_forecast.py --slots 2
  15 04 * * * python run_daily_forecast.py --slots 3

Author: Aprameya, CSIR Thunderstorm Project
"""

import subprocess
import sys
import os
import argparse
import pandas as pd
import numpy as np
import joblib
import warnings
from pathlib import Path
from datetime import datetime, timezone, timedelta
from sklearn.isotonic import IsotonicRegression

warnings.filterwarnings('ignore')

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE         = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
MODELS       = BASE / "models"
GFS_DIR      = BASE / "data" / "gfs_realtime"
UPPER_AIR    = BASE / "data" / "upperair_realtime_43295.csv"
FORECAST_LOG = BASE / "data" / "forecast_log.csv"
GFS_DIR.mkdir(parents=True, exist_ok=True)

SLOT_NAMES  = {0:"0001-0600 IST",1:"0601-1200 IST",2:"1201-1800 IST",3:"1801-2400 IST"}
SLOT_EMOJI  = {0:"🌙",1:"🌅",2:"☀️ ",3:"🌆"}
SLOT_COLORS = {0:"GREEN",1:"GREEN",2:"ORANGE",3:"YELLOW"}

# ── STEP 1: GET OPERATIONAL DATE ──────────────────────────────────────────────
def get_op_date(date_str=None):
    if date_str:
        return date_str
    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    return ist.strftime("%Y-%m-%d")

# ── STEP 2: FETCH GFS DATA ────────────────────────────────────────────────────
def fetch_gfs(date_str, slots):
    print(f"\n{'='*60}")
    print(f"STEP 1 — Fetching GFS Real-Time Data")
    print(f"{'='*60}")
    success = []
    for slot_id in slots:
        print(f"\n  Slot {slot_id} ({SLOT_NAMES[slot_id]})...")
        result = subprocess.run(
            [sys.executable, str(BASE / "fetch_gfs_realtime.py"),
             "--slot", str(slot_id), "--date", date_str],
            capture_output=True, text=True, cwd=str(BASE)
        )
        if result.returncode == 0:
            print(f"  ✓ GFS Slot {slot_id} fetched")
            success.append(slot_id)
        else:
            print(f"  ✗ GFS Slot {slot_id} failed:")
            for line in result.stdout.split('\n')[-5:]:
                if line.strip(): print(f"    {line}")
    return success

# ── STEP 3: FETCH UPPER-AIR DATA ──────────────────────────────────────────────
def fetch_upper_air(date_str, slots):
    print(f"\n{'='*60}")
    print(f"STEP 2 — Fetching Upper-Air Stability Indices")
    print(f"{'='*60}")

    fetcher = BASE / "fetch_upperair_realtime.py"
    if not fetcher.exists():
        fetcher = BASE / "gfs_fetcher.py"
    if not fetcher.exists():
        print("  ⚠ Upper-air fetcher not found — stability indices will use defaults")
        return []

    success = []
    for slot_id in slots:
        print(f"\n  Slot {slot_id} ({SLOT_NAMES[slot_id]})...")
        result = subprocess.run(
            [sys.executable, str(fetcher),
             "--slot", str(slot_id),
             "--now", datetime.now().strftime("%Y-%m-%d %H:%M")],
            capture_output=True, text=True, cwd=str(BASE)
        )
        if result.returncode == 0:
            print(f"  ✓ Upper-air Slot {slot_id} fetched")
            success.append(slot_id)
        else:
            print(f"  ⚠ Upper-air Slot {slot_id} failed — will use GFS CAPE/K defaults")
    return success

# ── STEP 4: LOAD MODELS ───────────────────────────────────────────────────────
def load_models():
    models = {}
    for slot_id in range(4):
        path = MODELS / f"nowcast_slot{slot_id}_xgb_v3_calibrated.pkl"
        if not path.exists():
            path = MODELS / f"nowcast_slot{slot_id}_xgb_v3.pkl"
        if not path.exists():
            path = MODELS / f"nowcast_slot{slot_id}_xgb_v2_calibrated.pkl"
        if path.exists():
            models[slot_id] = joblib.load(path)
    return models

def apply_calibrator(artifact, raw_prob):
    cal = artifact.get('calibrator')
    if cal is None: return raw_prob
    if artifact.get('calib_method') == 'sigmoid':
        return cal.predict_proba(raw_prob.reshape(-1,1))[:,1]
    return cal.predict(raw_prob)

# ── STEP 5: BUILD FEATURES ────────────────────────────────────────────────────
def build_obs(date_str, slot_id, gfs_df, upper_air_by_slot):
    import math
    date  = pd.Timestamp(date_str)
    doy   = date.dayofyear
    month = date.month
    m     = month

    # Get GFS row for this slot
    gfs_row = gfs_df[gfs_df['slot']==slot_id]
    if len(gfs_row) == 0:
        print(f"  ⚠ No GFS data for Slot {slot_id} — using defaults")
        gfs_row = pd.DataFrame([{}])
    gfs = gfs_row.iloc[0]

    obs = {
        'date':         date_str,
        'ERA5_T2M':     float(gfs.get('ERA5_T2M',  299.0)),
        'ERA5_D2M':     float(gfs.get('ERA5_D2M',  293.0)),
        'ERA5_U10':     float(gfs.get('ERA5_U10',  -2.0)),
        'ERA5_V10':     float(gfs.get('ERA5_V10',   1.0)),
        'ERA5_CAPE':    float(gfs.get('ERA5_CAPE',  0.0)),
        'ERA5_SP':      float(gfs.get('ERA5_SP',    91500.0)),
        'ERA5_t_500hPa':float(gfs.get('ERA5_t_500hPa', 268.0)),
        'ERA5_t_700hPa':float(gfs.get('ERA5_t_700hPa', 283.0)),
        'ERA5_t_850hPa':float(gfs.get('ERA5_t_850hPa', 293.0)),
        'ERA5_q_500hPa':float(gfs.get('ERA5_q_500hPa', 0.003)),
        'ERA5_q_700hPa':float(gfs.get('ERA5_q_700hPa', 0.009)),
        'ERA5_q_850hPa':float(gfs.get('ERA5_q_850hPa', 0.013)),
        'ERA5_u_500hPa':float(gfs.get('ERA5_u_500hPa', 5.0)),
        'ERA5_u_700hPa':float(gfs.get('ERA5_u_700hPa', 2.0)),
        'ERA5_u_850hPa':float(gfs.get('ERA5_u_850hPa',-3.0)),
        'ERA5_v_500hPa':float(gfs.get('ERA5_v_500hPa', 2.0)),
        'ERA5_v_700hPa':float(gfs.get('ERA5_v_700hPa', 1.0)),
        'ERA5_v_850hPa':float(gfs.get('ERA5_v_850hPa', 2.0)),
        'MAX':          float(gfs.get('ERA5_T2M', 302.0)) - 273.15,
        'MIN':          float(gfs.get('ERA5_T2M', 302.0)) - 278.15,
        'AW':0.0,'RF':0.0,'EVP':5.0,'DRNRF':0.0,'SSH':300.0,
        'RF_3d':0.0,'RF_7d':0.0,
        'MAX_3d_avg':float(gfs.get('ERA5_T2M',302.0))-273.15,
        'MIN_3d_avg':float(gfs.get('ERA5_T2M',302.0))-278.15,
        'DTR_3d_avg':8.0,
        'RF_lag1':0.0,'MAX_lag1':0.0,'MIN_lag1':0.0,
        'LABEL_lag1':0,'ts_label_lag1_slot':0,'ts_any_yesterday':0,
        'CAPE':0.0,'CIN':0.0,'K_INDEX':30.0,
        'LIFTED_INDEX':-2.0,'TOTALS_TOTALS':44.0,'PRECIP_WATER':40.0,
    }

    # Override with upper-air if available
    if slot_id in upper_air_by_slot:
        ua = upper_air_by_slot[slot_id]
        obs['CAPE']          = float(ua.get('CAPE',           obs['CAPE']))
        obs['CIN']           = float(ua.get('CIN',            obs['CIN']))
        obs['K_INDEX']       = float(ua.get('K_INDEX',        obs['K_INDEX']))
        obs['LIFTED_INDEX']  = float(ua.get('LIFTED_INDEX',   obs['LIFTED_INDEX']))
        obs['TOTALS_TOTALS'] = float(ua.get('TOTALS_TOTALS',  obs['TOTALS_TOTALS']))
        obs['PRECIP_WATER']  = float(ua.get('PRECIP_WATER',   obs['PRECIP_WATER']))

    # Derived features
    obs['DTR']        = obs['MAX'] - obs['MIN']
    obs['HA_flag']    = 0
    obs['RF_nonzero'] = 0

    if m in [3,4,5]:     obs['SEASON'] = 1
    elif m in [6,7,8,9]: obs['SEASON'] = 2
    elif m in [10,11]:   obs['SEASON'] = 3
    else:                 obs['SEASON'] = 0

    obs['MONTH_sin']      = math.sin(2*math.pi*m/12)
    obs['MONTH_cos']      = math.cos(2*math.pi*m/12)
    obs['DOY_sin']        = math.sin(2*math.pi*doy/365)
    obs['DOY_cos']        = math.cos(2*math.pi*doy/365)
    obs['doy_sin']        = obs['DOY_sin']
    obs['doy_cos']        = obs['DOY_cos']
    obs['slot_sin']       = math.sin(2*math.pi*slot_id/4)
    obs['slot_cos']       = math.cos(2*math.pi*slot_id/4)

    CLIM = {
        (4,0):0.025,(4,1):0.008,(4,2):0.129,(4,3):0.100,
        (5,0):0.129,(5,1):0.036,(5,2):0.194,(5,3):0.181,
        (6,0):0.042,(6,1):0.004,(6,2):0.096,(6,3):0.092,
        (7,0):0.028,(7,1):0.004,(7,2):0.032,(7,3):0.044,
        (8,0):0.020,(8,1):0.008,(8,2):0.052,(8,3):0.060,
        (9,0):0.083,(9,1):0.029,(9,2):0.079,(9,3):0.067,
        (10,0):0.077,(10,1):0.024,(10,2):0.077,(10,3):0.077,
    }
    obs['slot_month_clim'] = CLIM.get((m, slot_id), 0.02)

    # v3 derived atmospheric features
    q850 = obs['ERA5_q_850hPa']; q700 = obs['ERA5_q_700hPa']
    q500 = obs['ERA5_q_500hPa']; t850 = obs['ERA5_t_850hPa']
    t500 = obs['ERA5_t_500hPa']; u850 = obs['ERA5_u_850hPa']
    v850 = obs['ERA5_v_850hPa']; u700 = obs['ERA5_u_700hPa']
    v700 = obs['ERA5_v_700hPa']; u500 = obs['ERA5_u_500hPa']
    v500 = obs['ERA5_v_500hPa']
    CAPE = obs['CAPE']; K = obs['K_INDEX']
    LI   = obs['LIFTED_INDEX']; TT = obs['TOTALS_TOTALS']

    obs['cape_x_kindex']      = CAPE * K
    obs['li_x_totals']        = abs(LI) * TT
    obs['q_gradient_500_850'] = q850 - q500
    obs['thetae_850']         = t850 + 2491 * q850
    obs['wind_shear_500_850'] = ((u500-u850)**2 + (v500-v850)**2)**0.5
    obs['wind_shear_700_850'] = ((u700-u850)**2 + (v700-v850)**2)**0.5
    obs['moisture_flux_850']  = q850 * (u850**2 + v850**2)**0.5
    obs['moisture_flux_700']  = q700 * (u700**2 + v700**2)**0.5
    obs['thickness_500_850']  = t850 - t500
    obs['mid_level_drying']   = q700 / (q850 + 1e-9)

    return obs

# ── STEP 6: RUN PREDICTION ────────────────────────────────────────────────────
def run_prediction(date_str, models, gfs_df, upper_air_by_slot):
    print(f"\n{'='*60}")
    print(f"STEP 3 — Running Nowcast Prediction")
    print(f"{'='*60}")
    results = {}
    for slot_id in range(4):
        artifact     = models.get(slot_id)
        if artifact is None:
            print(f"  ⚠ Slot {slot_id}: no model loaded")
            results[slot_id] = {'probability':0.0,'predicted':False,'threshold':0.5}
            continue

        feature_cols = artifact['feature_cols']
        threshold    = artifact['threshold']
        model        = artifact['model']

        obs = build_obs(date_str, slot_id, gfs_df, upper_air_by_slot)
        X   = np.array([[float(obs.get(c, 0.0)) for c in feature_cols]])

        raw = float(model.predict_proba(X)[0][1])
        cal = float(apply_calibrator(artifact, np.array([raw]))[0])

        results[slot_id] = {
            'probability': cal,
            'predicted':   cal >= threshold,
            'threshold':   threshold,
            'raw_prob':    raw,
            'CAPE':        obs['CAPE'],
            'K_INDEX':     obs['K_INDEX'],
        }
        print(f"  Slot {slot_id} ({SLOT_NAMES[slot_id]}): "
              f"prob={cal*100:.1f}% thresh={threshold} "
              f"→ {'⚠ YES' if cal>=threshold else 'NO'} "
              f"[CAPE={obs['CAPE']:.0f} K={obs['K_INDEX']:.1f}]")
    return results

# ── STEP 7: PRINT FORECAST ────────────────────────────────────────────────────
def print_forecast(date_str, results):
    print(f"\n{'='*60}")
    print(f"CSIR THUNDERSTORM NOWCAST — Bengaluru Airport (43295)")
    print(f"{'='*60}")
    print(f"Date : {date_str}")
    print(f"Run  : {datetime.now().strftime('%Y-%m-%d %H:%M IST')}")
    print(f"Model: Calibrated v3 (6-hr ERA5 + derived features)")
    print(f"{'─'*60}")

    alert_slots = []
    for slot_id, res in results.items():
        prob      = res['probability']
        predicted = res['predicted']
        filled    = int(prob * 30)
        bar       = "█"*filled + "░"*(30-filled)
        call      = "⚠ ALERT" if predicted else "  CLEAR"
        if predicted: alert_slots.append(slot_id)

        if prob >= 0.60:   risk = "🔴 HIGH"
        elif prob >= 0.40: risk = "🟠 MOD "
        elif prob >= 0.20: risk = "🟡 LOW "
        else:               risk = "🟢 NONE"

        print(f"  {SLOT_EMOJI[slot_id]} Slot {slot_id} {SLOT_NAMES[slot_id]}  "
              f"{prob*100:5.1f}%  {bar}  {risk}  {call}")

    print(f"{'─'*60}")
    if alert_slots:
        peak = max(alert_slots, key=lambda s: results[s]['probability'])
        print(f"  🔴 THUNDERSTORM ALERT — {len(alert_slots)} window(s)")
        print(f"     Peak risk window: {SLOT_NAMES[peak]}")
        print(f"     Peak probability: {results[peak]['probability']*100:.1f}%")
    else:
        max_slot = max(results, key=lambda s: results[s]['probability'])
        print(f"  🟢 ALL CLEAR — No thunderstorm predicted")
        print(f"     Highest probability: Slot {max_slot} "
              f"({results[max_slot]['probability']*100:.1f}%)")
    print(f"{'='*60}")

# ── STEP 8: LOG FORECAST ──────────────────────────────────────────────────────
def log_forecast(date_str, results):
    rows = []
    for slot_id, res in results.items():
        rows.append({
            'date':           date_str,
            'slot':           slot_id,
            'slot_label':     SLOT_NAMES[slot_id],
            'probability':    round(res['probability'], 4),
            'predicted':      int(res['predicted']),
            'threshold':      res['threshold'],
            'raw_prob':       round(res.get('raw_prob', 0), 4),
            'CAPE_used':      round(res.get('CAPE', 0), 1),
            'K_INDEX_used':   round(res.get('K_INDEX', 0), 1),
            'model_version':  'v3_calibrated',
            'issued_at':      datetime.now().strftime('%Y-%m-%d %H:%M IST'),
        })

    new_df = pd.DataFrame(rows)
    if FORECAST_LOG.exists():
        existing = pd.read_csv(FORECAST_LOG)
        # Remove today's entries if re-running
        existing = existing[~((existing['date']==date_str))]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_csv(FORECAST_LOG, index=False)
    print(f"\n  Forecast logged → {FORECAST_LOG}")
    print(f"  Total entries in log: {len(combined)}")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="CSIR Thunderstorm Nowcast — Daily Operational Pipeline")
    parser.add_argument('--date',  type=str, help="Operational date YYYY-MM-DD")
    parser.add_argument('--slots', type=int, nargs='+',
                        choices=[0,1,2,3], default=[0,1,2,3],
                        help="Which slots to fetch (default: all 4)")
    parser.add_argument('--skip-fetch', action='store_true',
                        help="Skip GFS fetch, use existing files")
    args = parser.parse_args()

    date_str = get_op_date(args.date)

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║   CSIR Thunderstorm Nowcast — Daily Operational Pipeline    ║")
    print("║   Bengaluru Airport (IMD Station 43295)                     ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print(f"║   Date  : {date_str:<51}║")
    print(f"║   Slots : {str(args.slots):<51}║")
    print(f"║   Start : {datetime.now().strftime('%Y-%m-%d %H:%M IST'):<51}║")
    print("╚══════════════════════════════════════════════════════════════╝")

    # Step 1 — Fetch GFS
    if not args.skip_fetch:
        fetch_gfs(date_str, args.slots)
    else:
        print("\n[Skipping GFS fetch — using existing files]")

    # Step 2 — Fetch upper-air
    if not args.skip_fetch:
        fetch_upper_air(date_str, args.slots)

    # Step 3 — Load models
    print(f"\n{'='*60}")
    print(f"STEP 4 — Loading v3 Calibrated Models")
    print(f"{'='*60}")
    models = load_models()
    print(f"  Loaded {len(models)}/4 slot models")

    # Step 4 — Load GFS output
    gfs_file = GFS_DIR / f"gfs_{date_str}.csv"
    if gfs_file.exists():
        gfs_df = pd.read_csv(gfs_file)
        print(f"  GFS data: {gfs_file.name} ({len(gfs_df)} rows)")
    else:
        print(f"  ⚠ No GFS file found for {date_str} — using defaults")
        gfs_df = pd.DataFrame()

    # Step 5 — Load upper-air output
    upper_air_by_slot = {}
    if UPPER_AIR.exists():
        ua_df    = pd.read_csv(UPPER_AIR)
        ua_today = ua_df[ua_df['date']==date_str]
        for _, row in ua_today.iterrows():
            upper_air_by_slot[int(row['slot'])] = row.to_dict()
        print(f"  Upper-air: {len(upper_air_by_slot)} slots available")

    # Step 6 — Run prediction
    results = run_prediction(date_str, models, gfs_df, upper_air_by_slot)

    # Step 7 — Print forecast
    print_forecast(date_str, results)

    # Step 8 — Log
    log_forecast(date_str, results)

    print(f"\n✓ Pipeline complete for {date_str}")
    print(f"  Next run: python run_daily_forecast.py --date "
          f"{(pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')}")

if __name__ == "__main__":
    main()
