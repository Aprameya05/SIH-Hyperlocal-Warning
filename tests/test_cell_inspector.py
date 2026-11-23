import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_cell_inspector_node_suite():
    r = subprocess.run(["node", "--test", "tests/test_cell_inspector.mjs"], cwd=REPO, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-3500:] + r.stderr[-2000:]
