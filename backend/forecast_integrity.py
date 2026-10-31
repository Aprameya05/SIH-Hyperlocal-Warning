"""
backend/forecast_integrity.py
=============================
Independent integrity verification for DRIFT artifacts (unified forecast, pan-India grid, VOBL forecast).

Principle: integrity is RECOMPUTED from timestamps, provenance, source metadata and forecast semantics.
Labels the artifact carries about itself (temporal_status, integrity.applied, validation, presentable_as_current_
forecast, lead_time_resolution) are outputs to be cross-checked, never inputs to a decision. A self-declared
flag can never make an artifact valid.

Time vocabulary (no field is derived from another unless stated):
  model_init_time_utc         NWP cycle the inputs came from
  data_acquired_at_utc        when inputs were downloaded (null unless the pipeline recorded it; never invented)
  generated_at_utc            when the artifact file was built (builder clock)
  forecast_available_time_utc when the forecast became available to users (null unless OBSERVED; never invented)
  valid_time_utc              the instant/window the forecast refers to
  nwp_forecast_hour           valid - model_init            (a model property)
  operational_lead_hours      valid - forecast_available    (null unless availability time is genuinely recorded)
Model init and generation time are NEVER substitutes for availability.

Withholding: any value that may not be shown as a current forecast has probability -> null, risk -> NOT_AVAILABLE,
xai -> WITHHELD; the original values are preserved under `research_reference` (alias `historical_reference`),
never deleted. PU ranking scores are permanently research-only (they are not probabilities).
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Optional

SCHEMA_VERSION = 3
GRID_CONTRACT_VERSION = 1

HISTORICAL_AFTER_HOURS = 48.0     # model init older than this before generation => historical replay
STALE_AFTER_HOURS = 12.0          # generated longer ago than this (at request time) => STALE
MAX_NWP_HORIZON_HOURS = 384.0
CB_DAILY_WINDOW_START_H = 6       # panindia_cb_v1 aggregates GFS f006..f027 (scripts/gfs_live_cb_predictors.CB_LEADS_HOURS)
CB_DAILY_WINDOW_END_H = 27
TARGET_LEAD_WINDOW_HOURS = (2, 6)

LEAD_NOT_SPECIFIC = "DAILY_AGGREGATE_NOT_LEAD_SPECIFIC"
HAZARDS = ("TS", "CB", "FF")
# Heads that are trained AND validated per lead. EMPTY in production (no such head exists today); tests inject.
LEAD_CAPABLE_HEADS: frozenset = frozenset()
# Synthetic fixtures (provenance.synthetic_fixture / synthetic_fixture on a block) are INVALID_PROVENANCE in
# production. Contract tests flip this to True to exercise the lead-specific rules; it proves the verifier's
# contract ONLY and is never evidence that DRIFT can forecast by lead.
ALLOW_SYNTHETIC_FIXTURES: bool = False
OBSERVED_AVAILABILITY = "OBSERVED_PUBLICATION_CONFIRMATION"


class TemporalStatus:
    CURRENT = "CURRENT"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    NOT_LEAD_SPECIFIC = "NOT_LEAD_SPECIFIC"
    INVALID_PROVENANCE = "INVALID_PROVENANCE"
    UNAVAILABLE = "UNAVAILABLE"


PRESENTABLE_AS_CURRENT = frozenset({TemporalStatus.CURRENT})

VALUE_TYPES = ("OBSERVATION", "NWP_FIELD", "HEURISTIC_RISK_SCORE", "MODEL_SCORE_UNCALIBRATED",
               "CALIBRATED_PROBABILITY", "ADVISORY_LEVEL", "EXTERNAL_OFFICIAL_WARNING", "RESEARCH_ONLY_PU_RANKING")

TIME_DEFINITIONS = {
    "model_init_time_utc": "Initialisation time of the NWP cycle the inputs came from.",
    "data_acquired_at_utc": "When DRIFT downloaded the inputs; null unless the pipeline recorded it (never invented).",
    "generated_at_utc": "When this artifact file was built (builder clock).",
    "forecast_available_time_utc": ("When the forecast became available to users; null unless an observed "
                                    "publication confirmation exists (never inferred from generation or init)."),
    "valid_time_utc": "Instant (or window start/end) the forecast refers to.",
    "nwp_forecast_hour": "valid_time - model_init_time. A model property, NOT warning lead time.",
    "operational_lead_hours": ("valid_time - forecast_available_time. null with a status unless availability is "
                               "recorded; never negative; never derived from model init or generation time."),
}


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------

def parse_utc(value) -> Optional[datetime]:
    """ISO-8601 shapes found in DRIFT artifacts -> aware UTC datetime; None for anything unparseable (never guesses)."""
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
    return None if dt is None else dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def operational_lead_hours(valid_time, available_time) -> Optional[float]:
    """valid - AVAILABILITY time. Deliberately has no model-init or generation argument. Never negative:
    a non-positive lead is not a forecast lead, so it returns None."""
    v, a = parse_utc(valid_time), parse_utc(available_time)
    if v is None or a is None:
        return None
    h = (v - a).total_seconds() / 3600.0
    return round(h, 3) if h > 0 else None


def nwp_lead_hours(valid_time, model_init_time) -> Optional[float]:
    """valid - model init; None unless valid >= init and within a plausible NWP horizon (same time basis)."""
    v, i = parse_utc(valid_time), parse_utc(model_init_time)
    if v is None or i is None:
        return None
    h = (v - i).total_seconds() / 3600.0
    return round(h, 3) if 0 <= h <= MAX_NWP_HORIZON_HOURS else None


def lead_metadata(model_init, valid_start, generated_at, available_time=None) -> dict:
    """The only producer of lead fields. Returns nulls with explicit statuses when provenance is insufficient."""
    init, valid, gen, avail = (parse_utc(model_init), parse_utc(valid_start), parse_utc(generated_at),
                               parse_utc(available_time))
    out = {"nwp_forecast_hour": None, "nwp_forecast_hour_status": "UNAVAILABLE_NO_TIME",
           "operational_lead_hours": None, "operational_lead_status": "UNAVAILABLE_NO_AVAILABILITY_TIME"}
    if init is not None and valid is not None:
        h = nwp_lead_hours(valid, init)
        if h is None:
            out["nwp_forecast_hour_status"] = "UNAVAILABLE_INCONSISTENT_TIMES"
        else:
            out["nwp_forecast_hour"], out["nwp_forecast_hour_status"] = h, "OK"
    if valid is not None and gen is not None and valid <= gen:
        out["operational_lead_status"] = "NOT_APPLICABLE_PAST_VALID_TIME"   # availability >= generation, so lead <= 0
    elif avail is not None and valid is not None:
        if gen is not None and avail < gen:
            out["operational_lead_status"] = "UNAVAILABLE_INCONSISTENT_TIMES"
        else:
            lead = operational_lead_hours(valid, avail)
            if lead is None:
                out["operational_lead_status"] = "NOT_APPLICABLE_PAST_VALID_TIME"
            else:
                out["operational_lead_hours"], out["operational_lead_status"] = lead, "OK"
    return out


def classify_time(*, model_init, valid_end, issued_at, now=None) -> str:
    """CURRENT / STALE / EXPIRED / HISTORICAL_REPLAY / UNAVAILABLE from timestamps only (issued_at = generation)."""
    now = parse_utc(now) or datetime.now(timezone.utc)
    init, vend, gen = parse_utc(model_init), parse_utc(valid_end), parse_utc(issued_at)
    if init is None or gen is None:
        return TemporalStatus.UNAVAILABLE
    if (gen - init).total_seconds() / 3600.0 > HISTORICAL_AFTER_HOURS:
        return TemporalStatus.HISTORICAL_REPLAY
    if vend is not None and vend <= now:
        return TemporalStatus.EXPIRED
    if (now - gen).total_seconds() / 3600.0 > STALE_AFTER_HOURS:
        return TemporalStatus.STALE
    return TemporalStatus.CURRENT


# ---------------------------------------------------------------------------
# block-level recomputation and withholding
# ---------------------------------------------------------------------------

_WITHHELD_STATUS_FOR = {
    TemporalStatus.HISTORICAL_REPLAY: "HISTORICAL_REPLAY_NOT_A_CURRENT_FORECAST",
    TemporalStatus.NOT_LEAD_SPECIFIC: "LEAD_TIME_NOT_SUPPORTED",
    TemporalStatus.EXPIRED: "FORECAST_EXPIRED",
    TemporalStatus.STALE: "FORECAST_STALE",
    TemporalStatus.INVALID_PROVENANCE: "INVALID_PROVENANCE",
    TemporalStatus.UNAVAILABLE: "UNAVAILABLE",
}
_SOURCE_STATUS_FOR = {
    TemporalStatus.HISTORICAL_REPLAY: "HISTORICAL_REPLAY", TemporalStatus.NOT_LEAD_SPECIFIC: "NOT_LEAD_SPECIFIC",
    TemporalStatus.EXPIRED: "EXPIRED", TemporalStatus.STALE: "STALE",
    TemporalStatus.INVALID_PROVENANCE: "INVALID_PROVENANCE", TemporalStatus.UNAVAILABLE: "UNAVAILABLE",
}


def _has_value(block: dict) -> bool:
    ex = block.get("extra") or {}
    xai = block.get("xai") or {}
    return (block.get("probability") is not None or ex.get("pu_ranking_score") is not None
            or xai.get("status") == "AVAILABLE")


def _hazard_model_init(record: dict, block: dict):
    """The init time a hazard's inputs really came from. CB carries its own; TS/FF use the record-level replay cycle."""
    return block.get("model_init_time_utc") or block.get("cb_init_time_utc") or record.get("init_time")


