#!/usr/bin/env python3
"""Phase 0.4.5 focused tests for scripts/historical_gfs_thermodynamics.py
(research-only Magnus-Tetens dewpoint derivation + historical K-Index /
Totals-Totals). Does not touch production code."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from historical_gfs_thermodynamics import (  # noqa: E402
    derive_dewpoint, compute_k_index_historical, compute_totals_totals_historical,
    FEATURE_STATUS_DERIVED, DEWPOINT_SOURCE_DERIVED, DEWPOINT_FORMULA,
    KELVIN_OFFSET,
)


def test_dewpoint_physically_sensible_case():
    # 25 degC, 50% RH -> dewpoint should be meaningfully below 25C, a
    # textbook-range result (~13-14 degC).
    t_k = 25.0 + KELVIN_OFFSET
    res = derive_dewpoint(t_k, 50.0)
    assert res.available
    assert res.dewpoint_c is not None
    assert 10.0 < res.dewpoint_c < 16.0
    assert res.dewpoint_c < 25.0  # dewpoint never exceeds air temp
    assert res.feature_status == FEATURE_STATUS_DERIVED
    assert res.dewpoint_source == DEWPOINT_SOURCE_DERIVED
    assert res.formula == DEWPOINT_FORMULA
    assert res.rh_clamped is False


def test_dewpoint_saturated_air_equals_temperature():
    # RH=100% -> dewpoint should equal temperature (saturated air).
    t_k = 15.0 + KELVIN_OFFSET
    res = derive_dewpoint(t_k, 100.0)
    assert res.available
    assert abs(res.dewpoint_c - 15.0) < 0.01
    assert res.rh_clamped is False


def test_dewpoint_rh_above_100_is_clamped_and_flagged():
    t_k = 20.0 + KELVIN_OFFSET
    res = derive_dewpoint(t_k, 103.5)
    assert res.available
    assert res.rh_clamped is True
    assert res.rh_clamped_from == 103.5
    # Clamped to 100% -> dewpoint should equal temperature.
    assert abs(res.dewpoint_c - 20.0) < 0.01


def test_dewpoint_rh_zero_is_clamped_not_divide_by_zero():
    t_k = 10.0 + KELVIN_OFFSET
    res = derive_dewpoint(t_k, 0.0)
    assert res.rh_clamped is True
    assert res.rh_clamped_from == 0.0
    # Should not raise, and should produce a very low (but finite) dewpoint.
    assert res.available
    assert res.dewpoint_c < -50.0  # near-zero RH -> extremely low dewpoint


def test_dewpoint_rh_negative_is_clamped():
    t_k = 10.0 + KELVIN_OFFSET
    res = derive_dewpoint(t_k, -5.0)
    assert res.rh_clamped is True
    assert res.rh_clamped_from == -5.0
    assert res.available


def test_dewpoint_missing_temperature():
    res = derive_dewpoint(None, 60.0)
    assert not res.available
    assert "temperature" in res.missing_reason


def test_dewpoint_missing_rh():
    res = derive_dewpoint(290.0, None)
    assert not res.available
    assert "relative_humidity" in res.missing_reason


def test_dewpoint_both_missing():
    res = derive_dewpoint(None, None)
    assert not res.available
    assert "temperature" in res.missing_reason
    assert "relative_humidity" in res.missing_reason


def test_dewpoint_nan_input_rejected():
    res = derive_dewpoint(float("nan"), 50.0)
    assert not res.available
    assert "non-finite" in res.missing_reason


def test_dewpoint_inf_input_rejected():
    res = derive_dewpoint(290.0, float("inf"))
    assert not res.available
    assert "non-finite" in res.missing_reason


def test_k_index_physically_sensible_case():
    # Realistic monsoon-season sounding: warm/moist at 850, cooler at 700/500.
    res = compute_k_index_historical(
        t850_k=297.0, rh850_pct=80.0,
        t700_k=284.0, rh700_pct=60.0,
        t500_k=265.0,
    )
    assert res.available
    assert res.value_c is not None
    assert math.isfinite(res.value_c)
    assert res.feature_status == FEATURE_STATUS_DERIVED
    assert res.dewpoint_source == DEWPOINT_SOURCE_DERIVED


def test_k_index_missing_t500_is_unavailable():
    res = compute_k_index_historical(
        t850_k=297.0, rh850_pct=80.0, t700_k=284.0, rh700_pct=60.0, t500_k=None,
    )
    assert not res.available
    assert "T500" in res.missing_reason


def test_k_index_missing_rh_blocks_dewpoint_and_propagates():
    res = compute_k_index_historical(
        t850_k=297.0, rh850_pct=None, t700_k=284.0, rh700_pct=60.0, t500_k=265.0,
    )
    assert not res.available
    assert "Td850" in res.missing_reason


def test_totals_totals_physically_sensible_case():
    res = compute_totals_totals_historical(t850_k=297.0, rh850_pct=80.0, t500_k=265.0)
    assert res.available
    assert math.isfinite(res.value_c)
    assert res.feature_status == FEATURE_STATUS_DERIVED


def test_totals_totals_missing_inputs():
    res = compute_totals_totals_historical(t850_k=None, rh850_pct=80.0, t500_k=265.0)
    assert not res.available
    assert "T850" in res.missing_reason


def test_totals_totals_rh_edge_case_does_not_crash():
    # RH=0 at 850 hPa -- should clamp internally and still produce a
    # (physically extreme but finite) result, never raise.
    res = compute_totals_totals_historical(t850_k=297.0, rh850_pct=0.0, t500_k=265.0)
    assert res.available
    assert math.isfinite(res.value_k)
