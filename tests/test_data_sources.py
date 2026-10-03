"""
Unit tests for backend/data_sources (Phase 1 data-source abstraction).

Covers: metadata contract validation, freshness calculation, source
status classification, blocked-source behavior, missing credentials,
malformed source metadata, and provenance preservation.
"""
import csv
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.data_sources import (
    SourceMetadata,
    SourceMetadataError,
    SourceStatus,
    classify_freshness,
    compute_freshness_minutes,
)
from backend.data_sources.gfs import GFSSource
from backend.data_sources.himawari import HimawariSource
from backend.data_sources.metar import METARSource
from backend.data_sources.dem import DEMSource
from backend.data_sources.imdaa import IMDAASource
from backend.data_sources.imerg import IMERGSource
from backend.data_sources.gmgsi import GMGSISource
from backend.data_sources.source_registry import get_source, list_sources, get_all_metadata


def iso_minutes_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


# ---------------------------------------------------------------------------
# Metadata contract
# ---------------------------------------------------------------------------

class TestMetadataContract:
    def test_valid_live_metadata_constructs(self):
        m = SourceMetadata(
            source_name="gfs",
            source_product="NOAA NOMADS GFS 0.25deg",
            observation_time_utc=iso_minutes_ago(30),
            acquisition_time_utc=iso_minutes_ago(29),
            valid_time_utc=iso_minutes_ago(30),
            spatial_resolution="0.25 deg",
            temporal_resolution="3-hourly",
            status=SourceStatus.LIVE,
            freshness_minutes=30.0,
            provenance="data/gfs_realtime_43295.csv",
            quality_flags=[],
        )
        assert m.status == SourceStatus.LIVE
        assert m.to_dict()["status"] == "LIVE"

    def test_status_accepts_plain_string(self):
        m = SourceMetadata(
            source_name="gfs",
            source_product="x",
            observation_time_utc=iso_minutes_ago(1),
            acquisition_time_utc=iso_minutes_ago(1),
            valid_time_utc=None,
            spatial_resolution="x",
            temporal_resolution="x",
            status="LIVE",
            freshness_minutes=1.0,
            provenance="test",
        )
        assert m.status == SourceStatus.LIVE

    def test_invalid_status_string_rejected(self):
        with pytest.raises(SourceMetadataError):
            SourceMetadata(
                source_name="gfs",
                source_product="x",
                observation_time_utc=iso_minutes_ago(1),
                acquisition_time_utc=iso_minutes_ago(1),
                valid_time_utc=None,
                spatial_resolution="x",
                temporal_resolution="x",
                status="SUPER_LIVE",
                freshness_minutes=1.0,
                provenance="test",
            )

    def test_missing_source_name_rejected(self):
        with pytest.raises(SourceMetadataError):
            SourceMetadata(
                source_name="",
                source_product="x",
                observation_time_utc=None,
                acquisition_time_utc=None,
                valid_time_utc=None,
                spatial_resolution="n/a",
                temporal_resolution="n/a",
                status=SourceStatus.BLOCKED,
                freshness_minutes=None,
                provenance="reason",
            )

    def test_live_status_requires_observation_time(self):
        with pytest.raises(SourceMetadataError):
            SourceMetadata(
                source_name="gfs",
                source_product="x",
                observation_time_utc=None,
                acquisition_time_utc=None,
                valid_time_utc=None,
                spatial_resolution="x",
                temporal_resolution="x",
                status=SourceStatus.LIVE,
                freshness_minutes=1.0,
                provenance="test",
            )

    def test_blocked_status_forbids_observation_time(self):
        with pytest.raises(SourceMetadataError):
            SourceMetadata(
                source_name="imdaa",
                source_product="x",
                observation_time_utc=iso_minutes_ago(1),
                acquisition_time_utc=None,
                valid_time_utc=None,
                spatial_resolution="n/a",
                temporal_resolution="n/a",
                status=SourceStatus.BLOCKED,
                freshness_minutes=None,
                provenance="reason",
            )

    def test_blocked_status_requires_a_reason(self):
        with pytest.raises(SourceMetadataError):
            SourceMetadata(
                source_name="imdaa",
                source_product="x",
                observation_time_utc=None,
                acquisition_time_utc=None,
                valid_time_utc=None,
                spatial_resolution="n/a",
                temporal_resolution="n/a",
                status=SourceStatus.BLOCKED,
                freshness_minutes=None,
                provenance="",
            )

    def test_quality_flags_defaults_to_empty_list(self):
        m = SourceMetadata(
            source_name="gfs",
            source_product="x",
            observation_time_utc=iso_minutes_ago(1),
            acquisition_time_utc=iso_minutes_ago(1),
            valid_time_utc=None,
            spatial_resolution="x",
            temporal_resolution="x",
            status=SourceStatus.LIVE,
            freshness_minutes=1.0,
            provenance="test",
        )
        assert m.quality_flags == []

    def test_quality_flags_must_be_a_list(self):
        with pytest.raises(SourceMetadataError):
            SourceMetadata(
                source_name="gfs",
                source_product="x",
                observation_time_utc=iso_minutes_ago(1),
                acquisition_time_utc=iso_minutes_ago(1),
                valid_time_utc=None,
                spatial_resolution="x",
                temporal_resolution="x",
                status=SourceStatus.LIVE,
                freshness_minutes=1.0,
                provenance="test",
                quality_flags="not_a_list",
            )


