"""
Unit tests for backend/data_sources/himawari_features.py (Phase 2).

Covers: single-frame extraction, multi-frame extraction, cooling-rate
calculation, missing-frame behavior, timestamp ordering, invalid pixel
handling, quality flags, provenance, and stale-data detection.

Uses deterministic fixtures written to tmp_path, matching the exact schema
fetch_himawari_realtime.py actually writes. Also exercises the module
against the repo's own real data/himawari_realtime.json and
data/himawari_history.json where present (read-only; never modified).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.data_sources.himawari_features import (
    HimawariFeatureSet,
    extract_himawari_features,
    compute_spatial_statistics_from_pixels,
    BT_MIN_PLAUSIBLE_K,
    OFFSET_TOLERANCE_MINUTES,
    STALE_MINUTES,
    ANALYSIS_RADIUS_KM,
    COLD_PIXEL_THRESHOLD_C,
)

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_REALTIME = REPO_ROOT / "data" / "himawari_realtime.json"
REAL_HISTORY = REPO_ROOT / "data" / "himawari_history.json"


def frame(ts: datetime, vobl_bt=10.0, min_bt=5.0, mean_bt=12.0, cold_count=0,
          storm=False, nearest_km=None, data_source="Himawari-9 Band 13 (10.4um) via NOAA AWS S3"):
    return {
        "timestamp_utc": ts.strftime("%Y-%m-%dT%H:%M:%S"),
        "timestamp_ist": (ts + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%S"),
        "vobl_bt_celsius": vobl_bt,
        "min_bt_50km": min_bt,
        "mean_bt_50km": mean_bt,
        "cold_pixels_count": cold_count,
        "storm_detected": storm,
        "nearest_pixel_dist_km": nearest_km,
        "threshold_celsius": -40.0,
        "data_source": data_source,
    }


def write(path: Path, obj):
    path.write_text(json.dumps(obj))


# ---------------------------------------------------------------------------
# Single-frame extraction
# ---------------------------------------------------------------------------

class TestSingleFrameExtraction:
    def test_single_frame_populates_current_features(self, tmp_path):
        now = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        ts = now - timedelta(minutes=5)
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frame(ts, vobl_bt=8.0, min_bt=-2.0, mean_bt=10.0, cold_count=3))
        write(hist_path, [])  # no history yet -- first-ever run

        result = extract_himawari_features(rt_path, hist_path, now=now)
        assert isinstance(result, HimawariFeatureSet)
        assert result.feature_values["brightness_temperature_vobl_c"] == 8.0
        assert result.feature_values["ir_cloud_temperature_proxy_min_50km_c"] == -2.0
        assert result.feature_values["cold_pixel_count_50km"] == 3

    def test_single_frame_marks_temporal_features_unavailable(self, tmp_path):
        now = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        ts = now - timedelta(minutes=5)
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frame(ts))
        write(hist_path, [])

        result = extract_himawari_features(rt_path, hist_path, now=now)
        assert "temporal_features_unavailable_single_frame_only" in result.quality_flags
        assert result.feature_values["brightness_temperature_drop_10m_c"] is None
        assert result.feature_values["cooling_rate_c_per_hour"] is None
        assert result.temporal_coverage["frames_available"] == 1
        assert result.temporal_coverage["offsets_satisfied_minutes"] == []

    def test_no_data_at_all(self, tmp_path):
        result = extract_himawari_features(tmp_path / "missing_rt.json", tmp_path / "missing_hist.json")
        assert "no_data_available" in result.quality_flags
        assert result.observation_time_utc is None
        assert all(v in (None, [], 0, {}) for v in [
            result.feature_values["brightness_temperature_vobl_c"],
        ])


# ---------------------------------------------------------------------------
# Multi-frame extraction + cooling rate
# ---------------------------------------------------------------------------

class TestMultiFrameExtraction:
    def test_exact_10_20_30_min_frames_all_satisfied(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        frames = [
            frame(t0 - timedelta(minutes=30), min_bt=10.0),
            frame(t0 - timedelta(minutes=20), min_bt=5.0),
            frame(t0 - timedelta(minutes=10), min_bt=0.0),
            frame(t0, min_bt=-10.0),
        ]
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frames[-1])
        write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0 + timedelta(minutes=2))
        assert result.temporal_coverage["offsets_satisfied_minutes"] == [10, 20, 30]
        assert result.feature_values["brightness_temperature_drop_10m_c"] == 10.0   # 0.0 -> -10.0
        assert result.feature_values["brightness_temperature_drop_20m_c"] == 15.0   # 5.0 -> -10.0
        assert result.feature_values["brightness_temperature_drop_30m_c"] == 20.0   # 10.0 -> -10.0
        # cooling_rate for the 10m offset: 10C over 10 minutes = 60 C/hour
        assert result.feature_values["cooling_rate_c_per_hour_by_offset"]["10"] == 60.0

    def test_cooling_rate_uses_actual_gap_not_assumed_interval(self, tmp_path):
        """A frame found at t-27m (not t-30m exactly) must compute the rate
        using 27 minutes, never silently assuming 30."""
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        old = t0 - timedelta(minutes=27)  # within 3-min tolerance of the 30m target? No: |30-27|=3, exactly at tolerance edge
        frames = [frame(old, min_bt=10.0), frame(t0, min_bt=-10.0)]
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frames[-1])
        write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0 + timedelta(minutes=1))
        assert 30 in result.temporal_coverage["offsets_satisfied_minutes"]
        # drop = 10 - (-10) = 20C over 27 minutes = 44.44 C/hour
        rate = result.feature_values["cooling_rate_c_per_hour_by_offset"]["30"]
        assert abs(rate - (20.0 / (27.0 / 60.0))) < 0.01

    def test_warming_produces_negative_drop(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        frames = [frame(t0 - timedelta(minutes=10), min_bt=-10.0), frame(t0, min_bt=5.0)]
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frames[-1])
        write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert result.feature_values["brightness_temperature_drop_10m_c"] == -15.0
        assert result.feature_values["rapid_cloud_growth_flag"] is False

    def test_rapid_cloud_growth_flag_true_above_threshold(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        # 20C drop in 10 min = 120 C/hour, well above the 15 C/hour heuristic
        frames = [frame(t0 - timedelta(minutes=10), min_bt=10.0), frame(t0, min_bt=-10.0)]
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frames[-1])
        write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert result.feature_values["rapid_cloud_growth_flag"] is True


# ---------------------------------------------------------------------------
# Missing-frame behavior (CRITICAL: never fabricate/interpolate)
# ---------------------------------------------------------------------------

class TestMissingFrameBehavior:
    def test_offset_with_no_matching_frame_is_unavailable_not_interpolated(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        # Only a frame at t-20m exists; t-10m and t-30m have nothing nearby.
        frames = [frame(t0 - timedelta(minutes=20), min_bt=0.0), frame(t0, min_bt=-5.0)]
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frames[-1])
        write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert result.temporal_coverage["offsets_satisfied_minutes"] == [20]
        assert 10 in result.temporal_coverage["offsets_unavailable_minutes"]
        assert 30 in result.temporal_coverage["offsets_unavailable_minutes"]
        assert result.feature_values["brightness_temperature_drop_10m_c"] is None
        assert result.feature_values["brightness_temperature_drop_30m_c"] is None
        assert any("offsets_unavailable" in f for f in result.quality_flags)

    def test_frame_just_outside_tolerance_is_rejected(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        just_outside = t0 - timedelta(minutes=10 + OFFSET_TOLERANCE_MINUTES + 0.5)
        frames = [frame(just_outside, min_bt=0.0), frame(t0, min_bt=-5.0)]
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frames[-1])
        write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert 10 in result.temporal_coverage["offsets_unavailable_minutes"]
        assert result.feature_values["brightness_temperature_drop_10m_c"] is None

    def test_water_vapor_always_unavailable(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frame(t0))
        write(hist_path, [])
        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert result.feature_values["water_vapor_brightness_temperature_c"] is None
        assert "water_vapor_channel_unavailable" in result.quality_flags

    def test_spatial_variance_always_unavailable_without_raw_pixels(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frame(t0))
        write(hist_path, [])
        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert result.feature_values["spatial_gradient_std_c"] is None
        assert result.feature_values["cold_pixel_fraction_50km"] is None


# ---------------------------------------------------------------------------
# Timestamp ordering / duplicates
# ---------------------------------------------------------------------------

class TestTimestampOrdering:
    def test_out_of_order_history_is_corrected_not_fabricated(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        # Deliberately out of chronological order in the file
        frames_on_disk = [
            frame(t0, min_bt=-10.0),
            frame(t0 - timedelta(minutes=10), min_bt=0.0),
        ]
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frames_on_disk[0])
        write(hist_path, frames_on_disk)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert "frames_were_out_of_order_corrected" in result.quality_flags
        # Still correctly finds the real t-10m frame despite on-disk ordering
        assert 10 in result.temporal_coverage["offsets_satisfied_minutes"]
        assert result.feature_values["brightness_temperature_drop_10m_c"] == 10.0

    def test_duplicate_timestamps_are_deduped(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        frames = [frame(t0, min_bt=-10.0), frame(t0, min_bt=-10.0)]
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frames[0])
        write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert "duplicate_timestamp_dropped" in result.quality_flags
        assert result.temporal_coverage["frames_available"] == 1


# ---------------------------------------------------------------------------
# Invalid pixel handling / quality flags
# ---------------------------------------------------------------------------

class TestInvalidPixelHandling:
    def test_impossible_temperature_flagged(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        # 500C is not a physically plausible Earth-scene IR brightness temp
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frame(t0, min_bt=500.0))
        write(hist_path, [])

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert "min_bt_50km_outside_physically_plausible_range" in result.quality_flags

    def test_nan_value_flagged_and_treated_as_missing(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        raw = frame(t0)
        raw["min_bt_50km"] = float("nan")
        write(rt_path, raw)
        write(hist_path, [])

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert "min_bt_50km_nan" in result.quality_flags
        assert result.feature_values["ir_cloud_temperature_proxy_min_50km_c"] is None

    def test_missing_pixel_data_from_placeholder_frame(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        placeholder = frame(t0, vobl_bt=None, min_bt=None, mean_bt=None,
                             data_source="Himawari-9 -- UNAVAILABLE (S3/JAXA unreachable)")
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, placeholder)
        write(hist_path, [])

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert "source_reported_unavailable" in result.quality_flags
        assert "latest_frame_unavailable_placeholder" in result.quality_flags
        assert result.feature_values["ir_cloud_temperature_proxy_min_50km_c"] is None

    def test_corrupt_realtime_file(self, tmp_path):
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        rt_path.write_text("{not valid json")
        write(hist_path, [])

        result = extract_himawari_features(rt_path, hist_path)
        assert "corrupt_realtime_file" in result.quality_flags


# ---------------------------------------------------------------------------
# Stale-data detection
# ---------------------------------------------------------------------------

class TestStaleDataDetection:
    def test_fresh_frame_not_flagged_stale(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frame(t0 - timedelta(minutes=10)))
        write(hist_path, [])

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert "stale_imagery" not in result.quality_flags

    def test_old_frame_flagged_stale(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frame(t0 - timedelta(minutes=STALE_MINUTES + 30)))
        write(hist_path, [])

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert "stale_imagery" in result.quality_flags


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_provenance_names_both_source_files(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        write(rt_path, frame(t0))
        write(hist_path, [frame(t0 - timedelta(minutes=10))])

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        joined = " ".join(result.provenance)
        assert str(rt_path) in joined
        assert str(hist_path) in joined
        assert "fetch_himawari_realtime.py" in joined

    def test_provenance_names_exact_matched_frame_gap(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path = tmp_path / "realtime.json"
        hist_path = tmp_path / "history.json"
        frames = [frame(t0 - timedelta(minutes=10), min_bt=0.0), frame(t0, min_bt=-5.0)]
        write(rt_path, frames[-1])
        write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        joined = " ".join(result.provenance)
        assert "t-10m matched to a real frame" in joined


# ---------------------------------------------------------------------------
# Real repository data (read-only) -- "use real sample data where possible"
# ---------------------------------------------------------------------------

class TestAgainstRealRepoData:
    @pytest.mark.skipif(not REAL_REALTIME.exists() or not REAL_HISTORY.exists(),
                         reason="real himawari data files not present in this checkout")
    def test_extracts_without_crashing_against_real_committed_data(self):
        result = extract_himawari_features(REAL_REALTIME, REAL_HISTORY)
        assert result.observation_time_utc is not None
        assert result.temporal_coverage["frames_available"] >= 1
        # Must never claim water vapor or raw-pixel spatial stats from real
        # data either -- the fetcher doesn't ingest WV and doesn't persist
        # pixel arrays, so these must stay None even against real data.
        assert result.feature_values["water_vapor_brightness_temperature_c"] is None
        assert result.feature_values["cold_pixel_fraction_50km"] is None
        assert result.feature_values["brightness_temperature_std_c"] is None
        assert "spatial_pixel_statistics_unavailable_no_raw_array_provided_this_run" in result.quality_flags
        # Headline temporal fields must never claim an assumed interval --
        # if a headline window exists at all, its interval must be recorded.
        if result.feature_values["cooling_rate_c_per_hour"] is not None:
            assert result.feature_values["brightness_temperature_drop_interval_minutes"] is not None


# ---------------------------------------------------------------------------
# Phase 2 hardening: exact 10/20/30-min frame handling with explicit
# actual_frame_time_utc / actual_gap_minutes (never an assumed interval)
# ---------------------------------------------------------------------------

class TestExactOffsetFrames:
    def test_exact_10_minute_frame(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        frames = [frame(t0 - timedelta(minutes=10), min_bt=4.0), frame(t0, min_bt=-6.0)]
        rt_path, hist_path = tmp_path / "realtime.json", tmp_path / "history.json"
        write(rt_path, frames[-1]); write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert result.temporal_coverage["offset_actual_gap_minutes"]["10"] == 10.0
        assert result.temporal_coverage["offset_actual_frame_time_utc"]["10"] == (t0 - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert result.feature_values["brightness_temperature_drop_10m_c"] == 10.0

    def test_exact_20_minute_frame(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        frames = [frame(t0 - timedelta(minutes=20), min_bt=4.0), frame(t0, min_bt=-6.0)]
        rt_path, hist_path = tmp_path / "realtime.json", tmp_path / "history.json"
        write(rt_path, frames[-1]); write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert result.temporal_coverage["offset_actual_gap_minutes"]["20"] == 20.0
        assert result.feature_values["brightness_temperature_drop_20m_c"] == 10.0

    def test_exact_30_minute_frame(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        frames = [frame(t0 - timedelta(minutes=30), min_bt=4.0), frame(t0, min_bt=-6.0)]
        rt_path, hist_path = tmp_path / "realtime.json", tmp_path / "history.json"
        write(rt_path, frames[-1]); write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert result.temporal_coverage["offset_actual_gap_minutes"]["30"] == 30.0
        assert result.feature_values["brightness_temperature_drop_30m_c"] == 10.0

    def test_missing_frame_records_null_actual_frame_time(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        frames = [frame(t0, min_bt=-6.0)]  # only the latest -- nothing at any offset
        rt_path, hist_path = tmp_path / "realtime.json", tmp_path / "history.json"
        write(rt_path, frames[-1]); write(hist_path, [])

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        for offset in (10, 20, 30):
            assert result.temporal_coverage["offset_actual_frame_time_utc"][str(offset)] is None

    def test_irregular_spacing_only_satisfies_the_offset_that_actually_matches(self, tmp_path):
        """Mirrors the real repo's own irregular Himawari cadence: frames at
        16:10, 20:50, 21:20, 21:30, 02:20, 02:40 -- only t-20m has a real match."""
        frames_ts = ["2026-10-02T16:10:00", "2026-10-02T20:50:00", "2026-10-02T21:20:00",
                     "2026-10-02T21:30:00", "2026-10-03T02:20:00", "2026-10-03T02:40:00"]
        bts = [4.43, -0.18, 2.69, -5.73, -4.87, -23.49]
        frames = [frame(datetime.fromisoformat(ts).replace(tzinfo=timezone.utc), min_bt=bt)
                  for ts, bt in zip(frames_ts, bts)]
        rt_path, hist_path = tmp_path / "realtime.json", tmp_path / "history.json"
        write(rt_path, frames[-1]); write(hist_path, frames)

        now = datetime(2026, 10, 3, 2, 45, tzinfo=timezone.utc)
        result = extract_himawari_features(rt_path, hist_path, now=now)
        assert result.temporal_coverage["offsets_satisfied_minutes"] == [20]
        assert result.temporal_coverage["offsets_unavailable_minutes"] == [10, 30]
        # -23.49 at 02:40 vs -4.87 at 02:20 -> drop = -4.87 - (-23.49) = 18.62
        assert result.feature_values["brightness_temperature_drop_20m_c"] == 18.62

    def test_cooling_rate_never_mislabels_actual_interval_as_an_hour(self, tmp_path):
        """Regression guard for the exact mislabeling found in production's
        bt_trend_1h: a 20-minute real gap must never be reported as 1h."""
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        frames = [frame(t0 - timedelta(minutes=20), min_bt=4.87), frame(t0, min_bt=-18.62)]
        rt_path, hist_path = tmp_path / "realtime.json", tmp_path / "history.json"
        write(rt_path, frames[-1]); write(hist_path, frames)

        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert result.feature_values["brightness_temperature_drop_interval_minutes"] == 20.0
        assert not any("1h" in flag or "_1h" in flag for flag in result.quality_flags)
        assert "cooling_rate_c_per_hour" in result.feature_values  # rate is explicitly per-hour UNITS, not a 1h WINDOW
        # 23.49C over 20 minutes = 70.47 C/hour
        assert abs(result.feature_values["cooling_rate_c_per_hour"] - 70.47) < 0.01


# ---------------------------------------------------------------------------
# Phase 2 hardening: real pixel-level spatial statistics
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAVE_NUMPY, reason="numpy not installed")
class TestSpatialPixelStatistics:
    def _grid(self, n=21, spacing_deg=0.02, center_lat=13.1986, center_lon=77.7066):
        lats = np.array([[center_lat + (i - n // 2) * spacing_deg for _ in range(n)] for i in range(n)])
        lons = np.array([[center_lon + (j - n // 2) * spacing_deg for j in range(n)] for i in range(n)])
        return lats, lons

    def test_uniform_warm_field_has_zero_cold_fraction(self):
        lats, lons = self._grid()
        bt = np.full(lats.shape, 300.0)  # 26.85C everywhere -- no cold pixels
        stats, flags = compute_spatial_statistics_from_pixels(bt, lats, lons, 13.1986, 77.7066)
        assert stats["cold_pixel_fraction_50km"] == 0.0
        assert stats["valid_pixel_count_50km"] > 0
        assert flags == []

    def test_cold_cluster_increases_cold_pixel_fraction(self):
        lats, lons = self._grid()
        bt = np.full(lats.shape, 300.0)
        bt[8:13, 8:13] = 220.0  # -53.15C, below the -40C threshold
        stats, flags = compute_spatial_statistics_from_pixels(bt, lats, lons, 13.1986, 77.7066)
        assert stats["cold_pixel_fraction_50km"] > 0
        assert stats["brightness_temperature_min_c"] == pytest.approx(-53.15, abs=0.01)

    def test_valid_pixel_count_excludes_nan(self):
        lats, lons = self._grid()
        bt = np.full(lats.shape, 300.0)
        bt[0, 0] = np.nan
        stats, _ = compute_spatial_statistics_from_pixels(bt, lats, lons, 13.1986, 77.7066)
        total_in_radius = stats["valid_pixel_count_50km"]
        bt2 = np.full(lats.shape, 300.0)  # same grid, no NaN
        stats2, _ = compute_spatial_statistics_from_pixels(bt2, lats, lons, 13.1986, 77.7066)
        assert total_in_radius <= stats2["valid_pixel_count_50km"]

    def test_percentiles_are_monotonic_and_bracket_the_mean(self):
        lats, lons = self._grid()
        rng = np.random.RandomState(42)  # deterministic -- not "fake satellite data", a controlled unit-test fixture
        bt = 280.0 + rng.normal(0, 5, size=lats.shape)
        stats, _ = compute_spatial_statistics_from_pixels(bt, lats, lons, 13.1986, 77.7066)
        p = stats
        assert p["brightness_temperature_p10_c"] <= p["brightness_temperature_p25_c"] <= p["brightness_temperature_p50_c"]
        assert p["brightness_temperature_p50_c"] <= p["brightness_temperature_p75_c"] <= p["brightness_temperature_p90_c"]

    def test_std_is_zero_for_uniform_field(self):
        lats, lons = self._grid()
        bt = np.full(lats.shape, 300.0)
        stats, _ = compute_spatial_statistics_from_pixels(bt, lats, lons, 13.1986, 77.7066)
        assert stats["brightness_temperature_std_c"] == 0.0

    def test_implausible_pixels_excluded_and_flagged(self):
        lats, lons = self._grid()
        bt = np.full(lats.shape, 300.0)
        bt[0, 0] = 999.0  # not a physically plausible Earth-scene brightness temp
        stats, flags = compute_spatial_statistics_from_pixels(bt, lats, lons, 13.1986, 77.7066)
        assert any("excluded" in f and "physically_plausible" in f for f in flags)
        # The implausible pixel must not pollute min/percentiles
        assert stats["brightness_temperature_min_c"] < 999.0 - 273.15

    def test_empty_array_returns_none_not_zero(self):
        stats, flags = compute_spatial_statistics_from_pixels(
            np.array([]).reshape(0, 0), np.array([]).reshape(0, 0), np.array([]).reshape(0, 0),
            13.1986, 77.7066,
        )
        assert stats["valid_pixel_count_50km"] is None
        assert "pixel_array_empty" in flags

    def test_no_raw_pixels_means_all_spatial_fields_none(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path, hist_path = tmp_path / "realtime.json", tmp_path / "history.json"
        write(rt_path, frame(t0)); write(hist_path, [])
        result = extract_himawari_features(rt_path, hist_path, now=t0, raw_pixels=None)
        assert result.feature_values["cold_pixel_fraction_50km"] is None
        assert result.feature_values["valid_pixel_count_50km"] is None
        assert "spatial_pixel_statistics_unavailable_no_raw_array_provided_this_run" in result.quality_flags

    def test_real_raw_pixels_populate_spatial_fields_via_main_entrypoint(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path, hist_path = tmp_path / "realtime.json", tmp_path / "history.json"
        write(rt_path, frame(t0)); write(hist_path, [])
        lats, lons = self._grid()
        bt = np.full(lats.shape, 300.0)
        bt[8:13, 8:13] = 220.0

        result = extract_himawari_features(rt_path, hist_path, now=t0,
                                            raw_pixels={"bt": bt, "lats": lats, "lons": lons})
        assert result.feature_values["cold_pixel_fraction_50km"] is not None
        assert result.feature_values["cold_pixel_fraction_50km"] > 0
        assert result.feature_values["valid_pixel_count_50km"] > 0
        assert result.feature_values["analysis_radius_km"] == ANALYSIS_RADIUS_KM


# ---------------------------------------------------------------------------
# Phase 2 hardening: terminology guard -- "cold_cloud_fraction" must never
# appear unqualified; "cold_pixel_fraction" is the only accepted name
# ---------------------------------------------------------------------------

class TestTerminologyGuard:
    def test_no_bare_cold_cloud_fraction_field_name(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path, hist_path = tmp_path / "realtime.json", tmp_path / "history.json"
        write(rt_path, frame(t0)); write(hist_path, [])
        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert "cold_cloud_fraction_50km" not in result.feature_values
        assert "cold_pixel_fraction_50km" in result.feature_values

    def test_no_bt_trend_1h_style_field_anywhere(self, tmp_path):
        t0 = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
        rt_path, hist_path = tmp_path / "realtime.json", tmp_path / "history.json"
        write(rt_path, frame(t0)); write(hist_path, [frame(t0 - timedelta(minutes=20))])
        result = extract_himawari_features(rt_path, hist_path, now=t0)
        assert "bt_trend_1h" not in result.feature_values
        assert not any(k.endswith("_1h") for k in result.feature_values)
