#!/usr/bin/env python3
"""Fail-closed gate for forecast.json (VOBL station): climatology/defaulted/fallback slots can never drive
alert_active or peak_probability. Prints which slots are operational and which are withheld.
Usage: ci_validate_vobl_forecast.py [PATH]"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import forecast_integrity as fi  # noqa: E402


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "forecast.json")
    try:
        fc = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        print(f"NOT OK: {path} unreadable ({exc})")
        sys.exit(1)
    rep = fi.verify_vobl_forecast(fc)
    print(f"VOBL forecast: operational slots {rep['eligible_slots']}, withheld slots {rep['withheld_slots']}, "
          f"operational lead: {rep['operational_lead_status']}")
    if not rep["ok"]:
        for x in rep["violations"]:
            print(f"   - {x['code']}: {x['detail']}")
        sys.exit(1)
    print("OK: forecast.json satisfies the VOBL contract")


if __name__ == "__main__":
    main()
