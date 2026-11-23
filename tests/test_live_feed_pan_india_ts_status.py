"""drift_live.json must reflect the published pan-India TS artifact, not a hardcoded 'unavailable' claim."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from pipelines.publishing import build_live_feed as blf  # noqa: E402


def _build_with(monkeypatch, ts_doc):
    real = blf._load

    def fake(p):
        return ts_doc if p == "data/india_ts_model_latest.json" else real(p)

    monkeypatch.setattr(blf, "_load", fake)
    return blf.build()["hazards"]["thunderstorm"]


def test_available_when_artifact_is_evaluated_and_calibrated(monkeypatch):
    h = _build_with(monkeypatch, {"meta": {"model_evaluated": True, "calibrated": True, "model_version": "ts_panindia_xgb_v1",
                                           "cycle_init_utc": "2026-11-23T00:00:00Z", "leads": [6], "evaluation_ref": "x"}})
    assert h["pan_india_model"] == "EXPERIMENTAL_PRE_MONSOON_VALIDATION"
    assert "Apr-Jun" in h["pan_india_model_detail"]["limits"]
    assert "No pan-India thunderstorm probability exists" not in h["summary"]


def test_unavailable_when_artifact_missing_or_not_evaluated(monkeypatch):
    for doc in (None, {}, {"meta": {"model_evaluated": False, "calibrated": True}}):
        h = _build_with(monkeypatch, doc)
        assert h["pan_india_model"] == "UNAVAILABLE"
        assert h["pan_india_model_detail"] is None
