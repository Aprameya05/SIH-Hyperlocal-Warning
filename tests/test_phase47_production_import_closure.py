"""
tests/test_phase47_production_import_closure.py
====================================================
2026-10-08: regression test for a real production outage. A real
scheduled GitHub Actions run (clean checkout) failed with:

    ModuleNotFoundError: No module named 'imd_rainfall_adapter'

at scripts/ff_feature_adapter.py:205, inside
scripts/phase34_build_unified_forecast.py's [3/7] FF-loading step --
even though the file existed and worked locally. Root cause:
scripts/imd_rainfall_adapter.py (and scripts/ci_validate_unified_artifact.py,
scripts/ci_smoke_test_deployed_site.py, both directly invoked by
.github/workflows/forecast_update.yml) had never actually been
committed to git -- they existed only in local working trees from
prior sessions. "Works on my machine" was never sufficient evidence;
only a clean git checkout proves what the CI runner actually sees.

This test spawns a REAL subprocess with a repo checked out from git
(via `git archive` into a temp dir -- a clean export of exactly what
HEAD has committed, no working-tree leftovers) and imports the
production entry point from there, so a future "works locally, 404s
on a clean checkout" regression fails here first.
"""
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def clean_checkout(tmp_path_factory):
    """A real export of exactly what's committed to HEAD -- not the
    working tree, which may have uncommitted files that mask a missing
    file on a genuinely clean checkout."""
    dest = tmp_path_factory.mktemp("clean_checkout")
    archive_path = dest / "archive.tar"
    subprocess.run(
        ["git", "archive", "--format=tar", "-o", str(archive_path), "HEAD"],
        cwd=REPO_ROOT, check=True, capture_output=True,
    )
    with tarfile.open(archive_path) as tf:
        tf.extractall(dest)
    archive_path.unlink()
    return dest


def _run_in_checkout(checkout_dir: Path, code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(checkout_dir), capture_output=True, text=True, timeout=60,
    )


def test_ff_feature_adapter_imports_cleanly_from_a_clean_checkout(clean_checkout):
    """Direct regression for the real outage: this exact import, from a
    clean checkout, must not raise ModuleNotFoundError."""
    result = _run_in_checkout(clean_checkout, "import sys; sys.path.insert(0, 'scripts'); import ff_feature_adapter")
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert "ModuleNotFoundError" not in result.stderr


def test_imd_rainfall_adapter_is_committed_and_importable(clean_checkout):
    assert (clean_checkout / "scripts" / "imd_rainfall_adapter.py").exists(), \
        "scripts/imd_rainfall_adapter.py must be committed to git (confirmed missing during the real outage)"
    result = _run_in_checkout(clean_checkout, "import sys; sys.path.insert(0, 'scripts'); import imd_rainfall_adapter")
    assert result.returncode == 0, f"stderr:\n{result.stderr}"


def test_ci_scripts_referenced_by_the_workflow_are_actually_committed(clean_checkout):
    """scripts/ci_validate_unified_artifact.py and
    scripts/ci_smoke_test_deployed_site.py are invoked directly by
    .github/workflows/forecast_update.yml -- both were found missing
    from git during the same outage ('No such file or directory' on
    the runner)."""
    for rel_path in ("scripts/ci_validate_unified_artifact.py", "scripts/ci_smoke_test_deployed_site.py"):
        assert (clean_checkout / rel_path).exists(), (
            f"{rel_path} is referenced by forecast_update.yml but is not committed to git"
        )


def test_phase34_build_unified_forecast_itself_imports_cleanly(clean_checkout):
    """The actual production entry point, imported (not executed --
    executing it would do a real live GFS fetch) exactly as Python
    would resolve it when invoked as `python scripts/phase34_build_unified_forecast.py`
    from the repo root, matching forecast_update.yml's invocation."""
    code = (
        "import sys; sys.path.insert(0, '.'); sys.path.insert(0, 'scripts'); "
        "import importlib.util; "
        "spec = importlib.util.spec_from_file_location('phase34', 'scripts/phase34_build_unified_forecast.py'); "
        "mod = importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(mod)"
    )
    result = _run_in_checkout(clean_checkout, code)
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert "ModuleNotFoundError" not in result.stderr
