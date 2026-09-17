"""
main.py
=======
CSIR Thunderstorm Prediction System — FastAPI Application
Bengaluru Airport (Station 43295)

Endpoints:
  GET  /                          — health check
  POST /predict                   — daily thunderstorm prediction
  POST /nowcast/predict/slot/{id} — 6-hour slot prediction
  POST /nowcast/predict/all       — all 4 slots in one call
  GET  /nowcast/slots/info        — slot metadata
  POST /rag/explain               — Llama-3.3 forecast explanation
  POST /rag/analogs               — historical analog retrieval
  WS   /ws/lightning              — real-time Blitzortung lightning proxy (wss://)

Run:
  uvicorn main:app --reload

Swagger UI:
  http://127.0.0.1:8000/docs

Author: Satvik (Deployment), Aprameya (ML), CSIR Thunderstorm Project
"""

import os
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
import asyncio
import websockets
import json
import math

# ── APP SETUP ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="CSIR Thunderstorm Prediction System",
    description="AI-based thunderstorm forecasting for Bengaluru Airport (IMD Station 43295)",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE   = Path(__file__).parent
MODELS = BASE / "models"
DATA   = BASE / "data"

# ── SLOT METADATA ─────────────────────────────────────────────────────────────
SLOT_INFO = {
    0: {"label": "0001-0600 IST", "era5_utc": "00Z", "description": "Late night / early morning"},
    1: {"label": "0601-1200 IST", "era5_utc": "06Z", "description": "Morning"},
    2: {"label": "1201-1800 IST", "era5_utc": "12Z", "description": "Afternoon (peak TS window)"},
    3: {"label": "1801-2400 IST", "era5_utc": "18Z", "description": "Evening"},
}

SLOT_NAMES = {0: "Late Night", 1: "Morning", 2: "Afternoon", 3: "Evening"}

# ── LOAD MODELS AT STARTUP ────────────────────────────────────────────────────
daily_model_artifact   = None
nowcast_slot_artifacts = {}

@app.on_event("startup")
def load_models():
    global daily_model_artifact, nowcast_slot_artifacts

    daily_path = MODELS / "thunderstorm_model.pkl"
    if daily_path.exists():
        daily_model_artifact = joblib.load(daily_path)
        print(f"✓ Daily model loaded: {daily_path.name}")
    else:
        print(f"⚠ Daily model not found at {daily_path}")

    for slot_id in range(4):
        slot_path = MODELS / f"nowcast_slot{slot_id}_xgb_v3_calibrated.pkl"
        if slot_path.exists():
            nowcast_slot_artifacts[slot_id] = joblib.load(slot_path)
            print(f"✓ Slot {slot_id} model loaded: {slot_path.name}")
        else:
            print(f"⚠ Slot {slot_id} model not found at {slot_path}")

# ── SCHEMAS — DAILY ───────────────────────────────────────────────────────────
class DailyInput(BaseModel):
    date:       str   = Field(..., example="2026-07-16")
    MAX:        float = Field(..., example=29.0)
    MIN:        float = Field(..., example=21.0)
    AW:         float = Field(..., example=4.0)
    RF:         float = Field(..., example=2.1)
    SSH:        float = Field(..., example=180.0)
    RF_lag1:    float = Field(0.0, example=0.0)
    MAX_lag1:   float = Field(..., example=28.5)
    MIN_lag1:   float = Field(..., example=20.8)
    LABEL_lag1: int   = Field(0,   example=0)

class DailyOutput(BaseModel):
    date:                     str
    thunderstorm_probability: float
    alert_level:              str
    prediction:               bool
    threshold:                float
    message:                  str

