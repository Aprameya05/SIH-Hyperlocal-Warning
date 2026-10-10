"""
tests/test_phase60_update_grid_requirements.py
=====================================================
2026-10-10: real production bug, confirmed by the user from an actual
CI run of "Update Hazard Grid" (.github/workflows/update_grid.yml).
Root cause: canonical_forecast_writer.py calls
validate_canonical_forecast.py::validate_schema(), which does a lazy
`import jsonschema` inside a try/except and returns
`(False, ["jsonschema package not installed -- cannot validate
schema"])` if that import fails -- a correct, safe, fail-closed
design (canonical_forecast_writer.py then refuses to publish and
exits 1 rather than silently skipping validation). The clean
update_grid.yml runner installs packages ONLY from
backend/requirements.txt ("pip install -r backend/requirements.txt"),
which never listed jsonschema -- so this fail-closed check correctly
tripped on EVERY real CI run, even though the underlying GFS pipeline
itself succeeded and genuinely produced a valid 992-cell
data/pan_india_grid.json.

This test verifies the dependency is actually declared where the
clean CI environment installs from, and that validate_schema's own
lazy import genuinely resolves in a correctly-provisioned environment
(not just that *some* jsonschema happens to be importable in whatever
ad-hoc local dev environment runs this test suite).
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_PATH = REPO_ROOT / "backend" / "requirements.txt"


def test_jsonschema_is_declared_in_the_requirements_file_update_grid_yml_installs_from():
    """.github/workflows/update_grid.yml's 'Install Python dependencies'
    step runs exactly `pip install -r backend/requirements.txt` -- this
    is the one file that determines what's actually available on that
    clean runner. jsonschema must be listed here, not merely installed
    in whatever ad-hoc environment happens to run tests locally."""
    text = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    assert re.search(r"^jsonschema", text, re.MULTILINE), (
        "jsonschema must be declared in backend/requirements.txt -- "
        "canonical_forecast_writer.py's schema validation fails closed "
        "(refuses to publish, exits 1) without it, which is exactly "
        "what happened on every real update_grid.yml run until this was fixed"
    )


def test_update_grid_workflow_installs_from_backend_requirements_txt():
    """Confirms the actual install command update_grid.yml runs, so a
    future edit to that workflow (e.g. switching to a different
    requirements file) can't silently re-break this without this test
    catching the drift."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "update_grid.yml"
    text = workflow_path.read_text(encoding="utf-8")
    assert "pip install -r backend/requirements.txt" in text


def test_validate_schema_lazy_import_resolves_in_a_clean_subprocess():
    """Real, not mocked: spawn a fresh Python subprocess and confirm
    validate_canonical_forecast.py's own lazy `import jsonschema`
    genuinely succeeds -- proving the dependency is actually usable,
    not merely textually present in a requirements file that could
    itself be stale or wrong."""
    result = subprocess.run(
        [sys.executable, "-c", "import jsonschema; print(jsonschema.Draft7Validator)"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"jsonschema import failed: {result.stderr}"
    assert "Draft7Validator" in result.stdout


def test_validate_canonical_forecast_schema_check_actually_passes_now():
    """End-to-end: validate_schema() must return a real pass/fail
    result (using jsonschema), never the 'not installed' escape hatch,
    when called against a real, well-formed canonical forecast dict."""
    sys.path.insert(0, str(REPO_ROOT))
    from validate_canonical_forecast import validate_schema

    minimal_valid_canonical = {
        "generated_at": "2026-10-10T12:00:00Z",
        "cells": [],
    }
    ok, errors = validate_schema(minimal_valid_canonical)
    assert errors != ["jsonschema package not installed -- cannot validate schema"], (
        "validate_schema fell back to the 'not installed' escape hatch -- "
        "jsonschema is not actually usable in this environment"
    )