# ---------------------------------------------------------------------------
# Malformed source metadata (from_dict / round trip)
# ---------------------------------------------------------------------------

class TestMalformedMetadata:
    def test_from_dict_missing_fields_raises(self):
        with pytest.raises(SourceMetadataError):
            SourceMetadata.from_dict({"source_name": "gfs"})

    def test_from_dict_round_trips_a_valid_dict(self):
        original = SourceMetadata(
            source_name="metar",
            source_product="VOBL METAR",
            observation_time_utc=iso_minutes_ago(5),
            acquisition_time_utc=iso_minutes_ago(4),
            valid_time_utc=iso_minutes_ago(5),
            spatial_resolution="single station",
            temporal_resolution="hourly",
            status=SourceStatus.LIVE,
            freshness_minutes=5.0,
            provenance="forecast.json['metar']",
            quality_flags=["x"],
        )
        restored = SourceMetadata.from_dict(original.to_dict())
        assert restored.to_dict() == original.to_dict()

    def test_from_dict_ignores_unknown_extra_fields(self):
        base = SourceMetadata(
            source_name="metar",
            source_product="VOBL METAR",
            observation_time_utc=iso_minutes_ago(5),
            acquisition_time_utc=iso_minutes_ago(4),
            valid_time_utc=iso_minutes_ago(5),
            spatial_resolution="single station",
            temporal_resolution="hourly",
            status=SourceStatus.LIVE,
            freshness_minutes=5.0,
            provenance="forecast.json['metar']",
        ).to_dict()
        base["unexpected_future_field"] = "surprise"
        restored = SourceMetadata.from_dict(base)
        assert restored.source_name == "metar"


# ---------------------------------------------------------------------------
# Freshness calculation
# ---------------------------------------------------------------------------