# ── SCHEMAS — NOWCAST ─────────────────────────────────────────────────────────
class NowcastInput(BaseModel):
    date:       str = Field(..., example="2026-07-16")
    slot:       int = Field(..., ge=0, le=3)

    MAX:        float = Field(..., example=29.0)
    MIN:        float = Field(..., example=21.0)
    AW:         float = Field(0.0, example=4.0)
    RF:         float = Field(0.0, example=0.0)
    EVP:        float = Field(5.0, example=5.0)
    DRNRF:      float = Field(0.0, example=0.0)
    SSH:        float = Field(300.0, example=300.0)

    RF_3d:       float = Field(0.0)
    RF_7d:       float = Field(0.0)
    MAX_3d_avg:  float = Field(None)
    MIN_3d_avg:  float = Field(None)
    DTR_3d_avg:  float = Field(None)
    RF_lag1:     float = Field(0.0)
    MAX_lag1:    float = Field(None)
    MIN_lag1:    float = Field(None)
    LABEL_lag1:  int   = Field(0)

    CAPE:          float = Field(..., example=500.0)
    K_INDEX:       float = Field(..., example=35.0)
    LIFTED_INDEX:  float = Field(..., example=-2.0)
    TOTALS_TOTALS: float = Field(..., example=45.0)
    PRECIP_WATER:  float = Field(40.0, example=40.0)

    ERA5_T2M:      float = Field(..., example=299.0)
    ERA5_D2M:      float = Field(293.0)
    ERA5_U10:      float = Field(-2.0)
    ERA5_V10:      float = Field(1.0)
    ERA5_CAPE:     float = Field(None)
    ERA5_SP:       float = Field(91500.0)
    ERA5_t_500hPa: float = Field(268.0)
    ERA5_t_700hPa: float = Field(283.0)
    ERA5_t_850hPa: float = Field(293.0)
    ERA5_q_500hPa: float = Field(0.003)
    ERA5_q_700hPa: float = Field(0.009)
    ERA5_q_850hPa: float = Field(0.013)
    ERA5_u_500hPa: float = Field(5.0)
    ERA5_u_700hPa: float = Field(2.0)
    ERA5_u_850hPa: float = Field(-3.0)
    ERA5_v_500hPa: float = Field(2.0)
    ERA5_v_700hPa: float = Field(1.0)
    ERA5_v_850hPa: float = Field(2.0)

    ts_label_lag1_slot: int = Field(0)
    ts_any_yesterday:   int = Field(0)

class NowcastOutput(BaseModel):
    date:           str
    slot:           int
    slot_label:     str
    ts_probability: float
    ts_predicted:   bool
    threshold_used: float
    alert_level:    str

# ── SCHEMAS — RAG ─────────────────────────────────────────────────────────────
class RAGExplainInput(BaseModel):
    query: str = Field(..., example="Why did Slot 2 fire today?")
    date:  str = Field(..., example="2026-07-25")

class RAGExplainOutput(BaseModel):
    query:       str
    date:        str
    explanation: str
    source:      str

class RAGAnalogsInput(BaseModel):
    date:      str   = Field(..., example="2026-07-25")
    cape:      float = Field(None, example=500.0)
    k_index:   float = Field(None, example=38.0)
    slot:      int   = Field(2,    example=2)
    top_n:     int   = Field(5,    example=5)

class RAGAnalogsOutput(BaseModel):
    date:    str
    slot:    int
    analogs: list

# ── HELPERS ───────────────────────────────────────────────────────────────────
def get_alert_level(prob: float) -> str:
    if prob < 0.20:   return "GREEN"
    elif prob < 0.45: return "YELLOW"
    elif prob < 0.70: return "ORANGE"
    else:             return "RED"

