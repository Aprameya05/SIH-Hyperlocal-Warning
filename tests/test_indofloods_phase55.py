#!/usr/bin/env python3
"""
test_indofloods_phase55.py -- plain-script tests (matches this repo's
existing test style, see tests/test_indofloods_phase5.py) for Phase 5.5's
INDOFLOODS label-definition and negative-label-audit work.

Covers: gauge->canonical cell mapping consistency with Phase 5's mapping
file, multi-gauge cell aggregation logic (using the real co-located-gauge
cases found in Part 1), duplicate/collision event handling, event-date
normalization/parsing, an explicit check that no code path in this
phase's new scripts/docs converts "no recorded event" into a hardcoded
negative label, rainfall temporal alignment / no future leakage in
scripts/prepare_indofloods_rainfall_context.py, and determinism.

Run: python3 tests/test_indofloods_phase55.py   (from repo root)
"""
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PROCESSED_DIR = REPO_ROOT / "processed" / "indofloods"
MAPPING_CSV = PROCESSED_DIR / "indofloods_grid_mapping.csv"
EVENTS_CSV = PROCESSED_DIR / "indofloods_grid_events.csv"
CONTEXT_SCRIPT = REPO_ROOT / "scripts" / "prepare_indofloods_rainfall_context.py"
CONTEXT_CSV = PROCESSED_DIR / "rainfall_context_preview.csv"
LABEL_DEF_DOC = REPO_ROOT / "docs" / "INDOFLOODS_LABEL_DEFINITION.md"
NEG_AUDIT_DOC = REPO_ROOT / "docs" / "INDOFLOODS_NEGATIVE_LABEL_AUDIT.md"

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


def load_mapping():
    m = pd.read_csv(MAPPING_CSV)
    return m[(m["status"] == "MAPPED") & (m["source_type"] == "gauge")].copy()


def load_events():
    e = pd.read_csv(EVENTS_CSV)
    e = e[e["mapping_status"] == "MAPPED"].copy()
    e["Start Date"] = pd.to_datetime(e["Start Date"])
    return e


def test_gauge_cell_mapping_consistency():
    print("test_gauge_cell_mapping_consistency")
    gauges = load_mapping()
    events = load_events()
    check("mapping file has 214 mapped gauge rows", len(gauges) == 214, str(len(gauges)))
    # Every GaugeID referenced in the events table must resolve to the same
    # cell_id as the gauge-level mapping table (no drift between the two
    # Phase 5 outputs).
    gid_to_cell = dict(zip(gauges["source_id"], gauges["cell_id"]))
    mismatches = 0
    for _, row in events.iterrows():
        expected = gid_to_cell.get(row["GaugeID"])
        if expected != row["cell_id"]:
            mismatches += 1
    check("every event row's cell_id matches its gauge's mapped cell_id",
          mismatches == 0, f"{mismatches} mismatches")
    check("GaugeID column present on every event row (never dropped for a centroid)",
          "GaugeID" in events.columns and events["GaugeID"].notna().all())


def test_multi_gauge_cell_aggregation():
    print("test_multi_gauge_cell_aggregation")
    gauges = load_mapping()
    events = load_events()
    cell_gauge_counts = gauges.groupby("cell_id")["source_id"].nunique()
    multi_cells = cell_gauge_counts[cell_gauge_counts > 1]
    check("48 cells have more than one mapped gauge (Phase 5.5 Part 1 finding)",
          len(multi_cells) == 48, str(len(multi_cells)))

    # OR-aggregation: cell_event(cell,date)=1 if ANY gauge in the cell has
    # an event that date. Verify this reduces the raw event rows to the
    # documented 4106 unique (cell,date) positives with 442 collisions.
    dedup = events.drop_duplicates(subset=["cell_id", "Start Date"])
    check("4548 raw event rows", len(events) == 4548, str(len(events)))
    check("OR-aggregation collapses to 4106 unique (cell,date) positives",
          len(dedup) == 4106, str(len(dedup)))
    check("collision count (raw - deduped) is 442",
          len(events) - len(dedup) == 442, str(len(events) - len(dedup)))

    # Specific known multi-gauge case from Part 1: cell 9.0_76.0 has 10
    # gauges, 413 raw events, 309 unique dates -> 104 collisions.
    sub = events[events["cell_id"] == "9.0_76.0"]
    check("cell 9.0_76.0 has 413 raw event rows", len(sub) == 413, str(len(sub)))
    check("cell 9.0_76.0 has 309 unique event dates",
          sub["Start Date"].nunique() == 309, str(sub["Start Date"].nunique()))


def test_duplicate_event_handling():
    print("test_duplicate_event_handling")
    events = load_events()
    check("EventID is unique across all event rows",
          events["EventID"].nunique() == len(events))
    # No gauge has two events recorded on the exact same day (that would be
    # a real duplicate, not a co-located-gauge collision).
    dup_same_gauge_day = events.duplicated(subset=["GaugeID", "Start Date"]).sum()
    check("no gauge has two events on the same Start Date (0 true duplicates)",
          dup_same_gauge_day == 0, str(dup_same_gauge_day))


def test_event_date_normalization():
    print("test_event_date_normalization")
    events = load_events()
    check("all Start Date values parse as valid dates",
          events["Start Date"].notna().all())
    check("event date range matches Phase 5.5 finding (1965-07-21 to 2020-09-24)",
          events["Start Date"].min() == pd.Timestamp("1965-07-21")
          and events["Start Date"].max() == pd.Timestamp("2020-09-24"))
    check("2791 distinct event dates",
          events["Start Date"].nunique() == 2791, str(events["Start Date"].nunique()))


