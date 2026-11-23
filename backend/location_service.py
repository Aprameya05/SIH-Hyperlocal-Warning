"""
backend/location_service.py
===========================
Location-aware data service for DRIFT's national grid.

Every value returned is traceable: which cell it represents, the source and its resolution, forecast valid time, model
initialisation time, acquisition time, publication time of the NWP input, units, and what KIND of quantity it is.
Statuses are RECOMPUTED here from timestamps; the status/label fields written by producers are never trusted.

Artifacts (all produced by scripts/):
  data/india_grid_025.json       pipelines/grid/build_india_grid.py     land cells, ids, state/district, gazetteer in india_places.json
  data/india_fields_latest.json  pipelines/ingestion/ingest_gfs_forecast.py  GFS fields per cell per forecast hour (+ provenance per file)
  data/india_static_025.json     pipelines/grid/build_terrain_and_gauge_layers.py   terrain and gauge context per cell
  data/drift_live.json           pipelines/publishing/build_live_feed.py     (only the 1 deg heavy-rain-day outlook column is used)
  data/india_ts_model_latest.json models/thunderstorm/predict_thunderstorm.py optional pan-India thunderstorm model output per cell and lead

The JavaScript twin (frontend/assets/drift_location.js) implements the same record logic for the static site;
tests/test_location_parity.py asserts the two agree.
"""
from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

MODEL_MAX_AGE_H = 18.0          # a GFS cycle older than this is STALE (newer 00/12Z cycles exist after 12 h + publication delay)
PAST_TOLERANCE_H = 1.5          # a value whose valid time passed more than this long ago is "PAST_VALID_TIME", not current
RESOLUTION_DEG = 0.25
SAT_MAX_AGE_MIN = 90            # a satellite scan older than this is NOT current (observation, so it ages in minutes, not hours)
SOURCE = "NOAA GFS 0.25 deg (AWS Open Data mirror)"

# thresholds used only for descriptive categories (never probabilities)
CAPE_BANDS = [(300, "WEAK"), (1000, "MARGINAL"), (2500, "MODERATE"), (1e9, "STRONG")]
IMD_24H_BANDS = [(15.5, "NO_HEAVY_RAIN"), (64.5, "MODERATE"), (115.6, "HEAVY"), (204.5, "VERY_HEAVY"), (1e9, "EXTREMELY_HEAVY")]


