"""
run_daily_forecast_v2.py
========================
One-command operational pipeline for the CSIR Thunderstorm Nowcast System.
Version 2 — integrates ForecastLogger for automatic prediction logging.

Changes from v1:
  - Imports and uses ForecastLogger to log every prediction
  - Exports verification report after each run
  - Checks performance alerts automatically

Author: Aprameya, CSIR Thunderstorm Project
"""

import subprocess
import sys
import argparse
import pandas as pd
import numpy as np
import joblib
import warnings
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from sklearn.isotonic import IsotonicRegression

warnings.filterwarnings('ignore')

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE         = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
MODELS       = BASE / "models"
GFS_DIR      = BASE / "data" / "gfs_realtime"
UPPER_AIR    = BASE / "data" / "upperair_realtime_43295.csv"
GFS_DIR.mkdir(parents=True, exist_ok=True)

SLOT_NAMES = {0:"0001-0600 IST",1:"0601-1200 IST",2:"1201-1800 IST",3:"1801-2400 IST"}
SLOT_EMOJI = {0:"🌙",1:"🌅",2:"☀️ ",3:"🌆"}

# ── IMPORT FORECAST LOGGER ────────────────────────────────────────────────────
sys.path.insert(0, str(BASE))
try:
    from forecast_logger import ForecastLogger
    LOGGER_AVAILABLE = True
    print("  ForecastLogger loaded ✓")
except ImportError:
    LOGGER_AVAILABLE = False
    print("  ⚠ ForecastLogger not found — logging disabled")

# ── HELPERS (same as v1) ──────────────────────────────────────────────────────
def get_op_date(date_str=None):
    if date_str:
        return date_str
    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    return ist.strftime("%Y-%m-%d")

def load_models():
    models = {}
    for slot_id in range(4):
        for suffix in ['_xgb_v3_calibrated','_xgb_v3','_xgb_v2_calibrated']:
            path = MODELS / f"nowcast_slot{slot_id}{suffix}.pkl"
            if path.exists():
                models[slot_id] = joblib.load(path)
                break
    return models

def apply_calibrator(artifact, raw_prob):
    cal = artifact.get('calibrator')
    if cal is None: return raw_prob
    if artifact.get('calib_method') == 'sigmoid':
        return cal.predict_proba(raw_prob.reshape(-1,1))[:,1]
    return cal.predict(raw_prob)

def fetch_gfs(date_str, slots):
    print(f"\n{'='*60}")
    print(f"STEP 1 — Fetching GFS Real-Time Data")
    print(f"{'='*60}")
    for slot_id in slots:
        print(f"  Slot {slot_id}...")
        result = subprocess.run(
            [sys.executable, str(BASE/"fetch_gfs_realtime.py"),
             "--slot", str(slot_id), "--date", date_str],
            capture_output=True, text=True, cwd=str(BASE))
        if result.returncode == 0:
            print(f"  ✓ Slot {slot_id} GFS fetched")
        else:
            print(f"  ✗ Slot {slot_id} GFS failed")

def fetch_upper_air(date_str, slots):
    print(f"\n{'='*60}")
    print(f"STEP 2 — Fetching Upper-Air Stability Indices")
    print(f"{'='*60}")
    for fetcher_name in ['fetch_upperair_realtime.py','gfs_fetcher.py']:
        fetcher = BASE / fetcher_name
        if fetcher.exists():
            break
    else:
        print("  ⚠ Upper-air fetcher not found")
        return
    for slot_id in slots:
        result = subprocess.run(
            [sys.executable, str(fetcher),
             "--slot", str(slot_id),
             "--now", datetime.now().strftime("%Y-%m-%d %H:%M")],
            capture_output=True, text=True, cwd=str(BASE))
        status = "✓" if result.returncode == 0 else "⚠ failed"
        print(f"  Slot {slot_id}: {status}")

