"""
forecast_json_exporter_v2.py
CSIR Thunderstorm Nowcast – Bengaluru Airport (Station 43295)
Exports today's 4-slot forecast + real met parameters to forecast.json
Author: Aprameya (ML Lead)
"""

import json
import os
import pandas as pd
import numpy as np
from datetime import datetime, date
import pytz

# ── CONFIG ──────────────────────────────────────────────────────────────────
BASE_DIR = r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm"

FORECAST_LOG   = os.path.join(BASE_DIR, "data", "forecast_log.csv")
GFS_REALTIME   = os.path.join(BASE_DIR, "data", "gfs_realtime_43295.csv")
UPPER_REALTIME  = os.path.join(BASE_DIR, "data", "upperair_realtime_43295.csv")
HIMAWARI_FILE   = os.path.join(BASE_DIR, "data", "himawari_realtime.json")
HIMAWARI_HIST   = os.path.join(BASE_DIR, "data", "himawari_history.json")
OUTPUT_JSON     = os.path.join(BASE_DIR, "forecast.json")

IST = pytz.timezone("Asia/Kolkata")

SLOT_META = {
    0: {"label": "Late Night", "time": "0001–0600 IST"},
    1: {"label": "Morning",    "time": "0601–1200 IST"},
    2: {"label": "Afternoon",  "time": "1201–1800 IST"},
    3: {"label": "Evening",    "time": "1801–2400 IST"},
}

THRESHOLDS = {0: 0.24, 1: 0.38, 2: 0.16, 3: 0.39}

# ── HELPER ──────────────────────────────────────────────────────────────────
def safe_float(val, decimals=2):
    """Return rounded float or None if NaN/missing."""
    try:
        v = float(val)
        return None if np.isnan(v) else round(v, decimals)
    except Exception:
        return None


def load_forecast_slots(today_str):
    """Read today's 4 slot rows from forecast_log.csv."""
    if not os.path.exists(FORECAST_LOG):
        print(f"  [WARN] forecast_log.csv not found at {FORECAST_LOG}")
        return []

    df = pd.read_csv(FORECAST_LOG, parse_dates=["date"])
    df["date_str"] = df["date"].dt.strftime("%Y-%m-%d")
    today_df = df[df["date_str"] == today_str].copy()

    if today_df.empty:
        print(f"  [WARN] No forecast_log entries for {today_str}")
        return []

    slots = []
    for _, row in today_df.iterrows():
        slot_id = int(row.get("slot", -1))
        if slot_id not in SLOT_META:
            continue
        prob = safe_float(row.get("probability", 0), decimals=3)
        thresh = THRESHOLDS.get(slot_id, 0.30)
        predicted = bool(prob is not None and prob >= thresh)
        slots.append({
            "slot": slot_id,
            "label": SLOT_META[slot_id]["label"],
            "time": SLOT_META[slot_id]["time"],
            "ts_probability": prob,
            "ts_predicted": predicted,
            "threshold": thresh,
        })

    slots.sort(key=lambda x: x["slot"])
    return slots


def load_gfs_met(today_str):
    """Pull latest GFS-derived met params for today."""
    met = {}
    if not os.path.exists(GFS_REALTIME):
        print(f"  [WARN] gfs_realtime_43295.csv not found")
        return met

    df = pd.read_csv(GFS_REALTIME)

    # Try to find a date column
    date_col = None
    for c in ["date", "valid_date", "forecast_date", "DATE"]:
        if c in df.columns:
            date_col = c
            break

    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df["date_str"] = df[date_col].dt.strftime("%Y-%m-%d")
        today_df = df[df["date_str"] == today_str]
        if today_df.empty:
            today_df = df.tail(1)   # fall back to most recent row
    else:
        today_df = df.tail(1)

    if today_df.empty:
        return met

    row = today_df.iloc[-1]   # latest row for today

    # Map known GFS column names → JSON keys
    col_map = {
        "CAPE":             "cape_jkg",
        "cape":             "cape_jkg",
        "CIN":              "cin_jkg",
        "cin":              "cin_jkg",
        "K_INDEX":          "k_index",
        "k_index":          "k_index",
        "LIFTED_INDEX":     "lifted_index",
        "lifted_index":     "lifted_index",
        "TOTALS_TOTALS":    "totals_totals",
        "totals_totals":    "totals_totals",
        "PRECIP_WATER":     "precip_water_mm",
        "precip_water":     "precip_water_mm",
        "ERA5_CAPE":        "era5_cape_jkg",
        "ERA5_T2M":         "era5_t2m_k",
        "ERA5_D2M":         "era5_d2m_k",
        "ERA5_SP":          "era5_sp_pa",
    }

    for src_col, json_key in col_map.items():
        if src_col in row.index:
            val = safe_float(row[src_col])
            if val is not None:
                met[json_key] = val

    return met


def load_upperair_met(today_str):
    """Pull stability indices from Atul's upper-air realtime CSV."""
    met = {}
    if not os.path.exists(UPPER_REALTIME):
        print(f"  [WARN] upperair_realtime_43295.csv not found")
        return met

    df = pd.read_csv(UPPER_REALTIME)

    date_col = None
    for c in ["date", "Date", "DATE", "valid_date"]:
        if c in df.columns:
            date_col = c
            break

    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df["date_str"] = df[date_col].dt.strftime("%Y-%m-%d")
        today_df = df[df["date_str"] == today_str]
        if today_df.empty:
            today_df = df.tail(1)
    else:
        today_df = df.tail(1)

    if today_df.empty:
        return met

    row = today_df.iloc[-1]

    col_map = {
        "CAPE":          "ua_cape_jkg",
        "cape":          "ua_cape_jkg",
        "CIN":           "ua_cin_jkg",
        "cin":           "ua_cin_jkg",
        "K_INDEX":       "ua_k_index",
        "k_index":       "ua_k_index",
        "LIFTED_INDEX":  "ua_lifted_index",
        "lifted_index":  "ua_lifted_index",
        "TOTALS":        "ua_totals_totals",
        "TOTALS_TOTALS": "ua_totals_totals",
        "PW":            "ua_precip_water_mm",
        "PRECIP_WATER":  "ua_precip_water_mm",
    }

    for src_col, json_key in col_map.items():
        if src_col in row.index:
            val = safe_float(row[src_col])
            if val is not None:
                met[json_key] = val

    return met


