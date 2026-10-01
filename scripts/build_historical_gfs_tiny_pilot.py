#!/usr/bin/env python3
"""
build_historical_gfs_tiny_pilot.py -- Phase 0.4.5 RESEARCH-ONLY pilot builder.

Builds a TINY real-data pilot (6 representative canonical cells, one 00Z
initialization cycle, using the already-downloaded f003/f006 files) covering
only the features Phase 0.4.4/0.4.5 classified as DIRECT or DERIVE-and-
implemented-this-phase. Does NOT download anything, does NOT train anything,
does NOT touch production code, and does NOT include CTT drop rate (BLOCKED,
per Phase 0.4.4/0.4.5 Part 5).

Reuses this project's existing regrid.py::regrid_to_cell/cell_id_for (not
reimplemented) and the two new research-only helpers from this phase
(historical_gfs_thermodynamics.py, historical_gfs_precipitation.py).

Output: a small JSON file with full provenance per row, written under
processed/historical_gfs_pilot/ (research artifact, not a production data
path).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eccodes as ec  # noqa: E402
import numpy as np  # noqa: E402

from regrid import regrid_to_cell, cell_id_for  # noqa: E402
from historical_gfs_thermodynamics import (  # noqa: E402
    compute_k_index_historical, compute_totals_totals_historical,
)
from historical_gfs_precipitation import (  # noqa: E402
    compute_interval_precipitation, verify_same_initialization_cycle,
)

RAW_DIR = REPO_ROOT / "data" / "external" / "historical_gfs" / "raw"
F003_PATH = RAW_DIR / "gfs.0p25.2020071500.f003.grib2"
F006_PATH = RAW_DIR / "gfs.0p25.2020071500.f006.grib2"
CANONICAL_GRID_PATH = REPO_ROOT / "data" / "pan_india_grid.json"
OUT_DIR = REPO_ROOT / "processed" / "historical_gfs_pilot"
OUT_PATH = OUT_DIR / "phase_0_4_5_tiny_pilot.json"

ARCHIVE_SOURCE = "NCAR_GDEX_d084001"

REPRESENTATIVE_CELLS = {
    "Bengaluru": (13.0, 77.0),
    "Northern India": (32.0, 77.0),
    "Western India": (23.0, 70.0),
    "Northeastern India": (26.0, 94.0),
    "Southern India": (10.0, 77.0),
    "Boundary (SW corner)": (6.0, 68.0),
}

# (shortName, typeOfLevel, level) for every field this pilot needs.
WANTED_FIELDS = [
    ("cape", "surface", 0),
    ("cin", "surface", 0),
    ("pwat", "atmosphereSingleLayer", 0),
    ("u", "isobaricInhPa", 850),
    ("v", "isobaricInhPa", 850),
    ("u", "isobaricInhPa", 200),
    ("v", "isobaricInhPa", 200),
    ("t", "isobaricInhPa", 850),
    ("t", "isobaricInhPa", 700),
    ("t", "isobaricInhPa", 500),
    ("r", "isobaricInhPa", 850),
    ("r", "isobaricInhPa", 700),
    ("tp", "surface", None),  # accum field; level not meaningful, filtered by stepType below
]


def enumerate_messages(path: Path):
    msgs = []
    with open(path, "rb") as f:
        while True:
            gid = ec.codes_grib_new_from_file(f)
            if gid is None:
                break

            def g(key, default=None):
                try:
                    return ec.codes_get(gid, key)
                except Exception:
                    return default

            msgs.append({
                "gid": gid, "shortName": g("shortName"), "typeOfLevel": g("typeOfLevel"),
                "level": g("level"), "units": g("units"), "stepType": g("stepType"),
                "startStep": g("startStep"), "endStep": g("endStep"),
                "dataDate": g("dataDate"), "dataTime": g("dataTime"),
                "forecastTime": g("forecastTime"),
                "validityDate": g("validityDate"), "validityTime": g("validityTime"),
            })
    return msgs


def find_message(msgs, shortName, typeOfLevel, level=None, stepType=None):
    for m in msgs:
        if m["shortName"] != shortName or m["typeOfLevel"] != typeOfLevel:
            continue
        if level is not None and m["level"] != level:
            continue
        if stepType is not None and m["stepType"] != stepType:
            continue
        return m
    return None


def extract_bbox_values(gid, lat_c, lon_c, pad=2.0):
    lats = np.array(ec.codes_get_array(gid, "latitudes"))
    lons = np.array(ec.codes_get_array(gid, "longitudes"))
    values = np.array(ec.codes_get_array(gid, "values"))
    mask = (
        (lats >= lat_c - pad) & (lats <= lat_c + pad) &
        (lons >= lon_c - pad) & (lons <= lon_c + pad)
    )
    return lats[mask], lons[mask], values[mask]


def regrid_point_value(gid, lat_c, lon_c, source_timestamp):
    lats, lons, values = extract_bbox_values(gid, lat_c, lon_c)
    if len(lats) == 0:
        return None
    uniq_lats = np.array(sorted(set(lats.tolist()), reverse=True))
    uniq_lons = np.array(sorted(set(lons.tolist())))
    grid2d = np.full((len(uniq_lats), len(uniq_lons)), np.nan)
    lat_idx = {v: i for i, v in enumerate(uniq_lats)}
    lon_idx = {v: i for i, v in enumerate(uniq_lons)}
    for a, b, v in zip(lats, lons, values):
        grid2d[lat_idx[a], lon_idx[b]] = v
    res = regrid_to_cell(
        "gfs", uniq_lats.tolist(), uniq_lons.tolist(), grid2d.tolist(),
        target_lat=lat_c, target_lon=lon_c, source_timestamp=source_timestamp,
        source_resolution_deg=0.25, target_resolution_deg=1.0, method="nearest",
    )
    return res


def main():
    if not F003_PATH.exists() or not F006_PATH.exists():
        print(json.dumps({"status": "MISSING_PILOT_FILES", "f003": str(F003_PATH), "f006": str(F006_PATH)}, indent=2))
        sys.exit(0)

    cells_doc = json.loads(CANONICAL_GRID_PATH.read_text())
    canonical_cells = {(c["lat"], c["lon"]) for c in cells_doc["grid_cells"]}

    msgs003 = enumerate_messages(F003_PATH)
    msgs006 = enumerate_messages(F006_PATH)

    init003 = f"{msgs003[0]['dataDate']} {msgs003[0]['dataTime']:04d}"
    init006 = f"{msgs006[0]['dataDate']} {msgs006[0]['dataTime']:04d}"
    valid003 = f"{msgs003[0]['validityDate']} {msgs003[0]['validityTime']:04d}"
    valid006 = f"{msgs006[0]['validityDate']} {msgs006[0]['validityTime']:04d}"
    lead003 = msgs003[0]["forecastTime"]
    lead006 = msgs006[0]["forecastTime"]

    same_cycle = verify_same_initialization_cycle(init003, init006)

    rows = []
    seen_keys = set()

    for name, (lat, lon) in REPRESENTATIVE_CELLS.items():
        in_canonical = (lat, lon) in canonical_cells
        cell_id = cell_id_for(lat, lon)

        row_key = (cell_id, init003, lead003)
        assert row_key not in seen_keys, f"duplicate cell/time/lead row: {row_key}"
        seen_keys.add(row_key)

        # --- DIRECT fields (f003) ---
        direct_vals = {}
        for shortName, typeOfLevel, level in WANTED_FIELDS:
            if shortName == "tp":
                continue  # handled separately (needs both f003 and f006)
            m = find_message(msgs003, shortName, typeOfLevel, level)
            if m is None:
                direct_vals[f"{shortName}_{typeOfLevel}_{level}"] = {"value": None, "missing": True, "reason": "not found in f003"}
                continue
            res = regrid_point_value(m["gid"], lat, lon, valid003)
            direct_vals[f"{shortName}_{typeOfLevel}_{level}"] = (
                {"value": None, "missing": True, "reason": "cell outside extracted window"} if res is None
                else res.to_json()
            )

        def val(key):
            v = direct_vals.get(key, {})
            return v.get("value")

        cape = val("cape_surface_0")
        cin = val("cin_surface_0")
        pwat = val("pwat_atmosphereSingleLayer_0")
        u850 = val("u_isobaricInhPa_850")
        v850 = val("v_isobaricInhPa_850")
        u200 = val("u_isobaricInhPa_200")
        v200 = val("v_isobaricInhPa_200")
        t850 = val("t_isobaricInhPa_850")
        t700 = val("t_isobaricInhPa_700")
        t500 = val("t_isobaricInhPa_500")
        rh850 = val("r_isobaricInhPa_850")
        rh700 = val("r_isobaricInhPa_700")

        # --- Derived kinematic: 850-200 hPa wind shear (same formula as
        # backend/pipeline.py::compute_wind_shear) ---
        shear_ms = None
        if None not in (u850, v850, u200, v200):
            shear_ms = round(((u200 - u850) ** 2 + (v200 - v850) ** 2) ** 0.5, 2)

        # --- Derived thermodynamic: K-Index / Totals-Totals (research helper) ---
        ki_res = compute_k_index_historical(t850, rh850, t700, rh700, t500)
        tt_res = compute_totals_totals_historical(t850, rh850, t500)

        # --- Precipitation: tp(f006) - tp(f003) interval ---
        tp003_msg = find_message(msgs003, "tp", "surface", stepType="accum")
        tp006_msg = find_message(msgs006, "tp", "surface", stepType="accum")
        tp003_res = regrid_point_value(tp003_msg["gid"], lat, lon, valid003) if tp003_msg else None
        tp006_res = regrid_point_value(tp006_msg["gid"], lat, lon, valid006) if tp006_msg else None
        tp003_val = tp003_res.value if tp003_res else None
        tp006_val = tp006_res.value if tp006_res else None

        precip_res = None
        if same_cycle and tp003_msg is not None and tp006_msg is not None:
            precip_res = compute_interval_precipitation(
                tp_start_mm=tp003_val, tp_end_mm=tp006_val,
                init_time=init003, start_valid_time=valid003, end_valid_time=valid006,
                lead_start_hours=lead003, lead_end_hours=lead006,
                start_step=tp003_msg["startStep"], end_start_step=tp006_msg["startStep"],
            )

        row = {
            "region_label": name,
            "cell_id": cell_id,
            "latitude": lat,
            "longitude": lon,
            "in_canonical_992_grid": in_canonical,
            "initialization_time": init003,
            "valid_time": valid003,
            "forecast_lead_hours": lead003,
            "source": "gfs",
            "archive_source": ARCHIVE_SOURCE,
            "source_file": F003_PATH.name,
            "source_valid_time": valid003,
            # --- DIRECT features ---
            "cape": cape, "cin": cin, "pwat_mm": pwat,
            "u850": u850, "v850": v850, "u200": u200, "v200": v200,
            "wind_shear_850_200_ms": shear_ms,
            "t850_k": t850, "t700_k": t700, "t500_k": t500,
            "rh850_pct": rh850, "rh700_pct": rh700,
            # --- DERIVED features (full provenance objects, not bare values) ---
            "k_index_historical": ki_res.to_json(),
            "totals_totals_historical": tt_res.to_json(),
            # --- Precipitation reconstruction (full provenance object) ---
            "gfs_precip_3h_interval_mm": precip_res.to_json() if precip_res else {
                "available": False, "rejected": True,
                "rejection_reason": "same_cycle check failed or tp message missing",
            },
            "missing_field_detail": {k: v for k, v in direct_vals.items() if v.get("missing")},
        }
        rows.append(row)

    for fname, msgs in [(F003_PATH.name, msgs003), (F006_PATH.name, msgs006)]:
        for m in msgs:
            try:
                ec.codes_release(m["gid"])
            except Exception:
                pass

    out_doc = {
        "status": "BUILT_FROM_ACTUAL_FILES",
        "phase": "0.4.5",
        "note": "Tiny real-data pilot. Does NOT include ctt_c, ctt_drop_rate (BLOCKED), "
                "or the production hazard-probability formulas. Research-only artifact, "
                "not a production data path, not used for training in this phase.",
        "initialization_time": init003,
        "cycle_consistency_check": {"same_initialization_cycle": same_cycle, "init003": init003, "init006": init006},
        "n_rows": len(rows),
        "n_unique_cell_time_lead_keys": len(seen_keys),
        "rows": rows,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out_doc, indent=2, default=str))
    print(json.dumps({"status": "BUILT_FROM_ACTUAL_FILES", "out_path": str(OUT_PATH), "n_rows": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
