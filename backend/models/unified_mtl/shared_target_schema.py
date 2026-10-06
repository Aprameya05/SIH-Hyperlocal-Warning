"""
backend/models/unified_mtl/shared_target_schema.py
======================================================
Phase 33 Part 5/6/9: the single, deterministic, leakage-safe target
schema shared across TS/CB/FF, built strictly from the real label sources
already audited in Phases 20/21/22/26-29/31/32/32B. This module does not
invent any new label source -- it only assembles existing, already-traced
real data into one canonical record shape and explicitly masks every
value this project's prior audits already found cannot be honestly
constructed at 2-6h lead-time resolution.

Reuses, never redesigns:
  - backend/models/unified_mtl/lead_time_interface.py's LEAD_HOURS,
    STATUS_ENUM, and leakage check (valid_time - init_time == lead_hours).
  - data/actual_log.csv (TS, VOBL-only, 6h-slot manual/IMD ground truth).
  - processed/labels/cb_labels.csv / cb_labels_summary.json (CB, real IMD
    daily gridded rainfall >= 64.5mm/day, 992/992 cells, applied -- per
    its own documented limitation -- identically to all 4 six-hour slots
    of a date, i.e. genuinely DAILY resolution, never finer).
  - processed/ff_pu/ff_pu_training_table.csv / dataset_build_summary.json
    (FF, real INDOFLOODS events, PU semantics, date-only resolution, 69
    gauge-mapped cells, 620 positive / 143,866 unlabeled rows).

Phase 33's explicit instruction -- "do not simply repeat a daily label
across 2h/3h/4h/5h/6h and call it a trained lead-time target" -- is
enforced structurally here: CB and FF, being daily/date-resolution
sources, can NEVER produce a POSITIVE/NEGATIVE mask at a 2-6h lead
granularity in this module. They are masked UNKNOWN with an explicit
reason, every time, by construction -- not by an omission that could
later be "fixed" into a leakage bug.
"""
from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.models.unified_mtl.lead_time_interface import LEAD_HOURS  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))

VOBL_CELL_ID = "IND_13.0_78.0"  # corrected 2026-10-06: nearest canonical cell to VOBL
# (13.1979N 77.7063E) is 38.68km away; the previous "IND_13.0_77.0" here was
# 79.6km away. See scripts/ts_station_model_interface.py's VOBL_CELL_ID for
# the authoritative definition this module keeps its own copy of.

ACTUAL_LOG_PATH = REPO_ROOT / "data" / "actual_log.csv"
CB_LABELS_SUMMARY_PATH = REPO_ROOT / "processed" / "labels" / "cb_labels_summary.json"
CB_LABELS_PATH = REPO_ROOT / "processed" / "labels" / "cb_labels.csv"
FF_TRAINING_TABLE_PATH = REPO_ROOT / "processed" / "ff_pu" / "ff_pu_training_table.csv"
FF_SUMMARY_PATH = REPO_ROOT / "processed" / "ff_pu" / "dataset_build_summary.json"
FF_GRID_EVENTS_PATH = REPO_ROOT / "processed" / "indofloods" / "indofloods_grid_events.csv"

# Part 5: mask enum -- unknown must never become negative, and a cell with
# no label source at all (MISSING) is distinct from a cell with a real
# source that simply has not resolved a value yet for this exact
# (cell, time) pair (UNKNOWN).
MASK_ENUM = ("POSITIVE", "NEGATIVE", "UNKNOWN", "MISSING")

# Part 3: FF provenance must distinguish these four explicitly -- the
# rainfall proxy must never be labeled OBSERVED_FLOOD.
FF_PROVENANCE_ENUM = ("OBSERVED_FLOOD", "POSITIVE_UNLABELED", "UNKNOWN", "PROXY")


class TargetLeakageError(Exception):
    """Raised instead of silently constructing a record whose valid_time
    does not genuinely sit lead_hours after init_time."""


