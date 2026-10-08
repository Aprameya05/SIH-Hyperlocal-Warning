#!/usr/bin/env python3
"""
Alert Delivery Backend -- FastAPI
POST /alert  -> sends SMS via Twilio + logs to SQLite
GET  /alerts -> returns recent alert history

Environment variables (set in .env or export):
  TWILIO_ACCOUNT_SID   -- Twilio account SID
  TWILIO_AUTH_TOKEN    -- Twilio auth token
  TWILIO_FROM_NUMBER   -- Twilio phone number (e.g. +15555550100)
  ALERT_RECIPIENTS     -- comma-separated E.164 phone numbers to notify
  ALERT_WEBHOOK_URL    -- optional: POST alert JSON to this URL too

Run: uvicorn backend.alerts:app --host 0.0.0.0 --port 8000 --reload
     or: python backend/alerts.py  (starts uvicorn directly)

Systemd service file is written by this script to /etc/systemd/system/sih-alerts.service
when run with: python backend/alerts.py --install-service
"""

import json
import os
import sqlite3
import sys
import urllib.request
import urllib.parse
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# FastAPI / Pydantic -- install: pip install fastapi uvicorn pydantic python-dotenv
try:
    from fastapi import FastAPI, HTTPException, status
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError:
    print("FastAPI not installed -- run: pip install fastapi uvicorn pydantic")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # python-dotenv optional; vars can be exported directly

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# DB_PATH: SQLite needs real POSIX file-locking/fsync semantics that some
# mounted/networked/FUSE filesystems do not implement correctly, which can
# produce "disk I/O error" on writes even with ample free space and a
# structurally valid database file (confirmed on this dev environment via
# direct SQLite write tests at multiple paths under the project mount --
# see docs/PIPELINE_OWNERSHIP.md). ALERTS_DB_PATH lets deployment
# relocate the database off such a mount without any code change; the
# default is unchanged from before (data/alerts.db next to the repo).
DB_PATH = Path(os.environ.get("ALERTS_DB_PATH", "")) if os.environ.get("ALERTS_DB_PATH") else (
    Path(__file__).parent.parent / "data" / "alerts.db"
)
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.environ.get("TWILIO_FROM_NUMBER", "")
RECIPIENTS_RAW = os.environ.get("ALERT_RECIPIENTS", "")
RECIPIENTS = [r.strip() for r in RECIPIENTS_RAW.split(",") if r.strip()]
WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")

# ---------------------------------------------------------------------------
# DB SETUP
# ---------------------------------------------------------------------------

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            triggered_at TEXT NOT NULL,
            hazard_type  TEXT NOT NULL,
            severity     TEXT NOT NULL,
            location     TEXT,
            ts_prob      REAL,
            cb_prob      REAL,
            ff_prob      REAL,
            message      TEXT,
            sms_sent     INTEGER DEFAULT 0,
            sms_count    INTEGER DEFAULT 0,
            webhook_sent INTEGER DEFAULT 0,
            error        TEXT
        )
    """)
    con.commit()
    con.close()


def log_alert(row: dict) -> int:
    con = sqlite3.connect(str(DB_PATH))
    cur = con.execute("""
        INSERT INTO alerts
          (triggered_at, hazard_type, severity, location, ts_prob, cb_prob, ff_prob,
           message, sms_sent, sms_count, webhook_sent, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row["triggered_at"], row["hazard_type"], row["severity"],
        row.get("location"), row.get("ts_prob"), row.get("cb_prob"), row.get("ff_prob"),
        row.get("message"), row.get("sms_sent", 0), row.get("sms_count", 0),
        row.get("webhook_sent", 0), row.get("error"),
    ))
    alert_id = cur.lastrowid
    con.commit()
    con.close()
    return alert_id


def fetch_alerts(limit: int = 50) -> list:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# DELIVERY
# ---------------------------------------------------------------------------

