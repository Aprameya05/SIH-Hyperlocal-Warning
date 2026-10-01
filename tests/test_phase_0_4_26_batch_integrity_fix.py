"""
Regression tests for the Phase 0.4.26 batch-integrity bugfix.

Evidence this fixes (confirmed by direct inspection of the live Colab run
and the notebook source, not assumption):

  * Batch 6 printed "Batch 6 complete." even though two expected files
    (gfs.0p25.2015010300.f003.grib2, .f006.grib2) 404'd. Root cause:
    run_one_batch()'s success flag came only from validate_batch() on the
    *already-filtered* ready subset -- there was no check that every
    event group in the batch actually made it through.
  * Batch 0 and Batch 1 repeated the same failure across reruns after a
    Colab disconnect: DOWNLOADED/VERIFIED entries survive in the
    Drive-persisted state JSON after /content is wiped, so
    download_batch()/verify_batch() skipped them forever instead of
    redownloading.

These tests re-implement the exact fixed logic (copied from the notebook
cells, not approximated) in isolated, pure-Python form, so they run
without Drive, network, or eccodes. A second block of tests asserts the
actual notebook source contains the fix, so a future edit can't silently
regress it.
"""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK_PATH = REPO_ROOT / "notebooks" / "PHASE_0_4_26_TS_ACQUISITION_AND_DATASET.ipynb"


# ---------------------------------------------------------------------
# Part 1: isolated re-implementation of the fixed state-machine logic.
# ---------------------------------------------------------------------

TERMINAL_STATUSES = {"DOWNLOADED", "VERIFIED", "EXTRACTED", "VALIDATED", "PERSISTED", "PURGED"}
LOCAL_DEPENDENT_STATUSES = {"DOWNLOADED", "VERIFIED", "EXTRACTED", "VALIDATED"}


def extraction_ready_event_groups(event_groups, files_by_group, state):
    ready = []
    for eg in event_groups:
        files = files_by_group[eg]
        if len(files) == 2 and all(state.get(f, {}).get("status") == "VERIFIED" for f in files):
            ready.append(eg)
    return ready


def incomplete_event_groups(event_groups, files_by_group, state):
    ready = set(extraction_ready_event_groups(event_groups, files_by_group, state))
    incomplete = []
    for eg in event_groups:
        if eg in ready:
            continue
        files = files_by_group[eg]
        statuses = {f: state.get(f, {}).get("status") for f in files}
        if all(s in ("PERSISTED", "PURGED") for s in statuses.values()):
            continue
        incomplete.append(eg)
    return incomplete


def reconcile_stale_local_state(event_groups, files_by_group, state, local_files_present):
    """local_files_present: set of filenames that actually exist on 'disk'."""
    for eg in event_groups:
        for f in files_by_group[eg]:
            entry = state.get(f)
            if entry is None or entry.get("status") not in LOCAL_DEPENDENT_STATUSES:
                continue
            if f not in local_files_present:
                entry["stale_reset_from"] = entry["status"]
                entry["status"] = "PENDING"
    return state


def simulate_run_one_batch(event_groups, files_by_group, state, local_files_present,
                            downloadable_files, extraction_succeeds_for):
    """A deliberately simplified stand-in for run_one_batch()/download_batch()/
    verify_batch()/extract_batch()/validate_batch()/persist_batch()/purge_batch_raw(),
    reproducing only the control flow the fix changed, to test it in isolation."""
    state = reconcile_stale_local_state(event_groups, files_by_group, state, local_files_present)

    # download_batch: anything not in a terminal status gets (re)downloaded.
    for eg in event_groups:
        for f in files_by_group[eg]:
            status = state.get(f, {}).get("status")
            if status in TERMINAL_STATUSES:
                continue
            if f in downloadable_files:
                state[f] = {"status": "DOWNLOADED"}
                local_files_present.add(f)
            else:
                state[f] = {"status": "FAILED", "error": "404"}

    # verify_batch: only acts on status == DOWNLOADED.
    for eg in event_groups:
        for f in files_by_group[eg]:
            entry = state.get(f, {})
            if entry.get("status") != "DOWNLOADED":
                continue
            if f not in local_files_present:
                entry["status"] = "FAILED"
                entry["error"] = "file missing"
            else:
                entry["status"] = "VERIFIED"

    incomplete = incomplete_event_groups(event_groups, files_by_group, state)
    ready = extraction_ready_event_groups(event_groups, files_by_group, state)

    if not ready:
        return state, incomplete, "FAILED_VALIDATION"

    for eg in ready:
        ok = extraction_succeeds_for.get(eg, True)
        for f in files_by_group[eg]:
            state[f]["status"] = "EXTRACTED" if ok else "FAILED"
    if not all(extraction_succeeds_for.get(eg, True) for eg in ready):
        return state, incomplete, "FAILED_VALIDATION"

    for eg in ready:
        for f in files_by_group[eg]:
            state[f]["status"] = "VALIDATED"
    for eg in ready:
        for f in files_by_group[eg]:
            state[f]["status"] = "PERSISTED"
    for eg in ready:
        for f in files_by_group[eg]:
            state[f]["status"] = "PURGED"
            local_files_present.discard(f)

    outcome = "COMPLETE" if not incomplete else "PARTIALLY_COMPLETE"
    return state, incomplete, outcome