def recompute_block_status(record: dict, hz: str, *, generated_at, now=None) -> tuple[str, list[str]]:
    """Status from timestamps and provenance ONLY. Returns (status, violation_codes_about_the_published_block)."""
    block = record[hz]
    viol: list[str] = []
    init = _hazard_model_init(record, block)
    valid = block.get("valid_time_utc") or record.get("valid_time")
    if (block.get("synthetic_fixture") or record.get("synthetic_fixture")) and not ALLOW_SYNTHETIC_FIXTURES:
        return TemporalStatus.INVALID_PROVENANCE, ["SYNTHETIC_ARTIFACT_IN_PRODUCTION"]
    if parse_utc(init) is None:
        return (TemporalStatus.INVALID_PROVENANCE if _has_value(block) else TemporalStatus.UNAVAILABLE), ["TIME_UNPARSABLE"]
    if block.get("source_status") in ("FIXED_VALIDATION_FALLBACK", "HISTORICAL_REPLAY"):
        return TemporalStatus.HISTORICAL_REPLAY, viol
    status = classify_time(model_init=init, valid_end=valid, issued_at=generated_at, now=now)
    if status == TemporalStatus.HISTORICAL_REPLAY:
        return status, viol
    # lead-slot semantics: a slot is a genuine lead forecast ONLY with a lead-capable head and its own lead basis;
    # the lead_time_resolution label is informational and never sufficient.
    basis = block.get("lead_basis") or {}
    capable = bool(basis) and basis.get("model_head") in LEAD_CAPABLE_HEADS and bool(basis.get("input_fingerprint"))
    if block.get("lead_time_resolution") == LEAD_NOT_SPECIFIC or not capable:
        return TemporalStatus.NOT_LEAD_SPECIFIC, viol
    return status, viol


