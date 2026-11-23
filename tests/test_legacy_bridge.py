import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_legacy_bridge_node_suite():
    r = subprocess.run(["node", "--test", "tests/test_legacy_bridge.mjs"], cwd=REPO, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