def derive_nowcast_features(p: NowcastInput) -> dict:
    import math
    date  = pd.Timestamp(p.date)
    doy   = date.dayofyear
    month = date.month
    slot  = p.slot
    DTR   = p.MAX - p.MIN
    m     = month

    season = 1 if m in [3,4,5] else 2 if m in [6,7,8,9] else 3 if m in [10,11] else 0

    CLIM = {
        (1,0):0.000,(1,1):0.000,(1,2):0.000,(1,3):0.000,
        (2,0):0.000,(2,1):0.000,(2,2):0.009,(2,3):0.013,
        (3,0):0.008,(3,1):0.004,(3,2):0.036,(3,3):0.028,
        (4,0):0.025,(4,1):0.008,(4,2):0.129,(4,3):0.100,
        (5,0):0.129,(5,1):0.036,(5,2):0.194,(5,3):0.181,
        (6,0):0.042,(6,1):0.004,(6,2):0.096,(6,3):0.092,
        (7,0):0.028,(7,1):0.004,(7,2):0.032,(7,3):0.044,
        (8,0):0.020,(8,1):0.008,(8,2):0.052,(8,3):0.060,
        (9,0):0.083,(9,1):0.029,(9,2):0.079,(9,3):0.067,
        (10,0):0.077,(10,1):0.024,(10,2):0.077,(10,3):0.077,
        (11,0):0.008,(11,1):0.008,(11,2):0.021,(11,3):0.017,
        (12,0):0.000,(12,1):0.000,(12,2):0.000,(12,3):0.000,
    }

    obs = {
        "MAX": p.MAX, "MIN": p.MIN, "DTR": DTR,
        "AW": p.AW, "RF": p.RF, "EVP": p.EVP,
        "DRNRF": p.DRNRF, "SSH": p.SSH,
        "RF_3d": p.RF_3d, "RF_7d": p.RF_7d,
        "MAX_3d_avg": p.MAX_3d_avg or p.MAX,
        "MIN_3d_avg": p.MIN_3d_avg or p.MIN,
        "DTR_3d_avg": p.DTR_3d_avg or DTR,
        "RF_lag1": p.RF_lag1,
        "MAX_lag1": p.MAX_lag1 or p.MAX,
        "MIN_lag1": p.MIN_lag1 or p.MIN,
        "LABEL_lag1": p.LABEL_lag1,
        "MONTH_sin": math.sin(2*math.pi*m/12),
        "MONTH_cos": math.cos(2*math.pi*m/12),
        "DOY_sin":   math.sin(2*math.pi*doy/365),
        "DOY_cos":   math.cos(2*math.pi*doy/365),
        "SEASON": season,
        "HA_flag": 0, "RF_nonzero": 1 if p.RF > 0 else 0,
        "CAPE": p.CAPE, "CIN": 0.0,
        "K_INDEX": p.K_INDEX, "LIFTED_INDEX": p.LIFTED_INDEX,
        "TOTALS_TOTALS": p.TOTALS_TOTALS, "PRECIP_WATER": p.PRECIP_WATER,
        "ERA5_T2M": p.ERA5_T2M, "ERA5_D2M": p.ERA5_D2M,
        "ERA5_U10": p.ERA5_U10, "ERA5_V10": p.ERA5_V10,
        "ERA5_CAPE": p.ERA5_CAPE or p.CAPE, "ERA5_SP": p.ERA5_SP,
        "ERA5_t_500hPa": p.ERA5_t_500hPa, "ERA5_t_700hPa": p.ERA5_t_700hPa,
        "ERA5_t_850hPa": p.ERA5_t_850hPa,
        "ERA5_q_500hPa": p.ERA5_q_500hPa, "ERA5_q_700hPa": p.ERA5_q_700hPa,
        "ERA5_q_850hPa": p.ERA5_q_850hPa,
        "ERA5_u_500hPa": p.ERA5_u_500hPa, "ERA5_u_700hPa": p.ERA5_u_700hPa,
        "ERA5_u_850hPa": p.ERA5_u_850hPa,
        "ERA5_v_500hPa": p.ERA5_v_500hPa, "ERA5_v_700hPa": p.ERA5_v_700hPa,
        "ERA5_v_850hPa": p.ERA5_v_850hPa,
        "slot_sin": math.sin(2*math.pi*slot/4),
        "slot_cos": math.cos(2*math.pi*slot/4),
        "slot_month_clim": CLIM.get((m, slot), 0.0),
        "doy_sin": math.sin(2*math.pi*doy/365),
        "doy_cos": math.cos(2*math.pi*doy/365),
        "ts_label_lag1_slot": p.ts_label_lag1_slot,
        "ts_any_yesterday":   p.ts_any_yesterday,
    }
    obs['cape_x_kindex']      = p.CAPE * p.K_INDEX
    obs['li_x_totals']        = abs(p.LIFTED_INDEX) * p.TOTALS_TOTALS
    obs['q_gradient_500_850'] = p.ERA5_q_850hPa - p.ERA5_q_500hPa
    obs['thetae_850']         = p.ERA5_t_850hPa + 2491 * p.ERA5_q_850hPa
    obs['wind_shear_500_850'] = ((p.ERA5_u_500hPa-p.ERA5_u_850hPa)**2 + (p.ERA5_v_500hPa-p.ERA5_v_850hPa)**2)**0.5
    obs['wind_shear_700_850'] = ((p.ERA5_u_700hPa-p.ERA5_u_850hPa)**2 + (p.ERA5_v_700hPa-p.ERA5_v_850hPa)**2)**0.5
    obs['moisture_flux_850']  = p.ERA5_q_850hPa * (p.ERA5_u_850hPa**2 + p.ERA5_v_850hPa**2)**0.5
    obs['moisture_flux_700']  = p.ERA5_q_700hPa * (p.ERA5_u_700hPa**2 + p.ERA5_v_700hPa**2)**0.5
    obs['thickness_500_850']  = p.ERA5_t_850hPa - p.ERA5_t_500hPa
    obs['mid_level_drying']   = p.ERA5_q_700hPa / (p.ERA5_q_850hPa + 1e-9)

    return obs


