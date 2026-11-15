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
sys.path.insert(0, str(REPO_ROOT))
from backend import forecast_integrity as integrity  # noqa: E402

UNIFIED_PATH = REPO_ROOT / "data" / "unified_forecast.json"
GRID_PATH = REPO_ROOT / "data" / "pan_india_grid.json"
SYNC_LEAD_HOURS = 2  # CB is daily-resolution; any lead carries the same value


def build_cb_lookup(unified: dict) -> dict[str, dict]:
    """cell_id -> daily CB outlook, from the integrity layer's daily_outlook only.

    M0: the unified artifact's per-lead CB values are a single daily aggregate copied to five lead
    slots and are withheld (probability null) by backend/forecast_integrity.py. The honest source is
    daily_outlook, and only entries that are CURRENT are synced. Legacy artifacts are sanitised first,
    so an old or replayed artifact can never feed the live grid."""
    unified = integrity.sanitize_artifact(unified)
    out = {}
    for cid, entry in (unified.get("daily_outlook") or {}).items():
        cb = entry.get("CB") or {}
        if cb.get("probability") is None or cb.get("temporal_status") != integrity.TemporalStatus.CURRENT:
            continue
        out[cid] = {
            "probability": cb["probability"],
            "model_version": cb.get("model_version"),
            "source_status": cb.get("source_status"),
            "model_init_time_utc": cb.get("model_init_time_utc"),
            "window_start_utc": cb.get("window_start_utc"),
            "window_end_utc": cb.get("window_end_utc"),
            "event_class": cb.get("event_class"),
        }
    return out


def build_ff_research_status_lookup(unified: dict) -> dict[str, dict]:
    """cell_id -> {pu_score, model_version, basis} ONLY where a real PU ranking score exists.

    M0: that score is computed from the fixed 2024-08-01 rainfall cycle, so it is read from the
    withheld value's historical_reference and always carried with a HISTORICAL_REPLAY designation
    and its basis time. It is additive research metadata; it never touches flash_flood_probability."""
    unified = integrity.sanitize_artifact(unified)
    out = {}
    for rec in unified.get("records", []):
        if rec.get("lead_hours") != SYNC_LEAD_HOURS:
            continue
        ff = rec.get("FF", {}) or {}
        ref = ff.get("historical_reference") or {}
        pu_score = ref.get("pu_ranking_score")
        if pu_score is None and ff.get("temporal_status") == integrity.TemporalStatus.CURRENT:
            pu_score = (ff.get("extra") or {}).get("pu_ranking_score")
        if pu_score is None:
            continue
        out[rec["cell_id"]] = {
            "pu_score": pu_score,
            "model_version": ff.get("model_version"),
            "designation": ref.get("designation") or "CURRENT",
            "basis_time_utc": ref.get("model_init_time_utc"),
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

    def sync_cell_list(cells):
        """Applies the CB/FF sync to one grid_cells list in place. Returns
        (n_cb_updated, n_ff_updated) for that list.

        2026-10-10: index.html's own loader comment is explicit that
        d.forecasts[primary].grid_cells -- NOT the top-level d.grid_cells --
        is "the actual source of truth" the frontend reads (top-level
        grid_cells is a compatibility mirror used only as a fallback when
        forecasts[] is absent). The first version of this function only
        updated the top-level mirror, so these fields were correctly present
        in data/pan_india_grid.json's top-level array but silently absent
        from the nested array the deployed UI actually renders -- confirmed
        via a live browser check against the real production site showing
        'FF research coverage: UNAVAILABLE' for IND_14.0_74.0 even though
        that cell has a real PU score. Every grid_cells list in the artifact
        (top-level mirror AND each forecasts[] entry's own list) must be
        updated identically so neither path can silently diverge again.
        """
        n_cb, n_ff = 0, 0
        for cell in cells:
            cid = cell.get("cell_id")
            cb = cb_lookup.get(cid)
            if cb is not None:
                cell["cloudburst_probability"] = cb["probability"]
                cell["cloudburst_probability_source"] = "panindia_cb_v1 (Phase 21, LODO-validated XGBoost, REAL trained model)"
                cell["cloudburst_probability_model_version"] = cb["model_version"]
                cell["cloudburst_source_status"] = cb["source_status"]
                # M0 terminology/lead honesty: a daily heavy-rain-day score, not a lead-specific
                # forecast and not a verified hourly cloudburst.
                cell["cloudburst_event_class"] = cb.get("event_class")
                cell["cloudburst_lead_specific"] = False
                cell["cloudburst_model_init_time_utc"] = cb.get("model_init_time_utc")
                cell["cloudburst_window_start_utc"] = cb.get("window_start_utc")
                cell["cloudburst_window_end_utc"] = cb.get("window_end_utc")
                cell["cloudburst_value_type"] = "MODEL_SCORE_UNCALIBRATED"
                n_cb += 1
            ff = ff_lookup.get(cid)
            if ff is not None:
                cell["flash_flood_research_status"] = "RESEARCH_ONLY_PU_SCORE_AVAILABLE"
                cell["flash_flood_research_pu_score"] = ff["pu_score"]
                cell["flash_flood_research_model_version"] = ff["model_version"]
                cell["flash_flood_research_designation"] = ff["designation"]
                cell["flash_flood_research_basis_time_utc"] = ff["basis_time_utc"]
            else:
                cell["flash_flood_research_status"] = "NO_RESEARCH_COVERAGE"
        return n_cb, n_ff

    top_level_cells = grid.get("grid_cells") or grid.get("cells") or []
    n_cb_updated, n_ff_updated = sync_cell_list(top_level_cells)

    n_forecast_lists = 0
    for forecast_entry in grid.get("forecasts") or []:
        nested_cells = forecast_entry.get("grid_cells")
        if nested_cells:
            sync_cell_list(nested_cells)
            n_forecast_lists += 1

    print(f"Synced real panindia_cb_v1 CB probabilities into {n_cb_updated}/{len(top_level_cells)} "
          f"top-level cells, FF research-only status into all {len(top_level_cells)} top-level cells "
          f"({len(ff_lookup)} with a real PU score), and mirrored the same sync into "
          f"{n_forecast_lists} forecasts[].grid_cells list(s) (the array the deployed frontend "
          f"actually reads) of {GRID_PATH} (lead_hours={SYNC_LEAD_HOURS})")

    if "forecasts" in grid or "generated_at_utc" in grid:
        integrity.annotate_grid(grid)       # refresh hazard_value_types / lead semantics after the sync

    tmp_path = GRID_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(grid, f)
    tmp_path.replace(GRID_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
