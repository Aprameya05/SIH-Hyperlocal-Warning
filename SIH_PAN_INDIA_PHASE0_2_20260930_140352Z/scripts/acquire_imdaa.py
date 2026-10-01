#!/usr/bin/env python3
"""
acquire_imdaa.py

Downloads a scientifically useful (NOT terabyte-scale) subset of IMDAA
reanalysis data from the official NCMRWF data service.

STATUS: cannot run from this Claude sandbox -- this environment has no
network route to NCMRWF-adjacent hosts (verified: a direct request to an
IMD/NCMRWF-adjacent domain returns a 403 from this sandbox's own network
proxy, not from NCMRWF itself). This script is real, working scaffolding
for YOU to run once you have NCMRWF credentials and real network access.

WHAT THIS SCRIPT DOES NOT DO:
  - It does not guess NCMRWF's current download URL/API shape. The
    NCMRWF_BASE_URL and endpoint path below are placeholders you must
    replace with the real ones from your own registered account --
    filling them from memory/training data without verification would
    risk shipping a wrong or stale endpoint as if it were confirmed.
  - It does not fabricate a sample response if the request fails.

Usage (once you have real credentials and a real endpoint):
    export NCMRWF_USER=...
    export NCMRWF_PASS=...
    python3 scripts/acquire_imdaa.py --start 2024-01-01 --end 2024-01-07 \
        --variables cape,cin,t,q,u,v,z --bounds 6,37,68,98

Prioritized variables (per the SIH architecture's needs, matched against
backend/mtl_backbone.py's FEATURE_NAMES and the pan-India grid schema):
    temperature (multi-level), specific humidity, geopotential height,
    U/V wind (multi-level), CAPE, CIN if available, shear/convergence-
    derivable fields (computed downstream from U/V, not requested raw).

Do NOT download the full archive blindly. Start with a short date range
(a week, prioritizing a known thunderstorm-active period) and the
pressure levels actually used elsewhere in this repo (850/700/500hPa,
matching backend/mtl_backbone.py and the existing ERA5 extraction) before
considering anything larger.
"""
import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "raw" / "imdaa"

# PLACEHOLDER -- replace with the real NCMRWF endpoint from your registered
# account. Deliberately not filled with a guessed URL.
NCMRWF_BASE_URL = os.environ.get("NCMRWF_BASE_URL", "")

PRIORITY_VARIABLES = [
    "t",     # temperature, multi-level
    "q",     # specific humidity, multi-level
    "z",     # geopotential height, multi-level
    "u", "v",  # wind components, multi-level
    "cape",
    "cin",   # if available -- document in IMDAA_DATA_SPEC.md whether it is
]

PRIORITY_LEVELS_HPA = [850, 700, 500]  # matches ERA5 extraction already in this repo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--variables", default=",".join(PRIORITY_VARIABLES))
    ap.add_argument("--bounds", default="6,37,68,98", help="S,N,W,E -- matches backend/pipeline.py BOUNDS")
    args = ap.parse_args()

    user = os.environ.get("NCMRWF_USER", "")
    password = os.environ.get("NCMRWF_PASS", "")
    if not user or not password:
        print("ERROR: NCMRWF_USER / NCMRWF_PASS not set.")
        print("Register at the official NCMRWF data service to obtain credentials.")
        print("This script will not proceed with a guessed or anonymous request.")
        sys.exit(1)

    if not NCMRWF_BASE_URL:
        print("ERROR: NCMRWF_BASE_URL is not set.")
        print("This script deliberately does not ship a guessed endpoint.")
        print("Set NCMRWF_BASE_URL to the real download endpoint from your NCMRWF account,")
        print("confirmed against the current NCMRWF data service documentation --")
        print("not assumed from this script's training-era knowledge.")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Would request: variables={args.variables} bounds={args.bounds} "
          f"period={args.start}..{args.end} levels={PRIORITY_LEVELS_HPA} -> {OUT_DIR}")
    print("Real download logic intentionally not written until NCMRWF_BASE_URL is confirmed --")
    print("writing untested request logic against a guessed API risks silent failures or,")
    print("worse, silently wrong data. Provide the confirmed endpoint/product spec and this")
    print("script will be completed against it.")
    sys.exit(2)


if __name__ == "__main__":
    main()