def build_met_context(date: str) -> str:
    """Read today's met params from upperair CSV for RAG context."""
    upper_path = DATA / "upperair_realtime_43295.csv"
    if not upper_path.exists():
        return "No real-time upper-air data available."

    try:
        df = pd.read_csv(upper_path)
        date_col = next((c for c in df.columns if "date" in c.lower()), None)
        if date_col:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            row = df[df[date_col].dt.strftime("%Y-%m-%d") == date]
            if row.empty:
                row = df.tail(1)
        else:
            row = df.tail(1)

        r = row.iloc[-1]
        parts = []
        for col in ["CAPE", "cape", "CIN", "cin", "K_INDEX", "k_index",
                    "LIFTED_INDEX", "lifted_index", "TOTALS_TOTALS",
                    "PRECIP_WATER", "precip_water"]:
            if col in r.index and pd.notna(r[col]):
                parts.append(f"{col}={r[col]:.2f}")
        return ", ".join(parts) if parts else "Met data present but no key indices found."
    except Exception as e:
        return f"Error reading met data: {e}"


def call_groq_llama(prompt: str) -> str:
    """Call Groq Llama-3.3-70b for explanation generation."""
    try:
        import requests
        groq_key = os.environ.get("GROQ_API_KEY", "")
        if not groq_key:
            return generate_rule_based_explanation(prompt)

        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert meteorologist specializing in thunderstorm forecasting "
                        "for Bengaluru Airport (VOBL), India. You explain forecasts from an "
                        "XGBoost ML model trained on IMD observations, ERA5 reanalysis, and "
                        "upper-air sounding data. Be concise, scientific, and specific to "
                        "Bengaluru's convective climatology. Keep responses under 150 words."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 300,
            "temperature": 0.4,
        }
        res = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=20,
        )
        if res.status_code == 200:
            return res.json()["choices"][0]["message"]["content"].strip()
        else:
            return generate_rule_based_explanation(prompt)
    except Exception:
        return generate_rule_based_explanation(prompt)


def generate_rule_based_explanation(prompt: str) -> str:
    """Fallback rule-based explanation when Groq is unavailable."""
    return (
        "The CSIR XGBoost v3 model evaluates atmospheric instability using CAPE, K-Index, "
        "Lifted Index, and ERA5 reanalysis fields. For Bengaluru Airport (VOBL), the primary "
        "convective window is Slot 2 (1201-1800 IST) driven by surface heating over the Deccan "
        "Plateau. Key triggers include CAPE > 500 J/kg, K-Index > 35, and strong moisture "
        "convergence at 850hPa from Arabian Sea westerly flow during monsoon onset. "
        "The cape_x_kindex interaction term is the top SHAP feature across all slots. "
        "[Note: Groq API unavailable — rule-based fallback active]"
    )


# ── ROUTES — EXISTING ─────────────────────────────────────────────────────────
@app.get("/")
def health():
    nowcast_info = {}
    for s in range(4):
        if s in nowcast_slot_artifacts:
            a = nowcast_slot_artifacts[s]
            nowcast_info[str(s)] = {
                "loaded":    True,
                "version":   "v3_calibrated",
                "threshold": a.get("threshold", "unknown"),
                "slot_name": a.get("slot_name", SLOT_INFO[s]["label"]),
            }
        else:
            nowcast_info[str(s)] = {"loaded": False}
    return {
        "status":         "ok",
        "system":         "CSIR Thunderstorm Prediction System",
        "station":        "Bengaluru Airport — IMD 43295",
        "daily_model":    daily_model_artifact is not None,
        "nowcast_models": nowcast_info,
        "rag_endpoints":  ["/rag/explain", "/rag/analogs"],
    }

