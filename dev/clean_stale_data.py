#!/usr/bin/env python3
"""
clean_stale_data.py — Guardian script for CSIR Thunderstorm Pipeline
Runs as the FIRST step in forecast_update.yml (before any fetch scripts).

Scans all critical data files and resets any that are older than MAX_AGE_HOURS
to a clean placeholder with today's UTC timestamp. This prevents stale data
from July/August from ever reaching the commit step.

Usage:
    python clean_stale_data.py
    python clean_stale_data.py --max-age-hours 36   # relax threshold
    python clean_stale_data.py --dry-run             # report without resetting
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

DATA_DIR = Path("data")
MAX_AGE_HOURS = 24          # files older than this are considered stale
IST_OFF = timedelta(hours=5, minutes=30)

STATION_LAT = 13.1979
STATION_LON = 77.7063

# ── Placeholder factories ─────────────────────────────────────────────────────

def now_strings():
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc + IST_OFF
    return (
        now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        now_ist.strftime("%Y-%m-%d %H:%M IST"),
        now_utc,
    )


def placeholder_himawari():
    utc_s, ist_s, _ = now_strings()
    return {
        "timestamp_utc": utc_s,
        "timestamp_ist": ist_s,
        "vobl_bt_celsius": None,
        "min_bt_50km": None,
        "mean_bt_50km": None,
        "cold_pixels_count": 0,
        "storm_detected": False,
        "nearest_pixel_dist_km": None,
        "threshold_celsius": -32.0,
        "data_source": "Himawari-9 — reset by clean_stale_data guardian",
        "bt_trend_1h": None,
    }


def placeholder_pipeline_health():
    utc_s, ist_s, _ = now_strings()
    return {
        "generated_at_utc": utc_s,
        "generated_at_ist": ist_s,
        "pipeline_status": "PENDING",
        "components": {
            "gfs": {"status": "PENDING", "message": "reset by guardian"},
            "himawari": {"status": "PENDING", "message": "reset by guardian"},
            "era5": {"status": "PENDING", "message": "reset by guardian"},
        },
        "data_source": "clean_stale_data guardian",
    }


def placeholder_skill_scores():
    utc_s, ist_s, now_utc = now_strings()
    today = (now_utc + IST_OFF).strftime("%Y-%m-%d")
    return {
        "generated_at": ist_s,
        "date": today,
        "brier_score": None,
        "bss": None,
        "ets": None,
        "pod": None,
        "far": None,
        "csi": None,
        "n_samples": 0,
        "note": "reset by clean_stale_data guardian — will populate after pipeline accumulates data",
    }


def placeholder_verification():
    utc_s, ist_s, now_utc = now_strings()
    # verification covers yesterday
    yesterday = ((now_utc + IST_OFF) - timedelta(days=1)).strftime("%Y-%m-%d")
    return {
        "generated_at": ist_s,
        "verification_date": yesterday,
        "observed_thunderstorm": None,
        "forecast_probability": None,
        "correct": None,
        "note": "reset by clean_stale_data guardian",
    }


def placeholder_multiday():
    """Empty multiday list — gfs_fetcher writes a plain JSON list, not a dict.
    Must return [] so write_multiday_json can merge correctly."""
    return []


# ── File registry ─────────────────────────────────────────────────────────────
# Maps filename → (timestamp_key_in_json, placeholder_factory)
# timestamp_key: the JSON key that holds an ISO timestamp string, or None to
# rely only on filesystem mtime.

FILE_REGISTRY = {
    "himawari_realtime.json": ("timestamp_utc", placeholder_himawari),
    "pipeline_health.json":   ("generated_at_utc", placeholder_pipeline_health),
    "skill_scores.json":      ("generated_at", placeholder_skill_scores),
    "verification_today.json":("generated_at", placeholder_verification),
    "gfs_multiday_43295.json":(None,               placeholder_multiday),  # plain list — use mtime
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_ts(ts_str: str) -> datetime | None:
    """Try to parse an ISO timestamp string to an aware UTC datetime."""
    if not ts_str:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M IST",   # IST strings — treat as IST → subtract offset
        "%Y-%m-%dT%H:%M:%S.%fZ",
    ):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if "IST" in ts_str:
                dt = dt - IST_OFF          # convert IST → UTC
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def file_age_hours(path: Path, ts_key: str | None) -> float:
    """Return age of the file in hours. Uses JSON timestamp if available,
    falls back to filesystem mtime."""
    now_utc = datetime.now(timezone.utc)

    if ts_key and path.exists():
        try:
            data = json.loads(path.read_text())
            ts_str = data.get(ts_key, "")
            dt = _parse_ts(str(ts_str))
            if dt:
                return (now_utc - dt).total_seconds() / 3600
        except Exception:
            pass

    if path.exists():
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return (now_utc - mtime).total_seconds() / 3600

    return float("inf")   # missing = infinitely stale


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Guardian: reset stale data files")
    parser.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report stale files without resetting them")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    stale_found = []
    ok_found = []

    print(f"[guardian] Scanning {data_dir}/ with max_age={args.max_age_hours}h"
          f"  (dry_run={args.dry_run})")

    for filename, (ts_key, factory) in FILE_REGISTRY.items():
        fpath = data_dir / filename
        age_h = file_age_hours(fpath, ts_key)
        exists = fpath.exists()

        if not exists:
            tag = "MISSING"
        elif age_h > args.max_age_hours:
            tag = f"STALE ({age_h:.1f}h old)"
        else:
            tag = f"OK ({age_h:.1f}h old)"
            ok_found.append(filename)
            print(f"[guardian]  ✓ {filename}: {tag}")
            continue

        stale_found.append((filename, tag))
        print(f"[guardian]  ✗ {filename}: {tag}")

        if not args.dry_run:
            placeholder = factory()
            fpath.write_text(json.dumps(placeholder, indent=2))
            ts_display = (placeholder.get(ts_key or 'generated_at_utc', '?')
                          if isinstance(placeholder, dict) else 'empty list')
            print(f"[guardian]    → reset to placeholder: {ts_display}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if not stale_found:
        print("[guardian] All data files are fresh. Pipeline is clean.")
        sys.exit(0)

    action = "would reset" if args.dry_run else "reset"
    print(f"[guardian] {len(stale_found)} stale/missing file(s) {action}:")
    for fname, tag in stale_found:
        print(f"           - {fname}: {tag}")

    if args.dry_run:
        print("[guardian] Dry-run complete. Re-run without --dry-run to apply fixes.")
        # Exit 0 so dry-run doesn't block the workflow
        sys.exit(0)

    print("[guardian] Placeholders written. Downstream fetch scripts will "
          "populate real data this run.")
    sys.exit(0)


if __name__ == "__main__":
    main()
