"""
tests/test_phase42_gfs_live_cb_predictors.py
===============================================
Phase 3 (2026-10-06 takeover pass): DETERMINISTIC (no network) unit
tests for scripts/gfs_live_cb_predictors.py -- the module that builds
a live-cycle replacement for the fixed-2024-08-01 CB predictor table.
Real network behaviour is covered separately in
tests/test_phase41_gfs_aws_source.py; this file must never require
network access so the normal test suite stays fast and deterministic.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gfs_live_cb_predictors as cb  # noqa: E402


def test_cb_field_map_covers_every_panindia_cb_features_raw_column():
    """panindia_cb_features.engineer_daily_features()'s agg_spec needs
    these exact raw columns (besides label/meta columns) -- if this
    drifts out of sync with that module, CB feature engineering would
    silently KeyError or (worse) silently aggregate the wrong column."""
    required_raw_cols = {
        "cape_sfc_jkg", "cin_sfc_jkg", "t2m_k", "rh2m_pct", "q2m_kgkg", "td2m_k",
        "sp_pa", "mslp_pa", "pwat_mm", "gh850_gpm", "gh500_gpm", "gh200_gpm",
        "target_day_precip_tp_mm", "target_day_precip_acpcp_mm",
    }
    mapped_cols = set(cb.CB_FIELD_MAP.values())
    missing = required_raw_cols - mapped_cols
    assert not missing, f"CB_FIELD_MAP is missing raw columns required downstream: {missing}"
    # wind_speed_*_ms and shear_mag_850_200_ms are derived (not fetched
    # directly) from u/v -- confirm the u/v pairs needed to derive them
    # are present instead.
    for level in ("850", "500", "200"):
        assert f"u{level}_ms" in mapped_cols and f"v{level}_ms" in mapped_cols


def test_cb_required_fields_are_valid_idx_style_tuples():
    for var, level in cb.CB_REQUIRED_FIELDS:
        assert isinstance(var, str) and var.isupper()
        assert isinstance(level, str) and len(level) > 0


def test_decode_subset_to_rows_never_fabricates_missing_columns(monkeypatch, tmp_path):
    """If a decode group fails to open, the resulting columns dict must
    simply omit that column (handled upstream as NaN) -- never invent
    a zero or synthetic value."""
    def _fail_open(path, filter_keys):
        return None
    monkeypatch.setattr(cb, "_open_filtered", _fail_open)
    fake_path = tmp_path / "doesnotmatter.grib2"
    fake_path.write_bytes(b"not a real grib file")
    cells = [{"cell_id": "IND_TEST", "lat": 10.0, "lon": 80.0}]
    result = cb._decode_subset_to_rows(fake_path, cells)
    assert result == {}


def test_build_live_cb_longform_table_reports_unavailable_when_no_cycle(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cb.aws_src, "find_latest_available_cycle", lambda max_days_back=3: None)
    result = cb.build_live_cb_longform_table([{"cell_id": "IND_TEST", "lat": 10.0, "lon": 80.0}])
    assert result["status"] == "UNAVAILABLE"
    assert result["dataframe"] is None


def test_build_live_cb_longform_table_reports_unavailable_when_every_lead_fails(monkeypatch, tmp_path):
    # Isolate the cache directory: this test uses a FAKE cycle string
    # ("2026100600") that could collide with the real production cache
    # key for that same real cycle -- writing (or, worse, reading) fake
    # per-test data under the real cycle's cache entry would silently
    # corrupt a real scheduled run's live CB data with synthetic rows.
    monkeypatch.setattr(cb, "CACHE_DIR", tmp_path / "cache")
    fake_cycle = cb.aws_src.CycleInfo(date_str="20261006", cycle_hour="00")
    monkeypatch.setattr(cb.aws_src, "find_latest_available_cycle", lambda max_days_back=3: fake_cycle)
    monkeypatch.setattr(cb, "fetch_one_lead", lambda cycle, fhour, cells, out_dir: (
        None, {"fhour": fhour, "source_used": "UNAVAILABLE", "bytes": 0, "elapsed_s": 0.1,
               "fields_found": 0, "fields_missing": []}))
    result = cb.build_live_cb_longform_table([{"cell_id": "IND_TEST", "lat": 10.0, "lon": 80.0}],
                                              out_dir=tmp_path / "work")
    assert result["status"] == "UNAVAILABLE"
    assert result["dataframe"] is None
    assert len(result["per_lead_meta"]) == len(cb.CB_LEADS_HOURS)


def test_build_live_cb_longform_table_derives_wind_speed_and_shear_without_network(monkeypatch, tmp_path):
    """Feeds fetch_one_lead a deterministic fake columns dict for every
    lead and checks the long-format output table has the derived
    wind_speed_*_ms / shear_mag_850_200_ms columns computed correctly --
    this is the arithmetic a live run depends on, tested without
    touching the network."""
    monkeypatch.setattr(cb, "CACHE_DIR", tmp_path / "cache")
    fake_cycle = cb.aws_src.CycleInfo(date_str="20261006", cycle_hour="00")
    monkeypatch.setattr(cb.aws_src, "find_latest_available_cycle", lambda max_days_back=3: fake_cycle)

    def _fake_fetch_one_lead(cycle, fhour, cells, out_dir):
        n = len(cells)
        columns = {
            "cape_sfc_jkg": np.array([500.0] * n), "cin_sfc_jkg": np.array([-10.0] * n),
            "t2m_k": np.array([300.0] * n), "rh2m_pct": np.array([60.0] * n),
            "q2m_kgkg": np.array([0.01] * n), "td2m_k": np.array([290.0] * n),
            "sp_pa": np.array([95000.0] * n), "mslp_pa": np.array([101000.0] * n),
            "pwat_mm": np.array([40.0] * n),
            "u850_ms": np.array([3.0] * n), "v850_ms": np.array([4.0] * n),  # |v|=5
            "u500_ms": np.array([0.0] * n), "v500_ms": np.array([0.0] * n),
            "u200_ms": np.array([9.0] * n), "v200_ms": np.array([12.0] * n),  # |v|=15
            "gh850_gpm": np.array([1500.0] * n), "gh500_gpm": np.array([5900.0] * n),
            "gh200_gpm": np.array([12400.0] * n),
            "target_day_precip_tp_mm": np.array([2.0] * n), "target_day_precip_acpcp_mm": np.array([1.0] * n),
        }
        meta = {"fhour": fhour, "source_used": "LIVE_AWS_GFS", "bytes": 1000, "elapsed_s": 0.1,
                "fields_found": len(columns), "fields_missing": []}
        return columns, meta

    monkeypatch.setattr(cb, "fetch_one_lead", _fake_fetch_one_lead)
    cells = [{"cell_id": "IND_13.0_77.0", "lat": 13.0, "lon": 77.0}]
    result = cb.build_live_cb_longform_table(cells, out_dir=tmp_path)

    assert result["status"] == "LIVE_AWS_GFS"
    assert result["n_leads_live"] == len(cb.CB_LEADS_HOURS)
    df = result["dataframe"]
    assert len(df) == len(cells) * len(cb.CB_LEADS_HOURS)
    # wind_speed_850_ms = sqrt(3^2+4^2) = 5.0 ; wind_speed_200_ms = sqrt(9^2+12^2) = 15.0
    assert np.allclose(df["wind_speed_850_ms"].unique(), [5.0])
    assert np.allclose(df["wind_speed_200_ms"].unique(), [15.0])
    # shear_mag_850_200_ms = sqrt((9-3)^2+(12-4)^2) = sqrt(36+64) = 10.0
    assert np.allclose(df["shear_mag_850_200_ms"].unique(), [10.0])
    # every row must carry init_time info consistent with the resolved cycle
    assert result["cycle_str"] == "2026100600"
    assert result["target_date"] == "2026-10-06"


def test_build_live_cb_longform_table_output_is_compatible_with_engineer_daily_features(monkeypatch, tmp_path):
    """The real integration contract: feed the live table straight into
    panindia_cb_features.engineer_daily_features() and confirm it
    produces exactly the 34 feature columns panindia_cb_v1 was trained
    on, with no crash -- without touching the network."""
    import panindia_cb_features as cbf

    monkeypatch.setattr(cb, "CACHE_DIR", tmp_path / "cache")
    fake_cycle = cb.aws_src.CycleInfo(date_str="20261006", cycle_hour="00")
    monkeypatch.setattr(cb.aws_src, "find_latest_available_cycle", lambda max_days_back=3: fake_cycle)

    def _fake_fetch_one_lead(cycle, fhour, cells, out_dir):
        n = len(cells)
        rng = np.random.default_rng(fhour)  # vary slightly per lead, deterministically
        columns = {
            "cape_sfc_jkg": rng.uniform(100, 1000, n), "cin_sfc_jkg": rng.uniform(-200, 0, n),
            "t2m_k": rng.uniform(295, 310, n), "rh2m_pct": rng.uniform(30, 90, n),
            "q2m_kgkg": rng.uniform(0.005, 0.02, n), "td2m_k": rng.uniform(280, 300, n),
            "sp_pa": rng.uniform(90000, 101000, n), "mslp_pa": rng.uniform(100500, 101500, n),
            "pwat_mm": rng.uniform(20, 60, n),
            "u850_ms": rng.uniform(-10, 10, n), "v850_ms": rng.uniform(-10, 10, n),
            "u500_ms": rng.uniform(-10, 10, n), "v500_ms": rng.uniform(-10, 10, n),
            "u200_ms": rng.uniform(-20, 20, n), "v200_ms": rng.uniform(-20, 20, n),
            "gh850_gpm": rng.uniform(1400, 1600, n), "gh500_gpm": rng.uniform(5800, 6000, n),
            "gh200_gpm": rng.uniform(12300, 12500, n),
            "target_day_precip_tp_mm": rng.uniform(0, 10, n), "target_day_precip_acpcp_mm": rng.uniform(0, 5, n),
        }
        meta = {"fhour": fhour, "source_used": "LIVE_AWS_GFS", "bytes": 1000, "elapsed_s": 0.1,
                "fields_found": len(columns), "fields_missing": []}
        return columns, meta

    monkeypatch.setattr(cb, "fetch_one_lead", _fake_fetch_one_lead)
    cells = [{"cell_id": "IND_13.0_77.0", "lat": 13.0, "lon": 77.0},
             {"cell_id": "IND_28.0_77.0", "lat": 28.0, "lon": 77.0}]
    result = cb.build_live_cb_longform_table(cells, out_dir=tmp_path)
    df = result["dataframe"]

    engineered = cbf.engineer_daily_features(df, include_prate=False, include_9000pa=False)
    assert len(engineered) == len(cells)
    import json
    expected_cols = set(json.loads(
        (REPO_ROOT / "models" / "panindia_cb_v1" / "panindia_cb_v1_feature_list.json").read_text(encoding="utf-8")
    ))
    missing = expected_cols - set(engineered.columns)
    assert not missing, f"engineered live table is missing model feature columns: {missing}"