@app.post("/predict", response_model=DailyOutput)
def predict_daily(payload: DailyInput):
    if daily_model_artifact is None:
        raise HTTPException(503, "Daily model not loaded")

    model     = daily_model_artifact.get("model") or daily_model_artifact
    features  = daily_model_artifact.get("feature_cols", [])
    threshold = daily_model_artifact.get("threshold", 0.45)

    data = payload.dict()
    vec  = np.array([[data.get(f, 0.0) for f in features]])
    prob = float(model.predict_proba(vec)[0][1])
    pred = prob >= threshold

    return DailyOutput(
        date=payload.date,
        thunderstorm_probability=round(prob, 4),
        alert_level=get_alert_level(prob),
        prediction=pred,
        threshold=threshold,
        message=f"Thunderstorm probability: {prob*100:.1f}%",
    )

@app.get("/nowcast/slots/info")
def slot_info():
    return {"slots": SLOT_INFO}

@app.post("/nowcast/predict/slot/{slot_id}", response_model=NowcastOutput)
def predict_slot(slot_id: int, payload: NowcastInput):
    if slot_id not in range(4):
        raise HTTPException(400, "slot_id must be 0, 1, 2, or 3")
    if slot_id not in nowcast_slot_artifacts:
        raise HTTPException(503, f"Slot {slot_id} model not loaded")
    if payload.slot != slot_id:
        raise HTTPException(400, "URL slot_id must match payload slot field")
    if payload.CAPE is None or payload.CAPE == 0.0:
        raise HTTPException(422, "CAPE is required and cannot be zero")
    if payload.ERA5_T2M is None or payload.ERA5_T2M == 0.0:
        raise HTTPException(422, "ERA5_T2M is required and cannot be zero")

    artifact     = nowcast_slot_artifacts[slot_id]
    model        = artifact["model"]
    feature_cols = artifact["feature_cols"]
    threshold    = artifact["threshold"]

    obs = derive_nowcast_features(payload)
    vec = np.array([[obs.get(f, 0.0) for f in feature_cols]])

    prob = float(model.predict_proba(vec)[0][1])
    pred = prob >= threshold

    return NowcastOutput(
        date=payload.date,
        slot=slot_id,
        slot_label=SLOT_INFO[slot_id]["label"],
        ts_probability=round(prob, 4),
        ts_predicted=pred,
        threshold_used=threshold,
        alert_level=get_alert_level(prob),
    )

@app.post("/nowcast/predict/all")
def predict_all(payloads: list[NowcastInput]):
    if len(payloads) != 4:
        raise HTTPException(400, "Provide exactly 4 inputs, one per slot (0-3)")
    results = []
    for p in sorted(payloads, key=lambda x: x.slot):
        try:
            results.append(predict_slot(p.slot, p))
        except HTTPException as e:
            results.append({"slot": p.slot, "error": e.detail})
    return {"date": payloads[0].date, "predictions": results}


# ── ROUTES — RAG ──────────────────────────────────────────────────────────────
@app.post("/rag/explain", response_model=RAGExplainOutput)
def rag_explain(payload: RAGExplainInput):
    """
    Generate a physical meteorological explanation for the forecast query
    using Llama-3.3-70b (Groq) with real met parameter context.
    """
    # Load today's met context
    met_context = build_met_context(payload.date)

    # Build prompt
    prompt = (
        f"Date: {payload.date}\n"
        f"Station: Bengaluru Airport (VOBL / IMD 43295)\n"
        f"Current atmospheric conditions: {met_context}\n\n"
        f"Slot thresholds: Slot0=0.24, Slot1=0.38, Slot2=0.16, Slot3=0.39\n"
        f"Top SHAP features: cape_x_kindex, ERA5_CAPE (Slot3), K_INDEX, LIFTED_INDEX\n\n"
        f"User question: {payload.query}\n\n"
        f"Provide a concise meteorological explanation referencing the actual values above."
    )

    explanation = call_groq_llama(prompt)
    source = "Groq Llama-3.3-70b" if os.environ.get("GROQ_API_KEY") else "Rule-based fallback"

    return RAGExplainOutput(
        query=payload.query,
        date=payload.date,
        explanation=explanation,
        source=source,
    )