def parse_utc(s) -> Optional[datetime]:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso(d: Optional[datetime]) -> Optional[str]:
    return None if d is None else d.strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    """Loads artifacts, verifies them independently, indexes them. Reloads when a file's mtime changes."""

    FILES = {"grid": "india_grid_025.json", "places": "india_places.json", "fields": "india_fields_latest.json",
             "static": "india_static_025.json", "live": "drift_live.json", "tsmodel": "india_ts_model_latest.json",
             "rainmodel": "india_rain_model_latest.json", "satellite": "india_satellite_ctt_latest.json"}

    def __init__(self, data_dir: Path = DATA):
        self.dir = data_dir
        self._lock = threading.Lock()
        self._mt: dict = {}
        self.version = ""
        self.reload(force=True)

    def _stamp(self):
        return {k: (os.path.getmtime(self.dir / f) if (self.dir / f).exists() else None) for k, f in self.FILES.items()}

    def reload(self, force=False):
        with self._lock:
            st = self._stamp()
            if not force and st == self._mt:
                return False
            self._mt = st
            self.version = "-".join(f"{int(v or 0)}" for v in st.values())
            load = lambda k: json.load(open(self.dir / self.FILES[k])) if st[k] else None
            self.grid_doc, self.places_doc = load("grid"), load("places")
            self.fields_doc, self.static_doc, self.live_doc, self.ts_doc = load("fields"), load("static"), load("live"), load("tsmodel")
            self.rain_doc = load("rainmodel")
            g = self.grid_doc
            self.cells = g["cells"] if g else []
            self.idx = {c[0]: i for i, c in enumerate(self.cells)}
            self.by_ll = {(c[1], c[2]): i for i, c in enumerate(self.cells)}
            self.states, self.districts = (g["states"], g["districts"]) if g else ([], [])
            self.fields_problems = verify_fields(self.fields_doc, self.grid_doc, self.dir / self.FILES["grid"]) if self.fields_doc else ["fields artifact missing"]
            self.fields_ok = not self.fields_problems
            self.static_idx = {c: i for i, c in enumerate(self.static_doc["cells"])} if self.static_doc else {}
            self.fcol = {c: i for i, c in enumerate(self.fields_doc["cells"])} if self.fields_ok else {}
            self.cb = {}
            if self.live_doc and self.live_doc.get("grid"):
                cols = {n: i for i, n in enumerate(self.live_doc["grid"]["columns"])}
                for r in self.live_doc["grid"]["cells"]:
                    v = r[cols["cb_daily_outlook"]]
                    if v is not None:
                        self.cb[(float(r[0]), float(r[1]))] = v
            self.sat_doc = load("satellite")
            self.sat_problems, self.sat_idx = [], {}
            if self.sat_doc:
                try:
                    from pipelines.validation.validate_satellite_ctt import problems as _sat_problems
                    self.sat_problems = _sat_problems(self.sat_doc, grid_ids=[c[0] for c in self.cells] if self.cells else None)
                except Exception as e:          # an unreadable/odd artifact must never break the rest of the service
                    self.sat_problems = [f"satellite artifact could not be verified: {e}"]
            else:
                self.sat_problems = ["satellite artifact not published"]
            self.sat_ok = bool(self.sat_doc) and not self.sat_problems
            if self.sat_ok:
                self.sat_idx = {c: i for i, c in enumerate(self.sat_doc["cells"])}
            self.rain_ok = bool(self.rain_doc and self.rain_doc.get("meta", {}).get("model_evaluated"))
            self.rain_idx = {c: i for i, c in enumerate(self.rain_doc["cells"])} if self.rain_doc else {}
            self.ts_ok = False
            if self.ts_doc:
                self.ts_idx = {c: i for i, c in enumerate(self.ts_doc["cells"])}
                self.ts_ok = bool(self.ts_doc.get("meta", {}).get("model_evaluated"))
            return True

    # ---------------------------------------------------------------- geography
    def resolve(self, lat: float, lon: float) -> dict:
        la, lo = round(round(lat / RESOLUTION_DEG) * RESOLUTION_DEG, 2), round(round(lon / RESOLUTION_DEG) * RESOLUTION_DEG, 2)
        i = self.by_ll.get((la, lo))
        if i is not None:
            return {"covered": True, "cell_id": self.cells[i][0], "query": {"lat": lat, "lon": lon}, "cell_centre": {"lat": la, "lon": lo}}
        near = []
        for dy in (-0.5, -0.25, 0, 0.25, 0.5):
            for dx in (-0.5, -0.25, 0, 0.25, 0.5):
                j = self.by_ll.get((round(la + dy, 2), round(lo + dx, 2)))
                if j is not None:
                    near.append((math.hypot(self.cells[j][1] - lat, self.cells[j][2] - lon), j))
        out = {"covered": False, "query": {"lat": lat, "lon": lon}, "cell_centre": {"lat": la, "lon": lo},
               "reason": "the point lies in a 0.25 deg cell that is less than 50% Indian land (sea or another country)"}
        if near:
            d, j = min(near)
            out["nearest_covered_cell"] = {"cell_id": self.cells[j][0], "lat": self.cells[j][1], "lon": self.cells[j][2], "distance_deg": round(d, 3)}
        return out

    def location_info(self, cid: str) -> dict:
        c = self.cells[self.idx[cid]]
        d = self.districts[c[5]] if c[5] is not None else None
        return {"cell_id": cid, "lat": c[1], "lon": c[2], "land_fraction": c[3], "state": self.states[c[4]] if c[4] is not None else None,
                "district": d["name"] if d else None, "admin_method": c[6], "resolution_deg": RESOLUTION_DEG,
                "cell_definition": self.grid_doc["meta"]["cell_definition"],
                "admin_note": "state/district describe the cell CENTRE point"}

    def search(self, q: str, limit: int = 10) -> list:
        q = (q or "").strip().lower()
        if len(q) < 2:
            return []
        out = []
        for p in (self.places_doc or {}).get("places", []):
            n = p["ascii"].lower()
            if n.startswith(q) or q in n:
                out.append({"type": "place", "name": p["name"], "cell_id": p["cell"], "lat": p["lat"], "lon": p["lon"], "population": p["pop"],
                            "rank": (0 if n == q else 1 if n.startswith(q) else 2, -p["pop"])})
        for i, s in enumerate(self.states):
            if q in s.lower():
                out.append({"type": "state", "name": s, "rank": (0 if s.lower() == q else 1 if s.lower().startswith(q) else 2, 1e9)})
        seen = set()
        for i, d in enumerate(self.districts):
            if q in d["name"].lower() and (d["name"], d["state"]) not in seen:
                seen.add((d["name"], d["state"]))
                out.append({"type": "district", "name": d["name"], "state": d["state"], "rank": (0 if d["name"].lower() == q else 1 if d["name"].lower().startswith(q) else 2, 1e9 - 1)})
        out.sort(key=lambda r: r["rank"])
        for r in out:
            r.pop("rank")
        return out[:limit]

    # ---------------------------------------------------------------- values
    def _hour_meta(self):
        m = self.fields_doc["meta"]
        init = parse_utc(m["cycle_init_utc"])
        return init, m["files"]

    def default_hour(self, now: datetime) -> int:
        init, files = self._hour_meta()
        for k, f in enumerate(files):
            v = parse_utc(f["valid_time_utc"])
            if v and v >= now - timedelta(hours=PAST_TOLERANCE_H):
                return k
        return len(files) - 1

    def satellite(self, cid: str, now: datetime) -> dict:
        """Himawari-9 B13 brightness temperature for the cell: an OBSERVATION shown only while current (<= SAT_MAX_AGE_MIN old)."""
        base = {"kind": "SATELLITE_OBSERVATION", "quantity": "BRIGHTNESS_TEMPERATURE_K", "is_observation": True, "is_retrieved_cloud_top_temperature": False,
                "source": "Himawari-9 AHI L1b band 13 (10.4 um)", "interpretation": "Brightness temperature of the 10.4 um window band. Equals cloud-top temperature only for opaque cloud; clear sky shows the surface; thin cirrus reads warmer than its top."}
        if not self.sat_ok or cid not in self.sat_idx:
            return {**base, "status": "UNAVAILABLE", "reason": (self.sat_problems[0] if self.sat_problems else "cell not in the satellite artifact")}
        m, i = self.sat_doc["meta"], self.sat_idx[cid]
        scene = parse_utc(m["scene_nominal_utc"])
        age = round((now - scene).total_seconds() / 60.0, 1)
        cur = 0 <= age <= SAT_MAX_AGE_MIN
        d = self.sat_doc
        g = lambda k: d[k][i] if cur else None
        zen = d["view_zenith_deg"][i]
        return {**base, "status": "CURRENT" if cur else "STALE", "scene_nominal_utc": m["scene_nominal_utc"], "age_minutes": age, "max_age_minutes": SAT_MAX_AGE_MIN,
                "ctt_p10_k": g("ctt_p10_k"), "ctt_min_k": g("ctt_min_k"), "cold_frac_235k": g("cold_frac_235k"), "n_pixels": g("n_pixels"),
                "dtdt_30min_k_per_h": g("dtdt_30min_k_per_h"), "dtdt_60min_k_per_h": g("dtdt_60min_k_per_h"), "view_zenith_deg": zen,
                "quality": "COARSE_VIEW_ANGLE" if zen is not None and zen > 70 else "OK",
                "reason": None if cur else "last satellite scan is %.0f min old (current means <= %d min): values withheld, not shown as current" % (age, SAT_MAX_AGE_MIN)}

    def record(self, cid: str, hour: Optional[int] = None, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        if cid not in self.idx:
            raise KeyError(cid)
        rec = {"location": self.location_info(cid), "now_utc": iso(now), "artifact_version": self.version}
        rec["availability"] = {"fields": "AVAILABLE" if self.fields_ok else "UNAVAILABLE",
                               "problems": self.fields_problems[:5] if not self.fields_ok else []}
        rec["static"] = self.static_record(cid)
        rec["satellite"] = self.satellite(cid, now)
        if not self.fields_ok:
            rec["fields"], rec["hazards"] = {}, self.hazards(cid, None, None, now)
            return rec
        init, files = self._hour_meta()
        age_h = (now - init).total_seconds() / 3600.0
        hour = self.default_hour(now) if hour is None else int(hour)
        if not 0 <= hour < len(files):
            raise IndexError(hour)
        col = self.fcol[cid]
        F = self.fields_doc["fields"]; R = self.fields_doc["registry"]
        hours = []
        for k, f in enumerate(files):
            v = parse_utc(f["valid_time_utc"])
            # Operational lead = valid - the time DRIFT actually held the data (recorded per file). Never negative: a valid time
            # that was already past when the data arrived has no lead (None + status), as in the forecast_integrity contract.
            from backend import forecast_integrity as fi
            lead = fi.operational_lead_hours(f["valid_time_utc"], f["acquired_at_utc"])
            hours.append({"index": k, "fhour": f["fhour"], "valid_time_utc": f["valid_time_utc"], "published_at_utc": f["published_at_utc"],
                          "acquired_at_utc": f["acquired_at_utc"], "status": hour_status(now, init, v, parse_utc(f["published_at_utc"])),
                          "forecast_available_time_utc": f["acquired_at_utc"], "operational_lead_hours": lead,
                          "operational_lead_status": "OK" if lead is not None else "NOT_APPLICABLE_PAST_VALID_TIME"})
        rec["cycle"] = {"model_init_utc": iso(init), "age_hours": round(age_h, 2), "source": SOURCE, "resolution_deg": RESOLUTION_DEG,
                        "extraction": self.fields_doc["meta"]["extraction"], "hours": hours,
                        "availability_note": self.fields_doc["meta"]["availability_note"],
                        "operational_lead_hours": hours[hour]["operational_lead_hours"], "operational_lead_status": hours[hour]["operational_lead_status"],
                        "operational_lead_basis": "valid time minus the time DRIFT acquired the NWP file; excludes dissemination to users"}
        rec["selected_hour"] = hours[hour]
        fl = {}
        for name, reg in R.items():
            series = [F[name][k][col] for k in range(len(files))]
            fl[name] = {"value": series[hour], "series": series, "unit": reg["unit"], "kind": reg["kind"], "definition": reg["definition"],
                        "is_observation": False, "is_calibrated_probability": False,
                        "status": hours[hour]["status"] if series[hour] is not None else "MISSING",
                        "safe_as_current": hours[hour]["status"] == "CURRENT" and series[hour] is not None}
        rec["fields"] = fl
        rec["hazards"] = self.hazards(cid, fl, hours, now, hour)
        return rec

    def static_record(self, cid: str) -> dict:
        if not self.static_doc or cid not in self.static_idx:
            return {"status": "UNAVAILABLE"}
        row = self.static_doc["values"][self.static_idx[cid]]
        d = dict(zip(self.static_doc["columns"], row))
        return {"status": "AVAILABLE", "kind": "STATIC_GEOGRAPHY", "source": self.static_doc["meta"]["terrain_source"],
                "gauge_source": self.static_doc["meta"]["gauge_source"], "limits": self.static_doc["meta"]["limits"], **d}

    # ---------------------------------------------------------------- hazards (independent pathways)
    def neighbours(self, cid: str, r: int = 2):
        c = self.cells[self.idx[cid]]
        out = []
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                j = self.by_ll.get((round(c[1] + dy * RESOLUTION_DEG, 2), round(c[2] + dx * RESOLUTION_DEG, 2)))
                if j is not None:
                    out.append(self.cells[j][0])
        return out

    def hazards(self, cid, fl, hours, now, hour=None) -> dict:
        st = self.static_record(cid)
        return {"thunderstorm": self.ts_hazard(cid, fl, hours, hour), "extreme_rainfall": self.rain_hazard(cid, fl, hours, hour),
                "flash_flood": self.ff_hazard(cid, fl, hours, hour, st)}

    def ts_hazard(self, cid, fl, hours, hour):
        h = {"pathway": "thunderstorm", "target": "thunderstorm reported at/near a METAR station within +-1.5 h of valid time (pan-India model, see model block)",
             "indicators": None, "model": {"status": "UNAVAILABLE", "reason": "no evaluated pan-India thunderstorm model artifact"}}
        if fl is None:
            return h
        cape = fl["cape"]["value"]
        band = None if cape is None else next(n for t, n in CAPE_BANDS if cape < t)
        h["indicators"] = {
            "kind": "DERIVED_PHYSICAL (conventional bands; NOT a probability)", "instability_band": band,
            "cape": cape, "cin": fl["cin"]["value"], "lifted_index": fl["lftx"]["value"], "k_index": fl["k_index"]["value"],
            "total_totals": fl["total_totals"]["value"], "pwat_mm": fl["pwat"]["value"], "shear_850_500_ms": fl["shear_850_500"]["value"],
            "vertical_velocity_700_pa_s": fl["w700"]["value"], "cape_series": fl["cape"]["series"],
            "cap_strength": None if fl["cin"]["value"] is None else ("STRONG_CAP" if fl["cin"]["value"] <= -100 else "MODERATE_CAP" if fl["cin"]["value"] <= -25 else "WEAK_CAP")}
        if self.ts_ok and cid in self.ts_idx:
            m = self.ts_doc
            row = m["leads"]
            feats = m["meta"]["features"]
            exp = {str(L): [{"feature": feats[m["explanations"][str(L)][self.ts_idx[cid]][k]], "contribution": m["explanations"][str(L)][self.ts_idx[cid]][k + 1]} for k in (0, 2, 4)]
                   for L in m["meta"]["leads"]}
            h["model"] = {"status": "AVAILABLE_EVALUATED", "model_version": m["meta"]["model_version"], "model_init_utc": m["meta"]["cycle_init_utc"],
                          "value_type": m["meta"]["value_type"], "calibrated": m["meta"]["calibrated"], "leads": {str(L): {"value": row[str(L)][self.ts_idx[cid]], "valid_time_utc": m["meta"]["valid_times"][str(L)]} for L in m["meta"]["leads"]},
                          "evaluation_ref": m["meta"]["evaluation_ref"], "explanation_top3_treeshap": exp,
                          "alert_eligible": False, "alert_note": "automated alerts stay disabled: no recorded DRIFT publication time"}
        return h

    def rain_hazard(self, cid, fl, hours, hour):
        c = self.cells[self.idx[cid]]
        key = (float(math.floor(c[1] + 0.5)), float(math.floor(c[2] + 0.5)))
        cb = self.cb.get(key)
        h = {"pathway": "extreme_rainfall",
             "separation": {"heavy_rain_day": "daily outlook score (1 deg cell, shared by the 0.25 deg cells inside it)",
                            "short_duration_extreme_rainfall": "UNAVAILABLE: no hourly/sub-hourly rainfall labels or inputs are integrated",
                            "cloudburst": "UNAVAILABLE: requires observed >=100 mm/h events; DRIFT has none"},
             "heavy_rain_day_outlook": {"value": cb, "kind": "MODEL_SCORE_UNCALIBRATED", "resolution": "1 deg cell (%s,%s)" % key,
                                        "status": "AVAILABLE" if cb is not None else "UNAVAILABLE", "event_definition": ">=64.5 mm/24 h daily rainfall proxy; not a cloudburst"},
             "short_duration_extreme_rainfall": self.short_duration(cid), "cloudburst": {"status": "UNAVAILABLE"}, "model_guidance": None}
        if fl is None:
            return h
        p3 = fl["precip_3h"]["series"]; cum = fl["apcp_cum"]["series"]
        nb_ids = self.neighbours(cid, 2)
        fcols = [self.fcol[n] for n in nb_ids if n in self.fcol]
        P3 = self.fields_doc["fields"]["precip_3h"]
        extent = []
        for k in range(len(p3)):
            vals = [P3[k][j] for j in fcols if P3[k][j] is not None]
            extent.append({"cells_ge_10mm_3h": sum(1 for v in vals if v >= 10), "cells_ge_25mm_3h": sum(1 for v in vals if v >= 25), "n_cells": len(vals)})
        valid = [x for x in p3 if x is not None]
        total = cum[-1]
        h["model_guidance"] = {
            "kind": "RAW_MODEL_OUTPUT / DERIVED_PHYSICAL (GFS guidance; not observed rain)",
            "precip_3h_series_mm": p3, "accumulated_since_init_mm": cum, "max_3h_mm": max(valid) if valid else None,
            "max_3h_fhour": None if not valid else hours[p3.index(max(valid))]["fhour"],
            "accumulated_24h_mm": total, "imd_24h_category": None if total is None else next(n for t, n in IMD_24H_BANDS if total < t),
            "imd_category_note": "IMD daily-rainfall classes applied to MODEL rainfall over the available window; not an observation",
            "spatial_extent_within_half_degree": extent, "neighbourhood": "5x5 cells (+-0.5 deg), land cells only"}
        return h

    def short_duration(self, cid):
        d = self.rain_doc
        out = {"status": "UNAVAILABLE", "target": "3 h CMORPH rainfall at the 0.25 deg cell >= threshold (short-duration heavy rainfall; NOT cloudburst)",
               "reason": (d or {}).get("meta", {}).get("reason", "no evaluated short-duration rainfall model artifact")}
        if not (self.rain_ok and cid in self.rain_idx):
            return out
        m, i = d["meta"], self.rain_idx[cid]
        feats = m["features"]
        tg = {}
        for k in m["targets"]:
            a = d["explanations"][k][i]
            tg[k] = {"value": d["targets"][k][i], "threshold_mm_per_3h": m["target_info"][k]["threshold_mm_per_3h"], "lead_h": m["target_info"][k]["lead_h"],
                     "valid_time_utc": m["target_info"][k]["valid_time_utc"], "evaluation": m["target_info"][k],
                     "explanation_top3_treeshap": [{"feature": feats[a[j]], "contribution": a[j + 1]} for j in (0, 2, 4)]}
        return {"status": "AVAILABLE_EVALUATED", "target": out["target"], "model_version": m["model_version"], "model_init_utc": m["cycle_init_utc"], "value_type": m["value_type"],
                "calibrated": m["calibrated"], "not_cloudburst": True, "evaluation_ref": m["evaluation_ref"], "limits": m["limits"], "targets": tg}

    def ff_hazard(self, cid, fl, hours, hour, st):
        h = {"pathway": "flash_flood", "status": "SCREENING_ONLY_UNVALIDATED",
             "prediction": {"status": "UNAVAILABLE", "reason": "no validated flash-flood target, labels or hydrological inputs (soil-moisture observations, river levels)"},
             "missing_inputs": ["observed flash-flood event labels with time and location", "river network / catchment hydrology beyond 106 gauges",
                                "observed soil moisture", "live gauge levels", "validated model and evaluation"],
             "context": {"static": {k: st.get(k) for k in ("elev_mean_m", "relief_m", "slope_mean_deg", "slope_p90_deg", "gauge_in_cell", "nearest_gauge_id",
                                                           "nearest_gauge_km", "nearest_gauge_events", "nearest_gauge_rapid_events")} if st.get("status") == "AVAILABLE" else None},
             "screening_index": None}
        if fl is None or st.get("status") != "AVAILABLE":
            return h
        cum = fl["apcp_cum"]["series"]
        p3 = fl["precip_3h"]["series"]
        # 6 h trigger: largest sum of two consecutive 3 h amounts
        pairs = [p3[i] + p3[i + 1] for i in range(len(p3) - 1) if p3[i] is not None and p3[i + 1] is not None]
        r6 = max(pairs) if pairs else None
        soil = fl["soilw0_10"]["value"]
        slope90, relief = st.get("slope_p90_deg"), st.get("relief_m")
        if r6 is not None and slope90 is not None and relief is not None:
            trig = clip(r6 / 50.0)
            terr = clip(0.5 * slope90 / 10.0 + 0.5 * relief / 1500.0)
            wet = None if soil is None else clip((soil - 0.15) / 0.25)
            wet_eff = 0.5 if wet is None else wet
            idx = trig * (0.4 + 0.3 * terr + 0.3 * wet_eff)
            h["screening_index"] = {"value": round(idx, 3), "kind": "HEURISTIC_SCREENING_INDEX (0-1; NOT a probability; not validated)",
                                    "formula": "trigger * (0.4 + 0.3*terrain + 0.3*wetness)",
                                    "components": {"rain_6h_max_mm": r6, "rain_trigger": round(trig, 3), "terrain_factor": round(terr, 3),
                                                   "soil_wetness": None if wet is None else round(wet, 3), "soil_moisture_m3m3": soil},
                                    "notes": ["soil moisture is a GFS land-surface model state, not an observation",
                                              "if soil moisture is missing the wetness term is held at a neutral 0.5 and this is stated",
                                              "terrain uses slope/relief at ~1.2 km; hydrology beyond the nearest gauge is absent"]}
        return h

    # ---------------------------------------------------------------- explanation, alert eligibility, health
    def explain(self, cid: str, hazard: str, lead: Optional[int] = None) -> dict:
        """Why this location scored what it did: the model's top TreeSHAP attributions bound to the ACTUAL feature values.
        Attribution describes the model, not causal meteorology; a missing input is reported as missing, never narrated."""
        from backend.model_features import build_feature_matrix, describe
        if cid not in self.idx:
            raise KeyError(cid)
        out = {"cell_id": cid, "hazard": hazard, "method": "TreeSHAP (XGBoost pred_contribs), top 3 by |contribution|, log-odds scale",
               "caveat": "model attribution, not a causal meteorological explanation", "status": "UNAVAILABLE", "reason": None}
        if hazard == "thunderstorm":
            doc, ok, idx, kind = self.ts_doc, self.ts_ok, getattr(self, "ts_idx", {}), "thunderstorm"
            leads = {str(L): doc["leads"][str(L)] for L in doc["meta"]["leads"]} if ok else {}
        elif hazard == "extreme_rainfall":
            doc, ok, idx, kind = self.rain_doc, self.rain_ok, self.rain_idx, "rainfall"
            leads = doc["targets"] if ok else {}
        else:
            out["reason"] = "flash_flood has no model: only a heuristic screening index exists (see the location record); nothing to attribute"
            return out
        if not ok or cid not in idx or not self.fields_ok:
            out["reason"] = (doc or {}).get("meta", {}).get("reason") or "no evaluated model artifact for this hazard, or its inputs failed verification"
            return out
        keys = list(leads)
        key = keys[0] if lead is None else next((k for k in keys if k == str(lead) or k.endswith(f"_f{lead}")), None)
        if key is None:
            out["reason"] = f"no evaluated model for lead {lead}; available: {keys}"
            return out
        feats = doc["meta"]["features"]
        i = idx[cid]
        exp = doc["explanations"][key][i]
        Lh = int(key.split("_f")[1]) if kind == "rainfall" else int(key)
        row = build_feature_matrix(self.fields_doc, feats, Lh, kind)[self.fcol[cid]]
        contribs = [{"feature": feats[exp[j]], "value": None if row[exp[j]] != row[exp[j]] else float(row[exp[j]]), "contribution": exp[j + 1]} for j in (0, 2, 4)]
        for c in contribs:
            c["statement"] = describe(c["feature"], c["value"], c["contribution"])
        out.update({"status": "AVAILABLE_EVALUATED", "target": key, "lead_h": Lh, "probability": leads[key][i], "model_version": doc["meta"]["model_version"],
                    "model_init_utc": doc["meta"]["cycle_init_utc"], "calibrated": doc["meta"]["calibrated"], "top_contributions": contribs,
                    "missing_inputs": [f for f, v in zip(feats, row) if v != v], "evaluation_ref": doc["meta"]["evaluation_ref"]})
        return out

    def alert_eligibility(self, cid: str, now: Optional[datetime] = None) -> dict:
        """Runs every model output through the ONE alert policy. In production the policy registries are empty and DRIFT records no
        forecast-availability time, so nothing is eligible; the reason codes say exactly why."""
        from backend import alert_policy as ap
        now = now or datetime.now(timezone.utc)
        if cid not in self.idx:
            raise KeyError(cid)
        res = []
        specs = []
        if self.ts_ok and cid in getattr(self, "ts_idx", {}):
            for L in self.ts_doc["meta"]["leads"]:
                specs.append(("TS", f"thunderstorm +{L}h", self.ts_doc["leads"][str(L)][self.ts_idx[cid]], self.ts_doc["meta"]["valid_times"][str(L)], "TS_REPORT_AT_STATION"))
        if self.rain_ok and cid in self.rain_idx:
            for k, info in self.rain_doc["meta"]["target_info"].items():
                specs.append(("CB", f"rainfall {k}", self.rain_doc["targets"][k][self.rain_idx[cid]], info["valid_time_utc"], "HEAVY_RAIN_3H_PROXY"))
        for hz, name, p, valid, ev in specs:
            cand = {"probability": p, "temporal_status": "CURRENT", "value_type": "CALIBRATED_PROBABILITY", "event_class": ev, "lead_specific": True,
                    "valid_time_utc": valid, "generated_at_utc": None, "forecast_available_time_utc": None, "operational_lead_hours": None, "operational_lead_status": "UNAVAILABLE_NO_DRIFT_PUBLICATION_TIME"}
            ok, why = ap.evaluate(cand, hazard=hz, now=now)
            res.append({"output": name, "eligible": ok, "reason_codes": why, "reasons": ap.explain(why)})
        res.append({"output": "flash_flood screening index", "eligible": False, "reason_codes": ["HAZARD_NEVER_ALERTABLE", "VALUE_TYPE_NOT_ALERTABLE"], "reasons": "heuristic, unvalidated screening index: never alertable"})
        res.append({"output": "cloudburst", "eligible": False, "reason_codes": ["NO_MODEL"], "reasons": "cloudburst prediction is unavailable"})
        return {"cell_id": cid, "any_eligible": any(r["eligible"] for r in res), "policy": "production (empty registries)", "evaluated": res,
                "automated_dispatch": "DISABLED"}

    def health(self, now: Optional[datetime] = None, probe: bool = False) -> dict:
        """Dependency-level health computed on the SERVER from timestamps. `probe=True` additionally makes live HEAD requests to the
        upstream sources, proving reachability from this execution environment."""
        now = now or datetime.now(timezone.utc)
        comp = {}
        comp["grid"] = {"status": "OK" if self.cells and 4000 <= len(self.cells) <= 5500 else "FAILED", "n_cells": len(self.cells)}
        comp["static_layer"] = {"status": "OK" if self.static_doc and len(self.static_doc["cells"]) == len(self.cells) else "FAILED"}
        if self.fields_ok:
            m = self.fields_doc["meta"]; init = parse_utc(m["cycle_init_utc"]); age = (now - init).total_seconds() / 3600
            comp["gfs_fields"] = {"status": "OK" if age <= MODEL_MAX_AGE_H else "STALE", "cycle_init_utc": m["cycle_init_utc"], "age_hours": round(age, 2),
                                  "fhours": m["fhours"], "built_at_utc": m["generated_at_utc"], "n_variables": len(self.fields_doc["registry"]),
                                  "last_published_by_source_utc": max(f["published_at_utc"] for f in m["files"])}
        else:
            comp["gfs_fields"] = {"status": "FAILED", "problems": self.fields_problems[:5]}
        for name, doc, ok in (("thunderstorm_model", self.ts_doc, self.ts_ok), ("rainfall_model", self.rain_doc, self.rain_ok)):
            if doc is None:
                comp[name] = {"status": "MISSING"}
            else:
                mm = doc["meta"]; same = self.fields_ok and mm.get("cycle_init_utc") == self.fields_doc["meta"]["cycle_init_utc"]
                comp[name] = {"status": ("OK" if ok and same else "CYCLE_MISMATCH" if ok else "WITHHELD"), "model_evaluated": bool(ok), "cycle_init_utc": mm.get("cycle_init_utc"),
                              "matches_fields_cycle": bool(same), "reason": mm.get("reason")}
        try:
            st = json.load(open(self.dir / "ingest_status.json"))
            comp["ingestion"] = {"status": "OK" if st.get("last_result") == "SUCCESS" else "DEGRADED", "last_attempt_utc": st.get("last_attempt_utc"), "last_success_utc": st.get("last_success_utc"),
                                 "last_result": st.get("last_result"), "last_error": st.get("last_error"), "duration_s": st.get("duration_s"), "n_cells": st.get("n_cells"),
                                 "all_null_variables": st.get("all_null_variables")}
        except Exception:  # noqa: BLE001
            comp["ingestion"] = {"status": "UNKNOWN", "note": "no ingest_status.json"}
        comp["alert_policy"] = {"status": "FAIL_CLOSED", "automated_dispatch": "DISABLED"}
        comp["simulation"] = {"status": "ISOLATED", "note": "synthetic engine runs only in the browser and has no network or notification capability"}
        if probe:
            import urllib.request
            from email.utils import parsedate_to_datetime  # noqa: F401
            probes = {"aws_gfs": "https://noaa-gfs-bdp-pds.s3.amazonaws.com/", "aws_cmorph": "https://noaa-cdr-precip-cmorph-pds.s3.amazonaws.com/", "iem_metar": "https://mesonet.agron.iastate.edu/"}
            comp["upstream_probe"] = {}
            for k, u in probes.items():
                try:
                    r = urllib.request.urlopen(urllib.request.Request(u, method="HEAD", headers={"User-Agent": "DRIFT-health"}), timeout=8)
                    comp["upstream_probe"][k] = {"status": "REACHABLE", "http": r.status}
                except Exception as e:  # noqa: BLE001
                    comp["upstream_probe"][k] = {"status": "UNREACHABLE", "error": type(e).__name__}
        bad = [k for k, v in comp.items() if isinstance(v, dict) and v.get("status") in ("FAILED", "MISSING", "CYCLE_MISMATCH")]
        stale = [k for k, v in comp.items() if isinstance(v, dict) and v.get("status") == "STALE"]
        overall = "UNHEALTHY" if bad else ("DEGRADED" if stale or any(v.get("status") == "WITHHELD" for v in comp.values() if isinstance(v, dict)) else "HEALTHY")
        return {"status": overall, "ready": overall != "UNHEALTHY" and not stale, "checked_at_utc": iso(now), "clock": "server UTC (never the browser clock)", "components": comp,
                "artifact_version": self.version}

    # ---------------------------------------------------------------- regions
    def region(self, state: Optional[str] = None, district: Optional[str] = None, hour: Optional[int] = None, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        sel = []
        for c in self.cells:
            s = self.states[c[4]] if c[4] is not None else None
            d = self.districts[c[5]]["name"] if c[5] is not None else None
            if (state and (s or "").lower() != state.lower()) or (district and (d or "").lower() != district.lower()):
                continue
            sel.append(c[0])
        if not sel:
            raise KeyError("no cells")
        out = {"state": state, "district": district, "n_cells": len(sel), "resolution_deg": RESOLUTION_DEG}
        if not self.fields_ok:
            out["availability"] = "UNAVAILABLE"
            return out
        init, files = self._hour_meta()
        hour = self.default_hour(now) if hour is None else int(hour)
        F = self.fields_doc["fields"]
        cols = [self.fcol[i] for i in sel]
        summ = {}
        for name in ("cape", "cin", "pwat", "t2m_c", "rh2m", "k_index", "shear_850_500", "precip_3h", "soilw0_10"):
            vals = [F[name][hour][j] for j in cols if F[name][hour][j] is not None]
            summ[name] = {"n": len(vals), "min": min(vals), "max": max(vals), "mean": round(sum(vals) / len(vals), 3)} if vals else {"n": 0}
        cape = [F["cape"][hour][j] for j in cols if F["cape"][hour][j] is not None]
        out["selected_hour"] = {"fhour": files[hour]["fhour"], "valid_time_utc": files[hour]["valid_time_utc"], "model_init_utc": iso(init),
                                "status": hour_status(now, init, parse_utc(files[hour]["valid_time_utc"]), parse_utc(files[hour]["published_at_utc"]))}
        out["summary"] = summ
        out["exceedance"] = {"cells_cape_ge_1000": sum(1 for v in cape if v >= 1000), "cells_precip_3h_ge_10mm": sum(1 for j in cols if (F["precip_3h"][hour][j] or 0) >= 10)}
        return out


def clip(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def hour_status(now: datetime, init: datetime, valid: Optional[datetime], published: Optional[datetime]) -> str:
    if valid is None or published is None:
        return "UNAVAILABLE"
    if published < init or valid <= init or published > now + timedelta(minutes=5):
        return "INVALID_PROVENANCE"
    if (now - init).total_seconds() / 3600.0 > MODEL_MAX_AGE_H:
        return "STALE"
    if valid < now - timedelta(hours=PAST_TOLERANCE_H):
        return "PAST_VALID_TIME"
    return "CURRENT"


def verify_fields(doc: dict, grid_doc: Optional[dict], grid_path: Path) -> list:
    """Independent verification of the fields artifact: recomputes everything that can be recomputed; trusts no label."""
    import hashlib
    p = []
    try:
        m = doc["meta"]
        init = parse_utc(m["cycle_init_utc"])
        if init is None or init.hour not in (0, 12):
            p.append("cycle init missing or not a 00/12Z cycle")
        if grid_doc is None:
            p.append("grid missing")
            return p
        if hashlib.sha256(open(grid_path, "rb").read()).hexdigest()[:16] != m.get("grid_sha256"):
            p.append("fields were built on a different grid version")
        if doc["cells"] != [c[0] for c in grid_doc["cells"]]:
            p.append("cell order differs from the grid")
        files = m["files"]
        if [f["fhour"] for f in files] != m["fhours"]:
            p.append("fhours/files mismatch")
        gen = parse_utc(m["generated_at_utc"])
        for f in files:
            v, pub, acq = parse_utc(f["valid_time_utc"]), parse_utc(f["published_at_utc"]), parse_utc(f["acquired_at_utc"])
            if v != init + timedelta(hours=f["fhour"]):
                p.append(f"f{f['fhour']}: valid time != init + fhour")
            if pub is None or pub <= init + timedelta(minutes=30):
                p.append(f"f{f['fhour']}: publication time missing or implausibly early")
            elif acq is not None and acq < pub - timedelta(minutes=1):
                p.append(f"f{f['fhour']}: acquired before it was published")
            if not f.get("messages_verified"):
                p.append(f"f{f['fhour']}: GRIB messages were not verified")
            if not any(w == [0, f["fhour"]] for w in f.get("apcp_windows", [])):
                p.append(f"f{f['fhour']}: no verified 0-{f['fhour']} accumulation window")
        n = len(doc["cells"])
        for k, v in doc["fields"].items():
            if len(v) != len(files) or any(len(h) != n for h in v):
                p.append(f"field {k}: shape mismatch")
        if "cape" in doc["fields"]:
            allv = [x for h in doc["fields"]["cape"] for x in h if x is not None]
            if allv and (min(allv) < 0 or max(allv) > 10000):
                p.append("CAPE outside physical range")
        for h in doc["fields"].get("rh2m", []):
            if any(x is not None and not (0 <= x <= 100.5) for x in h):
                p.append("RH outside 0-100")
                break
        if "soilw0_10" in doc["fields"]:
            allv = [x for h in doc["fields"]["soilw0_10"] for x in h if x is not None]
            if allv and max(allv) > 1.0:
                p.append("soil moisture sentinel leaked into values")
    except (KeyError, TypeError) as e:
        p.append(f"malformed artifact: {e!r}")
    return p
