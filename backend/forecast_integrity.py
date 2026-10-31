"""
backend/forecast_integrity.py
=============================
M0 (fix/m0-forecast-integrity): one pure, I/O-free place that defines what
the time fields of a DRIFT forecast MEAN and decides whether a hazard value
may be presented as a current, lead-specific forecast.

Audit findings this module exists to prevent (CRITICAL_BUGS #1, #6, #16):
  * records stamped init_time=2024-08-01 but generated_at=2026 and labelled
    TRAINED / MIXED_LIVE_SOURCES;
  * one DAILY cloudburst value copied to five "2-6 h" lead slots;
  * operational lead time derived from model initialisation time alone.

Time vocabulary (use these names everywhere; see TIME_DEFINITIONS):
  model_init_time_utc   NWP cycle the inputs came from (e.g. GFS 18Z run)
  data_acquired_at_utc  when DRIFT downloaded the inputs (None if not recorded)
  artifact_generated_at_utc  when the artifact file was built
  issued_at_utc         when DRIFT issued the product. Equal to generation time:
                        no separate publication timestamp is measured, so
                        deploy/CDN lag is NOT included (documented, not hidden)
  valid_time_utc        instant (or window) the forecast refers to
  nwp_lead_hours        valid - model_init   (a model property)
  operational_lead_hours  valid - issued_at  (what a user actually gets; may
                        be <= 0, meaning the product is not a forecast)

Nothing here fabricates or alters a number: values that may not be shown as
current are WITHHELD (probability -> None, risk -> NOT_AVAILABLE) and the
original values are preserved under `historical_reference` with their
original timestamps.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Optional

SCHEMA_VERSION = 2

# A hazard value whose model init is older than this, relative to the time the
# artifact was issued, is a historical replay, not a forecast of the present.
HISTORICAL_AFTER_HOURS = 48.0
# Issued longer ago than this (at request/now time) -> STALE.
STALE_AFTER_HOURS = 12.0

# CB's trained feature table is a whole-day aggregate over GFS leads f006..f027
# (see backend/unified_api.py ff/cb notes and scripts/panindia_cb_features.py).
CB_DAILY_WINDOW_START_H = 6
CB_DAILY_WINDOW_END_H = 27

LEAD_NOT_SPECIFIC = "DAILY_AGGREGATE_NOT_LEAD_SPECIFIC"
HAZARDS = ("TS", "CB", "FF")


class TemporalStatus:
    CURRENT = "CURRENT"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    NOT_LEAD_SPECIFIC = "NOT_LEAD_SPECIFIC"
    UNAVAILABLE = "UNAVAILABLE"


# Statuses under which a value may be shown as a current forecast.
PRESENTABLE_AS_CURRENT = frozenset({TemporalStatus.CURRENT})

# Value-type vocabulary shared by API and UI.
VALUE_TYPES = ("OBSERVATION", "NWP_FIELD", "HEURISTIC_RISK_SCORE", "MODEL_SCORE_UNCALIBRATED",
               "CALIBRATED_PROBABILITY", "ADVISORY_LEVEL", "EXTERNAL_OFFICIAL_WARNING")

TIME_DEFINITIONS = {
    "model_init_time_utc": "Initialisation time of the NWP cycle the inputs came from.",
    "data_acquired_at_utc": "When DRIFT downloaded the inputs; null when not recorded.",
    "artifact_generated_at_utc": "When this artifact file was built.",
    "issued_at_utc": ("When DRIFT issued this product. Equals artifact generation time; deploy/CDN "
                      "delay is not measured and is not included."),
    "valid_time_utc": "Instant (or window start/end) the forecast refers to.",
    "nwp_lead_hours": "valid_time - model_init_time. A model property, NOT warning lead time.",
    "operational_lead_hours": ("valid_time - issued_at. The lead a user actually receives. "
                               "<= 0 means the value is not a forecast of the future."),
}


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------

def parse_utc(value) -> Optional[datetime]:
    """Parse the ISO-8601 shapes found in DRIFT artifacts ('...Z', '+00:00',
    with/without microseconds, date-only) to an aware UTC datetime.
    Returns None for anything unparseable (never guesses)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def operational_lead_hours(valid_time, issued_at) -> Optional[float]:
    """Hours from ISSUE to valid time. Deliberately has no model-init argument:
    operational lead must never be derived from initialisation time alone."""
    v, i = parse_utc(valid_time), parse_utc(issued_at)
    if v is None or i is None:
        return None
    return round((v - i).total_seconds() / 3600.0, 3)


