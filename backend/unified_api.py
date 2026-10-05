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

# ---------------------------------------------------------------------------
# Real, documented source status -- never hidden or upgraded (Phase 35 Part 11)
# ---------------------------------------------------------------------------
SOURCE_STATUS = {
    "GFS": "READY",
    "IMERG": "REAL_SAMPLE_VALIDATED (1 day, 2024-08-01); LIMITED_ARCHIVE "
             "(FULL_IMERG_ARCHIVE_NOT_ACQUIRED, AUTOMATED_IMERG_ACQUISITION_BLOCKED)",
    "Himawari": "B13_ONLY",
    "METAR": "VOBL_VOBG",
    "DEM": "353_OF_992",
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


def _get_cb_features_for(cell_id: str) -> Optional[pd.DataFrame]:
    """Real Phase 20 GFS-derived CB features for the fixed source cycle
    this engine's live endpoint serves (2024-08-01, the only cycle with a
    validated real IMERG day alongside it). Returns None if this cell's
    feature row is incomplete -- never imputed here."""
    global _cb_feature_cache
    if _cb_feature_cache is None:
        from panindia_cb_features import engineer_daily_features
        raw_df = pd.read_csv(REPO_ROOT / "data" / "external" / "historical_gfs" / "phase20_full_predictor_dataset.csv")
        cyc = raw_df[raw_df["target_date"] == "2024-08-01"].copy()
        _cb_feature_cache = engineer_daily_features(cyc, include_prate=False, include_9000pa=False)
    cb_cols = _get_engine().heads["CB"]._model.feature_cols if "CB" in _get_engine().heads else []
    row = _cb_feature_cache[_cb_feature_cache["cell_id"] == cell_id]
    if len(row) == 0 or row[cb_cols].isna().any(axis=1).iloc[0]:
        return None
    return row.iloc[[0]]


def _get_ff_features_for(cell_id: str) -> Optional[pd.DataFrame]:
    global _ff_table_by_cell
    if _ff_table_by_cell is None:
        from ff_feature_adapter import build_ff_input_table_for_date
        result = build_ff_input_table_for_date("2024-08-01")
        ready_ids = set(result.get("ready_cell_ids_full") or result.get("ready_cell_ids") or [])
        tbl = result["feature_table"]
        _ff_table_by_cell = ({cid: tbl[tbl["cell_id"] == cid].iloc[[0]] for cid in ready_ids}
                              if "cell_id" in tbl.columns and len(tbl) else {})
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
            candidates.append({
                "alert_id": None,  # assigned only on actual dispatch (log_alert autoincrement)
                "cell_id": r["cell_id"], "hazard": hz, "lead_hours": r["lead_hours"],
                "probability": p, "risk": cat, "valid_time": r["valid_time"],
                "trigger_features": r[hz].get("extra", {}),
                "status": "CANDIDATE",
                "delivery_status": "NOT_SENT: evaluation only, no dispatch requested",
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
    hz = rec[req.hazard]
    if hz["probability"] is None or hz["status"] in ("NOT_TRAINED", "OUT_OF_DOMAIN_STATION_ONLY", "UNAVAILABLE"):
        raise HTTPException(status_code=422,
                             detail=f"{req.hazard} has no legitimate probability for this cell/lead "
                                    f"(status={hz['status']}) -- refusing to dispatch an alert")

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
