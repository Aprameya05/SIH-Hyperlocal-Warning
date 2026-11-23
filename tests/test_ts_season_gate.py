"""The pan-India TS model was evaluated on Apr-Jun only; other seasons must be published as unavailable."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
spec = importlib.util.spec_from_file_location("predict_ts", REPO / "models" / "thunderstorm" / "predict_thunderstorm.py")
pts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pts)


def utc(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_in_season_leads():
    assert pts.in_validated_season(utc(2026, 5, 10, 0), 6)
    assert pts.in_validated_season(utc(2026, 4, 1, 0), 24)


def test_out_of_season_leads():
    for init in (utc(2026, 11, 23, 0), utc(2026, 1, 5, 12), utc(2026, 7, 15, 0), utc(2026, 9, 1, 0)):
        assert not pts.in_validated_season(init, 6)


def test_boundary_month_crossing_is_excluded():
    assert not pts.in_validated_season(utc(2026, 6, 30, 12), 24)      # valid time lands in July
    assert not pts.in_validated_season(utc(2026, 3, 31, 12), 24)      # init is in March


def _load(path, name):
    sp = importlib.util.spec_from_file_location(name, REPO / path)
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


def test_gate_months_cover_exactly_the_training_dates_of_each_model():
    """The validated months are derived from the dataset builders' own init dates, not typed from memory."""
    ts_dates = _load("pipelines/feature_engineering/build_thunderstorm_training_dataset.py", "ts_ds").dates()
    ts_months = {d.month for d in ts_dates}
    assert ts_months == set(pts.VALIDATED_MONTHS), ts_months
    rain_dates = _load("pipelines/feature_engineering/build_rainfall_training_dataset.py", "rain_ds").dates()
    rp = _load("models/extreme_rainfall/predict_extreme_rainfall.py", "rain_pred")
    assert {d.month for d in rain_dates} == set(rp.VALIDATED_MONTHS)


def test_gate_runs_before_any_probability_is_published():
    src = (REPO / "models" / "thunderstorm" / "predict_thunderstorm.py").read_text(encoding="utf-8")
    assert src.index("in_validated_season(init, L)") < src.index("build_feature_matrix(s.fields_doc")
