"""
forecast_logger.py
==================
ForecastLogger — Operational prediction logging and verification system
for the CSIR Thunderstorm Nowcast System, Bengaluru Airport (43295).

Responsibilities:
  - Log every model prediction with metadata
  - Ingest actual IMD observations when available
  - Compute real-time verification metrics (POD, FAR, HSS, Brier)
  - Flag when model performance degrades below operational thresholds
  - Export verification reports

Author: Sneha, CSIR Thunderstorm Project
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import json

BASE         = Path(__file__).parent
FORECAST_LOG = BASE / "data" / "forecast_log.csv"
ACTUAL_LOG   = BASE / "data" / "actual_log.csv"
ALERT_LOG    = BASE / "data" / "performance_alerts.csv"

SLOT_NAMES = {
    0: "0001-0600 IST",
    1: "0601-1200 IST",
    2: "1201-1800 IST",
    3: "1801-2400 IST",
}

# Operational thresholds — alert if rolling 30-day metrics drop below these
PERFORMANCE_THRESHOLDS = {
    'HSS':  0.20,   # minimum acceptable HSS
    'FAR':  0.85,   # maximum acceptable FAR
    'POD':  0.25,   # minimum acceptable POD
}


class ForecastLogger:
    """
    Logs and verifies operational thunderstorm forecasts.

    Usage:
        logger = ForecastLogger()

        # Log a prediction
        logger.log_forecast(
            date="2026-07-24",
            slot=2,
            probability=0.67,
            predicted=True,
            threshold=0.34,
            model_version="v3_calibrated",
            cape=320.0,
            k_index=38.5
        )

        # Log actual observation (next morning)
        logger.log_actual(date="2026-07-24", slot=2, observed=1)

        # Get current performance
        metrics = logger.get_rolling_metrics(slot=2, window=30)
        print(metrics)

        # Check for alerts
        alerts = logger.check_performance_alerts()
    """

    def __init__(self):
        self.forecast_log = FORECAST_LOG
        self.actual_log   = ACTUAL_LOG
        self.alert_log    = ALERT_LOG
        self._ensure_files()

    def _ensure_files(self):
        """Create log files with correct headers if they don't exist."""
        if not self.forecast_log.exists():
            pd.DataFrame(columns=[
                'date','slot','slot_label','probability','predicted',
                'threshold','model_version','cape_used','k_index_used',
                'issued_at','logged_at'
            ]).to_csv(self.forecast_log, index=False)

        if not self.actual_log.exists():
            pd.DataFrame(columns=[
                'date','slot','observed','source','ingested_at'
            ]).to_csv(self.actual_log, index=False)

        if not self.alert_log.exists():
            pd.DataFrame(columns=[
                'timestamp','slot','metric','value','threshold',
                'severity','message'
            ]).to_csv(self.alert_log, index=False)

    # ── LOGGING ───────────────────────────────────────────────────────────
    def log_forecast(self, date, slot, probability, predicted,
                     threshold, model_version="v3_calibrated",
                     cape=None, k_index=None):
        """Log one slot prediction."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M IST")
        row = {
            'date':          date,
            'slot':          slot,
            'slot_label':    SLOT_NAMES.get(slot, f"Slot {slot}"),
            'probability':   round(float(probability), 4),
            'predicted':     int(predicted),
            'threshold':     threshold,
            'model_version': model_version,
            'cape_used':     round(float(cape), 1) if cape is not None else None,
            'k_index_used':  round(float(k_index), 1) if k_index is not None else None,
            'issued_at':     f"{date} 05:00 IST",
            'logged_at':     now,
        }
        df = pd.read_csv(self.forecast_log)
        # Remove duplicate if re-logging same date/slot
        df = df[~((df['date']==date) & (df['slot']==slot))]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(self.forecast_log, index=False)
        print(f"  [Logger] Logged: {date} Slot {slot} → {probability*100:.1f}% "
              f"({'YES' if predicted else 'NO'})")

    def log_actual(self, date, slot, observed, source="IMD_manual"):
        """Log actual thunderstorm observation for a slot."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M IST")
        row = {
            'date':        date,
            'slot':        slot,
            'observed':    int(observed),
            'source':      source,
            'ingested_at': now,
        }
        df = pd.read_csv(self.actual_log)
        df = df[~((df['date']==date) & (df['slot']==slot))]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(self.actual_log, index=False)
        print(f"  [Logger] Actual logged: {date} Slot {slot} → "
              f"{'TS OCCURRED' if observed else 'no TS'} (source: {source})")

    def log_day(self, date, predictions: dict):
        """
        Log all 4 slots for a day at once.
        predictions: {slot_id: {'probability':0.67,'predicted':True,'threshold':0.34,...}}
        """
        for slot_id, pred in predictions.items():
            self.log_forecast(
                date=date, slot=slot_id,
                probability=pred['probability'],
                predicted=pred['predicted'],
                threshold=pred['threshold'],
                model_version=pred.get('model_version','v3_calibrated'),
                cape=pred.get('CAPE'),
                k_index=pred.get('K_INDEX'),
            )

    # ── VERIFICATION ──────────────────────────────────────────────────────
    def _get_merged(self):
        """Merge forecast and actual logs."""
        fc = pd.read_csv(self.forecast_log)
        ac = pd.read_csv(self.actual_log)
        if len(fc) == 0 or len(ac) == 0:
            return pd.DataFrame()
        return fc.merge(ac[['date','slot','observed']], on=['date','slot'], how='inner')

    def get_rolling_metrics(self, slot=2, window=30):
        """
        Compute WMO verification metrics over the last N days for a slot.
        Returns dict with POD, FAR, CSI, HSS, Brier, n_days, n_ts.
        """
        merged = self._get_merged()
        if len(merged) == 0:
            return {"error": "No verified forecasts available yet"}

        merged = merged[merged['slot']==slot].copy()
        merged['date'] = pd.to_datetime(merged['date'])
        merged = merged.sort_values('date').tail(window)

        if len(merged) == 0:
            return {"error": f"No data for slot {slot}"}

        y_true = merged['observed'].values
        y_pred = merged['predicted'].values
        y_prob = merged['probability'].values

        tp = int(((y_pred==1)&(y_true==1)).sum())
        fp = int(((y_pred==1)&(y_true==0)).sum())
        fn = int(((y_pred==0)&(y_true==1)).sum())
        tn = int(((y_pred==0)&(y_true==0)).sum())

        pod  = tp/(tp+fn)      if (tp+fn)>0      else 0
        far  = fp/(tp+fp)      if (tp+fp)>0      else 0
        csi  = tp/(tp+fp+fn)   if (tp+fp+fn)>0   else 0
        hss_num = 2*(tp*tn - fp*fn)
        hss_den = (tp+fn)*(fn+tn)+(tp+fp)*(fp+tn)
        hss  = hss_num/hss_den if hss_den>0      else 0

        # Brier score
        brier = float(np.mean((y_prob - y_true)**2))

        return {
            'slot':      slot,
            'slot_label':SLOT_NAMES.get(slot),
            'window':    window,
            'n_days':    len(merged),
            'n_ts':      int(y_true.sum()),
            'ts_rate':   round(y_true.mean()*100, 1),
            'POD':       round(pod, 3),
            'FAR':       round(far, 3),
            'CSI':       round(csi, 3),
            'HSS':       round(hss, 3),
            'Brier':     round(brier, 4),
            'TP':tp,'FP':fp,'FN':fn,'TN':tn,
            'computed_at': datetime.now().strftime("%Y-%m-%d %H:%M IST"),
        }

    def get_all_slot_metrics(self, window=30):
        """Get rolling metrics for all 4 slots."""
        return {slot: self.get_rolling_metrics(slot, window) for slot in range(4)}

    def check_performance_alerts(self, window=30):
        """
        Check if any slot's rolling metrics have dropped below
        operational thresholds. Returns list of alert dicts.
        """
        alerts = []
        now    = datetime.now().strftime("%Y-%m-%d %H:%M IST")

        for slot in range(4):
            metrics = self.get_rolling_metrics(slot, window)
            if 'error' in metrics:
                continue
            if metrics['n_ts'] < 3:
                continue  # not enough positives to evaluate

            for metric, threshold in PERFORMANCE_THRESHOLDS.items():
                value = metrics.get(metric, None)
                if value is None:
                    continue

                if metric == 'FAR':
                    triggered = value > threshold
                    severity  = 'HIGH' if value > threshold + 0.1 else 'MEDIUM'
                else:
                    triggered = value < threshold
                    severity  = 'HIGH' if value < threshold - 0.1 else 'MEDIUM'

                if triggered:
                    alert = {
                        'timestamp': now,
                        'slot':      slot,
                        'metric':    metric,
                        'value':     value,
                        'threshold': threshold,
                        'severity':  severity,
                        'message':   (f"Slot {slot} ({SLOT_NAMES[slot]}) "
                                      f"{metric}={value:.3f} has breached "
                                      f"operational threshold of {threshold}"),
                    }
                    alerts.append(alert)
                    print(f"  ⚠ ALERT [{severity}]: {alert['message']}")

        if alerts:
            df = pd.read_csv(self.alert_log)
            df = pd.concat([df, pd.DataFrame(alerts)], ignore_index=True)
            df.to_csv(self.alert_log, index=False)

        return alerts

    # ── REPORTING ─────────────────────────────────────────────────────────
    def daily_summary(self, date=None):
        """Print a clean daily summary for a given date."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        merged = self._get_merged()
        day    = merged[merged['date']==date]

        print(f"\n{'='*55}")
        print(f"DAILY VERIFICATION SUMMARY — {date}")
        print(f"{'='*55}")

        if len(day) == 0:
            print("  No verified data for this date yet.")
            return

        for _, row in day.sort_values('slot').iterrows():
            pred = "YES" if row['predicted'] else "NO"
            obs  = "YES" if row['observed']  else "NO"
            if row['predicted']==1 and row['observed']==1:   outcome = "✓ HIT"
            elif row['predicted']==0 and row['observed']==1: outcome = "✗ MISS"
            elif row['predicted']==1 and row['observed']==0: outcome = "✗ FALSE ALARM"
            else:                                             outcome = "✓ CORRECT NO"
            print(f"  Slot {int(row['slot'])} {SLOT_NAMES[int(row['slot'])]}: "
                  f"Pred={pred} ({row['probability']*100:.0f}%) "
                  f"Obs={obs} → {outcome}")

    def export_verification_report(self, output_path=None):
        """Export a full verification report as JSON."""
        if output_path is None:
            output_path = BASE / "results" / "verification_report.json"

        report = {
            'generated_at':  datetime.now().strftime("%Y-%m-%d %H:%M IST"),
            'station':       'Bengaluru Airport — IMD 43295',
            'model_version': 'v3_calibrated',
            'metrics_30day': self.get_all_slot_metrics(window=30),
            'metrics_7day':  self.get_all_slot_metrics(window=7),
            'alerts':        self.check_performance_alerts(),
        }

        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        print(f"\nVerification report exported → {output_path}")
        return report


# ── DEMO / TEST ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing ForecastLogger...")
    logger = ForecastLogger()

    # Load existing logs if available
    fc_path = BASE / "data" / "forecast_log.csv"
    ac_path = BASE / "data" / "actual_log.csv"

    if fc_path.exists() and ac_path.exists():
        print("\nExisting logs found — computing metrics...")
    else:
        print("\nNo existing logs — logging demo data...")
        logger.log_forecast("2026-07-24", 2, 0.67, True, 0.34, cape=320.0, k_index=38.5)
        logger.log_forecast("2026-07-24", 3, 0.21, False, 0.39, cape=195.0, k_index=40.3)
        logger.log_actual("2026-07-24", 2, 1, source="IMD_manual")
        logger.log_actual("2026-07-24", 3, 1, source="IMD_manual")

    print("\n30-day rolling metrics (Slot 2):")
    metrics = logger.get_rolling_metrics(slot=2, window=30)
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    print("\nChecking performance alerts...")
    alerts = logger.check_performance_alerts()
    if not alerts:
        print("  No alerts — all metrics within operational thresholds")

    logger.export_verification_report()
    print("\nForecastLogger test complete.")