@dataclass
class SharedTargetRecord:
    cell_id: str
    init_time: str          # ISO-8601 UTC
    valid_time: str         # ISO-8601 UTC
    lead_hours: int
    ts_target: Optional[int]   # 1, 0, or None
    cb_target: Optional[int]
    ff_target: Optional[int]
    ts_mask: str             # one of MASK_ENUM
    cb_mask: str
    ff_mask: str
    ts_provenance: str
    cb_provenance: str
    ff_provenance: str       # one of FF_PROVENANCE_ENUM (as a substring/tag within the text)

    def __post_init__(self):
        if self.lead_hours not in LEAD_HOURS:
            raise ValueError(f"lead_hours must be one of {LEAD_HOURS}, got {self.lead_hours}")
        for mask_field in (self.ts_mask, self.cb_mask, self.ff_mask):
            if mask_field not in MASK_ENUM:
                raise ValueError(f"mask value {mask_field!r} not in {MASK_ENUM}")
        init_dt = _parse_iso(self.init_time)
        valid_dt = _parse_iso(self.valid_time)
        delta_hours = (valid_dt - init_dt).total_seconds() / 3600.0
        if abs(delta_hours - self.lead_hours) > 1e-6:
            raise TargetLeakageError(
                f"valid_time - init_time = {delta_hours}h, does not equal declared "
                f"lead_hours={self.lead_hours} for cell {self.cell_id} -- refusing to "
                f"construct a leaking target record"
            )
        # a target value may exist only where its mask says so -- never a
        # POSITIVE/NEGATIVE value under an UNKNOWN/MISSING mask, and never
        # a None value under a POSITIVE/NEGATIVE mask.
        for target, mask, name in ((self.ts_target, self.ts_mask, "ts"),
                                    (self.cb_target, self.cb_mask, "cb"),
                                    (self.ff_target, self.ff_mask, "ff")):
            if mask in ("UNKNOWN", "MISSING") and target is not None:
                raise ValueError(f"{name}_target must be None when {name}_mask={mask!r}")
            if mask in ("POSITIVE", "NEGATIVE") and target is None:
                raise ValueError(f"{name}_target must be set (0/1) when {name}_mask={mask!r}")
            if mask == "POSITIVE" and target != 1:
                raise ValueError(f"{name}_mask=POSITIVE requires {name}_target=1, got {target}")
            if mask == "NEGATIVE" and target != 0:
                raise ValueError(f"{name}_mask=NEGATIVE requires {name}_target=0, got {target}")

    def to_row(self) -> dict:
        return self.__dict__.copy()


