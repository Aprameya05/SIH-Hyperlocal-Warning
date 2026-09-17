"""
populate_skill_scores.py
========================
CSIR Thunderstorm Nowcasting System — WMO Skill Score Aggregator

Reads rolling verification data from:
  data/verification_today.json       — daily update from verify_today.py
  results/verification_report.json   — 30-day rolling report (if present)
  data/forecast_log.csv              — historical probabilistic forecasts

Computes and writes:
  data/skill_scores.json             — WMO/operational metrics for dashboard

Metric set per slot (WMO standard for deterministic nowcasting):
  POD   — Probability of Detection (a.k.a. Hit Rate)
  FAR   — False Alarm Ratio
  CSI   — Critical Success Index (Threat Score)
  HSS   — Heidke Skill Score (-1 to +1, 0 = no skill)
  BSS   — Brier Skill Score (vs climatological base rate)
  BIAS  — Frequency Bias
  AUROC — Area Under ROC curve (from SHAP/model artifact, if available)

Run:
  python populate_skill_scores.py
  python populate_skill_scores.py --window 90    # use 90-day window

GitHub Actions step (after verify_today.py):
  - name: Populate skill scores
    run: python populate_skill_scores.py
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

BASE          = Path(__file__).resolve().parent
DATA          = BASE / "data"
RESULTS       = BASE / "results"
FORECAST_LOG  = DATA / "forecast_log.csv"
VERIF_TODAY   = DATA / "verification_today.json"
VERIF_REPORT  = RESULTS / "verification_report.json"
OUT           = DATA / "skill_scores.json"

IST = timezone(timedelta(hours=5, minutes=30))

BASE_THRESHOLDS = {0: 0.24, 1: 0.15, 2: 0.16, 3: 0.39}
CLIM_RATES      = {0: 0.037, 1: 0.011, 2: 0.063, 3: 0.059}   # per-slot base rates

SLOT_NAMES = {
    0: "0001–0600 IST",
    1: "0601–1200 IST",
    2: "1201–1800 IST",
    3: "1801–2400 IST",
}


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray,
                    y_prob: np.ndarray, clim_rate: float) -> dict:
    """Compute full WMO verification metric set."""
    TP = int(((y_pred == 1) & (y_true == 1)).sum())
    FP = int(((y_pred == 1) & (y_true == 0)).sum())
    FN = int(((y_pred == 0) & (y_true == 1)).sum())
    TN = int(((y_pred == 0) & (y_true == 0)).sum())
    n  = TP + FP + FN + TN

    pod  = TP / (TP + FN)    if (TP + FN)  > 0 else None
    far  = FP / (TP + FP)    if (TP + FP)  > 0 else None
    csi  = TP / (TP + FP + FN) if (TP + FP + FN) > 0 else None
    bias = (TP + FP) / (TP + FN) if (TP + FN) > 0 else None

    hss_num = 2 * (TP * TN - FP * FN)
    hss_den = ((TP + FN) * (FN + TN) + (TP + FP) * (FP + TN))
    hss  = hss_num / hss_den if hss_den > 0 else None

    # Brier Skill Score vs climatology
    brier = float(np.mean((y_prob - y_true) ** 2)) if len(y_prob) > 0 else None
    brier_clim = clim_rate * (1 - clim_rate)
    bss  = (1 - brier / brier_clim) if (brier is not None and brier_clim > 0) else None

    # AUROC
    auroc = None
    if y_true.sum() > 0 and (y_true == 0).sum() > 0:
        try:
            from sklearn.metrics import roc_auc_score
            auroc = round(float(roc_auc_score(y_true, y_prob)), 4)
        except Exception:
            pass

    def r4(v):
        return round(float(v), 4) if v is not None else None

    return {
        "TP": TP, "FP": FP, "FN": FN, "TN": TN, "n": n,
        "POD":   r4(pod),
        "FAR":   r4(far),
        "CSI":   r4(csi),
        "HSS":   r4(hss),
        "BIAS":  r4(bias),
        "Brier": r4(brier),
        "BSS":   r4(bss),
        "AUROC": auroc,
    }


def load_forecast_log(window_days: int) -> pd.DataFrame | None:
    """Load forecast_log.csv and filter to the rolling window."""
    if not FORECAST_LOG.exists():
        print(f"  forecast_log.csv not found at {FORECAST_LOG}")
        return None
    try:
        df = pd.read_csv(FORECAST_LOG, parse_dates=["date"])
    except Exception as e:
        print(f"  Error reading forecast_log.csv: {e}")
        return None

    cutoff = datetime.now(IST).date() - timedelta(days=window_days)
    if "date" in df.columns:
        df = df[df["date"].dt.date >= cutoff]

    print(f"  forecast_log.csv: {len(df)} rows in last {window_days} days")
    return df


def metrics_from_log(df: pd.DataFrame, slot_id: int, threshold: float) -> dict | None:
    """Extract per-slot metrics from forecast log."""
    slot_df = df[df["slot"] == slot_id].copy() if "slot" in df.columns else pd.DataFrame()
    if len(slot_df) == 0:
        return None
    if "ts_probability" not in slot_df.columns or "ts_observed" not in slot_df.columns:
        return None

    slot_df = slot_df.dropna(subset=["ts_probability", "ts_observed"])
    if len(slot_df) == 0:
        return None

    y_prob = slot_df["ts_probability"].values.astype(float)
    y_true = slot_df["ts_observed"].values.astype(int)
    y_pred = (y_prob >= threshold).astype(int)

    return compute_metrics(y_true, y_pred, y_prob, CLIM_RATES.get(slot_id, 0.05))


def metrics_from_verification_report(slot_id: int) -> dict | None:
    """Pull pre-computed 30-day metrics from results/verification_report.json."""
    if not VERIF_REPORT.exists():
        return None
    try:
        with open(VERIF_REPORT) as f:
            vr = json.load(f)
        m30 = vr.get("metrics_30day", {}).get(str(slot_id), {})
        if not m30:
            return None
        return {k: m30.get(k) for k in ["TP","FP","FN","TN","n","POD","FAR","CSI","HSS","BIAS","Brier","BSS","AUROC"]}
    except Exception as e:
        print(f"  verification_report.json error: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=30,
                    help="Rolling window in days (default: 30)")
    args = ap.parse_args()

    print("=" * 60)
    print("  populate_skill_scores.py — WMO Skill Score Aggregator")
    print("=" * 60)
    print(f"  Window: {args.window} days")

    now_ist = datetime.now(IST)
    scores  = []

    # Try forecast_log.csv first (most authoritative — has raw probs)
    df_log = load_forecast_log(args.window)

    for slot_id in range(4):
        threshold = BASE_THRESHOLDS.get(slot_id, 0.16)
        # October fix for slot 2 — use base threshold for historical aggregation
        slot_name = SLOT_NAMES[slot_id]
        clim_rate = CLIM_RATES.get(slot_id, 0.05)

        metrics = None
        source  = "none"

        # Priority 1: compute from forecast_log.csv
        if df_log is not None:
            metrics = metrics_from_log(df_log, slot_id, threshold)
            if metrics:
                source = f"forecast_log ({args.window}d)"

        # Priority 2: pull from verification_report.json
        if metrics is None:
            metrics = metrics_from_verification_report(slot_id)
            if metrics:
                source = "verification_report (30d)"

        # Priority 3: pull from verification_today.json
        if metrics is None and VERIF_TODAY.exists():
            try:
                with open(VERIF_TODAY) as f:
                    vt = json.load(f)
                m = vt.get("metrics_30day", {}).get(str(slot_id), {})
                if m:
                    metrics = {k: m.get(k) for k in ["TP","FP","FN","TN","n","POD","FAR","CSI","HSS","BIAS","Brier","BSS","AUROC"]}
                    source  = "verification_today"
            except Exception:
                pass

        if metrics:
            pod_str  = f"{metrics['POD']:.3f}" if metrics.get("POD") is not None else "N/A"
            far_str  = f"{metrics['FAR']:.3f}" if metrics.get("FAR") is not None else "N/A"
            hss_str  = f"{metrics['HSS']:.3f}" if metrics.get("HSS") is not None else "N/A"
            bss_str  = f"{metrics['BSS']:.3f}" if metrics.get("BSS") is not None else "N/A"
            print(f"  Slot {slot_id}: POD={pod_str}  FAR={far_str}  "
                  f"HSS={hss_str}  BSS={bss_str}  [{source}]")
        else:
            print(f"  Slot {slot_id}: no data — climatology placeholder")
            metrics = {
                "TP": None, "FP": None, "FN": None, "TN": None, "n": 0,
                "POD": None, "FAR": None, "CSI": None, "HSS": None,
                "BIAS": None, "Brier": None, "BSS": None, "AUROC": None,
            }

        scores.append({
            "slot":          slot_id,
            "slot_name":     slot_name,
            "window_days":   args.window,
            "threshold":     threshold,
            "clim_rate":     clim_rate,
            "source":        source,
            "metrics":       metrics,
            "computed_at":   now_ist.strftime("%Y-%m-%d %H:%M IST"),
        })

    # ── Overall weighted summary ──────────────────────────────────────────────
    pods  = [s["metrics"].get("POD") for s in scores if s["metrics"].get("POD") is not None]
    fars  = [s["metrics"].get("FAR") for s in scores if s["metrics"].get("FAR") is not None]
    hsss  = [s["metrics"].get("HSS") for s in scores if s["metrics"].get("HSS") is not None]
    bsss  = [s["metrics"].get("BSS") for s in scores if s["metrics"].get("BSS") is not None]

    summary = {
        "mean_POD":  round(float(np.mean(pods)), 4) if pods else None,
        "mean_FAR":  round(float(np.mean(fars)), 4) if fars else None,
        "mean_HSS":  round(float(np.mean(hsss)), 4) if hsss else None,
        "mean_BSS":  round(float(np.mean(bsss)), 4) if bsss else None,
        "n_slots_with_data": sum(1 for s in scores if s["source"] != "none"),
        "computed_at": now_ist.strftime("%Y-%m-%d %H:%M IST"),
    }

    output = {
        "generated_at":  now_ist.strftime("%Y-%m-%d %H:%M IST"),
        "window_days":   args.window,
        "slot_scores":   scores,
        "summary":       summary,
    }

    DATA.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Saved → {OUT}")
    print(f"  Summary: POD={summary['mean_POD']}  FAR={summary['mean_FAR']}  "
          f"HSS={summary['mean_HSS']}  BSS={summary['mean_BSS']}")

    sys.exit(0)


if __name__ == "__main__":
    main()
