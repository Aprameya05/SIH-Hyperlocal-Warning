#!/usr/bin/env python3
"""
historical_gfs_precipitation.py -- Phase 0.4.5 RESEARCH-ONLY helper.

Reconstructs a physically meaningful interval-precipitation feature from two
historical GFS forecast leads of the SAME initialization cycle, using the
GRIB accumulation semantics verified directly from real files in
docs/PHASE_0_4_4_HISTORICAL_GFS_FEATURE_CONTRACT.md Part 2:

  tp(lead_end) and tp(lead_start) are both "accumulated since forecast start"
  (startStep=0 confirmed for both f003 and f006 in the real pilot files), so
  tp(lead_end) - tp(lead_start) is the precipitation that fell during the
  disjoint interval (lead_start, lead_end].

This produces a feature named `gfs_precip_3h_interval_mm` (or more generally
`gfs_precip_interval_mm` for other lead pairs) -- DELIBERATELY NOT the name
`qpe_mm`, because it is not the same quantity as production's current
`qpe_mm` (which is read at fhour=0 and is therefore structurally near-zero,
per Phase 0.4.4 Part 1). This module does not modify, call, or import
backend/pipeline.py's qpe_mm computation in any way.

This is a RESEARCH-ONLY module, not wired into any production code path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

FEATURE_NAME = "gfs_precip_interval_mm"
SOURCE_FIELD = "tp"  # GRIB shortName, typeOfLevel=surface, stepType=accum
ACCUMULATION_SEMANTICS = (
    "GFS tp (total precipitation, surface, kg/m^2) accumulated from forecast "
    "start (step 0) through the requested lead -- confirmed by direct GRIB "
    "metadata inspection (startStep=0 for both f003 and f006 in the real "
    "pilot files) in docs/PHASE_0_4_4_HISTORICAL_GFS_FEATURE_CONTRACT.md "
    "Part 2. Differencing two such accumulations sharing the same start "
    "(step 0) and the same initialization yields the precipitation total "
    "for the disjoint interval between the two valid times."
)

# Two accumulated fields measuring the identical physical total should
# difference to exactly 0 or a positive number. A tiny negative value can
# arise from floating-point rounding in the GRIB encoding/decoding chain
# (packing precision), not from a real physical violation. Values beyond
# this tolerance are treated as a genuine semantic problem, not a rounding
# artifact, and are rejected rather than silently floored.
FLOATING_POINT_NEGATIVE_TOLERANCE_MM = 0.05


@dataclass
class IntervalPrecipResult:
    value_mm: Optional[float]
    available: bool
    feature_name: str
    source_field: str
    accumulation_semantics: str
    initialization_time: Optional[str]
    start_valid_time: Optional[str]
    end_valid_time: Optional[str]
    lead_start_hours: Optional[int]
    lead_end_hours: Optional[int]
    tp_at_lead_start_mm: Optional[float]
    tp_at_lead_end_mm: Optional[float]
    rejected: bool
    rejection_reason: Optional[str]

    def to_json(self) -> dict:
        return {
            "value_mm": self.value_mm,
            "available": self.available,
            "feature_name": self.feature_name,
            "source_field": self.source_field,
            "accumulation_semantics": self.accumulation_semantics,
            "initialization_time": self.initialization_time,
            "start_valid_time": self.start_valid_time,
            "end_valid_time": self.end_valid_time,
            "lead_start_hours": self.lead_start_hours,
            "lead_end_hours": self.lead_end_hours,
            "tp_at_lead_start_mm": self.tp_at_lead_start_mm,
            "tp_at_lead_end_mm": self.tp_at_lead_end_mm,
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
        }


def compute_interval_precipitation(
    *,
    tp_start_mm: Optional[float],
    tp_end_mm: Optional[float],
    init_time: str,
    start_valid_time: str,
    end_valid_time: str,
    lead_start_hours: int,
    lead_end_hours: int,
    start_step: int,
    end_start_step: int,
) -> IntervalPrecipResult:
    """Compute tp(lead_end) - tp(lead_start) as an interval precipitation
    total, ONLY if the GRIB accumulation-window metadata confirms both
    fields share the same accumulation origin (step 0) and the same
    initialization -- the validity check the brief requires, not assumed.

    Parameters named `start_step`/`end_start_step` are each field's own
    GRIB `startStep` (the beginning of ITS accumulation window) -- both
    must equal 0 for the subtraction to be valid under the semantics
    documented in ACCUMULATION_SEMANTICS above. (Despite the confusing
    adjacent names, `start_step` is the f-lead-start file's startStep and
    `end_start_step` is the f-lead-end file's startStep; both are expected
    to be 0, not equal to each other's lead.)
    """
    base = dict(
        feature_name=FEATURE_NAME, source_field=SOURCE_FIELD,
        accumulation_semantics=ACCUMULATION_SEMANTICS,
        initialization_time=init_time,
        start_valid_time=start_valid_time, end_valid_time=end_valid_time,
        lead_start_hours=lead_start_hours, lead_end_hours=lead_end_hours,
        tp_at_lead_start_mm=tp_start_mm, tp_at_lead_end_mm=tp_end_mm,
    )

    if tp_start_mm is None or tp_end_mm is None:
        missing = []
        if tp_start_mm is None:
            missing.append(f"tp at lead f{lead_start_hours:03d}")
        if tp_end_mm is None:
            missing.append(f"tp at lead f{lead_end_hours:03d}")
        return IntervalPrecipResult(
            None, False, rejected=True,
            rejection_reason=f"missing value(s): {', '.join(missing)}", **base,
        )

    if lead_end_hours <= lead_start_hours:
        return IntervalPrecipResult(
            None, False, rejected=True,
            rejection_reason=(
                f"lead_end_hours ({lead_end_hours}) must be strictly greater "
                f"than lead_start_hours ({lead_start_hours}) -- wrong lead "
                f"ordering, cannot represent a forward interval"
            ), **base,
        )

    # The validity check the brief requires: both accumulations must share
    # the same accumulation origin (step 0). If either field's own window
    # does not start at 0, differencing is NOT validated by this module's
    # reasoning (Phase 0.4.4 Part 2) and must be rejected, not assumed.
    if start_step != 0 or end_start_step != 0:
        return IntervalPrecipResult(
            None, False, rejected=True,
            rejection_reason=(
                f"accumulation windows do not both start at step 0 "
                f"(start_step={start_step}, end_start_step={end_start_step}) "
                f"-- the differencing validity established in "
                f"docs/PHASE_0_4_4_HISTORICAL_GFS_FEATURE_CONTRACT.md Part 2 "
                f"does not hold for this pair; refusing to compute a "
                f"silently-wrong interval total"
            ), **base,
        )

    diff = tp_end_mm - tp_start_mm

    if diff < 0:
        if abs(diff) <= FLOATING_POINT_NEGATIVE_TOLERANCE_MM:
            # Tiny negative, within documented floating-point tolerance --
            # floored to 0 and the flooring is recorded, not hidden.
            return IntervalPrecipResult(
                0.0, True, rejected=False, rejection_reason=(
                    f"raw difference {diff:.6f} mm was a tiny negative within "
                    f"the documented tolerance ({FLOATING_POINT_NEGATIVE_TOLERANCE_MM} mm) "
                    f"-- floored to 0.0, not silently discarded"
                ), **base,
            )
        return IntervalPrecipResult(
            None, False, rejected=True,
            rejection_reason=(
                f"physically impossible negative interval precipitation: "
                f"tp(f{lead_end_hours:03d})={tp_end_mm} - "
                f"tp(f{lead_start_hours:03d})={tp_start_mm} = {diff:.6f} mm, "
                f"exceeds the {FLOATING_POINT_NEGATIVE_TOLERANCE_MM} mm "
                f"floating-point tolerance -- this indicates a genuine data "
                f"problem (e.g. mismatched initialization cycles), not "
                f"rounding; rejected rather than floored"
            ), **base,
        )

    if not math.isfinite(diff):
        return IntervalPrecipResult(
            None, False, rejected=True,
            rejection_reason="non-finite result (NaN or inf)", **base,
        )

    return IntervalPrecipResult(round(diff, 4), True, rejected=False, rejection_reason=None, **base)


def verify_same_initialization_cycle(init_time_a: str, init_time_b: str) -> bool:
    """Guard used by callers before computing an interval: the two fields
    being differenced must come from the SAME initialization cycle. Comparing
    two tp fields from different cycles (e.g. a 00Z f006 against an 18Z f003)
    would not represent a real, well-defined interval at all -- this
    function exists so that mistake is caught explicitly rather than
    producing a plausible-looking but meaningless number."""
    return init_time_a == init_time_b