def build_obs(date_str, slot_id, gfs_df, upper_air_by_slot):
    date  = pd.Timestamp(date_str)
    doy   = date.dayofyear
    month = date.month
    m     = month

    gfs_row = gfs_df[gfs_df['slot']==slot_id] if len(gfs_df) > 0 else pd.DataFrame()
    gfs     = gfs_row.iloc[0] if len(gfs_row) > 0 else pd.Series(dtype=float)

    obs = {
        'date':date_str,
        'ERA5_T2M':     float(gfs.get('ERA5_T2M',  299.0)),
        'ERA5_D2M':     float(gfs.get('ERA5_D2M',  293.0)),
        'ERA5_U10':     float(gfs.get('ERA5_U10',  -2.0)),
        'ERA5_V10':     float(gfs.get('ERA5_V10',   1.0)),
        'ERA5_CAPE':    float(gfs.get('ERA5_CAPE',  0.0)),
        'ERA5_SP':      float(gfs.get('ERA5_SP',    91500.0)),
        'ERA5_t_500hPa':float(gfs.get('ERA5_t_500hPa',268.0)),
        'ERA5_t_700hPa':float(gfs.get('ERA5_t_700hPa',283.0)),
        'ERA5_t_850hPa':float(gfs.get('ERA5_t_850hPa',293.0)),
        'ERA5_q_500hPa':float(gfs.get('ERA5_q_500hPa',0.003)),
        'ERA5_q_700hPa':float(gfs.get('ERA5_q_700hPa',0.009)),
        'ERA5_q_850hPa':float(gfs.get('ERA5_q_850hPa',0.013)),
        'ERA5_u_500hPa':float(gfs.get('ERA5_u_500hPa',5.0)),
        'ERA5_u_700hPa':float(gfs.get('ERA5_u_700hPa',2.0)),
        'ERA5_u_850hPa':float(gfs.get('ERA5_u_850hPa',-3.0)),
        'ERA5_v_500hPa':float(gfs.get('ERA5_v_500hPa',2.0)),
        'ERA5_v_700hPa':float(gfs.get('ERA5_v_700hPa',1.0)),
        'ERA5_v_850hPa':float(gfs.get('ERA5_v_850hPa',2.0)),
        'MAX':float(gfs.get('ERA5_T2M',302.0))-273.15,
        'MIN':float(gfs.get('ERA5_T2M',302.0))-278.15,
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

    if slot_id in upper_air_by_slot:
        ua = upper_air_by_slot[slot_id]
        obs['CAPE']          = float(ua.get('CAPE',          obs['CAPE']))
        obs['CIN']           = float(ua.get('CIN',           obs['CIN']))
        obs['K_INDEX']       = float(ua.get('K_INDEX',       obs['K_INDEX']))
        obs['LIFTED_INDEX']  = float(ua.get('LIFTED_INDEX',  obs['LIFTED_INDEX']))
        obs['TOTALS_TOTALS'] = float(ua.get('TOTALS_TOTALS', obs['TOTALS_TOTALS']))
        obs['PRECIP_WATER']  = float(ua.get('PRECIP_WATER',  obs['PRECIP_WATER']))

    obs['DTR'] = obs['MAX'] - obs['MIN']
    obs['HA_flag'] = 0; obs['RF_nonzero'] = 0
    if m in [3,4,5]:     obs['SEASON'] = 1
    elif m in [6,7,8,9]: obs['SEASON'] = 2
    elif m in [10,11]:   obs['SEASON'] = 3
    else:                 obs['SEASON'] = 0
    obs['MONTH_sin'] = math.sin(2*math.pi*m/12)
    obs['MONTH_cos'] = math.cos(2*math.pi*m/12)
    obs['DOY_sin']   = math.sin(2*math.pi*doy/365)
    obs['DOY_cos']   = math.cos(2*math.pi*doy/365)
    obs['doy_sin']   = obs['DOY_sin']
    obs['doy_cos']   = obs['DOY_cos']
    obs['slot_sin']  = math.sin(2*math.pi*slot_id/4)
    obs['slot_cos']  = math.cos(2*math.pi*slot_id/4)
    CLIM = {
        (4,2):0.129,(5,2):0.194,(6,2):0.096,(7,2):0.032,
        (8,2):0.052,(9,2):0.079,(10,2):0.077,
    }
    obs['slot_month_clim'] = CLIM.get((m,slot_id), 0.02)

    q850=obs['ERA5_q_850hPa']; q700=obs['ERA5_q_700hPa']; q500=obs['ERA5_q_500hPa']
    t850=obs['ERA5_t_850hPa']; t500=obs['ERA5_t_500hPa']
    u850=obs['ERA5_u_850hPa']; v850=obs['ERA5_v_850hPa']
    u700=obs['ERA5_u_700hPa']; v700=obs['ERA5_v_700hPa']
    u500=obs['ERA5_u_500hPa']; v500=obs['ERA5_v_500hPa']
    CAPE=obs['CAPE']; K=obs['K_INDEX']; LI=obs['LIFTED_INDEX']; TT=obs['TOTALS_TOTALS']

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

def run_prediction(date_str, models, gfs_df, upper_air_by_slot):
    print(f"\n{'='*60}")
    print(f"STEP 3 — Running Nowcast Prediction")
    print(f"{'='*60}")
    results = {}
    for slot_id in range(4):
        artifact = models.get(slot_id)
        if artifact is None:
            results[slot_id] = {'probability':0.0,'predicted':False,'threshold':0.5}
            continue
        feature_cols = artifact['feature_cols']
        threshold    = artifact['threshold']
        model        = artifact['model']
        obs = build_obs(date_str, slot_id, gfs_df, upper_air_by_slot)
        X   = np.array([[float(obs.get(c,0.0)) for c in feature_cols]])
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
              f"{cal*100:.1f}% → {'⚠ YES' if cal>=threshold else 'NO'} "
              f"[CAPE={obs['CAPE']:.0f} K={obs['K_INDEX']:.1f}]")
    return results

