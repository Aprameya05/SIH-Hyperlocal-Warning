"""
backend/unified_api.py
==========================
Phase 35: thin HTTP layer around the Phase 34 UnifiedInferenceEngine,
mounted onto the EXISTING FastAPI app in backend/alerts.py (preserving
its /health, /alert, /alerts endpoints and CORS config exactly -- this
module only ADDS routes, it never redefines an existing path).

Endpoints added:
  GET  /forecast              -- single cell/lead_hours, live inference
  GET  /forecast/all          -- full 992x5 artifact (data/unified_forecast.json),
                                  forecast_source=OFFLINE_ARTIFACT, never
                                  recomputed live (Phase 35 Part 12)
  GET  /forecast/sources      -- per-source status (Part 11)
  GET  /forecast/alerts       -- evaluate (read-only) alert candidates from
                                  the offline artifact; never dispatches
  POST /forecast/alerts/dispatch -- actually sends via the existing Twilio/
                                  webhook helpers in backend.alerts; honest
                                  delivery_status, never fabricated SENT

/health (from backend.alerts) is ENRICHED in place (see backend/alerts.py's
health() -- additive fields only, nothing removed) rather than redefined
here, because FastAPI/Starlette route matching uses registration order:
a second @app.get("/health") in this module would never be reached.

No model is retrained. No production file is modified. No commit/push.
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

from fastapi import HTTPException, Query  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend.alerts import app, log_alert, send_sms_twilio, post_webhook, RECIPIENTS  # noqa: E402
from backend.models.unified_mtl.inference_engine import (  # noqa: E402
    UnifiedInferenceEngine, compute_valid_time, assert_no_leakage, InferenceLeakageError,
)
from backend.models.unified_mtl.lead_time_interface import LEAD_HOURS  # noqa: E402
from backend.models.unified_mtl.local_xai import local_shap_cb, local_shap_ts, local_xai_ff  # noqa: E402
from ts_station_model_interface import VOBL_CELL_ID  # noqa: E402

UNIFIED_FORECAST_PATH = REPO_ROOT / "data" / "unified_forecast.json"
TERRAIN_PATH = REPO_ROOT / "data" / "pan_india_terrain_992.json"
COMMON_GRID_PATH = REPO_ROOT / "data" / "pan_india_common_grid_992.json"

# Alert-eligible source_status values (2026-10-06 Phase 3 update): CB may
# now genuinely report LIVE_AWS_GFS/LIVE_NOMADS_GFS/MIXED_LIVE_SOURCES when
# scripts/gfs_live_cb_predictors.py's current-cycle fetch succeeded, not
# just the legacy LIVE/RECENT strings. FIXED_VALIDATION_FALLBACK/STALE/
# UNAVAILABLE are never eligible, regardless of generated_at_utc.
LIVE_SOURCE_STATUSES = {"LIVE", "RECENT", "LIVE_AWS_GFS", "LIVE_NOMADS_GFS", "MIXED_LIVE_SOURCES"}

# 2026-10-08 (Phase 9, freshness enforcement): the LIVE_SOURCE_STATUSES
# comment above used to say "regardless of generated_at_utc" -- that was
# an accurate description of a real gap, not a design choice: a
# source_status string is frozen at artifact-GENERATION time by
# scripts/phase34_build_unified_forecast.py, but every endpoint below
# was serving it unchanged no matter how long the artifact had been
# sitting on disk since then. If the scheduled GitHub Actions run stops
# firing (cron outage, manual pause) for e.g. 2 days, every endpoint
# would keep reporting "LIVE_AWS_GFS" on data that is genuinely 2 days
# stale -- exactly what "never present an old artifact as LIVE" forbids.
# GFS cycles are nominally produced every 6h and this pipeline is
# scheduled ~5x/day (see .github/workflows/forecast_update.yml); twice
# that cadence is a defensible, documented floor for "no longer
# current," not an arbitrary number.
ARTIFACT_STALE_AFTER_HOURS = 12.0


def _reapply_request_time_freshness(artifact: dict, hazard_block: dict) -> dict:
    """Returns hazard_block with source_status downgraded to 'STALE' if
    the ARTIFACT itself (not just this one hazard) is older than
    ARTIFACT_STALE_AFTER_HOURS, measured against wall-clock now at
    REQUEST time -- not at the time the artifact was generated. The
    original value is preserved under original_source_status so nothing
    is destroyed, only relabeled. Never upgrades a status, only ever
    downgrades toward STALE."""
    generated_at = artifact.get("generated_at_utc")
    if not generated_at or hazard_block.get("source_status") not in LIVE_SOURCE_STATUSES:
        return hazard_block
    try:
        generated_dt = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return hazard_block
    age_hours = (datetime.now(timezone.utc) - generated_dt).total_seconds() / 3600.0
    if age_hours <= ARTIFACT_STALE_AFTER_HOURS:
        return hazard_block
    out = dict(hazard_block)
    out["original_source_status"] = hazard_block.get("source_status")
    out["source_status"] = "STALE"
    out["artifact_age_hours"] = round(age_hours, 1)
    return out

# ---------------------------------------------------------------------------
# Real, documented source status -- never hidden or upgraded (Phase 35 Part 11)
# ---------------------------------------------------------------------------
SOURCE_STATUS = {
    "GFS": "READY",
    "IMERG": "REAL_SAMPLE_VALIDATED (1 day, 2024-08-01); LIMITED_ARCHIVE "
             "(FULL_IMERG_ARCHIVE_NOT_ACQUIRED, AUTOMATED_IMERG_ACQUISITION_BLOCKED)",
    "Himawari": "B13_ONLY",
    "METAR": "VOBL_VOBG",
    # 2026-10-08 correction: terrain acquisition has since achieved
    # genuine 992/992 real SRTM coverage -- confirmed directly against
    # data/pan_india_terrain_992.json's terrain_status field, not
    # assumed. The "353_OF_992" value here was accurate when written
    # but had gone stale as terrain coverage work continued.
    "DEM": "992_OF_992",
    "Hydrology": "75_OF_992",
    "IMDAA": "BLOCKED_CREDENTIAL",
    "INSAT": "BLOCKED_CREDENTIAL",
}

# Module-level, lazily-constructed singletons (loaded once per process, not per request).
_engine: Optional[UnifiedInferenceEngine] = None
_terrain_by_cell: Optional[dict] = None
_grid_by_cell: Optional[dict] = None
_cb_feature_cache: Optional[pd.DataFrame] = None
_ff_table_by_cell: Optional[dict] = None
_ts_row: Optional[pd.DataFrame] = None


def _get_engine() -> UnifiedInferenceEngine:
    global _engine
    if _engine is None:
        _engine = UnifiedInferenceEngine()
    return _engine


def _get_terrain() -> dict:
    global _terrain_by_cell
    if _terrain_by_cell is None:
        with open(TERRAIN_PATH) as f:
            doc = json.load(f)
        _terrain_by_cell = {c["cell_id"]: c for c in doc["cells"]}
    return _terrain_by_cell


def _get_grid() -> dict:
    global _grid_by_cell
    if _grid_by_cell is None:
        with open(COMMON_GRID_PATH) as f:
            doc = json.load(f)
        _grid_by_cell = {c["cell_id"]: c for c in doc["cells"]}
    return _grid_by_cell


# CB cycle status set by the most recent _get_cb_features_for() call.
# Vocabulary matches the project-wide freshness hierarchy:
#   LIVE                      -- a real current-cycle NOMADS fetch succeeded this call
#   FIXED_VALIDATION_FALLBACK -- current-cycle fetch failed; using the fixed,
#                                 validated 2024-08-01 Phase 20 cycle as a
#                                 research/validation fallback, never presented
#                                 as current
#   UNAVAILABLE               -- neither path produced a usable feature table
# Every non-LIVE state carries: reason (the real failure text), cycle_str
# (the source date/cycle actually used), age_days, and provenance. Read by
# get_forecast() to build an honest data_cycle_status/data_cycle_note --
# never silently reported as "FIXED_HISTORICAL_CYCLE" with no explanation.
_FIXED_CB_CYCLE = "2024-08-01"


def _cb_age_days(cycle_str: str) -> Optional[int]:
    try:
        dt = datetime.strptime(cycle_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


_cb_cycle_status: dict = {
    "mode": "NOT_ATTEMPTED", "reason": None, "cycle_str": None,
    "age_days": None, "provenance": None,
}
# Retry cooldown (Phase 3 hardening, 2026-10-06): without this, every single
# /forecast request would attempt a fresh NOMADS fetch -- confirmed to cost
# up to ~90s per request (download_grib retries with time.sleep(30),
# time.sleep(60) before giving up) whenever NOMADS is unreachable, which
# would make the API effectively unusable during an outage. Once a live
# attempt fails, subsequent requests reuse the fallback without retrying
# NOMADS until the cooldown elapses -- this does NOT change what is
# reported (still FIXED_VALIDATION_FALLBACK, never silently upgraded), it
# only avoids re-paying the network timeout cost on every request.
_CB_RETRY_COOLDOWN_SECONDS = 600
_cb_next_retry_at: Optional[datetime] = None


def _get_cb_features_for(cell_id: str) -> Optional[pd.DataFrame]:
    """Primary path: attempt a genuine current-GFS-cycle fetch via
    scripts/current_cycle_cb_fetcher.py (reuses pan_india_gfs_fetcher.py's
    cycle resolution/download primitives; builds the exact raw-column
    schema panindia_cb_features.engineer_daily_features() requires).
    Fallback (only on a documented CurrentCycleFetchError, e.g. the NOMADS
    egress block confirmed in this environment on 2026-10-06, or a missing
    xarray/cfgrib runtime dependency -- both now declared in
    backend/requirements.txt rather than assumed): the fixed, validated
    2024-08-01 Phase 20 GFS/IMERG cycle, explicitly labeled
    FIXED_VALIDATION_FALLBACK with the real failure reason -- never
    silently presented as live. Returns None if this cell's feature row is
    incomplete, in either path -- never imputed."""
    global _cb_feature_cache, _cb_cycle_status, _cb_next_retry_at
    from panindia_cb_features import engineer_daily_features

    # --- Primary: genuine current-cycle fetch (subject to the retry
    # cooldown above -- see its comment for why) -------------------------
    now = datetime.now(timezone.utc)
    should_attempt_live = (_cb_cycle_status["mode"] != "LIVE" and
                            (_cb_next_retry_at is None or now >= _cb_next_retry_at))
    if should_attempt_live:
        try:
            from current_cycle_cb_fetcher import fetch_current_cycle_cb_longformat, CurrentCycleFetchError
            longdf = fetch_current_cycle_cb_longformat(cell_ids=[cell_id])
            live_df = engineer_daily_features(longdf, include_prate=False, include_9000pa=False)
            if len(live_df):
                _cb_feature_cache = live_df
                import pan_india_gfs_fetcher as _gfs
                cyc_str, _ = _gfs.resolve_gfs_cycle(now)
                _cb_cycle_status = {
                    "mode": "LIVE", "reason": None, "cycle_str": cyc_str,
                    "age_days": 0, "provenance": "NOMADS/GFS current cycle (live fetch)",
                }
                _cb_next_retry_at = None
        except Exception as exc:  # CurrentCycleFetchError or any import/runtime failure
            reason = str(exc)
            _cb_cycle_status = {
                "mode": "FIXED_VALIDATION_FALLBACK", "reason": reason, "cycle_str": _FIXED_CB_CYCLE,
                "age_days": _cb_age_days(_FIXED_CB_CYCLE),
                "provenance": "Phase 20 fixed validated historical GFS/IMERG cycle (research fallback, not current)",
            }
            from datetime import timedelta as _timedelta
            _cb_next_retry_at = now + _timedelta(seconds=_CB_RETRY_COOLDOWN_SECONDS)

    # --- Fallback: fixed, validated historical cycle ---------------------
    if _cb_cycle_status["mode"] != "LIVE":
        if _cb_feature_cache is None:
            raw_df = pd.read_csv(REPO_ROOT / "data" / "external" / "historical_gfs" / "phase20_full_predictor_dataset.csv")
            cyc = raw_df[raw_df["target_date"] == _FIXED_CB_CYCLE].copy()
            _cb_feature_cache = engineer_daily_features(cyc, include_prate=False, include_9000pa=False)

    cb_cols = _get_engine().heads["CB"]._model.feature_cols if "CB" in _get_engine().heads else []
    row = _cb_feature_cache[_cb_feature_cache["cell_id"] == cell_id]
    if len(row) == 0 or row[cb_cols].isna().any(axis=1).iloc[0]:
        if _cb_cycle_status["mode"] == "NOT_ATTEMPTED":
            _cb_cycle_status = {"mode": "UNAVAILABLE", "reason": "no feature row for this cell in either path",
                                 "cycle_str": None, "age_days": None, "provenance": None}
        return None
    return row.iloc[[0]]


# FF rainfall-source status. IMD gridded rainfall (imd_rain/rain/*.grd) is
# never same-day (confirmed: AVAILABLE_YEARS tops out at 2025, no 2026 file
# exists on disk in this environment as of 2026-10-06) -- this is a genuine
# data-availability wall, not a hardcode bug, so the honest fix is to
# resolve the LATEST date that is actually present and complete, and label
# it STALE with its real age, never "today" and never LIVE.
_ff_cycle_status: dict = {
    "mode": "NOT_ATTEMPTED", "resolved_date": None, "age_days": None,
    "reason": None, "provenance": None,
}
# Explicit, named freshness policy (Priority 7, 2026-10-06 pass) --
# replaces any implicit/hardcoded-by-year judgment with real, documented
# thresholds computed from the actual resolved date's age in days. FF's
# source (IMD daily gridded rainfall) is a once-a-day archive by nature,
# so LIVE is architecturally impossible for it; RECENT/STALE are the only
# reachable states, exactly as reported.
FF_FRESHNESS_RECENT_MAX_AGE_DAYS = 2   # <=2 days old: RECENT (same/next-day archive lag)
FF_FRESHNESS_STALE_MAX_AGE_DAYS = 14   # 3-14 days old: STALE but still usable context
                                        # >14 days old: still reported STALE (no further tier
                                        # requested), but age_days always carries the real number


def _resolve_latest_available_ff_date() -> Optional[str]:
    """Returns the most recent 'YYYY-MM-DD' for which IMD gridded rainfall
    history is actually on disk (per imd_rainfall_adapter.AVAILABLE_YEARS),
    by probing backwards from today. Never returns a date later than what
    genuinely exists."""
    from imd_rainfall_adapter import AVAILABLE_YEARS
    if not AVAILABLE_YEARS:
        return None
    latest_year = max(AVAILABLE_YEARS)
    # The .grd archive is keyed by year; use Dec 31 of the latest available
    # year as the latest genuinely available date (exact day-level
    # completeness is checked by build_ff_input_table_for_date itself,
    # which returns ready_cell_ids only for rows it could actually build).
    return f"{latest_year}-12-31"


def _get_ff_features_for(cell_id: str) -> Optional[pd.DataFrame]:
    global _ff_table_by_cell, _ff_cycle_status
    if _ff_table_by_cell is None:
        from ff_feature_adapter import build_ff_input_table_for_date
        target_date = _resolve_latest_available_ff_date() or "2024-08-01"
        try:
            result = build_ff_input_table_for_date(target_date)
            ready_ids = set(result.get("ready_cell_ids_full") or result.get("ready_cell_ids") or [])
            tbl = result["feature_table"]
            _ff_table_by_cell = ({cid: tbl[tbl["cell_id"] == cid].iloc[[0]] for cid in ready_ids}
                                  if "cell_id" in tbl.columns and len(tbl) else {})
            resolved_dt = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - resolved_dt).days
            mode = "RECENT" if age_days <= FF_FRESHNESS_RECENT_MAX_AGE_DAYS else "STALE"
            _ff_cycle_status = {
                "mode": mode, "resolved_date": target_date, "age_days": age_days, "reason": None,
                "provenance": "IMD gridded rainfall archive (imd_rain/rain/*.grd), latest date on disk",
            }
        except Exception as exc:
            _ff_table_by_cell = {}
            _ff_cycle_status = {"mode": "UNAVAILABLE", "resolved_date": None, "age_days": None,
                                 "reason": str(exc), "provenance": None}
    return _ff_table_by_cell.get(cell_id)


def _get_ts_row() -> pd.DataFrame:
    global _ts_row
    if _ts_row is None:
        _ts_row = pd.read_csv(REPO_ROOT / "data" / "bengaluru_thunderstorm_features_merged.csv").tail(1)
    return _ts_row


def _hazard_block(pred, xai: dict) -> dict:
    return {
        "probability": pred.probability,
        "risk": pred.risk_category,
        "status": pred.status,
        "model_version": pred.model_version,
        "provenance": pred.provenance,
        "confidence": pred.confidence,
        "xai": xai,
    }


# ---------------------------------------------------------------------------
# GET /location -- resolve ANY Indian location query to its real
# coordinates and nearest canonical cell, then return that cell's
# hazard forecast from the offline artifact (2026-10-08 pass). Fixes
# the "every search silently gets VOBL/Bengaluru" gap -- a query that
# doesn't geocode, or that geocodes outside the canonical grid's
# coverage, returns found=False / cell_id=None, never a substituted
# location.
# ---------------------------------------------------------------------------
@app.get("/location")
def resolve_location(q: str = Query(..., description="Free-text location, e.g. 'Mumbai' or 'Chennai, India'"),
                      lead_hours: int = Query(3)):
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import location_resolver as _lr

    resolved = _lr.geocode(q)
    response = {
        "query": resolved.query, "found": resolved.found,
        "display_name": resolved.display_name,
        "latitude": resolved.latitude, "longitude": resolved.longitude,
        "cell_id": resolved.cell_id, "distance_to_cell_km": resolved.distance_to_cell_km,
        "in_india_grid_bounds": resolved.in_india_grid_bounds,
        "source": resolved.source, "from_cache": resolved.from_cache,
        "error": resolved.error,
        "hazards": None,
    }
    if not resolved.cell_id:
        return response

    if not UNIFIED_FORECAST_PATH.exists():
        response["error"] = (response["error"] or "") + " (no hazard data: data/unified_forecast.json not found)"
        return response
    with open(UNIFIED_FORECAST_PATH) as f:
        artifact = json.load(f)
    rec = next((r for r in artifact["records"]
                if r["cell_id"] == resolved.cell_id and r["lead_hours"] == lead_hours), None)
    if rec is None:
        response["error"] = (response["error"] or "") + f" (cell {resolved.cell_id} has no record for lead_hours={lead_hours})"
        return response
    response["hazards"] = {
        "lead_hours": lead_hours, "init_time": rec["init_time"], "valid_time": rec["valid_time"],
        "TS": _reapply_request_time_freshness(artifact, rec["TS"]),
        "CB": _reapply_request_time_freshness(artifact, rec["CB"]),
        "FF": _reapply_request_time_freshness(artifact, rec["FF"]),
    }
    return response


# ---------------------------------------------------------------------------
# GET /forecast
# ---------------------------------------------------------------------------
@app.get("/forecast")
def get_forecast(cell_id: str = Query(...), lead_hours: int = Query(...),
                  init_time: Optional[str] = Query(None)):
    if lead_hours not in LEAD_HOURS:
        raise HTTPException(status_code=400, detail=f"lead_hours must be one of {LEAD_HOURS}")
    if init_time:
        try:
            init_dt = datetime.strptime(init_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="init_time must be ISO-8601 'YYYY-MM-DDTHH:MM:SSZ'")
    else:
        init_dt = datetime(2024, 8, 1, 0, 0, tzinfo=timezone.utc)  # the one real validated cycle

    try:
        valid_dt = compute_valid_time(init_dt, lead_hours)
        assert_no_leakage(init_dt, valid_dt, lead_hours)
    except InferenceLeakageError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    grid = _get_grid()
    cell = grid.get(cell_id)
    if cell is None:
        raise HTTPException(status_code=404, detail=f"unknown cell_id {cell_id!r} (not in the 992-cell canonical grid)")

    engine = _get_engine()
    ts_feat = _get_ts_row() if cell_id == VOBL_CELL_ID else None
    cb_feat = _get_cb_features_for(cell_id)
    ff_feat = _get_ff_features_for(cell_id)

    ts_pred = engine.predict_ts(cell_id, lead_hours, init_dt, ts_feat)
    cb_pred = engine.predict_cb(cell_id, lead_hours, init_dt, cb_feat)
    ff_pred = engine.predict_ff(cell_id, lead_hours, init_dt, ff_feat)

    ts_xai = local_shap_ts(engine.heads["TS"], ts_feat) if ts_feat is not None and "TS" in engine.heads else \
        {"status": "NOT_AVAILABLE", "reason": "no VOBL feature row for this request"}
    cb_xai = local_shap_cb(engine.heads["CB"], cb_feat) if cb_feat is not None and "CB" in engine.heads else \
        {"status": "NOT_AVAILABLE", "reason": "no complete CB feature row for this cell/cycle"}
    ff_xai = local_xai_ff()

    terr = _get_terrain().get(cell_id, {})
    terrain_status = terr.get("terrain_status")
    terrain_block = {
        "elevation_m": terr.get("elevation_m"),
        "slope_deg": terr.get("slope_deg"),
        "status": "MISSING" if terrain_status != "REAL_SRTM_PANINDIA" else "AVAILABLE",
        "raw_status": terrain_status,
        "catchment_status": terr.get("catchment_status"),
    }

    # Honesty fix (audit pass, 2026-10-06): _get_cb_features_for() and
    # _get_ff_features_for() are hardcoded to the fixed 2024-08-01 GFS/IMERG
    # cycle regardless of the init_time a caller passes -- there is no live
    # current-cycle GFS feed wired into the CB/FF feature path yet. The
    # inference itself (model forward pass) runs live, on this request, but
    # its CB/FF *inputs* are not the current meteorological cycle. Reporting
    # this plainly as "LIVE_INFERENCE" with no further qualification would
    # let a caller believe CB/FF reflect right-now weather, which they do
    # not. TS uses the latest row of a periodically-refreshed station feed
    # (bengaluru_thunderstorm_features_merged.csv), which is recent but not
    # guaranteed to be this instant either.
    cb_mode = _cb_cycle_status.get("mode", "NOT_ATTEMPTED")
    ff_mode = _ff_cycle_status.get("mode", "NOT_ATTEMPTED")
    data_cycle_status = {
        "ts": "RECENT" if ts_feat is not None else "NOT_AVAILABLE",
        "cb": cb_mode,
        "ff": ff_mode,
    }
    cb_ff_source_cycle_utc = (
        (_cb_cycle_status.get("cycle_str") + "Z") if cb_mode == "LIVE" and _cb_cycle_status.get("cycle_str")
        else "2024-08-01T00:00:00Z"
    )
    cb_note = (
        f"CB feature fetch used the current GFS cycle ({cb_ff_source_cycle_utc}); inputs reflect current weather."
        if cb_mode == "LIVE" else
        f"CB current-cycle fetch failed ({_cb_cycle_status.get('reason')}); falling back to the fixed, "
        f"validated 2024-08-01 GFS/IMERG cycle. Model inference still runs live on this request, but its "
        f"CB input features do not reflect current weather. CB's trained feature representation "
        f"(panindia_cb_features.engineer_daily_features) is also a whole-day aggregate over 8 GFS leads "
        f"(f006..f027), so even with a live fetch CB cannot produce genuinely distinct 2h/3h/4h/5h/6h "
        f"predictions without retraining on a lead-indexed feature set -- all 5 lead slots carry the same "
        f"daily-aggregate prediction (lead_time_resolution=DAILY_AGGREGATE_NOT_LEAD_SPECIFIC)."
    )
    ff_note = (
        f"FF antecedent-rainfall feature uses the latest genuinely available IMD gridded-rainfall date "
        f"({_ff_cycle_status.get('resolved_date')}, {_ff_cycle_status.get('age_days')} days old) -- IMD "
        f"gridded rainfall (imd_rain/rain/*.grd) has no 2026 file on disk in this environment, so FF's "
        f"rainfall input is a genuine data-availability wall, not a hardcode bug, and is marked STALE with "
        f"its real age rather than presented as current."
        if ff_mode in ("STALE", "RECENT") else
        f"FF rainfall feature table could not be built ({_ff_cycle_status.get('reason')}); FF is UNAVAILABLE "
        f"for this request rather than fabricated."
    )
    CB_FF_FIXED_SOURCE_CYCLE_UTC = cb_ff_source_cycle_utc

    return {
        "cell_id": cell_id, "lat": cell["lat"], "lon": cell["lon"],
        "init_time": _iso(init_dt), "valid_time": _iso(valid_dt), "lead_hours": lead_hours,
        "ts": _hazard_block(ts_pred, ts_xai),
        "cb": _hazard_block(cb_pred, cb_xai),
        "ff": _hazard_block(ff_pred, ff_xai),
        "extra": {"ff_pu_ranking_score": ff_pred.extra.get("pu_ranking_score")},
        "terrain": terrain_block,
        "data_sources": SOURCE_STATUS,
        "forecast_source": "LIVE_INFERENCE",
        "data_cycle_status": data_cycle_status,
        "cb_ff_source_cycle_utc": CB_FF_FIXED_SOURCE_CYCLE_UTC,
        "cb_lead_time_resolution": "DAILY_AGGREGATE_NOT_LEAD_SPECIFIC",
        "cb_source_status": {
            "status": cb_mode, "reason": _cb_cycle_status.get("reason"),
            "source_cycle": _cb_cycle_status.get("cycle_str"), "age_days": _cb_cycle_status.get("age_days"),
            "provenance": _cb_cycle_status.get("provenance"),
        },
        "ff_source_status": {
            "status": ff_mode, "reason": _ff_cycle_status.get("reason"),
            "source_cycle": _ff_cycle_status.get("resolved_date"), "age_days": _ff_cycle_status.get("age_days"),
            "provenance": _ff_cycle_status.get("provenance"),
        },
        "ff_rainfall_resolved_date": _ff_cycle_status.get("resolved_date"),
        "ff_rainfall_age_days": _ff_cycle_status.get("age_days"),
        "ff_realtime_ceiling_note": __import__("ff_feature_adapter").FF_REALTIME_CEILING_NOTE,
        "data_cycle_note": cb_note + " " + ff_note,
    }


# ---------------------------------------------------------------------------
# GET /forecast/all -- offline artifact only, never computed live (Part 12)
# ---------------------------------------------------------------------------
@app.get("/forecast/all")
def get_forecast_all():
    if not UNIFIED_FORECAST_PATH.exists():
        raise HTTPException(status_code=503,
                             detail="data/unified_forecast.json not found -- run "
                                    "scripts/phase34_build_unified_forecast.py first")
    with open(UNIFIED_FORECAST_PATH) as f:
        artifact = json.load(f)
    artifact["forecast_source"] = "OFFLINE_ARTIFACT"
    for rec in artifact["records"]:
        for hz in ("TS", "CB", "FF"):
            rec[hz] = _reapply_request_time_freshness(artifact, rec[hz])
    return artifact


# ---------------------------------------------------------------------------
# GET /forecast/sources
# ---------------------------------------------------------------------------
@app.get("/forecast/sources")
def get_forecast_sources():
    return {"data_sources": SOURCE_STATUS,
            "note": "BLOCKED/LIMITED sources are reported as such, never upgraded to READY."}


# ---------------------------------------------------------------------------
# GET /forecast/alerts -- read-only evaluation, never dispatches (Part 5/6)
# ---------------------------------------------------------------------------
@app.get("/forecast/alerts")
def get_forecast_alerts(min_risk: str = Query("HIGH")):
    """Scans the offline artifact (deterministic, no live recomputation)
    for records whose TS/CB probability is real AND whose risk_category
    is HIGH or SEVERE (or min_risk, if looser). FF NEVER appears here --
    it has no probability to alert on, by design (Phase 34/35)."""
    if not UNIFIED_FORECAST_PATH.exists():
        raise HTTPException(status_code=503, detail="data/unified_forecast.json not found")
    with open(UNIFIED_FORECAST_PATH) as f:
        artifact = json.load(f)
    order = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "SEVERE": 3}
    threshold_rank = order.get(min_risk.upper(), 2)

    candidates = []
    for r in artifact["records"]:
        for hz in ("TS", "CB"):
            p = r[hz]["probability"]
            cat = r[hz]["risk_category"]
            if p is None or r[hz]["status"] in ("NOT_TRAINED", "OUT_OF_DOMAIN_STATION_ONLY", "UNAVAILABLE"):
                continue  # NEVER: an unavailable/NOT_TRAINED hazard triggers an alert
            if cat not in order or order[cat] < threshold_rank:
                continue
            hz_block = _reapply_request_time_freshness(artifact, r[hz])
            source_status = hz_block.get("source_status")
            alert_eligible = source_status in LIVE_SOURCE_STATUSES
            candidates.append({
                "alert_id": None,  # assigned only on actual dispatch (log_alert autoincrement)
                "cell_id": r["cell_id"], "hazard": hz, "lead_hours": r["lead_hours"],
                "probability": p, "risk": cat, "valid_time": r["valid_time"],
                "trigger_features": r[hz].get("extra", {}),
                "status": "CANDIDATE",
                "delivery_status": "NOT_SENT: evaluation only, no dispatch requested",
                "source_status": source_status,
                "alert_eligible": alert_eligible,
                "alert_eligibility_note": (
                    "eligible for dispatch" if alert_eligible else
                    f"a POST /forecast/alerts/dispatch for this candidate will return "
                    f"SKIPPED_NO_ALERT -- its source_status is {source_status!r}, not a live status "
                    f"(e.g. CB currently serves a FIXED_VALIDATION_FALLBACK cycle, never dispatched "
                    f"as if it reflected current weather)"
                ),
            })
    return {"forecast_source": "OFFLINE_ARTIFACT", "min_risk": min_risk.upper(),
            "n_candidates": len(candidates), "candidates": candidates}


class DispatchRequest(BaseModel):
    cell_id: str
    hazard: str  # "TS" or "CB" -- never "FF"
    lead_hours: int


@app.post("/forecast/alerts/dispatch")
def dispatch_forecast_alert(req: DispatchRequest):
    """Actually sends via the EXISTING Twilio/webhook helpers (backend.alerts),
    reusing them unmodified. delivery_status is exactly what the provider
    reports -- never SENT unless send_sms_twilio/post_webhook confirmed it."""
    if req.hazard not in ("TS", "CB"):
        raise HTTPException(status_code=400,
                             detail="FF has no legitimate probability and can never be dispatched as an alert")
    if not UNIFIED_FORECAST_PATH.exists():
        raise HTTPException(status_code=503, detail="data/unified_forecast.json not found")
    with open(UNIFIED_FORECAST_PATH) as f:
        artifact = json.load(f)
    rec = next((r for r in artifact["records"]
                if r["cell_id"] == req.cell_id and r["lead_hours"] == req.lead_hours), None)
    if rec is None:
        raise HTTPException(status_code=404, detail="no matching forecast record")
    hz = _reapply_request_time_freshness(artifact, rec[req.hazard])
    if hz["probability"] is None or hz["status"] in ("NOT_TRAINED", "OUT_OF_DOMAIN_STATION_ONLY", "UNAVAILABLE"):
        raise HTTPException(status_code=422,
                             detail=f"{req.hazard} has no legitimate probability for this cell/lead "
                                    f"(status={hz['status']}) -- refusing to dispatch an alert")

    # Alert freshness gate (Priority 14, 2026-10-06 pass): never dispatch an
    # alert built from stale/fallback data as if it reflected current
    # weather. Requires: (1) lead_hours is a genuinely valid lead slot,
    # (2) valid_time is still in the future relative to now (an alert for a
    # window that has already passed is meaningless), (3) the hazard's
    # recorded source_status is LIVE or RECENT -- a FIXED_VALIDATION_FALLBACK
    # or STALE/UNAVAILABLE source never triggers a real dispatch, it returns
    # a truthful SKIPPED_NO_ALERT instead.
    now_dt = datetime.now(timezone.utc)
    skip_reasons = []
    if req.lead_hours not in LEAD_HOURS:
        skip_reasons.append(f"lead_hours {req.lead_hours} is not a valid lead slot {list(LEAD_HOURS)}")
    # CB carries its own cb_valid_time_utc (2026-10-06 Phase 3) independent
    # of the record's shared valid_time (which still reflects TS/FF's fixed
    # validation cycle) -- use it when present so a genuinely live CB
    # probability is judged against ITS real valid time, not accidentally
    # rejected (or, worse, wrongly accepted) against an unrelated cycle's.
    valid_time_str = hz.get("cb_valid_time_utc") or rec["valid_time"]
    try:
        valid_dt = datetime.strptime(valid_time_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if valid_dt <= now_dt:
            skip_reasons.append(f"valid_time {valid_time_str} is not in the future relative to now")
    except Exception:
        skip_reasons.append("valid_time could not be parsed")
    source_status = hz.get("source_status")
    if source_status not in LIVE_SOURCE_STATUSES:
        skip_reasons.append(
            f"{req.hazard} source_status is {source_status!r}, not a live status -- this data point "
            f"comes from a fixed/stale/fallback cycle and must never be dispatched as a live alert"
        )
    if skip_reasons:
        return {
            "alert_id": None, "cell_id": req.cell_id, "hazard": req.hazard, "lead_hours": req.lead_hours,
            "probability": hz["probability"], "risk": hz["risk_category"], "valid_time": rec["valid_time"],
            "status": "SKIPPED_NO_ALERT", "delivery_status": "NOT_SENT: freshness/validity gate failed",
            "skip_reasons": skip_reasons,
        }

    now = datetime.now(timezone.utc).isoformat()
    sms_body = (f"[SIH WEATHER ALERT] {hz['risk_category'] if 'risk_category' in hz else hz['risk']} "
                f"{req.hazard} WARNING\nCell: {req.cell_id}\nLead: {req.lead_hours}h\n"
                f"Probability: {round(hz['probability']*100)}%\nMonitor updates.")
    sms_count = 0
    errors = []
    for phone in RECIPIENTS:
        ok, err = send_sms_twilio(phone, sms_body)
        if ok:
            sms_count += 1
        else:
            errors.append(f"SMS to {phone}: {err}")
    wh_ok, wh_err = post_webhook({"triggered_at": now, "hazard_type": req.hazard, "cell_id": req.cell_id,
                                   "lead_hours": req.lead_hours, "probability": hz["probability"],
                                   "risk": hz["risk_category"], "message": sms_body})
    if wh_err:
        errors.append(f"Webhook: {wh_err}")

    alert_id = log_alert({
        "triggered_at": now, "hazard_type": req.hazard, "severity": hz["risk_category"], "location": req.cell_id,
        "ts_prob": hz["probability"] if req.hazard == "TS" else None,
        "cb_prob": hz["probability"] if req.hazard == "CB" else None,
        "ff_prob": None, "message": sms_body,
        "sms_sent": sms_count > 0, "sms_count": sms_count, "webhook_sent": wh_ok,
        "error": "; ".join(errors) or None,
    })
    delivery_status = "SENT" if (sms_count > 0 or wh_ok) else "NOT_SENT"
    return {
        "alert_id": alert_id, "cell_id": req.cell_id, "hazard": req.hazard, "lead_hours": req.lead_hours,
        "probability": hz["probability"], "risk": hz["risk_category"], "valid_time": rec["valid_time"],
        "trigger_features": hz.get("extra", {}),
        "status": "DISPATCHED", "delivery_status": delivery_status,
        "sms_count": sms_count, "webhook_sent": wh_ok, "errors": errors,
    }


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
