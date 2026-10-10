"""
tests/test_phase58_artifact_freshness_check_decoupled_from_exit_code.py
=====================================================
2026-10-10: two separate attempts to fix the real exit-134 "double
free or corruption (!prev)" native crash in
scripts/phase34_build_unified_forecast.py (os._exit(0), then
MALLOC_ARENA_MAX=1/OMP_NUM_THREADS=1/nthread=1) both failed to stop
it from happening on every real CI run, confirmed via the GitHub
Checks annotations API across multiple fresh runs. Yet the ORIGINAL
incident that started this whole investigation showed the crash
happens AFTER the script already printed accepted=true -- i.e. after
its own atomic os.replace() already safely wrote a genuinely valid
data/unified_forecast.json to disk. Gating
steps.unified_artifact_check.outputs.ok (used by the commit step and
every deploy step) on the Python PROCESS's exit code was therefore
the wrong signal all along: a native crash in cleanup code that runs
after the real work finished will always masquerade as a build
failure under that scheme, permanently blocking every commit even
once the artifact itself is genuinely good -- which is exactly what
happened for 2+ days straight (data/unified_forecast.json frozen at
generated_at_utc=2026-10-08T06:08:16 across 10+ consecutive commits).

Fix: a new "Verify unified artifact is genuinely fresh and valid"
step independently re-validates the artifact's actual on-disk content
and freshness (generated_at_utc within the last 30 minutes, every one
of the script's own validation keys true), decoupled entirely from
whatever the producing process's exit code was. This test verifies
that validation logic directly (extracted from the workflow's inline
Python, since the workflow itself can't be executed in pytest) against
the real production artifact and deliberately corrupted variants.

2026-10-10 hardening: per explicit feedback that "modification time
alone is insufficient proof of successful generation" and "the
artifact must demonstrably belong to the current run," two further
checks were added (and are verified here): (1) the artifact's actual
filesystem mtime must be newer than a marker timestamp captured
OUTSIDE and BEFORE the generator step runs -- real, external proof
the file was touched this run, not merely a self-reported timestamp
inside the same file being validated; (2) record/cell/lead counts are
re-derived directly from the records array itself (not merely trusted
from the self-reported validation dict, which is computed by the same
process being validated).
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_VALIDATION_KEYS = [
    "exactly_992_cells", "exactly_4960_records", "all_5_lead_slots_present_per_cell",
    "lead_time_arithmetic_correct_for_every_record", "all_present_probabilities_in_0_1_range",
    "ff_never_carries_a_fabricated_probability", "no_unavailable_probability_mapped_to_a_risk_category",
]


def check_artifact(path: Path, marker_epoch: float, max_age_minutes: float = 30.0) -> tuple[bool, str]:
    """Same logic as the workflow's inline Python step -- kept here as
    a single source of truth so a change to one can be tested against
    the other, rather than drifting silently apart. marker_epoch: the
    pre-build marker timestamp (seconds since epoch) captured OUTSIDE
    and BEFORE the generator step -- the file's own mtime must be
    newer than this to prove this run actually touched it."""
    try:
        mtime_epoch = os.path.getmtime(path)
    except Exception as exc:
        return False, f"{path} missing or stat-unreadable ({type(exc).__name__}: {exc})"
    if mtime_epoch < marker_epoch:
        return False, (f"filesystem mtime ({mtime_epoch:.0f}) is OLDER than the pre-build marker "
                        f"({marker_epoch:.0f}) -- provably the untouched last-known-good artifact")

    try:
        with open(path, encoding="utf-8") as f:
            artifact = json.load(f)
    except Exception as exc:
        return False, f"{path} missing or unparseable ({type(exc).__name__}: {exc})"

    generated_at = artifact.get("generated_at_utc")
    try:
        generated_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except Exception as exc:
        return False, f"generated_at_utc missing or unparseable ({generated_at!r}: {exc})"
    age_minutes = (datetime.now(timezone.utc) - generated_dt).total_seconds() / 60.0
    if age_minutes > max_age_minutes:
        return False, f"artifact is {age_minutes:.1f} minutes old -- not fresh"

    records = artifact.get("records", [])
    if len(records) != 4960:
        return False, f"expected exactly 4960 records, found {len(records)} (re-derived directly)"
    unique_cells = {r.get("cell_id") for r in records}
    if len(unique_cells) != 992:
        return False, f"expected exactly 992 unique cell_ids, found {len(unique_cells)} (re-derived directly)"
    lead_hours_by_cell: dict = {}
    for r in records:
        lead_hours_by_cell.setdefault(r.get("cell_id"), set()).add(r.get("lead_hours"))
    incomplete_cells = [c for c, leads in lead_hours_by_cell.items() if len(leads) != 5]
    if incomplete_cells:
        return False, f"{len(incomplete_cells)} cell(s) lack exactly 5 distinct lead_hours (re-derived directly)"

    v = artifact.get("validation", {})
    failed_keys = [k for k in REQUIRED_VALIDATION_KEYS if not v.get(k)]
    if failed_keys:
        return False, f"validation keys failed: {failed_keys}"
    return True, (f"OK: artifact is {age_minutes:.1f} min old, mtime proves this-run origin, "
                  f"4960 records / 992 cells / 5 leads re-derived directly, all validation keys true")


@pytest.fixture
def real_artifact_dict():
    path = REPO_ROOT / "data" / "unified_forecast.json"
    if not path.exists():
        pytest.skip("data/unified_forecast.json not present in this checkout")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_stale_committed_artifact_fails_the_freshness_check(real_artifact_dict, tmp_path):
    """The real committed artifact on disk right now is from a prior
    run (possibly hours/days old) -- it must correctly fail this
    check's freshness requirement, never be treated as 'this run's
    output' just because its content happens to be valid."""
    p = tmp_path / "artifact.json"
    marker = time.time() - 5  # marker clearly before the write, so only the age check should trip
    with open(p, "w", encoding="utf-8") as f:
        json.dump(real_artifact_dict, f)
    ok, reason = check_artifact(p, marker_epoch=marker)
    assert ok is False
    assert "minutes old" in reason or "not fresh" in reason