def print_forecast(date_str, results):
    print(f"\n{'='*60}")
    print(f"CSIR THUNDERSTORM NOWCAST — Bengaluru Airport (43295)")
    print(f"{'='*60}")
    print(f"Date : {date_str}  |  Model: Calibrated v3")
    print(f"Run  : {datetime.now().strftime('%Y-%m-%d %H:%M IST')}")
    print(f"{'─'*60}")
    alert_slots = []
    for slot_id, res in results.items():
        prob = res['probability']
        if prob >= 0.60:   risk = "🔴 HIGH"
        elif prob >= 0.40: risk = "🟠 MOD "
        elif prob >= 0.20: risk = "🟡 LOW "
        else:               risk = "🟢 NONE"
        call = "⚠ ALERT" if res['predicted'] else "  CLEAR"
        bar  = "█"*int(prob*30) + "░"*(30-int(prob*30))
        if res['predicted']: alert_slots.append(slot_id)
        print(f"  {SLOT_EMOJI[slot_id]} Slot {slot_id} {SLOT_NAMES[slot_id]}  "
              f"{prob*100:5.1f}%  {bar}  {risk}  {call}")
    print(f"{'─'*60}")
    if alert_slots:
        peak = max(alert_slots, key=lambda s: results[s]['probability'])
        print(f"  🔴 ALERT — {len(alert_slots)} window(s) | Peak: {SLOT_NAMES[peak]}")
    else:
        print(f"  🟢 ALL CLEAR")
    print(f"{'='*60}")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date',       type=str)
    parser.add_argument('--slots',      type=int, nargs='+',
                        choices=[0,1,2,3], default=[0,1,2,3])
    parser.add_argument('--skip-fetch', action='store_true')
    parser.add_argument('--log-actual', type=int, nargs=2,
                        metavar=('SLOT','OBSERVED'),
                        help="Log actual observation: --log-actual 2 1")
    args = parser.parse_args()

    date_str = get_op_date(args.date)

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║   CSIR Thunderstorm Nowcast — Daily Operational Pipeline v2 ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print(f"║   Date: {date_str:<53}║")
    print(f"║   Run : {datetime.now().strftime('%Y-%m-%d %H:%M IST'):<53}║")
    print("╚══════════════════════════════════════════════════════════════╝")

    # Init logger
    logger = ForecastLogger() if LOGGER_AVAILABLE else None

    # Handle --log-actual (log an observation and exit)
    if args.log_actual:
        slot_id, observed = args.log_actual
        if logger:
            logger.log_actual(date_str, slot_id, observed, source="IMD_manual")
            print(f"\n✓ Actual logged: {date_str} Slot {slot_id} → "
                  f"{'TS occurred' if observed else 'no TS'}")
            report = logger.export_verification_report()
        return

    # Fetch data
    if not args.skip_fetch:
        fetch_gfs(date_str, args.slots)
        fetch_upper_air(date_str, args.slots)
    else:
        print("\n[Skipping fetch — using existing files]")

    # Load models
    print(f"\n{'='*60}")
    print(f"STEP 4 — Loading Models")
    print(f"{'='*60}")
    models = load_models()
    print(f"  Loaded {len(models)}/4 slot models")

    # Load GFS
    gfs_file = GFS_DIR / f"gfs_{date_str}.csv"
    gfs_df   = pd.read_csv(gfs_file) if gfs_file.exists() else pd.DataFrame()
    print(f"  GFS rows: {len(gfs_df)}")

    # Load upper-air
    upper_air_by_slot = {}
    if UPPER_AIR.exists():
        ua_df    = pd.read_csv(UPPER_AIR)
        ua_today = ua_df[ua_df['date']==date_str]
        for _, row in ua_today.iterrows():
            upper_air_by_slot[int(row['slot'])] = row.to_dict()
        print(f"  Upper-air slots: {list(upper_air_by_slot.keys())}")

    # Predict
    results = run_prediction(date_str, models, gfs_df, upper_air_by_slot)

    # Print
    print_forecast(date_str, results)

    # ── LOG WITH FORECASTLOGGER ───────────────────────────────────────────────
    if logger:
        print(f"\n{'='*60}")
        print(f"STEP 5 — Logging Forecast")
        print(f"{'='*60}")
        logger.log_day(date_str, results)

        print(f"\n{'='*60}")
        print(f"STEP 6 — Performance Check")
        print(f"{'='*60}")
        metrics = logger.get_rolling_metrics(slot=2, window=30)
        if 'error' not in metrics:
            print(f"  Slot 2 rolling 30-day: "
                  f"POD={metrics['POD']} FAR={metrics['FAR']} "
                  f"HSS={metrics['HSS']} Brier={metrics['Brier']}")
        alerts = logger.check_performance_alerts()
        if not alerts:
            print("  ✓ All metrics within operational thresholds")

        logger.export_verification_report()

    print(f"\n✓ Pipeline complete — {date_str}")
    print(f"\nTo log today's actual observation:")
    print(f"  python run_daily_forecast_v2.py --log-actual 2 1  (TS occurred in Slot 2)")
    print(f"  python run_daily_forecast_v2.py --log-actual 2 0  (no TS in Slot 2)")

if __name__ == "__main__":
    main()
