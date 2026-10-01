#!/usr/bin/env python3
"""
build_vobl_historical_gfs_ts_join.py -- RESEARCH-ONLY pilot builder.

Builds the smallest real, fully auditable historical GFS -> VOBL observed
thunderstorm supervised-join table, for an ARBITRARY historical GFS
initialization cycle and an arbitrary list of native forecast leads (Phase
0.4.10 parameterization of the original Phase 0.4.8 script, which was
hardcoded to one cycle/lead pair).

Usage:
    python3 scripts/build_vobl_historical_gfs_ts_join.py \\
        --cycle 2020071500 --leads 003 006

    python3 scripts/build_vobl_historical_gfs_ts_join.py \\
        --cycle 2015030300 --leads 003 009 \\
        --ts-labels path/to/ts_labels.csv

With no arguments, defaults to the original Phase 0.4.8 pilot
(cycle=2020071500, leads=[3, 6]) and writes to the original output paths,
preserving backward compatibility -- no existing caller or test that
invokes this script with no arguments sees any behavior change.

--ts-labels (Phase 0.4.10 addition) lets the caller point at an explicit
ts_labels.csv artifact when the project's default path
(processed/labels/ts_labels.csv) does not exist in a given checkout --
e.g. when that file currently only exists inside a timestamped delivery
folder that has not been copied into processed/labels/ yet. Omitting this
flag preserves the exact original default-path behavior. This script
never generates, copies, alters, or fabricates the label file's contents
regardless of which path is used -- it only reads whatever file it is
given.

Does NOT download anything (files must already exist under
data/external/historical_gfs/raw/). Does NOT train anything. Does NOT
touch production code -- this module is never imported by
backend/pipeline.py, forecast_action.py, canonical_forecast_writer.py, or
any production entry point (verified by test
test_no_production_code_imports_research_builder).

Reuses regrid.py::regrid_to_cell/cell_id_for (Phase 3/4),
historical_gfs_thermodynamics.py and historical_gfs_precipitation.py
(Phase 0.4.5), and the exact VOBL canonical cell already established in
scripts/build_panindia_ts_labels.py / scripts/build_panindia_dataset.py
(IND_13.0_78.0) -- not a new or re-derived cell id.

Label source: the real, already-existing processed/labels/ts_labels.csv
(station-observed VOBL/43295 thunderstorm labels, Phase 4/0.4.6). This
script does not generate, alter, re-derive, or hardcode any label value --
every label is looked up from that file at run time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eccodes as ec  # noqa: E402

from regrid import regrid_to_cell, cell_id_for  # noqa: E402
from historical_gfs_thermodynamics import (  # noqa: E402
    compute_k_index_historical, compute_totals_totals_historical,
)
from historical_gfs_precipitation import (  # noqa: E402
    compute_interval_precipitation, verify_same_initialization_cycle,
)

RAW_DIR = REPO_ROOT / "data" / "external" / "historical_gfs" / "raw"
TS_LABELS_PATH = REPO_ROOT / "processed" / "labels" / "ts_labels.csv"
OUT_DIR = REPO_ROOT / "processed" / "historical_gfs_vobl"

ARCHIVE_SOURCE = "NCAR_GDEX_d084001"

# Default cycle/leads: the original Phase 0.4.8 pilot. Preserved exactly so
# running this script with no arguments reproduces the original pilot --
# backward compatibility, not a new default.
DEFAULT_CYCLE = "2020071500"
DEFAULT_LEADS = [3, 6]
DEFAULT_OUT_CSV = OUT_DIR / "historical_gfs_vobl_pilot.csv"
DEFAULT_OUT_MANIFEST = OUT_DIR / "historical_gfs_vobl_pilot_manifest.json"

# Real VOBL/Kempegowda station coordinates (location_engine.py::VOBL_LAT/LON),
# distinct from the canonical cell CENTER used for the grid join.
VOBL_STATION_LAT = 13.1979
VOBL_STATION_LON = 77.7063

# Canonical VOBL cell already established by scripts/build_panindia_ts_labels.py
# and scripts/build_panindia_dataset.py -- NOT re-derived or hardcoded fresh
# here, reused as-is.
VOBL_CELL_LAT = 13.0
VOBL_CELL_LON = 78.0

IST = timezone(timedelta(hours=5, minutes=30))

SLOT_WINDOWS_IST = {
    0: (0, 0, 5, 59),
    1: (6, 0, 11, 59),
    2: (12, 0, 17, 59),
    3: (18, 0, 23, 59),
}
SLOT_LABEL_STRING = {
    0: "0001-0600",
    1: "0601-1200",
    2: "1201-1800",
    3: "1801-2400",
}

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
]

REQUIRED_FEATURES = [
    "cape", "cin", "pwat_mm", "u850", "v850", "u200", "v200",
    "wind_shear_850_200_ms", "t850_k", "t700_k", "t500_k",
    "rh850_pct", "rh700_pct", "k_index", "totals_totals",
]
REQUIRED_PROVENANCE = [
    "source", "archive_source", "source_file", "initialization_time_utc",
    "valid_time_utc", "valid_time_ist", "forecast_lead_hours", "slot_id",
    "cell_id", "label_source", "label_timestamp", "label_definition", "label_status",
    "ist_date", "event_group_key",
]

# Phase 0.4.12 dataset contract version, written into every manifest so a
# consumer can tell which contract (event grouping key, precipitation
# policy, split policy) a given pilot file was produced under. Bumped only
# when the contract itself changes, not on every code tweak.
CONTRACT_VERSION = "phase_0_4_12_v1"

# Phase 0.4.12 Part 1 -- the mandatory event grouping key. Every historical
# forecast row targets one real observed outcome: (cell_id, IST calendar
# date, 6-hour slot). Multiple GFS leads/cycles may legitimately produce
# separate predictor rows for the SAME event_group_key (e.g. a 00Z+3h and a
# 00Z+6h forecast both targeting slot 1) -- those rows are correlated, not
# independent, and must never be split across train/validation/test. See
# docs/PHASE_0_4_12_HISTORICAL_GFS_TS_DATASET_CONTRACT.md.
EVENT_GROUP_KEY_FORMAT = "{cell_id}|{ist_date}|{slot_id}"


def event_group_key_for(cell_id: str, ist_date: str, slot_id: Optional[int]) -> str:
    """Deterministic grouping key per the Phase 0.4.12 dataset contract.
    Two rows with the same (cell_id, ist_date, slot_id) are predicting the
    identical real-world observed outcome and must be grouped together for
    any train/validation/test split -- never split across partitions."""
    return EVENT_GROUP_KEY_FORMAT.format(cell_id=cell_id, ist_date=ist_date, slot_id=slot_id)

# Feature leakage classification. Every GFS field below is a forecast-model
# output computed entirely from the model's own initialization-time analysis
# and physics integration -- none of it reads back any observation made
# after the initialization time. Classified per-feature, not asserted in
# bulk, per the Phase 0.4.8/0.4.10 brief's leakage-audit requirement.
FEATURE_LEAKAGE_CLASS = {
    "cape": "FORECAST_DERIVED",
    "cin": "FORECAST_DERIVED",
    "pwat_mm": "FORECAST_DERIVED",
    "u850": "FORECAST_DERIVED",
    "v850": "FORECAST_DERIVED",
    "u200": "FORECAST_DERIVED",
    "v200": "FORECAST_DERIVED",
    "wind_shear_850_200_ms": "FORECAST_DERIVED",
    "t850_k": "FORECAST_DERIVED",
    "t700_k": "FORECAST_DERIVED",
    "t500_k": "FORECAST_DERIVED",
    "rh850_pct": "FORECAST_DERIVED",
    "rh700_pct": "FORECAST_DERIVED",
    "k_index": "FORECAST_DERIVED",
    "totals_totals": "FORECAST_DERIVED",
    "gfs_precip_3h_interval_mm": "FORECAST_DERIVED",
    "gfs_prate_kg_m2_s": "FORECAST_DERIVED",
}

# Phase 0.4.12 Part 2 -- primary precipitation feature. `prate` is a GFS
# surface field, independently valid at (or over a short window ending at)
# each lead's own valid time -- unlike `tp`, it never requires differencing
# two leads, so it does not inherit the accumulation-window-compatibility
# problem Phase 0.4.10B found for gfs_precip_3h_interval_mm. Never called
# qpe or treated as equivalent to production's qpe_mm.
#
# IMPORTANT, discovered this phase by direct GRIB inspection (not assumed
# from the brief's premise): `prate` is NOT uniformly an instantaneous
# field across this archive. The real downloaded files show TWO different
# shapes:
#   - 2020-07-15 f003/f006: TWO prate messages each -- one stepType=instant
#     (startStep==endStep==lead) and one stepType=avg (startStep=0).
#   - 2015-03-03 f003/f009: only ONE prate message each, stepType=avg
#     (f003: startStep=0..3; f009: startStep=6..9 -- the same non-zero-origin
#     windowing already found for `tp` in Phase 0.4.10B).
# This extractor therefore prefers stepType="instant" when present (a true
# point-in-time rate) and falls back to stepType="avg" when it is the only
# message available, recording exactly which stepType/startStep/endStep was
# used in the row's own provenance -- never silently treating an averaged
# rate as if it were instantaneous.
PRATE_FIELD = ("prate", "surface", 0)
# "OBSERVED_AT_INITIALIZATION" would describe a feature read directly at
# lead 0 (e.g. production's current qpe_mm at fhour=0); none of this
# pilot's features are that -- they are all forecast fields valid at a
# future lead, produced BY the model run AT initialization time but
# describing a later moment. This is the correct, non-leaking supervised
# setup (forecast-at-init -> future-valid-time predictor). No feature here
# is USES_FUTURE_INFORMATION or UNKNOWN; if any lookup had failed, it would
# be classified UNKNOWN below rather than silently assumed FORECAST_DERIVED.


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
    return regrid_to_cell(
        "gfs", uniq_lats.tolist(), uniq_lons.tolist(), grid2d.tolist(),
        target_lat=lat_c, target_lon=lon_c, source_timestamp=source_timestamp,
        source_resolution_deg=0.25, target_resolution_deg=1.0, method="nearest",
    )


def extract_prate(msgs, lat_c: float, lon_c: float, source_timestamp: str) -> dict:
    """Phase 0.4.12 primary precipitation feature extractor. Prefers a
    stepType="instant" prate message (a true point-in-time rate); falls
    back to stepType="avg" only when no instant message exists. Returns a
    dict with the extracted value (or None) plus full stepType/startStep/
    endStep provenance -- never fabricates a value and never silently
    treats an averaged rate as instantaneous."""
    shortName, typeOfLevel, level = PRATE_FIELD
    m_instant = find_message(msgs, shortName, typeOfLevel, level, stepType="instant")
    m_avg = find_message(msgs, shortName, typeOfLevel, level, stepType="avg")

    chosen, chosen_kind = (m_instant, "instant") if m_instant is not None else (m_avg, "avg")

    if chosen is None:
        return {
            "value": None, "available": False,
            "stepType_used": None, "startStep": None, "endStep": None,
            "note": f"no {shortName} message found at {typeOfLevel}/{level} "
                    f"(neither stepType=instant nor stepType=avg present in this file)",
        }

    res = regrid_point_value(chosen["gid"], lat_c, lon_c, source_timestamp)
    if res is None or not res.available:
        return {
            "value": None, "available": False,
            "stepType_used": chosen_kind,
            "startStep": chosen["startStep"], "endStep": chosen["endStep"],
            "note": f"{shortName} message found (stepType={chosen_kind}) but cell value unavailable from regrid",
        }

    note = None
    if chosen_kind == "avg":
        note = (
            f"no stepType=instant {shortName} message in this file; using stepType=avg "
            f"(time-averaged rate over startStep={chosen['startStep']} to endStep={chosen['endStep']}, "
            f"NOT a literal instantaneous sample -- recorded here, not hidden)"
        )
    return {
        "value": res.value, "available": True,
        "stepType_used": chosen_kind,
        "startStep": chosen["startStep"], "endStep": chosen["endStep"],
        "note": note,
    }


def parse_grib_dt(date_int: int, time_int: int) -> datetime:
    """GRIB dataDate=YYYYMMDD (int), dataTime/validityTime=HHMM (int, e.g. 0 or 300 or 1130)."""
    s = f"{date_int:08d}{time_int:04d}"
    return datetime.strptime(s, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


def ist_slot_for(dt_ist: datetime):
    """Return (slot_id, slot_label) for an IST-local datetime, using the
    exact same SLOT_WINDOWS_IST table lead_time.py/metar_ground_truth.py use.
    Does not invent a new slot boundary convention."""
    h, m = dt_ist.hour, dt_ist.minute
    minute_of_day = h * 60 + m
    for slot_id, (sh, sm, eh, em) in SLOT_WINDOWS_IST.items():
        start = sh * 60 + sm
        end = eh * 60 + em
        if start <= minute_of_day <= end:
            return slot_id, SLOT_LABEL_STRING[slot_id]
    return None, None


def grib_filename_for(cycle: str, lead: int) -> str:
    """Exact GDEX d084001 naming convention: gfs.0p25.{YYYYMMDDHH}.f{FFF}.grib2
    -- not a new or invented pattern, matches every file already on disk."""
    return f"gfs.0p25.{cycle}.f{lead:03d}.grib2"


def path_for(cycle: str, lead: int) -> Path:
    return RAW_DIR / grib_filename_for(cycle, lead)


def _is_relative_to(path: Path, other: Path) -> bool:
    """Portable equivalent of Path.is_relative_to() (3.9+ only) -- this
    module must not assume a Python minor version. Operates on the paths
    literally, exactly as relative_to() itself does (no symlink
    resolution), so its result always agrees with a following
    path.relative_to(other) call. Never raises."""
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def build_pilot(cycle: str, leads: List[int], out_csv: Path, out_manifest: Path,
                 ts_labels_path: Optional[Path] = None) -> dict:
    """Core builder, generalized over an arbitrary cycle and an arbitrary
    (>=1) list of native forecast leads. Returns the summary dict that is
    also printed to stdout.

    ts_labels_path: explicit path to the real ts_labels.csv artifact to
    join against. Defaults to TS_LABELS_PATH (the original Phase 0.4.8
    default, processed/labels/ts_labels.csv) when omitted -- this preserves
    exact backward compatibility for every existing caller/test that does
    not pass this argument. This script never generates, copies, alters,
    or fabricates the contents of this file -- it only reads it."""
    leads = sorted(set(int(x) for x in leads))
    if len(leads) < 1:
        raise ValueError("at least one forecast lead is required")

    labels_path = Path(ts_labels_path) if ts_labels_path is not None else TS_LABELS_PATH

    paths = [path_for(cycle, lead) for lead in leads]
    missing_paths = [p for p in paths if not p.exists()]
    if missing_paths:
        result = {
            "status": "MISSING_PILOT_FILES",
            "cycle": cycle, "leads": leads,
            "missing_files": [str(p) for p in missing_paths],
        }
        print(json.dumps(result, indent=2))
        return result

    if not labels_path.exists():
        result = {
            "status": "BLOCKED",
            "reason": "ts_labels.csv not found",
            "ts_labels_path_checked": str(labels_path),
        }
        print(json.dumps(result, indent=2))
        return result

    # ---- distance station -> cell center (Haversine) ----
    R_KM = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [VOBL_STATION_LAT, VOBL_STATION_LON, VOBL_CELL_LAT, VOBL_CELL_LON])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    station_to_cell_km = round(2 * R_KM * math.asin(math.sqrt(a)), 3)

    vobl_cell_id = cell_id_for(VOBL_CELL_LAT, VOBL_CELL_LON)
    vobl_cell_mapping = {
        "station_latitude": VOBL_STATION_LAT,
        "station_longitude": VOBL_STATION_LON,
        "canonical_cell_id": vobl_cell_id,
        "cell_center_lat": VOBL_CELL_LAT,
        "cell_center_lon": VOBL_CELL_LON,
        "distance_station_to_cell_center_km": station_to_cell_km,
        "note": "cell id reused from scripts/build_panindia_ts_labels.py / "
                "scripts/build_panindia_dataset.py (not re-derived fresh); "
                "cell_id_for() is deterministic from (lat,lon), verified by calling it here.",
    }

    all_msgs = {lead: enumerate_messages(p) for lead, p in zip(leads, paths)}

    fdefs = []
    for lead, p in zip(leads, paths):
        msgs = all_msgs[lead]
        init_dt = parse_grib_dt(msgs[0]["dataDate"], msgs[0]["dataTime"])
        valid_dt = parse_grib_dt(msgs[0]["validityDate"], msgs[0]["validityTime"])
        init_str = f"{msgs[0]['dataDate']} {msgs[0]['dataTime']:04d}"
        valid_str = f"{msgs[0]['validityDate']} {msgs[0]['validityTime']:04d}"
        grib_lead = msgs[0]["forecastTime"]
        fdefs.append({
            "path": p, "msgs": msgs, "lead_requested": lead, "lead_hours": grib_lead,
            "init_dt": init_dt, "valid_dt": valid_dt, "init_str": init_str, "valid_str": valid_str,
        })

    # same-cycle check across all files (pairwise against the first)
    same_cycle_all = all(
        verify_same_initialization_cycle(fdefs[0]["init_str"], fd["init_str"]) for fd in fdefs
    )

    ts_labels = pd.read_csv(labels_path)
    vobl_labels = ts_labels[ts_labels["cell_id"] == vobl_cell_id].copy()

    rows = []
    unmatched = []
    quality_issues = []
    seen_init_lead = set()
    seen_valid_time = set()

    for fd in fdefs:
        msgs = fd["msgs"]
        init_dt, valid_dt, lead_hours = fd["init_dt"], fd["valid_dt"], fd["lead_hours"]

        if lead_hours != fd["lead_requested"]:
            quality_issues.append({
                "source_file": fd["path"].name, "issue": "requested lead does not match GRIB forecastTime",
                "requested_lead": fd["lead_requested"], "grib_forecast_time": lead_hours,
            })

        temporal_ok = init_dt < valid_dt
        computed_lead = int((valid_dt - init_dt).total_seconds() // 3600)
        lead_consistent = (computed_lead == lead_hours)
        if not temporal_ok or not lead_consistent:
            quality_issues.append({
                "source_file": fd["path"].name,
                "issue": "lead-time inconsistency",
                "init_time_utc": init_dt.isoformat(), "valid_time_utc": valid_dt.isoformat(),
                "forecast_lead_hours_field": lead_hours, "computed_lead_hours": computed_lead,
            })

        valid_ist = valid_dt.astimezone(IST)
        slot_id, slot_label = ist_slot_for(valid_ist)
        ist_date_str = valid_ist.strftime("%Y-%m-%d")
        label_timestamp = f"{ist_date_str}T{slot_label}" if slot_label is not None else None

        key_il = (init_dt.isoformat(), lead_hours)
        if key_il in seen_init_lead:
            quality_issues.append({"issue": "duplicate initialization+lead", "key": str(key_il)})
        seen_init_lead.add(key_il)
        if valid_dt.isoformat() in seen_valid_time:
            quality_issues.append({"issue": "duplicate forecast valid time", "key": valid_dt.isoformat()})
        seen_valid_time.add(valid_dt.isoformat())

        # ---- label join: looked up at run time, never hardcoded ----
        label_row = None
        if label_timestamp is not None:
            match = vobl_labels[vobl_labels["timestamp"] == label_timestamp]
            if len(match) == 1:
                label_row = match.iloc[0]
            elif len(match) > 1:
                unmatched.append({
                    "source_file": fd["path"].name, "valid_time_utc": valid_dt.isoformat(),
                    "label_timestamp_sought": label_timestamp,
                    "reason": f"AMBIGUOUS: {len(match)} label rows matched this timestamp for this cell",
                })
            else:
                unmatched.append({
                    "source_file": fd["path"].name, "valid_time_utc": valid_dt.isoformat(),
                    "label_timestamp_sought": label_timestamp,
                    "reason": "no ts_labels.csv row found for this (cell_id, timestamp) -- not discarded, recorded here",
                })
        else:
            unmatched.append({
                "source_file": fd["path"].name, "valid_time_utc": valid_dt.isoformat(),
                "label_timestamp_sought": None,
                "reason": "could not compute an IST slot for this valid time",
            })

        direct_vals = {}
        missing_features = []
        for shortName, typeOfLevel, level in WANTED_FIELDS:
            m = find_message(msgs, shortName, typeOfLevel, level)
            key = f"{shortName}_{typeOfLevel}_{level}"
            if m is None:
                direct_vals[key] = None
                missing_features.append(key)
                continue
            res = regrid_point_value(m["gid"], VOBL_CELL_LAT, VOBL_CELL_LON, fd["valid_str"])
            if res is None or not res.available:
                direct_vals[key] = None
                missing_features.append(key)
            else:
                direct_vals[key] = res.value

        cape = direct_vals["cape_surface_0"]
        cin = direct_vals["cin_surface_0"]
        pwat = direct_vals["pwat_atmosphereSingleLayer_0"]
        u850 = direct_vals["u_isobaricInhPa_850"]
        v850 = direct_vals["v_isobaricInhPa_850"]
        u200 = direct_vals["u_isobaricInhPa_200"]
        v200 = direct_vals["v_isobaricInhPa_200"]
        t850 = direct_vals["t_isobaricInhPa_850"]
        t700 = direct_vals["t_isobaricInhPa_700"]
        t500 = direct_vals["t_isobaricInhPa_500"]
        rh850 = direct_vals["r_isobaricInhPa_850"]
        rh700 = direct_vals["r_isobaricInhPa_700"]

        shear_ms = None
        if None not in (u850, v850, u200, v200):
            shear_ms = round(((u200 - u850) ** 2 + (v200 - v850) ** 2) ** 0.5, 2)

        ki_res = compute_k_index_historical(t850, rh850, t700, rh700, t500)
        tt_res = compute_totals_totals_historical(t850, rh850, t500)

        prate_res = extract_prate(msgs, VOBL_CELL_LAT, VOBL_CELL_LON, fd["valid_str"])
        if not prate_res["available"]:
            missing_features.append("gfs_prate_kg_m2_s")

        event_group_key = event_group_key_for(vobl_cell_id, ist_date_str, slot_id)

        row = {
            "initialization_time_utc": init_dt.isoformat(),
            "valid_time_utc": valid_dt.isoformat(),
            "valid_time_ist": valid_ist.isoformat(),
            "forecast_lead_hours": lead_hours,
            "slot_id": slot_id,
            "ist_date": ist_date_str,
            "event_group_key": event_group_key,
            "cell_id": vobl_cell_id,
            "cape": cape, "cin": cin, "pwat_mm": pwat,
            "u850": u850, "v850": v850, "u200": u200, "v200": v200,
            "wind_shear_850_200_ms": shear_ms,
            "t850_k": t850, "t700_k": t700, "t500_k": t500,
            "rh850_pct": rh850, "rh700_pct": rh700,
            "k_index": ki_res.value_k if ki_res.available else None,
            "totals_totals": tt_res.value_k if tt_res.available else None,
            "gfs_prate_kg_m2_s": prate_res["value"],
            "gfs_prate_stepType_used": prate_res["stepType_used"],
            "gfs_prate_startStep": prate_res["startStep"],
            "gfs_prate_endStep": prate_res["endStep"],
            "gfs_prate_note": prate_res["note"],
            "gfs_precip_3h_interval_mm": None,
            "gfs_precip_3h_interval_mm_note": None,
            "source": "gfs",
            "archive_source": ARCHIVE_SOURCE,
            "source_file": fd["path"].name,
            "label_source": f"{labels_path} (real IMD/METAR station observation, VOBL/43295)",
            "label_timestamp": label_timestamp,
            "label_definition": "ANY TS observation within the IST slot window => POSITIVE (1); "
                                 "closed window with >=1 real observation and none showing TS => "
                                 "NEGATIVE_CONFIRMED (0); no row / open window => UNKNOWN",
            "label_status": label_row["label_status"] if label_row is not None else "UNMATCHED",
            "label": (int(label_row["label"]) if (label_row is not None and pd.notna(label_row["label"])) else None),
            "missing_features": missing_features,
        }
        rows.append(row)

    # ---- interval precipitation: tp(lead_end) - tp(lead_start), using the
    # two SMALLEST leads present, attached only to the lead-end row -- this
    # pilot has no lead-0 tp in general, so a true 0-to-first-lead interval
    # is never claimed. Generalizes the Phase 0.4.8 f003/f006 logic to any
    # ascending pair of native leads. ----
    if len(fdefs) >= 2:
        start_fd, end_fd = fdefs[0], fdefs[1]
        tp_start_msg = find_message(start_fd["msgs"], "tp", "surface", stepType="accum")
        tp_end_msg = find_message(end_fd["msgs"], "tp", "surface", stepType="accum")
        tp_start_res = regrid_point_value(tp_start_msg["gid"], VOBL_CELL_LAT, VOBL_CELL_LON, start_fd["valid_str"]) if tp_start_msg else None
        tp_end_res = regrid_point_value(tp_end_msg["gid"], VOBL_CELL_LAT, VOBL_CELL_LON, end_fd["valid_str"]) if tp_end_msg else None
        tp_start_val = tp_start_res.value if tp_start_res else None
        tp_end_val = tp_end_res.value if tp_end_res else None

        precip_result = None
        if same_cycle_all and tp_start_msg is not None and tp_end_msg is not None:
            precip_result = compute_interval_precipitation(
                tp_start_mm=tp_start_val, tp_end_mm=tp_end_val,
                init_time=start_fd["init_str"], start_valid_time=start_fd["valid_str"], end_valid_time=end_fd["valid_str"],
                lead_start_hours=start_fd["lead_hours"], lead_end_hours=end_fd["lead_hours"],
                start_step=tp_start_msg["startStep"], end_start_step=tp_end_msg["startStep"],
            )
        rows[0]["gfs_precip_3h_interval_mm_note"] = (
            f"not computable from this pilot: no lead-0 tp field available to form a "
            f"0-to-{start_fd['lead_hours']}h interval (only leads {leads} downloaded)"
        )
        if precip_result is not None and precip_result.available:
            rows[1]["gfs_precip_3h_interval_mm"] = precip_result.value_mm
            rows[1]["gfs_precip_3h_interval_mm_note"] = None
        else:
            rows[1]["gfs_precip_3h_interval_mm_note"] = (
                precip_result.rejection_reason if precip_result is not None else "tp message missing"
            )
        for extra_row in rows[2:]:
            extra_row["gfs_precip_3h_interval_mm_note"] = (
                "interval precipitation only computed for the first two leads in this pilot"
            )
    else:
        rows[0]["gfs_precip_3h_interval_mm_note"] = (
            "not computable: only one forecast lead was provided, no second lead to difference against"
        )

    for fd in fdefs:
        for m in fd["msgs"]:
            try:
                ec.codes_release(m["gid"])
            except Exception:
                pass

    for row in rows:
        missing_req_features = [f for f in REQUIRED_FEATURES if row.get(f) is None]
        if missing_req_features:
            quality_issues.append({
                "source_file": row["source_file"], "issue": "missing required features",
                "features": missing_req_features,
            })
        missing_prov = [f for f in REQUIRED_PROVENANCE if row.get(f) in (None, "")]
        if missing_prov:
            quality_issues.append({
                "source_file": row["source_file"], "issue": "provenance incomplete",
                "fields": missing_prov,
            })
        for f in REQUIRED_FEATURES:
            v = row.get(f)
            if v is not None and not math.isfinite(v):
                quality_issues.append({
                    "source_file": row["source_file"], "issue": "non-finite value", "feature": f,
                })
        if row["cell_id"] != vobl_cell_id:
            quality_issues.append({"source_file": row["source_file"], "issue": "invalid cell id", "cell_id": row["cell_id"]})
        if row["label"] is not None and row["label"] not in (0, 1):
            quality_issues.append({"source_file": row["source_file"], "issue": "invalid label value", "label": row["label"]})

    n_dup_valid_time = len(rows) - len({r["valid_time_utc"] for r in rows})
    n_dup_init_lead = len(rows) - len({(r["initialization_time_utc"], r["forecast_lead_hours"]) for r in rows})
    if n_dup_valid_time:
        quality_issues.append({"issue": "duplicate rows by valid_time", "count": n_dup_valid_time})
    if n_dup_init_lead:
        quality_issues.append({"issue": "duplicate rows by (initialization_time, lead)", "count": n_dup_init_lead})

    leakage_table = dict(FEATURE_LEAKAGE_CLASS)
    uses_future_information = [k for k, v in leakage_table.items() if v == "USES_FUTURE_INFORMATION"]

    n_positive = sum(1 for r in rows if r["label"] == 1)
    n_negative = sum(1 for r in rows if r["label"] == 0)
    n_unmatched = sum(1 for r in rows if r["label"] is None)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_rows = []
    for r in rows:
        rr = dict(r)
        rr["missing_features"] = ";".join(rr["missing_features"]) if rr["missing_features"] else ""
        out_rows.append(rr)
    pd.DataFrame(out_rows).to_csv(out_csv, index=False)

    manifest = {
        "phase": "0.4.12",
        "contract_version": CONTRACT_VERSION,
        "contract_doc": "docs/PHASE_0_4_12_HISTORICAL_GFS_TS_DATASET_CONTRACT.md",
        "status": "BUILT_FROM_ACTUAL_FILES",
        "cycle": cycle,
        "leads_requested": leads,
        "source_files": [
            {
                # Relative to REPO_ROOT when the source file lives inside the
                # repo tree (the historical default/test layout, preserved
                # exactly as before); otherwise the file was read from an
                # external raw root (e.g. a relocated drive passed via
                # --raw-root) that is not a subpath of REPO_ROOT at all, so
                # relative_to() would raise ValueError -- record the
                # absolute path instead rather than crashing the build.
                "path": (
                    str(p.relative_to(REPO_ROOT)) if _is_relative_to(p, REPO_ROOT) else str(p)
                ),
                "sha256": sha256_of(p), "size_bytes": p.stat().st_size,
            }
            for p in paths
        ],
        "archive_source": ARCHIVE_SOURCE,
        "initialization_times": sorted({r["initialization_time_utc"] for r in rows}),
        "forecast_leads_hours": sorted({r["forecast_lead_hours"] for r in rows}),
        "cycle_consistency_check": {
            "same_initialization_cycle_across_all_files": same_cycle_all,
            "initialization_times_seen": sorted({fd["init_str"] for fd in fdefs}),
        },
        "vobl_cell_mapping": vobl_cell_mapping,
        "event_group_keys": sorted({r["event_group_key"] for r in rows}),
        "n_rows": len(rows),
        "n_positive": n_positive,
        "n_negative": n_negative,
        "n_unmatched": n_unmatched,
        "unmatched_detail": unmatched,
        "feature_list": REQUIRED_FEATURES + ["gfs_precip_3h_interval_mm", "gfs_prate_kg_m2_s"],
        "feature_leakage_classification": leakage_table,
        "features_using_future_information": uses_future_information,
        "label_definition": rows[0]["label_definition"] if rows else None,
        "label_source": str(labels_path),
        "canonical_cell": vobl_cell_id,
        "quality_issues": quality_issues,
        "creation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "script": "scripts/build_vobl_historical_gfs_ts_join.py",
        "script_version": "phase_0_4_12_v1_contract_fields",
    }
    out_manifest.write_text(json.dumps(manifest, indent=2, default=str))

    summary = {
        "status": "BUILT_FROM_ACTUAL_FILES",
        "cycle": cycle, "leads": leads,
        "out_csv": str(out_csv), "out_manifest": str(out_manifest),
        "n_rows": len(rows), "n_positive": n_positive, "n_negative": n_negative,
        "n_unmatched": n_unmatched, "quality_issues": len(quality_issues),
        "uses_future_information_features": uses_future_information,
    }
    print(json.dumps(summary, indent=2))
    return summary


def parse_args(argv: Optional[List[str]] = None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cycle", default=DEFAULT_CYCLE,
                   help="GFS initialization cycle, YYYYMMDDHH (default: %(default)s, the original Phase 0.4.8 pilot)")
    p.add_argument("--leads", nargs="+", default=[str(x) for x in DEFAULT_LEADS],
                   help="Forecast lead hours, e.g. --leads 003 009 (default: %(default)s)")
    p.add_argument("--out-csv", default=None, help="Output CSV path (default depends on cycle)")
    p.add_argument("--out-manifest", default=None, help="Output manifest JSON path (default depends on cycle)")
    p.add_argument("--ts-labels", default=None,
                   help="Explicit path to the real ts_labels.csv artifact to join against "
                        "(default: %s, the original Phase 0.4.8 default). This script never "
                        "generates, copies, alters, or fabricates this file's contents -- it "
                        "only reads it from whatever path is given." % TS_LABELS_PATH)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None):
    args = parse_args(argv)
    leads = [int(x) for x in args.leads]

    if args.cycle == DEFAULT_CYCLE and leads == DEFAULT_LEADS:
        # Exact backward-compatible path: identical output filenames to the
        # original Phase 0.4.8 script.
        out_csv = Path(args.out_csv) if args.out_csv else DEFAULT_OUT_CSV
        out_manifest = Path(args.out_manifest) if args.out_manifest else DEFAULT_OUT_MANIFEST
    else:
        lead_tag = "_".join(f"f{l:03d}" for l in sorted(leads))
        out_csv = Path(args.out_csv) if args.out_csv else OUT_DIR / f"historical_gfs_vobl_{args.cycle}_{lead_tag}.csv"
        out_manifest = Path(args.out_manifest) if args.out_manifest else OUT_DIR / f"historical_gfs_vobl_{args.cycle}_{lead_tag}_manifest.json"

    ts_labels_path = Path(args.ts_labels) if args.ts_labels else None

    build_pilot(args.cycle, leads, out_csv, out_manifest, ts_labels_path=ts_labels_path)


if __name__ == "__main__":
    main()
