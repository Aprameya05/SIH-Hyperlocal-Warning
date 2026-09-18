#!/usr/bin/env python3
"""
clean_stale_data.py
Removes per-run data files that are older than STALE_HOURS so the pipeline
always fetches fresh values rather than replaying stale intermediates.

Run as the very first step in the GitHub Actions forecast workflow.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STALE_HOURS = 12

# Files that must be regenerated every run -- delete if too old
STALE_CANDIDATES = [
    "data/gfs_realtime_43295.csv",
    "data/upperair_realtime_43295.csv",
    "data/himawari_realtime.json",
    "data/verification_today.json",
    "data/realtime_shap.json",
    "data/pipeline_health.json",
]


def age_hours(path: Path) -> float | None:
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime).total_seconds() / 3600


def main() -> None:
    removed: list[str] = []
    for rel in STALE_CANDIDATES:
        p = Path(rel)
        h = age_hours(p)
        if h is not None and h > STALE_HOURS:
            p.unlink(missing_ok=True)
            removed.append(f"{rel} ({h:.1f}h old)")

    if removed:
        print("Removed stale data files:")
        for r in removed:
            print(f"  {r}")
    else:
        print("No stale data files found.")

    # Check forecast.json age -- just warn, do not delete (pipeline overwrites it)
    forecast = Path("forecast.json")
    h = age_hours(forecast)
    if h is None:
        print("forecast.json not present -- pipeline will create it.")
    elif h > STALE_HOURS:
        print(f"WARNING: forecast.json is {h:.1f}h old -- pipeline will overwrite it.")
    else:
        try:
            with forecast.open() as fh:
                d = json.load(fh)
            gen = d.get("generated_at_utc", d.get("generated_at", ""))
            print(f"forecast.json age: {h:.1f}h  generated_at: {gen}")
        except Exception:
            print(f"forecast.json age: {h:.1f}h (could not read JSON)")


if __name__ == "__main__":
    main()
    sys.exit(0)