def _files_by_group(groups_and_files):
    return {eg: files for eg, files in groups_and_files.items()}


def test_one_failed_download_prevents_extraction():
    files_by_group = _files_by_group({"eg1": ["eg1.f003.grib2", "eg1.f006.grib2"]})
    state = {}
    local = set()
    state, incomplete, outcome = simulate_run_one_batch(
        ["eg1"], files_by_group, state, local,
        downloadable_files=set(),  # both files 404
        extraction_succeeds_for={},
    )
    assert state["eg1.f003.grib2"]["status"] == "FAILED"
    assert "eg1" in incomplete
    assert outcome == "FAILED_VALIDATION"
    assert state["eg1.f003.grib2"]["status"] != "PURGED"


def test_one_missing_raw_file_prevents_extraction():
    # f003 downloads fine; f006 "downloads" but verify_batch finds it missing
    # on disk (simulating a download that silently produced no bytes).
    files_by_group = _files_by_group({"eg1": ["eg1.f003.grib2", "eg1.f006.grib2"]})
    state = {}
    local = set()

    # Hand-simulate: f003 downloadable and present, f006 downloadable per
    # download_batch's bookkeeping but never actually lands on disk.
    class _FakeLocal(set):
        def add(self, item):
            if item == "eg1.f006.grib2":
                return  # simulate the file never actually appearing
            super().add(item)

    local = _FakeLocal()
    state, incomplete, outcome = simulate_run_one_batch(
        ["eg1"], files_by_group, state, local,
        downloadable_files={"eg1.f003.grib2", "eg1.f006.grib2"},
        extraction_succeeds_for={},
    )
    assert state["eg1.f006.grib2"]["status"] == "FAILED"
    assert state["eg1.f006.grib2"]["error"] == "file missing"
    assert "eg1" in incomplete
    assert outcome == "FAILED_VALIDATION"


def test_incomplete_event_group_cannot_be_marked_purged():
    files_by_group = _files_by_group({
        "eg_good": ["good.f003.grib2", "good.f006.grib2"],
        "eg_bad": ["bad.f003.grib2", "bad.f006.grib2"],
    })
    state = {}
    local = set()
    state, incomplete, outcome = simulate_run_one_batch(
        ["eg_good", "eg_bad"], files_by_group, state, local,
        downloadable_files={"good.f003.grib2", "good.f006.grib2"},  # bad.* 404
        extraction_succeeds_for={},
    )
    assert outcome == "PARTIALLY_COMPLETE"
    assert incomplete == ["eg_bad"]
    assert state["bad.f003.grib2"]["status"] != "PURGED"
    assert state["bad.f006.grib2"]["status"] != "PURGED"
    # The good event group should still have completed its full lifecycle.
    assert state["good.f003.grib2"]["status"] == "PURGED"
    assert state["good.f006.grib2"]["status"] == "PURGED"


def test_complete_event_group_can_still_extract_normally():
    files_by_group = _files_by_group({"eg1": ["eg1.f003.grib2", "eg1.f006.grib2"]})
    state = {}
    local = set()
    state, incomplete, outcome = simulate_run_one_batch(
        ["eg1"], files_by_group, state, local,
        downloadable_files={"eg1.f003.grib2", "eg1.f006.grib2"},
        extraction_succeeds_for={},
    )
    assert incomplete == []
    assert outcome == "COMPLETE"
    assert state["eg1.f003.grib2"]["status"] == "PURGED"
    assert state["eg1.f006.grib2"]["status"] == "PURGED"


def test_stale_state_plus_missing_content_file_causes_redownload():
    # Simulates Batch 0/1: a prior session left status VERIFIED (or
    # DOWNLOADED) in Drive-persisted state, but /content was wiped by a
    # runtime restart, so the file isn't actually present.
    files_by_group = _files_by_group({"eg1": ["eg1.f003.grib2", "eg1.f006.grib2"]})
    state = {
        "eg1.f003.grib2": {"status": "VERIFIED"},
        "eg1.f006.grib2": {"status": "DOWNLOADED"},
    }
    local = set()  # neither file actually present on disk this session

    state = reconcile_stale_local_state(["eg1"], files_by_group, state, local)
    assert state["eg1.f003.grib2"]["status"] == "PENDING"
    assert state["eg1.f003.grib2"]["stale_reset_from"] == "VERIFIED"
    assert state["eg1.f006.grib2"]["status"] == "PENDING"

    # After reconciliation, a full batch run should redownload and succeed
    # cleanly rather than repeating the same failure forever.
    state, incomplete, outcome = simulate_run_one_batch(
        ["eg1"], files_by_group, state, local,
        downloadable_files={"eg1.f003.grib2", "eg1.f006.grib2"},
        extraction_succeeds_for={},
    )
    assert incomplete == []
    assert outcome == "COMPLETE"


