#!/usr/bin/env python3
"""
fetch_dem_terrain.py
Downloads SRTM elevation tiles from the AWS Terrain Tiles endpoint
(terrarium encoding), computes slope and flash-flood susceptibility
for the Bengaluru/VOBL region, and writes data/blr_terrain.json.

Re-runs only when the output file is older than 30 days -- terrain
does not change. Safe to call with continue-on-error: true in CI.

Output schema (data/blr_terrain.json):
{
  "generated_at": "<ISO timestamp>",
  "bbox": {"lat_min": 12.5, "lat_max": 14.0, "lon_min": 77.0, "lon_max": 78.5},
  "resolution_deg": 0.01,
  "grid": [
    {
      "lat": 12.50, "lon": 77.00,
      "elevation_m": 834.2,
      "slope_deg": 1.4,
      "flood_susceptibility": 0.72
    },
    ...
  ]
}
"""

import json
import math
import os
import sys
import struct
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BBOX = {
    "lat_min": 12.5,
    "lat_max": 14.0,
    "lon_min": 77.0,
    "lon_max": 78.5,
}
RESOLUTION_DEG = 0.01          # ~1.1 km grid spacing
ZOOM = 11                      # SRTM zoom level (256px tiles, ~78m/px at eq)
OUTPUT_PATH = Path("data/blr_terrain.json")
MAX_AGE_DAYS = 30
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"

# ---------------------------------------------------------------------------
# Tile math helpers
# ---------------------------------------------------------------------------

