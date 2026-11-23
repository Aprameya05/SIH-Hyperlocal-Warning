"""Station CB/FF models leak the target (same-day RF in features) and must not be scored for live output."""
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from station_inputs import defaulted_inputs  # noqa: E402


def test_feature_list_contains_target_source_features():
    feats = json.loads((REPO / "models" / "cb_ff_feature_list.json").read_text(encoding="utf-8"))
    assert {"RF", "RF_3d", "RF_7d"} <= set(feats)
    assert defaulted_inputs(feats)


def test_cb_label_is_defined_by_same_day_rainfall_in_training_data():
    """Documents the leakage: cb_label == (RF >= 64.5) for ~99% of rows."""
    p = REPO / "data" / "bengaluru_6hr_training_dataset_cb_ff.csv"
    rows = list(csv.DictReader(p.open(encoding="utf-8-sig")))
    agree = sum((float(r["RF"] or 0) >= 64.5) == (r["cb_label"] in ("1", "1.0")) for r in rows)
    assert agree / len(rows) > 0.99


def test_forecast_action_withholds_instead_of_scoring():
    src = (REPO / "forecast_action.py").read_text(encoding="utf-8")
    assert "station CB/FF models withheld" in src
    assert '"status": "UNAVAILABLE"' in src
    assert src.index("_leaky = _di(cb_ff_features)") < src.index("cb_probs = score_hazard_slots")
