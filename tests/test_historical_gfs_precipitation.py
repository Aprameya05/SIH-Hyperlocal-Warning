#!/usr/bin/env python3
"""Phase 0.4.5 focused tests for scripts/historical_gfs_precipitation.py
(research-only interval-precipitation reconstruction via GFS accumulation
differencing). Does not touch production qpe_mm."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from historical_gfs_precipitation import (  # noqa: E402
    compute_interval_precipitation, verify_same_initialization_cycle, FEATURE_NAME,
)

COMMON = dict(
    init_time="20200715 0000",
    start_valid_time="20200715 0300",
    end_valid_time="20200715 0600",
    lead_start_hours=3,
    lead_end_hours=6,
    start_step=0,
    end_start_step=0,
)


def test_normal_accumulation_difference():
    res = compute_interval_precipitation(tp_start_mm=5.0, tp_end_mm=12.0, **COMMON)
    assert res.available
    assert not res.rejected
    assert res.value_mm == 7.0
    assert res.feature_name == FEATURE_NAME


def test_equal_accumulation_yields_zero():
    # No rain fell in the interval -- both accumulations identical.
    res = compute_interval_precipitation(tp_start_mm=3.2, tp_end_mm=3.2, **COMMON)
    assert res.available
    assert res.value_mm == 0.0


def test_tiny_floating_point_negative_is_floored_and_flagged():
    res = compute_interval_precipitation(tp_start_mm=3.2000001, tp_end_mm=3.2, **COMMON)
    assert res.available
    assert not res.rejected
    assert res.value_mm == 0.0
    assert "floored" in res.rejection_reason


def test_physically_impossible_negative_is_rejected():
    res = compute_interval_precipitation(tp_start_mm=10.0, tp_end_mm=2.0, **COMMON)
    assert not res.available
    assert res.rejected
    assert "physically impossible" in res.rejection_reason


def test_missing_start_value():
    res = compute_interval_precipitation(tp_start_mm=None, tp_end_mm=5.0, **COMMON)
    assert not res.available
    assert res.rejected
    assert "missing value" in res.rejection_reason


def test_missing_end_value():
    res = compute_interval_precipitation(tp_start_mm=5.0, tp_end_mm=None, **COMMON)
    assert not res.available
    assert res.rejected


def test_wrong_lead_ordering_rejected():
    bad = dict(COMMON)
    bad["lead_start_hours"] = 6
    bad["lead_end_hours"] = 3
    res = compute_interval_precipitation(tp_start_mm=5.0, tp_end_mm=12.0, **bad)
    assert res.rejected
    assert "lead ordering" in res.rejection_reason or "strictly greater" in res.rejection_reason


def test_nonzero_start_step_rejected_even_if_values_look_fine():
    # Simulates a wrong-initialization-cycle / malformed-window pairing: the
    # accumulation windows don't both start at 0, so differencing must be
    # refused per the Phase 0.4.4 validity check, regardless of how
    # plausible the raw numbers look.
    bad = dict(COMMON)
    bad["start_step"] = 3  # should be 0
    res = compute_interval_precipitation(tp_start_mm=5.0, tp_end_mm=12.0, **bad)
    assert res.rejected
    assert "step 0" in res.rejection_reason


def test_wrong_initialization_cycle_guard():
    assert verify_same_initialization_cycle("20200715 0000", "20200715 0000") is True
    assert verify_same_initialization_cycle("20200714 1800", "20200715 0000") is False
