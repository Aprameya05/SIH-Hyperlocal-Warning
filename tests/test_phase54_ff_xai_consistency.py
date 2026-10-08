"""
tests/test_phase54_ff_xai_consistency.py
=====================================================
2026-10-08: TS and CB both populate a top-level "xai" field in their
batch-artifact records (see test_phase50/51/53). FF was missing the
same key for interface consistency -- not because it needs a real
attribution (none is scientifically valid for the PU-logistic model,
per backend/models/unified_mtl/local_xai.py's module docstring: no
TreeExplainer story for a LogisticRegression, no validated
LinearExplainer background dataset), but so that any code iterating
TS/CB/FF uniformly for an "xai" field gets an honest NOT_AVAILABLE
instead of a missing key.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "models" / "unified_mtl"))


def _ff_model_available():
    from heads import FFHead
    try:
        FFHead()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ff_model_available(), reason="FF model artifact not present in this checkout")
def test_predict_ff_populates_an_honest_not_available_xai():
    from inference_engine import UnifiedInferenceEngine
    from heads import FFHead

    ff = FFHead()
    rng = np.random.default_rng(6)
    X = pd.DataFrame(rng.random((1, len(ff.feature_cols))) * 10, columns=ff.feature_cols)

    eng = UnifiedInferenceEngine()
    pred = eng.predict_ff("TEST_CELL", 2, datetime.now(timezone.utc), X)
    xai = pred.extra.get("xai")
    assert xai is not None
    assert xai["status"] == "NOT_AVAILABLE"
    assert "reason" in xai
    assert "PU-logistic" in xai["reason"] or "SHAP" in xai["reason"]
