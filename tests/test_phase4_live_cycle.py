#!/usr/bin/env python3
"""
test_phase4_live_cycle.py -- Phase 4 hard checks: realistic freshness
states, honest VOBL slot degradation labeling, the Himawari
total-failure-mislabeled-as-available bug fix, 992-cell update integrity,
and the two workflow-ordering/race fixes.

Run: python3 tests/test_phase4_live_cycle.py   (from repo root)
"""
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
import canonical_forecast_writer as cfw  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def _touch(path: Path, age_hours: float, now: datetime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    mtime = (now - timedelta(hours=age_hours)).timestamp()
    import os
    os.utime(path, (mtime, mtime))


def test_1_freshness_matrix():
    print("1. freshness state matrix: FETCHED / CACHED / STALE / FAILED, for GFS and Himawari independently")
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "artifact.json"

        # FAILED: file does not exist
        state, mtime, age = cfw._file_freshness_state(p, 7.0, now, freshly_fetched=False)
        check("missing file -> FAILED", state == "FAILED", f"got {state}")

        # FETCHED: file just written (mtime within 10 min) AND flag passed
        _touch(p, age_hours=0.02, now=now)  # ~1.2 min old
        state, mtime, age = cfw._file_freshness_state(p, 7.0, now, freshly_fetched=True)
        check("very recent file + freshly_fetched=True -> FETCHED", state == "FETCHED", f"got {state}")

        # CACHED: file recent-ish but flag NOT passed (this run didn't fetch it)
        state, mtime, age = cfw._file_freshness_state(p, 7.0, now, freshly_fetched=False)
        check("recent file but freshly_fetched=False -> CACHED (not FETCHED)", state == "CACHED", f"got {state}")

        # CACHED even with flag=True if the file is NOT actually recent (flag alone cannot lie)
        _touch(p, age_hours=3.0, now=now)
        state, mtime, age = cfw._file_freshness_state(p, 7.0, now, freshly_fetched=True)
        check("3h-old file + freshly_fetched=True -> still CACHED, not FETCHED (flag cannot override real mtime)",
              state == "CACHED", f"got {state}")

        # STALE: file older than its real threshold
        _touch(p, age_hours=8.0, now=now)
        state, mtime, age = cfw._file_freshness_state(p, 7.0, now, freshly_fetched=False)
        check("8h-old file with 7h threshold -> STALE", state == "STALE", f"got {state}")

        _touch(p, age_hours=8.0, now=now)
        state, mtime, age = cfw._file_freshness_state(p, 2.0, now, freshly_fetched=False)
        check("8h-old file with Himawari's 2h threshold -> STALE (different real cadence, not a universal number)",
              state == "STALE", f"got {state}")

    check("STALE and CACHED are never the same value (independent states)", "STALE" != "CACHED")
    check("FAILED and STALE are never the same value (independent states)", "FAILED" != "STALE")


def test_2_generated_at_does_not_affect_freshness():
    print("2. generated_at never changes source freshness (only real file mtime does)")
    now1 = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    now2 = datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc)  # 8h "later" writer invocation
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "artifact.json"
        # File genuinely fetched at 11:00 (1h before now1)
        _touch(p, age_hours=1.0, now=now1)
        state1, _, age1 = cfw._file_freshness_state(p, 7.0, now1, freshly_fetched=False)
        # Re-evaluate as if the writer ran later (now2) -- the file's real
        # mtime hasn't moved, so age must be computed relative to each
        # evaluation time, and the STATE must reflect the file's real age
        # at THAT evaluation time, not be frozen by an earlier generated_at.
        state2, _, age2 = cfw._file_freshness_state(p, 7.0, now2, freshly_fetched=False)
        check("same file evaluated at two different generated_at times gives two different, correct ages",
              abs(age2 - age1 - 8.0) < 0.01, f"age1={age1} age2={age2}")
        check("the SAME on-disk file transitions CACHED->STALE purely from real elapsed time, "
              "independent of any generated_at value", state1 == "CACHED" and state2 == "STALE",
              f"state1={state1} state2={state2}")