def _research_reference(block: dict, status: str, reason: str, init, valid) -> dict:
    ex = block.get("extra") or {}
    ref = {"designation": status, "operational_use": False,
           "probability": block.get("probability"), "risk_category": block.get("risk_category", block.get("risk")),
           "status": block.get("status"), "source_status": block.get("source_status"),
           "source_cycle": block.get("source_cycle"),
           "model_init_time_utc": iso_z(parse_utc(init)), "valid_time_utc": iso_z(parse_utc(valid)), "note": reason}
    if ex.get("pu_ranking_score") is not None:
        ref["pu_ranking_score"] = ex["pu_ranking_score"]
    xai = block.get("xai") or ex.get("xai")
    if xai and xai.get("status") != "WITHHELD":
        ref["xai"] = xai
    return ref


def _strip_operational(out: dict, reason: str) -> None:
    out["probability"] = None
    if "risk_category" in out or "risk" not in out:
        out["risk_category"] = "NOT_AVAILABLE"
    if "risk" in out:
        out["risk"] = "NOT_AVAILABLE"
    out["confidence"] = "NOT_AVAILABLE"
    withheld = {"status": "WITHHELD", "reason": reason}
    if "xai" in out:
        out["xai"] = withheld
    ex = out.get("extra")
    if isinstance(ex, dict):
        ex = dict(ex)
        if "pu_ranking_score" in ex:
            ex["pu_ranking_score"] = None
        if "xai" in ex:
            ex["xai"] = withheld
        out["extra"] = ex


def withhold_hazard_block(block: dict, *, temporal_status: str, reason: str, model_init, valid_time,
                          issued_at, preserve: bool = True, available_time=None) -> dict:
    """Return a copy that can no longer be read as a current forecast; originals go to research_reference."""
    out = copy.deepcopy(block)
    prior = block.get("research_reference") or block.get("historical_reference")
    if preserve and _has_value(block):
        out["research_reference"] = out["historical_reference"] = _research_reference(
            block, temporal_status, reason, model_init, valid_time)
    elif preserve and prior:
        out["research_reference"] = out["historical_reference"] = prior      # keep what an earlier pass preserved
    if _has_value(block):
        _strip_operational(out, reason)
    out["status"] = _WITHHELD_STATUS_FOR.get(temporal_status, "UNAVAILABLE")
    out["source_status"] = _SOURCE_STATUS_FOR.get(temporal_status, "UNAVAILABLE")
    out["temporal_status"] = temporal_status
    out["presentable_as_current_forecast"] = False
    out["unavailable_reason"] = reason
    out["model_init_time_utc"] = iso_z(parse_utc(model_init))
    out["valid_time_utc"] = iso_z(parse_utc(valid_time))
    out.update(lead_metadata(model_init, valid_time, issued_at, available_time))
    out["nwp_lead_hours"] = out["nwp_forecast_hour"]          # compatibility alias
    return out


def annotate_current_block(block: dict, *, model_init, valid_time, issued_at, temporal_status,
                           available_time=None) -> dict:
    out = dict(block)
    out["temporal_status"] = temporal_status
    out["presentable_as_current_forecast"] = temporal_status in PRESENTABLE_AS_CURRENT
    out["model_init_time_utc"] = iso_z(parse_utc(model_init))
    out["valid_time_utc"] = iso_z(parse_utc(valid_time))
    out.update(lead_metadata(model_init, valid_time, issued_at, available_time))
    out["nwp_lead_hours"] = out["nwp_forecast_hour"]
    return out


def _move_pu_to_research(block: dict) -> dict:
    """PU ranking scores are permanently research-only, even for a temporally current record (Q4)."""
    ex = block.get("extra") or {}
    if ex.get("pu_ranking_score") is None:
        return block
    out = copy.deepcopy(block)
    ref = dict(out.get("research_reference") or {})
    ref.update({"designation": "RESEARCH_ONLY_PU_RANKING", "operational_use": False,
                "pu_ranking_score": ex["pu_ranking_score"],
                "note": "PU ranking score: research ranking only; not a probability and never an alert threshold."})
    out["research_reference"] = ref
    out["extra"] = dict(ex, pu_ranking_score=None,
                        pu_ranking_score_note="research-only; see research_reference.pu_ranking_score")
    return out


