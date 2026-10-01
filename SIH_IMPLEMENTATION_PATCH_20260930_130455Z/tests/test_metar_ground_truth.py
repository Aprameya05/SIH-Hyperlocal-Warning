#!/usr/bin/env python3
"""
Regression tests for metar_ground_truth.py (automated verification, Phase 2).

Uses synthetic METAR-shaped fixture data -- no real network call, no
fabricated forecast content. Proves:
  1. observations append durably and dedupe by (station, obs_time_utc)
  2. a slot is labeled TS the moment any observation in its window shows TS
  3. a slot is labeled NO_TS only after its window has closed with >=1
     clear-sky observation
  4. a slot with zero observations, or an in-progress window with only
     clear-sky reads so far, is NEVER scored as a negative
     (NO_OBSERVATION_AVAILABLE, label=None)
  5. forecast_log.csv rows are only updated when a real label is available,
     and existing labels are never silently overwritten

Run: python3 test_metar_ground_truth.py
"""

import csv
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from metar_ground_truth import (
    IST, append_metar_observation, label_slot_from_metar_log,
    update_forecast_log_labels, slot_for_datetime,
)

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_metar(obs_time_utc, ts_present, station="VOBL", wx="TS" if False else ""):
    return {
        "station": station,
        "obs_time": obs_time_utc,
        "fetched_utc": obs_time_utc,
        "thunderstorm_present": ts_present,
        "wx_string": "TSRA" if ts_present else "FEW020",
        "raw": f"{station} {obs_time_utc} METAR",
    }


def test_slot_boundaries():
    print("test_slot_boundaries")
    check("00:30 IST -> slot 0", slot_for_datetime(datetime(2026, 9, 30, 0, 30, tzinfo=IST)) == 0)
    check("05:59 IST -> slot 0", slot_for_datetime(datetime(2026, 9, 30, 5, 59, tzinfo=IST)) == 0)
    check("06:01 IST -> slot 1", slot_for_datetime(datetime(2026, 9, 30, 6, 1, tzinfo=IST)) == 1)
    check("13:00 IST -> slot 2", slot_for_datetime(datetime(2026, 9, 30, 13, 0, tzinfo=IST)) == 2)
    check("23:30 IST -> slot 3", slot_for_datetime(datetime(2026, 9, 30, 23, 30, tzinfo=IST)) == 3)


def test_append_dedupes():
    print("test_append_dedupes")
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "metar_observations_43295.csv"
        m1 = make_metar("2026-09-30T07:00:00Z", ts_present=False)
        r1 = append_metar_observation(log_path, m1)
        check("first observation is logged", r1 is not None)
        r2 = append_metar_observation(log_path, m1)  # exact duplicate report
        check("duplicate obs_time is not logged again", r2 is None)
        with open(log_path) as f:
            rows = list(csv.DictReader(f))
        check("only 1 row on disk after duplicate attempt", len(rows) == 1, len(rows))


def test_ts_observed_is_immediate():
    print("test_ts_observed_is_immediate")
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "metar_observations_43295.csv"
        # 07:00 IST (=01:30 UTC) falls in slot 1 (06:01-12:00 IST)
        append_metar_observation(log_path, make_metar("2026-09-30T01:30:00Z", ts_present=True))
        # Slot 1 window hasn't closed (it's only 07:00 IST), but a TS sighting
        # is conclusive immediately -- must not wait for window close.
        now_ist = datetime(2026, 9, 30, 7, 5, tzinfo=IST)
        result = label_slot_from_metar_log(log_path, "2026-09-30", 1, now_ist=now_ist)
        check("outcome is TS_OBSERVED", result.outcome == "TS_OBSERVED", result.outcome)
        check("label is 1", result.label == 1, result.label)


def test_no_ts_only_after_window_closes():
    print("test_no_ts_only_after_window_closes")
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "metar_observations_43295.csv"
        # 07:00 IST clear-sky read, slot 1 window (06:01-12:00) still open
        append_metar_observation(log_path, make_metar("2026-09-30T01:30:00Z", ts_present=False))

        mid_window = datetime(2026, 9, 30, 9, 0, tzinfo=IST)
        result_mid = label_slot_from_metar_log(log_path, "2026-09-30", 1, now_ist=mid_window)
        check("mid-window with only clear-sky reads is NOT scored as a negative yet",
              result_mid.outcome == "NO_OBSERVATION_AVAILABLE", result_mid.outcome)
        check("label is None mid-window", result_mid.label is None, result_mid.label)

        after_window = datetime(2026, 9, 30, 12, 30, tzinfo=IST)
        result_after = label_slot_from_metar_log(log_path, "2026-09-30", 1, now_ist=after_window)
        check("after window close, clear-sky evidence gives NO_TS_OBSERVED",
              result_after.outcome == "NO_TS_OBSERVED", result_after.outcome)
        check("label is 0 after window close", result_after.label == 0, result_after.label)


def test_zero_observations_never_scored():
    print("test_zero_observations_never_scored")
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "metar_observations_43295.csv"
        # Never call append_metar_observation -- simulate aviationweather.gov
        # being down all day for this slot.
        after_window = datetime(2026, 9, 30, 13, 0, tzinfo=IST)
        result = label_slot_from_metar_log(log_path, "2026-09-30", 1, now_ist=after_window)
        check("zero observations -> NO_OBSERVATION_AVAILABLE even after window close",
              result.outcome == "NO_OBSERVATION_AVAILABLE", result.outcome)
        check("label is None, never fabricated as 0", result.label is None, result.label)


def test_forecast_log_update_respects_existing_labels():
    print("test_forecast_log_update_respects_existing_labels")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        metar_log = tmp_path / "metar_observations_43295.csv"
        forecast_log = tmp_path / "forecast_log.csv"

        # Slot 1, 2026-09-30: one TS observation logged.
        append_metar_observation(metar_log, make_metar("2026-09-30T01:30:00Z", ts_present=True))

        cols = ["date", "slot", "ts_probability", "ts_predicted", "ts_label_actual"]
        with open(forecast_log, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            writer.writerow({"date": "2026-09-30", "slot": 1, "ts_probability": 0.5,
                              "ts_predicted": 1, "ts_label_actual": ""})       # should get labeled
            writer.writerow({"date": "2026-09-30", "slot": 0, "ts_probability": 0.1,
                              "ts_predicted": 0, "ts_label_actual": "0"})      # already labeled -- must not change

        now_ist = datetime(2026, 9, 30, 7, 30, tzinfo=IST)
        actions = update_forecast_log_labels(forecast_log, metar_log, now_ist=now_ist)
        check("exactly 1 row was updated (slot 1 only)", len(actions) == 1, len(actions))

        with open(forecast_log) as f:
            rows = {r["slot"]: r for r in csv.DictReader(f)}
        check("slot 1 got labeled TS (1)", rows["1"]["ts_label_actual"] == "1", rows["1"]["ts_label_actual"])
        check("slot 0's pre-existing label of 0 was NOT overwritten",
              rows["0"]["ts_label_actual"] == "0", rows["0"]["ts_label_actual"])


if __name__ == "__main__":
    test_slot_boundaries()
    test_append_dedupes()
    test_ts_observed_is_immediate()
    test_no_ts_only_after_window_closes()
    test_zero_observations_never_scored()
    test_forecast_log_update_respects_existing_labels()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed -> {FAILURES}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
