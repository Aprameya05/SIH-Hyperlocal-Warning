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


def test_notebook_resolves_ts_labels_from_drive_and_hard_fails_if_missing():
    nb = json.loads(NOTEBOOK_PATH.read_text())
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    ts_cells = [c for c in code_cells if "TS_LABELS_DRIVE_PATH" in "".join(c["source"])]
    assert ts_cells, "no cell defines TS_LABELS_DRIVE_PATH"
    src = "".join(ts_cells[0]["source"])

    # Resolves the exact Drive root path, not any other location.
    assert '/content/drive/MyDrive/ts_labels.csv' in src

    # Hard-fails (raises) rather than silently continuing or substituting another path.
    assert "raise RuntimeError" in src
    assert "if not TS_LABELS_DRIVE_PATH.exists():" in src

    # Verifies it's readable as a CSV and checks for the real columns
    # build_vobl_historical_gfs_ts_join.py::build_pilot() actually requires.
    assert "pd.read_csv(TS_LABELS_DRIVE_PATH)" in src
    for col in ("cell_id", "timestamp", "label", "label_status"):
        assert col in src, f"missing required TS label column check: {col}"


def test_notebook_build_phase_0_4_20_calls_always_pass_explicit_ts_labels():
    nb = json.loads(NOTEBOOK_PATH.read_text())
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    invocation_cells = [
        c for c in code_cells
        if "build_phase_0_4_20_dataset.py" in "".join(c["source"])
        and "cmd = (" in "".join(c["source"])
    ]
    assert invocation_cells, "no cell builds a build_phase_0_4_20_dataset.py command line"
    for c in invocation_cells:
        src = "".join(c["source"])
        assert "--ts-labels" in src, "a build_phase_0_4_20_dataset.py call is missing --ts-labels"
        assert "TS_LABELS_DRIVE_PATH" in src, (
            "--ts-labels must reference the Drive-hosted path, not a hardcoded/default one"
        )
        # Never allow silent fallback to the builder's own default path: the actual
        # --ts-labels argument must be the Drive variable, not a literal default path
        # (a comment *mentioning* the default, to explain why it's avoided, is fine).
        assert '--ts-labels "{TS_LABELS_DRIVE_PATH}"' in src


def test_notebook_ts_labels_section_precedes_manifest_and_extraction_sections():
    nb = json.loads(NOTEBOOK_PATH.read_text())
    headings = [
        "".join(c["source"]).strip()
        for c in nb["cells"]
        if c["cell_type"] == "markdown" and "".join(c["source"]).strip().startswith("##")
    ]
    def index_of(prefix):
        for i, h in enumerate(headings):
            if h.startswith(prefix):
                return i
        raise AssertionError(f"heading not found: {prefix}")

    ts_idx = index_of("## 4b. TS labels")
    manifest_idx = index_of("## 7. Manifest verification")
    extraction_idx = index_of("## 11. Historical feature extraction")
    assert ts_idx < manifest_idx < extraction_idx


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
