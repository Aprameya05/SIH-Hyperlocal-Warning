"""Runs tests/test_m0_frontend_integrity.mjs under pytest so the frontend guarantees are part of the normal suite."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_m0_frontend_integrity_node_suite_passes():
    r = subprocess.run(["node", str(ROOT / "tests" / "test_m0_frontend_integrity.mjs")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
    assert "ALL TESTS PASSED" in r.stdout
