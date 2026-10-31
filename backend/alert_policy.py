"""
backend/alert_policy.py
=======================
The ONE gate every automated, forecast-driven alert path must pass (stdlib only, so GitHub Actions scripts
can import it without the ML stack).

Decisions encoded here (owner-approved):
  * Q14  An alert is eligible only if ALL hold: a genuine, observed `forecast_available_time_utc`; a calculable,
         strictly positive operational lead; a lead inside an EXPLICITLY registered horizon; valid hazard-specific
         provenance and prediction semantics; and the existing conditions (current, in the future, not FF,
         not a daily outlook, not synthetic). A missing availability time means INELIGIBLE; it is never inferred.
  * Q18  The production registries below start EMPTY. A hazard becomes alertable only when its target
         definition, temporal provenance, horizon, validation evidence and operational availability are
         explicitly approved and added here. A model merely existing never enables a hazard.
  * Q4   PU ranking scores and heuristic scores can never trigger an alert.

Every refusal carries machine-readable reason codes, so a withheld alert is always explained, never silent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

OBSERVED_PROVENANCE = "OBSERVED_PUBLICATION_CONFIRMATION"

# Value types that may never be alerted on, regardless of registries.
NEVER_ALERTABLE_VALUE_TYPES = frozenset({
    "HEURISTIC_RISK_SCORE", "heuristic_risk_score", "MODEL_SCORE_UNCALIBRATED", "RESEARCH_ONLY_PU_RANKING",
    "risk_score", "NWP_FIELD", "OBSERVATION",
})
NEVER_ALERTABLE_EVENT_CLASSES = frozenset({"HEAVY_RAIN_DAY_PROXY"})
NEVER_ALERTABLE_HAZARDS = frozenset({"FF"})  # until a validated, observed-flood-based target exists


@dataclass(frozen=True)
class AlertPolicy:
    """Registries are PRODUCTION-EMPTY by decision (Q18). Tests build their own policy to exercise the contract."""
    # hazard -> (min_lead_hours, max_lead_hours): the explicitly approved operational horizon
    supported_alert_horizons: dict = field(default_factory=dict)
    # hazard -> frozenset of (event_class, value_type) pairs approved as alertable, each with validation evidence
    alertable_semantics: dict = field(default_factory=dict)
    allow_synthetic: bool = False
    clock_skew_s: int = 120


PRODUCTION_POLICY = AlertPolicy()
# Modules read the active policy from here; tests may monkeypatch it with a synthetic policy.
ACTIVE_POLICY = PRODUCTION_POLICY


def _parse(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def evaluate(candidate: dict, *, hazard: str, now: Optional[datetime] = None,
             policy: Optional[AlertPolicy] = None) -> tuple[bool, list[str]]:
    """candidate keys (all optional; absence means ineligible):
         temporal_status, probability, value_type, event_class, valid_time_utc, generated_at_utc,
         forecast_available_time_utc, forecast_available_provenance, operational_lead_hours,
         operational_lead_status, lead_specific (bool), is_daily_outlook (bool), synthetic_fixture (bool),
         source, fallback (bool)
       returns (eligible, reason_codes)."""
    pol = policy or ACTIVE_POLICY
    now = now or datetime.now(timezone.utc)
    why: list[str] = []
    h = (hazard or "").upper()
    if h in NEVER_ALERTABLE_HAZARDS:
        why.append("HAZARD_NEVER_ALERTABLE")
    if candidate.get("is_daily_outlook"):
        why.append("DAILY_OUTLOOK_NOT_ALERTABLE")
    if candidate.get("synthetic_fixture") and not pol.allow_synthetic:
        why.append("SYNTHETIC_ARTIFACT_IN_PRODUCTION")
    if candidate.get("fallback") or str(candidate.get("source", "")).startswith(("climatology", "model_defaults")):
        why.append("INPUT_UNAVAILABLE_OR_FALLBACK")
    if candidate.get("probability") is None:
        why.append("NO_PROBABILITY")
    if candidate.get("temporal_status") != "CURRENT":
        why.append("NOT_CURRENT")
    if candidate.get("lead_specific") is not True:
        why.append("NOT_LEAD_SPECIFIC")

    # 1. recorded, genuine availability time (never inferred)
    avail = _parse(candidate.get("forecast_available_time_utc"))
    gen = _parse(candidate.get("generated_at_utc"))
    if avail is None:
        why.append("NO_AVAILABILITY_TIME")
    else:
        if candidate.get("forecast_available_provenance") != OBSERVED_PROVENANCE and not (
                pol.allow_synthetic and candidate.get("forecast_available_provenance") == "SYNTHETIC_TEST"):
            why.append("AVAILABILITY_PROVENANCE_NOT_OBSERVED")
        if gen is not None and avail < gen:
            why.append("AVAILABLE_BEFORE_GENERATED")
        if (avail - now).total_seconds() > pol.clock_skew_s:
            why.append("AVAILABLE_IN_FUTURE")

    # 2. calculable, strictly positive operational lead
    lead = candidate.get("operational_lead_hours")
    if not isinstance(lead, (int, float)) or isinstance(lead, bool) or lead <= 0 \
            or candidate.get("operational_lead_status") != "OK":
        why.append("OPERATIONAL_LEAD_NOT_POSITIVE")

    # 3. explicitly supported horizon
    hz = pol.supported_alert_horizons.get(h)
    if hz is None:
        why.append("HORIZON_NOT_REGISTERED")
    elif isinstance(lead, (int, float)) and not isinstance(lead, bool) and not (hz[0] <= lead <= hz[1]):
        why.append("LEAD_OUTSIDE_SUPPORTED_HORIZON")

    # 4. hazard-specific provenance and prediction semantics
    sem = pol.alertable_semantics.get(h)
    vt, ec = candidate.get("value_type"), candidate.get("event_class")
    if vt in NEVER_ALERTABLE_VALUE_TYPES:
        why.append("VALUE_TYPE_NOT_ALERTABLE")
    if ec in NEVER_ALERTABLE_EVENT_CLASSES:
        why.append("EVENT_CLASS_NOT_ALERTABLE")
    if sem is None or (ec, vt) not in sem:
        why.append("SEMANTICS_NOT_REGISTERED")

    # existing condition: valid window in the future
    valid = _parse(candidate.get("valid_time_utc"))
    if valid is None or valid <= now:
        why.append("VALID_TIME_NOT_IN_FUTURE")

    why = sorted(set(why))
    return (not why, why)


def explain(reasons: list[str]) -> str:
    """Human-readable one-liner used by logs, job summaries and UI text."""
    text = {
        "NO_AVAILABILITY_TIME": "no recorded forecast availability time (never inferred)",
        "HORIZON_NOT_REGISTERED": "no approved operational horizon registered for this hazard",
        "SEMANTICS_NOT_REGISTERED": "no approved alertable prediction semantics for this hazard",
        "VALUE_TYPE_NOT_ALERTABLE": "value is a heuristic/uncalibrated/research score, not a calibrated probability",
        "HAZARD_NEVER_ALERTABLE": "hazard has no validated observed-event target",
        "INPUT_UNAVAILABLE_OR_FALLBACK": "inputs were unavailable or a climatology/default fallback was used",
        "OPERATIONAL_LEAD_NOT_POSITIVE": "operational lead is not calculable and strictly positive",
        "NOT_CURRENT": "forecast is not current",
        "NOT_LEAD_SPECIFIC": "no genuine lead-specific prediction",
        "DAILY_OUTLOOK_NOT_ALERTABLE": "daily outlooks never trigger alerts",
        "EVENT_CLASS_NOT_ALERTABLE": "event class (e.g. heavy-rain-day proxy) is not an alertable target",
    }
    return "; ".join(text.get(r, r) for r in reasons)


# ---------------------------------------------------------------------------
# helpers shared by the automated alert paths (grid SMS, WhatsApp, ntfy push, browser)
# ---------------------------------------------------------------------------
from collections import Counter  # noqa: E402
from datetime import timedelta  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))


def parse_ist_string(s) -> Optional[datetime]:
    """forecast.json `generated_at` ('2026-10-31 03:15 IST') -> aware UTC datetime, or None."""
    if not isinstance(s, str):
        return None
    try:
        return datetime.strptime(s.replace(" IST", "").strip(), "%Y-%m-%d %H:%M").replace(tzinfo=IST).astimezone(timezone.utc)
    except ValueError:
        return None


def vobl_slot_candidates(forecast: dict, now: Optional[datetime] = None) -> list[dict]:
    """One candidate per forecast.json slot, with the provenance the policy needs. Climatology / defaulted-input /
    fallback slots carry `fallback`/`source` so they can never be alertable (H1)."""
    now = now or datetime.now(timezone.utc)
    gen = parse_ist_string(forecast.get("generated_at"))
    fresh = gen is not None and (now - gen).total_seconds() <= 12 * 3600
    out = []
    for s in forecast.get("slots") or []:
        lt = s.get("lead_time") or {}
        out.append({
            "slot": s.get("slot"), "probability": s.get("ts_probability"),
            "temporal_status": "CURRENT" if fresh else "STALE",
            "source": s.get("source"), "fallback": bool(s.get("fallback")),
            "value_type": "MODEL_SCORE_UNCALIBRATED",     # slot XGBoost output: no approved calibration evidence on file
            "event_class": "STATION_SLOT_WINDOW_TS", "lead_specific": False,
            "valid_time_utc": lt.get("valid_from"), "generated_at_utc": gen.isoformat() if gen else None,
            "forecast_available_time_utc": None, "operational_lead_hours": None,
            "operational_lead_status": "UNAVAILABLE_NO_AVAILABILITY_TIME",
        })
    return out


def evaluate_forecast_alerts(forecast: dict, now: Optional[datetime] = None, policy=None) -> dict:
    """Evaluate every slot of forecast.json. Returns eligible slots and a reason histogram."""
    cands = vobl_slot_candidates(forecast, now)
    results = [(c["slot"],) + evaluate(c, hazard="TS", now=now, policy=policy) for c in cands]
    eligible = [slot for slot, ok, _ in results if ok]
    reasons = Counter(r for _, ok, why in results if not ok for r in why)
    return {"eligible_slots": eligible, "n_slots": len(results), "reason_counts": dict(reasons),
            "alert_active_in_file": bool(forecast.get("alert_active"))}
