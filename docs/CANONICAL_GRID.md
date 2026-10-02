# DRIFT — Canonical Pan-India Grid (Phase 3) — 2026-09-30

## Canonical Definition

Per your instruction, the **existing, deployed 992-cell grid** is formalized as the single canonical spatial grid for this project — no second grid is created.

| Property | Value |
|---|---|
| Bounds | S=6.0, N=37.0, W=68.0, E=98.0 (mainland India bounding box) |
| Grid step | **1.0 degree** |
| Cell count | 992 (32 lat x 31 lon) |
| Lat values | 6.0, 7.0, ..., 37.0 (32 values) |
| Lon values | 68.0, 69.0, ..., 98.0 (31 values) |
| Ordering | Row-major, latitude outer loop, longitude inner loop (matches `backend/pipeline.py`'s own nested `for lat in lats: for lon in lons:`) |
| Coordinate convention | Cell center, not corner — a cell's `(lat, lon)` is its own point sample, not a bounding-box corner |
| Cell bounds/geometry | Implicit only: a cell's box is `[lat-0.5, lat+0.5] x [lon-0.5, lon+0.5]` at 1.0 deg step. Not materialized as an explicit polygon anywhere in the current data. |
| **cell_id** | **Did not exist before this pass.** `data/pan_india_grid.json` cells carry only `lat`/`lon`, no id field. This pass defines the canonical convention: `cell_id = f"IND_{lat:.1f}_{lon:.1f}"` (e.g. `IND_13.0_78.0`), deterministic from lat/lon, used only in the new label-engine scripts below — **the production grid file itself is not modified to add this field**, per "do not modify production inference." |

## CRITICAL FINDING: The Live Grid File Does Not Match The Current Code

This is the single most important finding of Phase 3, and it was already partially flagged in `regrid.py`'s own docstring from an earlier pass ("GRID_STEP = 0.25 by default for the GFS-native path, 1.0 for the deployed pan_india_grid.json") — Phase 3 makes it explicit and traces it to source:

- **`backend/pipeline.py` (current code, line 52)**: `GRID_STEP = 0.25`. Git history shows this was set in commit `7d52a42 "Upgrade grid to 0.25-degree sub-district resolution"`, superseding an earlier `GRID_STEP = 1.0`. At 0.25 deg over the same bounds, the loop at lines 573-575 would generate **15,125 cells** (125 lat x 121 lon), not 992.
- **`data/pan_india_grid.json` (the actual, live, on-disk file)**: `"grid_step_deg": 1.0`, `"n_cells": 992`, `"generated_at_utc": "2026-09-30T11:23:22Z"`. Verified directly by reading the file and counting its unique lat/lon values (32 x 31 = 992, confirmed by direct computation, not just trusting the `n_cells` field).

**In other words: the file everyone (the frontend, `location_engine.py`, this whole session's location-aware work) has been treating as "the 992-cell production grid" was generated at the OLD 1.0-degree resolution, but the code that would run on the next scheduled `update_grid.yml` trigger has already been upgraded to 0.25 degrees and would silently replace it with a 15,125-cell file.** This is not a hypothetical — `update_grid.yml` runs 4x/day; the next run overwrites `pan_india_grid.json` with whatever `backend/pipeline.py` currently produces.

**This was not fixed in this pass** — "do not modify production inference" — but it needs a decision from you before Phase 5 (dataset construction) can safely assume which grid definition it's building against. The canonical grid documented above (992 cells, 1.0 deg) is what's live *right now*; it may not still be live after the next scheduled pipeline run.

## Extended Schema (new fields, defined for the label engine, not retrofitted onto production)

| Field | Populated? | Source | Notes |
|---|---|---|---|
| `cell_id` | Yes, in new scripts only | Derived (`IND_{lat}_{lon}`) | Not in production `pan_india_grid.json` |
| `lat`, `lon` | Yes | Existing production field | Unchanged |
| `geometry/bounds` | No | — | Not materialized; implicit `+-0.5deg` box at the current 1.0deg step (would be `+-0.125deg` if/when the 0.25deg code path becomes live — see finding above) |
| `elevation`, `slope` | Only for cells inside the Bengaluru terrain file's bounds | `data/blr_terrain.json` via `terrain_lookup.py` | `null` for the ~980 cells outside Bengaluru — **not fabricated for those cells** |
| `drainage/catchment` | No | — | `data/catchment_characteristics_indofloods.csv` does not exist in this repo (confirmed again this pass) |
| `land/sea mask` | No | — | No land-sea mask exists anywhere in this repo; ~15-20% of the 992-cell bounding box falls over ocean (Arabian Sea, Bay of Bengal) with no flag distinguishing those cells today |

## Regridding — `regrid.py` Audit and Extension

`regrid.py` (written 3 passes ago) already provides `regrid_point()` with nearest-neighbor and bilinear methods, safe fallback behavior (never extrapolates, never interpolates across a missing neighbor), and a `RegridResult` carrying `method_used`, `method_requested`, `fallback_reason`, `source_resolution_deg`, `target_resolution_deg`, `distance_deg`. Verified still correct by re-reading the full 191-line file.

**Gap found and fixed this pass**: `RegridResult` did not carry `source`, `source_timestamp`, or `target_cell_id` — three of the seven provenance fields this phase requires. Rather than change `regrid_point()`'s signature (which 8 existing passing tests depend on), a new, additive wrapper function `regrid_to_cell()` was appended to `regrid.py`: it calls the existing `regrid_point()` unchanged, then wraps the result with the three missing fields plus an explicit `missing_flag`/`missing_reason` pair (derived from `RegridResult.available`/`fallback_reason`, not new logic). All 8 existing tests in `test_regrid.py` still pass unmodified; 6 new tests for `regrid_to_cell()` are in `tests/test_canonical_grid.py`.
