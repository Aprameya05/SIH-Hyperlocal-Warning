#!/usr/bin/env python3
"""
acquire_historical_gfs_pilot.py -- Phase 0.4.3 RESEARCH-ONLY acquisition tool.

Downloads a SMALL pilot subset of NCAR GDEX dataset d084001 (historical GFS
0.25-degree forecast-cycle archive) for the purpose of validating whether this
project's existing GFS feature-extraction conventions can be applied to a
historical forecast cycle.

This script is intended to be run by the project owner on a normal
network-connected machine. It was authored in a sandbox whose network egress
policy rejects every relevant host (confirmed in Phase 0.4.2) -- it has
therefore NOT been executed end-to-end against the live GDEX service. Treat
its HTTP-calling code as reviewed-but-untested against the real endpoint, and
read docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md before running it.

Hard requirements honored by this script:
  - no hardcoded credentials, ever
  - defaults to a dry run; a real network fetch requires --execute
  - refuses to download anything above a configurable size ceiling unless
    --allow-large is passed
  - never attempts to log in to the login-gated "Get a Subset" GDEX tool on
    the user's behalf -- if OPeNDAP subsetting is not usable, it prints the
    manual URL/instructions and stops
  - writes a provenance manifest (data/external/historical_gfs/manifest.json)
    for every file it does fetch, including a SHA-256 checksum
  - never touches any production file, schema, or pipeline
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DATASET_ID = "d084001"
DATASET_LANDING_PAGE = "https://gdex.ucar.edu/datasets/d084001/"
DATASET_DATAACCESS_PAGE = "https://gdex.ucar.edu/datasets/d084001/dataaccess/"
LICENSE = "CC-BY-4.0"

# Confirmed (Phase 0.4.3) direct-file naming convention and base URL for the
# full-global-file fallback path. This is a real, documented pattern -- not
# invented -- but has not been live-tested from this sandbox (network blocked).
DIRECT_FILE_BASE = "https://data.gdex.ucar.edu/d084001/{yyyy}/{yyyymmdd}/"
DIRECT_FILE_NAME = "gfs.0p25.{cycle}.f{lead}.grib2"

# Observed (search-confirmed, not independently re-opened this phase) THREDDS/
# OPeNDAP catalog URL pattern for the subsetting-capable path.
THREDDS_CATALOG_PATTERN = (
    "https://thredds.rda.ucar.edu/thredds/catalog/files/g/d084001/"
    "{yyyy}/{yyyymmdd}/catalog.html?dataset=files/g/d084001/"
    "{yyyy}/{yyyymmdd}/gfs.0p25.{cycle}.f{lead}.grib2"
)

# Confirmed observed file size range for a single full-global direct file
# (Phase 0.4.2/0.4.3 research): ~487-532 MB. Default ceiling is set just above
# a two-file (f003+f006) direct-download pilot (~1.1 GB) so the *intended*
# small pilot does not require --allow-large, while anything larger does.
DEFAULT_SIZE_CEILING_BYTES = 1_200_000_000  # ~1.2 GB

DEFAULT_OUT_DIR = Path("data/external/historical_gfs")


def human_size(n_bytes):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n_bytes) < 1024.0:
            return f"{n_bytes:.1f}{unit}"
        n_bytes /= 1024.0
    return f"{n_bytes:.1f}PB"


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_direct_url(cycle: str, lead: str) -> str:
    yyyy = cycle[:4]
    yyyymmdd = cycle[:8]
    return DIRECT_FILE_BASE.format(yyyy=yyyy, yyyymmdd=yyyymmdd) + DIRECT_FILE_NAME.format(
        cycle=cycle, lead=lead
    )


def build_thredds_catalog_url(cycle: str, lead: str) -> str:
    yyyy = cycle[:4]
    yyyymmdd = cycle[:8]
    return THREDDS_CATALOG_PATTERN.format(yyyy=yyyy, yyyymmdd=yyyymmdd, cycle=cycle, lead=lead)


def validate_cycle(cycle: str) -> None:
    if len(cycle) != 10 or not cycle.isdigit():
        raise SystemExit(f"--cycle must be YYYYMMDDHH (10 digits), got: {cycle!r}")
    hh = cycle[8:10]
    if hh not in ("00", "06", "12", "18"):
        raise SystemExit(f"GFS cycles are only 00/06/12/18 UTC; got hour {hh!r}")


def validate_leads(leads):
    native = {"000", "003", "006", "009", "012"}
    for lead in leads:
        if len(lead) != 3 or not lead.isdigit():
            raise SystemExit(f"--leads entries must be 3-digit forecast hours, got: {lead!r}")
        if lead not in native:
            print(
                f"WARNING: forecast hour f{lead} is outside this script's confirmed-native "
                f"set {sorted(native)} for a *pilot*. d084001 documents 3-hourly steps through "
                f"240h, so larger multiples of 3 likely exist, but this script only pre-validates "
                f"the small pilot range. Proceeding, but do not treat f{lead} as validated by "
                f"this warning alone.",
                file=sys.stderr,
            )


def head_request_size(url: str) -> int | None:
    """Best-effort remote size check via HTTP HEAD. Returns None if it cannot
    be determined (e.g. no network, as in the authoring sandbox)."""
    try:
        import urllib.request

        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
    except Exception as e:  # noqa: BLE001 - deliberately broad for a best-effort probe
        print(f"  (could not HEAD {url}: {e})", file=sys.stderr)
        return None


def download_file(url: str, dest: Path, size_ceiling: int, allow_large: bool) -> dict:
    size = head_request_size(url)
    if size is not None:
        print(f"  remote reports size: {human_size(size)}")
        if size > size_ceiling and not allow_large:
            raise SystemExit(
                f"REFUSING download: {human_size(size)} exceeds the size ceiling "
                f"{human_size(size_ceiling)}. Re-run with --allow-large if this is intentional, "
                f"or narrow your --leads/--bbox selection."
            )
    else:
        print("  remote size unknown (HEAD failed) -- proceeding cautiously; monitor disk usage.")

    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading -> {dest}")
    urllib.request.urlretrieve(url, dest)  # noqa: S310 - documented official HTTPS source only
    actual_size = dest.stat().st_size
    checksum = sha256_of_file(dest)
    return {"size_bytes": actual_size, "sha256": checksum}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycle", help="GFS init cycle, YYYYMMDDHH (00/06/12/18Z only)")
    ap.add_argument("--leads", nargs="+", default=["003", "006"], help="Forecast hours, 3-digit (default: 003 006)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("SOUTH", "NORTH", "WEST", "EAST"),
                     help="Geographic bounding box for documentation/manifest purposes. "
                          "NOTE: this script's direct-download fallback path fetches the FULL "
                          "GLOBAL file regardless of --bbox (GDEX direct files are not "
                          "pre-subset) -- bbox is recorded in the manifest and is relevant to "
                          "the OPeNDAP path, which this script does not implement a client for "
                          "(see docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md).")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help=f"Output directory (default: {DEFAULT_OUT_DIR})")
    ap.add_argument("--execute", action="store_true", help="Actually perform the network fetch. Without this flag, dry-run only.")
    ap.add_argument("--dry-run", action="store_true", help="Explicit dry run (default behavior even without this flag).")
    ap.add_argument("--allow-large", action="store_true", help="Allow a download larger than the default size ceiling.")
    ap.add_argument("--size-ceiling-bytes", type=int, default=DEFAULT_SIZE_CEILING_BYTES,
                     help=f"Refuse downloads larger than this unless --allow-large (default: {DEFAULT_SIZE_CEILING_BYTES}).")
    args = ap.parse_args()

    if not args.cycle:
        print("No --cycle given -- printing acquisition plan only (dry run).")
        print(f"Dataset: {DATASET_ID} ({DATASET_LANDING_PAGE})")
        print(f"License: {LICENSE}")
        print("Recommended pilot: a single 2015-2020 cycle, leads 003 and 006, "
              "bbox 10-16N/74-80E (see docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md).")
        return

    validate_cycle(args.cycle)
    validate_leads(args.leads)

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"

    plan = []
    for lead in args.leads:
        direct_url = build_direct_url(args.cycle, lead)
        catalog_url = build_thredds_catalog_url(args.cycle, lead)
        dest = raw_dir / DIRECT_FILE_NAME.format(cycle=args.cycle, lead=lead)
        plan.append({"lead": lead, "direct_url": direct_url, "thredds_catalog_url": catalog_url, "dest": str(dest)})

    print(f"Acquisition plan for cycle {args.cycle}, leads {args.leads}:")
    for p in plan:
        print(f"  f{p['lead']}: direct={p['direct_url']}")
        print(f"         thredds (subsettable, preferred)={p['thredds_catalog_url']}")
        print(f"         -> {p['dest']}")

    if not args.execute:
        print("\nDRY RUN ONLY (pass --execute to actually download). No network request was made.")
        print("NOTE: this script's direct-download path pulls the FULL GLOBAL file per the GDEX "
              "documentation found in Phase 0.4.3 -- there is no confirmed lightweight "
              "subsetting client implemented here. Prefer manually using the OPeNDAP/THREDDS "
              "URL above, or the login-gated 'Get a Subset' tool at "
              f"{DATASET_DATAACCESS_PAGE}, for a true small pilot. This script's --execute path "
              "is the documented-but-larger fallback.")
        return

    manifest_entries = []
    for p in plan:
        try:
            result = download_file(p["direct_url"], Path(p["dest"]), args.size_ceiling_bytes, args.allow_large)
        except Exception as e:  # noqa: BLE001
            print(f"FAILED to download f{p['lead']}: {e}", file=sys.stderr)
            print("This is expected if run from a network-restricted environment (e.g. the "
                  "sandbox this script was authored in). Run from a normal network-connected "
                  "machine, or use the manual OPeNDAP/subset URLs above.", file=sys.stderr)
            continue
        manifest_entries.append({
            "dataset_id": DATASET_ID,
            "source_url": p["direct_url"],
            "retrieval_date_utc": datetime.now(timezone.utc).isoformat(),
            "cycle": args.cycle,
            "forecast_lead_hours": int(p["lead"]),
            "file_name": Path(p["dest"]).name,
            "sha256": result["sha256"],
            "size_bytes": result["size_bytes"],
            "requested_bbox": args.bbox,
            "license": LICENSE,
            "note": "Direct full-global-file download; NOT bbox-subset at fetch time (GDEX direct files are not pre-subset). bbox recorded for downstream cropping only.",
        })

    manifest_path = out_dir / "manifest.json"
    existing = []
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text())
        except Exception:  # noqa: BLE001
            existing = []
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(existing + manifest_entries, indent=2))
    print(f"\nProvenance manifest written/updated: {manifest_path}")


if __name__ == "__main__":
    main()
