"""
radar_router.py
===============
Step 6a — FastAPI router for storm proximity endpoints.

Mount in your existing main.py:
    from radar_router import router as radar_router
    app.include_router(radar_router, prefix="/radar", tags=["Radar / Proximity"])

Endpoints:
    GET  /radar/proximity          — latest Himawari BT signal + IMERG
    GET  /radar/history            — last N frames (default 6)
    GET  /radar/image/latest       — latest PNG thumbnail (for Streamlit)
    GET  /radar/image/{timestamp}  — specific frame PNG
    GET  /radar/status             — data freshness check

All responses include CORS headers so the Streamlit frontend can call directly.
"""

import json, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse, FileResponse

router = APIRouter()

# Paths — relative to csir-repo root (same dir as main.py)
HIMAWARI_DIR = Path("data") / "himawari_realtime"
IMERG_DIR    = Path("data") / "imerg_realtime"


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _data_age_minutes(fetched_at_utc_str: str) -> float:
    """Return how many minutes ago the data was fetched."""
    try:
        fetched = datetime.datetime.strptime(
            fetched_at_utc_str, "%Y-%m-%dT%H:%M:%SZ")
        now     = datetime.datetime.utcnow()
        return round((now - fetched).total_seconds() / 60, 1)
    except Exception:
        return -1.0


def _alert_color(alert: str) -> str:
    return {"RED": "#e74c3c", "ORANGE": "#e67e22",
            "YELLOW": "#f1c40f", "GREEN": "#27ae60"}.get(alert, "#95a5a6")


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/proximity")
def get_proximity():
    """
    Returns the merged Himawari + IMERG proximity signal for VOBL.

    Response schema:
    {
      "station": "VOBL — Bengaluru International Airport",
      "alert_level": "GREEN" | "YELLOW" | "ORANGE" | "RED",
      "alert_color": "#hex",
      "storm_within_50km": bool,
      "nearest_storm_km": float | null,
      "bt_min_C": float | null,
      "area_deep_conv_km2": float,
      "scene_time_ist": "YYYY-MM-DD HH:MM IST",
      "data_age_minutes": float,
      "himawari": { ...full Himawari JSON... },
      "imerg": { ...full IMERG JSON... } | null,
      "warnings": [str]
    }
    """
    himawari = _load_json(HIMAWARI_DIR / "himawari_latest.json")
    imerg    = _load_json(IMERG_DIR    / "imerg_latest.json") or None

    if not himawari:
        raise HTTPException(
            status_code=503,
            detail=(
                "Himawari data not yet available. "
                "Run fetch_himawari_realtime.py first."
            )
        )

    warnings = []
    data_age = _data_age_minutes(himawari.get("fetched_at_utc", ""))
    if data_age > 20:
        warnings.append(f"Himawari data is {data_age:.0f} min old — fetcher may be down")
    if "NICT-PNG" in himawari.get("data_source", ""):
        warnings.append("Using degraded NICT PNG source — BT accuracy ±5 K")
    if imerg:
        imerg_age = _data_age_minutes(imerg.get("fetched_at_utc", ""))
        imerg_lat = imerg.get("latency_hours", 0)
        if imerg_lat > 5:
            warnings.append(f"IMERG data is {imerg_lat:.1f}h behind real-time (normal for Early run)")

    alert = himawari.get("alert_level", "UNKNOWN")

    response = {
        # Top-level summary (what dashboard needs directly)
        "station":           himawari.get("station"),
        "alert_level":       alert,
        "alert_color":       _alert_color(alert),
        "alert_description": himawari.get("alert_description", ""),
        "storm_within_50km": himawari.get("storm_within_50km", False),
        "nearest_storm_km":  himawari.get("nearest_storm_km"),
        "nearest_storm_lat": himawari.get("nearest_storm_lat"),
        "nearest_storm_lon": himawari.get("nearest_storm_lon"),
        "bt_min_C":          himawari.get("bt_min_C"),
        "area_deep_conv_km2":himawari.get("area_deep_conv_km2", 0),
        "scene_time_ist":    himawari.get("scene_time_ist"),
        "data_age_minutes":  data_age,
        # IMERG corroboration
        "imerg_precip_max_mm_hr":   imerg.get("precip_max_mm_hr") if imerg else None,
        "imerg_heavy_area_km2":     imerg.get("heavy_area_km2")   if imerg else None,
        "imerg_convection_flag":    imerg.get("convection_flag")   if imerg else None,
        "imerg_scene_ist":          imerg.get("scene_time_ist")    if imerg else None,
        # Warnings
        "warnings": warnings,
        # Full payloads
        "himawari": himawari,
        "imerg":    imerg,
    }

    return JSONResponse(content=response)


@router.get("/history")
def get_history(n: int = 6):
    """
    Returns the last N frames from the rolling JSONL log.
    Default n=6 (1 hour of 10-min scenes).
    """
    log_path = HIMAWARI_DIR / "himawari_vobl_log.jsonl"
    if not log_path.exists():
        return JSONResponse(content={"frames": [], "count": 0})

    with open(log_path) as f:
        lines = [l.strip() for l in f if l.strip()]

    frames = []
    for line in lines[-n:]:
        try:
            frames.append(json.loads(line))
        except Exception:
            pass

    return JSONResponse(content={
        "frames": frames,
        "count":  len(frames),
        "n_requested": n,
    })


@router.get("/image/latest")
def get_latest_image():
    """Serve the latest BT thumbnail PNG."""
    pngs = sorted(HIMAWARI_DIR.glob("himawari_vobl_2*.png"))
    if not pngs:
        raise HTTPException(status_code=404, detail="No PNG available yet")
    return FileResponse(pngs[-1], media_type="image/png",
                        filename="himawari_vobl_latest.png")


@router.get("/image/{timestamp}")
def get_image_by_timestamp(timestamp: str):
    """
    Serve a specific frame PNG by timestamp (YYYYMMDD_HHmm).
    Example: /radar/image/20260725_0930
    """
    p = HIMAWARI_DIR / f"himawari_vobl_{timestamp}.png"
    if not p.exists():
        raise HTTPException(status_code=404,
                            detail=f"No PNG for timestamp {timestamp}")
    return FileResponse(p, media_type="image/png",
                        filename=f"himawari_vobl_{timestamp}.png")


@router.get("/status")
def get_status():
    """Data freshness and source health check."""
    himawari = _load_json(HIMAWARI_DIR / "himawari_latest.json")
    imerg    = _load_json(IMERG_DIR    / "imerg_latest.json") or {}

    h_age = _data_age_minutes(himawari.get("fetched_at_utc", "")) if himawari else -1
    i_age = _data_age_minutes(imerg.get("fetched_at_utc", ""))    if imerg    else -1

    pngs = sorted(HIMAWARI_DIR.glob("himawari_vobl_2*.png"))

    return JSONResponse(content={
        "himawari": {
            "available":    bool(himawari),
            "source":       himawari.get("data_source", "N/A"),
            "scene_ist":    himawari.get("scene_time_ist", "N/A"),
            "age_minutes":  h_age,
            "alert":        himawari.get("alert_level", "N/A"),
            "frames_on_disk": len(pngs),
        },
        "imerg": {
            "available":   bool(imerg),
            "scene_ist":   imerg.get("scene_time_ist", "N/A"),
            "age_minutes": i_age,
            "latency_h":   imerg.get("latency_hours", "N/A"),
        },
        "checked_at_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    })