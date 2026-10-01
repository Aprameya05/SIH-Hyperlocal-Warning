#!/usr/bin/env python3
"""
historical_gfs_thermodynamics.py -- Phase 0.4.5 RESEARCH-ONLY helper.

Derives dewpoint from temperature + relative humidity (Magnus-Tetens), then
computes K-Index and Totals-Totals using EXACTLY the formulas already traced
from production (backend/pipeline.py::compute_k_index/compute_totals_totals,
Phase 0.4.4 Part 3) -- this module does not alter those formulas, it only
supplies a derived dewpoint input for use with historical GFS archives that
lack a direct isobaric dewpoint field (confirmed absent in the GDEX d084001
f003/f006 pilot files, Phase 0.4.3B/0.4.4).

This is a RESEARCH-ONLY module. It is not imported by, and does not alter,
any production code path (backend/pipeline.py, forecast_action.py,
canonical_forecast_writer.py, or any schema). Every result this module
produces is tagged with explicit provenance (source, dewpoint_source,
formula, feature_status) so a derived K-Index/Totals-Totals value can never
be mistaken for a value computed from a model-native dewpoint field.

Why Magnus-Tetens, and why this is an approximation, not a substitute:
production's live K-Index/Totals-Totals use GFS's own dewpoint field
directly (when NOMADS serves var_DPT). The GDEX d084001 archive used for
this project's historical pilot does not carry a dewpoint field at isobaric
levels at all (confirmed by full shortName enumeration in
docs/PHASE_0_4_4_HISTORICAL_GFS_FEATURE_CONTRACT.md Part 3) -- only
temperature and relative humidity are available at the required levels
(850/700/500 hPa). Magnus-Tetens recovers an approximate dewpoint from T+RH;
it is a well-established meteorological approximation but it is NOT the
model's own dewpoint output, and this module never claims otherwise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

FEATURE_STATUS_DERIVED = "DERIVED"
DEWPOINT_SOURCE_DERIVED = "derived_from_temperature_rh"
DEWPOINT_FORMULA = "Magnus-Tetens"
SOURCE_HISTORICAL_GFS = "historical_gfs"

# Magnus-Tetens coefficients (same widely-used form cited in
# docs/PHASE_0_4_4_HISTORICAL_GFS_FEATURE_CONTRACT.md Part 3).
_MAGNUS_A = 17.67
_MAGNUS_B = 243.5  # degC
_MAGNUS_E0 = 6.112  # hPa, saturation vapor pressure reference

KELVIN_OFFSET = 273.15

# RH is physically bounded [0, 100]%. Real NWP output can report values
# slightly outside this range (supersaturation artifacts near 100%, or
# small negative/zero RH from interpolation/rounding). This module clamps
# to this range -- documented explicitly here and surfaced in every result's
# `rh_clamped` flag, never silently.
RH_MIN_PCT = 0.0
RH_MAX_PCT = 100.0

# Below this RH, saturation vapor pressure ratio (e/es) underflows toward
# zero and ln(e/es) diverges to -inf; dewpoint derivation is not meaningful.
RH_FLOOR_FOR_DEWPOINT_PCT = 0.01


@dataclass
class DewpointResult:
    """Provenance-complete result of deriving dewpoint from T + RH."""
    dewpoint_k: Optional[float]
    dewpoint_c: Optional[float]
    available: bool
    source: str
    dewpoint_source: str
    formula: str
    feature_status: str
    input_t_k: Optional[float]
    input_rh_pct: Optional[float]
    rh_clamped: bool
    rh_clamped_from: Optional[float]
    missing_reason: Optional[str]

    def to_json(self) -> dict:
        return {
            "dewpoint_k": self.dewpoint_k,
            "dewpoint_c": self.dewpoint_c,
            "available": self.available,
            "source": self.source,
            "dewpoint_source": self.dewpoint_source,
            "formula": self.formula,
            "feature_status": self.feature_status,
            "input_t_k": self.input_t_k,
            "input_rh_pct": self.input_rh_pct,
            "rh_clamped": self.rh_clamped,
            "rh_clamped_from": self.rh_clamped_from,
            "missing_reason": self.missing_reason,
        }


def derive_dewpoint(t_k: Optional[float], rh_pct: Optional[float]) -> DewpointResult:
    """Derive dewpoint (Kelvin) from temperature (Kelvin) and relative
    humidity (percent, 0-100 nominal) via Magnus-Tetens.

    Edge cases handled explicitly (never silently):
      - t_k is None or rh_pct is None -> unavailable, missing_reason set.
      - rh_pct > 100 (supersaturation artifact) -> clamped to 100, flagged
        via rh_clamped=True and rh_clamped_from=<original value>.
      - rh_pct <= 0 -> clamped to RH_FLOOR_FOR_DEWPOINT_PCT (not to 0, since
        RH=0 makes e=0 and ln(0) is undefined) and flagged the same way.
      - t_k is non-finite (NaN/inf) -> unavailable, missing_reason set.
    """
    if t_k is None or rh_pct is None:
        missing = []
        if t_k is None:
            missing.append("temperature")
        if rh_pct is None:
            missing.append("relative_humidity")
        return DewpointResult(
            None, None, False, SOURCE_HISTORICAL_GFS, DEWPOINT_SOURCE_DERIVED,
            DEWPOINT_FORMULA, FEATURE_STATUS_DERIVED, t_k, rh_pct,
            False, None, f"missing input(s): {', '.join(missing)}",
        )

    if not math.isfinite(t_k) or not math.isfinite(rh_pct):
        return DewpointResult(
            None, None, False, SOURCE_HISTORICAL_GFS, DEWPOINT_SOURCE_DERIVED,
            DEWPOINT_FORMULA, FEATURE_STATUS_DERIVED, t_k, rh_pct,
            False, None, "non-finite input (NaN or inf)",
        )

    rh_clamped = False
    rh_clamped_from = None
    rh_used = rh_pct
    if rh_pct > RH_MAX_PCT:
        rh_clamped = True
        rh_clamped_from = rh_pct
        rh_used = RH_MAX_PCT
    elif rh_pct <= RH_MIN_PCT:
        rh_clamped = True
        rh_clamped_from = rh_pct
        rh_used = RH_FLOOR_FOR_DEWPOINT_PCT

    t_c = t_k - KELVIN_OFFSET

    # Saturation vapor pressure at T (hPa), then actual vapor pressure at
    # the (possibly clamped) RH.
    es = _MAGNUS_E0 * math.exp((_MAGNUS_A * t_c) / (t_c + _MAGNUS_B))
    e = (rh_used / 100.0) * es

    if e <= 0:
        # Should not occur given the RH floor above, but guarded explicitly
        # rather than letting log(0) raise.
        return DewpointResult(
            None, None, False, SOURCE_HISTORICAL_GFS, DEWPOINT_SOURCE_DERIVED,
            DEWPOINT_FORMULA, FEATURE_STATUS_DERIVED, t_k, rh_pct,
            rh_clamped, rh_clamped_from,
            "computed vapor pressure <= 0 after RH floor -- dewpoint undefined",
        )

    ln_ratio = math.log(e / _MAGNUS_E0)
    denom = _MAGNUS_A - ln_ratio
    if abs(denom) < 1e-12:
        return DewpointResult(
            None, None, False, SOURCE_HISTORICAL_GFS, DEWPOINT_SOURCE_DERIVED,
            DEWPOINT_FORMULA, FEATURE_STATUS_DERIVED, t_k, rh_pct,
            rh_clamped, rh_clamped_from,
            "Magnus-Tetens denominator underflow -- dewpoint undefined",
        )

    td_c = (_MAGNUS_B * ln_ratio) / denom
    td_k = td_c + KELVIN_OFFSET

    return DewpointResult(
        round(td_k, 4), round(td_c, 4), True,
        SOURCE_HISTORICAL_GFS, DEWPOINT_SOURCE_DERIVED, DEWPOINT_FORMULA,
        FEATURE_STATUS_DERIVED, t_k, rh_pct, rh_clamped, rh_clamped_from, None,
    )


@dataclass
class ThermoIndexResult:
    """K-Index / Totals-Totals result with full provenance. The formula
    fields are copied verbatim from backend/pipeline.py (read-only reference,
    not imported, to keep this module fully decoupled from production code
    per the brief's 'do not alter production K/T-T code' instruction --
    duplicating the two one-line formulas here is a deliberate, documented
    choice, not an accidental fork)."""
    value_k: Optional[float]  # raw formula output, Kelvin-scale arithmetic
    value_c: Optional[float]  # converted to Celsius if raw value > 200 (same convention as backend/pipeline.py's _ki_c/_tt_c helpers)
    available: bool
    source: str
    dewpoint_source: str
    formula: str
    feature_status: str
    missing_reason: Optional[str]

    def to_json(self) -> dict:
        return {
            "value_k": self.value_k,
            "value_c": self.value_c,
            "available": self.available,
            "source": self.source,
            "dewpoint_source": self.dewpoint_source,
            "formula": self.formula,
            "feature_status": self.feature_status,
            "missing_reason": self.missing_reason,
        }


def _to_c_if_kelvin(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value - KELVIN_OFFSET, 2) if value > 200 else round(value, 2)


def compute_k_index_historical(t850_k: Optional[float], rh850_pct: Optional[float],
                                t700_k: Optional[float], rh700_pct: Optional[float],
                                t500_k: Optional[float]) -> ThermoIndexResult:
    """K-Index = (T850 - T500) + Td850 - (T700 - Td700).
    Identical formula to backend/pipeline.py::compute_k_index -- the only
    difference is Td850/Td700 are DERIVED here (Magnus-Tetens from T+RH),
    not read directly from a GRIB dewpoint field (none exists in this
    archive, see docs/PHASE_0_4_4_HISTORICAL_GFS_FEATURE_CONTRACT.md)."""
    td850 = derive_dewpoint(t850_k, rh850_pct)
    td700 = derive_dewpoint(t700_k, rh700_pct)

    missing = []
    if t850_k is None:
        missing.append("T850")
    if t700_k is None:
        missing.append("T700")
    if t500_k is None:
        missing.append("T500")
    if not td850.available:
        missing.append(f"Td850 ({td850.missing_reason})")
    if not td700.available:
        missing.append(f"Td700 ({td700.missing_reason})")

    if missing:
        return ThermoIndexResult(
            None, None, False, SOURCE_HISTORICAL_GFS, DEWPOINT_SOURCE_DERIVED,
            "K-Index = (T850-T500)+Td850-(T700-Td700)", FEATURE_STATUS_DERIVED,
            f"missing/unavailable: {', '.join(missing)}",
        )

    ki = (t850_k - t500_k) + td850.dewpoint_k - (t700_k - td700.dewpoint_k)
    return ThermoIndexResult(
        round(ki, 4), _to_c_if_kelvin(ki), True,
        SOURCE_HISTORICAL_GFS, DEWPOINT_SOURCE_DERIVED,
        "K-Index = (T850-T500)+Td850-(T700-Td700)", FEATURE_STATUS_DERIVED, None,
    )


def compute_totals_totals_historical(t850_k: Optional[float], rh850_pct: Optional[float],
                                      t500_k: Optional[float]) -> ThermoIndexResult:
    """Totals-Totals = (T850 + Td850) - 2*T500.
    Identical formula to backend/pipeline.py::compute_totals_totals; Td850
    is DERIVED here (Magnus-Tetens), as above."""
    td850 = derive_dewpoint(t850_k, rh850_pct)

    missing = []
    if t850_k is None:
        missing.append("T850")
    if t500_k is None:
        missing.append("T500")
    if not td850.available:
        missing.append(f"Td850 ({td850.missing_reason})")

    if missing:
        return ThermoIndexResult(
            None, None, False, SOURCE_HISTORICAL_GFS, DEWPOINT_SOURCE_DERIVED,
            "Totals-Totals = (T850+Td850)-2*T500", FEATURE_STATUS_DERIVED,
            f"missing/unavailable: {', '.join(missing)}",
        )

    tt = (t850_k + td850.dewpoint_k) - 2 * t500_k
    return ThermoIndexResult(
        round(tt, 4), _to_c_if_kelvin(tt), True,
        SOURCE_HISTORICAL_GFS, DEWPOINT_SOURCE_DERIVED,
        "Totals-Totals = (T850+Td850)-2*T500", FEATURE_STATUS_DERIVED, None,
    )