class TestFreshnessCalculation:
    def test_freshness_minutes_basic(self):
        ts = iso_minutes_ago(42)
        freshness = compute_freshness_minutes(ts)
        assert 41.5 <= freshness <= 42.5

    def test_freshness_none_for_missing_timestamp(self):
        assert compute_freshness_minutes(None) is None
        assert compute_freshness_minutes("") is None

    def test_freshness_none_for_unparseable_timestamp(self):
        assert compute_freshness_minutes("not-a-timestamp") is None

    def test_freshness_handles_z_suffix(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        freshness = compute_freshness_minutes(ts)
        assert 9.5 <= freshness <= 10.5

    def test_freshness_never_negative(self):
        future_ts = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        assert compute_freshness_minutes(future_ts) == 0.0


# ---------------------------------------------------------------------------
# Source status classification
# ---------------------------------------------------------------------------

class TestStatusClassification:
    def test_classify_live(self):
        assert classify_freshness(5.0, live_max_minutes=60, cached_max_minutes=600) == SourceStatus.LIVE

    def test_classify_cached(self):
        assert classify_freshness(120.0, live_max_minutes=60, cached_max_minutes=600) == SourceStatus.CACHED

    def test_classify_stale(self):
        assert classify_freshness(1000.0, live_max_minutes=60, cached_max_minutes=600) == SourceStatus.STALE

    def test_classify_boundary_is_inclusive_live(self):
        assert classify_freshness(60.0, live_max_minutes=60, cached_max_minutes=600) == SourceStatus.LIVE

    def test_classify_none_is_unavailable(self):
        assert classify_freshness(None, live_max_minutes=60, cached_max_minutes=600) == SourceStatus.UNAVAILABLE

    def test_file_existing_is_not_enough_for_live(self, tmp_path):
        """A source must not be LIVE just because its output file exists --
        it must check the file's embedded observation time against a budget."""
        stale_csv = tmp_path / "gfs_realtime_43295.csv"
        header = ["fetched_at_utc", "gfs_cycle"]
        old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
        with open(stale_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerow([old_ts, "2026-09-30 00Z f012"])

        source = GFSSource(scope="vobl", vobl_csv_path=stale_csv)
        meta = source.get_metadata()
        assert meta.status != SourceStatus.LIVE
        assert meta.status == SourceStatus.STALE


# ---------------------------------------------------------------------------
# Blocked-source behavior
# ---------------------------------------------------------------------------

class TestBlockedSourceBehavior:
    def test_imdaa_blocked_without_credentials(self):
        meta = IMDAASource(env={}).get_metadata()
        assert meta.status == SourceStatus.BLOCKED
        assert "NCMRWF_USER" in meta.provenance or "credential" in meta.provenance.lower()
        assert meta.observation_time_utc is None

    def test_gmgsi_always_unavailable_no_endpoint_verified(self):
        meta = GMGSISource().get_metadata()
        assert meta.status == SourceStatus.UNAVAILABLE
        assert "endpoint" in meta.provenance.lower() or "verif" in meta.provenance.lower()

    def test_imerg_unavailable_without_credentials(self):
        meta = IMERGSource(env={}).get_metadata()
        assert meta.status == SourceStatus.UNAVAILABLE
        assert "EARTHDATA_USER" in meta.provenance or "credential" in meta.provenance.lower()

    def test_imerg_blocked_with_credentials_but_unimplemented_fetch(self):
        meta = IMERGSource(env={"EARTHDATA_USER": "u", "EARTHDATA_PASS": "p"}).get_metadata()
        assert meta.status == SourceStatus.BLOCKED


# ---------------------------------------------------------------------------
# Missing credentials (dedicated, beyond the blocked-behavior smoke tests)
# ---------------------------------------------------------------------------

class TestMissingCredentials:
    def test_imdaa_partial_credentials_still_blocked(self):
        meta = IMDAASource(env={"NCMRWF_USER": "someone"}).get_metadata()
        assert meta.status == SourceStatus.BLOCKED
        assert "NCMRWF_PASS" in meta.provenance

    def test_imdaa_full_credentials_but_no_endpoint_still_blocked(self):
        meta = IMDAASource(env={"NCMRWF_USER": "u", "NCMRWF_PASS": "p"}).get_metadata()
        assert meta.status == SourceStatus.BLOCKED
        assert "NCMRWF_BASE_URL" in meta.provenance

    def test_imdaa_never_reports_live_even_with_everything_set(self):
        meta = IMDAASource(
            env={"NCMRWF_USER": "u", "NCMRWF_PASS": "p", "NCMRWF_BASE_URL": "https://example.invalid"}
        ).get_metadata()
        assert meta.status != SourceStatus.LIVE
        assert meta.status == SourceStatus.BLOCKED


# ---------------------------------------------------------------------------
# Provenance preservation
# ---------------------------------------------------------------------------

class TestProvenancePreservation:
    def test_gfs_vobl_provenance_names_source_file(self, tmp_path):
        csv_path = tmp_path / "gfs_realtime_43295.csv"
        ts = iso_minutes_ago(10)
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["fetched_at_utc", "gfs_cycle"])
            w.writerow([ts, "2026-10-03 00Z f012"])
        meta = GFSSource(scope="vobl", vobl_csv_path=csv_path).get_metadata()
        assert str(csv_path) in meta.provenance
        assert "gfs_fetcher.py" in meta.provenance

    def test_himawari_provenance_names_source_file(self, tmp_path):
        json_path = tmp_path / "himawari_realtime.json"
        record = {"timestamp_utc": iso_minutes_ago(5), "data_source": "live"}
        json_path.write_text(json.dumps(record))
        meta = HimawariSource(realtime_path=json_path).get_metadata()
        assert str(json_path) in meta.provenance
        assert "fetch_himawari_realtime.py" in meta.provenance

    def test_metar_provenance_names_source_file(self, tmp_path):
        forecast_path = tmp_path / "forecast.json"
        forecast_path.write_text(json.dumps({
            "metar": {"obs_time": iso_minutes_ago(5), "fetched_utc": iso_minutes_ago(4), "station": "VOBL"}
        }))
        meta = METARSource(forecast_json_path=forecast_path).get_metadata()
        assert str(forecast_path) in meta.provenance
        assert "fetch_metar.py" in meta.provenance

    def test_dem_provenance_names_source_file_and_is_never_live(self, tmp_path):
        terrain_path = tmp_path / "blr_terrain.json"
        terrain_path.write_text(json.dumps({
            "generated_at": iso_minutes_ago(5), "resolution_deg": 0.01, "n_points": 100
        }))
        meta = DEMSource(terrain_path=terrain_path).get_metadata()
        assert str(terrain_path) in meta.provenance
        assert "fetch_dem_terrain.py" in meta.provenance
        # DEM is static -- a freshly regenerated file is CACHED, never LIVE.
        assert meta.status == SourceStatus.CACHED

    def test_unavailable_metadata_still_carries_a_provenance_reason(self, tmp_path):
        missing_path = tmp_path / "does_not_exist.csv"
        meta = GFSSource(scope="vobl", vobl_csv_path=missing_path).get_metadata()
        assert meta.status == SourceStatus.UNAVAILABLE
        assert str(missing_path) in meta.provenance


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_known_sources_are_registered(self):
        names = list_sources()
        for expected in ("gfs_vobl", "gfs_pan_india", "himawari", "metar", "dem", "imdaa", "imerg", "gmgsi"):
            assert expected in names

    def test_get_source_unknown_name_raises(self):
        with pytest.raises(KeyError):
            get_source("not_a_real_source")

    def test_get_all_metadata_never_raises_and_covers_all_sources(self):
        result = get_all_metadata()
        assert set(result.keys()) == set(list_sources())
        for name, meta in result.items():
            assert meta["status"] in [s.value for s in SourceStatus]

    def test_gfs_scopes_are_distinct_adapters(self):
        vobl = get_source("gfs_vobl")
        pan_india = get_source("gfs_pan_india")
        assert vobl.scope == "vobl"
        assert pan_india.scope == "pan_india"
