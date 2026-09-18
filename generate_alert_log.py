#!/usr/bin/env python3
"""
generate_alert_log.py
Appends today's nowcast outcome to alert_log.json after verification.
Run by GitHub Actions after forecast_action.py completes.

Usage:
    python generate_alert_log.py
    python generate_alert_log.py --observed  # marks today's slot as TS observed
    python generate_alert_log.py --no-storm  # marks today's slot as no TS
"""

import json
import os
import sys
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ALERT_LOG_PATH = Path("alert_log.json")
FORECAST_PATH = Path("forecast.json")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[alert_log] Written: {path}")


def get_today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def get_issued_ist() -> str:
    """Alert is always issued at 09:40 IST when forecast_action runs."""
    today = datetime.now(IST).replace(hour=9, minute=40, second=0, microsecond=0)
    return today.strftime("%Y-%m-%d %H:%M")


def lead_time_min(issued_ist: str, observed_ist: str) -> int | None:
    if not observed_ist:
        return None
    fmt = "%Y-%m-%d %H:%M"
    t0 = datetime.strptime(issued_ist, fmt)
    t1 = datetime.strptime(observed_ist, fmt)
    delta = (t1 - t0).total_seconds() / 60
    return int(delta) if delta > 0 else None


def build_event(
    forecast: dict,
    thunderstorm_observed: bool | None,
    observed_time_ist: str | None,
) -> dict:
    """Construct a single event record from today's forecast.json data."""
    now_ist = get_today_ist()
    issued = get_issued_ist()

    # Primary slot (usually slot 2 — afternoon)
    peak_slot_idx = forecast.get("peak_slot", 2)
    slots = forecast.get("slots", [])
    slot = next((s for s in slots if s.get("slot") == peak_slot_idx), slots[0] if slots else {})
    slot_labels = {0: "Late Night", 1: "Morning", 2: "Afternoon", 3: "Evening"}

    model_prob = slot.get("ts_probability", 0.0)
    threshold = slot.get("threshold", 0.15)
    alert_active = model_prob >= threshold

    # Satellite data for context
    sat = forecast.get("satellite", {}).get("himawari9", {})
    peak_bt = sat.get("min_bt_50km")
    cold_pixels = sat.get("cold_pixels_count", 0)

    # Determine outcome
    if thunderstorm_observed is None:
        outcome = "PENDING"
    elif thunderstorm_observed and alert_active:
        outcome = "HIT"
    elif thunderstorm_observed and not alert_active:
        outcome = "MISS"
    elif not thunderstorm_observed and alert_active:
        outcome = "FALSE ALARM"
    else:
        outcome = "CORRECT REJECTION"

    lead = lead_time_min(issued, observed_time_ist) if thunderstorm_observed else None

    return {
        "date": now_ist,
        "slot": peak_slot_idx,
        "slot_label": slot_labels.get(peak_slot_idx, "Afternoon"),
        "alert_issued_ist": issued,
        "event_first_observed_ist": observed_time_ist,
        "lead_time_min": lead,
        "model_prob": round(model_prob, 4),
        "threshold_used": round(threshold, 4),
        "thunderstorm_observed": thunderstorm_observed,
        "verified": thunderstorm_observed is not None,
        "outcome": outcome,
        "peak_bt_celsius": peak_bt,
        "cold_pixels_count": cold_pixels,
        "notes": f"Auto-appended by generate_alert_log.py. Model: {slot.get('model_used', 'unknown')}",
    }


def recompute_summary(events: list) -> dict:
    """Recompute 30-day rolling summary statistics."""
    now_ist = datetime.now(IST)
    cutoff = now_ist - timedelta(days=30)

    recent = [
        e for e in events
        if e.get("verified", False) and
        datetime.strptime(e["date"], "%Y-%m-%d").replace(tzinfo=IST) >= cutoff
    ]

    hits = sum(1 for e in recent if e["outcome"] == "HIT")
    misses = sum(1 for e in recent if e["outcome"] == "MISS")
    false_alarms = sum(1 for e in recent if e["outcome"] == "FALSE ALARM")
    correct_rejections = sum(1 for e in recent if e["outcome"] == "CORRECT REJECTION")

    n_ts_obs = hits + misses
    n_alert = hits + false_alarms

    pod = round(hits / n_ts_obs, 3) if n_ts_obs > 0 else 0.0
    far = round(false_alarms / n_alert, 3) if n_alert > 0 else 0.0
    denom_csi = hits + misses + false_alarms
    csi = round(hits / denom_csi, 3) if denom_csi > 0 else 0.0

    # Heidke Skill Score
    n = len(recent)
    expected = ((hits + false_alarms) * (hits + misses) + (correct_rejections + false_alarms) * (correct_rejections + misses)) / n if n > 0 else 0
    hss = round((hits + correct_rejections - expected) / (n - expected), 3) if n > expected else 0.0

    lead_times = [e["lead_time_min"] for e in recent if e.get("lead_time_min") is not None]
    mean_lead = round(sum(lead_times) / len(lead_times)) if lead_times else None
    mean_lead_hr = round(mean_lead / 60, 1) if mean_lead else None

    return {
        "n_events": len(recent),
        "n_hits": hits,
        "n_misses": misses,
        "n_false_alarms": false_alarms,
        "n_correct_rejections": correct_rejections,
        "pod": pod,
        "far": far,
        "csi": csi,
        "hss": hss,
        "mean_lead_time_min": mean_lead,
        "mean_lead_time_hr": mean_lead_hr,
        "note": "Computed from verified events in alert_log.json only (slot-2, 30-day rolling window).",
    }


def main():
    parser = argparse.ArgumentParser(description="Append today's alert outcome to alert_log.json")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--observed", metavar="HH:MM",
                       help="Mark today as TS observed at this IST time (e.g. 14:55)")
    group.add_argument("--no-storm", action="store_true",
                       help="Mark today as no thunderstorm observed")
    parser.add_argument("--pending", action="store_true",
                        help="Append as pending (default when no storm info available yet)")
    args = parser.parse_args()

    forecast = load_json(FORECAST_PATH)
    if not forecast:
        print("[alert_log] ERROR: forecast.json not found or empty. Skipping.")
        sys.exit(1)

    log = load_json(ALERT_LOG_PATH)
    if not log:
        log = {
            "station": "VOBL/BLR",
            "station_id": "43295",
            "description": "Nowcast alert log — verified thunderstorm event history with issued lead times",
            "last_updated": get_today_ist(),
            "events": [],
            "summary_30d": {},
        }

    # Check if today already has an entry
    today = get_today_ist()
    existing_idx = next(
        (i for i, e in enumerate(log["events"]) if e.get("date") == today), None
    )

    # Determine storm observation status
    if args.observed:
        observed = True
        observed_time = f"{today} {args.observed}"
    elif args.no_storm:
        observed = False
        observed_time = None
    else:
        observed = None  # Pending
        observed_time = None

    event = build_event(forecast, observed, observed_time)

    if existing_idx is not None:
        # Update existing pending entry
        print(f"[alert_log] Updating existing entry for {today}")
        log["events"][existing_idx] = event
    else:
        # Prepend new entry (newest first)
        log["events"].insert(0, event)
        # Keep only last 90 days
        log["events"] = log["events"][:90]

    # Recompute summary
    log["summary_30d"] = recompute_summary(log["events"])
    log["last_updated"] = today

    save_json(ALERT_LOG_PATH, log)
    print(f"[alert_log] Today's outcome: {event['outcome']} (prob={event['model_prob']:.3f}, lead={event['lead_time_min']} min)")


if __name__ == "__main__":
    main()
