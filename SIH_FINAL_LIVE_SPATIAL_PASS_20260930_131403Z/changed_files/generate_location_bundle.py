#!/usr/bin/env python3
"""
generate_location_bundle.py — CLI reference/debug tool: given a lat/lon,
print the exact honest multi-architecture bundle location_engine.py (and
its browser port) would produce, reading the real on-disk files. Useful for
verifying the frontend against a known-good server-side computation, and
for a future thin API endpoint if one is ever added (none exists today --
this is deliberately just a CLI, not a server, per "do not invent an API
that doesn't exist").

Usage: python3 generate_location_bundle.py <lat> <lon>
"""
import json
import sys
from pathlib import Path

from location_engine import build_location_bundle


def _load(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 generate_location_bundle.py <lat> <lon>", file=sys.stderr)
        sys.exit(1)
    lat, lon = float(sys.argv[1]), float(sys.argv[2])

    grid = _load("data/pan_india_grid.json")
    forecast = _load("forecast.json")
    himawari = _load("data/himawari_realtime.json")

    bundle = build_location_bundle(lat, lon, grid, forecast, himawari)
    print(json.dumps(bundle.to_json(), indent=2, default=str))


if __name__ == "__main__":
    main()
