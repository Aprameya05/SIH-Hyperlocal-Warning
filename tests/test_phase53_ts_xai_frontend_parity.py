"""
tests/test_phase53_ts_xai_frontend_parity.py
=====================================================
2026-10-08: while checking whether TS has the same real per-prediction
XAI CB was just given (tests/test_phase50_cb_real_per_prediction_xai.py,
tests/test_phase51_local_xai_no_shap_dependency.py), found TS's
HazardPrediction.extra had no "xai" key at all in the batch artifact
path (backend/models/unified_mtl/inference_engine.py::predict_ts) --
only the live /forecast endpoint (backend/unified_api.py) had wired
local_shap_ts for TS. Fixed by reusing local_shap_ts directly inside
predict_ts (same function the live endpoint already uses, not a
duplicate implementation) and promoting it to scripts/phase34_build_
unified_forecast.py's TS record as a top-level "xai" key, matching how
CB's fix already did this.
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


def _model_available():
    return (REPO_ROOT / "models" / "thunderstorm_model.pkl").exists()


@pytest.mark.skipif(not _model_available(), reason="thunderstorm_model.pkl not present in this checkout")
class TestTSXAIFrontendParity:
    def test_predict_ts_populates_real_xai_matching_cb_contract(self):
        from inference_engine import UnifiedInferenceEngine
        from ts_station_model_interface import VOBLThunderstormModel, VOBL_CELL_ID

        eng = UnifiedInferenceEngine()
        m = VOBLThunderstormModel()
        rng = np.random.default_rng(9)
        X = pd.DataFrame(rng.random((1, len(m.feature_cols))) * 10, columns=m.feature_cols)

        pred = eng.predict_ts(VOBL_CELL_ID, 2, datetime.now(timezone.utc), X)
        assert pred.probability is not None
        xai = pred.extra.get("xai")
        assert xai is not None
        assert xai["status"] == "AVAILABLE"
        assert len(xai["top_contributions"]) > 0
        for c in xai["top_contributions"]:
            assert c["feature"] in m.feature_cols
            assert c["provenance"] == "DERIVED"
            assert isinstance(c["contribution"], float)

    def test_xai_failure_degrades_gracefully_without_corrupting_the_forecast(self):
        from unittest.mock import patch
        from inference_engine import UnifiedInferenceEngine
        from ts_station_model_interface import VOBLThunderstormModel, VOBL_CELL_ID
        import local_xai

        eng = UnifiedInferenceEngine()
        m = VOBLThunderstormModel()
        rng = np.random.default_rng(4)
        X = pd.DataFrame(rng.random((1, len(m.feature_cols))) * 10, columns=m.feature_cols)

        with patch.object(local_xai, "local_shap_ts", side_effect=RuntimeError("simulated XAI failure")):
            pred = eng.predict_ts(VOBL_CELL_ID, 2, datetime.now(timezone.utc), X)

        assert pred.probability is not None, "a real prediction must still be produced even if XAI crashes"
        assert pred.extra["xai"]["status"] == "NOT_AVAILABLE"
        assert "simulated XAI failure" in pred.extra["xai"]["reason"]

    def test_out_of_domain_cell_has_no_xai_key_and_does_not_crash(self):
        """A non-VOBL cell never reaches the XAI code path at all (TS is
        STATION_BASELINE, refuses to extrapolate) -- .get('xai') on that
        extra dict must return None cleanly, matching how
        scripts/phase34_build_unified_forecast.py reads it."""
        from inference_engine import UnifiedInferenceEngine

        eng = UnifiedInferenceEngine()
        pred = eng.predict_ts("SOME_OTHER_CELL", 2, datetime.now(timezone.utc), None)
        assert pred.status == "OUT_OF_DOMAIN_STATION_ONLY"
        assert pred.extra.get("xai") is None
