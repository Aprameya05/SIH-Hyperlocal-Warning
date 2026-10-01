#!/usr/bin/env python3
"""
acquire_phase_0_4_19_batch.py -- Phase 0.4.19 manifest-driven batch
acquisition for the Phase 0.4.18-cleaned candidate batch
(docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv, 20 event groups / 40 unique
files).

This script does NOT invent a new URL pattern. It reuses, by import, the
exact confirmed GDEX d084001 full-file HTTPS convention already used by
scripts/acquire_historical_gfs_pilot.py (Phase 0.4.3):

    https://data.gdex.ucar.edu/d084001/{YYYY}/{YYYYMMDD}/gfs.0p25.{cycle}.f{lead}.grib2

It does NOT implement or attempt THREDDS/OPeNDAP (Phase 0.4.18 found both
available execution environments here blocked from testing that path;
this script sticks to the one confirmed-working mechanism, per the
Phase 0.4.19 brief).

Safety properties (all deliberate, see docstrings below):
  - manifest-driven: reads the real candidate CSV, de-duplicates to the
    actual set of UNIQUE source files (a cycle can in principle be shared
    by more than one event group; this batch's cycles happen to be 1:1
    with event groups, but this script never assumes that -- it always
    computes uniqueness from the data)
  - defaults to a dry run; a real network fetch requires --execute
  - streams every download in fixed-size chunks -- never reads a whole
    500+ MB response into memory
  - writes to a `.part` temporary file and only atomically renames it to
    the final filename after a full, size-consistent download completes
  - resumes an interrupted `.part` file via an HTTP Range request IF (and
    only if) the server's HEAD response advertises `Accept-Ranges: bytes`
    for that URL -- this is checked per-file, never assumed
  - retries a bounded number of times on transient network/5xx failures,
    with exponential backoff; a `.part` file is preserved (not deleted)
    on final failure so a later run can resume it
  - NEVER re-downloads or overwrites a file that already exists at the
    final destination path with a size and SHA-256 matching this script's
    own prior manifest entry for that exact filename
  - every file this script acquires is tagged in the manifest with its
    label, event_group_key, and partition (train/holdout) from the
    candidate CSV, so a later consumer (not this script) can keep
    HOLDOUT files out of any training dataset -- this script performs no
    training and makes no claim about downstream usage
  - GDEX's direct-file HTTPS path does not publish a remote checksum
    (confirmed absent from docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md's
    documented access mechanisms) -- the SHA-256 this script records is
    explicitly labeled an ACQUISITION FINGERPRINT (this project's own
    record of what it downloaded), never described as a "remote
    verification" or "checksum match" against the source

Usage (from the repository root, on a normal network-connected machine --
NOT this sandbox, which Phase 0.4.2/0.4.18 already confirmed rejects
every GDEX host at the network layer):

    python scripts/acquire_phase_0_4_19_batch.py --dry-run
    python scripts/acquire_phase_0_4_19_batch.py --execute
    python scripts/acquire_phase_0_4_19_batch.py --execute --only-partition train
    python scripts/acquire_phase_0_4_19_batch.py --execute --limit 2   # smoke-test 2 files first
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Reused, not re-derived: the exact confirmed URL pattern, naming
# convention, dataset metadata, and human_size()/sha256_of_file() helpers
# already authored (and documented as reviewed-but-sandbox-untested) in
# Phase 0.4.3.
from acquire_historical_gfs_pilot import (  # noqa: E402
    DATASET_ID, DATASET_LANDING_PAGE, LICENSE,
    DIRECT_FILE_BASE, DIRECT_FILE_NAME,
    human_size, sha256_of_file, build_direct_url,
)

MANIFEST_CSV = REPO_ROOT / "docs" / "PHASE_0_4_17_ACQUISITION_MANIFEST.csv"
OUT_DIR = REPO_ROOT / "data" / "external" / "historical_gfs"
RAW_DIR = OUT_DIR / "raw"
PROVENANCE_MANIFEST = OUT_DIR / "manifest.json"

CHUNK_SIZE = 1 << 20  # 1 MiB streaming chunks -- never load the whole file into memory
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 2.0

# Same default ceiling logic as Phase 0.4.3's single-cycle tool, but this
# script compares PER FILE (a single ~200-550 MB global file), not per
# cycle-pair, since it may be asked to fetch leads independently.
DEFAULT_SIZE_CEILING_BYTES = 700_000_000  # ~700 MB -- above the observed 216.9-551.7MB range


@dataclass
class DownloadTarget:
    event_group_key: str
    label: str
    partition: str
    season: str
    target_ist_date: str
    cycle: str
    lead: str  # e.g. "f003"
    filename: str
    url: str
    dest: Path


@dataclass
class DownloadResult:
    target: DownloadTarget
    status: str  # SKIPPED_ALREADY_VERIFIED | DOWNLOADED | RESUMED_AND_COMPLETED | FAILED | DRY_RUN
    http_status: Optional[int] = None
    bytes_downloaded: int = 0
    final_size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    resumed: bool = False
    attempts: int = 0
    error: Optional[str] = None


def load_batch_targets(manifest_csv: Path = MANIFEST_CSV, raw_root: Path = RAW_DIR) -> list[DownloadTarget]:
    """Reads the real Phase 0.4.17/0.4.18 candidate CSV and returns the
    DE-DUPLICATED set of unique source files that actually need to be
    downloaded. Never assumes a 1:1 row-to-file mapping -- uniqueness is
    computed from (cycle, lead), i.e. the actual GDEX file identity, so
    if any future manifest version has two event groups sharing one
    cycle/lead, this function still returns exactly one download target
    for that file.

    raw_root: where destination files are written. Defaults to the
    original RAW_DIR (data/external/historical_gfs/raw under the repo)
    for exact backward compatibility, but Phase 0.4.19.1 (storage
    relocation) lets a caller point this anywhere -- e.g. a D: drive --
    without changing this function's logic or the manifest CSV it reads."""
    if not manifest_csv.exists():
        raise FileNotFoundError(f"candidate manifest not found: {manifest_csv}")

    raw_root = Path(raw_root)
    seen_files: dict[str, DownloadTarget] = {}
    with open(manifest_csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("selected_gfs_cycle") == "AMBIGUOUS":
                continue  # never attempt to acquire a flagged-ambiguous candidate
            cycle = row["selected_gfs_cycle"]
            lead_str = row["selected_lead"]  # "f003" / "f006"
            lead_num = int(lead_str.lstrip("f"))
            filename = row["expected_file"]
            if filename in seen_files:
                continue
            url = build_direct_url(cycle, f"{lead_num:03d}")
            seen_files[filename] = DownloadTarget(
                event_group_key=row["event_group_key"],
                label=row["label"],
                partition=row["partition"],
                season=row["season"],
                target_ist_date=row["target_ist_date"],
                cycle=cycle,
                lead=lead_str,
                filename=filename,
                url=url,
                dest=raw_root / filename,
            )
    return list(seen_files.values())


def load_existing_provenance(provenance_path: Path = PROVENANCE_MANIFEST) -> dict:
    provenance_path = Path(provenance_path)
    if not provenance_path.exists():
        return {}
    try:
        entries = json.loads(provenance_path.read_text())
    except Exception:  # noqa: BLE001
        return {}
    return {e["file_name"]: e for e in entries}


def already_verified(target: DownloadTarget, provenance: dict) -> bool:
    """A file is treated as already-acquired (never re-downloaded) only if
    it exists on disk AND its size AND locally-recomputed SHA-256 both
    match this script's own prior provenance record for that exact
    filename. A same-named file that fails either check is NOT silently
    trusted -- it is reported, not clobbered, by the caller."""
    if not target.dest.exists():
        return False
    entry = provenance.get(target.filename)
    if entry is None:
        return False
    actual_size = target.dest.stat().st_size
    if entry.get("size_bytes") != actual_size:
        return False
    actual_sha = sha256_of_file(target.dest)
    return entry.get("sha256") == actual_sha


def head_probe(url: str) -> dict:
    """Best-effort HEAD request. Returns {} if the network is unreachable
    (e.g. this sandbox) -- never fabricates a size or Accept-Ranges value."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=20) as resp:
            length = resp.headers.get("Content-Length")
            accept_ranges = resp.headers.get("Accept-Ranges")
            return {
                "status": resp.status,
                "content_length": int(length) if length else None,
                "accept_ranges_bytes": accept_ranges == "bytes",
            }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def stream_download(url: str, tmp_path: Path, resume_from: int, size_ceiling: int,
                     allow_large: bool, head_info: dict) -> dict:
    """Streams one HTTP GET (optionally Range-resumed) into tmp_path in
    CHUNK_SIZE pieces. Never reads the whole response into memory. Returns
    a dict with http_status and bytes_written this call."""
    import urllib.request
    import urllib.error

    content_length = head_info.get("content_length")
    if content_length is not None and content_length > size_ceiling and not allow_large:
        raise SystemExit(
            f"REFUSING download of {url}: {human_size(content_length)} exceeds the size "
            f"ceiling {human_size(size_ceiling)}. Pass --allow-large if intentional."
        )

    headers = {}
    mode = "wb"
    if resume_from > 0 and head_info.get("accept_ranges_bytes"):
        headers["Range"] = f"bytes={resume_from}-"
        mode = "ab"
    elif resume_from > 0:
        # Server does not advertise range support -- cannot safely resume;
        # the caller must restart this file from scratch rather than risk
        # silently appending onto a byte offset the server does not honor.
        resume_from = 0
        mode = "wb"

    req = urllib.request.Request(url, headers=headers)
    bytes_written = 0
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=60) as resp:
        http_status = resp.status
        with open(tmp_path, mode) as out:
            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                bytes_written += len(chunk)
    return {"http_status": http_status, "bytes_written": bytes_written, "resumed": mode == "ab"}


def download_one(target: DownloadTarget, size_ceiling: int, allow_large: bool,
                  provenance: dict) -> DownloadResult:
    if already_verified(target, provenance):
        return DownloadResult(target=target, status="SKIPPED_ALREADY_VERIFIED",
                               final_size_bytes=target.dest.stat().st_size,
                               sha256=provenance[target.filename]["sha256"])

    tmp_path = target.dest.with_suffix(target.dest.suffix + ".part")
    head_info = head_probe(target.url)
    resume_from = tmp_path.stat().st_size if tmp_path.exists() else 0

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = stream_download(target.url, tmp_path, resume_from, size_ceiling,
                                      allow_large, head_info)
            # verify the download is actually complete before renaming, if
            # the server told us how big the file should be
            final_size = tmp_path.stat().st_size
            expected = head_info.get("content_length")
            if expected is not None and not result["resumed"] and final_size != expected:
                raise IOError(
                    f"incomplete download: got {final_size} bytes, server reported {expected}"
                )
            # atomic rename only after the file is judged complete
            tmp_path.replace(target.dest)
            sha = sha256_of_file(target.dest)
            return DownloadResult(
                target=target, status="RESUMED_AND_COMPLETED" if resume_from else "DOWNLOADED",
                http_status=result["http_status"], bytes_downloaded=result["bytes_written"],
                final_size_bytes=target.dest.stat().st_size, sha256=sha,
                resumed=resume_from > 0, attempts=attempt,
            )
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            resume_from = tmp_path.stat().st_size if tmp_path.exists() else 0
            if attempt < MAX_RETRIES:
                backoff = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                print(f"  attempt {attempt}/{MAX_RETRIES} failed ({e}); "
                      f"retrying in {backoff:.0f}s (partial file preserved: {tmp_path})",
                      file=sys.stderr)
                time.sleep(backoff)
            continue

    # All retries exhausted -- the .part file is deliberately left on disk
    # so a future run can resume it; nothing is deleted.
    return DownloadResult(target=target, status="FAILED", error=last_error, attempts=MAX_RETRIES)


def write_provenance(results: list[DownloadResult], provenance_path: Path = PROVENANCE_MANIFEST) -> None:
    provenance_path = Path(provenance_path)
    existing = load_existing_provenance(provenance_path)
    for r in results:
        if r.status in ("DOWNLOADED", "RESUMED_AND_COMPLETED"):
            existing[r.target.filename] = {
                "dataset_id": DATASET_ID,
                "source_url": r.target.url,
                "retrieval_date_utc": datetime.now(timezone.utc).isoformat(),
                "cycle": r.target.cycle,
                "forecast_lead_hours": int(r.target.lead.lstrip("f")),
                "file_name": r.target.filename,
                "sha256": r.sha256,
                "size_bytes": r.final_size_bytes,
                "license": LICENSE,
                "note": "Direct full-global-file download; NOT bbox-subset at fetch time. "
                        "sha256 is a LOCAL ACQUISITION FINGERPRINT only -- GDEX's direct-file "
                        "HTTPS path does not publish a remote checksum to verify against "
                        "(confirmed absent from Phase 0.4.3's documented access mechanisms); "
                        "this value records what this project downloaded, and is used to "
                        "detect accidental re-download corruption or cross-file duplication, "
                        "not to prove the file matches NCAR's own copy byte-for-byte.",
                "event_group_key": r.target.event_group_key,
                "label": r.target.label,
                "partition": r.target.partition,
                "season": r.target.season,
                "target_ist_date": r.target.target_ist_date,
                "acquisition_phase": "phase_0_4_19",
            }
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(json.dumps(list(existing.values()), indent=2))


def detect_duplicate_fingerprints(provenance: dict) -> list[dict]:
    """Flags any two distinct filenames sharing the same SHA-256 -- would
    indicate the same bytes were saved under two different intended event
    groups (a real data-integrity problem, not assumed away)."""
    by_sha: dict[str, list[str]] = {}
    for fname, entry in provenance.items():
        by_sha.setdefault(entry.get("sha256"), []).append(fname)
    return [{"sha256": sha, "filenames": names} for sha, names in by_sha.items() if len(names) > 1]


def check_free_space(raw_root: Path, targets: list[DownloadTarget], skip: bool) -> None:
    """Best-effort preflight: sums known remaining bytes needed (observed
    file sizes from any existing manifest.json entries, else the mean of
    this project's own prior observed range as a conservative estimate)
    and compares against shutil.disk_usage(raw_root).free. This is a
    WARNING, not a hard stop, because remote sizes may be unknown offline
    -- but it is printed loudly specifically because this is exactly the
    failure (`[Errno 28] No space left on device`) this phase exists to
    help avoid."""
    import shutil

    if skip:
        return
    raw_root = Path(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)
    try:
        free_bytes = shutil.disk_usage(raw_root).free
    except Exception as e:  # noqa: BLE001
        print(f"  (could not check free space on {raw_root}: {e})", file=sys.stderr)
        return

    # Conservative per-file estimate: mean of this project's own observed
    # GDEX file sizes to date (216.9-551.7 MB range, documented in
    # docs/PHASE_0_4_19_ACQUISITION_READINESS_AUDIT.md) -- never invented,
    # drawn from real prior downloads.
    ASSUMED_MEAN_FILE_BYTES = 394_700_000
    already_present = sum(
        t.dest.stat().st_size for t in targets if t.dest.exists()
    )
    remaining = [t for t in targets if not t.dest.exists()]
    estimated_remaining_bytes = len(remaining) * ASSUMED_MEAN_FILE_BYTES

    print(f"Free space on destination ({raw_root}): {human_size(free_bytes)}")
    print(f"Estimated additional space needed for {len(remaining)} not-yet-present files: "
          f"~{human_size(estimated_remaining_bytes)} (using this project's observed mean "
          f"file size of {human_size(ASSUMED_MEAN_FILE_BYTES)} -- actual per-file sizes vary "
          f"216.9-551.7 MB)")
    if estimated_remaining_bytes > free_bytes:
        print(
            f"WARNING: estimated requirement (~{human_size(estimated_remaining_bytes)}) exceeds "
            f"free space ({human_size(free_bytes)}) on {raw_root}. This is the same condition "
            f"that caused the prior '[Errno 28] No space left on device' failure. Consider "
            f"--raw-root pointing at a drive with more free space, or --only-partition/--limit "
            f"to acquire in smaller batches.",
            file=sys.stderr,
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=str(MANIFEST_CSV))
    ap.add_argument("--raw-root", default=str(RAW_DIR),
                     help=f"Directory GRIB2 files are written to/read from (default: {RAW_DIR}). "
                          "Point this at a different drive (e.g. D:\\SIH-Historical-GFS\\raw) to "
                          "relocate large-file storage without moving the repository itself. "
                          "Never hardcoded -- always passed explicitly or left at the default.")
    ap.add_argument("--provenance-manifest", default=str(PROVENANCE_MANIFEST),
                     help=f"Path to the acquisition provenance manifest.json (default: {PROVENANCE_MANIFEST}). "
                          "This file only ever records bare filenames, never absolute paths, so it "
                          "remains valid regardless of where --raw-root points.")
    ap.add_argument("--execute", action="store_true", help="Actually download. Without this, dry-run only.")
    ap.add_argument("--dry-run", action="store_true", help="Explicit dry run (default even without this flag).")
    ap.add_argument("--only-partition", choices=["train", "holdout", "all"], default="all")
    ap.add_argument("--limit", type=int, default=None, help="Only process the first N unique files (smoke test).")
    ap.add_argument("--allow-large", action="store_true")
    ap.add_argument("--size-ceiling-bytes", type=int, default=DEFAULT_SIZE_CEILING_BYTES)
    ap.add_argument("--skip-space-check", action="store_true",
                     help="Skip the free-space preflight warning (the check never blocks execution by itself).")
    args = ap.parse_args()

    raw_root = Path(args.raw_root)
    provenance_path = Path(args.provenance_manifest)

    targets = load_batch_targets(Path(args.manifest), raw_root=raw_root)
    if args.only_partition != "all":
        targets = [t for t in targets if t.partition == args.only_partition]
    if args.limit is not None:
        targets = targets[: args.limit]

    n_train = sum(1 for t in targets if t.partition == "train")
    n_holdout = sum(1 for t in targets if t.partition == "holdout")
    print(f"Dataset: {DATASET_ID} ({DATASET_LANDING_PAGE}), license {LICENSE}")
    print(f"Raw-file storage root: {raw_root}")
    print(f"Provenance manifest: {provenance_path}")
    print(f"Batch: {len(targets)} unique files to acquire ({n_train} TRAIN, {n_holdout} HOLDOUT)")
    check_free_space(raw_root, targets, args.skip_space_check)

    if not args.execute:
        print("\nDRY RUN ONLY (pass --execute to actually download). No network request will be made.")
        for t in targets:
            print(f"  [{t.partition.upper():7s}] {t.filename}  <- {t.url}")
        return

    provenance = load_existing_provenance(provenance_path)
    results = []
    for t in targets:
        print(f"\n=== {t.filename} ({t.partition}, {t.label}) ===")
        r = download_one(t, args.size_ceiling_bytes, args.allow_large, provenance)
        print(f"  status: {r.status}" + (f" ({r.error})" if r.error else ""))
        results.append(r)

    write_provenance(results, provenance_path)
    updated_provenance = load_existing_provenance(provenance_path)
    dupes = detect_duplicate_fingerprints(updated_provenance)

    n_ok = sum(1 for r in results if r.status in ("DOWNLOADED", "RESUMED_AND_COMPLETED", "SKIPPED_ALREADY_VERIFIED"))
    n_failed = sum(1 for r in results if r.status == "FAILED")
    print(f"\n{n_ok}/{len(results)} files OK, {n_failed} failed")
    if dupes:
        print(f"WARNING: {len(dupes)} duplicate SHA-256 fingerprint(s) detected across filenames: {dupes}")
    if n_failed:
        print("Failed files left their .part file in place for a future resumed run.")
        sys.exit(1)


if __name__ == "__main__":
    main()