def sanitize_record_hazard(record: dict, hz: str, *, issued_at, now=None, available_time=None) -> dict:
    """Recompute and (if required) withhold one hazard block. Never trusts an existing temporal_status."""
    block = record[hz]
    init = _hazard_model_init(record, block)
    valid = block.get("valid_time_utc") or record.get("valid_time")
    status, _ = recompute_block_status(record, hz, generated_at=issued_at, now=now)
    if status == TemporalStatus.CURRENT:
        return annotate_current_block(_move_pu_to_research(block), model_init=init, valid_time=valid,
                                      issued_at=issued_at, temporal_status=status, available_time=available_time)
    reasons = {
        TemporalStatus.HISTORICAL_REPLAY: (
            f"Inputs are not current: model init {iso_z(parse_utc(init))} is more than {int(HISTORICAL_AFTER_HOURS)} h "
            f"before this artifact was generated ({iso_z(parse_utc(issued_at))}), or the source is a fixed validation "
            f"fallback. Retained for research/replay only; not a forecast of the present."),
        TemporalStatus.NOT_LEAD_SPECIFIC: (
            "No genuine lead-specific prediction: the value is a whole-day aggregate (GFS f006..f027) or the slot has "
            "no lead-capable head / independent lead basis. It cannot be offered as an independent 2-6 h forecast. "
            "See daily_outlook for the daily value."),
        TemporalStatus.EXPIRED: "The valid time has passed; the value is no longer a forecast.",
        TemporalStatus.STALE: "The artifact was generated more than 12 h ago; the value is stale.",
        TemporalStatus.INVALID_PROVENANCE: "Time provenance is missing, unparsable or marked synthetic; the value cannot be trusted.",
        TemporalStatus.UNAVAILABLE: "No usable time provenance for this block.",
    }[status]
    return withhold_hazard_block(block, temporal_status=status, reason=reasons, model_init=init, valid_time=valid,
                                 issued_at=issued_at, preserve=True, available_time=available_time)


# ---------------------------------------------------------------------------
# daily outlook (ongoing-outlook semantics)
# ---------------------------------------------------------------------------

def _window_state(start, end, now) -> str:
    s, e = parse_utc(start), parse_utc(end)
    if s is None or e is None:
        return "UNKNOWN"
    if e <= now:
        return "ELAPSED"
    return "UPCOMING" if s > now else "IN_PROGRESS"


def _outlook_entry_from_block(cb: dict, init: datetime) -> dict:
    w_start, w_end = init + timedelta(hours=CB_DAILY_WINDOW_START_H), init + timedelta(hours=CB_DAILY_WINDOW_END_H)
    return {"CB": {
        "probability": cb["probability"], "risk_category": cb.get("risk_category"),
        "model_version": cb.get("model_version"), "source_status": cb.get("source_status"),
        "model_init_time_utc": iso_z(init), "window_start_utc": iso_z(w_start), "window_end_utc": iso_z(w_end),
        "resolution": "DAILY_AGGREGATE", "lead_specific": False, "lead_claim": "NONE",
        "event_class": "HEAVY_RAIN_DAY_PROXY",
        "event_definition": ("Model of IMD gridded daily rainfall >= 64.5 mm at 0.25 deg (heavy-rain day). "
                             "NOT a verified hourly cloudburst and NOT a prediction for the remaining hours."),
        "value_type": "MODEL_SCORE_UNCALIBRATED",
        "operational_lead_hours": None, "operational_lead_status": "NOT_APPLICABLE_DAILY_OUTLOOK",
    }}


def verify_daily_entry(entry: dict, *, generated_at, now) -> dict:
    """Recompute an outlook entry's status/window state at request time; withhold the value unless CURRENT."""
    out = copy.deepcopy(entry)
    cb = out["CB"]
    init, wend = cb.get("model_init_time_utc"), cb.get("window_end_utc")
    status = classify_time(model_init=init, valid_end=wend, issued_at=generated_at, now=now)
    wstate = _window_state(cb.get("window_start_utc"), wend, now)
    cb["window_state"] = wstate
    cb["display_class"] = "ONGOING_DAILY_OUTLOOK" if wstate == "IN_PROGRESS" else "DAILY_OUTLOOK"
    cb["temporal_status"] = status
    cb["presentable_as_current_forecast"] = status == TemporalStatus.CURRENT
    cb["lead_specific"], cb["lead_claim"] = False, "NONE"
    cb["operational_lead_hours"], cb["operational_lead_status"] = None, "NOT_APPLICABLE_DAILY_OUTLOOK"
    if status != TemporalStatus.CURRENT and cb.get("probability") is not None:
        cb["research_reference"] = {"designation": status, "operational_use": False, "probability": cb["probability"],
                                    "risk_category": cb.get("risk_category"), "model_init_time_utc": init,
                                    "window_start_utc": cb.get("window_start_utc"), "window_end_utc": wend}
        cb["probability"], cb["risk_category"] = None, "NOT_AVAILABLE"
        cb["source_status"] = _SOURCE_STATUS_FOR.get(status, "UNAVAILABLE")
    return out


def build_daily_outlook(artifact: dict, *, issued_at, now=None) -> dict:
    """Per-cell daily CB outlook from the duplicated daily aggregate, or the already-built outlook (re-verified)."""
    now_dt = parse_utc(now) or datetime.now(timezone.utc)
    if artifact.get("daily_outlook"):
        return {cid: verify_daily_entry(e, generated_at=issued_at, now=now_dt)
                for cid, e in artifact["daily_outlook"].items()}
    out = {}
    for rec in artifact.get("records", []):
        cb = rec.get("CB") or {}
        cid = rec["cell_id"]
        if cid in out or cb.get("probability") is None or cb.get("lead_time_resolution") != LEAD_NOT_SPECIFIC:
            continue
        init = parse_utc(cb.get("cb_init_time_utc"))
        if init is None or cb.get("source_status") == "FIXED_VALIDATION_FALLBACK":
            continue
        out[cid] = verify_daily_entry(_outlook_entry_from_block(cb, init), generated_at=issued_at, now=now_dt)
    return out


# ---------------------------------------------------------------------------
# artifact-level sanitisation (always recomputes; no applied-flag short circuit)
# ---------------------------------------------------------------------------

