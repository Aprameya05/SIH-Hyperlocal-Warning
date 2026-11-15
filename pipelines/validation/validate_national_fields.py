#!/usr/bin/env python3
"""Fail-closed gate for the national location artifacts. Independently re-verifies data/india_fields_latest.json against the
grid (recomputes valid times, publication-time plausibility, accumulation windows, physical ranges, grid hash).
Usage: pipelines/validation/validate_national_fields.py [data_dir] [--max-age-hours H]"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.location_service import Store, parse_utc  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", nargs="?", default=str(ROOT / "data"))
    ap.add_argument("--max-age-hours", type=float)
    a = ap.parse_args()
    s = Store(Path(a.data_dir))
    bad = []
    if not s.cells:
        bad.append("grid missing")
    elif not 4000 <= len(s.cells) <= 5500:
        bad.append(f"unexpected cell count {len(s.cells)}")
    if not s.static_doc or len(s.static_doc["cells"]) != len(s.cells):
        bad.append("static layer missing or misaligned")
    if not s.fields_ok:
        bad += s.fields_problems
    elif a.max_age_hours is not None:
        age = (datetime.now(timezone.utc) - parse_utc(s.fields_doc["meta"]["cycle_init_utc"])).total_seconds() / 3600
        if age > a.max_age_hours:
            bad.append(f"fields cycle is {age:.1f} h old (> {a.max_age_hours}): not regenerated")
    if bad:
        print("NOT OK:", *bad, sep="\n  - ")
        sys.exit(1)
    m = s.fields_doc["meta"]
    print(f"OK: {len(s.cells)} cells, cycle {m['cycle_init_utc']}, hours {m['fhours']}, all provenance re-derived")


if __name__ == "__main__":
    main()