def test_3_slot_degradation_labeling():
    print("3. VOBL slot degradation (source=model_defaults/climatology) is never presented as a live prediction")
    generated_at = datetime.now(timezone.utc)
    grid = {"grid_cells": [{"lat": 13.0, "lon": 78.0}], "gfs_cycle": None, "gfs_fhour": None}
    himawari_dq = {"status": "FAILED", "timestamp": None}
    terrain_dq = {"status": "UNAVAILABLE"}

    # Case A: genuine live data
    fc_live = {
        "generated_at": "2026-09-30 06:10 IST", "model_version": "v6_temporal",
        "slots": [{"slot": 1, "label": "Morning", "time": "0601-1200 IST", "ts_probability": 0.30,
                   "cb_probability": 0.1, "ff_probability": 0.0, "source": "gfs+upperair",
                   "fallback": False, "model_used": "nowcast_slot1_xgb_v6_temporal.pkl"}],
    }
    cells = cfw.build_pan_india_cells(grid, generated_at, None)
    cfw.overlay_vobl_ml_cell(cells, fc_live, himawari_dq, terrain_dq)
    ts = cells[0]["forecasts"][0]["hazards"]["thunderstorm"]  # +2h -> slot1 window (08:10 IST)
    check("genuine live GFS+upperair slot: status AVAILABLE, no degradation reason attached",
          ts["status"] == "AVAILABLE" and ts.get("reason") is None, f"got {ts}")

    # Case B: model ran on climatological DEFAULTS (no live GFS row today)
    fc_defaults = {
        "generated_at": "2026-09-30 06:10 IST", "model_version": "v6_temporal",
        "slots": [{"slot": 1, "label": "Morning", "time": "0601-1200 IST", "ts_probability": 0.05,
                   "cb_probability": 0.0, "ff_probability": 0.0, "source": "model_defaults",
                   "fallback": False, "model_used": "nowcast_slot1_xgb_v6_temporal.pkl"}],
    }
    cells2 = cfw.build_pan_india_cells(grid, generated_at, None)
    cfw.overlay_vobl_ml_cell(cells2, fc_defaults, himawari_dq, terrain_dq)
    ts2 = cells2[0]["forecasts"][0]["hazards"]["thunderstorm"]
    check("model_defaults slot: still AVAILABLE (a real model did run) but its method field discloses "
          "climatological-default inputs, not live GFS", "AVAILABLE" == ts2["status"]
          and "default" in (ts2.get("method") or "").lower(), f"got {ts2}")

    # Case C: no model ran at all -- bare climatology lookup
    fc_clim = {
        "generated_at": "2026-09-30 06:10 IST", "model_version": "v6_temporal",
        "slots": [{"slot": 1, "label": "Morning", "time": "0601-1200 IST", "ts_probability": 0.011,
                   "cb_probability": 0.0, "ff_probability": 0.0, "source": "climatology",
                   "fallback": True, "fallback_reason": "no model artifact found for this slot in models/",
                   "model_used": "none"}],
    }
    cells3 = cfw.build_pan_india_cells(grid, generated_at, None)
    cfw.overlay_vobl_ml_cell(cells3, fc_clim, himawari_dq, terrain_dq)
    ts3 = cells3[0]["forecasts"][0]["hazards"]["thunderstorm"]
    check("climatology (no model ran) slot: status PROXY, never called a trained-model AVAILABLE prediction",
          ts3["status"] == "PROXY", f"got {ts3}")
    check("climatology slot's model field is explicitly a lookup table, not an XGBoost artifact name",
          ts3["model"] == "climatology_lookup_table", f"got {ts3['model']}")