def sanitize_artifact(artifact: dict, *, now=None) -> dict:
    """Return a verified deep copy. Safe to call repeatedly (idempotent by RECOMPUTATION, not by a flag)."""
    now_dt = parse_utc(now) or datetime.now(timezone.utc)
    art = copy.deepcopy(artifact)
    generated = parse_utc(art.get("generated_at_utc"))
    prov = art.get("forecast_available_provenance")
    available = art.get("forecast_available_time_utc") if (
        prov == OBSERVED_AVAILABILITY or (ALLOW_SYNTHETIC_FIXTURES and prov == "SYNTHETIC_TEST")) else None
    art["daily_outlook"] = build_daily_outlook(art, issued_at=art.get("generated_at_utc"), now=now_dt)
    counts = {"current_lead_specific": 0, "withheld_historical": 0, "withheld_not_lead_specific": 0,
              "withheld_other": 0}
    for rec in art.get("records", []):
        rec["record_class"] = "LEAD_SLOT"
        for hz in HAZARDS:
            if hz not in rec:
                continue
            rec[hz] = sanitize_record_hazard(rec, hz, issued_at=art.get("generated_at_utc"), now=now_dt,
                                             available_time=available)
            b = rec[hz]
            b["generated_at_utc"] = art.get("generated_at_utc")
            b["forecast_available_time_utc"] = available
            b["forecast_available_provenance"] = prov if available else None
            ts = b.get("temporal_status")
            if ts == TemporalStatus.CURRENT and b.get("probability") is not None:
                counts["current_lead_specific"] += 1
            elif ts == TemporalStatus.HISTORICAL_REPLAY and "research_reference" in b:
                counts["withheld_historical"] += 1
            elif ts == TemporalStatus.NOT_LEAD_SPECIFIC:
                counts["withheld_not_lead_specific"] += 1
            elif b.get("probability") is None:
                counts["withheld_other"] += 1

    cb_info = art.get("cb_live_fetch") or {}
    cb_live = cb_info.get("status") in ("LIVE_AWS_GFS", "LIVE_NOMADS_GFS", "MIXED_LIVE_SOURCES")
    cur_daily = sum(1 for e in art["daily_outlook"].values() if e["CB"].get("temporal_status") == "CURRENT")
    lead_ok = counts["current_lead_specific"] > 0
    art["schema_version"] = SCHEMA_VERSION
    art["artifact_mode"] = ("OPERATIONAL_LEAD_SPECIFIC" if lead_ok else
                            "OPERATIONAL_DAILY_OUTLOOK_ONLY" if (cb_live and cur_daily) else "HISTORICAL_REPLAY_ONLY")
    art["time_semantics"] = {
        "contract_version": SCHEMA_VERSION, "definitions": TIME_DEFINITIONS,
        "artifact_generated_at_utc": iso_z(generated),
        "model_init_times_utc": {"CB_daily_outlook": iso_z(parse_utc(cb_info.get("init_time_utc"))) if cb_live else None,
                                 "TS_replay": iso_z(parse_utc(art.get("init_time_utc"))),
                                 "FF_replay": iso_z(parse_utc(art.get("init_time_utc")))},
        "data_acquired_at_utc": art.get("data_acquired_at_utc"),
        "data_acquired_status": "SUPPLIED" if art.get("data_acquired_at_utc") else "NOT_SUPPLIED_BY_PIPELINE",
        "forecast_available_time_utc": available,
        "forecast_available_status": "OBSERVED" if available else "NOT_SUPPLIED_BY_PIPELINE",
        "legacy_field_note": ("Top-level init_time_utc / source_cycle_date and record-level init_time / valid_time / "
                              "lead_hours are LEGACY mirror fields of the fixed 2024-08-01 replay cycle. They are not "
                              "the model init of current data and `lead_hours` carries no operational-lead meaning."),
    }
    art["forecast_horizon_support"] = {
        "lead_specific_forecasts_available": lead_ok,
        "supported_operational_horizons_hours": [],
        "lead_slots_2_to_6h": ("AVAILABLE for the slots marked CURRENT only." if lead_ok else
                               "UNAVAILABLE: no genuine lead-specific forecast exists."),
        "operational_lead": "UNAVAILABLE: no recorded forecast availability time" if not available else "RECORDED",
        "daily_outlook_available_cells": cur_daily,
    }
    art["integrity"] = {"recomputed": True, "recomputed_at_utc": iso_z(now_dt), "counts": counts,
                        "module": "backend/forecast_integrity.py"}
    cov = art.get("coverage_summary") or {}
    recs = art.get("records", [])
    cov["n_cb_records_with_probability"] = sum(1 for r in recs if (r.get("CB") or {}).get("probability") is not None)
    cov["n_ts_records_with_probability"] = sum(1 for r in recs if (r.get("TS") or {}).get("probability") is not None)
    cov["n_ff_records_with_pu_ranking_score"] = sum(
        1 for r in recs if ((r.get("FF") or {}).get("extra") or {}).get("pu_ranking_score") is not None)
    cov["n_cb_daily_outlook_cells"] = cur_daily
    cov["integrity_note"] = ("Counts are of values presentable as current. Withheld values are preserved under "
                             "research_reference / daily_outlook; PU scores are permanently research-only.")
    art["coverage_summary"] = cov
    return art


def refresh_request_time_status(artifact: dict, *, now=None) -> dict:
    """Request-time re-verification: equivalent to sanitize_artifact (everything is recomputed, nothing trusted)."""
    return sanitize_artifact(artifact, now=now)