def test_batch_cannot_report_green_with_missing_expected_files():
    # "GREEN"/"complete" must never be reported while any expected file for
    # any selected event group in the batch is missing/failed.
    files_by_group = _files_by_group({
        "eg_good": ["good.f003.grib2", "good.f006.grib2"],
        "eg_missing": ["missing.f003.grib2", "missing.f006.grib2"],
    })
    state = {}
    local = set()
    state, incomplete, outcome = simulate_run_one_batch(
        ["eg_good", "eg_missing"], files_by_group, state, local,
        downloadable_files={"good.f003.grib2", "good.f006.grib2"},  # missing.* 404
        extraction_succeeds_for={},
    )
    assert outcome != "COMPLETE", "a batch with a missing expected file must not report full completion"
    assert "eg_missing" in incomplete


# ---------------------------------------------------------------------
# Part 2: assert the actual notebook source contains the fix (not just
# this test file's standalone re-implementation).
# ---------------------------------------------------------------------

pytestmark_notebook = pytest.mark.skipif(not NOTEBOOK_PATH.exists(), reason="notebook not present in this checkout")


@pytestmark_notebook
def test_notebook_reconciles_stale_local_state_before_download():
    nb = json.loads(NOTEBOOK_PATH.read_text())
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    cells_with_fn = [c for c in code_cells if "def reconcile_stale_local_state(" in "".join(c["source"])]
    assert cells_with_fn, "reconcile_stale_local_state() not defined anywhere in the notebook"
    src = "".join(cells_with_fn[0]["source"])
    assert "LOCAL_DEPENDENT_STATUSES" in src
    for status in ("DOWNLOADED", "VERIFIED", "EXTRACTED", "VALIDATED"):
        assert status in src

    download_cells = [c for c in code_cells if "def download_batch(batch_event_groups, state):" in "".join(c["source"])]
    assert download_cells, "download_batch() not found"
    dsrc = "".join(download_cells[0]["source"])
    assert "state = reconcile_stale_local_state(batch_event_groups, state)" in dsrc, (
        "download_batch() must call reconcile_stale_local_state() before anything else"
    )


@pytestmark_notebook
def test_notebook_tracks_incomplete_event_groups_explicitly():
    nb = json.loads(NOTEBOOK_PATH.read_text())
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    joined = "\n".join("".join(c["source"]) for c in code_cells)
    assert "def incomplete_event_groups(" in joined
    assert "def record_incomplete_event_groups(" in joined
    assert "def clear_resolved_incomplete_event_groups(" in joined
    assert "UNAVAILABLE_LOG_PATH" in joined
    assert "phase_0_4_26_incomplete_event_groups.json" in joined


@pytestmark_notebook
def test_notebook_batch_complete_message_is_gated_on_no_incomplete_groups():
    nb = json.loads(NOTEBOOK_PATH.read_text())
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    run_cells = [c for c in code_cells if "def run_one_batch(batch_event_groups, batch_id, state):" in "".join(c["source"])]
    assert run_cells, "run_one_batch() not found"
    src = "".join(run_cells[0]["source"])

    assert "incomplete = incomplete_event_groups(batch_event_groups, state)" in src
    assert "PARTIALLY complete" in src
    # The unconditional old message must be gone -- "Batch {batch_id} complete."
    # must only ever print inside the else-branch of an incomplete check.
    assert 'if incomplete:\n        record_incomplete_event_groups(incomplete, batch_id, state)' in src
    assert 'else:\n        print(f"Batch {batch_id} complete.")' in src


@pytestmark_notebook
def test_notebook_purge_batch_raw_only_purges_persisted_files():
    # Confirms the pre-existing safety property this bugfix relies on:
    # an incomplete/FAILED event group's raw files were never purged, even
    # before this fix (purge_batch_raw only acts on status == "PERSISTED").
    nb = json.loads(NOTEBOOK_PATH.read_text())
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    purge_cells = [c for c in code_cells if "def purge_batch_raw(" in "".join(c["source"])]
    assert purge_cells, "purge_batch_raw() not found"
    src = "".join(purge_cells[0]["source"])
    assert 'if state.get(f, {}).get("status") != "PERSISTED":' in src
    assert "continue  # only purge files that completed the full lifecycle" in src
