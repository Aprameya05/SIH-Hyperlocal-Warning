"""
populate_cape_baseline.py
=========================
CSIR Thunderstorm Prediction System — Drift Detection Baseline Generator

Extracts the CAPE distribution from the training feature CSV and saves it as
  data/cape_training_baseline.npy

This enables drift_check.yml to compute PSI (Population Stability Index) by
comparing recent GFS CAPE values against the training distribution baseline.

Run once after training data is finalized. Re-run any time training data expands.

Usage:
    python populate_cape_baseline.py
    python populate_cape_baseline.py --csv path/to/features.csv
    python populate_cape_baseline.py --train-years 2015 2016 2017 2018 2019 2020 2021 2022
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(".")
DATA = BASE / "data"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None,
                    help="Path to feature CSV (auto-detected if omitted)")
    ap.add_argument("--train-years", nargs="+", type=int,
                    default=list(range(2015, 2023)),
                    help="Training years to include (default: 2015-2022)")
    ap.add_argument("--out", default=str(DATA / "cape_training_baseline.npy"),
                    help="Output .npy path")
    args = ap.parse_args()

    # ── Locate feature CSV ────────────────────────────────────────────────────
    candidates = [
        args.csv,
        str(DATA / "bengaluru_thunderstorm_features_merged.csv"),
        str(BASE / "bengaluru_thunderstorm_features_merged.csv"),
        str(DATA / "bengaluru_features.csv"),
        str(BASE / "bengaluru_features.csv"),
    ]
    csv_path = None
    for c in candidates:
        if c and Path(c).exists():
            csv_path = Path(c)
            break

    if csv_path is None:
        print("ERROR: Could not find feature CSV. Pass --csv <path>")
        sys.exit(1)

    print(f"Loading: {csv_path}")
    df = pd.read_csv(csv_path, parse_dates=["date"], low_memory=False)
    print(f"  Total rows: {len(df)}")

    # Filter to training years
    df = df[df["date"].dt.year.isin(args.train_years)].copy()
    print(f"  Training years {args.train_years}: {len(df)} rows")

    if "CAPE" not in df.columns:
        print("ERROR: 'CAPE' column not found in CSV")
        print(f"  Available columns: {list(df.columns[:20])}")
        sys.exit(1)

    cape = df["CAPE"].dropna().values.astype(float)
    cape = cape[np.isfinite(cape) & (cape >= 0)]
    print(f"  Valid CAPE values: {len(cape)}")
    print(f"  Distribution: min={cape.min():.0f}  p25={np.percentile(cape,25):.0f}  "
          f"median={np.median(cape):.0f}  p75={np.percentile(cape,75):.0f}  "
          f"p95={np.percentile(cape,95):.0f}  max={cape.max():.0f}")

    # Also save KI and SRH if available
    extras = {}
    for col in ["K_INDEX", "LIFTED_INDEX", "TOTALS_TOTALS", "PRECIP_WATER"]:
        if col in df.columns:
            vals = df[col].dropna().values.astype(float)
            vals = vals[np.isfinite(vals)]
            extras[col] = vals
            print(f"  {col}: n={len(vals)}  median={np.median(vals):.2f}")

    # Save CAPE baseline
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(out_path, cape)
    print(f"\n  Saved CAPE baseline → {out_path}  ({len(cape)} values)")

    # Save supplementary baselines for richer PSI checks
    for col, vals in extras.items():
        extra_path = out_path.parent / f"{col.lower()}_training_baseline.npy"
        np.save(extra_path, vals)
        print(f"  Saved {col} baseline → {extra_path}")

    # Print PSI bin boundaries (for reference in drift_check.yml)
    bins = np.percentile(cape, [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    print(f"\n  Decile bin edges (for PSI): {np.round(bins).astype(int).tolist()}")
    print("\n  Done. Run populate_cape_baseline.py again if training data expands.")


if __name__ == "__main__":
    main()