@app.post("/rag/analogs", response_model=RAGAnalogsOutput)
def rag_analogs(payload: RAGAnalogsInput):
    """
    Find historical analog days with similar atmospheric conditions
    from the merged feature dataset.
    """
    features_path = BASE / "bengaluru_thunderstorm_features_merged.csv"
    if not features_path.exists():
        # Try data subfolder
        features_path = DATA / "bengaluru_thunderstorm_features_merged.csv"

    if not features_path.exists():
        raise HTTPException(503, "Historical features dataset not found on server")

    try:
        df = pd.read_csv(features_path, parse_dates=["date"])
    except Exception as e:
        raise HTTPException(500, f"Error reading features dataset: {e}")

    # Filter to same slot climatology month ± 1
    try:
        target_month = pd.Timestamp(payload.date).month
        months = [(target_month - 1) % 12 or 12, target_month, (target_month % 12) + 1]
        df["month"] = pd.to_datetime(df["date"]).dt.month
        df_filtered = df[df["month"].isin(months)].copy()
    except Exception:
        df_filtered = df.copy()

    if df_filtered.empty:
        df_filtered = df.copy()

    # Score similarity using available fields
    score_cols = []
    weights    = []

    if payload.cape is not None and "CAPE" in df_filtered.columns:
        df_filtered["_cape_diff"] = (df_filtered["CAPE"] - payload.cape).abs()
        score_cols.append("_cape_diff")
        weights.append(2.0)

    if payload.k_index is not None and "K_INDEX" in df_filtered.columns:
        df_filtered["_ki_diff"] = (df_filtered["K_INDEX"] - payload.k_index).abs()
        score_cols.append("_ki_diff")
        weights.append(1.5)

    if not score_cols:
        # No scoring possible — return most recent positives
        analogs_df = df_filtered[df_filtered.get("LABEL", df_filtered.get("label", pd.Series(dtype=int))) == 1].tail(payload.top_n)
    else:
        # Normalise and weight
        for col in score_cols:
            rng = df_filtered[col].max() - df_filtered[col].min()
            df_filtered[col] = df_filtered[col] / (rng + 1e-9)

        df_filtered["_score"] = sum(
            df_filtered[col] * w for col, w in zip(score_cols, weights)
        )
        analogs_df = df_filtered.nsmallest(payload.top_n, "_score")

    label_col = "LABEL" if "LABEL" in analogs_df.columns else "label" if "label" in analogs_df.columns else None

    analogs = []
    for _, row in analogs_df.iterrows():
        entry = {
            "date":  str(row["date"])[:10],
            "CAPE":  round(float(row["CAPE"]), 1)  if "CAPE"    in row.index else None,
            "K_INDEX": round(float(row["K_INDEX"]), 1) if "K_INDEX" in row.index else None,
            "LIFTED_INDEX": round(float(row["LIFTED_INDEX"]), 2) if "LIFTED_INDEX" in row.index else None,
            "thunderstorm_occurred": bool(row[label_col]) if label_col else None,
        }
        analogs.append(entry)

    return RAGAnalogsOutput(
        date=payload.date,
        slot=payload.slot,
        analogs=analogs,
    )

# ── LIGHTNING WEBSOCKET PROXY ──────────────────────────────────────────────────

