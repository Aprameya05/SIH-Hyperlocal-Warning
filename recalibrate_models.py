"""
recalibrate_models.py
=====================
CSIR Thunderstorm Nowcasting System — Post-hoc Isotonic Recalibration

Loads existing v5/v6 XGBoost models, fits isotonic regression calibrators
on 2024-2025 holdout data, and saves new *_recal.pkl wrappers.

Why recalibrate?
  XGBoost probability outputs are often over- or under-confident, especially
  on holdout years not seen during training. Isotonic regression corrects
  the probability scale using a monotone mapping fitted on real outcomes.
  It does NOT retrain the underlying tree model — calibration only.

Output per slot:
  models/nowcast_slot{N}_xgb_v6_recal.pkl   — CalibratedClassifierCV wrapper
  results/calibration_results_v6.json        — Brier score before/after
  results/calibration_curves_v6.png          — reliability diagrams

Usage:
  python recalibrate_models.py
  python recalibrate_models.py --slots 2 3          # specific slots
  python recalibrate_models.py --source v5          # calibrate v5 instead
  python recalibrate_models.py --cal-years 2024     # single holdout year
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import joblib

BASE    = Path(__file__).resolve().parent
MODELS  = BASE / "models"
DATA    = BASE / "data"
RESULTS = BASE / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

# Training CSV — bengaluru_6hr_training_dataset_v4.csv has the most complete set
TRAINING_CSV_PRIORITY = [
    DATA / "bengaluru_6hr_training_dataset_v4.csv",
    DATA / "bengaluru_6hr_training_dataset_v3.csv",
    DATA / "bengaluru_6hr_training_dataset.csv",
    BASE / "bengaluru_thunderstorm_features_merged.csv",
]

# Must match forecast_action.py
BASE_THRESHOLDS = {0: 0.24, 1: 0.15, 2: 0.16, 3: 0.39}

SLOT_LABELS = {
    0: "0001-0600 IST",
    1: "0601-1200 IST",
    2: "1201-1800 IST",
    3: "1801-2400 IST",
}


def find_training_csv() -> Path | None:
    for p in TRAINING_CSV_PRIORITY:
        if p.exists():
            print(f"  Training CSV: {p.name}")
            return p
    return None


def load_model(slot: int, source: str = "v6") -> tuple:
    """Load model artifact and return (model, feature_cols)."""
    candidates = [
        MODELS / f"nowcast_slot{slot}_xgb_{source}_temporal.pkl",
        MODELS / f"nowcast_slot{slot}_xgb_v6_temporal.pkl",
        MODELS / f"nowcast_slot{slot}_xgb_v5_temporal.pkl",
        MODELS / f"nowcast_slot{slot}_xgb_v4_ensemble.pkl",
        MODELS / f"nowcast_slot{slot}_xgb_v4_calibrated.pkl",
        MODELS / f"nowcast_slot{slot}_xgb_v3_calibrated.pkl",
    ]
    for path in candidates:
        if path.exists():
            artifact = joblib.load(path)
            # Support dict wrapper or raw model
            if isinstance(artifact, dict):
                model = artifact.get("model") or artifact.get("calibrated")
                feat_cols = artifact.get("feature_cols") or artifact.get("features")
            else:
                model = artifact
                feat_cols = None
            print(f"  Slot {slot}: loaded {path.name}")
            return model, feat_cols, path
    raise FileNotFoundError(f"No model found for slot {slot} (source={source})")


def build_feature_vector(df: pd.DataFrame, feat_cols: list | None) -> pd.DataFrame:
    """Return feature matrix aligned to model's expected columns."""
    if feat_cols is None:
        # Drop known non-feature columns and use what remains
        drop_cols = {"date", "DATE", "LABEL", "ts_label", "slot", "year", "month",
                     "day", "actual", "predicted", "ts_observed"}
        avail = [c for c in df.columns if c not in drop_cols]
        return df[avail]

    # Align to known feature list
    missing = [c for c in feat_cols if c not in df.columns]
    if missing:
        print(f"    Warning: {len(missing)} features missing, filling with 0")
        for c in missing:
            df[c] = 0.0
    return df[feat_cols]


def compute_brier(y_true, y_prob):
    return float(np.mean((y_prob - y_true) ** 2))


