"""
tests/test_phase41_gfs_aws_source.py
======================================
Priority 2 (2026-10-06 takeover pass): proves scripts/gfs_aws_source.py
is a genuinely working, free, public, no-credential replacement path
for the current-cycle GFS data that NOMADS (blocked, HTTP 403 from
both this sandbox and GitHub Actions runners -- see
ci_gfs_connectivity_diagnostic.py) cannot currently deliver.

These are REAL network tests against the NOAA "Big Data Program" AWS
Open Data mirror (s3://noaa-gfs-bdp-pds). They are skipped (not
failed) when that network is unreachable, exactly like the existing
NOMADS connectivity diagnostic -- a blocked network must never look
like a code regression.
"""
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gfs_aws_source as g  # noqa: E402


def _aws_reachable() -> bool:
    try:
        socket.gethostbyname(g.S3_BUCKET_HOST)
        return True
    except Exception:
        return False


requires_network = pytest.mark.skipif(
    not _aws_reachable(), reason="noaa-gfs-bdp-pds.s3.amazonaws.com unreachable from this environment"
)


@requires_network
def test_connectivity_check_reports_ok_or_an_honest_reason():
    result = g.check_connectivity()
    assert result["overall"] in ("OK", "DEGRADED", "UNREACHABLE")
    if result["overall"] != "OK":
        assert result["failure_reason"], "non-OK result must always explain why"


@requires_network
def test_find_latest_available_cycle_returns_a_real_recent_cycle():
    cycle = g.find_latest_available_cycle()
    assert cycle is not None, "no GFS cycle found in the last 3 days on the AWS mirror"
    from datetime import datetime, timezone
    cycle_dt = datetime.strptime(cycle.cycle_str, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - cycle_dt).total_seconds() / 3600
    assert 0 <= age_hours < 96, f"cycle {cycle.cycle_str} is not recent (age={age_hours:.1f}h)"


@requires_network
def test_fetch_cycle_subset_downloads_real_fields_and_grib_is_readable(tmp_path):
    cycle = g.find_latest_available_cycle()
    assert cycle is not None
    out = tmp_path / "subset_f002.grib2"
    manifest = g.fetch_cycle_subset(cycle, 2, out)
    assert manifest["status"] in ("OK", "PARTIAL")
    assert len(manifest["fields_found"]) >= 10, (
        f"expected most required fields present, got {manifest['fields_found']}"
    )
    assert manifest["bytes_downloaded"] > 0
    assert out.exists() and out.stat().st_size > 0

    result = g.validate_grib(out)
    assert result["readable"] is True, result.get("reason")
    assert "cape" in result["variables"]


@requires_network
def test_cape_over_india_bbox_is_physically_plausible(tmp_path):
    """End-to-end proof: current cycle -> real file -> real India-bbox
    values, not a fabricated/placeholder number."""
    import xarray as xr

    cycle = g.find_latest_available_cycle()
    out = tmp_path / "subset_f002.grib2"
    g.fetch_cycle_subset(cycle, 2, out)

    ds = xr.open_dataset(str(out), engine="cfgrib",
                          backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface", "stepType": "instant"},
                                           "indexpath": ""})
    cape_india = ds["cape"].sel(latitude=slice(37, 6), longitude=slice(68, 98))
    cape_min, cape_max = float(cape_india.min()), float(cape_india.max())
    ds.close()
    assert 0.0 <= cape_min
    assert cape_max < 10000.0  # physically implausible above this for CAPE in J/kg
    assert cape_max > 0.0, "all-zero CAPE across all of India would indicate a decode/crop bug, not real data"


def test_validate_grib_does_not_import_xarray_or_cfgrib():
    """2026-10-10: a real Linux CI run showed validate_grib's earlier
    xarray+cfgrib implementation (with a del+gc.collect() mitigation)
    still crashed with 'double free or corruption (!prev)' at
    interpreter shutdown -- confirming gc.collect() alone does not fix
    this class of native memory-management defect. Switched to
    eccodes' own low-level GRIB message API, which never goes through
    cfgrib's index/backend-management layer at all. This structural
    guard catches a future regression back to the crash-prone
    implementation, independent of whether a given test environment
    can actually reproduce the Linux-specific crash itself."""
    import ast
    import inspect
    source = inspect.getsource(g.validate_grib)
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert "xarray" not in imported_names, (
        "validate_grib must not import xarray -- this is the crash-prone cfgrib "
        "wrapper path, confirmed still crashing on real Linux CI even with gc.collect()"
    )
    assert "eccodes" in imported_names, "validate_grib should use eccodes' own low-level API directly"