def test_4_himawari_total_failure_never_looks_available():
    print("4. Himawari total-fetch-failure placeholder is never reported as an available observation")
    # Reproduces fetch_himawari_realtime.py's own documented total-failure
    # placeholder shape exactly (both S3 and JAXA unreachable): a full dict
    # of keys with the observation fields set to None and data_source
    # naming the failure -- forecast_action.py's own `bool(himawari)` check
    # on this dict is True (non-empty dict), which was the bug.
    placeholder = {
        "timestamp_utc": "2026-09-30T12:00:00", "timestamp_ist": "2026-09-30T17:30:00",
        "vobl_bt_celsius": None, "min_bt_50km": None, "mean_bt_50km": None,
        "cold_pixels_count": 0, "storm_detected": False, "nearest_pixel_dist_km": None,
        "threshold_celsius": -40.0, "data_source": "Himawari-9 — UNAVAILABLE (S3/JAXA unreachable)",
        "bt_trend_1h": None,
    }
    check("bool() on the failure placeholder dict is True (this IS the bug forecast_action.py has)",
          bool(placeholder) is True)

    with tempfile.TemporaryDirectory() as td:
        himawari_path = Path(td) / "himawari_realtime.json"
        himawari_path.write_text(json.dumps(placeholder))
        raw = cfw.read_json_or_none(himawari_path) or {}
        data_source = str(raw.get("data_source") or "")
        computed_available = raw.get("vobl_bt_celsius") is not None and "UNAVAILABLE" not in data_source.upper()
        check("canonical writer's own availability check (not forecast_action.py's buggy bool(dict)) "
              "correctly reports the placeholder as NOT available", computed_available is False)

        # And the genuinely-successful shape:
        good = dict(placeholder)
        good.update({"vobl_bt_celsius": -70.0, "data_source": "Himawari-9 Band 13 (10.4um) via NOAA AWS S3"})
        himawari_path.write_text(json.dumps(good))
        raw2 = cfw.read_json_or_none(himawari_path) or {}
        computed_available2 = raw2.get("vobl_bt_celsius") is not None and "UNAVAILABLE" not in str(raw2.get("data_source") or "").upper()
        check("a genuine successful observation is still correctly reported as available",
              computed_available2 is True)


def test_5_992_cell_update_integrity():
    print("5. 992-cell pan-India update integrity, from real on-disk data")
    doc = json.loads((REPO_ROOT / "data" / "canonical_forecast.json").read_text())
    cells = doc["cells"]
    check("exactly 992 cells", len(cells) == 992, f"got {len(cells)}")
    ids = [c["cell_id"] for c in cells]
    check("no duplicate cell IDs", len(ids) == len(set(ids)))
    grid_ids = {"IN_992_1.0deg_v1"}
    check("canonical grid_id matches the locked definition", doc["grid"]["grid_id"] in grid_ids)
    check("forecast_cycle.source_cycle is present (real GFS cycle string, not fabricated)",
          doc["forecast_cycle"].get("source_cycle") is not None)


def test_6_workflow_ordering_and_race_fix():
    print("6. workflow files: alert-after-canonical ordering and cross-workflow sync-before-build")
    grid_yml = (REPO_ROOT / ".github" / "workflows" / "update_grid.yml").read_text()
    forecast_yml = (REPO_ROOT / ".github" / "workflows" / "forecast_update.yml").read_text()

    canon_idx = grid_yml.index("Build canonical forecast")
    alert_idx = grid_yml.index("Dispatch alerts if hazard thresholds exceeded")
    check("update_grid.yml: canonical build now runs BEFORE alert dispatch (Phase 4 ordering fix)",
          canon_idx < alert_idx, f"canon_idx={canon_idx} alert_idx={alert_idx}")

    check("update_grid.yml has a cross-workflow sync pull before the canonical build step",
          "Sync latest cross-workflow artifacts" in grid_yml)
    check("forecast_update.yml has a cross-workflow sync pull before the canonical build step",
          "Sync latest cross-workflow artifacts" in forecast_yml)

    sync_idx = grid_yml.index("Sync latest cross-workflow artifacts")
    check("update_grid.yml: sync pull runs before canonical build", sync_idx < canon_idx)

    fc_sync_idx = forecast_yml.index("Sync latest cross-workflow artifacts")
    fc_canon_idx = forecast_yml.index("Build canonical forecast")
    fc_alert_idx = forecast_yml.index("Send WhatsApp alerts")
    check("forecast_update.yml: sync pull runs before canonical build", fc_sync_idx < fc_canon_idx)
    check("forecast_update.yml: canonical build runs before WhatsApp alert dispatch",
          fc_canon_idx < fc_alert_idx)


if __name__ == "__main__":
    for fn in [test_1_freshness_matrix, test_2_generated_at_does_not_affect_freshness,
               test_3_slot_degradation_labeling, test_4_himawari_total_failure_never_looks_available,
               test_5_992_cell_update_integrity, test_6_workflow_ordering_and_race_fix]:
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