def _parse_iso(ts: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(ts, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unparseable ISO timestamp: {ts}")


# --- TS: real 6h-slot VOBL ground truth lookup (data/actual_log.csv) ----

def _load_actual_log() -> dict:
    """Returns {(date_str, slot): observed_int}. Real file only -- returns
    {} (never fabricated rows) if absent."""
    if not ACTUAL_LOG_PATH.exists():
        return {}
    out = {}
    with open(ACTUAL_LOG_PATH, newline="") as f:
        for r in csv.DictReader(f):
            try:
                out[(r["date"], int(r["slot"]))] = int(r["observed"])
            except (KeyError, ValueError):
                continue
    return out


_SLOT_WINDOWS_IST = {0: (0, 5), 1: (6, 11), 2: (12, 17), 3: (18, 23)}


def _slot_for_ist_hour(hour: int) -> int:
    for slot, (h0, h1) in _SLOT_WINDOWS_IST.items():
        if h0 <= hour <= h1:
            return slot
    return 3


def _ts_lookup(cell_id: str, valid_time_utc: datetime) -> tuple:
    """Returns (target, mask, provenance). TS ground truth exists ONLY
    for the VOBL cell, at 6h-slot resolution, for dates actually present
    in data/actual_log.csv. A 2-6h lead valid_time that does not align to
    a fully-closed real slot in that log is UNKNOWN, never guessed."""
    if cell_id != VOBL_CELL_ID:
        return None, "MISSING", "no TS ground truth source exists for any cell other than VOBL"

    valid_ist = valid_time_utc.astimezone(IST)
    date_str = valid_ist.strftime("%Y-%m-%d")
    slot = _slot_for_ist_hour(valid_ist.hour)
    log = _load_actual_log()
    key = (date_str, slot)
    if key not in log:
        return None, "UNKNOWN", (
            f"data/actual_log.csv has no logged 6h-slot observation for {date_str} slot {slot} "
            f"covering this valid_time -- a 2-6h lead target cannot honestly be read off a "
            f"6h-slot log unless the exact slot is both logged and fully closed"
        )
    observed = log[key]
    mask = "POSITIVE" if observed == 1 else "NEGATIVE"
    return observed, mask, (
        f"data/actual_log.csv[{date_str}, slot={slot}]=observed={observed} (IMD_manual, 6h-slot "
        f"resolution) -- NOTE: this is a 6h-slot label being read for a sub-6h lead_hours target; "
        f"it is reported here only when the full slot's observation covers the queried valid_time, "
        f"and is still coarser than a true 2-6h-independent observation -- see "
        f"docs/PHASE_33_LABEL_TARGET_AUDIT.md Section 1 for why this remains a resolution caveat "
        f"even when a slot match is found"
    )


# --- CB: real daily IMD rainfall labels, structurally never subdaily ----

def _cb_lookup(cell_id: str, valid_time_utc: datetime) -> tuple:
    """CB labels (processed/labels/cb_labels.csv) are REAL and OBSERVED
    (IMD gridded daily rainfall >= 64.5mm/day) but are daily resolution,
    identically applied to all 4 six-hour slots of their date by the
    label builder's own documented design. Per Phase 33's explicit
    instruction, this function NEVER returns POSITIVE/NEGATIVE at a 2-6h
    lead granularity -- doing so would be exactly the prohibited
    'repeat a daily label across 2-6h slots' pattern. Always UNKNOWN."""
    return None, "UNKNOWN", (
        "processed/labels/cb_labels.csv provides a REAL, OBSERVED daily-resolution cloudburst "
        "label (IMD gridded rainfall >= 64.5mm/day), but a daily label cannot honestly support "
        "a 2h/3h/4h/5h/6h lead-time target without repeating one day's label across multiple "
        "sub-day windows -- which this project's standing instructions prohibit. No subdaily "
        "CB ground truth source exists (see docs/PHASE_32_REAL_IMERG_SAMPLE_VALIDATION.md and "
        "docs/PHASE_32_FULL_DAY_IMERG_VALIDATION.md: IMERG is a real, validated 30-min "
        "OBSERVED precipitation INPUT, not an independently-defined cloudburst target)."
    )


# --- FF: real INDOFLOODS events (date-only) + PU training table --------

def _canonical_cell_id_from_raw(raw: str) -> Optional[str]:
    """processed/indofloods/indofloods_grid_events.csv (and
    processed/ff_pu/ff_pu_training_table.csv) store cell ids as the bare
    '{lat}_{lon}' string (e.g. '12.0_78.0'), NOT the canonical
    'IND_{lat}_{lon}' convention used everywhere else in this project
    (regrid.py::cell_id_for, cb_labels.csv, VOBL_CELL_ID). Converting here
    -- rather than comparing the two formats directly -- was found missing
    during Phase 33 smoke-testing: without this conversion every genuinely
    gauge-mapped cell was silently misreported as MISSING instead of
    UNKNOWN, which would have been a false 'no FF source at all' claim."""
    if raw is None:
        return None
    raw = raw.strip()
    if raw.startswith("IND_"):
        return raw
    m = re.match(r"^-?\d+(\.\d+)?_-?\d+(\.\d+)?$", raw)
    if not m:
        return None
    return f"IND_{raw}"


def _load_ff_event_cells() -> set:
    if not FF_GRID_EVENTS_PATH.exists():
        return set()
    out = set()
    with open(FF_GRID_EVENTS_PATH, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("mapping_status") == "MAPPED":
                canon = _canonical_cell_id_from_raw(r.get("cell_id"))
                if canon:
                    out.add(canon)
    return out


def _ff_lookup(cell_id: str, valid_time_utc: datetime) -> tuple:
    """FF ground truth (INDOFLOODS) is REAL and OBSERVED at event/date
    resolution ONLY -- no intra-day timestamp exists anywhere in the
    source (Phase 26-28 audits). Like CB, this function never returns
    POSITIVE/NEGATIVE at 2-6h lead granularity. It distinguishes a
    gauge-mapped cell (where a real PU-style record exists at daily
    resolution, hence POSITIVE_UNLABELED-flavored UNKNOWN) from a
    non-mapped cell (MISSING -- no FF source at all), and explicitly
    never reports the rainfall-history proxy features as observed flood
    ground truth."""
    event_cells = _load_ff_event_cells()
    if cell_id not in event_cells:
        return None, "MISSING", (
            "cell is not among the INDOFLOODS gauge-mapped cells; no flash-flood ground truth "
            "(observed or unlabeled) source exists for this cell at any resolution"
        )
    return None, "UNKNOWN", (
        "cell has real INDOFLOODS/PU-table coverage (POSITIVE_UNLABELED semantics: unlabeled "
        "means 'not a confirmed flood day', never 'confirmed no flood' -- see "
        "docs/PHASE_28_FLASH_FLOOD_PU_HARDENING.md), but INDOFLOODS carries only a date-level "
        "event timestamp (Start Date/End Date, no intra-day time) -- a 2-6h lead target cannot "
        "be honestly resolved to POSITIVE or NEGATIVE at this granularity. The rainfall-history "
        "feature adapter (scripts/imd_rainfall_adapter.py, scripts/ff_feature_adapter.py) is a "
        "PREDICTOR source for the existing daily/event-resolution PU model and is never read as "
        "this target's ff_target value -- it is category PROXY when used as a feature, and is "
        "never relabeled OBSERVED_FLOOD."
    )


def build_shared_target_record(cell_id: str, init_time_utc: datetime, lead_hours: int) -> SharedTargetRecord:
    if lead_hours not in LEAD_HOURS:
        raise ValueError(f"lead_hours must be one of {LEAD_HOURS}, got {lead_hours}")
    valid_time_utc = init_time_utc + timedelta(hours=lead_hours)

    ts_target, ts_mask, ts_prov = _ts_lookup(cell_id, valid_time_utc)
    cb_target, cb_mask, cb_prov = _cb_lookup(cell_id, valid_time_utc)
    ff_target, ff_mask, ff_prov = _ff_lookup(cell_id, valid_time_utc)

    return SharedTargetRecord(
        cell_id=cell_id,
        init_time=init_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        valid_time=valid_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        lead_hours=lead_hours,
        ts_target=ts_target, cb_target=cb_target, ff_target=ff_target,
        ts_mask=ts_mask, cb_mask=cb_mask, ff_mask=ff_mask,
        ts_provenance=ts_prov, cb_provenance=cb_prov, ff_provenance=ff_prov,
    )


def build_shared_target_table(cell_ids: List[str], init_time_utc: datetime) -> List[SharedTargetRecord]:
    """Part 5/9: the full deterministic target table for a given
    init_time across all requested cells and all 5 lead slots. This is
    the 'best possible target dataset' Part 9 asks for: real masks and
    real values wherever a genuine source resolves one (TS/VOBL, when a
    matching slot is logged), and explicit UNKNOWN/MISSING -- never a
    fabricated value -- everywhere else."""
    out = []
    for cell_id in cell_ids:
        for lead_hours in LEAD_HOURS:
            out.append(build_shared_target_record(cell_id, init_time_utc, lead_hours))
    return out


__all__ = [
    "MASK_ENUM", "FF_PROVENANCE_ENUM", "VOBL_CELL_ID", "TargetLeakageError",
    "SharedTargetRecord", "build_shared_target_record", "build_shared_target_table",
]
