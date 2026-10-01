#!/usr/bin/env python3
"""
Regression tests for populate_skill_scores.py (Phase 3).

Root cause under test: metrics_from_log() required a column named
"ts_observed", which forecast_log.csv has never had (the real column,
written by fetch_metar.py/metar_ground_truth.py, is "ts_label_actual").
That name mismatch made metrics_from_log() return None on every call,
regardless of how much verified data actually existed.

This test builds a synthetic forecast_log.csv with known forecast/
observation pairs (some verified, some not, deliberately below and above
the MIN_VERIFIED_SAMPLES threshold) and asserts:
  1. metrics_from_log() now actually reads ts_label_actual and computes
     real POD/FAR/CSI/HSS/BIAS from it
  2. below MIN_VERIFIED_SAMPLES verified rows, status is INSUFFICIENT_DATA
     and no metric is silently reported as 0.0
  3. verified_count/unverified_count/total_count are always correct
  4. a hand-computed POD/FAR on a known confusion matrix matches exactly

Run: python3 test_skill_scores.py
"""

import sys
import pandas as pd

from populate_skill_scores import metrics_from_log, compute_metrics, MIN_VERIFIED_SAMPLES

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_df(rows):
    return pd.DataFrame(rows)


def test_insufficient_data_below_threshold():
    print("test_insufficient_data_below_threshold")
    # Only 3 verified rows for slot 2 -- below MIN_VERIFIED_SAMPLES (5).
    rows = [
        {"slot": 2, "ts_probability": 0.20, "ts_label_actual": 1},
        {"slot": 2, "ts_probability": 0.10, "ts_label_actual": 0},
        {"slot": 2, "ts_probability": 0.05, "ts_label_actual": 0},
    ]
    df = make_df(rows)
    result = metrics_from_log(df, slot_id=2, threshold=0.16)
    check("result is not None", result is not None)
    check("status is INSUFFICIENT_DATA", result.get("status") == "INSUFFICIENT_DATA", result.get("status"))
    check("verified_count is 3", result.get("verified_count") == 3, result.get("verified_count"))
    check("no POD key present (never fabricated as 0.0)", "POD" not in result)
    check(f"min_required matches module constant ({MIN_VERIFIED_SAMPLES})",
          result.get("min_required") == MIN_VERIFIED_SAMPLES)


def test_sufficient_data_computes_real_metrics():
    print("test_sufficient_data_computes_real_metrics")
    # 6 verified rows for slot 2, threshold 0.16.
    # Hand-computable confusion matrix at threshold 0.16:
    #   prob=0.50 label=1 -> pred=1 (TP)
    #   prob=0.30 label=1 -> pred=1 (TP)
    #   prob=0.05 label=0 -> pred=0 (TN)
    #   prob=0.20 label=0 -> pred=1 (FP)
    #   prob=0.02 label=1 -> pred=0 (FN)
    #   prob=0.01 label=0 -> pred=0 (TN)
    # TP=2 FP=1 FN=1 TN=2 -> POD=2/3=0.6667  FAR=1/3=0.3333  CSI=2/4=0.5
    rows = [
        {"slot": 2, "ts_probability": 0.50, "ts_label_actual": 1},
        {"slot": 2, "ts_probability": 0.30, "ts_label_actual": 1},
        {"slot": 2, "ts_probability": 0.05, "ts_label_actual": 0},
        {"slot": 2, "ts_probability": 0.20, "ts_label_actual": 0},
        {"slot": 2, "ts_probability": 0.02, "ts_label_actual": 1},
        {"slot": 2, "ts_probability": 0.01, "ts_label_actual": 0},
    ]
    df = make_df(rows)
    result = metrics_from_log(df, slot_id=2, threshold=0.16)
    check("status is OK", result.get("status") == "OK", result.get("status"))
    check("verified_count is 6", result.get("verified_count") == 6)
    check("TP=2", result["TP"] == 2, result["TP"])
    check("FP=1", result["FP"] == 1, result["FP"])
    check("FN=1", result["FN"] == 1, result["FN"])
    check("TN=2", result["TN"] == 2, result["TN"])
    # compute_metrics() (populate_skill_scores.py) intentionally rounds every
    # metric to 4 decimal places for the persisted/displayed skill-score
    # record (see r4() in compute_metrics()). The production value is
    # therefore round(2/3, 4) == 0.6667, not the unrounded float -- comparing
    # against the unrounded 2/3 with a 1e-6 tolerance was the test's own bug
    # (it demanded more precision than the production contract provides),
    # not a defect in compute_metrics(). Assert against the documented
    # 4-decimal rounding contract instead.
    check("POD == round(2/3, 4)", result["POD"] == round(2 / 3, 4), result["POD"])
    check("FAR == round(1/3, 4)", result["FAR"] == round(1 / 3, 4), result["FAR"])
    check("CSI == round(0.5, 4)", result["CSI"] == round(0.5, 4), result["CSI"])


def test_unverified_rows_counted_separately():
    print("test_unverified_rows_counted_separately")
    rows = [
        {"slot": 1, "ts_probability": 0.50, "ts_label_actual": 1},
        {"slot": 1, "ts_probability": 0.30, "ts_label_actual": 1},
        {"slot": 1, "ts_probability": 0.05, "ts_label_actual": 0},
        {"slot": 1, "ts_probability": 0.20, "ts_label_actual": 0},
        {"slot": 1, "ts_probability": 0.02, "ts_label_actual": 1},
        {"slot": 1, "ts_probability": 0.01, "ts_label_actual": ""},   # unverified -- blank string
        {"slot": 1, "ts_probability": 0.60, "ts_label_actual": None},  # unverified -- NaN
    ]
    df = make_df(rows)
    result = metrics_from_log(df, slot_id=1, threshold=0.15)
    check("total_count is 7", result.get("total_count") == 7, result.get("total_count"))
    check("verified_count is 5", result.get("verified_count") == 5, result.get("verified_count"))
    check("unverified_count is 2", result.get("unverified_count") == 2, result.get("unverified_count"))
    check("status is OK at exactly the threshold (5 == MIN_VERIFIED_SAMPLES)",
          result.get("status") == "OK", result.get("status"))


def test_missing_column_returns_none_not_crash():
    print("test_missing_column_returns_none_not_crash")
    df = pd.DataFrame([{"slot": 2, "ts_probability": 0.5}])  # no ts_label_actual at all
    result = metrics_from_log(df, slot_id=2, threshold=0.16)
    check("returns None cleanly when ts_label_actual column is entirely absent", result is None)


def test_no_rows_for_slot_returns_none():
    print("test_no_rows_for_slot_returns_none")
    df = pd.DataFrame([{"slot": 0, "ts_probability": 0.1, "ts_label_actual": 0}])
    result = metrics_from_log(df, slot_id=3, threshold=0.39)  # slot 3 has no rows
    check("returns None when the slot has zero rows in the window", result is None)


if __name__ == "__main__":
    test_insufficient_data_below_threshold()
    test_sufficient_data_computes_real_metrics()
    test_unverified_rows_counted_separately()
    test_missing_column_returns_none_not_crash()
    test_no_rows_for_slot_returns_none()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed -> {FAILURES}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