def test_freshly_timestamped_valid_artifact_passes(real_artifact_dict, tmp_path):
    d = dict(real_artifact_dict)
    d["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    p = tmp_path / "artifact.json"
    marker = time.time() - 5
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    ok, reason = check_artifact(p, marker_epoch=marker)
    assert ok is True, reason


def test_fresh_but_structurally_invalid_artifact_fails(real_artifact_dict, tmp_path):
    d = json.loads(json.dumps(real_artifact_dict))  # deep copy
    d["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    d["validation"]["exactly_992_cells"] = False
    p = tmp_path / "artifact.json"
    marker = time.time() - 5
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    ok, reason = check_artifact(p, marker_epoch=marker)
    assert ok is False
    assert "exactly_992_cells" in reason


def test_missing_artifact_fails_gracefully(tmp_path):
    ok, reason = check_artifact(tmp_path / "does_not_exist.json", marker_epoch=time.time())
    assert ok is False
    assert "missing or stat-unreadable" in reason


def test_corrupted_json_fails_gracefully(tmp_path):
    p = tmp_path / "artifact.json"
    p.write_text("<<<<<<< HEAD\nnot valid json\n=======\n>>>>>>>", encoding="utf-8")
    ok, reason = check_artifact(p, marker_epoch=time.time() - 60)
    assert ok is False
    assert "missing or unparseable" in reason


def test_untouched_file_with_future_marker_fails_the_mtime_check(real_artifact_dict, tmp_path):
    """2026-10-10 hardening: even a freshly-timestamped, structurally
    valid artifact must fail if its real filesystem mtime predates the
    pre-build marker -- this is the actual, external proof this run's
    generator step touched the file, independent of the file's own
    self-reported generated_at_utc content."""
    d = dict(real_artifact_dict)
    d["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    p = tmp_path / "artifact.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    marker_in_the_future = time.time() + 3600  # simulates a marker captured after the file was written
    ok, reason = check_artifact(p, marker_epoch=marker_in_the_future)
    assert ok is False
    assert "mtime" in reason


def test_wrong_record_count_fails_even_if_validation_dict_lies(real_artifact_dict, tmp_path):
    """The record/cell/lead counts must be re-derived directly from
    the records array, not merely trusted from the self-reported
    validation dict (which is computed by the same process)."""
    d = json.loads(json.dumps(real_artifact_dict))
    d["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    d["records"] = d["records"][:100]  # truncate -- but leave validation dict's lie intact
    p = tmp_path / "artifact.json"
    marker = time.time() - 5
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    ok, reason = check_artifact(p, marker_epoch=marker)
    assert ok is False
    assert "4960 records" in reason or "992" in reason


def test_workflow_uses_the_independent_check_not_the_process_exit_code():
    """The real bug: every gate must read
    steps.unified_artifact_check.outputs.ok, never
    steps.unified_artifact.outcome (the Python process's own exit
    code) -- that was the signal that stayed permanently blocked for
    2+ days despite the artifact itself being fine."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "forecast_update.yml"
    text = workflow_path.read_text(encoding="utf-8")
    assert "steps.unified_artifact_check.outputs.ok" in text
    # The literal condition 'steps.unified_artifact.outcome ==' must not
    # appear anywhere as a live gate any more (comments mentioning it
    # for historical context are fine and expected).
    import re
    live_gates = re.findall(r"if: steps\.unified_artifact\.outcome == 'success'", text)
    assert live_gates == [], f"found {len(live_gates)} gate(s) still reading the unreliable process exit code"


def test_workflow_has_a_pre_build_marker_step_before_the_generator():
    """The mtime cross-check needs a marker captured OUTSIDE and
    BEFORE scripts/phase34_build_unified_forecast.py runs -- verify
    that step exists and genuinely precedes the generator step in the
    workflow's step order."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "forecast_update.yml"
    text = workflow_path.read_text(encoding="utf-8")
    marker_idx = text.index("Record pre-build marker timestamp")
    generator_idx = text.index("id: unified_artifact\n")
    check_idx = text.index("id: unified_artifact_check")
    assert marker_idx < generator_idx < check_idx
    assert "pre_unified_artifact_marker.txt" in text
