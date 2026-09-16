"""
verify_today.py
===============
CSIR Thunderstorm Nowcasting System — Daily Verification

Compares yesterday's forecasts (from data/forecast_log.csv) against IMD VOBL
surface observations to compute rolling 30-day verification metrics.

IMD file format: 43295_Table_2_*.csv  (YEAR, MN, DT columns + TH flag at col 22)
The filename is resolved dynamically — no hardcoded filename.

Outputs:
  data/verification_today.json   — daily verification summary
  Also injects "verification" key into forecast.json (if it exists)

Usage:
  python verify_today.py
  python verify_today.py --date 2026-08-10   # verify a specific date

Thresholds used for prediction binary:
  Slot 2 : 0.16 (0.10 in October — per october_threshold_fix.py)
  All other slots: see BASE_THRESHOLDS dict below
"""

import argparse
import json
import sys
from glob import glob
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

BASE          = Path(__file__).resolve().parent
FORECAST_LOG  = BASE / "data" / "forecast_log.csv"
OUTPUT_JSON   = BASE / "data" / "verification_today.json"
FORECAST_JSON = BASE / "forecast.json"

# Operational thresholds (must match forecast_action.py)
BASE_THRESHOLDS = {0: 0.24, 1: 0.15, 2: 0.16, 3: 0.39}

def get_threshold(slot: int, month: int) -> float:
    if slot == 2 and month == 10:
        return 0.10   # October post-monsoon fix
    return BASE_THRESHOLDS.get(slot, 0.16)


# ── IMD file discovery ────────────────────────────────────────────────────────

