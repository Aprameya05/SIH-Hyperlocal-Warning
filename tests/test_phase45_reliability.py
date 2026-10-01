#!/usr/bin/env python3
"""
test_phase45_reliability.py -- Phase 4.5 hard checks: atomic engine writes,
the GFS max-age gate, workflow concurrency/ordering, and deployment-artifact
presence.

Run: python3 tests/test_phase45_reliability.py   (from repo root)
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from gfs_row_select import select_latest_gfs, check_gfs_freshness, GFS_MAX_AGE_HOURS  # noqa: E402
from atomic_write import atomic_write_json, AtomicWriteError, read_json_or_none  # noqa: E402

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


def test_1_engine_writes_use_atomic_write():
    print("1. backend/pipeline.py and forecast_action.py route their production writes through atomic_write.py")
    pipeline_src = (REPO_ROOT / "backend" / "pipeline.py").read_text()
    forecast_src = (REPO_ROOT / "forecast_action.py").read_text()

    check("backend/pipeline.py imports atomic_write_json", "from atomic_write import atomic_write_json" in pipeline_src)
    check("backend/pipeline.py's pan_india_grid.json write uses atomic_write_json, not OUT_FILE.write_text",
          "atomic_write_json(OUT_FILE, output" in pipeline_src
          and "OUT_FILE.write_text(json.dumps(output" not in pipeline_src)
    check("backend/pipeline.py's ctt_grid.json write uses atomic_write_json",
          "atomic_write_json(out_path, {" in pipeline_src)

    check("forecast_action.py imports atomic_write_json", "from atomic_write import atomic_write_json" in forecast_src)
    check("forecast_action.py's main forecast.json write uses atomic_write_json, not a bare open(...,'w')",
          'atomic_write_json("forecast.json", forecast' in forecast_src)
    check("forecast_action.py's emergency-path forecast.json write ALSO uses atomic_write_json",
          'atomic_write_json("forecast.json", emergency' in forecast_src)
    check("forecast_action.py now imports sys (previously missing -- sys.exit(1) in the crash handler "
          "would itself have raised NameError)", "import sys" in forecast_src)

    # Deliberately-left-alone writes: pipeline_health.json crash record is a
    # diagnostic, not a production forecast artifact consumed downstream in
    # a way where partial-write matters -- confirm it was NOT touched.
    check("pipeline_health.json crash-record write is left as a plain json.dump (diagnostic, not "
          "part of the production forecast publication path)",
          'json.dump({\n                    "generated_at_utc": emergency["generated_at_utc"' in forecast_src
          or 'with open(health_path, "w") as _f:' in forecast_src)


def test_2_atomic_write_preserves_previous_on_failure():
    print("2. a failed engine-style write preserves the previous artifact (using atomic_write.py directly)")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pan_india_grid.json"
        atomic_write_json(p, {"generated_at_utc": "t0", "grid_cells": []})
        original = p.read_text()

        import math
        try:
            atomic_write_json(p, {"bad": float("nan")})
            failed = False
        except AtomicWriteError:
            failed = True
        check("a NaN-containing payload is rejected before any write (allow_nan=False)", failed)
        check("previous valid artifact is completely untouched after the rejected write", p.read_text() == original)

        doc = read_json_or_none(p)
        check("resulting on-disk JSON is still complete and parseable", doc is not None and doc["generated_at_utc"] == "t0")


def test_3_successful_publication_then_replacement():
    print("3. successful atomic publication actually replaces the file, and never exposes a partial file")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "forecast.json"
        atomic_write_json(p, {"generated_at": "run1", "slots": [1, 2, 3]})
        atomic_write_json(p, {"generated_at": "run2", "slots": [4, 5, 6, 7]})
        doc = read_json_or_none(p)
        check("second publish fully replaced the first (no merge, no partial leftover)",
              doc == {"generated_at": "run2", "slots": [4, 5, 6, 7]})
        # No stray temp files left behind in the same directory.
        leftovers = [f for f in Path(td).iterdir() if f.name != "forecast.json"]
        check("no temp files left behind after a successful publish", leftovers == [], f"found {leftovers}")


def test_4_gfs_max_age_gate():
    print("4. GFS row selection: fresh accepted, stale rejected, invalid timestamp rejected, future timestamp rejected")
    now = pd.Timestamp("2026-09-30 12:00:00")

    def row(fetched, cape=500.0, k=35.0):
        return {"date": "2026-09-30", "gfs_cycle": "2026-09-30 06Z f006", "fetched_at_utc": fetched,
                "fetched_at": "", "slot": 2, "CAPE": cape, "K_INDEX": k}

    # Fresh: 1 hour old
    df_fresh = pd.DataFrame([row("2026-09-30 11:00")])
    sel = select_latest_gfs(df_fresh, date_str="2026-09-30", now_utc=now)
    check("a 1h-old row is ACCEPTED (well within the freshness window)", sel is not None)
    check("accepted selection reports freshness=VALID", sel is not None and sel.freshness == "VALID")

    # Boundary: exactly at the documented threshold (GFS_MAX_AGE_HOURS)
    boundary_fetched = (now - pd.Timedelta(hours=GFS_MAX_AGE_HOURS)).strftime("%Y-%m-%d %H:%M")
    df_boundary = pd.DataFrame([row(boundary_fetched)])
    sel_b = select_latest_gfs(df_boundary, date_str="2026-09-30", now_utc=now)
    check(f"a row exactly at the documented {GFS_MAX_AGE_HOURS}h threshold is ACCEPTED "
          "(the rule is 'older than', not 'at or older than')", sel_b is not None)

    # Just past boundary: one minute older than the threshold
    past_boundary = (now - pd.Timedelta(hours=GFS_MAX_AGE_HOURS, minutes=1)).strftime("%Y-%m-%d %H:%M")
    df_past = pd.DataFrame([row(past_boundary)])
    sel_p = select_latest_gfs(df_past, date_str="2026-09-30", now_utc=now)
    check("a row one minute past the threshold is REJECTED", sel_p is None)

    # Clearly stale: 24h old
    df_stale = pd.DataFrame([row("2026-09-29 12:00")])
    sel_s = select_latest_gfs(df_stale, date_str="2026-09-30", now_utc=now)
    check("a 24h-old row is REJECTED as stale, not silently accepted", sel_s is None)

    # Malformed timestamp
    check("check_gfs_freshness on an unparseable string is INVALID",
          check_gfs_freshness("not-a-timestamp", now_utc=now) == "INVALID")
    check("check_gfs_freshness on None is INVALID", check_gfs_freshness(None, now_utc=now) == "INVALID")

    # Future timestamp (beyond clock-skew allowance)
    future = (now + pd.Timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    check("check_gfs_freshness on a timestamp 1h in the future is INVALID (not accepted as extra-fresh)",
          check_gfs_freshness(future, now_utc=now) == "INVALID")

    # Small clock skew (2 minutes in the future) is tolerated
    small_future = (now + pd.Timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M")
    check("a 2-minute future timestamp (ordinary clock skew) is still VALID",
          check_gfs_freshness(small_future, now_utc=now) == "VALID")

    # Does not silently substitute an older row when the newest is stale
    df_two = pd.DataFrame([
        row("2026-09-29 04:00", cape=999.0),   # newest fetch, but stale (32h old)
        row("2026-09-29 03:00", cape=111.0),   # even older
    ])
    # both rows share date "2026-09-30" only via the `date` field set above; force both to today's date
    df_two["date"] = "2026-09-30"
    sel_two = select_latest_gfs(df_two, date_str="2026-09-30", now_utc=now)
    check("when the freshest candidate is stale, selection FAILS EXPLICITLY rather than falling back "
          "to an even older row", sel_two is None)


def test_5_deterministic_selection_preserved():
    print("5. deterministic newest-row selection is unaffected by the freshness gate when data is fresh")
    now = pd.Timestamp("2026-09-30 12:00:00")

    def row(fetched, cape):
        return {"date": "2026-09-30", "gfs_cycle": "2026-09-30 06Z f006", "fetched_at_utc": fetched,
                "fetched_at": "", "slot": 2, "CAPE": cape, "K_INDEX": 35.0}

    df = pd.DataFrame([row("2026-09-30 08:00", 100.0), row("2026-09-30 11:30", 200.0), row("2026-09-30 09:00", 150.0)])
    sel = select_latest_gfs(df, date_str="2026-09-30", now_utc=now)
    check("still picks the row with the newest fetched_at_utc, not row order",
          sel is not None and float(sel.row["CAPE"]) == 200.0)


def test_6_workflow_concurrency_configured():
    print("6. both workflows share one concurrency group with cancel-in-progress false")
    grid_yml = (REPO_ROOT / ".github" / "workflows" / "update_grid.yml").read_text()
    forecast_yml = (REPO_ROOT / ".github" / "workflows" / "forecast_update.yml").read_text()
    for name, src in (("update_grid.yml", grid_yml), ("forecast_update.yml", forecast_yml)):
        check(f"{name} declares a concurrency group", "concurrency:" in src and "group: sih-forecast-publish" in src)
        check(f"{name} does NOT set cancel-in-progress: true (a running publish must not be killed mid-write)",
              "cancel-in-progress: true" not in src)
        check(f"{name} explicitly sets cancel-in-progress: false", "cancel-in-progress: false" in src)
    check("both workflows use the exact same concurrency group name (serializes against EACH OTHER, "
          "not just against themselves)",
          "group: sih-forecast-publish" in grid_yml and "group: sih-forecast-publish" in forecast_yml)


def test_7_deployment_directory_contains_canonical():
    print("7. forecast_update.yml deploys '.', and canonical_forecast.json exists in the repo root's data/ dir at deploy time")
    forecast_yml = (REPO_ROOT / ".github" / "workflows" / "forecast_update.yml").read_text()
    check("forecast_update.yml runs 'wrangler pages deploy .' (whole working directory)",
          "pages deploy ." in forecast_yml)
    check("update_grid.yml has NO Cloudflare deploy step (only forecast_update.yml deploys)",
          "wrangler" not in (REPO_ROOT / ".github" / "workflows" / "update_grid.yml").read_text())
    check("data/canonical_forecast.json exists in the repo at the path that would be included in '.'  deploy",
          (REPO_ROOT / "data" / "canonical_forecast.json").exists())


def test_8_alert_never_before_canonical_validation():
    print("8. alert steps in both workflows are strictly after the canonical build+validate step")
    grid_yml = (REPO_ROOT / ".github" / "workflows" / "update_grid.yml").read_text()
    forecast_yml = (REPO_ROOT / ".github" / "workflows" / "forecast_update.yml").read_text()
    check("update_grid.yml: canonical build precedes Twilio dispatch",
          grid_yml.index("Build canonical forecast") < grid_yml.index("Dispatch alerts if hazard thresholds exceeded"))
    check("forecast_update.yml: canonical build precedes WhatsApp dispatch",
          forecast_yml.index("Build canonical forecast") < forecast_yml.index("Send WhatsApp alerts"))
    # Neither alert step has continue-on-error BEFORE the canonical step that
    # could mask a validation failure -- confirm the canonical build step
    # itself still has no continue-on-error (would let a bad build through).
    canon_step_grid = grid_yml.split("Build canonical forecast")[1].split("- name:")[0]
    canon_step_fc = forecast_yml.split("Build canonical forecast")[1].split("- name:")[0]
    # Check for the actual YAML key (a bare "continue-on-error:" line), not
    # just the phrase -- both steps' own explanatory comments legitimately
    # discuss why continue-on-error is absent, which would false-positive
    # on a plain substring check for "continue-on-error".
    check("update_grid.yml's canonical build step has no continue-on-error: key",
          "continue-on-error:" not in canon_step_grid)
    check("forecast_update.yml's canonical build step has no continue-on-error: key",
          "continue-on-error:" not in canon_step_fc)


if __name__ == "__main__":
    for fn in [test_1_engine_writes_use_atomic_write, test_2_atomic_write_preserves_previous_on_failure,
               test_3_successful_publication_then_replacement, test_4_gfs_max_age_gate,
               test_5_deterministic_selection_preserved, test_6_workflow_concurrency_configured,
               test_7_deployment_directory_contains_canonical, test_8_alert_never_before_canonical_validation]:
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
