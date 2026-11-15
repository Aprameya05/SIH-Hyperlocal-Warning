"""
backtest_himawari.py
====================
Step 4 — Validate the Himawari-9 BT proxy against historical
thunderstorm labels (LABEL column in bengaluru_thunderstorm_features_merged.csv).

For each labelled date in the dataset, fetches the closest available
Himawari scene from NOAA S3, computes BT min / cold-pixel area /
nearest-cold-pixel distance, then runs AUROC / skill metrics to
confirm the proxy is actually skill-bearing before trusting it.

Output:
  data/backtest/himawari_backtest_results.csv   — per-date BT + label
  data/backtest/himawari_backtest_metrics.json  — AUROC, skill scores
  data/backtest/himawari_backtest_plot.png      — ROC + calibration curve

Usage:
  python backtest_himawari.py

  # Limit to recent years (faster)
  python backtest_himawari.py --start 2021-01-01 --end 2023-12-31

  # Monsoon only
  python backtest_himawari.py --months 6,7,8,9

Notes:
  - Himawari-9 data on S3 starts July 2015; H8 data is in s3://noaa-himawari8
    and is used transparently for dates before 2022-12-13 (H9 operations start).
  - The script fetches one segment-5 file per date (the 13:00 IST scene,
    i.e. 07:30 UTC, peak convective hour). Adjust TARGET_UTC_HOUR to taste.
  - Run time: ~2-4 min per year of data (S3 latency limited).
    Use --dry-run to check CSV parsing without fetching.

Install:
  pip install boto3 requests numpy pandas scikit-learn matplotlib
"""

import os, bz2, sys, json, math, struct, logging, datetime, argparse, traceback
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backtest")

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Try training CSV first, fall back to legacy merged CSV
LABEL_CSV   = Path("data") / "bengaluru_6hr_training_dataset.csv"
LABEL_CSV_LEGACY = Path("bengaluru_thunderstorm_features_merged.csv")
OUT_DIR     = Path("data") / "backtest"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# v6 training expects this merged output file
V6_OUTPUT   = Path("data") / "himawari_backtest.csv"

# One BT scene per slot, sampled at peak convective hour for that slot (UTC)
# Slot 0 (0001-0600 IST) → 02:30 UTC (pre-dawn, low TS → sample at 21:00 UTC prev)
# Slot 1 (0601-1200 IST) → 06:00 UTC (morning)
# Slot 2 (1201-1800 IST) → 07:30 UTC = 13:00 IST (peak)
# Slot 3 (1801-2400 IST) → 13:00 UTC = 18:30 IST (late afternoon)
SLOT_UTC = {
    0: (21, 30),   # 21:30 UTC prev-day ≈ 03:00 IST
    1: ( 0, 30),   # 00:30 UTC ≈ 06:00 IST
    2: ( 7, 30),   # 07:30 UTC ≈ 13:00 IST  ← original single-scene target
    3: (12, 30),   # 12:30 UTC ≈ 18:00 IST
}

# Default (backward-compatible single-scene mode)
TARGET_UTC_HOUR   = 7
TARGET_UTC_MINUTE = 30

# Himawari satellite per date
H8_BUCKET = "noaa-himawari8"
H9_BUCKET = "noaa-himawari9"
H9_START  = datetime.date(2022, 12, 13)   # H9 took over from H8

# Copy constants from fetch_himawari_realtime.py
VOBL_COL     = 2524;  VOBL_ROW    = 4726
VOBL_SEG     = 5;     SEG_ROW_OFF = 4400
ROWS_PER_SEG = 1100;  TOTAL_COLS  = 11000; TOTAL_ROWS = 11000
CROP_COL_MIN = 2515;  CROP_COL_MAX = 2534
CROP_ROW_MIN = 4700;  CROP_ROW_MAX = 4753
CROP_SEG_RMIN = CROP_ROW_MIN - SEG_ROW_OFF
CROP_SEG_RMAX = CROP_ROW_MAX - SEG_ROW_OFF
BT_DEEP    = 233.15;  BT_STRONG  = 243.15;  BT_MOD = 253.15
SAT_LON = 140.7; H_SAT = 35786023.0; R_EQ = 6378137.0; R_POL = 6356752.3142
CFAC = 20466275; LFAC = 20466275; COFF = 5500.5; LOFF = 5500.5
VOBL_LAT = 13.1986; VOBL_LON = 77.7066


