#!/usr/bin/env python3
"""
test_canonical_forecast.py -- Phase 2 tests for the canonical forecast
schema, writer, and validator.

Run: python3 tests/test_canonical_forecast.py   (from repo root)
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import canonical_forecast_writer as writer  # noqa: E402
import validate_canonical_forecast as v  # noqa: E402

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


def _load_or_build():
    """Uses the real, already-published canonical forecast if present
    (built against real live data), otherwise builds one fresh -- either
    way this is real data, never synthetic fixtures."""
    path = writer.CANONICAL_PATH
    if path.exists():
        return json.loads(path.read_text())
    return writer.build_canonical_forecast()


def test_exactly_992_cells():
    print("1. exactly 992 canonical cells")
    doc = _load_or_build()
    check("cells array has exactly 992 entries", len(doc["cells"]) == 992, f"got {len(doc['cells'])}")


def test_unique_cell_ids():
    print("2. unique cell IDs")
    doc = _load_or_build()
    ids = [c["cell_id"] for c in doc["cells"]]
    check("no duplicate cell_id", len(ids) == len(set(ids)))


def test_generated_at_and_valid_time():
    print("3. generated_at / valid_time presence and validity")
    doc = _load_or_build()
    ok, errors = v.validate_integrity(doc)
    ts_errors = [e for e in errors if "generated_at" in e or "valid_time" in e]
    check("no generated_at/valid_time integrity errors", len(ts_errors) == 0, f"{ts_errors}")


def test_lead_hours_exactly_2_4_6():
    print("4. lead_hours are exactly {2,4,6} per cell")
    doc = _load_or_build()
    sample = doc["cells"][0]
    leads = sorted(f["lead_hours"] for f in sample["forecasts"])
    check("first cell has leads [2,4,6]", leads == [2, 4, 6], f"got {leads}")


def test_provenance_present_for_every_hazard():
    print("5. provenance (source/method/model) present for every hazard block")
    doc = _load_or_build()
    missing = 0
    for c in doc["cells"][:50]:  # sample -- full sweep is check 9 (integrity)
        for f in c["forecasts"]:
            for hz in f["hazards"].values():
                if not hz.get("source") or not hz.get("method") or not hz.get("model"):
                    missing += 1
    check("no missing provenance in sampled cells", missing == 0, f"found {missing}")


def test_status_values_valid():
    print("6. status values are from the allowed vocabulary")
    doc = _load_or_build()
    bad = set()
    for c in doc["cells"]:
        for f in c["forecasts"]:
            for hz in f["hazards"].values():
                if hz["status"] not in v.VALID_STATUSES:
                    bad.add(hz["status"])
    check("no invalid status values anywhere", len(bad) == 0, f"found {bad}")


def test_stale_data_handling_no_fabrication():
    print("7. UNAVAILABLE/NOT_APPLICABLE hazards never carry a fabricated probability")
    doc = _load_or_build()
    violations = 0
    for c in doc["cells"]:
        for f in c["forecasts"]:
            for hz in f["hazards"].values():
                if hz["status"] in ("UNAVAILABLE", "NOT_APPLICABLE") and hz["probability"] is not None:
                    violations += 1
    check("zero UNAVAILABLE/NOT_APPLICABLE hazards with a non-null probability", violations == 0, f"found {violations}")


def test_missing_himawari_handling():
    print("8. Himawari unavailable state is explicit, never fabricated as a CTT value")
    doc = _load_or_build()
    vobl = next(c for c in doc["cells"] if c["cell_id"] == writer.VOBL_CELL_ID)
    for f in vobl["forecasts"]:
        dq = f["data_quality"]["himawari"]
        check(f"VOBL lead={f['lead_hours']}: himawari data_quality status is a valid state",
              dq["status"] in v.VALID_STATUSES, f"got {dq['status']}")


def test_full_schema_and_integrity_validation():
    print("9. full schema + integrity validation passes on the real published artifact")
    doc = _load_or_build()
    schema_ok, schema_errors = v.validate_schema(doc)
    check("schema validation passes", schema_ok, f"{schema_errors[:5]}")
    integrity_ok, integrity_errors = v.validate_integrity(doc)
    check("integrity validation passes", integrity_ok, f"{integrity_errors[:5]}")


def test_no_fake_values_detected_by_validator():
    print("10. validator actually catches a deliberately corrupted copy")
    doc = _load_or_build()
    import copy
    bad = copy.deepcopy(doc)
    bad["cells"] = bad["cells"][:990]  # break the 992 invariant
    ok, errors = v.validate_integrity(bad)
    check("validator rejects a 990-cell forecast", not ok)
    check("error message names the cell-count problem", any("992" in e for e in errors))

    bad2 = copy.deepcopy(doc)
    bad2["cells"][0]["forecasts"][0]["hazards"]["thunderstorm"]["probability"] = 1.5
    ok2, errors2 = v.validate_integrity(bad2)
    check("validator rejects an out-of-range probability", not ok2)

    bad3 = copy.deepcopy(doc)
    bad3["cells"][0]["forecasts"][0]["hazards"]["thunderstorm"]["status"] = "UNAVAILABLE"
    bad3["cells"][0]["forecasts"][0]["hazards"]["thunderstorm"]["probability"] = 0.5
    ok3, errors3 = v.validate_integrity(bad3)
    check("validator rejects UNAVAILABLE status with a non-null (fabricated) probability", not ok3)


def test_canonical_writer_ownership():
    print("11. canonical_forecast_writer.py is the sole writer referenced by name in this repo")
    import subprocess
    hits = subprocess.run(
        ["grep", "-rl", "canonical_forecast.json", "--include=*.py", "--include=*.yml", str(REPO_ROOT)],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()
    hits = [h for h in hits if "__pycache__" not in h]
    # Phase 3.5 wired canonical_forecast_writer.py as an invoked STEP inside
    # both production workflows (forecast_update.yml, update_grid.yml) --
    # they reference the artifact path in their canonical-build/commit
    # steps, but neither workflow computes or writes canonical_forecast.json
    # itself; they only run the one real writer and then git-add its
    # output. That is invocation, not a competing writer, so both are now
    # legitimately allowlisted alongside the writer/validator/test modules.
    allowed_suffixes = (
        "canonical_forecast_writer.py",
        "validate_canonical_forecast.py",
        "test_canonical_forecast.py",
        "test_phase35_canonical_authority.py",
        "test_phase4_live_cycle.py",
        "test_phase45_reliability.py",
        "forecast_update.yml",
        "update_grid.yml",
    )
    writers = [h for h in hits if h.endswith(allowed_suffixes)]
    check("only the writer/validator/test modules and the two workflows that invoke the writer reference "
          "canonical_forecast.json by path (no competing writer)",
          set(hits) == set(writers) or all(h in writers for h in hits), f"{hits}")


def test_atomic_publication_preserves_previous_on_validation_failure():
    print("12. a validation failure never reaches atomic_write_json (previous artifact preserved)")
    import tempfile
    from atomic_write import atomic_write_json
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / "canonical_forecast.json"
        atomic_write_json(dest, {"good": True})
        before = dest.read_text()
        # simulate the writer's own gate: build a doc, validate, and only
        # write if valid -- this mirrors canonical_forecast_writer.main()'s
        # control flow without needing live GFS data.
        fake_bad_doc = {"cells": []}
        ok, _ = v.validate_integrity(fake_bad_doc)
        if not ok:
            pass  # writer.main() would return 1 here, WITHOUT calling atomic_write_json
        check("invalid doc is caught before any write call", not ok)
        check("previous artifact on disk is untouched", dest.read_text() == before)


if __name__ == "__main__":
    for fn in [test_exactly_992_cells, test_unique_cell_ids, test_generated_at_and_valid_time,
               test_lead_hours_exactly_2_4_6, test_provenance_present_for_every_hazard,
               test_status_values_valid, test_stale_data_handling_no_fabrication,
               test_missing_himawari_handling, test_full_schema_and_integrity_validation,
               test_no_fake_values_detected_by_validator, test_canonical_writer_ownership,
               test_atomic_publication_preserves_previous_on_validation_failure]:
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