def eligible_for_alert(hazard_block: dict, *, hazard: str, valid_time, now=None, policy=None) -> tuple[bool, list]:
    """Delegates to the single shared gate (backend/alert_policy.py). Production registries are empty, so this is
    always ineligible until a hazard is explicitly approved."""
    from backend import alert_policy
    cand = dict(hazard_block)
    cand["valid_time_utc"] = hazard_block.get("valid_time_utc") or valid_time
    cand["lead_specific"] = hazard_block.get("temporal_status") == TemporalStatus.CURRENT and (
        (hazard_block.get("lead_basis") or {}).get("model_head") in LEAD_CAPABLE_HEADS)
    ok, why = alert_policy.evaluate(cand, hazard=hazard, now=parse_utc(now), policy=policy)
    return ok, why


# ---------------------------------------------------------------------------
# independent CI reports (re-derive; never trust the artifact's own `validation` block)
# ---------------------------------------------------------------------------

def verify_artifact_report(artifact: dict, *, now=None, max_violations: int = 2000) -> dict:
    """Violations a PUBLISHED unified artifact commits, found by recomputation. Empty list == valid."""
    now_dt = parse_utc(now) or datetime.now(timezone.utc)
    v: list[dict] = []

    def add(code, path, detail=""):
        if len(v) < max_violations:
            v.append({"code": code, "path": path, "detail": detail})
        counts[code] = counts.get(code, 0) + 1

    counts: dict = {}
    if int(artifact.get("schema_version", 1)) < SCHEMA_VERSION or "time_semantics" not in artifact \
            or "forecast_horizon_support" not in artifact:
        add("SCHEMA_UNSUPPORTED", "artifact", f"schema_version={artifact.get('schema_version')}; need >= "
            f"{SCHEMA_VERSION} with time_semantics and forecast_horizon_support")
    generated = artifact.get("generated_at_utc")
    if parse_utc(generated) is None:
        add("TIME_UNPARSABLE", "generated_at_utc", repr(generated))
    elif (parse_utc(generated) - now_dt).total_seconds() > 300:
        add("GENERATED_IN_FUTURE", "generated_at_utc", repr(generated))
    if artifact.get("forecast_available_time_utc") and artifact.get("forecast_available_provenance") != OBSERVED_AVAILABILITY \
            and not (ALLOW_SYNTHETIC_FIXTURES and artifact.get("forecast_available_provenance") == "SYNTHETIC_TEST"):
        add("AVAILABILITY_PROVENANCE_NOT_OBSERVED", "forecast_available_time_utc", "availability must be observed")
    n_current = 0
    for r in artifact.get("records", []):
        for hz in HAZARDS:
            b = r.get(hz)
            if not isinstance(b, dict):
                continue
            path = f"{r.get('cell_id')}/lead{r.get('lead_hours')}/{hz}"
            status, why = recompute_block_status(r, hz, generated_at=generated, now=now_dt)
            for code in why:
                add(code, path)
            ex = b.get("extra") or {}
            if ex.get("pu_ranking_score") is not None:
                add("PU_SCORE_IN_OPERATIONAL_FIELD", path, "PU ranking scores are research-only")
            has_value = b.get("probability") is not None
            xai_on = (b.get("xai") or {}).get("status") == "AVAILABLE"
            if status != TemporalStatus.CURRENT and (has_value or xai_on):
                code = {"NOT_LEAD_SPECIFIC": "DAILY_AGGREGATE_AS_LEAD",
                        "HISTORICAL_REPLAY": "INIT_STALE_FOR_CURRENT"}.get(status, "WITHHELD_VALUE_PRESENT")
                add(code, path, f"recomputed status {status} but a value/explanation is published")
            claimed = b.get("temporal_status")
            if claimed is not None and claimed != status:
                add("LABEL_MISMATCH", path, f"claimed {claimed}, recomputed {status}")
            if claimed is None and has_value:
                add("LABEL_MISSING_ON_VALUE", path)
            if isinstance(b.get("operational_lead_hours"), (int, float)) and (
                    b["operational_lead_hours"] <= 0 or not artifact.get("forecast_available_time_utc")):
                add("OPERATIONAL_LEAD_NOT_SUPPORTED", path, "lead published without a recorded availability time")
            if status == TemporalStatus.CURRENT and has_value:
                n_current += 1
    for cid, e in (artifact.get("daily_outlook") or {}).items():
        cb = e.get("CB") or {}
        if cb.get("probability") is not None:
            st = classify_time(model_init=cb.get("model_init_time_utc"), valid_end=cb.get("window_end_utc"),
                               issued_at=generated, now=now_dt)
            if st != TemporalStatus.CURRENT:
                add("WITHHELD_VALUE_PRESENT", f"{cid}/daily_outlook", f"recomputed {st}")
        if cb.get("lead_specific") is not False or isinstance(cb.get("operational_lead_hours"), (int, float)):
            add("DAILY_OUTLOOK_LEAD_CLAIM", f"{cid}/daily_outlook")
    fhs = artifact.get("forecast_horizon_support") or {}
    if fhs and bool(fhs.get("lead_specific_forecasts_available")) != (n_current > 0):
        add("ARTIFACT_MODE_INCONSISTENT", "forecast_horizon_support", "claim disagrees with content")
    return {"ok": not counts, "violations": v, "counts_by_code": counts, "n_violations": sum(counts.values())}


