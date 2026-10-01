"""
Regression tests for the Phase 0.4.26 Colab acquisition URL bug.

Root cause (confirmed by direct code/CSV inspection, not assumption):
scripts/design_phase_0_4_24_ts_300_candidates.py built `expected_url` with
its own inline f-string pointing at the defunct
`https://rda.ucar.edu/datasets/d084001/...` domain, instead of importing
and reusing the single already-confirmed-working helper
(`build_direct_url`, scripts/acquire_historical_gfs_pilot.py) that
successfully acquired the original 40 GFS files in Phase 0.4.19. The
calendar-date directory structure (YYYY/YYYYMMDD, not the full
YYYYMMDDHH cycle) was ALREADY correct in both places -- only the domain
was wrong. These tests pin the canonical URL shape so a regression (either
domain drifting back, or someone introducing a full-cycle-timestamp
directory bug) is caught immediately.

This file does not modify event selection, labels, partitioning, or any
other manifest field -- it only exercises URL construction.
"""
import csv
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from acquire_historical_gfs_pilot import build_direct_url  # noqa: E402

MANIFEST_PATH = REPO_ROOT / "docs" / "PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv"


@pytest.mark.parametrize(
    "cycle,lead,expected",
    [
        (
            "2024010400", "003",
            "https://data.gdex.ucar.edu/d084001/2024/20240104/gfs.0p25.2024010400.f003.grib2",
        ),
        (
            "2024010400", "006",
            "https://data.gdex.ucar.edu/d084001/2024/20240104/gfs.0p25.2024010400.f006.grib2",
        ),
        (
            "2024012406", "003",
            "https://data.gdex.ucar.edu/d084001/2024/20240124/gfs.0p25.2024012406.f003.grib2",
        ),
        (
            "2024012812", "006",
            "https://data.gdex.ucar.edu/d084001/2024/20240128/gfs.0p25.2024012812.f006.grib2",
        ),
        (
            "2024030118", "003",
            "https://data.gdex.ucar.edu/d084001/2024/20240301/gfs.0p25.2024030118.f003.grib2",
        ),
        (
            # Previous-day cycle: regression guard for the exact bug class the user
            # flagged -- the directory must be the CYCLE'S OWN calendar date
            # (2019-12-09), never a forecast-valid-time-derived date, and never the
            # full 10-digit timestamp as a directory segment.
            "2019120918", "003",
            "https://data.gdex.ucar.edu/d084001/2019/20191209/gfs.0p25.2019120918.f003.grib2",
        ),
    ],
)
def test_build_direct_url_canonical_shape(cycle, lead, expected):
    assert build_direct_url(cycle, lead) == expected


def test_build_direct_url_directory_is_calendar_date_not_full_cycle():
    # The directory segment must be exactly the first 8 digits of the cycle
    # (calendar date), never the full 10-digit cycle string.
    url = build_direct_url("2024012406", "003")
    assert "/20240124/" in url
    assert "/2024012406/" not in url


def test_build_direct_url_domain_is_data_gdex_not_rda():
    url = build_direct_url("2024010400", "003")
    assert url.startswith("https://data.gdex.ucar.edu/d084001/")
    assert "rda.ucar.edu" not in url


@pytest.mark.skipif(not MANIFEST_PATH.exists(), reason="Phase 0.4.24 manifest not present in this checkout")
def test_manifest_expected_url_matches_canonical_builder_for_every_row():
    """Every expected_url in the live 300-event-group manifest must be
    reproducible from expected_filename via build_direct_url(). This does
    NOT check selection, labels, or partitioning -- only the URL column."""
    fname_re = re.compile(r"^gfs\.0p25\.(\d{10})\.f(\d{3})\.grib2$")
    with open(MANIFEST_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 600, f"expected 600 manifest rows (files), found {len(rows)}"
    for row in rows:
        m = fname_re.match(row["expected_filename"])
        assert m, f"unexpected filename format: {row['expected_filename']!r}"
        cycle, lead = m.group(1), m.group(2)
        assert row["expected_url"] == build_direct_url(cycle, lead), (
            f"expected_url mismatch for {row['expected_filename']}: "
            f"got {row['expected_url']!r}"
        )


@pytest.mark.skipif(not MANIFEST_PATH.exists(), reason="Phase 0.4.24 manifest not present in this checkout")
def test_manifest_has_no_defunct_rda_urls():
    with open(MANIFEST_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    bad = [r["expected_url"] for r in rows if "rda.ucar.edu" in r["expected_url"]]
    assert not bad, f"{len(bad)} manifest rows still reference the defunct rda.ucar.edu domain"
