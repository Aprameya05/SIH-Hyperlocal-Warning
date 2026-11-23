"""Runs the Cloudflare Worker security tests with Node's built-in test runner."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required to test worker/index.js")
def test_worker_security_suite():
    r = subprocess.run(["node", "--test", str(REPO / "tests" / "worker" / "worker_security.test.mjs")], capture_output=True, text=True,
                       cwd=REPO, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