def verify_grid_report(doc: dict, *, now=None) -> dict:
    """Independent check of data/pan_india_grid.json against ITS OWN contract (not the unified schema)."""
    v: list[dict] = []

    def add(code, detail=""):
        v.append({"code": code, "detail": detail})

    if int(doc.get("grid_contract_version", 0)) < GRID_CONTRACT_VERSION:
        add("GRID_SCHEMA_UNSUPPORTED", "grid_contract_version missing (legacy grid): regenerate with backend/pipeline.py")
    gen, init = parse_utc(doc.get("generated_at_utc")), parse_utc(doc.get("gfs_cycle_init_utc"))
    if gen is None or init is None:
        add("GRID_TIME_UNPARSABLE", "generated_at_utc / gfs_cycle_init_utc")
    elif init > gen:
        add("GRID_TIME_ORDER", "model init after generation")
    elif (gen - init).total_seconds() / 3600 > 24:
        add("GRID_CYCLE_STALE", f"cycle age {(gen - init).total_seconds() / 3600:.1f} h at generation")
    fcs = doc.get("forecasts") or []
    if not fcs:
        add("GRID_STRUCTURE", "no forecasts[]")
    prim = [f for f in fcs if f.get("is_primary")]
    if len(prim) != 1:
        add("GRID_STRUCTURE", f"expected exactly one primary forecast, found {len(prim)}")
    for f in fcs:
        cells = f.get("grid_cells") or []
        n_unique = len({c.get("cell_id") for c in cells})
        if n_unique != 992:
            add("GRID_STRUCTURE", f"lead {f.get('forecast_lead_hours')}: expected 992 unique cells, got {n_unique}")
        lh = f.get("forecast_lead_hours")
        for c in cells[:1] + cells[-1:]:
            if init is not None and lh is not None and parse_utc(c.get("forecast_valid_at_utc")) != init + timedelta(hours=lh):
                add("GRID_TIME_ORDER", f"valid time != init + {lh} h")
        for c in cells:
            if c.get("is_calibrated_probability") is True:
                add("GRID_VALUE_TYPE", "heuristic score claimed as calibrated probability")
                break
            if isinstance(c.get("operational_lead_hours"), (int, float)):
                add("GRID_LEAD_SEMANTICS", "numeric operational lead without recorded availability time")
                break
            if c.get("flash_flood_research_pu_score") is not None and not c.get("flash_flood_research_designation"):
                add("GRID_PU_RESEARCH_ONLY", "PU score without research designation")
                break
    if prim and doc.get("grid_cells") is not None:
        a = {c.get("cell_id"): c.get("cloudburst_probability") for c in (doc.get("grid_cells") or [])}
        b = {c.get("cell_id"): c.get("cloudburst_probability") for c in (prim[0].get("grid_cells") or [])}
        if a != b:
            add("GRID_MIRROR_DRIFT", "top-level grid_cells differ from the primary forecast's cells")
        recomputed = sorted(k for k, x in predictor_availability(prim[0].get("grid_cells") or []).items()
                            if x["status"] == "UNAVAILABLE_ALL_CELLS")
        if sorted(doc.get("unavailable_predictors") or []) != recomputed:
            add("GRID_PREDICTOR_MISMATCH", f"claims {doc.get('unavailable_predictors')}, recomputed {recomputed}")
    by_code: dict = {}
    for x in v:
        by_code[x["code"]] = by_code.get(x["code"], 0) + 1
    return {"ok": not v, "violations": v, "counts_by_code": by_code, "n_violations": len(v)}


VOBL_OPERATIONAL_SOURCES = ("gfs", "gfs+upperair")


def verify_vobl_forecast(fc: dict) -> dict:
    """forecast.json (VOBL station) contract: fallback / defaulted / climatology slots are never operational and
    can never drive alert_active or peak_probability."""
    fc = fc if isinstance(fc, dict) else {}
    slots = fc.get("slots") if isinstance(fc.get("slots"), list) else []
    elig, withheld = [], []
    for s in slots:
        ok = (s.get("source") in VOBL_OPERATIONAL_SOURCES) and not s.get("fallback") and s.get("ts_probability") is not None
        (elig if ok else withheld).append(s.get("slot"))
    elig_probs = [s["ts_probability"] for s in slots if s.get("slot") in elig]
    elig_alert = any(s.get("ts_predicted") for s in slots if s.get("slot") in elig)
    v = []
    if not isinstance(fc, dict) or not isinstance(fc.get("slots"), list) or not fc["slots"]:
        v.append({"code": "VOBL_STRUCTURE_INVALID", "detail": "forecast.json has no slots list"})
    elif not isinstance(fc.get("generated_at"), str) or not fc["generated_at"].strip():
        v.append({"code": "VOBL_STRUCTURE_INVALID", "detail": "forecast.json has no generated_at"})
    if fc.get("alert_active") and not elig_alert:
        v.append({"code": "VOBL_ALERT_FROM_INELIGIBLE_SLOT",
                  "detail": "alert_active is true but no eligible slot predicts"})
    if fc.get("peak_probability") is not None and float(fc["peak_probability"]) > (max(elig_probs) if elig_probs else 0.0) + 1e-6:
        v.append({"code": "VOBL_PEAK_FROM_INELIGIBLE_SLOT", "detail": "peak_probability exceeds every eligible slot"})
    return {"ok": not v, "violations": v, "eligible_slots": elig, "withheld_slots": withheld,
            "operational_lead_hours": None, "operational_lead_status": "UNAVAILABLE_NO_AVAILABILITY_TIME"}


# ---------------------------------------------------------------------------
# pan-India grid: additive, honest annotation (grid contract v1)
# ---------------------------------------------------------------------------

GRID_PREDICTORS = ("cape", "cin", "k_index", "totals_totals", "wind_shear_ms", "pwat_mm",
                   "ctt_c", "convergence_s", "ctt_drop_rate_c_hr", "qpe_mm")

