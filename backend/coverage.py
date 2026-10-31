"""Coverage matrix: which data and which hazard pathways genuinely exist, by state, derived from the loaded artifacts."""
from __future__ import annotations

from collections import defaultdict


def coverage_matrix(s) -> dict:
    by = defaultdict(lambda: {"cells": 0, "terrain": 0, "gauge_within_100km": 0, "gfs_fields": 0, "soil_moisture": 0, "heavy_rain_day_outlook": 0, "ts_model": 0, "short_duration_rain_model": 0})
    for i, c in enumerate(s.cells):
        st = s.states[c[4]] if c[4] is not None else "(no state)"
        row = by[st]; row["cells"] += 1
        sr = s.static_doc["values"][s.static_idx[c[0]]] if s.static_doc and c[0] in s.static_idx else None
        if sr:
            cols = s.static_doc["columns"]
            row["terrain"] += sr[cols.index("elev_mean_m")] is not None
            row["gauge_within_100km"] += sr[cols.index("nearest_gauge_id")] is not None
        if s.fields_ok and c[0] in s.fcol:
            row["gfs_fields"] += 1
            sm = s.fields_doc["fields"]["soilw0_10"][0][s.fcol[c[0]]]
            row["soil_moisture"] += sm is not None
        import math
        row["heavy_rain_day_outlook"] += (float(math.floor(c[1] + 0.5)), float(math.floor(c[2] + 0.5))) in s.cb
        row["ts_model"] += bool(s.ts_ok and c[0] in getattr(s, "ts_idx", {}))
        row["short_duration_rain_model"] += bool(s.rain_ok and c[0] in s.rain_idx)
    hazards = {
        "thunderstorm": {"inputs": "GFS CAPE/CIN/LI/K/TT/PWAT/shear/omega at 0.25 deg", "model": ("pan-India METAR-trained model, leads %s h (evaluated: temporal + leave-region-out)" % s.ts_doc["meta"]["leads"]) if s.ts_ok else "UNAVAILABLE (no evaluated model artifact)",
                         "lead_specific": bool(s.ts_ok), "validated_alerting": False},
        "extreme_rainfall": {"inputs": "GFS precipitation (verified accumulation windows), 3 h amounts, neighbourhood extent",
                             "heavy_rain_day": "panindia_cb_v1 daily outlook (1 deg, uncalibrated)",
                             "short_duration_heavy_rainfall": ("CMORPH-labelled 3 h model, targets %s (evaluated)" % s.rain_doc["meta"]["targets"]) if s.rain_ok else "UNAVAILABLE",
                             "short_duration_extreme_rainfall": "UNAVAILABLE", "cloudburst": "UNAVAILABLE", "lead_specific": bool(s.rain_ok), "validated_alerting": False},
        "flash_flood": {"inputs": "GFS precipitation + GFS soil-moisture model state + SRTM slope/relief + 106 gauges (historical events)",
                        "prediction": "UNAVAILABLE", "screening_index": "heuristic, unvalidated", "lead_specific": False, "validated_alerting": False}}
    return {"n_cells": len(s.cells), "by_state": dict(sorted(by.items())), "hazards": hazards}