def nwp_lead_hours(valid_time, model_init_time) -> Optional[float]:
    v, i = parse_utc(valid_time), parse_utc(model_init_time)
    if v is None or i is None:
        return None
    return round((v - i).total_seconds() / 3600.0, 3)


def classify_time(*, model_init, valid_end, issued_at, now=None) -> str:
    """CURRENT / STALE / EXPIRED / HISTORICAL_REPLAY / UNAVAILABLE for a value."""
    now = parse_utc(now) or datetime.now(timezone.utc)
    init, valid_end_dt, issued = parse_utc(model_init), parse_utc(valid_end), parse_utc(issued_at)
    if init is None or issued is None:
        return TemporalStatus.UNAVAILABLE
    if (issued - init).total_seconds() / 3600.0 > HISTORICAL_AFTER_HOURS:
        return TemporalStatus.HISTORICAL_REPLAY
    if valid_end_dt is not None and valid_end_dt <= now:
        return TemporalStatus.EXPIRED
    if (now - issued).total_seconds() / 3600.0 > STALE_AFTER_HOURS:
        return TemporalStatus.STALE
    return TemporalStatus.CURRENT


# ---------------------------------------------------------------------------
# hazard-block sanitising
# ---------------------------------------------------------------------------

_SOURCE_STATUS_FOR = {
    TemporalStatus.HISTORICAL_REPLAY: "HISTORICAL_REPLAY",
    TemporalStatus.NOT_LEAD_SPECIFIC: "NOT_LEAD_SPECIFIC",
    TemporalStatus.EXPIRED: "EXPIRED",
    TemporalStatus.STALE: "STALE",
    TemporalStatus.UNAVAILABLE: "UNAVAILABLE",
}

_WITHHELD_STATUS_FOR = {
    TemporalStatus.HISTORICAL_REPLAY: "HISTORICAL_REPLAY_NOT_A_CURRENT_FORECAST",
    TemporalStatus.NOT_LEAD_SPECIFIC: "LEAD_TIME_NOT_SUPPORTED",
    TemporalStatus.EXPIRED: "FORECAST_EXPIRED",
    TemporalStatus.STALE: "FORECAST_STALE",
    TemporalStatus.UNAVAILABLE: "UNAVAILABLE",
}


def withhold_hazard_block(block: dict, *, temporal_status: str, reason: str,
                          model_init, valid_time, issued_at,
                          preserve: bool = True) -> dict:
    """Return a copy of `block` that can no longer be read as a current
    forecast: probability None, risk NOT_AVAILABLE, explicit status. The
    original values are kept under `historical_reference` (preserve=True) with
    their ORIGINAL timestamps; nothing is deleted or invented."""
    out = copy.deepcopy(block)
    ref = {
        "designation": temporal_status,
        "probability": block.get("probability"),
        "risk_category": block.get("risk_category", block.get("risk")),
        "status": block.get("status"),
        "source_status": block.get("source_status"),
        "source_cycle": block.get("source_cycle"),
        "model_init_time_utc": iso_z(parse_utc(model_init)),
        "valid_time_utc": iso_z(parse_utc(valid_time)),
        "note": reason,
    }
    extra = out.get("extra") or {}
    if extra.get("pu_ranking_score") is not None:
        ref["pu_ranking_score"] = extra["pu_ranking_score"]
        extra = dict(extra)
        extra["pu_ranking_score"] = None
        extra["pu_ranking_score_withheld_reason"] = reason
        out["extra"] = extra
    # Local explanations (SHAP etc.) describe the withheld value, so they must not stay attached
    # to a block that now shows no forecast. Preserve them in the reference only.
    xai = block.get("xai") or (block.get("extra") or {}).get("xai")
    if xai:
        ref["xai"] = xai
        withheld_xai = {"status": "WITHHELD", "reason": reason}
        if "xai" in out:
            out["xai"] = withheld_xai
        if isinstance(out.get("extra"), dict) and "xai" in out["extra"]:
            out["extra"] = dict(out["extra"])
            out["extra"]["xai"] = withheld_xai
    if preserve:
        out["historical_reference"] = ref
    out["probability"] = None
    if "risk_category" in out or "risk" not in out:
        out["risk_category"] = "NOT_AVAILABLE"
    if "risk" in out:
        out["risk"] = "NOT_AVAILABLE"
    out["status"] = _WITHHELD_STATUS_FOR.get(temporal_status, "UNAVAILABLE")
    out["source_status"] = _SOURCE_STATUS_FOR.get(temporal_status, "UNAVAILABLE")
    out["confidence"] = "NOT_AVAILABLE"
    out["temporal_status"] = temporal_status
    out["presentable_as_current_forecast"] = False
    out["unavailable_reason"] = reason
    out["model_init_time_utc"] = iso_z(parse_utc(model_init))
    out["valid_time_utc"] = iso_z(parse_utc(valid_time))
    out["nwp_lead_hours"] = nwp_lead_hours(valid_time, model_init)
    out["operational_lead_hours"] = operational_lead_hours(valid_time, issued_at)
    return out