GRID_HAZARD_DEFINITIONS = {
    "thunderstorm": ("Fixed-weight heuristic over GFS instability indices. A risk score, NOT a calibrated "
                     "probability, even though the legacy field is named thunderstorm_probability."),
    "cloudburst": ("Field named cloudburst_probability. Where the source is panindia_cb_v1 it is an "
                   "uncalibrated-for-this-use model score for a heavy-rain DAY (IMD gridded rainfall >= 64.5 "
                   "mm/24 h at 0.25 deg), not a verified hourly cloudburst. Heavy rainfall, extreme "
                   "short-duration rainfall and verified cloudbursts are distinct events; none is verified here."),
    "flash_flood": ("Heuristic derived from the cloudburst/thunderstorm scores. A risk score, NOT a flood "
                    "probability and not validated against flood observations."),
    "official_warning": "None of these values is an IMD/NDMA warning. DRIFT outputs are model advisories only.",
}


def predictor_availability(cells: list) -> dict:
    """Per-predictor null counts. Nulls are reported, never filled."""
    n = len(cells)
    out = {}
    for k in GRID_PREDICTORS:
        n_null = sum(1 for c in cells if c.get(k) is None)
        out[k] = {"n_null": n_null, "n_cells": n,
                  "status": ("UNAVAILABLE_ALL_CELLS" if n and n_null == n else "AVAILABLE" if n_null == 0 else "PARTIAL")}
    return out


def annotate_grid(doc: dict) -> dict:
    """Add the grid contract (v1) IN PLACE, additively: NWP hour, null operational lead + status, predictor
    availability, honest hazard value types. No existing field is changed or removed."""
    issued = doc.get("generated_at_utc")
    leads = doc.get("forecasts") or []
    mirror = doc.get("grid_cells") or []

    def _annotate_cells(cells):
        for c in cells:
            c["nwp_forecast_hour"] = c.get("forecast_lead_hours")
            c["nwp_lead_hours"] = c.get("forecast_lead_hours")
            c["operational_lead_hours"] = None
            c["operational_lead_status"] = "UNAVAILABLE_NO_AVAILABILITY_TIME"
            if c.get("flash_flood_research_pu_score") is not None:
                c["flash_flood_research_designation"] = "RESEARCH_ONLY_PU_RANKING_NOT_A_PROBABILITY"

    for f in leads:
        cells = f.get("grid_cells") or []
        _annotate_cells(cells)
        f["predictor_availability"] = predictor_availability(cells)
    _annotate_cells(mirror)
    primary = next((f for f in leads if f.get("is_primary")), None)
    basis = (primary or {}).get("grid_cells") or mirror
    avail = predictor_availability(basis)
    doc["grid_contract_version"] = GRID_CONTRACT_VERSION
    doc["time_semantics"] = {
        "contract_version": GRID_CONTRACT_VERSION, "definitions": TIME_DEFINITIONS,
        "model_init_time_utc": iso_z(parse_utc(doc.get("gfs_cycle_init_utc"))),
        "artifact_generated_at_utc": iso_z(parse_utc(issued)),
        "data_acquired_at_utc": None, "forecast_available_time_utc": None,
        "note": ("forecast_lead_hours / nwp_forecast_hour are the NWP forecast hour. time_until_valid_hours (legacy) is "
                 "valid minus GENERATION and is not an operational lead. operational_lead_hours is null: no forecast "
                 "availability time is recorded by the pipeline."),
    }
    doc["lead_semantics"] = {"target_window_hours": list(TARGET_LEAD_WINDOW_HOURS),
                             "operational_lead_hours": None,
                             "operational_lead_status": "UNAVAILABLE_NO_AVAILABILITY_TIME",
                             "primary_meets_2_to_6h_target": None}
    doc["predictor_availability"] = avail
    doc["unavailable_predictors"] = sorted(k for k, v in avail.items() if v["status"] == "UNAVAILABLE_ALL_CELLS")
    doc["predictor_availability_note"] = ("Predictors in unavailable_predictors are null in every cell: null means "
                                          "'not computed', never zero. Hazard scores were computed WITHOUT them.")
    cb_model = sum(1 for c in basis if str(c.get("cloudburst_probability_source", "")).startswith("panindia_cb_v1"))
    notcal = sum(1 for c in basis if c.get("is_calibrated_probability") is not True)
    doc["hazard_value_types"] = {
        "thunderstorm": "HEURISTIC_RISK_SCORE" if notcal == len(basis) else "MIXED",
        "cloudburst": {"MODEL_SCORE_UNCALIBRATED": cb_model, "HEURISTIC_RISK_SCORE": len(basis) - cb_model},
        "flash_flood": "HEURISTIC_RISK_SCORE" if notcal == len(basis) else "MIXED",
        "calibrated_probability_cells": sum(1 for c in basis if c.get("is_calibrated_probability") is True),
    }
    doc["hazard_definitions"] = GRID_HAZARD_DEFINITIONS
    return doc


def compact_for_publish(artifact: dict) -> dict:
    """Drop the in-memory `historical_reference` alias from serialised blocks (it duplicates `research_reference`).
    Keeps the published file under the 25 MiB per-file hosting limit. sanitize_artifact re-adds the alias on load."""
    for r in artifact.get("records") or []:
        for hz in ("TS", "CB", "FF"):
            b = r.get(hz)
            if isinstance(b, dict) and "research_reference" in b:
                b.pop("historical_reference", None)
    return artifact