def deg_to_tile(lat_deg: float, lon_deg: float, zoom: int):
    """Return (tile_x, tile_y) for a lat/lon at the given zoom level."""
    lat_r = math.radians(lat_deg)
    n = 2 ** zoom
    x = int((lon_deg + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def tile_to_lat_lon(x: int, y: int, zoom: int):
    """Return (lat, lon) for the NW corner of a tile."""
    n = 2 ** zoom
    lon_deg = x / n * 360.0 - 180.0
    lat_r = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_deg = math.degrees(lat_r)
    return lat_deg, lon_deg


def pixel_to_lat_lon(x: int, y: int, tile_x: int, tile_y: int, zoom: int):
    """Return (lat, lon) for a pixel within a tile."""
    n = 2 ** zoom
    lon_deg = (tile_x + x / 256.0) / n * 360.0 - 180.0
    lat_r = math.atan(math.sinh(math.pi * (1 - 2 * (tile_y + y / 256.0) / n)))
    lat_deg = math.degrees(lat_r)
    return lat_deg, lon_deg

# ---------------------------------------------------------------------------
# Tile download + terrarium decode
# ---------------------------------------------------------------------------

def fetch_tile(z: int, x: int, y: int) -> bytes:
    url = TILE_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": "SIH-Hyperlocal-WeatherWarning/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def decode_terrarium_png(png_bytes: bytes):
    """
    Decode a terrarium-encoded PNG into a 256x256 float32 elevation grid.
    Terrarium encoding: elevation_m = R * 256 + G + B / 256 - 32768
    Returns a list of lists [row][col] = elevation_m.
    Uses only stdlib (struct + zlib) -- no Pillow dependency.
    """
    import zlib

    # Parse PNG structure manually
    if png_bytes[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError("Not a valid PNG file")

    pos = 8
    idat_chunks = []
    width = height = 0

    while pos < len(png_bytes):
        length = struct.unpack(">I", png_bytes[pos:pos+4])[0]
        chunk_type = png_bytes[pos+4:pos+8]
        data = png_bytes[pos+8:pos+8+length]
        pos += 12 + length

        if chunk_type == b'IHDR':
            width = struct.unpack(">I", data[0:4])[0]
            height = struct.unpack(">I", data[4:8])[0]
            bit_depth = data[8]
            color_type = data[9]
            if bit_depth != 8 or color_type != 2:  # must be 8-bit RGB
                raise ValueError(f"Unsupported PNG: bit_depth={bit_depth} color_type={color_type}")
        elif chunk_type == b'IDAT':
            idat_chunks.append(data)
        elif chunk_type == b'IEND':
            break

    raw = zlib.decompress(b''.join(idat_chunks))

    # PNG scanline filter
    stride = width * 3  # RGB, 3 bytes/pixel
    grid = []
    row_out = 0
    raw_pos = 0
    prev_row = bytearray(stride)

    for _ in range(height):
        filter_type = raw[raw_pos]
        raw_pos += 1
        row = bytearray(raw[raw_pos:raw_pos + stride])
        raw_pos += stride

        if filter_type == 0:   # None
            pass
        elif filter_type == 1:  # Sub
            for i in range(3, stride):
                row[i] = (row[i] + row[i - 3]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                row[i] = (row[i] + prev_row[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = row[i - 3] if i >= 3 else 0
                up = prev_row[i]
                row[i] = (row[i] + (left + up) // 2) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                a = row[i - 3] if i >= 3 else 0
                b = prev_row[i]
                c = prev_row[i - 3] if i >= 3 else 0
                p = a + b - c
                pa = abs(p - a); pb = abs(p - b); pc = abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[i] = (row[i] + pr) & 0xFF
        else:
            raise ValueError(f"Unknown PNG filter type {filter_type}")

        elevations_row = []
        for col in range(width):
            R = row[col * 3]
            G = row[col * 3 + 1]
            B = row[col * 3 + 2]
            elev = R * 256 + G + B / 256.0 - 32768.0
            elevations_row.append(elev)

        grid.append(elevations_row)
        prev_row = bytearray(row)

    return grid  # [row][col], row 0 = north


# ---------------------------------------------------------------------------
# Slope computation
# ---------------------------------------------------------------------------

def compute_slope_deg(elev_grid: list, meters_per_pixel: float) -> list:
    """
    Compute slope magnitude in degrees from an elevation grid.
    Uses simple finite differences (Sobel-style central difference).
    Returns grid of same shape.
    """
    rows = len(elev_grid)
    cols = len(elev_grid[0]) if rows > 0 else 0
    slope = [[0.0] * cols for _ in range(rows)]

    for r in range(1, rows - 1):
        for c in range(1, cols - 1):
            dz_dx = (elev_grid[r][c + 1] - elev_grid[r][c - 1]) / (2 * meters_per_pixel)
            dz_dy = (elev_grid[r + 1][c] - elev_grid[r - 1][c]) / (2 * meters_per_pixel)
            slope[r][c] = math.degrees(math.atan(math.sqrt(dz_dx ** 2 + dz_dy ** 2)))

    return slope


# ---------------------------------------------------------------------------
# Flood susceptibility
# ---------------------------------------------------------------------------

def compute_flood_susceptibility(elevation: float, slope_deg: float,
                                 elev_min: float, elev_range: float) -> float:
    """
    Simple heuristic flood susceptibility [0, 1]:
    - Low elevation relative to local minimum --> higher susceptibility
    - Low slope --> water ponds rather than runs off
    Combined as a weighted score, clamped to [0, 1].
    """
    if elev_range < 1e-6:
        norm_elev = 0.5
    else:
        norm_elev = max(0.0, min(1.0, (elevation - elev_min) / elev_range))

    # Slope contribution: >15 deg = runoff, <2 deg = ponding
    slope_score = max(0.0, 1.0 - slope_deg / 15.0)

    # Elevation contribution: lower relative elevation = more susceptible
    elev_score = max(0.0, 1.0 - norm_elev)

    # Combined (slope matters more for flash flood)
    return round(0.55 * slope_score + 0.45 * elev_score, 3)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def should_regenerate() -> bool:
    if not OUTPUT_PATH.exists():
        return True
    age_days = (datetime.now(timezone.utc).timestamp() - OUTPUT_PATH.stat().st_mtime) / 86400
    return age_days > MAX_AGE_DAYS


def run():
    if not should_regenerate():
        print(f"[DEM] {OUTPUT_PATH} is fresh (< {MAX_AGE_DAYS} days). Skipping download.")
        return

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"[DEM] Generating terrain data for BLR region "
          f"lat {BBOX['lat_min']}-{BBOX['lat_max']} lon {BBOX['lon_min']}-{BBOX['lon_max']}")

    # Build the grid of lat/lon sample points
    lats = []
    lat = BBOX["lat_min"]
    while lat <= BBOX["lat_max"] + 1e-9:
        lats.append(round(lat, 4))
        lat = round(lat + RESOLUTION_DEG, 4)

    lons = []
    lon = BBOX["lon_min"]
    while lon <= BBOX["lon_max"] + 1e-9:
        lons.append(round(lon, 4))
        lon = round(lon + RESOLUTION_DEG, 4)

    print(f"[DEM] Grid: {len(lats)} lat x {len(lons)} lon = {len(lats)*len(lons)} points")

    # Determine which tiles are needed
    x_min, y_max = deg_to_tile(BBOX["lat_min"], BBOX["lon_min"], ZOOM)
    x_max, y_min = deg_to_tile(BBOX["lat_max"], BBOX["lon_max"], ZOOM)

    # Meters per pixel at this zoom/latitude (approx, use centre lat)
    centre_lat = (BBOX["lat_min"] + BBOX["lat_max"]) / 2.0
    earth_circ_m = 2 * math.pi * 6378137.0
    meters_per_pixel = (earth_circ_m * math.cos(math.radians(centre_lat))) / (256 * 2 ** ZOOM)
    print(f"[DEM] Zoom {ZOOM}, ~{meters_per_pixel:.1f} m/pixel. "
          f"Tiles x={x_min}-{x_max} y={y_min}-{y_max}")

    # Fetch and cache all needed tiles
    tile_cache = {}
    slope_cache = {}
    for ty in range(y_min, y_max + 1):
        for tx in range(x_min, x_max + 1):
            key = (tx, ty)
            try:
                png = fetch_tile(ZOOM, tx, ty)
                elev_grid = decode_terrarium_png(png)
                slope_grid = compute_slope_deg(elev_grid, meters_per_pixel)
                tile_cache[key] = elev_grid
                slope_cache[key] = slope_grid
                print(f"[DEM]   tile {tx}/{ty} OK")
            except Exception as exc:
                print(f"[DEM]   tile {tx}/{ty} failed: {exc}")
                tile_cache[key] = None
                slope_cache[key] = None

    # Sample elevation + slope at each grid point
    raw_elevations = []
    raw_slopes = []
    sample_meta = []

    for lat in lats:
        for lon in lons:
            # Which tile does this point fall on?
            tx, ty = deg_to_tile(lat, lon, ZOOM)
            key = (tx, ty)

            elev = None
            slope = None

            if tile_cache.get(key) is not None:
                # Compute pixel offset within tile
                tile_lat_n, tile_lon_w = tile_to_lat_lon(tx, ty, ZOOM)
                tile_lat_s, tile_lon_e = tile_to_lat_lon(tx + 1, ty + 1, ZOOM)

                tile_lat_span = tile_lat_n - tile_lat_s
                tile_lon_span = tile_lon_e - tile_lon_w

                px_x = int((lon - tile_lon_w) / tile_lon_span * 256)
                px_y = int((tile_lat_n - lat) / tile_lat_span * 256)
                px_x = max(0, min(255, px_x))
                px_y = max(0, min(255, px_y))

                eg = tile_cache[key]
                sg = slope_cache[key]
                if px_y < len(eg) and px_x < len(eg[px_y]):
                    elev = round(eg[px_y][px_x], 1)
                    slope = round(sg[px_y][px_x], 2)

            raw_elevations.append(elev if elev is not None else 900.0)
            raw_slopes.append(slope if slope is not None else 1.0)
            sample_meta.append((lat, lon, elev, slope))

    # Compute flood susceptibility using local elevation statistics
    valid_elevs = [e for e in raw_elevations if e is not None]
    elev_min = min(valid_elevs) if valid_elevs else 800.0
    elev_max = max(valid_elevs) if valid_elevs else 1000.0
    elev_range = elev_max - elev_min

    print(f"[DEM] Elevation range: {elev_min:.0f} m -- {elev_max:.0f} m")

    grid_out = []
    for i, (lat, lon, elev, slope) in enumerate(sample_meta):
        e = raw_elevations[i]
        s = raw_slopes[i]
        fs = compute_flood_susceptibility(e, s, elev_min, elev_range)
        grid_out.append({
            "lat": lat,
            "lon": lon,
            "elevation_m": e,
            "slope_deg": s,
            "flood_susceptibility": fs,
        })

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "AWS Terrain Tiles (terrarium) zoom=11, SRTM-derived",
        "bbox": BBOX,
        "resolution_deg": RESOLUTION_DEG,
        "n_points": len(grid_out),
        "elevation_stats": {
            "min_m": round(elev_min, 1),
            "max_m": round(elev_max, 1),
            "range_m": round(elev_range, 1),
        },
        "grid": grid_out,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    print(f"[DEM] Wrote {len(grid_out)} grid points to {OUTPUT_PATH} "
          f"({OUTPUT_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"[DEM] ERROR: {exc}")
        sys.exit(1)