VOBL_LAT = 13.1986
VOBL_LON = 77.7066
LIGHTNING_RADIUS_KM = 250
# Blitzortung bounding box (~2.5° around VOBL)
BBOX = {
    "west":  VOBL_LON - 2.5,
    "east":  VOBL_LON + 2.5,
    "south": VOBL_LAT - 2.5,
    "north": VOBL_LAT + 2.5,
}
BLITZORTUNG_SERVERS = [
    "ws://ws.blitzortung.org:8079",
    "ws://ws.blitzortung.org:8080",
    "ws://ws.blitzortung.org:8078",
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


@app.websocket("/ws/lightning")
async def lightning_proxy(client_ws: WebSocket):
    """
    WebSocket proxy: connects to Blitzortung (ws://), filters strikes within
    250 km of VOBL, and forwards them to the browser over wss://.

    Message format sent to client:
    {"lat": 13.45, "lng": 77.80, "dist_km": 42, "time_ms": 1234567890123}
    """
    await client_ws.accept()
    server_ix = 0

    while True:
        url = BLITZORTUNG_SERVERS[server_ix % len(BLITZORTUNG_SERVERS)]
        server_ix += 1
        try:
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=30,
                close_timeout=10,
            ) as blitz_ws:
                # Subscribe to bounding box
                await blitz_ws.send(json.dumps(BBOX))

                async for raw in blitz_ws:
                    try:
                        d = json.loads(raw)
                        if "lat" not in d or "lon" not in d:
                            continue

                        # Blitzortung sends microdegrees
                        lat = d["lat"] / 1_000_000
                        lng = d["lon"] / 1_000_000
                        dist = _haversine_km(VOBL_LAT, VOBL_LON, lat, lng)

                        if dist > LIGHTNING_RADIUS_KM:
                            continue

                        await client_ws.send_json({
                            "lat":     round(lat, 5),
                            "lng":     round(lng, 5),
                            "dist_km": round(dist, 1),
                            "time_ms": d.get("time", 0) // 1_000_000,  # nanoseconds → ms
                        })

                    except (json.JSONDecodeError, KeyError):
                        continue
                    except WebSocketDisconnect:
                        return

        except WebSocketDisconnect:
            return
        except Exception:
            # Blitzortung dropped us — wait and try next server
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return
            
@app.get("/correction/status")
async def correction_status():
    import json
    from pathlib import Path

    meta_path = Path("models/correction_model_meta.json")
    fc_path = Path("forecast.json")

    meta, fc = {}, {}

    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)

    if fc_path.exists():
        with open(fc_path) as f:
            fc = json.load(f)

    return {
        "correction_model": meta,
        "currently_active": fc.get("himawari_override_active", False),
        "override_slots": fc.get("himawari_override_slots", []),
        "boost_value": fc.get("himawari_boost_value", 0.0),
        "himawari": fc.get("satellite", {}).get("himawari9", {}),
    }


@app.get("/nowcast/explain/{slot_id}")
async def explain_slot(slot_id: int):
    import json
    from pathlib import Path

    if slot_id not in [0, 1, 2, 3]:
        raise HTTPException(status_code=400, detail="slot_id must be 0-3")

    fc_path = Path("forecast.json")

    if not fc_path.exists():
        raise HTTPException(status_code=503, detail="forecast.json not found")

    with open(fc_path) as f:
        fc = json.load(f)

    slot = next((s for s in fc.get("slots", []) if s["slot"] == slot_id), None)

    if not slot:
        raise HTTPException(status_code=404, detail=f"Slot {slot_id} not found")

    mp = fc.get("met_parameters", {})
    shap = fc.get("realtime_shap", {}).get(str(slot_id), {})
    top = shap.get("top_features", [{}])[0] if shap else {}

    override = slot.get("himawari_override", False)
    prob = slot["ts_probability"]

    level = "HIGH" if prob >= 0.5 else "MODERATE" if prob >= 0.3 else "LOW"

    explanation = (
        f"Slot {slot_id} ({slot.get('time','')}) shows {prob*100:.1f}% thunderstorm probability — {level} risk. "
        f"Top driver: {top.get('feature','unknown')} (SHAP={top.get('shap',0):.3f}). "
        f"Atmosphere: CAPE={mp.get('ua_cape_jkg',0):.0f} J/kg, K-Index={mp.get('ua_k_index',0):.1f}. "
    )

    if override:
        explanation += "Himawari-9 satellite override active — deep convection detected near VOBL."

    return {
        "slot": slot_id,
        "probability": prob,
        "risk_level": level,
        "explanation": explanation,
        "himawari_override": override,
        "top_shap_feature": top,
    }            
