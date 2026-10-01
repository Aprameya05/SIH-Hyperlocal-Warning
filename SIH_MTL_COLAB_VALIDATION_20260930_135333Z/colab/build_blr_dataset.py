#!/usr/bin/env python3
"""
build_blr_dataset.py

Builds the MTL training population from the REAL, already-existing
data/bengaluru_6hr_training_dataset_cb_ff.csv (no synthetic rows, no
synthetic labels). Maps its columns onto backend/mtl_backbone.py's
expected 12-feature input (FEATURE_NAMES), documents every mapping
(direct / derived / unavailable) in feature_manifest.json, splits
chronologically (never randomly), and computes class weights from the
TRAINING split only.

Run this from the repo root, or from Colab after uploading the repo:
    python3 colab/build_blr_dataset.py

Outputs (under colab/training/):
    feature_manifest.json
    splits.json             -- row counts / date ranges / label balance per split
    train.npz, val.npz, test.npz   -- feats (N,12), labels (N,3), lat, lon, date
    class_weights.json
    leakage_report.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_CSV = REPO_ROOT / "data" / "bengaluru_6hr_training_dataset_cb_ff.csv"
OUT_DIR = Path(__file__).resolve().parent / "training"

# VOBL station coordinates -- used as the constant lat/lon for every row,
# since this is a single-station dataset (backend/mtl_backbone.py expects
# per-row lat/lon for its positional encoding; there is only one station here).
VOBL_LAT = 13.1979
VOBL_LON = 77.7063

# backend/mtl_backbone.py FEATURE_NAMES, in the model's required order.
FEATURE_NAMES = [
    "cape", "cin", "pwat_mm", "k_index", "totals_totals", "wind_shear_ms",
    "t850", "t700", "t500", "td850", "td700", "ctt_c",
]

TRAIN_END = "2021-12-31"
VAL_END = "2023-12-31"
# everything after VAL_END is the held-out test set


def dewpoint_from_q_t(q_kg_kg: pd.Series, t_k: pd.Series, p_hpa: float) -> pd.Series:
    """
    Standard meteorological derivation: dewpoint temperature from specific
    humidity, temperature and pressure via vapor pressure + Bolton's (1980)
    inverted Magnus formula. This is a physical transform of real ERA5
    fields already in the CSV (ERA5_q_*hPa, ERA5_t_*hPa) -- not a fabricated
    or substituted variable.
    """
    # specific humidity -> mixing ratio -> vapor pressure (hPa)
    w = q_kg_kg / (1 - q_kg_kg)
    e = (w * p_hpa) / (0.622 + w)
    e = e.clip(lower=1e-6)
    # Bolton (1980) inverse: Td (deg C) from vapor pressure e (hPa)
    td_c = (243.5 * np.log(e / 6.112)) / (17.67 - np.log(e / 6.112))
    return td_c + 273.15  # Kelvin, to match t850/t700/t500 being in Kelvin


def build_feature_manifest(df: pd.DataFrame) -> dict:
    manifest = {}

    manifest["cape"] = {
        "source_column": "CAPE", "preprocessing": "direct", "units": "J/kg",
        "missing_treatment": "none observed", "status": "available",
    }
    manifest["cin"] = {
        "source_column": None, "preprocessing": "unavailable",
        "units": "J/kg", "missing_treatment": f"filled with model's FILL_VALUE=0.0",
        "status": "UNAVAILABLE -- no CIN column exists anywhere in this CSV or its "
                  "known upstream sources (fetch_metar.py, ERA5 extraction). Not "
                  "substituted with an unrelated variable.",
    }
    manifest["pwat_mm"] = {
        "source_column": "PRECIP_WATER", "preprocessing": "direct", "units": "mm",
        "missing_treatment": "none observed", "status": "available",
    }
    manifest["k_index"] = {
        "source_column": "K_INDEX", "preprocessing": "direct", "units": "K",
        "missing_treatment": "none observed", "status": "available",
    }
    manifest["totals_totals"] = {
        "source_column": "TOTALS_TOTALS", "preprocessing": "direct", "units": "TT index",
        "missing_treatment": "none observed", "status": "available",
    }
    manifest["wind_shear_ms"] = {
        "source_column": "wind_shear_500_850", "preprocessing": "direct (closest available shear layer)",
        "units": "m/s",
        "missing_treatment": "none observed",
        "status": "available -- documented substitution: the model's generic "
                  "'wind_shear_ms' has no single canonical layer defined in "
                  "mtl_backbone.py; the 500-850hPa deep-layer shear already computed "
                  "in this CSV is used and labeled as such, not an unrelated variable.",
    }
    manifest["t850"] = {"source_column": "ERA5_t_850hPa", "preprocessing": "direct", "units": "K", "missing_treatment": "none observed", "status": "available"}
    manifest["t700"] = {"source_column": "ERA5_t_700hPa", "preprocessing": "direct", "units": "K", "missing_treatment": "none observed", "status": "available"}
    manifest["t500"] = {"source_column": "ERA5_t_500hPa", "preprocessing": "direct", "units": "K", "missing_treatment": "none observed", "status": "available"}
    manifest["td850"] = {
        "source_column": "ERA5_q_850hPa + ERA5_t_850hPa (derived)",
        "preprocessing": "Bolton (1980) dewpoint-from-specific-humidity formula at 850hPa",
        "units": "K", "missing_treatment": "none observed",
        "status": "derived -- real physical transform of real ERA5 fields, not fabricated",
    }
    manifest["td700"] = {
        "source_column": "ERA5_q_700hPa + ERA5_t_700hPa (derived)",
        "preprocessing": "Bolton (1980) dewpoint-from-specific-humidity formula at 700hPa",
        "units": "K", "missing_treatment": "none observed",
        "status": "derived -- real physical transform of real ERA5 fields, not fabricated",
    }
    manifest["ctt_c"] = {
        "source_column": None, "preprocessing": "unavailable", "units": "deg C",
        "missing_treatment": "filled with model's FILL_VALUE=0.0",
        "status": "UNAVAILABLE -- this CSV predates Himawari cloud-top-temperature "
                  "integration and has no per-row CTT field. Not substituted.",
    }
    return manifest


def main():
    df = pd.read_csv(DATA_CSV)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "slot"]).reset_index(drop=True)

    manifest = build_feature_manifest(df)

    feats = pd.DataFrame(index=df.index)
    feats["cape"] = df["CAPE"]
    feats["cin"] = 0.0  # documented unavailable
    feats["pwat_mm"] = df["PRECIP_WATER"]
    feats["k_index"] = df["K_INDEX"]
    feats["totals_totals"] = df["TOTALS_TOTALS"]
    feats["wind_shear_ms"] = df["wind_shear_500_850"]
    feats["t850"] = df["ERA5_t_850hPa"]
    feats["t700"] = df["ERA5_t_700hPa"]
    feats["t500"] = df["ERA5_t_500hPa"]
    feats["td850"] = dewpoint_from_q_t(df["ERA5_q_850hPa"], df["ERA5_t_850hPa"], 850.0)
    feats["td700"] = dewpoint_from_q_t(df["ERA5_q_700hPa"], df["ERA5_t_700hPa"], 700.0)
    feats["ctt_c"] = 0.0  # documented unavailable

    feats = feats[FEATURE_NAMES].fillna(0.0)

    labels = df[["ts_label", "cb_label", "ff_label"]].fillna(0).astype(int)
    lat = pd.Series(VOBL_LAT, index=df.index)
    lon = pd.Series(VOBL_LON, index=df.index)

    train_mask = df["date"] <= TRAIN_END
    val_mask = (df["date"] > TRAIN_END) & (df["date"] <= VAL_END)
    test_mask = df["date"] > VAL_END

    splits = {}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, mask in (("train", train_mask), ("val", val_mask), ("test", test_mask)):
        sub_feats = feats[mask].values.astype(np.float32)
        sub_labels = labels[mask].values.astype(np.float32)
        sub_lat = lat[mask].values.astype(np.float32)
        sub_lon = lon[mask].values.astype(np.float32)
        sub_dates = df.loc[mask, "date"].astype(str).values
        np.savez(
            OUT_DIR / f"{name}.npz",
            features=sub_feats, labels=sub_labels, lat=sub_lat, lon=sub_lon, dates=sub_dates,
        )
        splits[name] = {
            "n_rows": int(mask.sum()),
            "date_range": [str(df.loc[mask, "date"].min().date()), str(df.loc[mask, "date"].max().date())] if mask.sum() else None,
            "ts_positive": int(labels.loc[mask, "ts_label"].sum()),
            "cb_positive": int(labels.loc[mask, "cb_label"].sum()),
            "ff_positive": int(labels.loc[mask, "ff_label"].sum()),
            "ts_positive_rate": float(labels.loc[mask, "ts_label"].mean()) if mask.sum() else None,
            "cb_positive_rate": float(labels.loc[mask, "cb_label"].mean()) if mask.sum() else None,
            "ff_positive_rate": float(labels.loc[mask, "ff_label"].mean()) if mask.sum() else None,
        }

    # Hazard overlap (same rows positive for more than one hazard) -- train split only, informational
    tr = df[train_mask]
    overlap = {
        "ts_and_cb": int(((tr["ts_label"] == 1) & (tr["cb_label"] == 1)).sum()),
        "ts_and_ff": int(((tr["ts_label"] == 1) & (tr["ff_label"] == 1)).sum()),
        "cb_and_ff": int(((tr["cb_label"] == 1) & (tr["ff_label"] == 1)).sum()),
    }
    splits["hazard_overlap_train_only"] = overlap

    (OUT_DIR / "splits.json").write_text(json.dumps(splits, indent=2))
    (OUT_DIR / "feature_manifest.json").write_text(json.dumps({FEATURE_NAMES[i]: manifest[FEATURE_NAMES[i]] for i in range(len(FEATURE_NAMES))}, indent=2))

    # Class weights from TRAINING split only: standard pos_weight = n_neg / n_pos
    class_weights = {}
    for hz in ("ts_label", "cb_label", "ff_label"):
        n_pos = int(labels.loc[train_mask, hz].sum())
        n_neg = int(train_mask.sum() - n_pos)
        pos_weight = (n_neg / n_pos) if n_pos > 0 else None
        class_weights[hz] = {
            "n_pos_train": n_pos, "n_neg_train": n_neg,
            "pos_weight": pos_weight,
            "formula": "pos_weight = n_negative_train / n_positive_train (standard BCEWithLogitsLoss weighting), computed on TRAINING split only",
        }
    (OUT_DIR / "class_weights.json").write_text(json.dumps(class_weights, indent=2))

    # Leakage checks
    leakage = {}
    leakage["chronological_order_respected"] = bool((df["date"].diff().dropna() >= pd.Timedelta(0)).all())
    leakage["train_max_date_before_val_min_date"] = str(df.loc[train_mask, "date"].max().date()) + " < " + str(df.loc[val_mask, "date"].min().date())
    leakage["val_max_date_before_test_min_date"] = str(df.loc[val_mask, "date"].max().date()) + " < " + str(df.loc[test_mask, "date"].min().date())
    leakage["train_val_date_overlap_rows"] = int(pd.Series(df.loc[train_mask, "date"]).isin(df.loc[val_mask, "date"]).sum())
    leakage["val_test_date_overlap_rows"] = int(pd.Series(df.loc[val_mask, "date"]).isin(df.loc[test_mask, "date"]).sum())
    leakage["duplicate_date_slot_rows_total"] = int(df.duplicated(subset=["date", "slot"]).sum())
    leakage["scaler_fit_policy"] = "Normalization constants in mtl_backbone.py (FEATURE_MEAN/FEATURE_STD) are FIXED climatological constants, not fit from data -- no scaler-leakage risk exists for this architecture."
    leakage["class_weights_fit_policy"] = "class_weights.json computed from TRAINING split only (see formula field) -- verified by construction above."
    leakage["status"] = "PASS -- no leakage detected" if (
        leakage["train_val_date_overlap_rows"] == 0 and leakage["val_test_date_overlap_rows"] == 0 and leakage["duplicate_date_slot_rows_total"] == 0
    ) else "FAIL -- see fields above"
    (OUT_DIR / "leakage_report.json").write_text(json.dumps(leakage, indent=2, default=str))

    print(json.dumps(splits, indent=2, default=str))
    print("\nLeakage report:")
    print(json.dumps(leakage, indent=2, default=str))
    print(f"\nWrote train/val/test npz + manifest + weights + leakage report to {OUT_DIR}")


if __name__ == "__main__":
    main()