def annotate_current_block(block: dict, *, model_init, valid_time, issued_at, temporal_status) -> dict:
    out = dict(block)
    out["temporal_status"] = temporal_status
    out["presentable_as_current_forecast"] = temporal_status in PRESENTABLE_AS_CURRENT
    out["model_init_time_utc"] = iso_z(parse_utc(model_init))
    out["valid_time_utc"] = iso_z(parse_utc(valid_time))
    out["nwp_lead_hours"] = nwp_lead_hours(valid_time, model_init)
    out["operational_lead_hours"] = operational_lead_hours(valid_time, issued_at)
    return out


def _hazard_model_init(record: dict, block: dict):
    """The init time a hazard's inputs really came from. CB carries its own
    (cb_init_time_utc); TS/FF use the record-level fixed replay cycle."""
    return block.get("cb_init_time_utc") or record.get("init_time")


def sanitize_record_hazard(record: dict, hz: str, *, issued_at, now=None) -> dict:
    """Sanitise one hazard block of a lead-slot record. Idempotent."""
    block = record[hz]
    if "temporal_status" in block:
        return block
    init = _hazard_model_init(record, block)
    valid_time = record.get("valid_time")
    if block.get("source_status") == "FIXED_VALIDATION_FALLBACK":
        status = TemporalStatus.HISTORICAL_REPLAY
    else:
        status = classify_time(model_init=init, valid_end=valid_time, issued_at=issued_at, now=now)

    if block.get("lead_time_resolution") == LEAD_NOT_SPECIFIC and block.get("probability") is not None:
        return withhold_hazard_block(
            block, temporal_status=TemporalStatus.NOT_LEAD_SPECIFIC,
            reason=("The model output is a single whole-day aggregate (GFS f006..f027). It cannot be "
                    "split into independent 2/3/4/5/6 h lead forecasts, so this lead slot is unavailable. "
                    "See artifact.daily_outlook for the daily value."),
            model_init=init, valid_time=valid_time, issued_at=issued_at, preserve=False)
    if status in (TemporalStatus.HISTORICAL_REPLAY, TemporalStatus.UNAVAILABLE) and (
            block.get("probability") is not None
            or (block.get("extra") or {}).get("pu_ranking_score") is not None):
        return withhold_hazard_block(
            block, temporal_status=status,
            reason=(f"Inputs are not current: the record's model init is {iso_z(parse_utc(init))}, more "
                    f"than {int(HISTORICAL_AFTER_HOURS)} h before this artifact was issued "
                    f"({iso_z(parse_utc(issued_at))}). TS and FF have no live data source wired in. "
                    f"Retained for research/replay only; not a forecast of the present."),
            model_init=init, valid_time=valid_time, issued_at=issued_at, preserve=True)
    return annotate_current_block(block, model_init=init, valid_time=valid_time,
                                  issued_at=issued_at, temporal_status=status)


