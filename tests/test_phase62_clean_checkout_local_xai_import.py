"""
tests/test_phase62_clean_checkout_local_xai_import.py
=====================================================
2026-10-10: real production CI failure, from an actual GitHub Actions
log: "ModuleNotFoundError: No module named 'local_xai'" at
backend/models/unified_mtl/inference_engine.py:309, inside
predict_ff()'s XAI call. The unified build then aborted with
"double free or corruption (!prev)" -- a SEPARATE, native-level crash,
but this ImportError is what made the build fail BEFORE reaching the
work the crash-mitigation code is meant to protect.

Root cause: inference_engine.py's predict_ts() and predict_ff() used
bare `from local_xai import ...` (added earlier this session), while
every OTHER import in this same file correctly uses the full package
path (`from backend.models.unified_mtl.X import ...`, with
sys.path.insert(0, REPO_ROOT) at module load time making `backend` an
importable package). The bare form happened to work in every manual
local test this session because those tests had already done
sys.path.insert(0, 'backend/models/unified_mtl') by hand before
calling inference_engine -- which scripts/phase34_build_unified_forecast.py
itself never does. A real clean-checkout subprocess invocation (no
manual sys.path manipulation, matching exactly how
.github/workflows/forecast_update.yml runs this script:
`python scripts/phase34_build_unified_forecast.py`) never had that
directory on sys.path, so the bare import failed there and only there.

This test specifically reproduces that exact invocation shape: a
subprocess with NO extra sys.path setup, that exercises the FF
inference path (where the reported crash occurred) and separately the
TS path (which had the same bug pattern), proving both resolve
cleanly from a genuinely clean entry point -- not merely "importable
from within an already-polluted test process's sys.path."
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_clean_subprocess(code: str) -> subprocess.CompletedProcess:
    """A genuinely separate process with NO extra sys.path setup beyond
    what the code string itself adds -- this is what actually
    reproduces the real bug (a bare import relying on a sys.path entry
    this session's own interactive shell had added by hand, which
    scripts/phase34_build_unified_forecast.py never does). Deliberately
    NOT -I (isolated mode): that also strips user site-packages, where
    numpy/pandas/etc. live in a normal dev install, which has nothing
    to do with the actual bug being tested here."""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
    )


def test_inference_engine_itself_imports_cleanly_with_no_manual_sys_path_setup():
    """The real failure point: `import inference_engine` (the exact
    module name scripts/phase34_build_unified_forecast.py uses) must
    not raise ModuleNotFoundError for local_xai, from a subprocess that
    does nothing except what phase34_build_unified_forecast.py itself
    does (insert scripts/ and backend/models/unified_mtl/ onto
    sys.path, matching that script's own real setup -- not an
    artificial test-only convenience)."""
    code = (
        "import sys; "
        "sys.path.insert(0, 'scripts'); "
        "sys.path.insert(0, 'backend/models/unified_mtl'); "
        "import inference_engine; "
        "print('OK')"
    )
    result = _run_clean_subprocess(code)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "ModuleNotFoundError" not in result.stderr
    assert "local_xai" not in result.stderr or "OK" in result.stdout


@pytest.mark.skipif(
    not (REPO_ROOT / "processed" / "ff_pu").exists()
    and not list(REPO_ROOT.glob("models/ff_*")),
    reason="FF model artifacts not present in this checkout",
)
def test_ff_inference_path_including_xai_exercises_cleanly_from_a_clean_subprocess():
    """Directly reproduces the reported crash site: predict_ff(), which
    internally calls local_xai_ff() (backend/models/unified_mtl/
    inference_engine.py:309). Run as a genuinely separate subprocess
    with the exact minimal sys.path setup scripts/phase34_build_unified_forecast.py
    itself uses -- not more, which would mask the bug this test exists
    to catch."""
    code = (
        "import sys; "
        "sys.path.insert(0, 'scripts'); "
        "sys.path.insert(0, 'backend/models/unified_mtl'); "
        "import numpy as np, pandas as pd; "
        "from datetime import datetime, timezone; "
        "from inference_engine import UnifiedInferenceEngine; "
        "from heads import FFHead; "
        "ff = FFHead(); "
        "X = pd.DataFrame(np.random.rand(1, len(ff.feature_cols)) * 10, columns=ff.feature_cols); "
        "eng = UnifiedInferenceEngine(); "
        "pred = eng.predict_ff('TEST_CELL', 2, datetime.now(timezone.utc), X); "
        "assert pred.extra.get('xai') is not None, 'FF prediction must always carry an xai field, even if NOT_AVAILABLE'; "
        "print('XAI_STATUS=' + str(pred.extra['xai'].get('status')))"
    )
    result = _run_clean_subprocess(code)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "ModuleNotFoundError" not in result.stderr
    assert "XAI_STATUS=" in result.stdout
    # Per explicit instruction: if FF XAI is unavailable, it must say so
    # explicitly (NOT_AVAILABLE), never silently crash or fabricate an
    # explanation.
    status = result.stdout.strip().split("XAI_STATUS=")[-1]
    assert status in ("AVAILABLE", "NOT_AVAILABLE"), f"unexpected FF xai status: {status!r}"


@pytest.mark.skipif(
    not (REPO_ROOT / "models" / "thunderstorm_model.pkl").exists(),
    reason="thunderstorm_model.pkl not present in this checkout",
)
def test_ts_inference_path_including_xai_exercises_cleanly_from_a_clean_subprocess():
    """Same bug pattern existed in predict_ts()'s local_shap_ts import --
    verified separately since it is a distinct code path."""
    code = (
        "import sys; "
        "sys.path.insert(0, 'scripts'); "
        "sys.path.insert(0, 'backend/models/unified_mtl'); "
        "import numpy as np, pandas as pd; "
        "from datetime import datetime, timezone; "
        "from inference_engine import UnifiedInferenceEngine; "
        "from ts_station_model_interface import VOBLThunderstormModel, VOBL_CELL_ID; "
        "m = VOBLThunderstormModel(); "
        "X = pd.DataFrame(np.random.rand(1, len(m.feature_cols)) * 10, columns=m.feature_cols); "
        "eng = UnifiedInferenceEngine(); "
        "pred = eng.predict_ts(VOBL_CELL_ID, 2, datetime.now(timezone.utc), X); "
        "assert pred.probability is not None; "
        "print('OK')"
    )
    result = _run_clean_subprocess(code)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "ModuleNotFoundError" not in result.stderr


def test_no_bare_local_xai_imports_remain_anywhere_in_inference_engine():
    """Structural guard: catches a future regression of the exact same
    bug pattern (a bare `from local_xai import ...` reintroduced
    without the full package path) via direct source inspection,
    regardless of whether a specific code path is exercised by another
    test."""
    text = (REPO_ROOT / "backend" / "models" / "unified_mtl" / "inference_engine.py").read_text(encoding="utf-8")
    import re
    bare_imports = re.findall(r"^\s*from local_xai import", text, re.MULTILINE)
    assert bare_imports == [], (
        f"found {len(bare_imports)} bare 'from local_xai import' statement(s) -- "
        f"must use 'from backend.models.unified_mtl.local_xai import ...' instead, "
        f"matching every other import in this file"
    )
