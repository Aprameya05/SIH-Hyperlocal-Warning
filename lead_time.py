#!/usr/bin/env python3
"""
lead_time.py — explicit, mathematically-derived lead-time metadata for each
forecast slot, per the SIH PS's "2-6 hour actionable lead time" requirement.

Root cause this replaces: forecast_action.py's SLOT_NAMES only expressed
each slot as a fixed 6-hour IST calendar bucket ("1201-1800 IST"). Nothing
in forecast.json quantified how far ahead of the event window the forecast
was actually issued, so the PS's "2-6 hour lead time" claim had no field an
evaluator could check.

Definitions used here (all derived from real timestamps, never hardcoded):

  reference_time   -- when THIS forecast was actually produced, taken as the
                       GFS row's fetched_at_utc (gfs_row_select.GFSSelection).
                       This is "now" from the pipeline's point of view.
  valid_from/to     -- the IST slot window this forecast is FOR, converted to
                       UTC ISO-8601 with offset, from metar_ground_truth's
                       SLOT_WINDOWS_IST (the same table forecast_action.py and
                       fetch_metar.py already agree on for slot boundaries).
  lead_time_hours   -- hours between reference_time and valid_from. This is
                       the actionable lead time: how far ahead of the event
                       window the operational forecast was issued using data
                       fetched at reference_time. If reference_time already
                       falls inside or after the window (the forecast run is
                       late, or this is the currently-open slot), lead time is
                       clipped at 0 and marked accordingly rather than
                       reported as a misleading negative number.
  source_cycle/fhour -- the underlying GFS model cycle and forecast hour that
                       produced the row, taken from the same GFSSelection so
                       this always matches what select_latest_gfs() actually
                       picked (see gfs_row_select.py).

If reference_time cannot be determined (e.g. fetched_at_utc missing/
unparsable, or no valid GFS row at all), this module returns an explicit
unavailable state -- it never fabricates a lead-time number.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

# Must match metar_ground_truth.SLOT_WINDOWS_IST exactly (hour, minute) pairs
# for (start, end) of each slot, in IST. Duplicated here (not imported) to
# keep this module import-light for forecast_action.py / compute_realtime_shap.py;
# a mismatch would be caught by test_lead_time.py's cross-check test.
SLOT_WINDOWS_IST = {
    0: (0, 0, 5, 59),
    1: (6, 0, 11, 59),
    2: (12, 0, 17, 59),
    3: (18, 0, 23, 59),
}


@dataclass
class LeadTime:
    available: bool
    hours: Optional[float]
    valid_from: Optional[str]      # ISO-8601 UTC
    valid_to: Optional[str]        # ISO-8601 UTC
    reference_time: Optional[str]  # ISO-8601 UTC
    source_cycle: Optional[str]
    source_fhour: Optional[int]
    reason: str
    clipped: bool = False  # True if the run was late / window already open

    def to_json(self) -> dict:
        d = asdict(self)
        return d


def _slot_window_utc(date_str: str, slot: int):
    """Return (start_utc, end_utc) datetimes for the given IST calendar date
    and slot, converted to UTC. date_str is 'YYYY-MM-DD' (the IST calendar
    date the slot belongs to, matching forecast_action.py's date_str)."""
    h0, m0, h1, m1 = SLOT_WINDOWS_IST[slot]
    year, month, day = (int(x) for x in date_str.split("-"))
    start_ist = datetime(year, month, day, h0, m0, tzinfo=IST)
    if slot == 3:
        end_ist = datetime(year, month, day, 23, 59, 59, tzinfo=IST)
    else:
        end_ist = datetime(year, month, day, h1, m1, 59, tzinfo=IST)
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


def compute_lead_time(date_str: str, slot: int,
                       reference_time_utc,
                       gfs_cycle: Optional[str] = None,
                       gfs_fhour: Optional[int] = None) -> LeadTime:
    """
    reference_time_utc: a pandas.Timestamp / datetime (naive-UTC or tz-aware),
    or None -- normally GFSSelection.fetched_at_utc.
    """
    if slot not in SLOT_WINDOWS_IST:
        return LeadTime(False, None, None, None, None, gfs_cycle, gfs_fhour,
                         reason=f"unknown slot id {slot!r}")

    try:
        valid_from_utc, valid_to_utc = _slot_window_utc(date_str, slot)
    except Exception as e:
        return LeadTime(False, None, None, None, None, gfs_cycle, gfs_fhour,
                         reason=f"could not derive slot window from date_str={date_str!r}: {e}")

    if reference_time_utc is None:
        return LeadTime(False, None,
                         valid_from_utc.isoformat(), valid_to_utc.isoformat(),
                         None, gfs_cycle, gfs_fhour,
                         reason="reference_time unavailable (no valid GFS fetched_at_utc for this run)")

    # Normalize to tz-aware UTC.
    ref = reference_time_utc
    try:
        ref = ref.to_pydatetime() if hasattr(ref, "to_pydatetime") else ref
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        else:
            ref = ref.astimezone(timezone.utc)
    except Exception as e:
        return LeadTime(False, None,
                         valid_from_utc.isoformat(), valid_to_utc.isoformat(),
                         None, gfs_cycle, gfs_fhour,
                         reason=f"malformed reference_time: {e}")

    delta_hours = (valid_from_utc - ref).total_seconds() / 3600.0
    clipped = delta_hours < 0
    lead_hours = max(0.0, round(delta_hours, 2))

    reason = ("lead_time = valid_from - reference_time (GFS fetch time)")
    if clipped:
        reason = ("reference_time falls inside or after the slot window "
                   "(late/current-slot run) -- lead time clipped to 0, not reported negative")

    return LeadTime(
        available=True,
        hours=lead_hours,
        valid_from=valid_from_utc.isoformat(),
        valid_to=valid_to_utc.isoformat(),
        reference_time=ref.isoformat(),
        source_cycle=gfs_cycle,
        source_fhour=gfs_fhour,
        reason=reason,
        clipped=clipped,
    )
