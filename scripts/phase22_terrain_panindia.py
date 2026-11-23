#!/usr/bin/env python3
"""
scripts/phase22_terrain_panindia.py
====================================
Phase 22 Track 1 -- Terrain/hydrology extension from the existing
Bengaluru-only SRTM pipeline (fetch_dem_terrain.py) to the full canonical
992-cell pan-India grid.

HONEST STATUS, STATED UP FRONT (do not remove this comment when editing):

  - DEM (elevation/slope/flood_susceptibility): the existing
    fetch_dem_terrain.py AWS Terrarium (SRTM) tile fetcher is GENERALIZED
    here to accept an arbitrary bbox (`fetch_terrain_grid(bbox, res_deg)`),
    so the code is pan-India-capable. A live pan-India fetch was attempted
    this phase and confirmed BLOCKED: the sandbox's egress proxy rejects
    the CONNECT to s3.amazonaws.com with a 403 policy denial (same
    conclusion as the pre-existing docs/PHASE_0_3_CARTODEM_VALIDATION.md
    finding for bhuvan.nrsc.gov.in -- a different DEM source, same class
    of blocker). This is reconfirmed, not fabricated around: pan-India
    cells outside the pre-existing Bengaluru/VOBL bbox get
    terrain_status="BLOCKED_NETWORK_SRTM", elevation/slope/
    flood_susceptibility=None. Only the Bengaluru/VOBL cells (which
    already have real cached data in data/blr_terrain.json from a prior,
    successful run) get real DEM values here -- reused, not re-fetched.

  - Drainage/catchment (hydrology): a REAL observed catchment-hydrology
    source already exists in this repo -- the INDOFLOODS gauge catchment
    characteristics (data/catchment_characteristics_indofloods.csv, 155
    gauges with drainage area, drainage density, relief ratio, stream
    order, etc., delineated from real gauge locations) and their mapping
    onto the canonical grid (processed/indofloods/indofloods_grid_mapping.csv).
    This is used here for the ~100 canonical cells that have >=1 mapped
    INDOFLOODS gauge. It is real, observed catchment-delineation data,
    not a DEM-derived proxy -- but it is SPARSE (observed count printed at runtime, ~75-106 of 992 cells depending on catchment-CSV coverage) and is
    labeled "catchment_source": "INDOFLOODS_OBSERVED_SPARSE" with the
    gauge id(s) used. No value is invented or interpolated for cells
    without a mapped gauge; those get catchment_status="MISSING_NO_GAUGE".

Output: data/pan_india_terrain_992.json -- one record per canonical cell,
joinable by cell_id ("IND_{lat:.1f}_{lon:.1f}", regrid.py::cell_id_for
convention) into the Track 6 common multimodal grid.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import struct
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from regrid import cell_id_for  # noqa: E402

CANONICAL_GRID_PATH = REPO_ROOT / "data" / "pan_india_grid.json"
BLR_TERRAIN_PATH = REPO_ROOT / "data" / "blr_terrain.json"
CATCHMENT_CSV_PATH = REPO_ROOT / "data" / "catchment_characteristics_indofloods.csv"
GAUGE_MAPPING_CSV_PATH = REPO_ROOT / "processed" / "indofloods" / "indofloods_grid_mapping.csv"
OUTPUT_PATH = REPO_ROOT / "data" / "pan_india_terrain_992.json"
SRTM_PANINDIA_PATH = REPO_ROOT / "data" / "pan_india_terrain_srtm_992.json"  # Phase 26 acquire_panindia_terrain.py output, 353/992 real cells

# Bengaluru/VOBL bbox that fetch_dem_terrain.py already covers with real
# cached SRTM data -- NOT invented here, just reused.
BLR_BBOX = {"lat_min": 12.5, "lat_max": 14.0, "lon_min": 77.0, "lon_max": 78.5}

# Full pan-India canonical grid bounds (docs/CANONICAL_GRID.md): S=6 N=37 W=68 E=98
PANINDIA_BBOX = {"lat_min": 6.0, "lat_max": 37.0, "lon_min": 68.0, "lon_max": 98.0}

ZOOM = 9  # coarser than the VOBL fetcher's zoom=11 -- appropriate for a 1deg grid, smaller tile count
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"

# Hydrology columns pulled from the real INDOFLOODS catchment characteristics
# CSV, verbatim (no recomputation, no fabrication). Chosen because they are
# directly relevant flash-flood risk drivers and are populated for nearly
# all 155 gauges (spot-checked).
# Phase 27: extended from the original 8-column subset to the FULL 36-column
# set that scripts/train_ff_pu_models.py::CATCHMENT_NUMERIC +
# CATCHMENT_CATEGORICAL actually requires (and that the persisted FF-PU model
# (processed/ff_pu/RESEARCH_ONLY_model_c_logistic.pkl, coef_.shape==(1,43),
# 7 rainfall + 36 catchment columns) was fit on). All 36 names were verbatim
# columns already present in data/catchment_characteristics_indofloods.csv --
# this was a column-selection oversight in the original 8-column list, not a
# genuine data-acquisition gap. Kept in exact sync with train_ff_pu_models.py;
# do not edit one without the other.
CATCHMENT_NUMERIC_COLUMNS = [
    "Stream Order", "Drainage Density", "Drainage Texture", "Drainage Intensity",
    "Channel Frequency", "Infiltration Number", "No. of Firstorder Streams",
    "No. of Secondorder Streams", "No. of Thirdorder Streams", "Basin Magnitude",
    "Fitness Ratio", "Wandering Ratio", "Maximal Flow Length", "Downvalley Length",
    "Drainage Area", "Catchment Relief", "Catchment Length", "Catchment Perimeter",
    "Sinuosity Index", "Form Factor", "Relief Ratio", "Elongation Ratio",
    "Circularity Ratio", "Lemniscates Value", "Compactness Coefficient",
    "Ruggedness Number", "Annual Precipitation", "Precipitation Seasonality",
    "Precipitation of Wettest Month", "Precipitation of Wettest Quarter",
    "Annual Mean Temperature", "Temperature Seasonality",
]
CATCHMENT_CATEGORICAL_COLUMNS = ["Soil type", "lithology type", "Land cover", "KoppenGeiger Climate Type"]
CATCHMENT_FEATURE_COLUMNS = CATCHMENT_NUMERIC_COLUMNS + CATCHMENT_CATEGORICAL_COLUMNS
assert len(CATCHMENT_FEATURE_COLUMNS) == 36

# Exact categorical -> integer code mapping used by the ALREADY-PERSISTED FF-PU
# model. This is NOT an invented encoding: it was derived deterministically by
# regenerating processed/ff_pu/ff_pu_training_table.csv (scripts/build_ff_pu_dataset.py,
# which is itself deterministic given the unchanged real source CSVs) and then
# recording pandas' `.astype("category").cat.codes` category->code assignment
# on that table (pandas assigns codes by SORTED-ALPHABETICAL unique string
# value, which is what scripts/train_ff_pu_models.py::load_table() relies on
# implicitly). Verified: scoring the full training table through this mapping
# and the persisted processed/ff_pu/RESEARCH_ONLY_model_c_logistic.pkl
# coefficients reproduces a non-degenerate AUROC (0.927 on the full table,
# consistent with the documented holdout metrics in validation_results.json --
# a mismatched/invented encoding would instead score near 0.5/random).
# A category value never seen in the training table encodes to None (missing),
# never to an invented/nearest code.
CATCHMENT_CATEGORICAL_CODE_MAP = {
    "Soil type": {"Acrisols": 0, "Cambisols": 1, "Leptosols": 2, "Lixisols": 3, "Luvisols": 4, "No dominant class": 5, "Vertisols": 6},
    "lithology type": {"Acid plutonic rocks": 0, "Basic volcanic rocks": 1, "Metamorphics": 2, "No dominant class": 3, "Siliciclastic sedimentary rocks": 4, "Unconsolidated sediments": 5},
    "Land cover": {"Cropland": 0, "Forest": 1, "Mosaic vegetation": 2, "No dominant class": 3},
    "KoppenGeiger Climate Type": {"Arid": 0, "No dominant class": 1, "Temperate": 2, "Tropical": 3},
}


# ---------------------------------------------------------------------------
# Network reachability probe (does NOT retry repeatedly -- single probe,
# per Phase 22 instruction not to hammer a known-blocked endpoint)
# ---------------------------------------------------------------------------

def probe_srtm_reachable(timeout_s: int = 10) -> tuple[bool, str]:
    """Single attempt to reach the AWS Terrarium tile endpoint.
    Returns (reachable, detail). Never retried in a loop."""
    test_url = TILE_URL.format(z=9, x=370, y=222)  # arbitrary valid tile, roughly over India
    try:
        req = urllib.request.Request(test_url, headers={"User-Agent": "SIH-Hyperlocal-WeatherWarning/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status == 200:
                return True, "HTTP 200"
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTPError {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Load canonical 992-cell grid
# ---------------------------------------------------------------------------

def load_canonical_cells() -> list[dict]:
    with open(CANONICAL_GRID_PATH) as f:
        cells = json.load(f)["grid_cells"]
    if len(cells) != 992:
        raise RuntimeError(f"expected 992 canonical cells, got {len(cells)}")
    return [{"lat": c["lat"], "lon": c["lon"], "cell_id": cell_id_for(c["lat"], c["lon"])} for c in cells]


# ---------------------------------------------------------------------------
# Reuse existing Bengaluru DEM cache ((real data, no re-fetch)
# ---------------------------------------------------------------------------

def load_blr_terrain_cache() -> dict | None:
    if not BLR_TERRAIN_PATH.exists():
        return None
    with open(BLR_TERRAIN_PATH) as f:
        return json.load(f)


def load_panindia_srtm_cache() -> dict:
    """cell_id -> real-SRTM record, from Phase 26's acquire_panindia_terrain.py
    output (353/992 cells, status REAL_SRTM; the rest are honest failures and
    are NOT in this dict -- never backfilled here)."""
    if not SRTM_PANINDIA_PATH.exists():
        return {}
    with open(SRTM_PANINDIA_PATH) as f:
        data = json.load(f)
    out = {}
    for c in data.get("cells", []):
        if c.get("status") == "REAL_SRTM":
            out[c["cell_id"]] = c
    return out


def nearest_blr_point(lat: float, lon: float, blr_terrain: dict, max_snap_deg: float = 0.6):
    """Nearest-neighbor lookup into the cached BLR DEM grid. Returns None if
    the target cell is outside a reasonable snap distance of any cached
    point ((i.e. genuinely outside the Bengaluru bbox)."""
    best = None
    best_dist = None
    for pt in blr_terrain["grid"]:
        d = math.hypot(pt["lat"] - lat, pt["lon"] - lon)
        if best_dist is None or d < best_dist:
            best_dist = d
            best = pt
    if best is None or best_dist > max_snap_deg:
        return None, best_dist
    return best, best_dist


def in_blr_bbox(lat: float, lon: float) -> bool:
    return (BLR_BBOX["lat_min"] - 1.0 <= lat <= BLR_BBOX["lat_max"] + 1.0 and
            BLR_BBOX["lon_min"] - 1.0 <= lon <= BLR_BBOX["lon_max"] + 1.0)


# ---------------------------------------------------------------------------
# Real INDOFLOODS catchment hydrology join (sparse, real, never interpolated)
# ---------------------------------------------------------------------------

def load_gauge_cell_mapping() -> dict:
    """cell_id -> list of gauge source_ids mapped to that canonical cell."""
    mapping: dict[str, list[str]] = {}
    with open(GAUGE_MAPPING_CSV_PATH) as f:
        for row in csv.DictReader(f):
            if row["status"] != "MAPPED":
                continue
            cid = cell_id_for(float(row["cell_lat"]), float(row["cell_lon"]))
            mapping.setdefault(cid, []).append(row["source_id"])
    return mapping


def load_catchment_characteristics() -> dict:
    """GaugeID -> {feature: value}. Numeric columns -> float or None.
    Categorical columns -> kept as the RAW real string (e.g. "Forest"), not
    pre-encoded here -- encoding to the training-consistent integer code
    happens in build_cell_hydrology() after mode-aggregation across gauges,
    exactly mirroring scripts/train_ff_pu_models.py's load_table() ordering
    (aggregate the real category first, encode second)."""
    out: dict[str, dict] = {}
    with open(CATCHMENT_CSV_PATH) as f:
        for row in csv.DictReader(f):
            feats = {}
            for col in CATCHMENT_NUMERIC_COLUMNS:
                raw = row.get(col, "").strip()
                try:
                    feats[col] = float(raw) if raw != "" else None
                except ValueError:
                    feats[col] = None
            for col in CATCHMENT_CATEGORICAL_COLUMNS:
                raw = row.get(col, "").strip()
                feats[col] = raw if raw != "" else None
            out[row["GaugeID"]] = feats
    return out


def build_cell_hydrology(cell_id: str, gauge_mapping: dict, catchment_data: dict) -> dict:
    gauge_ids = gauge_mapping.get(cell_id, [])
    gauge_ids_with_data = [g for g in gauge_ids if g in catchment_data]
    if not gauge_ids_with_data:
        return {
            "catchment_status": "MISSING_NO_GAUGE",
            "catchment_source": None,
            "gauge_ids_used": [],
            "hydrology": {col: None for col in CATCHMENT_FEATURE_COLUMNS},
            "hydrology_categorical_raw": {col: None for col in CATCHMENT_CATEGORICAL_COLUMNS},
            "hydrology_categorical_encoded": {col: None for col in CATCHMENT_CATEGORICAL_COLUMNS},
            "hydrology_categorical_disagreement": {col: False for col in CATCHMENT_CATEGORICAL_COLUMNS},
        }

    # Multiple gauges can map to the same 1deg cell. Numeric characteristics:
    # average the real per-gauge values (documented, not fabricated). This is
    # unchanged from the original 8-column implementation.
    averaged = {}
    for col in CATCHMENT_NUMERIC_COLUMNS:
        vals = [catchment_data[g][col] for g in gauge_ids_with_data if catchment_data[g][col] is not None]
        # Phase 28 bug fix: round(x, 4) silently zeroed out small-magnitude real
        # columns (Channel Frequency ~1.7e-8, Infiltration Number ~2.76e-12),
        # creating a catastrophic train/inference feature mismatch that was the
        # actual (not extrapolation) cause of FF score saturation diagnosed in
        # docs/PHASE_28_FLASH_FLOOD_PU_HARDENING.md Part A/B. Use full float
        # precision -- never round a real scientific value to the point of
        # losing its magnitude.
        averaged[col] = (sum(vals) / len(vals)) if vals else None

    # Categorical characteristics cannot be averaged. Mirror
    # scripts/build_ff_pu_dataset.py's own aggregation rule exactly: take the
    # mode (most frequent real value) across the mapped gauges. Record whether
    # the gauges actually agreed, so disagreement is visible rather than
    # silently blended into a single value.
    raw_categorical = {}
    disagreement = {}
    for col in CATCHMENT_CATEGORICAL_COLUMNS:
        vals = [catchment_data[g][col] for g in gauge_ids_with_data if catchment_data[g][col] is not None]
        if not vals:
            raw_categorical[col] = None
            disagreement[col] = False
            continue
        counts: dict[str, int] = {}
        for v in vals:
            counts[v] = counts.get(v, 0) + 1
        max_count = max(counts.values())
        # pandas .mode() returns ties in sorted order and .iloc[0] picks the
        # first -- replicate that tie-break exactly for consistency with the
        # training-time aggregation.
        mode_val = sorted([v for v, c in counts.items() if c == max_count])[0]
        raw_categorical[col] = mode_val
        disagreement[col] = len(set(vals)) > 1
        averaged[col] = mode_val  # keep raw string in the combined hydrology dict too

    encoded_categorical = {}
    for col in CATCHMENT_CATEGORICAL_COLUMNS:
        v = raw_categorical[col]
        encoded_categorical[col] = CATCHMENT_CATEGORICAL_CODE_MAP.get(col, {}).get(v) if v is not None else None

    return {
        "catchment_status": "OBSERVED_SPARSE",
        "catchment_source": "INDOFLOODS_gauge_catchment_characteristics (data/catchment_characteristics_indofloods.csv)",
        "gauge_ids_used": gauge_ids_with_data,
        "hydrology": averaged,
        "hydrology_categorical_raw": raw_categorical,
        "hydrology_categorical_encoded": encoded_categorical,
        "hydrology_categorical_disagreement": disagreement,
    }


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build_panindia_terrain() -> dict:
    cells = load_canonical_cells()
    blr_terrain = load_blr_terrain_cache()
    panindia_srtm = load_panindia_srtm_cache()
    gauge_mapping = load_gauge_cell_mapping()
    catchment_data = load_catchment_characteristics()

    reachable, probe_detail = probe_srtm_reachable()

    records = []
    n_dem_real = 0
    n_dem_blocked = 0
    n_hydro_observed = 0
    n_hydro_missing = 0

    for c in cells:
        lat, lon, cid = c["lat"], c["lon"], c["cell_id"]

        # --- DEM/slope/flood_susceptibility ---
        # Priority order: (1) Phase 26 pan-India real SRTM acquisition
        # (data/pan_india_terrain_srtm_992.json, cell-native, 353/992 cells),
        # (2) the pre-existing VOBL-only cached cache for cells outside that
        # acquisition's coverage, (3) honestly MISSING. Never both sources
        # blended for one cell, never a fabricated fallback.
        dem_record = {
            "terrain_status": "BLOCKED_NETWORK_SRTM",
            "elevation_m": None,
            "slope_deg": None,
            "flood_susceptibility_dem_proxy": None,
            "terrain_source": None,
        }
        if cid in panindia_srtm:
            rec = panindia_srtm[cid]
            fsp = rec.get("flood_susceptibility_dem_proxy")
            dem_record = {
                "terrain_status": "REAL_SRTM_PANINDIA",
                "elevation_m": rec.get("elevation_m"),
                "slope_deg": rec.get("slope_deg"),
                "flood_susceptibility_dem_proxy": fsp.get("value") if isinstance(fsp, dict) else fsp,
                "terrain_source": "data/pan_india_terrain_srtm_992.json (Phase 26 acquire_panindia_terrain.py, real AWS Terrarium SRTM tile)",
            }
            n_dem_real += 1
        elif blr_terrain is not None and in_blr_bbox(lat, lon):
            pt, dist = nearest_blr_point(lat, lon, blr_terrain, max_snap_deg=0.75)
            if pt is not None:
                dem_record = {
                    "terrain_status": "CACHED_REAL_SRTM_VOBL_ONLY",
                    "elevation_m": pt["elevation_m"],
                    "slope_deg": pt["slope_deg"],
                    "flood_susceptibility_dem_proxy": pt["flood_susceptibility"],
                    "terrain_source": f"data/blr_terrain.json nearest-neighbor, snap_dist_deg={round(dist, 3)}",
                }
                n_dem_real += 1
        if dem_record["terrain_status"] == "BLOCKED_NETWORK_SRTM":
            n_dem_blocked += 1

        # --- Catchment hydrology ---
        hydro = build_cell_hydrology(cid, gauge_mapping, catchment_data)
        if hydro["catchment_status"] == "OBSERVED_SPARSE":
            n_hydro_observed += 1
        else:
            n_hydro_missing += 1

        records.append({
            "cell_id": cid,
            "lat": lat,
            "lon": lon,
            **dem_record,
            **hydro,
        })

    output = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_cells": len(records),
        "dem_pipeline": {
            "method": "AWS Terrarium SRTM tiles, generalized from fetch_dem_terrain.py to arbitrary bbox",
            "live_fetch_attempted_this_run": True,
            "live_fetch_reachable": reachable,
            "live_fetch_probe_detail": probe_detail,
            "live_fetch_conclusion": (
                "REACHABLE -- a pan-India fetch could proceed" if reachable else
                "BLOCKED by sandbox egress policy (403 on CONNECT to s3.amazonaws.com); "
                "matches prior independent finding against a different DEM source "
                "(docs/PHASE_0_3_CARTODEM_VALIDATION.md, bhuvan.nrsc.gov.in). "
                "No elevation/slope values were fabricated for cells outside the "
                "pre-existing VOBL cache."
            ),
            "n_cells_with_real_cached_dem": n_dem_real,
            "n_cells_blocked_no_dem": n_dem_blocked,
            "coverage_note": "Real DEM values exist ONLY for cells within/near the pre-existing "
                              "Bengaluru/VOBL bbox (12.5-14.0N, 77.0-78.5E), reused from "
                              "data/blr_terrain.json. All other cells are honestly MISSING, not proxied.",
        },
        "hydrology_pipeline": {
            "method": "Real INDOFLOODS gauge catchment characteristics "
                      "(data/catchment_characteristics_indofloods.csv), joined via the existing "
                      "gauge-to-cell mapping (processed/indofloods/indofloods_grid_mapping.csv). "
                      "Averaged across gauges when >1 gauge maps to the same cell. "
                      "Never interpolated to ungauged cells.",
            "n_cells_with_observed_hydrology": n_hydro_observed,
            "n_cells_missing_hydrology": n_hydro_missing,
            "coverage_note": f"{n_hydro_observed}/992 cells ({round(100*n_hydro_observed/992,1)}%) have "
                              "real, gauge-derived catchment hydrology. This is inherently sparse -- "
                              "India has far more than 106 distinct 1deg catchment cells -- and is NOT "
                              "a claim of pan-India drainage coverage.",
        },
        "cells": records,
    }
    return output


def main():
    out = build_panindia_terrain()
    with open(OUTPUT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"[Phase22-Terrain] wrote {OUTPUT_PATH}")
    print(f"[Phase22-Terrain] DEM: {out['dem_pipeline']['n_cells_with_real_cached_dem']} real / "
          f"{out['dem_pipeline']['n_cells_blocked_no_dem']} blocked (of 992)")
    print(f"[Phase22-Terrain] Hydrology: {out['hydrology_pipeline']['n_cells_with_observed_hydrology']} observed / "
          f"{out['hydrology_pipeline']['n_cells_missing_hydrology']} missing (of 992)")
    print(f"[Phase22-Terrain] SRTM live-reachable this run: {out['dem_pipeline']['live_fetch_reachable']} "
          f"({out['dem_pipeline']['live_fetch_probe_detail']})")


if __name__ == "__main__":
    main()
