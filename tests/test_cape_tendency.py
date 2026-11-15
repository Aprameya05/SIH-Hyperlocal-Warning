#!/usr/bin/env python3
"""
Regression test for gfs_row_select.compute_cape_tendency() (Phase 2).

Root cause under test: forecast_action.py used to compute CAPE tendency from
data/gfs_history_43295.json, a file nothing has written since 2026-07-26,
whose "fetched_at" values ("2026-07-26 02:36 IST") are not valid ISO-8601 and
make datetime.fromisoformat() raise on every single run.

This test proves the replacement (compute_cape_tendency, built on the same
gfs_realtime_43295.csv rows used for Phase 1's selection):
  1. computes a real tendency once >=2 valid, distinctly-timed rows exist
     for today, using ONLY the current GFS CSV (no new data source),
  2. never fabricates a value when fewer than 2 valid rows exist yet,
  3. never silently reuses the old JSON file (it is not imported/read here).

Run: python3 test_cape_tendency.py
"""

import sys
import pandas as pd

from gfs_row_select import compute_cape_tendency

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_row(date, gfs_cycle, fetched_at_utc, cape):
    return {
        "date": date, "gfs_cycle": gfs_cycle, "fetched_at": "",
        "fetched_at_utc": fetched_at_utc, "CAPE": cape, "K_INDEX": 35.0,
    }


def test_two_valid_rows_gives_real_tendency():
    print("test_two_valid_rows_gives_real_tendency")
    rows = [
        make_row("2026-09-30", "2026-09-29 06Z f012", "2026-09-29 16:33", cape=190.0),
        make_row("2026-09-30", "2026-09-30 00Z f012", "2026-09-30 12:15", cape=1312.0),
    ]
    df = pd.DataFrame(rows)
    result = compute_cape_tendency(df, date_str="2026-09-30")
    check("available is True", result.available is True)
    check("value is not None", result.value_jkgh is not None)
    # dt = 19h42m = 19.7h; (1312-190)/19.7 ~= 56.9
    check("value is a sane positive number (CAPE increased)",
          result.value_jkgh is not None and result.value_jkgh > 0, result.value_jkgh)
    check("reason cites gfs_realtime_43295.csv-derived rows, not the old JSON file",
          "gfs_realtime_43295.csv" in result.reason, result.reason)


def test_only_one_row_is_unavailable_not_fabricated():
    print("test_only_one_row_is_unavailable_not_fabricated")
    rows = [make_row("2026-09-30", "2026-09-30 00Z f012", "2026-09-30 12:15", cape=1312.0)]
    df = pd.DataFrame(rows)
    result = compute_cape_tendency(df, date_str="2026-09-30")
    check("available is False with only 1 valid row", result.available is False)
    check("value is None (never fabricated)", result.value_jkgh is None)
    check("reason explains insufficient rows", "valid GFS row" in result.reason, result.reason)


def test_no_rows_is_unavailable():
    print("test_no_rows_is_unavailable")
    df = pd.DataFrame(columns=["date", "gfs_cycle", "fetched_at_utc", "CAPE", "K_INDEX"])
    result = compute_cape_tendency(df, date_str="2026-09-30")
    check("available is False", result.available is False)
    check("value is None", result.value_jkgh is None)


def test_invalid_rows_excluded_from_tendency():
    print("test_invalid_rows_excluded_from_tendency")
    rows = [
        make_row("2026-09-30", "2026-09-29 06Z f012", "2026-09-29 16:33", cape=190.0),
        make_row("2026-09-30", "2026-09-30 00Z f012", "2026-09-30 12:15", cape=float("nan")),  # invalid, newest
    ]
    df = pd.DataFrame(rows)
    result = compute_cape_tendency(df, date_str="2026-09-30")
    check("available is False (only 1 valid row survives the NaN drop)", result.available is False)


def test_no_import_of_dead_json_module():
    print("test_no_import_of_dead_json_module")
    # The docstring is allowed to explain history; what must be absent is any
    # actual read of the dead file (open()/json.load() against that path).
    # Read as plain text rather than importing, to avoid pulling in
    # forecast_action.py's heavier module-level dependencies in a unit test.
    with open("forecast_action.py") as f:
        src = f.read()
    check("forecast_action.py no longer opens data/gfs_history_43295.json",
          'DATA / "gfs_history_43295.json"' not in src)
    check("forecast_action.py no longer parses the dead JSON's fetched_at field",
          'gfs_hist[-1].get("fetched_at"' not in src)


if __name__ == "__main__":
    test_two_valid_rows_gives_real_tendency()
    test_only_one_row_is_unavailable_not_fabricated()
    test_no_rows_is_unavailable()
    test_invalid_rows_excluded_from_tendency()
    test_no_import_of_dead_json_module()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed -> {FAILURES}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
