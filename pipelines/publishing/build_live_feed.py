#!/usr/bin/env python3
"""
pipelines/publishing/build_live_feed.py
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

ROOT = Path(__file__).resolve().parents[2]
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
    cells, hours, hydrology = [], [], {}
    terrain_meta = {}
    if g_ok:
        fcs = sorted(grid["forecasts"], key=lambda f: f.get("forecast_lead_hours", 0))
        by_hour = [{c["cell_id"]: c for c in f["grid_cells"]} for f in fcs]
        hours = [{"forecast_hour": f.get("forecast_lead_hours"), "valid_time_utc": f.get("forecast_valid_at_utc"),
                  "is_primary": bool(f.get("is_primary")), "fetch_status": f.get("fetch_status")} for f in fcs]
        order = [c["cell_id"] for c in (next(f for f in fcs if f.get("is_primary")) if any(f.get("is_primary") for f in fcs) else fcs[-1])["grid_cells"]]
        terr = _load("data/pan_india_terrain_992.json") or {}
        tmap = {c["cell_id"]: c for c in terr.get("cells", [])}
        out_by_id = {}
        if unified:
            u = fi.sanitize_artifact(unified, now=now)
            for cid, d in (u.get("daily_outlook") or {}).items():
                cb = d.get("CB") or {}
                if cb.get("temporal_status") == "CURRENT" and cb.get("probability") is not None:
                    out_by_id[cid] = cb
        r3 = lambda v, n=1: None if v is None else round(v, n)
        for idx, cid in enumerate(order):
            rows = [h.get(cid) or {} for h in by_hour]
            c0 = next((r for r in rows if r), {})
            t = tmap.get(cid) or {}
            cb = out_by_id.get(cid)
            hyd = t.get("hydrology") or {}
            has_h = t.get("catchment_status") == "OBSERVED_SPARSE"
            if has_h:
                hydrology[str(idx)] = {k: v for k, v in hyd.items() if v is not None}
            ff_pu = c0.get("flash_flood_research_pu_score")
            cells.append([c0.get("lat"), c0.get("lon"),
                          *[r3(r.get("cape"), 0) for r in rows], *[r3(r.get("cin"), 0) for r in rows],
                          *[r3(r.get("qpe_mm"), 1) for r in rows],
                          None if cb is None else round(cb["probability"], 4), ff_pu,
                          r3(t.get("elevation_m"), 0), r3(t.get("slope_deg"), 1),
                          r3(t.get("flood_susceptibility_dem_proxy"), 3), 1 if has_h else 0])
        terrain_meta = {"status": "REAL_SRTM_PANINDIA" if tmap else "UNAVAILABLE",
                        "source": "AWS Terrarium SRTM tiles (data/pan_india_terrain_srtm_992.json)",
                        "susceptibility_note": "flood_susceptibility_dem_proxy is a DERIVED DEM index, not a flood prediction",
                        "hydrology_cells": sum(c[-1] for c in cells), "hydrology_total": len(cells),
                        "hydrology_note": (terr.get("hydrology_pipeline") or {}).get("coverage_note")}
        age = _age_h(grid.get("generated_at_utc"), now)
        sources.append({"id": "gfs_grid", "label": "GFS NWP guidance (CAPE, CIN, precipitation)",
                        "generated_at_utc": grid.get("generated_at_utc"), "model_init_utc": grid.get("gfs_cycle_init_utc"),
                        "forecast_hour": grid.get("primary_forecast_lead_hours"), "age_hours": age, "state": _state(age),
                        "unavailable_predictors": grid.get("unavailable_predictors", [])})
        sources.append({"id": "terrain_hydrology", "label": "SRTM terrain + gauge hydrology (static)",
                        "generated_at_utc": terr.get("generated_at_utc"), "age_hours": None, "state": "STATIC",
                        "note": "Static geography, not a forecast; hydrology exists for only %d of %d cells" %
                                (terrain_meta["hydrology_cells"], terrain_meta["hydrology_total"])})
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

    # ---- station evidence (each item carries its OWN observation time; ages are recomputed by the client) ---------
    v = vobl or {}
    metar, himi = v.get("metar") or {}, (v.get("satellite") or {}).get("himawari9") or {}
    mp = v.get("met_parameters") or {}
    evidence = {
        "metar": None if not metar else {k: metar.get(k) for k in (
            "station", "obs_time", "temp_c", "dewpoint_c", "rh_pct", "wind_dir", "wind_speed_kt", "wind_gust_kt",
            "visibility_km", "flight_category", "thunderstorm_present", "raw")},
        "satellite": None if not himi else {
            "timestamp_utc": himi.get("timestamp_utc") and himi["timestamp_utc"] + "Z",
            "band": "Himawari-9 Band 13 (10.4 um) brightness temperature",
            "min_bt_50km_c": himi.get("min_bt_50km"), "mean_bt_50km_c": himi.get("mean_bt_50km"),
            "cold_pixels_count": himi.get("cold_pixels_count"), "cold_threshold_c": himi.get("threshold_celsius"),
            "storm_detected": himi.get("storm_detected")},
        "model_winds": {k: mp.get(k) for k in ("ERA5_u_500hPa", "ERA5_v_500hPa", "ERA5_u_850hPa", "ERA5_v_850hPa")}
                       if mp else None,
        "model_winds_note": "Wind components are the station-model inputs (GFS-derived); wind shear is NOT available "
                            "pan-India (null in every grid cell).",
        "precipitable_water_mm": mp.get("iwv_mm"),
        "precipitable_water_note": mp.get("iwv_source"),
    }

    doc = {
        "schema": "drift_live_v1", "mode": "LIVE_OPERATIONAL",
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disclaimer": "DRIFT outputs are model advisories. They are NOT IMD/NDMA warnings. No forecast availability "
                      "time is recorded, so no operational 2-6 h lead time is claimed.",
        "sources": sources, "problems": problems,
        "grid": {"bounds": (grid or {}).get("bounds"), "step_deg": (grid or {}).get("grid_step_deg"),
                 "nwp_hours": hours,
                 "columns": (["lat", "lon"] + ["cape_%d" % i for i in range(len(hours))] + ["cin_%d" % i for i in range(len(hours))]
                             + ["qpe_%d" % i for i in range(len(hours))]
                             + ["cb_daily_outlook", "ff_research_pu_rank", "elevation_m", "slope_deg", "ff_susceptibility_proxy", "has_hydrology"]),
                 "cells": cells, "hydrology": hydrology, "terrain": terrain_meta},
        "hazards": {
            "thunderstorm": {
                "status": "PARTIAL", "pan_india_model": "UNAVAILABLE",
                "summary": "Station model for VOBL (Bengaluru) slot windows is live; pan-India thunderstorm shows NWP "
                           "instability guidance only (CAPE/CIN). No pan-India thunderstorm probability exists.",
                "target": "Thunderstorm at VOBL within a 6-h IST slot (observation label); pan-India: none",
                "station_slots": slots, "station_evidence": evidence, "evaluation": "thunderstorm",
                "supported_predictors": ["CAPE", "CIN"], "unsupported_predictors": ["K-index", "Totals-Totals",
                    "850 hPa convergence", "0-6 km wind shear", "PWAT (pan-India)", "cloud-top temperature / drop rate"]},
            "extreme_rainfall": {
                "status": "AVAILABLE_DAILY_OUTLOOK_ONLY" if any(c[5] is not None for c in cells) else "UNAVAILABLE",
                "summary": "Daily heavy-rain-day score (>= 64.5 mm/24 h proxy) per grid cell from a LODO-validated "
                           "XGBoost; an uncalibrated ranking score for the whole forecast day. Not a cloudburst, "
                           "not a 2-6 h forecast.",
                "target": "IMD gridded daily rainfall >= 64.5 mm at 0.25 deg", "evaluation": "extreme_rainfall",
                "distinctions": [
                    {"term": "Heavy-rain-day outlook", "what": "Daily score from panindia_cb_v1 for the whole forecast day (>= 64.5 mm/24 h).",
                     "status": "AVAILABLE (uncalibrated score)"},
                    {"term": "GFS precipitation intensity", "what": "NWP model precipitation at a single model hour. Guidance, not observed.",
                     "status": "AVAILABLE (guidance)"},
                    {"term": "Cloudburst", "what": "Very intense, short-duration, highly localised rainfall (commonly quoted as >= 100 mm/h over ~20-30 km2). "
                     "DRIFT has no verified cloudburst labels and no sub-hourly rainfall input, so it does NOT predict cloudbursts.",
                     "status": "UNAVAILABLE"}]},
            "flash_flood": {
                "status": "UNAVAILABLE",
                "summary": "No validated flash-flood model. A PU-learning ranking exists for research only and is "
                           "not a probability.",
                "missing_for_live": ["observed flood event labels (time + location)", "river-gauge / soil-moisture / "
                                     "antecedent-rainfall predictors", "per-cell catchment hydrology (only %d of %d cells "
                                     "have gauge-derived values)" % (terrain_meta.get("hydrology_cells", 0), terrain_meta.get("hydrology_total", 0)),
                                     "calibration + spatial hold-out evaluation"],
                "available_context": ["GFS precipitation (rainfall trigger)", "SRTM elevation and slope (all cells)",
                                      "DEM flood-susceptibility proxy (derived index)", "gauge catchment hydrology (sparse)"],
                "evaluation": "flash_flood"}},
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
