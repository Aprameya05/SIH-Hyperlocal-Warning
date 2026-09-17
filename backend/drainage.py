#!/usr/bin/env python3
"""
DEM Flow Routing -- Flash Flood Drainage Network
Downloads SRTM 30m tiles for the BLR region, computes flow direction
and flow accumulation using pysheds, then writes drainage.geojson
consumed by the dashboard as a line overlay.

Run: python drainage.py
Output: data/drainage.geojson

Dependencies: pip install pysheds rasterio requests numpy
SRTM tiles are free from NASA EARTHDATA (no auth for 1-arc-second SRTM).
"""

import json
import math
import os
import sys
import urllib.request
import zipfile
import tempfile
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "data"
OUT_FILE = OUT_DIR / "drainage.geojson"

# BLR region bounding box (slightly wider than dashboard BBOX)
REGION = {"S": 11.5, "N": 15.5, "W": 75.5, "E": 80.0}

# SRTM tile naming: N{lat}E{lon} for 1x1 degree tiles
# OpenTopography provides free SRTM 1-arc-second (30m) tiles
# URL: https://portal.opentopography.org/API/globaldem?demtype=SRTMGL1&south=...
OPENTOPO_API = "https://portal.opentopography.org/API/globaldem"

# Flow accumulation threshold -- cells above this become drainage channels
# At 30m resolution, ~500 cells = ~450000 m2 catchment
FLOW_ACCUM_THRESHOLD = 500

# Simplify polylines to reduce GeoJSON size (degrees)
SIMPLIFY_TOLERANCE = 0.002


def download_dem(south, north, west, east, dest_path: Path) -> bool:
    """Download SRTM 30m DEM from OpenTopography for the given bbox."""
    url = (
        f"{OPENTOPO_API}?demtype=SRTMGL1"
        f"&south={south}&north={north}&west={west}&east={east}"
        f"&outputFormat=GTiff"
    )
    print(f"  Downloading SRTM DEM: {south}N {north}N {west}E {east}E")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SIH-HyperLocalWarning/1.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            if resp.status != 200:
                print(f"  OpenTopography returned HTTP {resp.status}")
                return False
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        size_mb = dest_path.stat().st_size / (1024 * 1024)
        print(f"  DEM downloaded: {size_mb:.1f} MB -> {dest_path}")
        return True
    except Exception as e:
        print(f"  DEM download failed: {e}")
        return False


def flow_route_and_extract(dem_path: Path) -> list:
    """
    Run pysheds flow routing on the DEM.
    Returns a list of LineString coordinate arrays (drainage channels).
    """
    try:
        from pysheds.grid import Grid
        import numpy as np
    except ImportError:
        print("  pysheds or numpy not installed -- run: pip install pysheds rasterio numpy")
        sys.exit(1)

    print("  Loading DEM into pysheds...")
    grid = Grid.from_raster(str(dem_path))
    dem = grid.read_raster(str(dem_path))

    print("  Conditioning DEM (fill pits, resolve flats)...")
    pit_filled = grid.fill_pits(dem)
    flooded = grid.fill_depressions(pit_filled)
    inflated = grid.resolve_flats(flooded)

    print("  Computing flow direction (D8)...")
    fdir = grid.flowdir(inflated)

    print("  Computing flow accumulation...")
    acc = grid.accumulation(fdir)

    print(f"  Flow accumulation range: {acc.min():.0f} to {acc.max():.0f}")

    # Mask to threshold
    drainage_mask = acc >= FLOW_ACCUM_THRESHOLD

    print(f"  Drainage cells above threshold {FLOW_ACCUM_THRESHOLD}: {drainage_mask.sum()}")

    # Convert raster mask to vector lines
    # We trace each high-accumulation cell center to its downstream neighbor
    # using the D8 flow direction values

    # D8 direction encoding in pysheds (powers of 2, clockwise from E)
    # 64 128 1
    # 32   2
    # 16  8  4
    dir_to_offset = {
        1: (0, 1),   # E
        2: (1, 1),   # SE
        4: (1, 0),   # S
        8: (1, -1),  # SW
        16: (0, -1), # W
        32: (-1, -1),# NW
        64: (-1, 0), # N
        128: (-1, 1),# NE
    }

    # Get geotransform info
    transform = grid.affine
    # affine: (col_off, x_scale, x_skew, row_off, y_skew, y_scale)
    # For us: transform * (col, row) = (lon, lat)

    def pixel_to_lonlat(row, col):
        lon = transform.c + col * transform.a + row * transform.b
        lat = transform.f + col * transform.d + row * transform.e
        return lon, lat

    # Build list of line segments from drainage cells
    segments = []
    rows, cols = drainage_mask.shape
    fdir_np = np.array(fdir)
    mask_np = np.array(drainage_mask)
    acc_np = np.array(acc)

    # Sample every other cell to keep output size manageable
    step = 2
    for r in range(0, rows, step):
        for c in range(0, cols, step):
            if not mask_np[r, c]:
                continue
            d = int(fdir_np[r, c])
            if d not in dir_to_offset:
                continue
            dr, dc = dir_to_offset[d]
            nr, nc = r + dr, c + dc
            if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                continue
            if not mask_np[nr, nc]:
                continue
            lon1, lat1 = pixel_to_lonlat(r, c)
            lon2, lat2 = pixel_to_lonlat(nr, nc)
            acc_val = float(acc_np[r, c])
            segments.append(((lon1, lat1), (lon2, lat2), acc_val))

    print(f"  Extracted {len(segments)} drainage segments")
    return segments


