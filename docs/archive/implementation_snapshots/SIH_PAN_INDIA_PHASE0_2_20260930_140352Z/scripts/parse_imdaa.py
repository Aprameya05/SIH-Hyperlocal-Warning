#!/usr/bin/env python3
"""
parse_imdaa.py

Parses a real IMDAA data file (placed at raw/imdaa/<file> by you, after
NCMRWF registration and download) into the canonical 992-cell pan-India
grid schema (see docs/CANONICAL_GRID.md), using regrid.py's existing
nearest-neighbor/bilinear abstraction.

STATUS: UNTESTED against real IMDAA data. This sandbox has no NCMRWF
credentials and no real IMDAA file to test against -- xarray/netCDF4 are
also not installed here (checked this session: both ModuleNotFoundError).
Per instruction, no fake/synthetic IMDAA file was created to "pass" a
test. This script is written defensively (format-detecting, fails loudly
and specifically rather than silently) but its correctness against a real
file is UNVERIFIED until you supply one.

IMDAA is commonly distributed as NetCDF (based on general knowledge of
how NCMRWF/UK Met Office reanalysis products are typically packaged) --
this is NOT independently verified against a live NCMRWF product page in
this session (no network access). Confirm the actual format you receive
and update FORMAT_ASSUMPTION below before trusting this script's output.

Usage (once you have a real file):
    pip install xarray netCDF4    # or cfgrib if the real file is GRIB
    python3 scripts/parse_imdaa.py --input raw/imdaa/<file> --output processed/imdaa/<out>.json
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FORMAT_ASSUMPTION = "NetCDF (.nc) -- UNVERIFIED, confirm against your actual downloaded file"

# Candidate variable names to look for once a real file is available --
# these are guesses at IMDAA's likely internal naming (CF-convention-style),
# NOT confirmed. parse_imdaa.py will print whatever variable names the
# real file actually contains so this list can be corrected against ground
# truth on first real run.
CANDIDATE_VAR_NAMES = {
    "temperature": ["t", "temp", "air_temperature", "ta"],
    "specific_humidity": ["q", "shum", "specific_humidity"],
    "geopotential_height": ["z", "gh", "geopotential_height"],
    "u_wind": ["u", "u_wind", "eastward_wind"],
    "v_wind": ["v", "v_wind", "northward_wind"],
    "cape": ["cape", "CAPE"],
    "cin": ["cin", "CIN"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to a real IMDAA file under raw/imdaa/")
    ap.add_argument("--output", required=True, help="Path under processed/imdaa/ to write canonical-grid JSON")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"ERROR: {in_path} does not exist.")
        print("Place a real, downloaded IMDAA file there first -- this script will not")
        print("proceed against a missing or fabricated input.")
        sys.exit(1)

    try:
        import xarray as xr
    except ImportError:
        print("ERROR: xarray not installed. Run: pip install xarray netCDF4")
        print(f"(Format assumption for this script: {FORMAT_ASSUMPTION})")
        sys.exit(1)

    try:
        ds = xr.open_dataset(in_path)
    except Exception as e:
        print(f"ERROR: could not open {in_path} as NetCDF: {e}")
        print("If your real file is GRIB, not NetCDF, this script needs a small change")
        print("(xr.open_dataset(..., engine='cfgrib')) -- tell me the real format and")
        print("I will update this rather than guessing further.")
        sys.exit(1)

    print("Real variables found in this file (use this to correct CANDIDATE_VAR_NAMES above):")
    for v in ds.data_vars:
        print(f"  {v}: dims={ds[v].dims}, shape={ds[v].shape}")
    print("\nCoordinate variables:")
    for c in ds.coords:
        print(f"  {c}: {ds[c].values[:5] if ds[c].size > 5 else ds[c].values}")

    print("\nSTOPPING HERE -- variable-name mapping and regridding logic will be completed")
    print("once the real variable names above are confirmed against this actual file,")
    print("rather than assumed from CANDIDATE_VAR_NAMES. Re-run this script and it will")
    print("print exactly what it found; report that back and the mapping gets finished.")
    sys.exit(2)


if __name__ == "__main__":
    main()
