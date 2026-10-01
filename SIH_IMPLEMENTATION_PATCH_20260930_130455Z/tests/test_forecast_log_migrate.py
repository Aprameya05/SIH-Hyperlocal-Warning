#!/usr/bin/env python3
"""
Regression test for forecast_log_migrate.migrate_forecast_log() (Phase 3).

Root cause under test: forecast_action.py used to detect a forecast_log.csv
header mismatch, copy the file to forecast_log.csv.bak, DELETE the original,
and start fresh -- destroying every previously accumulated verification row.
git history on this repo shows the header schema genuinely changed once,
which is exactly what triggered a real data loss.

This test builds an old-schema CSV with historical rows, migrates it to the
current schema, and asserts every historical row still exists afterward,
new columns are populated blank, and legacy-only columns are preserved
rather than dropped.

Run: python3 test_forecast_log_migrate.py
"""

import csv
import sys
import tempfile
from pathlib import Path

from forecast_log_migrate import migrate_forecast_log

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


OLD_SCHEMA = ["date", "slot", "slot_label", "probability", "predicted",
              "threshold", "model_version", "issued_at", "cape_used",
              "k_index_used", "logged_at", "ts_label_actual"]

CURRENT_SCHEMA = ["date", "slot", "ts_probability", "ts_predicted", "threshold",
                   "model_used", "model_version", "source",
                   "cape", "k_index", "lifted_index", "totals_totals",
                   "monsoon_regime", "regime_adjustment", "cape_tendency_jkgh",
                   "alert_active", "generated_at"]

OLD_ROWS = [
    {"date": "2026-09-28", "slot": "2", "slot_label": "Afternoon", "probability": "0.41",
     "predicted": "1", "threshold": "0.16", "model_version": "v5_temporal",
     "issued_at": "2026-09-28 11:45 IST", "cape_used": "900", "k_index_used": "39",
     "logged_at": "2026-09-28 11:46 IST", "ts_label_actual": "1"},
    {"date": "2026-09-29", "slot": "2", "slot_label": "Afternoon", "probability": "0.33",
     "predicted": "1", "threshold": "0.16", "model_version": "v5_temporal",
     "issued_at": "2026-09-29 11:45 IST", "cape_used": "700", "k_index_used": "37",
     "logged_at": "2026-09-29 11:46 IST", "ts_label_actual": "0"},
]


def write_old_csv(path: Path):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OLD_SCHEMA)
        writer.writeheader()
        for row in OLD_ROWS:
            writer.writerow(row)


def test_migration_preserves_historical_rows():
    print("test_migration_preserves_historical_rows")
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "forecast_log.csv"
        write_old_csv(log_path)

        result = migrate_forecast_log(log_path, CURRENT_SCHEMA)
        check("migration reports ok", result.ok is True)
        check("action is 'migrated'", result.action == "migrated", result.action)
        check("2 historical rows preserved", result.rows_preserved == 2, result.rows_preserved)

        with open(log_path, "r", newline="") as f:
            rows = list(csv.DictReader(f))
        check("file still has 2 rows after migration (no rows lost)", len(rows) == 2, len(rows))
        check("first historical row's date survived",
              rows[0]["date"] == "2026-09-28", rows[0].get("date"))
        check("second historical row's date survived",
              rows[1]["date"] == "2026-09-29", rows[1].get("date"))

        # New columns (introduced by the current schema) exist and are blank
        # for historical rows, never fabricated.
        check("new column 'ts_probability' exists and is blank for old rows",
              "ts_probability" in rows[0] and rows[0]["ts_probability"] == "", rows[0].get("ts_probability"))
        check("new column 'monsoon_regime' exists and is blank for old rows",
              "monsoon_regime" in rows[0] and rows[0]["monsoon_regime"] == "")

        # Legacy-only columns (not in current schema) are kept for traceability.
        check("legacy column 'slot_label' preserved", "slot_label" in rows[0])
        check("legacy column 'ts_label_actual' preserved and value intact",
              rows[0].get("ts_label_actual") == "1", rows[0].get("ts_label_actual"))

        check("a pre-migration backup file was written",
              result.backup_path is not None and result.backup_path.exists())
        with open(result.backup_path, "r", newline="") as f:
            backup_rows = list(csv.DictReader(f))
        check("backup file contains the original 2 rows untouched",
              len(backup_rows) == 2 and backup_rows[0]["date"] == "2026-09-28")


def test_append_after_migration_keeps_new_row_aligned():
    print("test_append_after_migration_keeps_new_row_aligned")
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "forecast_log.csv"
        write_old_csv(log_path)
        result = migrate_forecast_log(log_path, CURRENT_SCHEMA)
        write_cols = CURRENT_SCHEMA + result.columns_kept_legacy

        # Simulate forecast_action.py's append step using the migrated header.
        with open(log_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=write_cols, extrasaction="ignore")
            writer.writerow({"date": "2026-09-30", "slot": 2, "ts_probability": 0.43,
                              "ts_predicted": 1, "threshold": 0.16,
                              "model_used": "nowcast_slot2_xgb_v6_temporal.pkl",
                              "model_version": "v6_temporal", "source": "gfs+upperair",
                              "cape": 808, "k_index": 40.2, "lifted_index": -4.75,
                              "totals_totals": 48.4, "monsoon_regime": "NEUTRAL",
                              "regime_adjustment": 1.0, "cape_tendency_jkgh": "",
                              "alert_active": 1, "generated_at": "2026-09-30 17:51 IST"})

        with open(log_path, "r", newline="") as f:
            rows = list(csv.DictReader(f))
        check("3 rows total after append (2 historical + 1 new)", len(rows) == 3, len(rows))
        check("new row's ts_probability column is correctly aligned, not shifted",
              rows[2]["ts_probability"] == "0.43", rows[2].get("ts_probability"))
        check("new row's legacy-only columns are blank, not misaligned garbage",
              rows[2].get("slot_label", "") == "", rows[2].get("slot_label"))
        check("historical row 0 is still intact after the append",
              rows[0]["date"] == "2026-09-28" and rows[0].get("ts_label_actual") == "1")


def test_no_op_when_schema_already_current():
    print("test_no_op_when_schema_already_current")
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "forecast_log.csv"
        with open(log_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CURRENT_SCHEMA)
            writer.writeheader()
            writer.writerow({c: "" for c in CURRENT_SCHEMA})
        result = migrate_forecast_log(log_path, CURRENT_SCHEMA)
        check("action is 'no_op'", result.action == "no_op", result.action)
        check("no backup created for a no-op", result.backup_path is None)


def test_corrupt_file_is_backed_up_not_deleted():
    print("test_corrupt_file_is_backed_up_not_deleted")
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "forecast_log.csv"
        # Not really "unparseable" as CSV (csv.reader is very permissive), so
        # instead prove the "no file" path leaves nothing to delete, and that
        # a directory-in-place-of-file style failure is reported, not silently
        # swallowed into a wipe.
        log_path.mkdir()  # make log_path a directory to force a real I/O failure
        result = migrate_forecast_log(log_path, CURRENT_SCHEMA)
        check("action is 'failed'", result.action == "failed", result.action)
        check("ok is False", result.ok is False)
        check("original path still exists (nothing deleted)", log_path.exists())


if __name__ == "__main__":
    test_migration_preserves_historical_rows()
    test_append_after_migration_keeps_new_row_aligned()
    test_no_op_when_schema_already_current()
    test_corrupt_file_is_backed_up_not_deleted()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed -> {FAILURES}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