def send_sms_twilio(to: str, body: str) -> tuple[bool, str]:
    """Send SMS via Twilio REST API. Returns (success, error_msg)."""
    if not TWILIO_SID or not TWILIO_TOKEN or not TWILIO_FROM:
        return False, "Twilio credentials not configured"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
    payload = urllib.parse.urlencode({
        "From": TWILIO_FROM,
        "To": to,
        "Body": body,
    }).encode()
    creds = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("sid"):
                return True, ""
            return False, result.get("message", "Unknown Twilio error")
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:
            err_json = json.loads(body_bytes)
            return False, err_json.get("message", str(e))
        except Exception:
            return False, str(e)
    except Exception as e:
        return False, str(e)


def post_webhook(payload: dict) -> tuple[bool, str]:
    """POST alert JSON to the configured webhook URL."""
    if not WEBHOOK_URL:
        return False, "No webhook URL configured"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        WEBHOOK_URL, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "SIH-AlertBackend/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 300, ""
    except Exception as e:
        return False, str(e)


def build_sms_text(hazard_type: str, severity: str, location: str,
                   ts_prob: float, cb_prob: float, ff_prob: float) -> str:
    loc = location or "BLR/VOBL Region"
    lines = [
        f"[SIH WEATHER ALERT] {severity} {hazard_type.upper()} WARNING",
        f"Location: {loc}",
        f"TS: {round(ts_prob*100)}%  CB: {round(cb_prob*100)}%  FF: {round(ff_prob*100)}%",
        "Take precautions. Monitor updates.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API MODELS
# ---------------------------------------------------------------------------

class AlertRequest(BaseModel):
    hazard_type: str             # "thunderstorm", "cloudburst", "flash_flood", "multi"
    severity: str                # "LOW", "MODERATE", "HIGH", "SEVERE"
    location: Optional[str] = "VOBL/BLR Region"
    ts_prob: Optional[float] = 0.0
    cb_prob: Optional[float] = 0.0
    ff_prob: Optional[float] = 0.0
    message: Optional[str] = None
    recipients: Optional[list[str]] = None  # override env recipients for this alert


class AlertResponse(BaseModel):
    alert_id: int
    triggered_at: str
    sms_sent: bool
    sms_count: int
    webhook_sent: bool
    errors: list[str]


# ---------------------------------------------------------------------------
# APP
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SIH Hyperlocal Warning -- Alert Backend",
    description="Real-time alert delivery via SMS (Twilio) and webhook for VOBL/BLR hazard events",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict to your Cloudflare Pages domain in production
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()


@app.get("/health")
def health():
    # Phase 35 Part 3: additive enrichment only -- every field that was
    # here before this phase is still here, unchanged. The new fields
    # describe the Phase 34/35 unified forecast engine's own health,
    # which is a different concern from Twilio/webhook delivery config
    # but is served from the same /health path per the Phase 35
    # instruction to preserve this endpoint rather than add a second one
    # at the same path (Starlette would never route to a second handler
    # registered for an already-matched path+method).
    base = {
        "status": "ok",
        "twilio_configured": bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM),
        "recipients_count": len(RECIPIENTS),
        "webhook_configured": bool(WEBHOOK_URL),
        "db": str(DB_PATH),
    }
    try:
        from backend.unified_api import SOURCE_STATUS, _get_engine
        engine = _get_engine()
        model_versions = {
            "CB": "panindia_cb_v1 (Phase 21, LODO-validated XGBoost)" if "CB" in engine.heads else "UNAVAILABLE",
            "TS": "models/thunderstorm_model.pkl (Phase 25, VOBL station)" if "TS" in engine.heads else "UNAVAILABLE",
            "FF": "RESEARCH_ONLY_model_c_logistic (PU-corrected)" if "FF" in engine.heads else "UNAVAILABLE",
            "shared_backbone": engine.backbone_status,
        }
        artifact_path = Path(__file__).parent.parent / "data" / "unified_forecast.json"
        unified_forecast_status = "READY" if artifact_path.exists() else "NOT_TRAINED"

        # Pipeline health block (Priority 13, 2026-10-06 pass): lets an
        # operator answer "is the production pipeline alive?" from one
        # endpoint -- real generation timestamp, real source cycle, real
        # per-hazard status, real artifact age. Additive only; every field
        # above is unchanged.
        pipeline_health = {"artifact_exists": artifact_path.exists()}
        if artifact_path.exists():
            try:
                import json as _json
                from datetime import datetime as _dt, timezone as _tz
                with open(artifact_path) as _f:
                    _doc = _json.load(_f)
                gen_at = _doc.get("generated_at_utc")
                age_hours = None
                if gen_at:
                    gen_dt = _dt.fromisoformat(gen_at.replace("Z", "+00:00"))
                    age_hours = round((_dt.now(_tz.utc) - gen_dt).total_seconds() / 3600.0, 2)
                records = _doc.get("records", [])
                from collections import Counter as _Counter
                cb_counts = _Counter((r.get("CB", {}) or {}).get("source_status") for r in records)
                ff_counts = _Counter((r.get("FF", {}) or {}).get("source_status") for r in records)
                ts_counts = _Counter((r.get("TS", {}) or {}).get("source_status") for r in records)
                pipeline_health.update({
                    "last_generated_at_utc": gen_at,
                    "artifact_age_hours": age_hours,
                    "artifact_age_hours_caveat": (
                        "this is the artifact FILE's rewrite age, not the underlying GFS data's "
                        "age -- see gfs_freshness below for the real data-freshness computation"
                    ),
                    "source_cycle_date": _doc.get("source_cycle_date"),
                    "n_records": _doc.get("n_records"),
                    "n_cells": _doc.get("n_cells"),
                    "validation": _doc.get("validation", {}),
                    "cb_status_counts": dict(cb_counts),
                    "ff_status_counts": dict(ff_counts),
                    "ts_status_counts": dict(ts_counts),
                })

                # Real GFS data freshness (2026-10-08 pass): computed from
                # CB's own cb_init_time_utc (the actual GFS cycle init
                # time), never from the artifact's rewrite time -- using
                # the single centralized policy in scripts/freshness_policy.py
                # so this never drifts from the alert gate's own notion of
                # freshness.
                try:
                    import sys as _sys
                    _sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
                    import freshness_policy as _fp
                    first_cb = (records[0].get("CB", {}) if records else {}) or {}
                    cb_src_status = first_cb.get("source_status")
                    cb_init = first_cb.get("cb_init_time_utc")
                    is_fixed = cb_src_status == "FIXED_VALIDATION_FALLBACK"
                    gfs_fresh = _fp.compute_freshness("GFS", cb_init, is_fixed_validation=is_fixed)
                    pipeline_health["gfs_freshness"] = {
                        "source_timestamp_utc": gfs_fresh.source_timestamp_utc,
                        "age_hours": gfs_fresh.age_hours,
                        "freshness_status": gfs_fresh.freshness_status,
                    }
                except Exception as _fexc:  # noqa: BLE001
                    pipeline_health["gfs_freshness"] = {"error": f"{type(_fexc).__name__}: {_fexc}"}
            except Exception as _exc:  # noqa: BLE001
                pipeline_health["read_error"] = f"{type(_exc).__name__}: {_exc}"

        # Live /forecast's own CB/FF cycle state (if a request has run this
        # process -- these are module-level caches, so they reflect this
        # worker's most recent live-fetch attempt, not the batch artifact).
        try:
            from backend.unified_api import _cb_cycle_status, _ff_cycle_status
            pipeline_health["live_cb_cycle_status"] = _cb_cycle_status.get("mode")
            pipeline_health["live_ff_cycle_status"] = _ff_cycle_status.get("mode")
        except Exception:
            pass

        base.update({
            "service": "SIH Hyperlocal Warning -- Unified Forecast + Alert Backend",
            "model_versions": model_versions,
            # Was hardcoded to "2024-08-01T00:00:00Z" -- wrong/stale as
            # soon as CB started genuinely using live GFS cycles (2026-10-06
            # pass). Now reads the real artifact's own init_time_utc, which
            # tracks whatever TS/FF's basis actually is (still the fixed
            # validation cycle until they get a live source of their own).
            "forecast_init_time": _doc.get("init_time_utc") if artifact_path.exists() else None,
            "grid_cells": 992,
            "lead_times_hours": [2, 3, 4, 5, 6],
            "data_source_status": SOURCE_STATUS,
            "unified_forecast_artifact_status": unified_forecast_status,
            "pipeline_health": pipeline_health,
        })
    except Exception as exc:  # noqa: BLE001
        # Never let the unified-engine enrichment break the pre-existing
        # Twilio/webhook health check this endpoint already provided.
        base["unified_forecast_engine_error"] = f"{type(exc).__name__}: {exc}"
    return base


