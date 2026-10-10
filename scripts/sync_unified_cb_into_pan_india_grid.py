#!/usr/bin/env python3
"""
scripts/sync_unified_cb_into_pan_india_grid.py

2026-10-10: found, by directly inspecting the deployed production
site (https://sih-hyperlocal-warning.pages.dev/) and its real network
traffic, that the client-side hazard lookup for any searched location
(assets/location_engine.js -> data/pan_india_grid.json) NEVER reads
data/unified_forecast.json at all. Every searched cell's
cloudburst_probability comes from backend/pipeline.py's old
deterministic physics-baseline formula (honestly labeled
"physics_baseline, not a calibrated model" in the UI -- not
fabricated, just not what this project's real trained model produces)
-- completely disconnected from models/panindia_cb_v1 (Phase 21,
LODO-validated XGBoost, the actual trained CB model this whole
pipeline exists to serve).

This script is the minimal, surgical fix: after
scripts/phase34_build_unified_forecast.py produces
data/unified_forecast.json, overwrite each matching cell's
cloudburst_probability in data/pan_india_grid.json with the REAL
panindia_cb_v1 calibrated value for that cell_id -- joined on the
exact same cell_id string both files already share (e.g.
"IND_6.0_68.0"), verified identical, not assumed. CB is daily-
resolution (same value at every lead slot, honestly documented
elsewhere), so any one lead's record carries the right value; this
reads lead_hours=2 (the first canonical slot) per cell.

Deliberately does NOT overwrite thunderstorm_probability or
flash_flood_probability: TS has no trained pan-India model (VOBL-only;
assets/location_engine.js already independently computes VOBL
applicability by distance, so it needs no sync here) and FF has no
valid OPERATIONAL probability (PU ranking score only) -- overwriting
flash_flood_probability itself would misrepresent an unavailable/
different-scope research result as this grid's existing probability
semantics. Only CB has a real, trained, pan-India, calibrated
probability to substitute in for the number itself.

A cell_id present in pan_india_grid.json but absent/UNAVAILABLE in
unified_forecast.json is left completely untouched for that field --
never zeroed, never guessed.

2026-10-10 addition: real pan-India per-hazard coverage audit (via the
live production unified_forecast.json) found FF's genuine research-
only PU ranking score exists for only 71/992 cells (sparse real
INDOFLOODS catchment coverage -- a genuine data limitation, not a
bug), honestly represented as probability=null/UNAVAILABLE in
unified_forecast.json for the other 921 -- but NONE of that honest
per-cell status ever reached data/pan_india_grid.json, the file the
deployed frontend actually reads, which only ever carries the OLD
physics-baseline flash_flood_probability (a number for every cell,
with no "research coverage exists here" concept at all). Added
flash_flood_research_status/flash_flood_research_pu_score (additive
fields, the existing flash_flood_probability number is untouched) so
the frontend CAN distinguish "a real research-only PU ranking exists
for this cell" from "only the physics-baseline heuristic exists here"
-- the actual data the honesty requirement needs, not present before.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIFIED_PATH = REPO_ROOT / "data" / "unified_forecast.json"
GRID_PATH = REPO_ROOT / "data" / "pan_india_grid.json"
SYNC_LEAD_HOURS = 2  # CB is daily-resolution; any lead carries the same value


def build_cb_lookup(unified: dict) -> dict[str, dict]:
    """cell_id -> {probability, model_version, source_status} for the
    chosen lead, CB records only, and only where a real probability
    exists (never a None/UNAVAILABLE CB entry)."""
    out = {}
    for rec in unified.get("records", []):
        if rec.get("lead_hours") != SYNC_LEAD_HOURS:
            continue
        cb = rec.get("CB", {})
        if cb.get("probability") is None:
            continue
        out[rec["cell_id"]] = {
            "probability": cb["probability"],
            "model_version": cb.get("model_version"),
            "source_status": cb.get("source_status"),
        }
    return out


def build_ff_research_status_lookup(unified: dict) -> dict[str, dict]:
    """cell_id -> {pu_score, model_version} ONLY where a real PU
    ranking score genuinely exists (never fabricates a status for a
    cell outside real INDOFLOODS catchment coverage) -- additive
    metadata, never touches flash_flood_probability itself."""
    out = {}
    for rec in unified.get("records", []):
        if rec.get("lead_hours") != SYNC_LEAD_HOURS:
            continue
        ff = rec.get("FF", {})
        pu_score = ff.get("extra", {}).get("pu_ranking_score")
        if pu_score is None:
            continue
        out[rec["cell_id"]] = {
            "pu_score": pu_score,
            "model_version": ff.get("model_version"),
        }
    return out


def main() -> int:
    if not UNIFIED_PATH.exists():
        print(f"SKIP: {UNIFIED_PATH} does not exist -- nothing to sync")
        return 0
    if not GRID_PATH.exists():
        print(f"SKIP: {GRID_PATH} does not exist -- nothing to sync into")
        return 0

    with open(UNIFIED_PATH, encoding="utf-8") as f:
        unified = json.load(f)
    with open(GRID_PATH, encoding="utf-8") as f:
        grid = json.load(f)

    cb_lookup = build_cb_lookup(unified)
    ff_lookup = build_ff_research_status_lookup(unified)
    if not cb_lookup and not ff_lookup:
        print("SKIP: no real CB probabilities or FF research scores found in the unified "
              "artifact (all UNAVAILABLE for this lead) -- leaving pan_india_grid.json untouched")
        return 0

    cells = grid.get("grid_cells") or grid.get("cells") or []
    n_cb_updated = 0
    n_ff_updated = 0
    for cell in cells:
        cid = cell.get("cell_id")
        cb = cb_lookup.get(cid)
        if cb is not None:
            cell["cloudburst_probability"] = cb["probability"]
            cell["cloudburst_probability_source"] = "panindia_cb_v1 (Phase 21, LODO-validated XGBoost, REAL trained model)"
            cell["cloudburst_probability_model_version"] = cb["model_version"]
            cell["cloudburst_source_status"] = cb["source_status"]
            n_cb_updated += 1
        ff = ff_lookup.get(cid)
        if ff is not None:
            cell["flash_flood_research_status"] = "RESEARCH_ONLY_PU_SCORE_AVAILABLE"
            cell["flash_flood_research_pu_score"] = ff["pu_score"]
            cell["flash_flood_research_model_version"] = ff["model_version"]
        else:
            cell["flash_flood_research_status"] = "NO_RESEARCH_COVERAGE"

    print(f"Synced real panindia_cb_v1 CB probabilities into {n_cb_updated}/{len(cells)} cells, "
          f"FF research-only status into all {len(cells)} cells ({len(ff_lookup)} with a real "
          f"PU score) of {GRID_PATH} (lead_hours={SYNC_LEAD_HOURS})")

    tmp_path = GRID_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(grid, f)
    tmp_path.replace(GRID_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
