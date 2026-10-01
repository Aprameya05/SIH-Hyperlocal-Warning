#!/usr/bin/env python3
"""
Regression test for gfs_row_select.select_latest_gfs().

Root cause under test (2026-09-30 root-cause report, Phase 1): both
forecast_action.py and compute_realtime_shap.py used to take
gfs_df.iloc[0] after filtering to today's date, which returns whichever
row was physically first in data/gfs_realtime_43295.csv -- not the most
recently fetched row, since gfs_fetcher.py appends and never re-sorts.

This test builds a small in-memory frame with an old row, a newer row,
and the newest row, deliberately NOT in chronological order, and asserts
that select_latest_gfs() picks the newest valid row regardless of that
order -- i.e. it proves the selection is order-independent, not merely
"iloc[-1] happens to work on today's data".

Run: python3 test_gfs_row_select.py
"""

import sys
import pandas as pd

from gfs_row_select import select_latest_gfs, latest_gfs_frame

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_row(date, gfs_cycle, fetched_at_utc, cape=500.0, k_index=35.0, slot=0):
    return {
        "date": date,
        "gfs_cycle": gfs_cycle,
        "fetched_at": "",
        "slot": slot,
        "fetched_at_utc": fetched_at_utc,
        "CAPE": cape,
        "K_INDEX": k_index,
        "TOTALS_TOTALS": 44.0,
        "LIFTED_INDEX": -1.0,
        "ERA5_T2M": 300.0,
    }


def test_out_of_order_rows_pick_newest():
    print("test_out_of_order_rows_pick_newest")
    # Deliberately scrambled order: newest row physically FIRST in the file
    # would make a naive iloc[0]-after-append-order fix look correct by
    # accident. Put it in the middle instead so only real timestamp-based
    # selection can pass.
    rows = [
        make_row("2026-09-30", "2026-09-29 18Z f012", "2026-09-30 02:24", cape=808.0, slot=2),   # newer
        make_row("2026-09-30", "2026-09-30 00Z f012", "2026-09-30 12:15", cape=1312.0, slot=3),  # newest
        make_row("2026-09-30", "2026-09-29 06Z f012", "2026-09-29 16:33", cape=190.0, slot=0),   # oldest (physically first in real file)
        make_row("2026-09-30", "2026-09-29 12Z f012", "2026-09-29 21:32", cape=237.0, slot=1),   # older
    ]
    df = pd.DataFrame(rows)
    sel = select_latest_gfs(df, date_str="2026-09-30")
    check("selection is not None", sel is not None)
    check("selected gfs_cycle is the newest cycle", sel.gfs_cycle == "2026-09-30 00Z f012", sel.gfs_cycle)
    check("selected fhour parsed correctly", sel.gfs_fhour == 12, sel.gfs_fhour)
    check("selected fetched_at_utc is the newest timestamp",
          str(sel.fetched_at_utc) == "2026-09-30 12:15:00", str(sel.fetched_at_utc))
    check("selected row CAPE matches the newest row, not row 0",
          float(sel.row["CAPE"]) == 1312.0, sel.row["CAPE"])
    check("n_candidates counts all 4 rows for the date", sel.n_candidates == 4, sel.n_candidates)
    check("n_valid counts all 4 as valid", sel.n_valid == 4, sel.n_valid)

    # latest_gfs_frame() must expose the same row at position 0 for the
    # `.iloc[0]` call sites in forecast_action.py.
    frame = latest_gfs_frame(df, date_str="2026-09-30")
    check("latest_gfs_frame has exactly 1 row", len(frame) == 1, len(frame))
    check("latest_gfs_frame.iloc[0] is the newest row",
          float(frame.iloc[0]["CAPE"]) == 1312.0, frame.iloc[0]["CAPE"])


def test_invalid_rows_are_skipped():
    print("test_invalid_rows_are_skipped")
    rows = [
        make_row("2026-09-30", "2026-09-30 00Z f012", "2026-09-30 12:15", cape=float("nan"), slot=3),  # newest but invalid (NaN CAPE)
        make_row("2026-09-30", "2026-09-29 18Z f012", "2026-09-30 02:24", cape=808.0, slot=2),          # valid, older
    ]
    df = pd.DataFrame(rows)
    sel = select_latest_gfs(df, date_str="2026-09-30")
    check("selection is not None", sel is not None)
    check("invalid newest row is skipped in favour of the valid older row",
          float(sel.row["CAPE"]) == 808.0, sel.row["CAPE"] if sel else None)
    check("n_candidates is 2", sel.n_candidates == 2, sel.n_candidates)
    check("n_valid is 1 (one row dropped for NaN CAPE)", sel.n_valid == 1, sel.n_valid)


def test_date_filter_excludes_other_days():
    print("test_date_filter_excludes_other_days")
    rows = [
        make_row("2026-09-29", "2026-09-29 12Z f012", "2026-09-29 20:00", cape=999.0, slot=1),  # yesterday — must be excluded
        make_row("2026-09-30", "2026-09-29 18Z f012", "2026-09-30 02:24", cape=808.0, slot=2),  # today
    ]
    df = pd.DataFrame(rows)
    sel = select_latest_gfs(df, date_str="2026-09-30")
    check("selection is not None", sel is not None)
    check("selected row is today's row, not yesterday's higher-CAPE row",
          float(sel.row["CAPE"]) == 808.0, sel.row["CAPE"])


def test_empty_and_all_invalid_return_none():
    print("test_empty_and_all_invalid_return_none")
    empty_df = pd.DataFrame(columns=["date", "gfs_cycle", "fetched_at_utc", "CAPE", "K_INDEX"])
    check("empty dataframe returns None", select_latest_gfs(empty_df, date_str="2026-09-30") is None)

    rows = [make_row("2026-09-30", "2026-09-30 00Z f012", "2026-09-30 12:15", cape=float("nan"))]
    df = pd.DataFrame(rows)
    check("all-invalid dataframe returns None", select_latest_gfs(df, date_str="2026-09-30") is None)


if __name__ == "__main__":
    test_out_of_order_rows_pick_newest()
    test_invalid_rows_are_skipped()
    test_date_filter_excludes_other_days()
    test_empty_and_all_invalid_return_none()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed -> {FAILURES}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
