#!/usr/bin/env python3
"""Regression tests for lead_time.py. Run: python3 test_lead_time.py"""

import sys
from datetime import datetime, timezone
import pandas as pd

from lead_time import compute_lead_time, SLOT_WINDOWS_IST

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def test_normal_forecast():
    print("test_normal_forecast")
    # Slot 2 window = 12:01-18:00 IST on 2026-09-30 -> 06:31-12:30 UTC.
    # GFS fetched at 04:30 UTC same day -> should be ~2.02h before window open.
    ref = pd.Timestamp("2026-09-30 04:30:00")
    lt = compute_lead_time("2026-09-30", 2, ref, gfs_cycle="2026-09-30 00Z f006", gfs_fhour=6)
    check("available", lt.available)
    check("hours is positive and in a sane 0-12h band", lt.hours is not None and 0 < lt.hours < 12, lt.hours)
    check("not clipped", lt.clipped is False)
    check("valid_from is UTC ISO", lt.valid_from.endswith("+00:00"), lt.valid_from)
    check("source_cycle preserved", lt.source_cycle == "2026-09-30 00Z f006")


def test_slot_boundary():
    print("test_slot_boundary")
    # reference_time exactly at slot 1 window start (06:01 IST == 00:31 UTC)
    ref = pd.Timestamp("2026-09-30 00:31:00")
    lt = compute_lead_time("2026-09-30", 1, ref)
    check("available", lt.available)
    check("lead hours ~= 0 at the exact boundary", abs(lt.hours) < 0.02, lt.hours)


def test_stale_input_clips_to_zero():
    print("test_stale_input_clips_to_zero")
    # reference_time well AFTER the slot window closed (stale/late run).
    ref = pd.Timestamp("2026-10-01 05:00:00")
    lt = compute_lead_time("2026-09-30", 0, ref)
    check("available", lt.available)
    check("clipped True", lt.clipped is True)
    check("hours is 0.0, never negative", lt.hours == 0.0, lt.hours)


def test_malformed_reference_time():
    print("test_malformed_reference_time")
    lt = compute_lead_time("2026-09-30", 1, "not-a-timestamp")
    check("not available on malformed reference_time", lt.available is False)
    check("valid_from/valid_to still computed (window is independent of reference_time)",
          lt.valid_from is not None and lt.valid_to is not None)
    check("reason mentions malformed", "malformed" in lt.reason)


def test_missing_reference_time():
    print("test_missing_reference_time")
    lt = compute_lead_time("2026-09-30", 3, None)
    check("not available when reference_time is None", lt.available is False)
    check("hours is None, never fabricated", lt.hours is None)


def test_unknown_slot():
    print("test_unknown_slot")
    lt = compute_lead_time("2026-09-30", 7, pd.Timestamp("2026-09-30 00:00:00"))
    check("not available for unknown slot id", lt.available is False)


def test_all_four_slot_windows_are_6_hours():
    print("test_all_four_slot_windows_are_6_hours")
    for slot, (h0, m0, h1, m1) in SLOT_WINDOWS_IST.items():
        span_min = (h1 * 60 + m1) - (h0 * 60 + m0)
        check(f"slot {slot} spans ~6h (359-360 min)", 358 <= span_min <= 360, span_min)


if __name__ == "__main__":
    test_normal_forecast()
    test_slot_boundary()
    test_stale_input_clips_to_zero()
    test_malformed_reference_time()
    test_missing_reference_time()
    test_unknown_slot()
    test_all_four_slot_windows_are_6_hours()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed -> {FAILURES}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