# ─────────────────────────────────────────────────────────────────────────────
# COORDINATE HELPERS (copied to avoid circular import)
# ─────────────────────────────────────────────────────────────────────────────

def himawari_to_latlon(col, row):
    sat = math.radians(SAT_LON)
    x   = math.radians((col - COFF) / ((2**-16) * CFAC))
    y   = math.radians((row - LOFF) / ((2**-16) * LFAC))
    sd  = math.sqrt(
        (H_SAT * math.cos(x) * math.cos(y))**2
        - (math.cos(y)**2 + (R_EQ**2 / R_POL**2) * math.sin(y)**2)
        * (H_SAT**2 - R_EQ**2))
    sn  = (H_SAT * math.cos(x) * math.cos(y) - sd) / (
           math.cos(y)**2 + (R_EQ**2 / R_POL**2) * math.sin(y)**2)
    s1  = H_SAT - sn * math.cos(x) * math.cos(y)
    s2  = sn * math.sin(x) * math.cos(y)
    s3  = -sn * math.sin(y)
    sxy = math.sqrt(s1**2 + s2**2)
    lon = math.degrees(math.atan(s2 / s1)) + SAT_LON
    lat = math.degrees(math.atan((R_EQ**2 / R_POL**2) * (s3 / sxy)))
    return lat, lon


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))


# ─────────────────────────────────────────────────────────────────────────────
# HSD PARSER (minimal — just what we need for BT)
# ─────────────────────────────────────────────────────────────────────────────

def parse_hsd_bt(raw_bytes: bytes) -> np.ndarray:
    """Return BT crop array (float32, K) from raw HSD bytes."""
    data   = raw_bytes
    offset = 0

    b1     = struct.unpack_from("<BH H B 16s 16s 4s 2s H d d d I I BBBB 128s",
                                data, offset)
    offset += b1[1]

    b2     = struct.unpack_from("<BH H H H B 40s", data, offset)
    n_cols  = b2[3]
    n_lines = b2[4]
    offset += b2[1]

    for _ in range(2):
        blen = struct.unpack_from("<BH", data, offset)[1]
        offset += blen

    b5     = struct.unpack_from("<BH H d H H H d d", data, offset)
    central_wl  = b5[3]
    count_error = b5[4]
    count_out   = b5[5]
    gain_c2r    = b5[7]
    off_c2r     = b5[8]
    offset     += b5[1]

    ir      = struct.unpack_from("<d d d d d d d d d 40s", data, offset)
    c0      = ir[0]; c1 = ir[1]; c2 = ir[2]
    c_light = ir[6]; h_p = ir[7]; k_b = ir[8]
    offset += struct.calcsize("<d d d d d d d d d 40s")

    for _ in range(6):
        try:
            blen = struct.unpack_from("<BH", data, offset)[1]
            if blen == 0 or blen > 200_000: break
            offset += blen
        except Exception: break

    counts = np.frombuffer(
        data[offset: offset + n_lines * n_cols * 2], dtype="<u2"
    ).reshape(n_lines, n_cols).astype(np.float32)

    bad = (counts == count_error) | (counts == count_out) | (counts == 0)
    rad = counts * gain_c2r + off_c2r
    rad[bad] = np.nan

    cwl_m = central_wl * 1e-6
    with np.errstate(divide="ignore", invalid="ignore"):
        b_val = (2.0 * h_p * c_light**2) / (rad * 1e6 * cwl_m**5) + 1.0
        Te    = (h_p * c_light / k_b) / (cwl_m * np.log(b_val))

    bt = c0 + c1 * Te + c2 * Te**2
    bt[bad] = np.nan
    bt[~np.isfinite(bt)] = np.nan

    # Crop window
    rs = max(0, CROP_SEG_RMIN);  re = min(n_lines, CROP_SEG_RMAX + 1)
    cs = max(0, CROP_COL_MIN);   ce = min(n_cols,  CROP_COL_MAX  + 1)
    return bt[rs:re, cs:ce].astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# S3 FETCH FOR A SPECIFIC DATE/TIME