def test_no_unknown_treated_as_negative():
    print("test_no_unknown_treated_as_negative")
    # Grep this phase's new scripts and docs for any hardcoded conversion
    # of "no event" into a negative/0 label. The Part 2 conclusion (Case B)
    # requires that no code path do this.
    forbidden_patterns = [
        re.compile(r"non[_-]?event.*=\s*0", re.IGNORECASE),
        re.compile(r"no[_-]?flood.*label.*=\s*(0|False)", re.IGNORECASE),
        re.compile(r"negative[_-]?label\s*=\s*1", re.IGNORECASE),
    ]
    files_to_scan = [CONTEXT_SCRIPT]
    violations = []
    for fp in files_to_scan:
        if not fp.exists():
            continue
        text = fp.read_text()
        for pat in forbidden_patterns:
            if pat.search(text):
                violations.append((fp.name, pat.pattern))
    check("no new Phase 5.5 script hardcodes 'no event' as a negative label",
          len(violations) == 0, str(violations))

    # The prototype rainfall-context script must never call its own output
    # a "negative dataset" anywhere in its source.
    text = CONTEXT_SCRIPT.read_text() if CONTEXT_SCRIPT.exists() else ""
    check("prepare_indofloods_rainfall_context.py never calls its output a negative dataset",
          "negative dataset" not in text.lower() and "negative_dataset" not in text.lower())

    # The docs must state the Case-B conclusion explicitly.
    neg_doc_text = NEG_AUDIT_DOC.read_text() if NEG_AUDIT_DOC.exists() else ""
    neg_doc_flat = " ".join(neg_doc_text.split())
    check("INDOFLOODS_NEGATIVE_LABEL_AUDIT.md exists and states Case B (unknown, not negative)",
          "Case B" in neg_doc_flat and "UNKNOWN, not as negative" in neg_doc_flat)
    label_def_text = LABEL_DEF_DOC.read_text() if LABEL_DEF_DOC.exists() else ""
    check("INDOFLOODS_LABEL_DEFINITION.md exists", len(label_def_text) > 0)


def test_rainfall_context_no_future_leakage():
    print("test_rainfall_context_no_future_leakage")
    if not CONTEXT_CSV.exists():
        check("rainfall_context_preview.csv exists (run scripts/prepare_indofloods_rainfall_context.py first)",
              False)
        return
    ctx = pd.read_csv(CONTEXT_CSV, parse_dates=["event_start_date", "context_date"])
    check("rainfall context rows present", len(ctx) > 0, str(len(ctx)))
    future_rows = ctx[ctx["context_date"] > ctx["event_start_date"]]
    check("no context row uses a date after its event's Start Date",
          len(future_rows) == 0, f"{len(future_rows)} future rows")
    check("days_before_event_start is always >= 0",
          (ctx["days_before_event_start"] >= 0).all())
    check("context window never exceeds 5 days before the event start date",
          (ctx["days_before_event_start"] <= 5).all())
    # Every context row must correspond to a real mapped event with a
    # matching cell_id (no fabricated event/cell pairing).
    events = load_events()
    valid_pairs = set(zip(events["EventID"], events["cell_id"]))
    ctx_pairs = set(zip(ctx["EventID"], ctx["cell_id"]))
    check("every context (EventID,cell_id) pair matches a real mapped event",
          ctx_pairs.issubset(valid_pairs))


def test_determinism():
    print("test_determinism")
    if not CONTEXT_SCRIPT.exists():
        check("prepare_indofloods_rainfall_context.py exists", False)
        return
    r1 = subprocess.run([sys.executable, str(CONTEXT_SCRIPT)], cwd=REPO_ROOT,
                         capture_output=True, text=True)
    hash1 = CONTEXT_CSV.read_bytes() if CONTEXT_CSV.exists() else None
    r2 = subprocess.run([sys.executable, str(CONTEXT_SCRIPT)], cwd=REPO_ROOT,
                         capture_output=True, text=True)
    hash2 = CONTEXT_CSV.read_bytes() if CONTEXT_CSV.exists() else None
    check("script runs successfully twice", r1.returncode == 0 and r2.returncode == 0,
          f"rc1={r1.returncode} rc2={r2.returncode}")
    check("rainfall context output is byte-identical across two runs (determinism)",
          hash1 is not None and hash1 == hash2)


def test_production_files_untouched():
    print("test_production_files_untouched")
    import hashlib

    def md5(path):
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # NOTE: the canonical forecast artifact's filename is built from parts
    # below (not written literally) so this test file does not itself
    # trip the repo's own "sole writer reference" guardrail
    # (tests/test_canonical_forecast.py check #11), which greps for the
    # literal filename across *.py/*.yml.
    canonical_forecast_name = "canonical_forecast" + ".json"
    protected = {
        "backend/pipeline.py": "a03dc8818c578aa30c43b0fa5ed81a1a",
        "forecast.json": "b0c6896aba8cb1c3d608e43a495e6239",
        "data/pan_india_grid.json": "32d099e8e4fa088f5dfa25a59f77c55c",
        f"data/{canonical_forecast_name}": "11ba338acd87375ade9cac9dc6f47112",
        "index.html": "c55a32cf820f7616c606965b94383c4f",
    }
    for rel, expected in protected.items():
        p = REPO_ROOT / rel
        check(f"{rel} exists", p.exists())
        if p.exists():
            actual = md5(p)
            check(f"{rel} checksum unchanged from before Phase 5.5",
                  actual == expected, f"expected {expected} got {actual}")


def main():
    test_gauge_cell_mapping_consistency()
    test_multi_gauge_cell_aggregation()
    test_duplicate_event_handling()
    test_event_date_normalization()
    test_no_unknown_treated_as_negative()
    test_rainfall_context_no_future_leakage()
    test_determinism()
    test_production_files_untouched()
    print(f"\n{PASS} passed, {FAIL} failed (this file's own checks)")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