def build_daily_outlook(artifact: dict, *, issued_at, now=None) -> dict:
    """Per-cell DAILY CB outlook taken from the lead slots' duplicated daily
    aggregate. Only genuinely current-cycle values are included. This is the
    single honest place the daily value lives; it makes no lead-time claim."""
    out = {}
    for rec in artifact.get("records", []):
        cb = rec.get("CB") or {}
        cid = rec["cell_id"]
        if cid in out or cb.get("probability") is None:
            continue
        if cb.get("lead_time_resolution") != LEAD_NOT_SPECIFIC:
            continue
        init = parse_utc(cb.get("cb_init_time_utc"))
        if init is None or cb.get("source_status") == "FIXED_VALIDATION_FALLBACK":
            continue
        w_start = init + timedelta(hours=CB_DAILY_WINDOW_START_H)
        w_end = init + timedelta(hours=CB_DAILY_WINDOW_END_H)
        ts = classify_time(model_init=init, valid_end=w_end, issued_at=issued_at, now=now)
        out[cid] = {"CB": {
            "probability": cb["probability"],
            "risk_category": cb.get("risk_category"),
            "model_version": cb.get("model_version"),
            "source_status": cb.get("source_status"),
            "model_init_time_utc": iso_z(init),
            "window_start_utc": iso_z(w_start),
            "window_end_utc": iso_z(w_end),
            "resolution": "DAILY_AGGREGATE",
            "lead_specific": False,
            "event_class": "HEAVY_RAIN_DAY_PROXY",
            "event_definition": ("Model of IMD gridded daily rainfall >= 64.5 mm at 0.25 deg "
                                 "(heavy-rain day). NOT a verified hourly cloudburst."),
            "value_type": "MODEL_SCORE_UNCALIBRATED",
            "temporal_status": ts,
            "presentable_as_current_forecast": ts in PRESENTABLE_AS_CURRENT,
            "operational_lead_hours_to_window_start": operational_lead_hours(w_start, issued_at),
            "nwp_lead_hours_to_window_start": float(CB_DAILY_WINDOW_START_H),
        }}
    return out


def sanitize_artifact(artifact: dict, *, now=None) -> dict:
    """Return a sanitised deep copy of a unified-forecast artifact (v1 legacy
    or v2). Idempotent. After this call no hazard value is presentable as a
    current lead-specific forecast unless it truly is."""
    if artifact.get("schema_version", 1) >= SCHEMA_VERSION and artifact.get("integrity", {}).get("applied"):
        return artifact
    now_dt = parse_utc(now) or datetime.now(timezone.utc)
    art = copy.deepcopy(artifact)
    issued_at = parse_utc(art.get("generated_at_utc"))

    art["daily_outlook"] = build_daily_outlook(art, issued_at=issued_at, now=now_dt)
    counts = {"current_lead_specific": 0, "withheld_historical": 0,
              "withheld_not_lead_specific": 0, "withheld_other": 0}
    for rec in art.get("records", []):
        rec["record_class"] = "LEAD_SLOT"
        for hz in HAZARDS:
            if hz not in rec:
                continue
            rec[hz] = sanitize_record_hazard(rec, hz, issued_at=issued_at, now=now_dt)
            ts = rec[hz].get("temporal_status")
            if ts == TemporalStatus.CURRENT and rec[hz].get("probability") is not None:
                counts["current_lead_specific"] += 1
            elif ts == TemporalStatus.HISTORICAL_REPLAY and "historical_reference" in rec[hz]:
                counts["withheld_historical"] += 1
            elif ts == TemporalStatus.NOT_LEAD_SPECIFIC:
                counts["withheld_not_lead_specific"] += 1
            elif rec[hz].get("probability") is None and ts not in (None,):
                counts["withheld_other"] += 1

    cb_init = (art.get("cb_live_fetch") or {}).get("init_time_utc")
    cb_live = (art.get("cb_live_fetch") or {}).get("status") in (
        "LIVE_AWS_GFS", "LIVE_NOMADS_GFS", "MIXED_LIVE_SOURCES")
    legacy_init = art.get("init_time_utc")
    art["schema_version"] = SCHEMA_VERSION
    art["artifact_mode"] = "OPERATIONAL_DAILY_OUTLOOK_ONLY" if (cb_live and art["daily_outlook"]) \
        else "HISTORICAL_REPLAY_ONLY"
    art["time_semantics"] = {
        "definitions": TIME_DEFINITIONS,
        "artifact_generated_at_utc": iso_z(issued_at),
        "issued_at_utc": iso_z(issued_at),
        "published_at_utc": None,
        "data_acquired_at_utc": None,
        "model_init_times_utc": {
            "CB_daily_outlook": iso_z(parse_utc(cb_init)) if cb_live else None,
            "TS_replay": iso_z(parse_utc(legacy_init)),
            "FF_replay": iso_z(parse_utc(legacy_init)),
        },
        "legacy_field_note": ("Top-level init_time_utc / source_cycle_date and record-level init_time / "
                              "valid_time describe the FIXED 2024-08-01 REPLAY cycle used by TS and FF. "
                              "They are NOT the model init of current data. Use the hazard-level "
                              "model_init_time_utc / valid_time_utc / operational_lead_hours fields."),
        "published_at_note": "Not measured; deploy/CDN delay is excluded from operational_lead_hours.",
        "data_acquired_at_note": "The upstream builder does not record download time; null, not guessed.",
    }
    art["forecast_horizon_support"] = {
        "lead_specific_forecasts_available": counts["current_lead_specific"] > 0,
        "supported_operational_horizons_hours": [],
        "lead_slots_2_to_6h": "UNAVAILABLE: no genuine lead-specific forecast exists yet.",
        "daily_outlook_available_cells": len(art["daily_outlook"]),
    }
    art["integrity"] = {"applied": True, "applied_at_utc": iso_z(now_dt), "counts": counts,
                        "module": "backend/forecast_integrity.py"}
    cov = art.get("coverage_summary") or {}
    cov["n_cb_records_with_probability"] = sum(1 for r in art["records"]
                                               if (r.get("CB") or {}).get("probability") is not None)
    cov["n_ts_records_with_probability"] = sum(1 for r in art["records"]
                                               if (r.get("TS") or {}).get("probability") is not None)
    cov["n_cb_daily_outlook_cells"] = len(art["daily_outlook"])
    cov["n_ff_records_with_pu_ranking_score"] = sum(
        1 for r in art["records"] if ((r.get("FF") or {}).get("extra") or {}).get("pu_ranking_score") is not None)
    cov["integrity_note"] = ("Counts are of values presentable as current. Withheld historical/duplicated "
                             "values are preserved under historical_reference or daily_outlook.")
    art["coverage_summary"] = cov
    return art


