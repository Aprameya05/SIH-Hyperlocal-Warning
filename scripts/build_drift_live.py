#!/usr/bin/env python3
"""
scripts/build_drift_live.py
===========================
Builds data/drift_live.json, the compact, integrity-checked feed for the DRIFT command centre (drift.html, LIVE mode).

Rules (this file is where "never present a stale/heuristic value as a live prediction" is enforced for the UI):
  * every source is re-verified through backend/forecast_integrity (recompute, never trust labels); a section that
    fails verification is emitted as UNAVAILABLE with the reason, not patched
  * only fields with honest semantics are exported: NWP guidance fields (CAPE, CIN, GFS precipitation), the daily
    heavy-rain-day outlook (uncalibrated score, in-progress daily window), VOBL station slot scores, and the
    PU flood ranking in a research-only block. The grid's heuristic thunderstorm/flash-flood "probabilities" are
    NOT exported at all.
  * nulls stay null; nothing is filled with zero
  * source timestamps are copied from the sources; none is invented
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import alert_policy, forecast_integrity as fi  # noqa: E402

OUT = ROOT / "data" / "drift_live.json"
STALE_AFTER_H = 12.0


def _load(p):
    try:
        return json.load(open(ROOT / p))
    except (OSError, ValueError):
        return None


def _age_h(ts, now):
    d = fi.parse_utc(ts) if isinstance(ts, str) else None
    return None if d is None else round((now - d).total_seconds() / 3600, 2)


def _state(age):
    return "UNAVAILABLE" if age is None else ("FRESH" if age <= STALE_AFTER_H else "STALE")


def build(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    grid, unified, vobl = _load("data/pan_india_grid.json"), _load("data/unified_forecast.json"), _load("forecast.json")
    sources, problems = [], []

    # ---- pan-India grid (NWP guidance fields) ------------------------------------------------------------
    g_ok, g_rep = False, {}
    if grid:
        g_rep = fi.verify_grid_report(grid)
        g_ok = bool(g_rep.get("ok"))
    cells = []
    if g_ok:
        prim = [c for f in grid["forecasts"] if f.get("is_primary") for c in f["grid_cells"]] or grid["grid_cells"]
        out_by_id = {}
        if unified:
            u = fi.sanitize_artifact(unified, now=now)
            for cid, d in (u.get("daily_outlook") or {}).items():
                cb = d.get("CB") or {}
                if cb.get("temporal_status") == "CURRENT" and cb.get("probability") is not None:
                    out_by_id[cid] = cb
        for c in prim:
            cb = out_by_id.get(c["cell_id"])
            cells.append([c["lat"], c["lon"], c.get("cape"), c.get("cin"), c.get("qpe_mm"),
                          None if cb is None else round(cb["probability"], 4),
                          c.get("flash_flood_research_pu_score")])
        age = _age_h(grid.get("generated_at_utc"), now)
        sources.append({"id": "gfs_grid", "label": "GFS NWP guidance (CAPE, CIN, precipitation)",
                        "generated_at_utc": grid.get("generated_at_utc"), "model_init_utc": grid.get("gfs_cycle_init_utc"),
                        "forecast_hour": grid.get("primary_forecast_lead_hours"), "age_hours": age, "state": _state(age),
                        "unavailable_predictors": grid.get("unavailable_predictors", [])})
    else:
        problems.append({"source": "gfs_grid", "reason": "missing" if not grid else "failed integrity gate",
                         "violations": g_rep.get("counts_by_code")})
        sources.append({"id": "gfs_grid", "label": "GFS NWP guidance", "state": "UNAVAILABLE", "age_hours": None})

    # ---- daily outlook provenance --------------------------------------------------------------------------
    u_rep = fi.verify_artifact_report(fi.sanitize_artifact(unified, now=now), now=now) if unified else {"ok": False}
    cb_sample = next(iter((unified or {}).get("daily_outlook", {}).values()), {}).get("CB", {}) if unified else {}
    age_u = _age_h((unified or {}).get("generated_at_utc"), now)
    sources.append({"id": "cb_daily_outlook", "label": "Heavy-rain-day outlook (panindia_cb_v1)",
                    "generated_at_utc": (unified or {}).get("generated_at_utc"),
                    "model_init_utc": cb_sample.get("model_init_time_utc"),
                    "window_start_utc": cb_sample.get("window_start_utc"), "window_end_utc": cb_sample.get("window_end_utc"),
                    "age_hours": age_u, "state": _state(age_u) if u_rep.get("ok") else "UNAVAILABLE",
                    "integrity_ok": bool(u_rep.get("ok"))})

    # ---- VOBL station slots --------------------------------------------------------------------------------
    v_rep = fi.verify_vobl_forecast(vobl) if vobl else {"ok": False}
    gen_v = alert_policy.parse_ist_string((vobl or {}).get("generated_at"))
    age_v = None if gen_v is None else round((now - gen_v).total_seconds() / 3600, 2)
    slots = []
    if vobl and v_rep.get("ok"):
        shap = vobl.get("realtime_shap") or {}
        for s in vobl["slots"]:
            tf = (shap.get(str(s["slot"])) or {}).get("top_features") or []
            slots.append({"slot": s["slot"], "label": s.get("label"), "time": s.get("time"),
                          "score": s.get("ts_probability"), "threshold": s.get("threshold"),
                          "predicted": bool(s.get("ts_predicted")), "source": s.get("source"),
                          "fallback": bool(s.get("fallback")), "valid_from": (s.get("lead_time") or {}).get("valid_from"),
                          "valid_to": (s.get("lead_time") or {}).get("valid_to"),
                          "drivers": [{"feature": t["feature"], "shap": t["shap"], "value": t["value"]} for t in tf[:5]]})
    sources.append({"id": "vobl_station", "label": "VOBL station slot model", "generated_at_utc":
                    None if gen_v is None else gen_v.strftime("%Y-%m-%dT%H:%M:%SZ"), "age_hours": age_v,
                    "state": _state(age_v) if v_rep.get("ok") else "UNAVAILABLE", "integrity_ok": bool(v_rep.get("ok")),
                    "model_version": (vobl or {}).get("model_version")})

    # ---- alert policy status -------------------------------------------------------------------------------
    pol = alert_policy.evaluate_forecast_alerts(vobl or {}, now=now)
    reasons = sorted(pol["reason_counts"])
    ev = _load("data/hazard_evaluation.json")

    doc = {
        "schema": "drift_live_v1", "mode": "LIVE_OPERATIONAL",
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disclaimer": "DRIFT outputs are model advisories. They are NOT IMD/NDMA warnings. No forecast availability "
                      "time is recorded, so no operational 2-6 h lead time is claimed.",
        "sources": sources, "problems": problems,
        "grid": {"bounds": (grid or {}).get("bounds"), "step_deg": (grid or {}).get("grid_step_deg"),
                 "columns": ["lat", "lon", "cape", "cin", "qpe_mm", "cb_daily_outlook", "ff_research_pu_rank"],
                 "cells": cells},
        "hazards": {
            "thunderstorm": {
                "status": "PARTIAL", "pan_india_model": "UNAVAILABLE",
                "summary": "Station model for VOBL (Bengaluru) slot windows is live; pan-India thunderstorm shows NWP "
                           "instability guidance only (CAPE/CIN). No pan-India thunderstorm probability exists.",
                "target": "Thunderstorm at VOBL within a 6-h IST slot (observation label); pan-India: none",
                "station_slots": slots, "evaluation": "thunderstorm"},
            "extreme_rainfall": {
                "status": "AVAILABLE_DAILY_OUTLOOK_ONLY" if any(c[5] is not None for c in cells) else "UNAVAILABLE",
                "summary": "Daily heavy-rain-day score (>= 64.5 mm/24 h proxy) per grid cell from a LODO-validated "
                           "XGBoost; an uncalibrated ranking score for the whole forecast day. Not a cloudburst, "
                           "not a 2-6 h forecast.",
                "target": "IMD gridded daily rainfall >= 64.5 mm at 0.25 deg", "evaluation": "extreme_rainfall"},
            "flash_flood": {
                "status": "UNAVAILABLE",
                "summary": "No validated flash-flood model. A PU-learning ranking exists for research only and is "
                           "not a probability.",
                "missing_for_live": ["observed flood event labels (time + location)", "river-gauge / soil-moisture / "
                                     "antecedent-rainfall predictors", "per-cell catchment hydrology",
                                     "calibration + spatial hold-out evaluation"], "evaluation": "flash_flood"}},
        "alerts": {"eligible": len(pol["eligible_slots"]), "n_evaluated": pol["n_slots"],
                   "withheld_reason_codes": reasons, "withheld_explanation": alert_policy.explain(reasons),
                   "automated_dispatch": "DISABLED"},
        "evaluation_available": ev is not None,
    }
    return doc


def main():
    doc = build()
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"wrote {OUT.relative_to(ROOT)}  {OUT.stat().st_size/1024:.0f} KiB  cells={len(doc['grid']['cells'])}")
    for s in doc["sources"]:
        print(f"  {s['id']:18s} {s['state']:11s} age_h={s.get('age_hours')}")
    if doc["problems"]:
        print("  problems:", doc["problems"])


if __name__ == "__main__":
    main()