@app.post("/alert", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
def trigger_alert(req: AlertRequest):
    now = datetime.now(timezone.utc).isoformat()
    errors = []

    # Build SMS text
    sms_body = req.message or build_sms_text(
        req.hazard_type, req.severity, req.location,
        req.ts_prob or 0.0, req.cb_prob or 0.0, req.ff_prob or 0.0,
    )

    # Determine recipients
    targets = req.recipients if req.recipients else RECIPIENTS

    # Send SMS
    sms_count = 0
    for phone in targets:
        ok, err = send_sms_twilio(phone, sms_body)
        if ok:
            sms_count += 1
        else:
            errors.append(f"SMS to {phone}: {err}")

    # Webhook
    webhook_payload = {
        "triggered_at": now,
        "hazard_type": req.hazard_type,
        "severity": req.severity,
        "location": req.location,
        "ts_prob": req.ts_prob,
        "cb_prob": req.cb_prob,
        "ff_prob": req.ff_prob,
        "message": sms_body,
    }
    wh_ok, wh_err = post_webhook(webhook_payload)
    if wh_err:
        errors.append(f"Webhook: {wh_err}")

    # Log to DB
    alert_id = log_alert({
        "triggered_at": now,
        "hazard_type": req.hazard_type,
        "severity": req.severity,
        "location": req.location,
        "ts_prob": req.ts_prob,
        "cb_prob": req.cb_prob,
        "ff_prob": req.ff_prob,
        "message": sms_body,
        "sms_sent": sms_count > 0,
        "sms_count": sms_count,
        "webhook_sent": wh_ok,
        "error": "; ".join(errors) or None,
    })

    return AlertResponse(
        alert_id=alert_id,
        triggered_at=now,
        sms_sent=sms_count > 0,
        sms_count=sms_count,
        webhook_sent=wh_ok,
        errors=errors,
    )


@app.get("/alerts")
def get_alerts(limit: int = 50):
    return {"alerts": fetch_alerts(limit)}


# ---------------------------------------------------------------------------
# SERVICE INSTALLER
# ---------------------------------------------------------------------------

SERVICE_TEMPLATE = """[Unit]
Description=SIH Hyperlocal Warning -- Alert Backend
After=network.target

[Service]
Type=simple
WorkingDirectory={work_dir}
ExecStart={python} -m uvicorn backend.alerts:app --host 0.0.0.0 --port 8000
EnvironmentFile={work_dir}/.env
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def install_service():
    import subprocess
    work_dir = Path(__file__).parent.parent.resolve()
    python = sys.executable
    service_text = SERVICE_TEMPLATE.format(work_dir=work_dir, python=python)
    service_path = Path("/etc/systemd/system/sih-alerts.service")
    try:
        service_path.write_text(service_text)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "sih-alerts"], check=True)
        subprocess.run(["systemctl", "start", "sih-alerts"], check=True)
        print(f"Service installed and started. Check: systemctl status sih-alerts")
    except PermissionError:
        print("Run as root to install systemd service.")
        print("Service file content:\n")
        print(service_text)


if __name__ == "__main__":
    if "--install-service" in sys.argv:
        install_service()
    else:
        import uvicorn
        uvicorn.run("backend.alerts:app", host="0.0.0.0", port=8000, reload=True)