def refresh_request_time_status(artifact: dict, *, now=None) -> dict:
    """Re-evaluate time-dependent status against REQUEST time. An artifact that
    was CURRENT when built is STALE/EXPIRED hours later; this only ever
    downgrades (never upgrades) and returns a copy. Withheld values stay
    withheld."""
    now_dt = parse_utc(now) or datetime.now(timezone.utc)
    art = copy.deepcopy(artifact)
    issued_at = art.get("generated_at_utc")
    changed = 0
    for cid, entry in (art.get("daily_outlook") or {}).items():
        cb = entry.get("CB") or {}
        if cb.get("temporal_status") != TemporalStatus.CURRENT:
            continue
        ts = classify_time(model_init=cb.get("model_init_time_utc"), valid_end=cb.get("window_end_utc"),
                           issued_at=issued_at, now=now_dt)
        if ts != TemporalStatus.CURRENT:
            cb["temporal_status"] = ts
            cb["presentable_as_current_forecast"] = False
            cb["source_status"] = _SOURCE_STATUS_FOR.get(ts, cb.get("source_status"))
            changed += 1
    for rec in art.get("records", []):
        for hz in HAZARDS:
            block = rec.get(hz)
            if not block or block.get("temporal_status") != TemporalStatus.CURRENT or block.get("probability") is None:
                continue
            ts = classify_time(model_init=block.get("model_init_time_utc"), valid_end=block.get("valid_time_utc"),
                               issued_at=issued_at, now=now_dt)
            if ts != TemporalStatus.CURRENT:
                rec[hz] = withhold_hazard_block(
                    block, temporal_status=ts,
                    reason=f"Became {ts} at request time {iso_z(now_dt)} (issued {iso_z(parse_utc(issued_at))}).",
                    model_init=block.get("model_init_time_utc"), valid_time=block.get("valid_time_utc"),
                    issued_at=issued_at, preserve=True)
                changed += 1
    art.setdefault("integrity", {})["request_time_evaluated_at_utc"] = iso_z(now_dt)
    art["integrity"]["request_time_downgrades"] = changed
    return art


def eligible_for_alert(hazard_block: dict, *, hazard: str, valid_time, now=None) -> tuple[bool, list]:
    """Single source of truth for 'may this value be dispatched as an alert?'.
    Used by BOTH the candidate listing and the dispatch gate so they cannot
    disagree (they did before M0)."""
    reasons = []
    if hazard == "FF":
        reasons.append("FF has no legitimate probability and is never alert-eligible")
    if hazard_block.get("probability") is None:
        reasons.append("no probability is available for this slot")
    ts = hazard_block.get("temporal_status")
    if ts != TemporalStatus.CURRENT:
        reasons.append(f"temporal_status is {ts!r}, not CURRENT")
    v = parse_utc(valid_time)
    now_dt = parse_utc(now) or datetime.now(timezone.utc)
    if v is None:
        reasons.append("valid_time could not be parsed")
    elif v <= now_dt:
        reasons.append(f"valid_time {iso_z(v)} is not in the future")
    return (not reasons, reasons)