def simplify_coords(coords: list, tolerance: float) -> list:
    """Ramer-Douglas-Peucker line simplification."""
    if len(coords) <= 2:
        return coords

    def point_line_dist(p, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if dx == 0 and dy == 0:
            return math.sqrt((p[0]-a[0])**2 + (p[1]-a[1])**2)
        t = max(0, min(1, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / (dx*dx + dy*dy)))
        proj = (a[0]+t*dx, a[1]+t*dy)
        return math.sqrt((p[0]-proj[0])**2 + (p[1]-proj[1])**2)

    max_dist = 0
    max_idx = 0
    for i in range(1, len(coords) - 1):
        d = point_line_dist(coords[i], coords[0], coords[-1])
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > tolerance:
        left = simplify_coords(coords[:max_idx+1], tolerance)
        right = simplify_coords(coords[max_idx:], tolerance)
        return left[:-1] + right
    else:
        return [coords[0], coords[-1]]


def segments_to_geojson(segments: list) -> dict:
    """Convert (p1, p2, acc) segments to a GeoJSON FeatureCollection."""
    features = []
    # Classify by flow accumulation for styling
    for (lon1, lat1), (lon2, lat2), acc_val in segments:
        # Stream order proxy: large acc = main river, small = tributary
        if acc_val >= 50000:
            order = "major"
            width_class = 3
        elif acc_val >= 10000:
            order = "secondary"
            width_class = 2
        else:
            order = "tributary"
            width_class = 1

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[round(lon1, 6), round(lat1, 6)],
                                 [round(lon2, 6), round(lat2, 6)]],
            },
            "properties": {
                "flow_accum": round(acc_val),
                "stream_order": order,
                "width_class": width_class,
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "source": "SRTM 30m DEM via OpenTopography + pysheds D8 flow routing",
            "threshold_cells": FLOW_ACCUM_THRESHOLD,
            "region": REGION,
        },
    }


def run():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        dem_path = Path(tmp) / "blr_dem.tif"

        ok = download_dem(
            REGION["S"], REGION["N"],
            REGION["W"], REGION["E"],
            dem_path,
        )
        if not ok:
            print("FATAL: DEM download failed. Check network and OpenTopography availability.")
            sys.exit(1)

        segments = flow_route_and_extract(dem_path)

        if not segments:
            print("WARNING: No drainage segments extracted -- check threshold or DEM quality")

        geojson = segments_to_geojson(segments)
        OUT_FILE.write_text(json.dumps(geojson, separators=(",", ":")))
        print(f"  Written {len(geojson['features'])} features -> {OUT_FILE}")

    print("Drainage routing complete.")


if __name__ == "__main__":
    run()
