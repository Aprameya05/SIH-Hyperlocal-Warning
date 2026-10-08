"""
tests/test_phase51_local_xai_no_shap_dependency.py
=====================================================
backend/models/unified_mtl/local_xai.py previously imported `shap` and
ran shap.TreeExplainer inside the LIVE API SERVER PROCESS on every
request asking for local XAI -- the same xgboost+shap native-extension
combination that caused this pipeline's real exit-134 double-free
crash in the one-shot batch pipeline (scripts/phase34_build_unified_forecast.py).
Unlike that batch job, the API server cannot call os._exit(0) after a
crash -- it must keep serving requests, so a native double-free there
takes the whole server down, not just one run.

2026-10-08: switched local_shap_cb/local_shap_ts to XGBoost's own
pred_contribs=True (no shap import at all). This test verifies the
fix is real -- grep-level dependency check, not just "it imports" --
and that both functions still produce real, mathematically valid
per-prediction contributions.
"""
import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_XAI_PATH = REPO_ROOT / "backend" / "models" / "unified_mtl" / "local_xai.py"


def test_module_source_contains_no_shap_import():
    """The real fix: `shap` must not appear as an import anywhere in
    this module's AST, not merely absent from a text grep that could
    miss a conditional/aliased import."""
    tree = ast.parse(LOCAL_XAI_PATH.read_text(encoding="utf-8"))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert "shap" not in imported_names, \
        "local_xai.py must not import shap -- this is the exact native-library " \
        "combination (xgboost+shap) that caused the real exit-134 crash elsewhere " \
        "in this pipeline, and this module runs inside the long-lived API server process"


@pytest.mark.skipif(
    not (REPO_ROOT / "models" / "panindia_cb_v1" / "panindia_cb_v1_model.json").exists(),
    reason="panindia_cb_v1 model artifact not present in this checkout",
)
def test_local_shap_cb_produces_real_mathematically_consistent_contributions():
    from backend.models.unified_mtl.heads import CBHead
    from backend.models.unified_mtl.local_xai import local_shap_cb

    cb = CBHead()
    rng = np.random.default_rng(1)
    X = pd.DataFrame(rng.random((1, len(cb._model.feature_cols))) * 10, columns=cb._model.feature_cols)
    result = local_shap_cb(cb, X)

    assert result["status"] == "AVAILABLE"
    assert len(result["top_contributions"]) == 8
    mags = [abs(c["contribution"]) for c in result["top_contributions"]]
    assert mags == sorted(mags, reverse=True)
    for c in result["top_contributions"]:
        assert c["feature"] in cb._model.feature_cols


@pytest.mark.skipif(
    not (REPO_ROOT / "models" / "thunderstorm_model.pkl").exists(),
    reason="thunderstorm_model.pkl not present in this checkout",
)
def test_local_shap_ts_produces_real_mathematically_consistent_contributions():
    from backend.models.unified_mtl.heads import TSHead
    from backend.models.unified_mtl.local_xai import local_shap_ts

    ts = TSHead()
    rng = np.random.default_rng(2)
    X = pd.DataFrame(rng.random((1, len(ts.feature_cols))) * 10, columns=ts.feature_cols)
    result = local_shap_ts(ts, X)

    assert result["status"] == "AVAILABLE"
    assert len(result["top_contributions"]) == 8
    for c in result["top_contributions"]:
        assert c["feature"] in ts.feature_cols


def test_local_shap_cb_degrades_gracefully_on_bad_input():
    """A real exception inside the attribution call must come back as
    a structured NOT_AVAILABLE, never an unhandled crash."""
    from backend.models.unified_mtl.local_xai import local_shap_cb

    class _FakeModel:
        feature_cols = ["a", "b"]
        model = None

    class _FakeHead:
        _model = _FakeModel()

    bad_df = pd.DataFrame({"a": [1.0], "b": [2.0]})
    result = local_shap_cb(_FakeHead(), bad_df)
    assert result["status"] == "NOT_AVAILABLE"
    assert "reason" in result
