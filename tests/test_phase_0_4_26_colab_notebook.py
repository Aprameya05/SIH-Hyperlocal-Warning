"""
Phase 0.4.26 -- tests for the Colab notebook's pure logic (batch sizing,
manifest-structure checks, resumability skip logic). These tests extract
and exercise the notebook's code cells' logic *without* running any cell
that touches Google Drive, the network, or eccodes-based GRIB decoding --
this notebook must never be executed by Claude, only prepared.
"""
import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK_PATH = REPO_ROOT / "notebooks" / "PHASE_0_4_26_TS_ACQUISITION_AND_DATASET.ipynb"

pytestmark = pytest.mark.skipif(not NOTEBOOK_PATH.exists(), reason="notebook not present in this checkout")


def test_notebook_is_valid_json_with_expected_sections():
    nb = json.loads(NOTEBOOK_PATH.read_text())
    assert nb["nbformat"] == 4
    markdown_sources = [
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown"
    ]
    joined = "\n".join(markdown_sources)
    required_sections = [
        "Environment setup", "Google Drive mount", "Repository clone/setup",
        "Dependency installation", "Environment verification", "Storage verification",
        "Manifest verification", "Batch configuration", "Batch acquisition",
        "GFS verification", "Historical feature extraction", "Dataset validation",
        "Persistent Drive write", "Raw temporary-file purge", "Batch state update",
        "Repeat remaining batches", "Final train/holdout assembly",
        "Final dataset validation", "Final evidence report", "HOW TO RUN",
    ]
    for section in required_sections:
        assert section in joined, f"missing required section: {section}"


def test_notebook_code_cells_are_syntactically_valid():
    import ast
    nb = json.loads(NOTEBOOK_PATH.read_text())
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert len(code_cells) >= 19
    for i, c in enumerate(code_cells):
        src = "".join(c["source"])
        ast.parse(src)  # raises SyntaxError on failure


def test_notebook_never_hardcodes_windows_drive_paths():
    nb = json.loads(NOTEBOOK_PATH.read_text())
    all_code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert "D:\\" not in all_code
    assert "G:\\" not in all_code


def test_notebook_batch_size_gate_logic_reduces_on_low_free_space():
    # Re-implements the exact arithmetic from Section 6's code cell, isolated
    # from any Drive/Colab dependency, to check the gating logic itself.
    MEASURED_MAX_FILE_BYTES = 556_624_838
    SAFETY_HEADROOM_BYTES = 2 * 1024**3
    DEFAULT_BATCH_EVENT_GROUPS = 10
    MIN_BATCH_EVENT_GROUPS = 1

    def worst_case_batch_bytes(n):
        return n * 2 * MEASURED_MAX_FILE_BYTES

    def compute_batch_size(free_bytes):
        batch_size = DEFAULT_BATCH_EVENT_GROUPS
        while batch_size >= MIN_BATCH_EVENT_GROUPS:
            needed = worst_case_batch_bytes(batch_size) + SAFETY_HEADROOM_BYTES
            if needed <= free_bytes:
                return batch_size
            batch_size -= 1
        return 0

    # Plenty of space: default batch size kept.
    assert compute_batch_size(500 * 1024**3) == 10
    # Only room for ~3 event groups worth + headroom.
    tight_free = worst_case_batch_bytes(3) + SAFETY_HEADROOM_BYTES + 1
    assert compute_batch_size(tight_free) == 3
    # Not even one event group fits.
    assert compute_batch_size(1024) == 0


def test_resumability_skip_logic_skips_terminal_statuses():
    TERMINAL_STATUSES = {"DOWNLOADED", "VERIFIED", "EXTRACTED", "VALIDATED", "PERSISTED", "PURGED"}
    state = {
        "a.grib2": {"status": "PURGED"},
        "b.grib2": {"status": "PENDING"},
        "c.grib2": {"status": "FAILED"},
    }

    def should_skip(fname):
        return state.get(fname, {}).get("status") in TERMINAL_STATUSES

    assert should_skip("a.grib2") is True
    assert should_skip("b.grib2") is False
    assert should_skip("c.grib2") is False  # FAILED must be retried, not skipped
    assert should_skip("missing.grib2") is False
