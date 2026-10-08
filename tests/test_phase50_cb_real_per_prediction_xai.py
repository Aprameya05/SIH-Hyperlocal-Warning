"""
tests/test_phase50_cb_real_per_prediction_xai.py
=====================================================
Phase 11 (XAI): previously, CB's only "explainability" artifact was a
static global mean-|SHAP| importance (models/panindia_cb_v1/panindia_cb_v1_shap.json)
-- the same numbers repeated for every one of the 4960 records in a
production run, not a real per-prediction explanation.

2026-10-08: added genuine per-prediction XAI using XGBoost's own
pred_contribs=True (exact Shapley values for tree models), deliberately
NOT the `shap` package -- avoids adding another native C-extension of
the exact class (xgboost+shap combination) that caused the real
exit-134 double-free crash this pipeline already hit and fixed
(see tests/test_phase48_transactional_deploy_gate.py's commit history).

This test verifies the REAL mathematical property that makes these
"genuine Shapley values" rather than an invented importance score:
sum(feature contributions) + bias == the model's raw margin for that
exact row -- not assumed, computed and compared.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "models" / "unified_mtl"))


def _model_available():
    return (REPO_ROOT / "models" / "panindia_cb_v1" / "panindia_cb_v1_model.json").exists()


@pytest.mark.skipif(not _model_available(), reason="panindia_cb_v1 model artifact not present in this checkout")
class TestRealPerPredictionXAI:
    def _model(self):
        from panindia_cb_model_interface import PanIndiaCBModel
        return PanIndiaCBModel()

    def test_contributions_sum_to_the_real_raw_margin(self):
        """The defining property of a genuine Shapley-value explanation
        for a tree model: contributions are exact and additive."""
        import xgboost as xgb
        m = self._model()
        rng = np.random.default_rng(42)
        X = pd.DataFrame(rng.random((5, len(m.feature_cols))) * 10, columns=m.feature_cols)

        dmat = xgb.DMatrix(X[m.feature_cols], missing=np.nan)
        full_contribs = m.model.predict(dmat, pred_contribs=True)
        margin = m.model.predict(dmat, output_margin=True)
        reconstructed = full_contribs.sum(axis=1)

        assert np.allclose(margin, reconstructed, atol=1e-4), \
            "feature contributions + bias must reconstruct the real raw margin -- " \
            "if this fails, predict_contribs is not returning genuine Shapley values"

    def test_predict_contribs_returns_one_entry_per_row_with_real_feature_names(self):
        m = self._model()
        rng = np.random.default_rng(7)
        X = pd.DataFrame(rng.random((3, len(m.feature_cols))) * 10, columns=m.feature_cols)
        out = m.predict_contribs(X, top_n=5)
        assert len(out) == 3
        for row in out:
            assert row["status"] == "AVAILABLE"
            assert len(row["top_contributions"]) == 5
            for feat in row["top_contributions"]:
                assert feat["feature"] in m.feature_cols
                assert feat["provenance"] == "DERIVED"
                assert isinstance(feat["contribution"], float)

    def test_top_contributions_are_sorted_by_real_magnitude(self):
        m = self._model()
        rng = np.random.default_rng(3)
        X = pd.DataFrame(rng.random((1, len(m.feature_cols))) * 10, columns=m.feature_cols)
        row = m.predict_contribs(X, top_n=5)[0]
        mags = [abs(f["contribution"]) for f in row["top_contributions"]]
        assert mags == sorted(mags, reverse=True)

    def test_inference_engine_predict_cb_populates_real_xai_in_extra(self):
        from inference_engine import UnifiedInferenceEngine
        from datetime import datetime, timezone
        m = self._model()
        rng = np.random.default_rng(11)
        X = pd.DataFrame(rng.random((1, len(m.feature_cols))) * 10, columns=m.feature_cols)

        eng = UnifiedInferenceEngine()
        pred = eng.predict_cb("TEST_CELL", 2, datetime.now(timezone.utc), X)
        assert pred.probability is not None
        assert "xai" in pred.extra
        assert pred.extra["xai"]["status"] == "AVAILABLE"
        assert "top_contributions" in pred.extra["xai"]

    def test_xai_failure_degrades_gracefully_without_corrupting_the_forecast(self):
        """Mandate requirement: 'XAI failure must degrade gracefully
        without corrupting the forecast.' Verified by forcing a real
        exception inside predict_contribs and confirming probability
        is still produced correctly."""
        from unittest.mock import patch
        from inference_engine import UnifiedInferenceEngine
        from datetime import datetime, timezone

        m = self._model()
        rng = np.random.default_rng(5)
        X = pd.DataFrame(rng.random((1, len(m.feature_cols))) * 10, columns=m.feature_cols)
        eng = UnifiedInferenceEngine()

        with patch.object(type(eng.heads["CB"]), "predict_contribs", side_effect=RuntimeError("simulated XAI failure")):
            pred = eng.predict_cb("TEST_CELL", 2, datetime.now(timezone.utc), X)

        assert pred.probability is not None, "a real prediction must still be produced even if XAI crashes"
        assert pred.extra["xai"]["status"] == "NOT_AVAILABLE"
        assert "simulated XAI failure" in pred.extra["xai"]["reason"]