def build_forecast_json():
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    print(f"\n[forecast_json_exporter_v2] Running for {today_str}")

    # ── 1. Load slots ────────────────────────────────────────────────────────
    slots = load_forecast_slots(today_str)

    if not slots:
        print("  [WARN] No slot data — writing minimal JSON with met params only")
        # Still write met params even if pipeline hasn't run today
        slots = [
            {
                "slot": k,
                "label": SLOT_META[k]["label"],
                "time": SLOT_META[k]["time"],
                "ts_probability": None,
                "ts_predicted": None,
                "threshold": THRESHOLDS[k],
            }
            for k in range(4)
        ]

    # ── 2. Derive summary fields ─────────────────────────────────────────────
    probs = [s["ts_probability"] for s in slots if s["ts_probability"] is not None]
    alert_active = any(s["ts_predicted"] for s in slots if s["ts_predicted"] is not None)
    peak_slot = None
    peak_probability = None

    if probs:
        best = max(slots, key=lambda x: x["ts_probability"] if x["ts_probability"] is not None else -1)
        peak_slot = best["slot"]
        peak_probability = best["ts_probability"]
        # Mark the peak slot
        for s in slots:
            if s["slot"] == peak_slot and peak_probability is not None and peak_probability > 0.10:
                s["primary"] = True

    # ── 3. Load met parameters ───────────────────────────────────────────────
    gfs_met = load_gfs_met(today_str)
    ua_met  = load_upperair_met(today_str)

    # Merge — upper-air takes priority over GFS for same physical quantity
    met_params = {**gfs_met, **ua_met}

    # Derive human-readable instability summary
    instability_level = "Unknown"
    cape = met_params.get("ua_cape_jkg") or met_params.get("cape_jkg")
    ki   = met_params.get("ua_k_index")  or met_params.get("k_index")

    if cape is not None:
        if cape < 100:
            instability_level = "Stable"
        elif cape < 500:
            instability_level = "Marginal"
        elif cape < 1500:
            instability_level = "Moderate"
        elif cape < 3000:
            instability_level = "High"
        else:
            instability_level = "Extreme"

    # ── 4. Assemble JSON ─────────────────────────────────────────────────────
    output = {
        "date": today_str,
        "generated_at": now_ist.strftime("%Y-%m-%d %H:%M IST"),
        "station": "Bengaluru Airport (VOBL / IMD 43295)",
        "model_version": "v3_calibrated",
        "slots": slots,
        "alert_active": alert_active,
        "peak_slot": peak_slot,
        "peak_probability": peak_probability,
        "met_parameters": {
            **met_params,
            "instability_level": instability_level,
        },
        "data_sources": {
            "gfs_available":        os.path.exists(GFS_REALTIME),
            "upperair_available":   os.path.exists(UPPER_REALTIME),
            "forecast_log_available": os.path.exists(FORECAST_LOG),
        },
    }

    # ── 5. Load Himawari satellite signal ────────────────────────────────────
    himawari = {}
    if os.path.exists(HIMAWARI_FILE):
        try:
            with open(HIMAWARI_FILE) as f:
                himawari = json.load(f)
            print(f"  ✓ Himawari loaded: storm_detected={himawari.get('storm_detected')} min_bt={himawari.get('min_bt_50km')}°C")
        except Exception as e:
            print(f"  [WARN] Himawari load error: {e}")

    himawari_history = []
    if os.path.exists(HIMAWARI_HIST):
        try:
            with open(HIMAWARI_HIST) as f:
                himawari_history = json.load(f)
        except Exception:
            pass

    # ── 6. Assemble and write ─────────────────────────────────────────────────
    output["satellite"] = {
        "himawari9": {
            "timestamp_utc":         himawari.get("timestamp_utc"),
            "timestamp_ist":         himawari.get("timestamp_ist"),
            "vobl_bt_celsius":       himawari.get("vobl_bt_celsius"),
            "min_bt_50km":           himawari.get("min_bt_50km"),
            "mean_bt_50km":          himawari.get("mean_bt_50km"),
            "cold_pixels_count":     himawari.get("cold_pixels_count", 0),
            "storm_detected":        himawari.get("storm_detected", False),
            "nearest_pixel_dist_km": himawari.get("nearest_pixel_dist_km"),
            "threshold_celsius":     himawari.get("threshold_celsius", -40.0),
            "data_source":           himawari.get("data_source", "Himawari-9 Band 13 (10.4um) via NOAA AWS S3"),
            "available":             bool(himawari),
        },
        "history": himawari_history[-6:] if himawari_history else [],
    }

    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  ✓ forecast.json written → {OUTPUT_JSON}")
    print(f"  ✓ Alert active: {alert_active}")
    print(f"  ✓ Peak slot: {peak_slot} | Peak prob: {peak_probability}")
    print(f"  ✓ Met params included: {list(met_params.keys())}")
    return output


if __name__ == "__main__":
    build_forecast_json()