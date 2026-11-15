"""Producer-side honesty contracts (M2): no fabricated times, no zero-filled undefined metrics, no relabelled leads."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_lead_time_json_labels_acquisition_lead_and_nulls_operational():
    import lead_time
    from datetime import datetime, timezone
    lt = lead_time.compute_lead_time("2026-10-31", 3, datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc), "2026-10-31 00Z", 3).to_json()
    assert lt["hours_semantics"] == "LEGACY_ACQUISITION_LEAD_NOT_OPERATIONAL"
    assert lt["acquisition_lead_hours"] == lt["hours"]
    assert lt["operational_lead_hours"] is None and lt["operational_lead_status"] == "UNAVAILABLE_NO_AVAILABILITY_TIME"
    late = lead_time.compute_lead_time("2026-10-31", 0, datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)).to_json()
    assert late["hours"] == 0.0 and late["clipped"] is True and late["operational_lead_hours"] is None


def test_verification_metrics_are_undefined_not_zero():
    import verify_today as v
    empty = v.compute_metrics(pd.DataFrame({"predicted": [], "actual": []}))
    assert all(empty[k] is None for k in ("POD", "FAR", "HSS", "Brier", "CSI")) and empty["n"] == 0
    all_neg = v.compute_metrics(pd.DataFrame({"predicted": [0, 0, 0], "actual": [0, 0, 0]}))
    assert all_neg["POD"] is None and all_neg["FAR"] is None and all_neg["CSI"] is None      # no events: undefined
    some = v.compute_metrics(pd.DataFrame({"predicted": [1, 0], "actual": [1, 1]}))
    assert some["POD"] == 0.5 and some["FAR"] == 0.0                                          # a real zero stays zero


def test_missing_probability_is_not_scored_as_zero_in_brier():
    import verify_today as v
    df = pd.DataFrame({"predicted": [1, 1], "actual": [1, 1], "ts_probability": [None, 0.9]})
    assert abs(v.compute_metrics(df)["Brier"] - 0.01) < 1e-6          # NaN row excluded, not (0-1)^2


def test_alert_log_issue_time_comes_from_the_forecast_never_hard_coded():
    import generate_alert_log as g
    assert g.get_issued_ist({"generated_at": "2026-10-31 03:15 IST"}) == "2026-10-31 03:15"
    assert g.get_issued_ist({}) is None and g.get_issued_ist(None) is None
    assert g.get_issued_ist({"generated_at": "garbage"}) is None
    ev = g.build_event({"slots": [{"slot": 2, "ts_probability": 0.3, "threshold": 0.1}], "peak_slot": 2}, True, "2026-10-31 15:00")
    assert ev["alert_issued_ist"] is None and ev["lead_time_min"] is None       # no invented 09:40, no invented lead
    ev2 = g.build_event({"generated_at": "2026-10-31 11:00 IST", "slots": [{"slot": 2, "ts_probability": 0.3, "threshold": 0.1}],
                         "peak_slot": 2}, True, "2026-10-31 15:00")
    assert ev2["lead_time_min"] == 240 and ev2["lead_time_basis"].startswith("FORECAST_GENERATED_AT")


def test_unscored_hazard_slots_are_none_in_forecast_action():
    src = (ROOT / "forecast_action.py").read_text()
    assert "probs = {i: None for i in range(4)}" in src
    assert 's["cb_probability"] = cb_probs.get(sid)' in src and "cb_probs.get(sid, 0.0)" not in src
    assert 'data_source == "model_defaults"' in src and "is_fallback = True" in src


def test_canonical_vobl_entries_state_they_are_slot_windows_not_leads():
    import canonical_forecast_writer as w
    haz = lambda: {k: {"probability": None} for k in ("thunderstorm", "cloudburst", "flash_flood")}
    cell = {"cell_id": w.VOBL_CELL_ID, "forecasts": [{"lead_hours": L, "hazards": haz(), "data_quality": {}, "valid_time": None} for L in (2, 4, 6)]}
    fc = {"generated_at": "2026-10-31 03:15 IST", "model_version": "t", "slots": []}
    w.overlay_vobl_ml_cell([cell], fc, {}, {})
    for lf in cell["forecasts"]:
        ls = lf["lead_semantics"]
        assert ls["kind"] == "SLOT_WINDOW_LOOKUP_NOT_LEAD_SPECIFIC" and ls["operational_lead_hours"] is None
        assert all(b["lead_specific"] is False and b["resolution"] == "STATION_SLOT_WINDOW" for b in lf["hazards"].values())


def test_weekly_digest_wording_is_not_misleading():
    y = (ROOT / ".github/workflows/weekly_digest.yml").read_text()
    assert "NOT an IMD/NDMA warning" in y and "[CSIR]" not in y and "Today max prob" not in y
    assert "(pod and far and hss and brier)" not in y            # a genuine 0.0 metric is not "missing"
    assert "uncalibrated score" in y


def test_publish_bundle_has_size_headroom(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    from pipelines.publishing import build_pages_bundle as b
    b.build(tmp_path / "d")
    biggest = max(f.stat().st_size for f in (tmp_path / "d").rglob("*") if f.is_file())
    assert biggest <= 0.85 * b.MAX_FILE_BYTES, f"largest published file {biggest/2**20:.1f} MiB leaves <15% headroom"


def test_peak_and_alert_cannot_come_from_a_fallback_slot():
    src = (ROOT / "forecast_action.py").read_text()
    assert '_elig = [s for s in slots_output if not s.get("fallback")]' in src
    assert "alert_active     = any(s[\"ts_predicted\"] for s in _elig)" in src
    assert "peak_slot, peak_probability = None, None" in src
    assert "def _haz_summary" in src and '"max_prob": None' in src       # no scored slot -> undefined, never 0 / alert
    from backend import forecast_integrity as fi
    fc = {"generated_at": "2026-10-31 15:30 IST", "alert_active": True, "peak_probability": 0.4,
          "slots": [{"slot": 3, "ts_probability": 0.4, "ts_predicted": True, "source": "model_defaults", "fallback": True}]}
    assert not fi.verify_vobl_forecast(fc)["ok"]          # the CI gate rejects exactly the old producer behaviour


def test_forecast_json_verification_block_does_not_coerce_undefined_to_zero():
    src = (ROOT / "forecast_action.py").read_text()
    assert 'float(slot2_30d.get("POD", 0))' not in src and 'float(v.get("POD", 0))' not in src
    assert "def _m(d, key, nd)" in src