# ─────────────────────────────────────────────────────────────────────────────

def s3_fetch_scene(target_dt: datetime.datetime) -> np.ndarray | None:
    """
    Fetch the closest available B13 segment-5 scene for target_dt from S3.
    Tries H9 bucket first; falls back to H8 for pre-Dec-2022 dates.
    Returns BT crop array or None.
    """
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config

        bucket = (H9_BUCKET if target_dt.date() >= H9_START else H8_BUCKET)
        sat    = "H09" if bucket == H9_BUCKET else "H08"

        s3 = boto3.client("s3", region_name="us-east-1",
                          config=Config(signature_version=UNSIGNED,
                                        connect_timeout=10,
                                        read_timeout=120))

        # Resolution codes differ by satellite era
        # H8 pre-2022: R10 (2km native), H9: R20 (2km) — try both
        res_codes = ["R20", "R10"] if bucket == H8_BUCKET else ["R20"]

        for offset_min in range(0, 70, 10):   # ±60 min window
            for sign in [1, -1]:
                t = target_dt + datetime.timedelta(minutes=sign * offset_min)
                min_r = (t.minute // 10) * 10
                scene = t.replace(minute=min_r, second=0, microsecond=0)
                folder = scene.strftime("%Y/%m/%d/%H%M")
                for res in res_codes:
                    fname = scene.strftime(
                        f"HS_{sat}_%Y%m%d_%H%M_B13_FLDK_{res}_S{VOBL_SEG:02d}10.DAT.bz2")
                    key = f"AHI-L1b-FLDK/{folder}/{fname}"
                    try:
                        obj  = s3.get_object(Bucket=bucket, Key=key)
                        data = obj["Body"].read()
                        raw  = bz2.decompress(data)
                        bt   = parse_hsd_bt(raw)
                        log.debug(f"    S3 hit: {key}")
                        return bt
                    except Exception:
                        continue

        # Last resort: list the folder and grab any B13 segment-5 file
        try:
            prefix = target_dt.strftime(f"AHI-L1b-FLDK/%Y/%m/%d/")
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=200)
            candidates = [
                o["Key"] for o in resp.get("Contents", [])
                if f"_B13_" in o["Key"] and f"_S{VOBL_SEG:02d}10" in o["Key"]
            ]
            if candidates:
                # Pick closest to target time
                def time_dist(key):
                    try:
                        part = key.split("/")[-1]
                        t_str = part.split("_")[3]  # HHMM
                        h, m = int(t_str[:2]), int(t_str[2:])
                        return abs(h * 60 + m - (target_dt.hour * 60 + target_dt.minute))
                    except Exception:
                        return 9999
                candidates.sort(key=time_dist)
                obj  = s3.get_object(Bucket=bucket, Key=candidates[0])
                data = obj["Body"].read()
                raw  = bz2.decompress(data)
                bt   = parse_hsd_bt(raw)
                log.info(f"    S3 list-hit: {candidates[0]}")
                return bt
        except Exception:
            pass

        return None
    except Exception as e:
        log.warning(f"  S3 error for {target_dt}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE EXTRACTION FROM BT CROP
# ─────────────────────────────────────────────────────────────────────────────

def extract_features(bt: np.ndarray) -> dict:
    """Extract proximity features from a BT crop."""
    PIX_KM2  = 4.0
    n_valid  = int(np.sum(~np.isnan(bt)))
    bt_min   = float(np.nanmin(bt))   if n_valid else np.nan
    bt_p5    = float(np.nanpercentile(bt, 5)) if n_valid else np.nan
    bt_p10   = float(np.nanpercentile(bt, 10)) if n_valid else np.nan
    n_deep   = int(np.sum(bt < BT_DEEP))
    n_strong = int(np.sum(bt < BT_STRONG))
    n_mod    = int(np.sum(bt < BT_MOD))

    # Distance to nearest deep-convective pixel
    nearest_km = np.nan
    cold_px = np.argwhere(bt < BT_DEEP)
    if len(cold_px) > 0:
        dists = []
        for (cr, cc) in cold_px[:300]:
            abs_col = CROP_COL_MIN + cc
            abs_row = SEG_ROW_OFF + CROP_SEG_RMIN + cr
            try:
                lat, lon = himawari_to_latlon(abs_col, abs_row)
                dists.append(haversine_km(VOBL_LAT, VOBL_LON, lat, lon))
            except Exception:
                pass
        if dists:
            nearest_km = min(dists)

    return {
        "bt_min_K":          bt_min,
        "bt_min_C":          bt_min - 273.15 if not np.isnan(bt_min) else np.nan,
        "bt_p5_K":           bt_p5,
        "bt_p10_K":          bt_p10,
        "area_deep_km2":     n_deep  * PIX_KM2,
        "area_strong_km2":   n_strong * PIX_KM2,
        "area_mod_km2":      n_mod   * PIX_KM2,
        "n_pix_deep":        n_deep,
        "n_pix_strong":      n_strong,
        "n_pix_mod":         n_mod,
        "nearest_storm_km":  nearest_km,
        "storm_within_50km": 1 if (not np.isnan(nearest_km) and nearest_km < 50) else 0,
        "valid_pixels":      n_valid,
    }


# ─────────────────────────────────────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(df: pd.DataFrame) -> dict:
    """Compute AUROC and skill scores for each BT feature vs LABEL."""
    from sklearn.metrics import (roc_auc_score, brier_score_loss,
                                 average_precision_score)

    results = {}
    feature_cols = [
        "bt_min_C", "bt_p5_K", "bt_p10_K",
        "area_deep_km2", "n_pix_deep",
        "nearest_storm_km", "storm_within_50km",
    ]

    labels = df["LABEL"].values.astype(int)

    for col in feature_cols:
        valid = df[[col, "LABEL"]].dropna()
        if len(valid) < 20:
            continue
        y     = valid["LABEL"].values.astype(int)
        x     = valid[col].values.astype(float)

        # Invert distance-like features so higher = more storm-like
        if col in ["bt_min_C", "bt_p5_K", "bt_p10_K", "nearest_storm_km"]:
            x_score = -x
        else:
            x_score = x

        try:
            auroc = roc_auc_score(y, x_score)
            ap    = average_precision_score(y, x_score)
            results[col] = {"auroc": round(auroc, 4), "avg_precision": round(ap, 4),
                            "n_samples": len(valid)}
            log.info(f"  {col:<25} AUROC={auroc:.4f}  AP={ap:.4f}  n={len(valid)}")
        except Exception as e:
            log.warning(f"  Metric error for {col}: {e}")

    return results


def compute_roc_plot(df: pd.DataFrame, metrics: dict):
    """Save ROC curves for the top features."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from sklearn.metrics import roc_curve

        top_features = sorted(
            [k for k in metrics if "bt_min_C" in k or "area_deep" in k
             or "nearest_storm" in k],
            key=lambda k: metrics[k]["auroc"], reverse=True)[:4]

        fig, axes = plt.subplots(1, 2, figsize=(13, 5))
        ax_roc, ax_cal = axes

        # ROC curves
        ax_roc.plot([0, 1], [0, 1], "k--", lw=0.8)
        for col in top_features:
            valid = df[[col, "LABEL"]].dropna()
            y     = valid["LABEL"].values.astype(int)
            x     = valid[col].values.astype(float)
            x_s   = -x if col in ["bt_min_C", "bt_p5_K", "nearest_storm_km"] else x
            fpr, tpr, _ = roc_curve(y, x_s)
            auc = metrics[col]["auroc"]
            ax_roc.plot(fpr, tpr, lw=1.8,
                        label=f"{col} (AUC={auc:.3f})")

        ax_roc.set_xlabel("False Positive Rate", fontsize=10)
        ax_roc.set_ylabel("True Positive Rate", fontsize=10)
        ax_roc.set_title("ROC Curves — Himawari BT proxy vs VOBL TS label",
                          fontsize=10)
        ax_roc.legend(fontsize=8)
        ax_roc.grid(alpha=0.3)

        # BT min distribution (label 0 vs 1)
        col = "bt_min_C"
        if col in df.columns:
            d0 = df.loc[df["LABEL"] == 0, col].dropna()
            d1 = df.loc[df["LABEL"] == 1, col].dropna()
            bins = np.linspace(-70, 30, 40)
            ax_cal.hist(d0, bins=bins, alpha=0.5, color="#3498db",
                        density=True, label="No TS (LABEL=0)")
            ax_cal.hist(d1, bins=bins, alpha=0.7, color="#e74c3c",
                        density=True, label="TS (LABEL=1)")
            ax_cal.axvline(-40, ls="--", color="gray", lw=1, label="–40°C threshold")
            ax_cal.set_xlabel("BT min (°C) at 13:00 IST", fontsize=10)
            ax_cal.set_ylabel("Density", fontsize=10)
            ax_cal.set_title("BT min distribution by thunderstorm label", fontsize=10)
            ax_cal.legend(fontsize=8)
            ax_cal.grid(alpha=0.3)

        fig.tight_layout()
        pp = OUT_DIR / "himawari_backtest_plot.png"
        fig.savefig(pp, dpi=130, bbox_inches="tight")
        plt.close()
        log.info(f"  Plot → {pp}")
    except Exception as e:
        log.warning(f"  Plot failed: {e}")
        log.debug(traceback.format_exc())


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",    default="2015-07-01",
                        help="Start date YYYY-MM-DD (Himawari data starts Jul 2015)")
    parser.add_argument("--end",      default="2025-12-31",
                        help="End date YYYY-MM-DD")
    parser.add_argument("--months",   default=None,
                        help="Comma-separated months to include, e.g. 6,7,8,9")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Parse CSV only, skip S3 fetches")
    parser.add_argument("--max-days", type=int, default=9999,
                        help="Max number of days to process (for testing)")
    parser.add_argument("--per-slot", action="store_true",
                        help="Fetch one scene per slot per day (4x slower, enables v6 training)")
    args = parser.parse_args()

    log.info("=" * 65)
    log.info("  Himawari-9 BT Proxy — Historical Backtest  (Step 4)")
    log.info("=" * 65)

    # Load labels — try new training CSV first, fall back to legacy
    csv_path = LABEL_CSV if LABEL_CSV.exists() else LABEL_CSV_LEGACY
    if not csv_path.exists():
        log.error(f"Label CSV not found: tried {LABEL_CSV} and {LABEL_CSV_LEGACY}")
        log.error("Run from the csir-repo root directory.")
        return 1

    df_all = pd.read_csv(csv_path, parse_dates=["date"])
    log.info(f"Loaded {len(df_all)} rows from {csv_path}")

    # Normalise label column name
    if "LABEL" not in df_all.columns and "ts_label" in df_all.columns:
        df_all = df_all.rename(columns={"ts_label": "LABEL"})
    if "LABEL" not in df_all.columns:
        log.error("No LABEL or ts_label column found in CSV")
        return 1

    # For per-slot mode, deduplicate to one row per (date, slot)
    has_slot = "slot" in df_all.columns

    # Filter date range
    start = datetime.datetime.strptime(args.start, "%Y-%m-%d")
    end   = datetime.datetime.strptime(args.end,   "%Y-%m-%d")
    df    = df_all[(df_all["date"] >= start) & (df_all["date"] <= end)].copy()

    if args.months:
        month_list = [int(m) for m in args.months.split(",")]
        df = df[df["date"].dt.month.isin(month_list)]

    log.info(f"Date range : {args.start} → {args.end}")
    log.info(f"Rows after filter: {len(df)}  (LABEL=1: {df['LABEL'].sum()})")

    if args.dry_run:
        log.info("--dry-run: skipping S3 fetches. CSV parsed OK.")
        return 0

    # Fetch BT for each date (and optionally each slot)
    results = []
    done    = 0
    slots_to_fetch = list(SLOT_UTC.keys()) if (args.per_slot and has_slot) else [2]

    unique_dates = df["date"].dt.date.unique()
    log.info(f"Unique dates to process: {len(unique_dates)}")

    for date in unique_dates:
        if done >= args.max_days:
            break

        day_rows = df[df["date"].dt.date == date]

        for slot_id in slots_to_fetch:
            if has_slot:
                slot_rows = day_rows[day_rows["slot"] == slot_id]
                if len(slot_rows) == 0:
                    continue
                label = int(slot_rows["LABEL"].iloc[0])
            else:
                label = int(day_rows["LABEL"].iloc[0])
                slot_id = 2   # legacy single-scene mode

            utc_h, utc_m = SLOT_UTC[slot_id]
            # Slot 0 samples the previous calendar day at 21:30 UTC
            fetch_date = date - datetime.timedelta(days=1) if slot_id == 0 else date
            target_dt  = datetime.datetime(
                fetch_date.year, fetch_date.month, fetch_date.day,
                utc_h, utc_m, 0)

            log.info(f"\n[{done+1}]  {date}  slot={slot_id}  LABEL={label}  "
                     f"target={target_dt.strftime('%Y-%m-%d %H:%M UTC')}")

            bt = s3_fetch_scene(target_dt)
            if bt is None:
                log.warning(f"  No data — skipping")
                results.append({
                    "date": str(date), "slot": slot_id, "LABEL": label, "fetch_ok": 0,
                    **{k: np.nan for k in [
                        "bt_min_K","bt_min_C","bt_p5_K","bt_p10_K",
                        "area_deep_km2","area_strong_km2","area_mod_km2",
                        "n_pix_deep","n_pix_strong","n_pix_mod",
                        "nearest_storm_km","storm_within_50km","valid_pixels"
                    ]}})
            else:
                feats = extract_features(bt)
                nearest = feats.get("nearest_storm_km", float("nan"))
                log.info(f"  BT min={feats['bt_min_C']:.1f}°C  "
                         f"n_deep={feats['n_pix_deep']}  "
                         f"nearest={'%.1f km' % nearest if not np.isnan(nearest) else 'None'}")
                results.append({
                    "date": str(date), "slot": slot_id, "LABEL": label, "fetch_ok": 1,
                    **feats
                })

        done += 1

    # Build results DataFrame
    df_res = pd.DataFrame(results)
    res_csv = OUT_DIR / "himawari_backtest_results.csv"
    df_res.to_csv(res_csv, index=False)
    log.info(f"\nResults saved → {res_csv}")

    # ── v6 training output — rename columns to match train_v6_slot_models.py ──
    v6_rows = df_res[df_res["fetch_ok"] == 1].copy()
    if len(v6_rows) > 0:
        v6_out = v6_rows.rename(columns={
            "bt_min_C":          "min_bt_50km",
            "n_pix_deep":        "cold_pixels_count",
        })
        # vobl_bt_celsius: use bt_p10_K converted to C as VOBL-centre proxy
        if "bt_p10_K" in v6_out.columns:
            v6_out["vobl_bt_celsius"] = v6_out["bt_p10_K"] - 273.15
        # bt_trend_1h: not available from single-scene backtest — set NaN
        v6_out["bt_trend_1h"] = np.nan

        keep = ["date", "slot", "min_bt_50km", "cold_pixels_count",
                "vobl_bt_celsius", "bt_trend_1h"]
        keep = [c for c in keep if c in v6_out.columns]
        v6_out[keep].to_csv(V6_OUTPUT, index=False)
        log.info(f"v6 training CSV → {V6_OUTPUT}  ({len(v6_out)} rows)")

    # Compute metrics
    log.info("\n── Metric computation ──")
    df_valid = df_res[df_res["fetch_ok"] == 1]
    log.info(f"Rows with data: {len(df_valid)} / {len(df_res)}")
    log.info(f"TS days with data: {df_valid['LABEL'].sum()}")

    metrics = compute_metrics(df_valid)

    met_json = OUT_DIR / "himawari_backtest_metrics.json"
    with open(met_json, "w") as f:
        json.dump(metrics, f, indent=2)
    log.info(f"Metrics saved → {met_json}")

    # Plot
    compute_roc_plot(df_valid, metrics)

    # Summary
    log.info("\n── Summary ──")
    if "bt_min_C" in metrics:
        auc = metrics["bt_min_C"]["auroc"]
        log.info(f"  BT min AUROC vs LABEL  : {auc:.4f}")
        if auc > 0.65:
            log.info("  ✓ Proxy has meaningful skill (AUROC > 0.65) — safe to use")
        elif auc > 0.55:
            log.info("  ~ Moderate skill — usable as secondary signal")
        else:
            log.info("  ✗ Weak skill — investigate before using operationally")

    log.info(f"\nAll outputs in: {OUT_DIR.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())