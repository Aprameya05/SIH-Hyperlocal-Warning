#!/usr/bin/env python3
"""Replaces the old 'Push alert notification' curl step. Evaluates forecast.json through the alert policy and
prints/records WHY automated alerts are withheld. Sends nothing (production registries are empty, decision Q18)."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import alert_policy  # noqa: E402


def main():
    fc = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "forecast.json"))
    res = alert_policy.evaluate_forecast_alerts(fc)
    msg = (f"Automated VOBL alert push WITHHELD. forecast.json alert_active={res['alert_active_in_file']}; "
           f"eligible slots: {res['eligible_slots']}; reasons: {res['reason_counts']}. "
           f"{alert_policy.explain(sorted(res['reason_counts']))}")
    print(msg)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("### Automated alert push\n" + msg + "\n")
    # nothing is ever POSTed to ntfy/WhatsApp from this guard; an eligible alert would be sent by an approved sender


if __name__ == "__main__":
    main()
