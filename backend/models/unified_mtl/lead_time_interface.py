"""
backend/models/unified_mtl/lead_time_interface.py
=====================================================
Phase 22 Track 5 -- 2-6h lead-time output interface.

HONEST STATUS: the existing CB/TS/FF heads all predict against DAILY
labels (Phase 20 IMD daily cloudburst labels mechanically expanded to
6-hour snapshot slots -- see docs/PHASE_19_GFS_PREDICTOR_EXTRACTION.md
and docs/PHASE_21_PANINDIA_CB_MODEL.md Section 12 -- never claimed as
genuine sub-daily labels; the FF PU labels are event-day resolution; the
VOBL TS model's station lead time is handled separately by
lead_time.py and is NOT what this interface targets). None of them
produce a genuine 2h/3h/4h/5h/6h-ahead forecast today.

This module defines the INPUT/OUTPUT CONTRACT a lead-time-aware model
must satisfy, and documents exactly what sub-daily label source would be
required to train one, per hazard. It does NOT implement lead-time
training or invent sub-daily labels.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

LEAD_HOURS = (2, 3, 4, 5, 6)


@dataclass
class LeadTimeRequest:
    """Required input shape for a lead-time-aware prediction call."""
    cell_id: str
    init_time_utc: datetime        # model-run/analysis time (GFS cycle init)
    valid_time_utc: datetime       # the specific time being forecast FOR
    lead_time_hours: int           # valid_time_utc - init_time_utc, in hours

    def __post_init__(self):
        if self.lead_time_hours not in LEAD_HOURS:
            raise ValueError(f"lead_time_hours must be one of {LEAD_HOURS}, got {self.lead_time_hours}")


@dataclass
class LeadTimeOutput:
    """Required output shape: one probability per hazard per lead hour."""
    cell_id: str
    lead_time_hours: int
    ts_probability: Optional[float] = None
    cb_probability: Optional[float] = None
    ff_probability: Optional[float] = None
    status: str = "NOT_YET_TRAINED"  # Phase 29: aligned with the 6-value status enum (TRAINED/BASELINE/PU_RANKING/PROTOTYPE/UNAVAILABLE/NOT_YET_TRAINED)
    note: str = field(default=(
        "No model trained against genuine sub-daily (<=6h-resolution) "
        "labels exists for any hazard. This output is a contract "
        "placeholder, not a prediction."
    ))


class LeadTimeModel:
    """Interface a future lead-time-aware model must implement.
    predict() raises NotImplementedError -- it must never return a
    fabricated probability dressed up as a +2h..+6h forecast."""

    def predict(self, request: LeadTimeRequest) -> LeadTimeOutput:
        raise NotImplementedError(
            "No lead-time-aware model is trained for any hazard (TS/CB/FF). "
            "See REQUIRED_SUBDAILY_LABEL_SOURCES below for what would be "
            "needed to train one. This method intentionally does not "
            "return a value computed from a daily-resolution model "
            "re-labeled as if it were lead-time-specific."
        )

    def predict_all_leads(self, cell_id: str, init_time_utc: datetime) -> list[LeadTimeOutput]:
        """Convenience wrapper returning one NOT_TRAINED output per lead
        hour via predict() -- still raises, by design."""
        return [self.predict(LeadTimeRequest(cell_id, init_time_utc, init_time_utc, h))
                for h in LEAD_HOURS]


# ---------------------------------------------------------------------------
# What sub-daily label source each hazard actually needs (documented, not
# invented) -- answers the Phase 22 Track 5 instruction to determine this
# without inventing the labels themselves.
# ---------------------------------------------------------------------------

REQUIRED_SUBDAILY_LABEL_SOURCES = {
    "TS": {
        "needed": "Sub-daily (ideally hourly) lightning/thunderstorm-occurrence "
                   "ground truth at pan-India scale, e.g. a lightning detection "
                   "network (ISRO/IMD LDN) or INSAT-3D convective-cell tracking.",
        "currently_have": "Phase 29 finding (data/actual_log.csv, dev/forecast_logger.py, "
                           "source=IMD_manual): a REAL, already-operational VOBL-only "
                           "6-hour-slot (4 slots/day) thunderstorm-occurrence ground-truth "
                           "log exists -- 360 slot-rows, 2026-04-01..2026-06-29 (90 days), "
                           "but only 7 positive slots in that entire window. This is genuine "
                           "subdaily data, not fabricated, but is a hard sample-size blocker "
                           "for training a meaningful subdaily model (7 positives is too few "
                           "for any legitimate held-out evaluation). Pan-India: none. "
                           "A separate, unintegrated patch module "
                           "(SIH_IMPLEMENTATION_PATCH_20260930_130455Z/changed_files/"
                           "metar_ground_truth.py) designs a more rigorous live-METAR-window "
                           "aggregation for this same VOBL/6h-slot ground truth, but was never "
                           "wired into the live pipeline and has not accumulated any history.",
        "classification": "BLOCKED-DATA (insufficient sample size, not a missing source)",
    },
    "CB": {
        "needed": "Sub-daily (<=6h window) precipitation-intensity ground truth "
                   "at pan-India scale, e.g. IMD 3-hourly or hourly AWS rain-gauge "
                   "or radar-derived QPE, to replace the current IMD DAILY "
                   "gridded rainfall (64.5mm/day) threshold.",
        "currently_have": "IMD daily gridded rainfall only (Phase 19/20/21). "
                           "GFS precipitation accumulation buckets exist at "
                           "6-hour resolution (f006/f012/f018/f024) but these are "
                           "FORECAST fields, not ground truth -- they cannot serve "
                           "as the sub-daily LABEL (only as predictors).",
        "classification": "BLOCKED-DATA",
    },
    "FF": {
        "needed": "Sub-daily (ideally hourly) streamflow/gauge-height "
                   "observations to timestamp a flash-flood ONSET rather than "
                   "just its reported Start Date.",
        "currently_have": "INDOFLOODS Start Date / End Date only (daily "
                           "resolution; see docs/FF_LABEL_READINESS.md). The "
                           "underlying metadata_indofloods.csv streamflow "
                           "record continuity is itself non-continuous for "
                           "most gauges, so even if sub-daily timestamps existed "
                           "in principle, the records available in this repo "
                           "do not carry them.",
        "classification": "BLOCKED-DATA",
    },
}


# ---------------------------------------------------------------------------
# Phase 29 Part C: the canonical lead-time DATA interface (distinct from the
# prediction-request/output contract above). This record type is what a
# future training/inference table for ANY hazard at ANY of the 5 supported
# lead slots (2h/3h/4h/5h/6h) must populate one row per
# (cell_id, hazard, init_time, lead_time) of. It intentionally carries no
# computed value by default -- a label is either a real observation (with
# real label_provenance) or explicitly None; nothing here fabricates one.
# This interface may exist, and is populated below with NOT_YET_TRAINED
# status rows, before any head is actually trained against it.
# ---------------------------------------------------------------------------

HAZARDS = ("TS", "CB", "FF")

STATUS_ENUM = ("TRAINED", "BASELINE", "PU_RANKING", "PROTOTYPE", "UNAVAILABLE", "NOT_YET_TRAINED")


@dataclass
class LeadTimeLabelRecord:
    """One canonical row: a specific hazard's label (if any) at a specific
    lead slot, for a specific cell, from a specific forecast initialization."""
    cell_id: str
    hazard: str                       # one of HAZARDS
    init_time_utc: datetime           # forecast initialization / analysis time
    valid_time_utc: datetime          # target time being forecast for
    lead_time_hours: int              # must be one of LEAD_HOURS
    label: Optional[int] = None       # 1 / 0 / None -- None means genuinely unknown, never defaulted to 0
    label_provenance: Optional[str] = None   # e.g. "OBSERVED", "POSITIVE_UNLABELED", None if no label exists
    label_source: Optional[str] = None       # file/dataset this label (if any) actually came from
    predictor_provenance: Optional[str] = None  # e.g. "GFS_FORECAST", "IMD_OBSERVED"
    status: str = "NOT_YET_TRAINED"

    def __post_init__(self):
        if self.hazard not in HAZARDS:
            raise ValueError(f"hazard must be one of {HAZARDS}, got {self.hazard}")
        if self.lead_time_hours not in LEAD_HOURS:
            raise ValueError(f"lead_time_hours must be one of {LEAD_HOURS}, got {self.lead_time_hours}")
        if self.status not in STATUS_ENUM:
            raise ValueError(f"status must be one of {STATUS_ENUM}, got {self.status}")
        implied = int((self.valid_time_utc - self.init_time_utc).total_seconds() // 3600)
        if implied != self.lead_time_hours:
            raise ValueError(
                f"lead_time_hours={self.lead_time_hours} does not match "
                f"valid_time_utc - init_time_utc = {implied}h -- the interface "
                f"never lets a mislabeled lead slot pass silently"
            )


def build_lead_time_label_table(cell_id: str, init_time_utc: datetime) -> list:
    """Returns one LeadTimeLabelRecord per (hazard, lead_hour) -- 3 hazards x
    5 lead hours = 15 rows -- for the given cell/init time. Every row's
    label is None and status is NOT_YET_TRAINED, because, per
    REQUIRED_SUBDAILY_LABEL_SOURCES, no hazard currently has a genuine,
    sufficient subdaily label source. This function exists so the
    interface (schema) is real and testable NOW, ahead of any hazard
    actually being trained against it -- it never repeats a daily label
    across lead slots and calls that a trained target."""
    from datetime import timedelta
    rows = []
    for hazard in HAZARDS:
        for h in LEAD_HOURS:
            rows.append(LeadTimeLabelRecord(
                cell_id=cell_id,
                hazard=hazard,
                init_time_utc=init_time_utc,
                valid_time_utc=init_time_utc + timedelta(hours=h),
                lead_time_hours=h,
                label=None,
                label_provenance=None,
                label_source=None,
                predictor_provenance="GFS_FORECAST",
                status="NOT_YET_TRAINED",
            ))
    return rows