def find_imd_file() -> Path | None:
    """Glob for IMD daily surface observation CSV in the repo root.
    Looks for 43295_Table_2_*.csv and returns the most recently modified match."""
    candidates = sorted(
        BASE.glob("43295_Table_2_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        print(f"  IMD file: {candidates[0].name}")
        return candidates[0]
    # Also check data/ subdirectory
    candidates = sorted(
        (BASE / "data").glob("43295_Table_2_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        print(f"  IMD file (data/): {candidates[0].name}")
        return candidates[0]
    return None


def write_empty_verification(date_str: str, reason: str) -> dict:
    summary = {
        "date_verified":        date_str,
        "slots_verified":       0,
        "thunderstorm_observed": None,
        "slot_results":          [],
        "rolling_30d": {"POD": 0.0, "FAR": 0.0, "HSS": 0.0, "Brier": 0.0},
        "note":                  reason,
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(summary, indent=2))
    _inject_into_forecast(summary)
    return summary


def _inject_into_forecast(data: dict) -> None:
    if not FORECAST_JSON.exists():
        return
    try:
        with open(FORECAST_JSON) as f:
            fc = json.load(f)
        fc["verification_daily"] = data
        with open(FORECAST_JSON, "w") as f:
            json.dump(fc, f, indent=2)
        print("  ✓ Injected into forecast.json")
    except Exception as e:
        print(f"  ⚠ Could not inject into forecast.json: {e}")


def compute_metrics(df: pd.DataFrame) -> dict:
    """Compute POD/FAR/HSS/Brier/CSI from a dataframe with predicted/actual columns."""
    df = df.dropna(subset=["predicted", "actual"])
    if len(df) == 0:
        return {"POD": 0.0, "FAR": 0.0, "HSS": 0.0, "Brier": 0.0, "CSI": 0.0, "n": 0}

    hits = int(((df["predicted"] == 1) & (df["actual"] == 1)).sum())
    miss = int(((df["predicted"] == 0) & (df["actual"] == 1)).sum())
    fa   = int(((df["predicted"] == 1) & (df["actual"] == 0)).sum())
    cn   = int(((df["predicted"] == 0) & (df["actual"] == 0)).sum())

    pod   = hits / (hits + miss) if (hits + miss) > 0 else 0.0
    far   = fa / (hits + fa) if (hits + fa) > 0 else 0.0
    csi   = hits / (hits + miss + fa) if (hits + miss + fa) > 0 else 0.0
    denom = ((hits + miss) * (miss + cn) + (hits + fa) * (fa + cn))
    hss   = 2 * (hits * cn - miss * fa) / denom if denom > 0 else 0.0

    # Brier: use ts_probability if available, else binary predicted
    if "ts_probability" in df.columns:
        brier = float(((df["ts_probability"].fillna(0) - df["actual"]) ** 2).mean())
    else:
        brier = float(((df["predicted"] - df["actual"]) ** 2).mean())

    return {
        "POD": round(pod, 3), "FAR": round(far, 3),
        "HSS": round(hss, 3), "Brier": round(brier, 4),
        "CSI": round(csi, 3),
        "n": len(df), "hits": hits, "misses": miss,
        "false_alarms": fa, "correct_negatives": cn,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, default=None,
                    help="Verification date (YYYY-MM-DD), default: yesterday")
    args = ap.parse_args()

    yesterday = (
        args.date if args.date
        else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    )
    print(f"  Verifying: {yesterday}")

    # ── Load forecast log ─────────────────────────────────────────────────────
    if not FORECAST_LOG.exists():
        print(f"  ⚠ forecast_log.csv not found — no verification possible")
        write_empty_verification(yesterday, "forecast_log.csv missing")
        sys.exit(0)

    df = pd.read_csv(FORECAST_LOG)

    # Ensure predicted column is present using correct slot thresholds
    if "predicted" not in df.columns:
        def apply_thresh(row):
            try:
                month = int(str(row.get("date", "2000-01-01")).split("-")[1])
            except Exception:
                month = datetime.now().month
            return int(float(row.get("ts_probability", 0)) >= get_threshold(int(row.get("slot", 0)), month))
        df["predicted"] = df.apply(apply_thresh, axis=1)

    yesterday_rows = df[df["date"] == yesterday]
    if yesterday_rows.empty:
        print(f"  ⚠ No forecast rows for {yesterday}")
        write_empty_verification(yesterday, f"No forecasts logged for {yesterday}")
        sys.exit(0)

    # ── Load IMD observations ─────────────────────────────────────────────────
    imd_path = find_imd_file()
    if imd_path is None:
        print("  ⚠ No IMD observation file found (43295_Table_2_*.csv)")
        write_empty_verification(yesterday, "IMD observation file not found")
        sys.exit(0)

    try:
        imd = pd.read_csv(imd_path)
        # Build DATE column from YEAR / MN / DT columns
        imd["DATE"] = pd.to_datetime(
            imd[["YEAR", "MN", "DT"]].rename(
                columns={"YEAR": "year", "MN": "month", "DT": "day"}
            )
        ).dt.strftime("%Y-%m-%d")
    except Exception as e:
        print(f"  ⚠ IMD file parse error: {e}")
        write_empty_verification(yesterday, f"IMD parse error: {e}")
        sys.exit(0)

    if yesterday not in imd["DATE"].values:
        print(f"  ⚠ {yesterday} not in IMD file (may not be updated yet)")
        write_empty_verification(yesterday, f"{yesterday} absent from IMD file")
        sys.exit(0)

    # TH (thunderstorm) flag is column index 22 (0-based) — confirmed against IMD format
    imd_row = imd.loc[imd["DATE"] == yesterday].iloc[0]
    th_flag = int(imd_row.iloc[22])
    print(f"  IMD TH flag for {yesterday}: {th_flag}")

    # ── Assign actual outcomes per slot ───────────────────────────────────────
    # TH=1 means thunderstorm occurred; we assign it to Slot 2 and 3 (afternoon/evening)
    # which is when VOBL storms most commonly occur. Slot 0/1 (night/morning) get 0
    # unless the TH flag specifically indicates nocturnal/morning storms (not in this data).
    actuals = []
    for _, row in yesterday_rows.iterrows():
        slot   = int(row["slot"])
        actual = 1 if (th_flag == 1 and slot in [2, 3]) else 0
        actuals.append(actual)

    # Write actuals back into df for rolling metrics
    for (idx, row), actual in zip(yesterday_rows.iterrows(), actuals):
        df.loc[idx, "actual"] = actual

    # ── Rolling 30-day metrics ────────────────────────────────────────────────
    # Use last 120 rows (30 days × 4 slots) with actual labels available
    df_with_actual = df.dropna(subset=["actual"]).tail(120)
    metrics_30d    = compute_metrics(df_with_actual)

    # Per-slot metrics
    metrics_per_slot = {}
    for slot_id in range(4):
        slot_df = df_with_actual[df_with_actual["slot"] == slot_id]
        if len(slot_df) > 0:
            metrics_per_slot[str(slot_id)] = compute_metrics(slot_df)

    # ── Build summary ─────────────────────────────────────────────────────────
    summary = {
        "date_verified":         yesterday,
        "slots_verified":        len(yesterday_rows),
        "thunderstorm_observed": bool(th_flag),
        "slot_results": [
            {
                "slot":      int(row["slot"]),
                "predicted": bool(row["predicted"]),
                "actual":    bool(actual),
                "correct":   bool(int(row["predicted"]) == actual),
                "ts_probability": round(float(row.get("ts_probability", 0)), 4),
                "threshold": get_threshold(int(row["slot"]),
                                           int(str(yesterday).split("-")[1])),
            }
            for row, actual in zip(yesterday_rows.to_dict("records"), actuals)
        ],
        "rolling_30d": metrics_30d,
        "rolling_30d_per_slot": metrics_per_slot,
        "n_days_in_window": len(df_with_actual["date"].unique()) if "date" in df_with_actual.columns else None,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M IST"),
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"  Verification complete → {OUTPUT_JSON}")
    print(f"  30d: POD={metrics_30d['POD']}  FAR={metrics_30d['FAR']}  "
          f"HSS={metrics_30d['HSS']}  Brier={metrics_30d['Brier']}")

    _inject_into_forecast(summary)


if __name__ == "__main__":
    main()
