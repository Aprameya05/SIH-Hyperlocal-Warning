#!/usr/bin/env python3
"""
scripts/acquire_panindia_terrain.py
======================================
Phase 25 Priority 3 -- the exact acquisition package to populate real
SRTM elevation/slope/flood-susceptibility for all 992 canonical
pan-India cells, reusing the EXISTING, already-working tile-fetch and
terrarium-decode primitives from fetch_dem_terrain.py (the Benguluru-
only fetcher) rather than reimplementing tile math or PNG decoding.

HISTORICAL NOTE, CORRECTED 2026-10-08 (same class of stale-network-
assumption bug already found and fixed for NOMADS during the AWS GFS
integration pass): earlier phases (0-22) found s3.amazonaws.com's
elevation-tiles-prod bucket returning 403 from the sandbox then in use
and documented it as a standing block. Re-verified live on 2026-10-08
from the environment this script now actually runs in (the user's own
Windows machine): the source is genuinely reachable (200 OK, real tile
bytes). A full --resume run recovered all 639 previously-blocked
cells with 0 failures, bringing DEM coverage to a real 992/992 (see
docs/PHASE_22_TERRAIN_HYDROLOGY.md / git history for the before/after
counts). The egress block described below was real for the sandbox
that observed it at the time, not a permanent property of the source
-- always re-verify with check_connectivity-style logic before
assuming a network source is still blocked.

EXACT COMMAND TO RUN ON A NORMAL NETWORK MACHINE (Windows, from the
repo root, with the existing Python environment that already has
fetch_dem_terrain.py's dependencies -- stdlib only, no extra installs
needed):

    python scripts\\acquire_panindia_terrain.py

Optional flags:
    --resume            skip cells whose cache file already exists (default: on)
    --cells-limit N     process only the first N cells (for a quick smoke test)
    --output PATH       override data/pan_india_terrain_srtm_992.json

What it does:
  1. Loads the canonical 992-cell grid from data/pan_india_grid.json
     (ground truth for cell identity -- never invents a cell).
  2. For each cell, computes the SRTM terrarium tile(s) covering it
     (deg_to_tile / pixel_to_lat_lon, imported unchanged from
     fetch_dem_terrain.py) and downloads ONLY tiles not already cached
     under data/_terrain_tile_cache/ (avoids duplicate downloads across
     runs AND across cells that share a tile at this zoom level).
  3. Decodes elevation (decode_terrarium_png, imported unchanged),
     computes slope_deg (compute_slope_deg, imported unchanged) and a
     DEM-derived flood-susceptibility PROXY (compute_flood_susceptibility,
     imported unchanged, category explicitly tagged "DERIVED", never
     "OBSERVED" -- it's a heuristic over real elevation/slope, not a
     ground-truth flood label).
  4. Validates each downloaded tile (checks PNG magic bytes + non-empty
     IDAT before accepting it; a corrupt/empty tile is retried up to 2x
     then marked FAILED_DOWNLOAD for that cell -- never silently
     defaulted to a plausible elevation).
  5. Writes a deterministic, cell-ordered
     data/pan_india_terrain_srtm_992.json with per-cell provenance:
     {"category": "OBSERVED", "status": "REAL_SRTM"} for a cell whose
     tile downloaded and decoded successfully, or
     {"category": "MISSING", "status": "FAILED_DOWNLOAD"|"NOT_ATTEMPTED"}
     otherwise -- never fabricated for a failed cell.

Its retry/timeout hardening (HARD_TIMEOUT_S, max_retries, structured
failure logging -- see get_tile_cached/fetch_tile_bounded below) is
exercised by tests/test_phase26_terrain_hardening.py against a
monkeypatched always-failing fetch, so the failure-path logic is
covered without needing a real network block to exist.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fetch_dem_terrain import (  # noqa: E402 -- reused unchanged, not reimplemented
    deg_to_tile, pixel_to_lat_lon, fetch_tile, decode_terrarium_png,
    compute_slope_deg, compute_flood_susceptibility, TILE_URL,
)
import queue
import threading

ZOOM = 11  # same zoom fetch_dem_terrain.py already uses successfully for Bengaluru
TILE_CACHE_DIR = REPO_ROOT / "data" / "_terrain_tile_cache"
GRID_PATH = REPO_ROOT / "data" / "pan_india_grid.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "pan_india_terrain_srtm_992.json"
FAILURE_LOG_PATH = REPO_ROOT / "data" / "_terrain_acquisition_failures.jsonl"

# Phase 26 Part A: fetch_dem_terrain.fetch_tile() already passes
# timeout=30 to urlopen(), but a real run on the user's Windows machine
# hung at socket.connect() past that timeout and needed Ctrl+C --
# meaning the socket-level timeout alone is not trustworthy on every
# network path (e.g. a firewall that silently drops SYN packets rather
# than refusing the connection, which some OS/network stacks don't
# surface as a timeout to Python's socket layer promptly). HARD_TIMEOUT_S
# is therefore a second, independent timeout enforced with a dedicated,
# throwaway daemon thread PER CALL (never a shared worker pool -- a
# shared single-worker pool would let one truly-hung tile permanently
# starve every tile fetched after it in the same process). A thread that
# never returns is simply abandoned (daemon=True, so it never blocks
# process exit); this guarantees forward progress through all 992 cells
# even if an individual connect() never returns at all.
HARD_TIMEOUT_S = 10


def _log_failure(cell_id: str, endpoint: str, reason: str, attempt: int):
    FAILURE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FAILURE_LOG_PATH, "a") as f:
        f.write(json.dumps({
            "cell_id": cell_id, "endpoint": endpoint, "reason": reason,
            "attempt": attempt, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }) + "\n")


def fetch_tile_bounded(z: int, x: int, y: int, cell_id: str, attempt: int) -> bytes:
    """Calls the EXISTING, unmodified fetch_tile() but never lets it
    block the run past HARD_TIMEOUT_S, regardless of what the socket
    layer does. Raises on any failure (timeout, network error, bad
    response) -- never returns a placeholder. Uses one throwaway daemon
    thread per call so a hung fetch cannot starve later cells."""
    endpoint = TILE_URL.format(z=z, x=x, y=y)
    result_q: queue.Queue = queue.Queue(maxsize=1)

    def _worker():
        try:
            result_q.put(("ok", fetch_tile(z, x, y)))
        except Exception as exc:  # noqa: BLE001
            result_q.put(("error", exc))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        status, payload = result_q.get(timeout=HARD_TIMEOUT_S)
    except queue.Empty:
        _log_failure(cell_id, endpoint, "HARD_TIMEOUT_NO_RESPONSE (socket.connect() likely hung)", attempt)
        raise TimeoutError(f"fetch_tile({z},{x},{y}) exceeded HARD_TIMEOUT_S={HARD_TIMEOUT_S}s")
    if status == "error":
        _log_failure(cell_id, endpoint, f"{type(payload).__name__}: {payload}", attempt)
        raise payload
    return payload


def load_canonical_cells() -> list[dict]:
    with open(GRID_PATH) as f:
        grid = json.load(f)
    from regrid import cell_id_for
    return [{"cell_id": cell_id_for(c["lat"], c["lon"]), "lat": c["lat"], "lon": c["lon"]}
            for c in grid["grid_cells"]]


def _tile_cache_path(z: int, x: int, y: int) -> Path:
    return TILE_CACHE_DIR / f"{z}_{x}_{y}.png"


def _valid_png(b: bytes) -> bool:
    return len(b) > 16 and b[:8] == b"\x89PNG\r\n\x1a\n"


def get_tile_cached(z: int, x: int, y: int, cell_id: str, max_retries: int = 2) -> bytes | None:
    """Never re-downloads a tile already validated on disk (resume
    support). Validates a newly-downloaded tile before caching it; a
    failed download is never retried more than max_retries times, never
    silently accepted if corrupt, and never blocks past
    HARD_TIMEOUT_S * max_retries total for this tile (see
    fetch_tile_bounded) -- it returns None (MISSING) rather than hang."""
    cache_path = _tile_cache_path(z, x, y)
    if cache_path.exists():
        data = cache_path.read_bytes()
        if _valid_png(data):
            return data
        cache_path.unlink()  # cached file was corrupt -- re-fetch below

    for attempt in range(max_retries):
        try:
            data = fetch_tile_bounded(z, x, y, cell_id, attempt)
        except Exception:
            continue
        if _valid_png(data):
            TILE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
            return data
    return None


def acquire_cell(cell: dict) -> dict:
    lat, lon = cell["lat"], cell["lon"]
    tx, ty = deg_to_tile(lat, lon, ZOOM)
    png = get_tile_cached(ZOOM, tx, ty, cell["cell_id"])
    if png is None:
        return {**cell, "category": "MISSING", "status": "FAILED_DOWNLOAD",
                "elevation_m": None, "slope_deg": None, "flood_susceptibility_dem_proxy": None}

    try:
        elev_grid = decode_terrarium_png(png)
    except Exception as exc:  # noqa: BLE001
        return {**cell, "category": "MISSING", "status": f"DECODE_ERROR: {type(exc).__name__}",
                "elevation_m": None, "slope_deg": None, "flood_susceptibility_dem_proxy": None}

    n = 2 ** ZOOM
    lon_deg_per_tile = 360.0 / n
    meters_per_pixel = (lon_deg_per_tile / 256.0) * 111_320.0 * max(0.2, abs(__import__("math").cos(__import__("math").radians(lat))))
    slope_grid = compute_slope_deg(elev_grid, meters_per_pixel)

    px_lon_frac = ((lon + 180.0) / 360.0 * n) - tx
    import math as _m
    lat_r = _m.radians(lat)
    py_frac = ((1.0 - _m.log(_m.tan(lat_r) + 1.0 / _m.cos(lat_r)) / _m.pi) / 2.0 * n) - ty
    px = max(1, min(254, int(px_lon_frac * 256)))
    py = max(1, min(254, int(py_frac * 256)))

    elevation = elev_grid[py][px]
    slope = slope_grid[py][px]
    flat_elevs = [v for row in elev_grid for v in row]
    susceptibility = compute_flood_susceptibility(elevation, slope, min(flat_elevs),
                                                   max(flat_elevs) - min(flat_elevs))
    return {**cell, "category": "OBSERVED", "status": "REAL_SRTM",
            "elevation_m": round(elevation, 1), "slope_deg": round(slope, 2),
            "flood_susceptibility_dem_proxy": {"value": susceptibility, "category": "DERIVED"}}


def run(cells_limit: int | None, output_path: Path, resume: bool):
    cells = load_canonical_cells()
    if cells_limit:
        cells = cells[:cells_limit]

    existing = {}
    if resume and output_path.exists():
        with open(output_path) as f:
            prior = json.load(f)
        existing = {c["cell_id"]: c for c in prior.get("cells", []) if c.get("status") == "REAL_SRTM"}

    results = []
    n_downloaded = n_cached = n_failed = 0
    t0 = time.time()
    for cell in cells:
        if cell["cell_id"] in existing:
            results.append(existing[cell["cell_id"]])
            n_cached += 1
            continue
        rec = acquire_cell(cell)
        results.append(rec)
        if rec["status"] == "REAL_SRTM":
            n_downloaded += 1
        else:
            n_failed += 1

    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "zoom": ZOOM,
        "n_cells": len(results),
        "n_downloaded_this_run": n_downloaded,
        "n_resumed_from_cache": n_cached,
        "n_failed": n_failed,
        "elapsed_seconds": round(time.time() - t0, 1),
        "cells": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(out, f, indent=1)
    print(f"Wrote {output_path}: {n_downloaded} downloaded, {n_cached} resumed, {n_failed} failed "
          f"/ {len(results)} total cells")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true", default=True)
    ap.add_argument("--cells-limit", type=int, default=None)
    ap.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    args = ap.parse_args()
    run(args.cells_limit, Path(args.output), args.resume)
