#!/usr/bin/env python3
"""
test_panindia_dataset.py -- Phase 5 test-harness wrapper.

Runs scripts/validate_panindia_dataset.py's real checks against the real,
already-built dataset partitions and reports pass/fail counts in this
repo's existing test style (plain script, not pytest).

Run: python3 tests/test_panindia_dataset.py   (from repo root)
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_panindia_dataset as v  # noqa: E402

if __name__ == "__main__":
    rc = v.main()
    print(f"\n[test_panindia_dataset.py] delegated to validate_panindia_dataset.py: "
          f"{v.PASS} passed, {v.FAIL} failed")
    sys.exit(rc)