def plot_reliability(slot_id, y_true_pre, y_prob_pre, y_true_post, y_prob_post, out_path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from sklearn.calibration import calibration_curve

        fig, axes = plt.subplots(1, 2, figsize=(12, 5))

        for ax, y_true, y_prob, label, color in [
            (axes[0], y_true_pre,  y_prob_pre,  "Before (raw v6)",       "#e74c3c"),
            (axes[1], y_true_post, y_prob_post, "After (isotonic recal)", "#27ae60"),
        ]:
            if len(y_true) < 5 or y_true.sum() == 0:
                ax.text(0.5, 0.5, "Insufficient data", ha="center", va="center",
                        transform=ax.transAxes)
                continue
            try:
                frac_pos, mean_pred = calibration_curve(y_true, y_prob, n_bins=8,
                                                        strategy="quantile")
                ax.plot([0, 1], [0, 1], "k--", lw=1, label="Perfect")
                ax.plot(mean_pred, frac_pos, "o-", color=color, lw=2,
                        label=f"{label}\nBrier={compute_brier(y_true, y_prob):.4f}")
                ax.fill_between(mean_pred, frac_pos, mean_pred,
                                alpha=0.15, color=color)
                ax.set_xlabel("Mean predicted probability", fontsize=10)
                ax.set_ylabel("Fraction of positives", fontsize=10)
                ax.set_title(f"Slot {slot_id} — {label}", fontsize=10)
                ax.legend(fontsize=8)
                ax.grid(alpha=0.3)
                ax.set_xlim(0, 1); ax.set_ylim(0, 1)
            except Exception as e:
                ax.text(0.5, 0.5, str(e), ha="center", va="center", fontsize=8,
                        transform=ax.transAxes)

        fig.tight_layout()
        fig.savefig(out_path, dpi=130, bbox_inches="tight")
        plt.close()
        print(f"    Reliability diagram → {out_path.name}")
    except ImportError:
        print("    matplotlib not installed — skipping plots")
    except Exception as e:
        print(f"    Plot error: {e}")


def recalibrate_slot(slot_id: int, df_cal: pd.DataFrame,
                     source: str, dry_run: bool) -> dict:
    """Fit isotonic calibration for one slot. Returns metric dict."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.isotonic import IsotonicRegression

    print(f"\n{'='*55}")
    print(f"  Slot {slot_id}  ({SLOT_LABELS[slot_id]})")
    print(f"{'='*55}")

    # Load model
    try:
        model, feat_cols, model_path = load_model(slot_id, source)
    except FileNotFoundError as e:
        print(f"  SKIP: {e}")
        return {"slot": slot_id, "status": "skipped", "reason": str(e)}

    # Filter calibration data to this slot
    if "slot" in df_cal.columns:
        slot_df = df_cal[df_cal["slot"] == slot_id].copy()
    else:
        # Legacy: slot 2 data only (whole-day labels)
        slot_df = df_cal.copy()
        if slot_id != 2:
            print(f"  SKIP: no per-slot column in training CSV — only slot 2 supported")
            return {"slot": slot_id, "status": "skipped",
                    "reason": "no slot column in training CSV"}

    label_col = "LABEL" if "LABEL" in slot_df.columns else "ts_label"
    slot_df = slot_df.dropna(subset=[label_col])
    print(f"  Calibration rows: {len(slot_df)}  (TS events: {int(slot_df[label_col].sum())})")

    if len(slot_df) < 30 or slot_df[label_col].sum() < 5:
        print(f"  SKIP: too few samples for reliable calibration")
        return {"slot": slot_id, "status": "skipped",
                "reason": f"only {len(slot_df)} rows, {int(slot_df[label_col].sum())} positives"}

    y_true = slot_df[label_col].values.astype(int)
    X = build_feature_vector(slot_df, feat_cols)

    # Raw model probabilities
    try:
        if hasattr(model, "predict_proba"):
            y_prob_raw = model.predict_proba(X)[:, 1]
        else:
            # XGBoost Booster — needs DMatrix
            import xgboost as xgb
            dm = xgb.DMatrix(X)
            y_prob_raw = model.predict(dm)
    except Exception as e:
        print(f"  ERROR getting raw probabilities: {e}")
        return {"slot": slot_id, "status": "error", "reason": str(e)}

    brier_before = compute_brier(y_true, y_prob_raw)
    print(f"  Brier BEFORE calibration: {brier_before:.4f}")

    if dry_run:
        print("  --dry-run: skipping calibrator fit and save")
        return {"slot": slot_id, "status": "dry_run",
                "brier_before": round(brier_before, 4)}

    # Fit isotonic regression on raw probabilities
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(y_prob_raw, y_true)
    y_prob_cal = iso.transform(y_prob_raw)

    brier_after = compute_brier(y_true, y_prob_cal)
    print(f"  Brier AFTER  calibration: {brier_after:.4f}  "
          f"({'improved' if brier_after < brier_before else 'no improvement'})")

    # Save: wrap as artifact dict compatible with forecast_action.py
    out_path = MODELS / f"nowcast_slot{slot_id}_xgb_{source}_recal.pkl"
    artifact = {
        "model":        model,
        "calibrator":   iso,
        "feature_cols": list(X.columns) if hasattr(X, "columns") else feat_cols,
        "slot":         slot_id,
        "source_model": str(model_path.name),
        "calibrated_on": f"{len(slot_df)} rows (2024-2025 holdout)",
        "calibrated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "brier_before": round(brier_before, 4),
        "brier_after":  round(brier_after, 4),
        "version":      f"{source}_recal",
    }
    joblib.dump(artifact, out_path)
    print(f"  Saved → {out_path.name}")

    # Reliability diagram
    plot_path = RESULTS / f"reliability_slot{slot_id}_{source}_recal.png"
    plot_reliability(slot_id, y_true, y_prob_raw, y_true, y_prob_cal, plot_path)

    return {
        "slot":         slot_id,
        "status":       "ok",
        "model_used":   str(model_path.name),
        "cal_rows":     len(slot_df),
        "cal_positives": int(slot_df[label_col].sum()),
        "brier_before": round(brier_before, 4),
        "brier_after":  round(brier_after, 4),
        "improvement":  round(brier_before - brier_after, 4),
        "output":       str(out_path.name),
    }


def main():
    ap = argparse.ArgumentParser(description="Isotonic recalibration of v5/v6 models")
    ap.add_argument("--slots",     nargs="+", type=int, default=[0, 1, 2, 3],
                    help="Slots to recalibrate (default: all four)")
    ap.add_argument("--source",    default="v6",
                    help="Model version to calibrate: v6 (default) or v5")
    ap.add_argument("--cal-years", nargs="+", type=int, default=[2024, 2025],
                    help="Holdout years to use for calibration (default: 2024 2025)")
    ap.add_argument("--dry-run",   action="store_true",
                    help="Compute Brier score but don't save calibrated models")
    args = ap.parse_args()

    print("=" * 60)
    print("  recalibrate_models.py — Isotonic Calibration")
    print("=" * 60)
    print(f"  Slots       : {args.slots}")
    print(f"  Source      : {args.source}")
    print(f"  Cal years   : {args.cal_years}")
    print(f"  Dry run     : {args.dry_run}")

    csv_path = find_training_csv()
    if csv_path is None:
        print("\nERROR: no training CSV found. Check data/ directory.")
        sys.exit(1)

    df_all = pd.read_csv(csv_path, parse_dates=["date"])

    # Normalise label column
    if "LABEL" not in df_all.columns and "ts_label" in df_all.columns:
        df_all = df_all.rename(columns={"ts_label": "LABEL"})

    # Filter to calibration years (holdout — not seen during v6 training)
    df_cal = df_all[df_all["date"].dt.year.isin(args.cal_years)].copy()
    print(f"\n  Total rows in CSV     : {len(df_all)}")
    print(f"  Calibration year rows : {len(df_cal)}")
    if len(df_cal) == 0:
        print(f"\nERROR: No rows found for years {args.cal_years}. "
              f"Available years: {sorted(df_all['date'].dt.year.unique().tolist())}")
        sys.exit(1)

    results = []
    for slot_id in args.slots:
        result = recalibrate_slot(slot_id, df_cal, args.source, args.dry_run)
        results.append(result)

    # Save summary
    summary = {
        "generated_at":  datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source_version": args.source,
        "cal_years":      args.cal_years,
        "dry_run":        args.dry_run,
        "slots":          results,
    }
    out_json = RESULTS / f"calibration_results_{args.source}_recal.json"
    if not args.dry_run:
        with open(out_json, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"\n  Summary → {out_json}")

    # Print table
    print("\n" + "=" * 60)
    print(f"  {'Slot':<6} {'Status':<10} {'Brier Before':<15} {'Brier After':<14} {'Delta'}")
    print("  " + "-" * 56)
    for r in results:
        if r["status"] == "ok":
            delta = r["improvement"]
            flag  = "✓" if delta > 0 else "~"
            print(f"  {r['slot']:<6} {r['status']:<10} "
                  f"{r['brier_before']:<15.4f} {r['brier_after']:<14.4f} "
                  f"{flag} {delta:+.4f}")
        else:
            print(f"  {r['slot']:<6} {r['status']:<10} {r.get('reason','')}")
    print("=" * 60)

    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\n  Done. Calibrated {ok}/{len(results)} slots.")
    if ok > 0 and not args.dry_run:
        print("\n  To use recalibrated models in production, update forecast_action.py")
        print("  to load nowcast_slot{N}_xgb_v6_recal.pkl and call:")
        print("      prob = artifact['calibrator'].transform([raw_prob])[0]")


if __name__ == "__main__":
    main()
