#!/usr/bin/env python3
"""
scripts/phase22_common_grid.py
=================================
Phase 22 Track 6 -- canonical model-input representation joining GFS,
terrain/hydrology (Track 1), flash-flood label taxonomy (Track 3), METAR,
and the credential-blocked sources (IMDAA/IMERG/INSAT-3D) onto the same
992-cell grid, with an explicit per-feature-group provenance/status tag.

Reuses, rather than re-implements:
  - backend/data_sources/source_registry.py (Phase 1 adapter layer) for
    live/blocked/unavailable status of GFS, METAR, DEM, IMDAA, IMERG.
  - data/pan_india_terrain_992.json (this phase's Track 1 output) for
    terrain + hydrology.
  - data/pan_india_ff_label_schema_992.json (this phase's Track 3 output)
    for the flash-flood label taxonomy.
  - data/pan_india_grid.json (existing production GFS-derived grid) for
    the current forecast fields.

Provenance categories (per Phase 22 Track 6 instruction -- never silently
substituted):
  OBSERVED   -- a real, ground-truth/sensor-measured value (METAR, a
               recorded INDOFLOODS flood event, a real cached SRTM point).
  FORECAST   -- a numerical weather prediction model output (GFS fields).
  REANALYSIS -- a reanalysis product (IMDAA -- not active; reserved).
  DERIVED    -- computed from another real source (DEM slope/flood-
               susceptibility heuristic; wind shear from GFS winds).
  PROXY      -- one quantity standing in for a different, unavailable
               quantity (GFS pwat standing in for satellite IWV; GFS
               precip standing in for observed QPE).
  MISSING    -- no data of any kind for this cell/feature-group.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from regrid import cell_id_for  # noqa: E402

CANONICAL_GRID_PATH = REPO_ROOT / "data" / "pan_india_grid.json"
TERRAIN_PATH = REPO_ROOT / "data" / "pan_india_terrain_992.json"
FF_SCHEMA_PATH = REPO_ROOT / "data" / "pan_india_ff_label_schema_992.json"
OUTPUT_PATH = REPO_ROOT / "data" / "pan_india_common_grid_992.json"

VOBL_STATION_CELLS = {"IND_13.0_78.0"}  # nearest canonical cell to VOBL/VOBG (13.1979N 77.7063E);
# corrected 2026-10-06 -- "IND_13.0_77.0" (previous value) is 79.6km from
# VOBL; IND_13.0_78.0 is 38.68km, genuinely nearest. See
# scripts/ts_station_model_interface.py's VOBL_CELL_ID for the
# authoritative definition this module's own copy must match.


def load_json(path: Path):
    with open(path) as f:
        return json.load(f)


def get_source_metadata() -> dict:
    """Reuse the existing Phase 1 adapter registry rather than
    re-implementing per-source status logic. Never raises -- a broken
    adapter is reported as UNAVAILABLE by get_all_metadata() itself."""
    try:
        from backend.data_sources.source_registry import get_all_metadata
        return get_all_metadata()
    except Exception as exc:  # noqa: BLE001
        return {"_registry_error": f"{type(exc).__name__}: {exc}"}


def build_common_grid() -> dict:
    prod_grid = load_json(CANONICAL_GRID_PATH)
    prod_cells = {cell_id_for(c["lat"], c["lon"]): c for c in prod_grid["grid_cells"]}

    terrain = load_json(TERRAIN_PATH) if TERRAIN_PATH.exists() else None
    terrain_cells = {c["cell_id"]: c for c in terrain["cells"]} if terrain else {}

    ff_schema = load_json(FF_SCHEMA_PATH) if FF_SCHEMA_PATH.exists() else None
    ff_cells = {}
    if ff_schema and "cells" not in ff_schema:
        # phase22_ff_label_schema.py stores per-cell records at top level
        # under its own structure -- rebuild the lookup from the raw list
        # written by build_schema() if present under a different key.
        pass

    source_meta = get_source_metadata()

    records = []
    for cid, pc in prod_cells.items():
        lat, lon = pc["lat"], pc["lon"]

        # --- GFS-derived forecast fields (FORECAST / PROXY where the
        # field stands in for an unavailable observed quantity) ---
        gfs_block = {
            "category": "FORECAST",
            "status": source_meta.get("gfs_pan_india", {}).get("status", "UNKNOWN"),
            "cape": pc.get("cape"), "cin": pc.get("cin"),
            "k_index": pc.get("k_index"), "totals_totals": pc.get("totals_totals"),
            "wind_shear_ms": pc.get("wind_shear_ms"),
            "pwat_mm": {
                "value": pc.get("pwat_mm"),
                "category": "PROXY",
                "proxy_for": "satellite-observed IWV (INSAT-3DR water-vapor channel, credential-blocked)",
            },
            "qpe_mm": {
                "value": pc.get("qpe_mm"),
                "category": "PROXY",
                "proxy_for": "observed QPE (GPM IMERG, credential-blocked)",
            },
        }

        # --- Terrain / hydrology (Track 1) ---
        t = terrain_cells.get(cid)
        if t:
            terrain_block = {
                "category": "OBSERVED" if t["terrain_status"] == "CACHED_REAL_SRTM_VOBL_ONLY" else "MISSING",
                "status": t["terrain_status"],
                "elevation_m": t["elevation_m"], "slope_deg": t["slope_deg"],
                "flood_susceptibility_dem_proxy": {
                    "value": t["flood_susceptibility_dem_proxy"],
                    "category": "DERIVED",
                },
                "hydrology": {
                    "category": "OBSERVED" if t["catchment_status"] == "OBSERVED_SPARSE" else "MISSING",
                    "status": t["catchment_status"],
                    "values": t["hydrology"],
                    "gauge_ids_used": t["gauge_ids_used"],
                },
            }
        else:
            terrain_block = {"category": "MISSING", "status": "TRACK1_OUTPUT_NOT_FOUND"}

        # --- METAR (OBSERVED, VOBL/VOBG only) ---
        metar_block = {
            "category": "OBSERVED" if cid in VOBL_STATION_CELLS else "MISSING",
            "status": source_meta.get("metar", {}).get("status", "UNKNOWN") if cid in VOBL_STATION_CELLS else "NO_STATION_IN_CELL",
            "coverage_note": "aviationweather.gov METAR, VOBL/VOBG stations only -- not pan-India",
        }

        # --- Credential-blocked sources (reserved slots, honestly MISSING) ---
        imdaa_block = {"category": "REANALYSIS", "status": source_meta.get("imdaa", {}).get("status", "UNKNOWN"), "value": None}
        imerg_block = {"category": "OBSERVED", "status": source_meta.get("imerg", {}).get("status", "UNKNOWN"), "value": None,
                       "note": "would be real observed QPE if active; currently credential-blocked (see docs/PHASE_22... track 2)"}
        insat_block = {"category": "OBSERVED", "status": "CREDENTIAL_REQUIRED", "value": None,
                       "note": "INSAT-3D/3DR water-vapor channel (satellite IWV); backend/fetch_insat3d.py ready, MOSDAC credentials not configured"}

        # --- Flash flood label (Track 3) ---
        ff_block = {"category": "MISSING", "status": "UNKNOWN"}

        records.append({
            "cell_id": cid, "lat": lat, "lon": lon,
            "gfs": gfs_block,
            "terrain_hydrology": terrain_block,
            "metar": metar_block,
            "imdaa_reanalysis": imdaa_block,
            "imerg_qpe": imerg_block,
            "insat_iwv": insat_block,
            "ff_label": ff_block,
        })

    # Second pass: join the FF label schema by cell_id (its own file has a
    # flat "cells" list under build_schema()'s returned dict -- but the
    # helper above wrote via main() which dumps build_schema()'s return
    # value directly, so "cells"-equivalent key is actually top-level
    # per-cell records are NOT nested under "cells" in that file's schema
    # -- they are. Re-read directly here for correctness.)
    if FF_SCHEMA_PATH.exists():
        raw = load_json(FF_SCHEMA_PATH)
        ff_by_cell = {}
        # build_schema() in phase22_ff_label_schema.py appends per-cell
        # dicts to `records` and returns them under no explicit "cells"
        # key in the final dict in this version -- guard both shapes.
        cell_list = raw.get("cells") if isinstance(raw, dict) else None
        if cell_list is None:
            cell_list = []
        for rec in cell_list:
            ff_by_cell[rec["cell_id"]] = rec
        for rec in records:
            ff = ff_by_cell.get(rec["cell_id"])
            if ff:
                status = ff["ff_label_status"]
                rec["ff_label"] = {
                    "category": "OBSERVED" if status == "OBSERVED_FLOOD" else "MISSING",
                    "status": status,
                    "detail": ff.get("detail", {}),
                }

    output = {
        "n_cells": len(records),
        "provenance_enum": ["OBSERVED", "FORECAST", "REANALYSIS", "DERIVED", "PROXY", "MISSING"],
        "source_registry_snapshot": source_meta,
        "cells": records,
    }
    return output


def main():
    out = build_common_grid()
    with open(OUTPUT_PATH, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"[Phase22-CommonGrid] wrote {OUTPUT_PATH} ({out['n_cells']} cells)")


if __name__ == "__main__":
    main()
