import joblib, pandas as pd, numpy as np

bundle   = joblib.load('thunderstorm_model.pkl')
model    = bundle['model']
FEATURES = bundle['features']

def predict_day(date_str, MAX, MIN, AW, RF, SSH,
                RF_lag1=0, MAX_lag1=None, MIN_lag1=None, LABEL_lag1=0):

    d    = pd.to_datetime(date_str)
    DTR  = MAX - MIN
    mon  = d.month
    doy  = d.dayofyear

    row = {
        'MAX': MAX, 'MIN': MIN, 'DTR': DTR,
        'AW': AW, 'RF': RF, 'SSH': SSH,
        'EVP': 4.5, 'DRNRF': 0,
        'RF_3d':      RF_lag1,
        'RF_7d':      RF_lag1,
        'MAX_3d_avg': MAX_lag1 or MAX,
        'MIN_3d_avg': MIN_lag1 or MIN,
        'DTR_3d_avg': DTR,
        'RF_lag1':    RF_lag1,
        'MAX_lag1':   MAX_lag1 or MAX,
        'MIN_lag1':   MIN_lag1 or MIN,
        'LABEL_lag1': LABEL_lag1,
        'MONTH_sin':  np.sin(2*np.pi*mon/12),
        'MONTH_cos':  np.cos(2*np.pi*mon/12),
        'DOY_sin':    np.sin(2*np.pi*doy/365),
        'DOY_cos':    np.cos(2*np.pi*doy/365),
        'SEASON':     {12:0,1:0,2:0,3:1,4:1,5:1,
                       6:2,7:2,8:2,9:2,10:3,11:3}[mon],
        'HA_flag':    0,
        'RF_nonzero': int(RF > 0)
    }

    X     = pd.DataFrame([row])[FEATURES]
    prob  = float(model.predict_proba(X)[0][1])
    alert = 'RED' if prob > 0.6 else 'YELLOW' if prob > 0.35 else 'GREEN'

    print(f"\n{'='*40}")
    print(f"Date        : {date_str}")
    print(f"Probability : {prob*100:.1f}%")
    print(f"Alert Level : {alert}")
    if alert == 'RED':
        print(">> HIGH chance of thunderstorm. Issue warning.")
    elif alert == 'YELLOW':
        print(">> MODERATE chance. Monitor closely.")
    else:
        print(">> LOW chance. No action needed.")
    print(f"{'='*40}\n")
    return prob

# ── Test with today's Bengaluru conditions ──
predict_day(
    date_str   = '2026-07-13',
    MAX        = 29.0,
    MIN        = 21.0,
    AW         = 4,
    RF         = 2.1,
    SSH        = 180,
    RF_lag1    = 0.0,
    MAX_lag1   = 28.5,
    MIN_lag1   = 20.8,
    LABEL_lag1 = 0
)