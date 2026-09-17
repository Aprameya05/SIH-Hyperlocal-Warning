#!/usr/bin/env python3
"""
Standalone Alert Dispatcher -- runs in GitHub Actions after pipeline.py
Reads pan_india_grid.json, finds HIGH+ hazard cells, sends SMS via Twilio.

Environment variables (set as GitHub Secrets):
  TWILIO_ACCOUNT_SID    Twilio account SID
  TWILIO_AUTH_TOKEN     Twilio auth token
  TWILIO_FROM_NUMBER    Twilio phone number e.g. +15555550100
  ALERT_RECIPIENTS      comma-separated E.164 numbers e.g. +919876543210,+919876543211

No server required -- runs to completion and exits.
"""

import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

DATA_FILE = Path(__file__).parent.parent / "data" / "pan_india_grid.json"

TWILIO_SID   = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM  = os.environ.get("TWILIO_FROM_NUMBER", "")
RECIPIENTS   = [r.strip() for r in os.environ.get("ALERT_RECIPIENTS", "").split(",") if r.strip()]

# Only alert if at least one cell crosses these thresholds
TS_ALERT_THRESHOLD = 0.35   # MODERATE or above
CB_ALERT_THRESHOLD = 0.35
FF_ALERT_THRESHOLD = 0.35


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def send_sms(to: str, body: str) -> tuple[bool, str]:
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
        return False, "Twilio credentials not set"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
    payload = urllib.parse.urlencode({"From": TWILIO_FROM, "To": to, "Body": body}).encode()
    creds = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return bool(result.get("sid")), result.get("message", "")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read()).get("message", str(e))
        except Exception:
            err = str(e)
        return False, err
    except Exception as e:
        return False, str(e)


def severity_label(prob: float) -> str:
    if prob >= 0.55:
        return "HIGH"
    if prob >= 0.35:
        return "MODERATE"
    if prob >= 0.15:
        return "LOW"
    return "MINIMAL"


def build_message(grid: dict, hot_cells: list) -> str:
    cycle = grid.get("gfs_cycle", "unknown cycle")
    generated = grid.get("generated_at_utc", "")[:16].replace("T", " ") + " UTC"

    max_ts = max(c["thunderstorm_probability"] for c in hot_cells)
    max_cb = max(c["cloudburst_probability"] for c in hot_cells)
    max_ff = max(c["flash_flood_probability"] for c in hot_cells)

    peak_cell = max(hot_cells, key=lambda c: c["thunderstorm_probability"])

    lines = [
        f"[HYPERLOCAL WEATHER ALERT] {generated}",
        f"GFS cycle: {cycle}",
        f"Active hazard cells: {len(hot_cells)}",
        f"Peak location: {peak_cell['lat']}N {peak_cell['lon']}E",
        f"TS: {round(max_ts*100)}% ({severity_label(max_ts)})  "
        f"CB: {round(max_cb*100)}%  FF: {round(max_ff*100)}%",
        "Monitor conditions. Take precautions.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE} not found -- run pipeline.py first")
        sys.exit(1)

    grid = json.loads(DATA_FILE.read_text())
    cells = grid.get("grid_cells", [])

    hot_cells = [
        c for c in cells
        if (c.get("thunderstorm_probability", 0) >= TS_ALERT_THRESHOLD or
            c.get("cloudburst_probability", 0)   >= CB_ALERT_THRESHOLD or
            c.get("flash_flood_probability", 0)  >= FF_ALERT_THRESHOLD)
    ]

    print(f"Grid: {len(cells)} cells  |  Hot cells (>=MODERATE): {len(hot_cells)}")

    if not hot_cells:
        print("No hazard cells above threshold -- no alert dispatched.")
        return

    if not RECIPIENTS:
        print("WARNING: ALERT_RECIPIENTS not set -- SMS skipped.")
        print("Hot cells found but no recipients configured.")
        return

    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
        print("WARNING: Twilio credentials not set -- SMS skipped.")
        return

    message = build_message(grid, hot_cells)
    print(f"\nAlert message:\n{message}\n")

    success = 0
    for phone in RECIPIENTS:
        ok, err = send_sms(phone, message)
        if ok:
            print(f"  SMS sent to {phone}")
            success += 1
        else:
            print(f"  SMS FAILED to {phone}: {err}")

    print(f"\nDispatched {success}/{len(RECIPIENTS)} SMS alerts.")


if __name__ == "__main__":
    main()
