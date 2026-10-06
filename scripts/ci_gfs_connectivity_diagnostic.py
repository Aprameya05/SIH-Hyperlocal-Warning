#!/usr/bin/env python3
"""
scripts/ci_gfs_connectivity_diagnostic.py

Safe, bounded diagnostic for .github/workflows/forecast_update.yml
(2026-10-06 pass, Priority 1): determines whether THIS GitHub Actions
runner -- not this development sandbox, which is confirmed blocked --
can actually reach NOMADS and read a real GRIB file. Never downloads a
full archive; fetches ONE small single-variable, single-level request
(CAPE at the surface, current resolved cycle, f006) which is a few MB,
not the ~200-550MB full-field file the production fetcher needs.

Exposes no secrets (NOMADS needs none). Exits 0 always -- this is
diagnostic, not a gate; the calling workflow step should use
continue-on-error so a NOMADS outage never fails the whole scheduled job,
while the printed JSON result still records exactly what happened.

Checks, in order:
  1. DNS resolution of nomads.ncep.noaa.gov
  2. A plain HTTPS GET to the NOMADS host (connectivity/TLS)
  3. Resolving the current GFS cycle (reuses pan_india_gfs_fetcher.resolve_gfs_cycle)
  4. Downloading ONE small filtered GRIB2 request (CAPE only, surface, f006)
  5. Opening it with cfgrib/xarray and confirming the expected variable + cycle metadata

Usage: python3 scripts/ci_gfs_connectivity_diagnostic.py
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

NOMADS_HOST = "nomads.ncep.noaa.gov"
result = {
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "dns_resolves": None,
    "https_reachable": None,
    "https_status": None,
    "resolved_cycle": None,
    "small_grib_downloaded": False,
    "small_grib_bytes": None,
    "grib_readable_by_cfgrib": False,
    "grib_cycle_matches_requested": None,
    "grib_has_expected_variable": False,
    "overall": "UNKNOWN",
    "failure_reason": None,
}


def main():
    # 1. DNS
    try:
        socket.gethostbyname(NOMADS_HOST)
        result["dns_resolves"] = True
    except Exception as exc:
        result["dns_resolves"] = False
        result["failure_reason"] = f"DNS resolution failed: {exc}"
        result["overall"] = "UNREACHABLE"
        print(json.dumps(result, indent=2))
        return

    # 2. HTTPS reachability
    import requests
    try:
        r = requests.get(f"https://{NOMADS_HOST}/", timeout=15)
        result["https_reachable"] = True
        result["https_status"] = r.status_code
        if r.status_code != 200:
            result["failure_reason"] = f"HTTPS reachable but non-200 status: {r.status_code}"
    except Exception as exc:
        result["https_reachable"] = False
        result["failure_reason"] = f"HTTPS request failed: {type(exc).__name__}: {exc}"
        result["overall"] = "UNREACHABLE"
        print(json.dumps(result, indent=2))
        return

    # 3. Resolve the current cycle using the EXISTING reusable resolver
    try:
        from pan_india_gfs_fetcher import resolve_gfs_cycle, NOMADS_BASE, HEADERS
        import pan_india_gfs_fetcher as _gfs
        cycle_str, fhour = resolve_gfs_cycle(datetime.now(timezone.utc))
        result["resolved_cycle"] = cycle_str
    except Exception as exc:
        result["failure_reason"] = f"Cycle resolution failed: {exc}"
        result["overall"] = "ERROR"
        print(json.dumps(result, indent=2))
        return

    # 4. ONE small, bounded GRIB request -- CAPE only, surface, f006, tiny
    # India-only bbox -- a few MB, never the full multi-variable field.
    date_str = cycle_str[:8]
    cycle_h = cycle_str[8:]
    fname = f"gfs.t{cycle_h}z.pgrb2.0p25.f006"
    small_url = (
        f"{NOMADS_BASE}?file={fname}&lev_surface=on&var_CAPE=on"
        f"&leftlon=70&rightlon=90&toplat=30&bottomlat=10"
        f"&dir=%2Fgfs.{date_str}%2F{cycle_h}%2Fatmos"
    )
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
            tmp_path = tmp.name
        r = requests.get(small_url, headers=HEADERS, timeout=60)
        if r.status_code == 200 and len(r.content) > 500 and r.content[:4] != b"<htm":
            with open(tmp_path, "wb") as f:
                f.write(r.content)
            result["small_grib_downloaded"] = True
            result["small_grib_bytes"] = len(r.content)
        else:
            result["failure_reason"] = (
                f"Small GRIB request did not return a real GRIB file "
                f"(HTTP {r.status_code}, {len(r.content)} bytes, "
                f"starts_with_html={r.content[:4] == b'<htm'})"
            )
            result["overall"] = "BLOCKED"
            print(json.dumps(result, indent=2))
            return
    except Exception as exc:
        result["failure_reason"] = f"Small GRIB download failed: {type(exc).__name__}: {exc}"
        result["overall"] = "BLOCKED"
        print(json.dumps(result, indent=2))
        return

    # 5. Confirm it is actually readable AND actually the requested cycle/variable
    # -- never mark LIVE just because the HTTP request returned 200.
    try:
        import xarray as xr
        ds = xr.open_dataset(tmp_path, engine="cfgrib",
                              backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface"}}, indexpath="")
        result["grib_readable_by_cfgrib"] = True
        result["grib_has_expected_variable"] = "cape" in ds.data_vars
        grib_date = ds.attrs.get("GRIB_dataDate") if hasattr(ds, "attrs") else None
        actual_data_date = int(ds["time"].dt.strftime("%Y%m%d").item()) if "time" in ds.coords else None
        result["grib_cycle_matches_requested"] = (str(actual_data_date) == date_str) if actual_data_date else None
        ds.close()
        result["overall"] = "LIVE_VERIFIED" if (result["grib_has_expected_variable"] and
                                                  result["grib_cycle_matches_requested"] is not False) else "PARTIAL"
    except Exception as exc:
        result["failure_reason"] = f"cfgrib could not read the downloaded file: {type(exc).__name__}: {exc}"
        result["overall"] = "BLOCKED"
    finally:
        try:
            Path(tmp_path).unlink()
        except Exception:
            pass

    print(json.dumps(result, indent=2))
    _run_aws_mirror_check()


def _run_aws_mirror_check():
    """Priority 2 (2026-10-06 takeover pass): NOMADS is confirmed blocked
    from both this sandbox and GitHub Actions runners (see the NOMADS
    result printed above). This checks the free, public, no-credential
    NOAA AWS Open Data mirror (scripts/gfs_aws_source.py) as an
    independent diagnostic -- printed as its own JSON object so neither
    result is ever conflated with the other. continue-on-error in the
    workflow step means neither check can fail the scheduled job."""
    # Printed to stderr, not stdout: callers (including
    # tests/test_phase39_realtime_ceiling_and_connectivity.py) parse
    # stdout as a single JSON document for the NOMADS result above:
    # this diagnostic must stay additive and never break that contract.
    try:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import gfs_aws_source as aws_src
        result = aws_src.check_connectivity()
    except Exception as exc:
        result = {"overall": "ERROR", "failure_reason": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"aws_mirror_diagnostic": result}, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
