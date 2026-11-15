"""
fetch_metar.py — Pull live METAR from aviationweather.gov for VOBL
Writes metar section into data/forecast.json
No API key required. Run standalone or from GitHub Action.
"""

import requests
import json
import os
import math
from datetime import datetime, timezone

STATIONS   = ["VOBL", "VOBG"]
FORECAST_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forecast.json")


def parse_metar_json(obs: dict) -> dict:
    t  = obs.get("temp")    # °C int
    td = obs.get("dewp")    # °C int

    # RH via Magnus formula
    rh = None
    if t is not None and td is not None:
        try:
            rh = round(
                100.0 * math.exp(17.625 * td / (243.04 + td)) /
                         math.exp(17.625 * t  / (243.04 + t )), 1)
            rh = min(rh, 100.0)
        except Exception:
            rh = None

    # Visibility: statute miles → km
    vis_sm = obs.get("visib")
    vis_km = None
    if vis_sm is not None:
        try:
            vis_km = round(float(str(vis_sm).replace("+", "")) * 1.60934, 1)
        except Exception:
            pass

    # Sky cover layers
    sky_raw = obs.get("clouds", [])
    sky = [{"cover": c.get("cover", ""), "base_ft": c.get("base")}
           for c in (sky_raw if isinstance(sky_raw, list) else [])]

    # Thunderstorm flag
    wx = obs.get("wxString") or obs.get("wx_string") or ""
    ts_present = "TS" in wx.upper()

    return {
        "station":          obs.get("icaoId", "VOBL"),
        "obs_time":         obs.get("reportTime", ""),
        "temp_c":           t,
        "dewpoint_c":       td,
        "rh_pct":           rh,
        "wind_dir":         obs.get("wdir"),
        "wind_speed_kt":    obs.get("wspd"),
        "wind_gust_kt":     obs.get("wgst"),       # None when no gust
        "visibility_sm":    vis_sm,
        "visibility_km":    vis_km,
        "altimeter_hpa":    obs.get("altim"),
        "flight_category":  obs.get("fltCat", ""), # camelCase from API
        "wx_string":        wx,
        "sky_cover":        sky,
        "thunderstorm_present": ts_present,
        "raw":              obs.get("rawOb", ""),
        "fetched_utc":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def fetch_metar() -> dict | None:
    for station in STATIONS:
        url = (f"https://aviationweather.gov/api/data/metar"
               f"?ids={station}&format=json&hours=1")
        try:
            resp = requests.get(url, timeout=15,
                                headers={"User-Agent": "CSIR-Thunderstorm-VOBL/1.0"})
            resp.raise_for_status()
            data = resp.json()
            if data:
                parsed = parse_metar_json(data[0])
                print(f"[METAR] {station} OK | {parsed['raw']}")
                print(f"        T={parsed['temp_c']}°C  RH={parsed['rh_pct']}%  "
                      f"Wind={parsed['wind_dir']}°/{parsed['wind_speed_kt']}kt  "
                      f"Vis={parsed['visibility_km']}km  Cat={parsed['flight_category']}")
                return parsed
            else:
                print(f"[METAR] {station}: empty response, trying fallback")
        except Exception as e:
            print(f"[METAR] {station}: error — {e}")
    return None


def inject_into_forecast(metar_data: dict):
    if not os.path.exists(FORECAST_JSON):
        print(f"[METAR] WARNING: forecast.json not found at {FORECAST_JSON}")
        return
    with open(FORECAST_JSON, "r", encoding="utf-8") as f:
        forecast = json.load(f)
    forecast["metar"] = metar_data
    with open(FORECAST_JSON, "w", encoding="utf-8") as f:
        json.dump(forecast, f, indent=2)
    print("[METAR] Injected into forecast.json ✓")


def inject_ground_truth_label(metar_data: dict):
    """
    Write ts_label_actual column into data/forecast_log.csv for today's rows.
    This gives verify_today.py a real ground-truth label instead of using
    ts_predicted as a self-referential proxy.

    Writes 1 if METAR reports TS in the active slot window, else 0.
    Only updates today's rows that don't yet have a ts_label_actual value.
    """
    import csv, io
    from datetime import datetime, timezone, timedelta

    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")
    ts_observed = int(metar_data.get("thunderstorm_present", False))

    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "forecast_log.csv")
    if not os.path.exists(log_path):
        print("[METAR] forecast_log.csv not found — skipping ground truth injection")
        return

    with open(log_path, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return

    fieldnames = list(rows[0].keys())
    if "ts_label_actual" not in fieldnames:
        fieldnames.append("ts_label_actual")

    updated = 0
    for row in rows:
        if row.get("date") == today_str:
            # Only set if not already labeled
            if not row.get("ts_label_actual"):
                row["ts_label_actual"] = str(ts_observed)
                updated += 1

    with open(log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"[METAR] Ground truth label written: ts_label_actual={ts_observed} "
          f"for {updated} row(s) on {today_str}")


if __name__ == "__main__":
    metar = fetch_metar()
    if metar:
        inject_into_forecast(metar)
        inject_ground_truth_label(metar)
    else:
        print("[METAR] WARNING: No METAR fetched — forecast.json unchanged")