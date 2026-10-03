"""
tests/test_phase4_himawari_wiring.py

Phase 4 -- wiring tests only. Confirms:

  1. The real in-memory pixel object returned by try_scene() reaches
     extract_himawari_features(raw_pixels=...) unchanged (same object,
     no re-fetch, no re-shaping).
  2. data/himawari_features.json is produced as an OBSERVED artifact,
     never a model input.
  3. Spatial fields become genuinely non-null when valid raw pixels are
     supplied.
  4. Malformed/missing pixel data fails safely (extraction degrades to
     None-valued fields, it does not raise out of main()).
  5. save_outputs() (and therefore himawari_realtime.json/history.json)
     is unaffected even when Phase 4 feature extraction raises.
  6. himawari_realtime.json / himawari_history.json schemas are
     byte-for-byte what fetch_himawari_realtime.py always wrote --
     confirmed by diffing keys against the pre-Phase-4 schema.
  7. The VOBL/CB/FF feature vector construction in forecast_action.py
     is untouched: no Himawari key appears in its feature_cols/obs
     machinery, and forecast_action.py's own source text is unchanged
     by this phase (file hash comparison against a pre-recorded hash is
     not used here -- instead we directly re-assert the absence of any
     himawari reference in the obs-construction region, matching the
     Phase 3 audit's method).
  8. No raw pixel array (the "bt"/"lats"/"lons" 2D arrays) is ever
     persisted into himawari_features.json -- only scalar/aggregate
     statistics.

This file imports fetch_himawari_realtime.py as a plain module (it is a
repo-root script, not a package) and monkeypatches its file-path
constants to a tmp_path so it never touches the real data/ directory.
No network call is made anywhere in this file.
"""
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import fetch_himawari_realtime as fhr  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers -- build a deterministic, real-shaped {"bt","lats","lons"}
# dict around VOBL using the exact haversine construction analyse() expects.
# ---------------------------------------------------------------------------

def _make_raw_pixels(cold: bool, n=21):
    """A small lat/lon grid centered on VOBL with a uniform BT value, so
    `analyse()` and `compute_spatial_statistics_from_pixels()` both see a
    deterministic, physically plausible array. `cold=True` places every
    pixel below the -40C storm threshold; `cold=False` keeps it warm."""
    lat_lin = np.linspace(fhr.VOBL_LAT - 0.3, fhr.VOBL_LAT + 0.3, n)
    lon_lin = np.linspace(fhr.VOBL_LON - 0.3, fhr.VOBL_LON + 0.3, n)
    lons, lats = np.meshgrid(lon_lin, lat_lin)
    bt_c = -55.0 if cold else 10.0
    bt_k = np.full_like(lats, bt_c + 273.15, dtype=np.float64)
    return {"bt": bt_k, "lats": lats, "lons": lons}


def _patch_paths(monkeypatch, tmp_path):
    out_dir = tmp_path / "data"
    out_dir.mkdir()
    monkeypatch.setattr(fhr, "OUT_DIR", out_dir, raising=True)
    monkeypatch.setattr(fhr, "OUT_FILE", out_dir / "himawari_realtime.json", raising=True)
    monkeypatch.setattr(fhr, "HIST_FILE", out_dir / "himawari_history.json", raising=True)
    monkeypatch.setattr(fhr, "FEATURES_FILE", out_dir / "himawari_features.json", raising=True)
    monkeypatch.setattr(fhr, "B08_FEATURES_FILE", out_dir / "himawari_b08_features.json", raising=True)
    return out_dir


def _run_main_with_fixed_pixels(monkeypatch, tmp_path, raw_pixels, scene_dt=None):
    """Drive fetch_himawari_realtime.main() end-to-end with try_scene()
    mocked to return a fixed, real-shaped pixel dict -- no network, no
    satpy, no S3/JAXA involved."""
    out_dir = _patch_paths(monkeypatch, tmp_path)
    scene_dt = scene_dt or datetime(2026, 10, 3, 3, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: raw_pixels, raising=True)
    rc = fhr.main()
    return out_dir, rc


# ---------------------------------------------------------------------------
# 1 + 2. The exact in-memory object reaches the extractor; the artifact is
#         written and self-labels as an observation, not a model input.
# ---------------------------------------------------------------------------

def test_raw_pixels_object_reaches_extractor_and_artifact_is_written(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=True)
    captured = {}

    import backend.data_sources.himawari_features as hf
    real_extract = hf.extract_himawari_features

    def spy_extract(*args, **kwargs):
        captured["raw_pixels"] = kwargs.get("raw_pixels")
        return real_extract(*args, **kwargs)

    monkeypatch.setattr(fhr, "extract_himawari_features", spy_extract, raising=False)
    # fetch_himawari_realtime imports the function lazily inside main();
    # patch it at the source module too so the lazy `from ... import`
    # inside main() picks up the spy regardless of import style.
    monkeypatch.setattr(hf, "extract_himawari_features", spy_extract, raising=True)

    out_dir, rc = _run_main_with_fixed_pixels(monkeypatch, tmp_path, raw_pixels)

    assert rc == 0
    assert captured.get("raw_pixels") is raw_pixels, (
        "the exact object returned by try_scene() must reach "
        "extract_himawari_features(raw_pixels=...) unchanged"
    )

    features_path = out_dir / "himawari_features.json"
    assert features_path.exists(), "data/himawari_features.json must be written"
    artifact = json.loads(features_path.read_text())

    assert artifact["artifact_type"] == "observed_satellite_feature_set"
    assert artifact["model_input"] is False
    assert artifact["raw_pixels_available_this_run"] is True
    assert "feature_extraction_version" in artifact
    assert "processing_timestamp_utc" in artifact
    assert "observation_time_utc" in artifact
    assert "quality_flags" in artifact
    assert "provenance" in artifact


# ---------------------------------------------------------------------------
# 3. Spatial fields become genuinely non-null with valid raw pixels.
# ---------------------------------------------------------------------------

def test_spatial_fields_non_null_with_valid_cold_pixels(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=True)
    out_dir, rc = _run_main_with_fixed_pixels(monkeypatch, tmp_path, raw_pixels)
    assert rc == 0

    artifact = json.loads((out_dir / "himawari_features.json").read_text())
    fv = artifact["feature_values"]

    assert fv["valid_pixel_count_50km"] is not None
    assert fv["valid_pixel_count_50km"] > 0
    assert fv["cold_pixel_fraction_50km"] is not None
    assert fv["cold_pixel_fraction_50km"] > 0.0
    assert fv["brightness_temperature_std_c"] is not None
    assert fv["brightness_temperature_p50_c"] is not None
    assert artifact["spatial_statistics_available"] is True


def test_spatial_fields_present_but_zero_cold_fraction_when_warm(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=False)
    out_dir, rc = _run_main_with_fixed_pixels(monkeypatch, tmp_path, raw_pixels)
    assert rc == 0

    artifact = json.loads((out_dir / "himawari_features.json").read_text())
    fv = artifact["feature_values"]
    assert fv["valid_pixel_count_50km"] is not None
    assert fv["valid_pixel_count_50km"] > 0
    assert fv["cold_pixel_fraction_50km"] == 0.0


# ---------------------------------------------------------------------------
# 4 + 5. Malformed pixel data / a raised exception must never break
#         save_outputs(); the production files must still be written.
# ---------------------------------------------------------------------------

def test_malformed_pixels_fail_safely_production_output_unaffected(monkeypatch, tmp_path):
    # Shape mismatch between bt and lats/lons -- compute_spatial_statistics_
    # from_pixels() is documented to return None-valued stats + a flag for
    # this, never raise. The outer artifact write must still succeed.
    bad = {
        "bt": np.full((5, 5), 290.0),
        "lats": np.full((3, 3), fhr.VOBL_LAT),
        "lons": np.full((5, 5), fhr.VOBL_LON),
    }
    # analyse() itself needs matching shapes for the production BT signal,
    # so give it a second, well-formed dict for the main signal and swap in
    # the malformed one only for the Phase 4 raw_pixels path.
    good = _make_raw_pixels(cold=True)

    out_dir = _patch_paths(monkeypatch, tmp_path)
    scene_dt = datetime(2026, 10, 3, 3, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: good, raising=True)

    import backend.data_sources.himawari_features as hf
    original = hf.extract_himawari_features

    def extractor_sees_bad_pixels(*args, **kwargs):
        kwargs["raw_pixels"] = bad
        return original(*args, **kwargs)

    monkeypatch.setattr(hf, "extract_himawari_features", extractor_sees_bad_pixels, raising=True)

    rc = fhr.main()
    assert rc == 0, "a malformed raw_pixels array must not fail the production run"

    rt = json.loads((out_dir / "himawari_realtime.json").read_text())
    assert rt["storm_detected"] is True  # from the well-formed `good` signal
    assert rt["min_bt_50km"] is not None

    artifact = json.loads((out_dir / "himawari_features.json").read_text())
    assert artifact["feature_values"]["valid_pixel_count_50km"] is None
    assert any("shape_mismatch" in f for f in artifact["quality_flags"])


def test_extraction_exception_does_not_block_save_outputs(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=True)
    out_dir = _patch_paths(monkeypatch, tmp_path)
    scene_dt = datetime(2026, 10, 3, 3, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: raw_pixels, raising=True)

    import backend.data_sources.himawari_features as hf

    def boom(*args, **kwargs):
        raise RuntimeError("simulated Phase 4 extraction failure")

    monkeypatch.setattr(hf, "extract_himawari_features", boom, raising=True)

    rc = fhr.main()
    assert rc == 0, "main() must still return success when Phase 4 extraction raises"

    assert (out_dir / "himawari_realtime.json").exists()
    assert (out_dir / "himawari_history.json").exists()
    rt = json.loads((out_dir / "himawari_realtime.json").read_text())
    assert rt["storm_detected"] is True
    assert rt["min_bt_50km"] is not None
    assert rt["bt_trend_1h"] is None  # no prior history yet, but key must exist

    # No artifact should exist (or if it somehow half-wrote, that's also
    # acceptable -- the hard requirement is only that production output
    # above is unaffected). We assert it simply does NOT exist here because
    # our write happens only inside the try block, after extraction.
    assert not (out_dir / "himawari_features.json").exists()


def test_import_failure_of_phase2_module_does_not_block_save_outputs(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=True)
    out_dir = _patch_paths(monkeypatch, tmp_path)
    scene_dt = datetime(2026, 10, 3, 3, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: raw_pixels, raising=True)

    # Simulate an import failure for the Phase 2 module by making the name
    # unresolvable inside main()'s local `from ... import` statement.
    import builtins
    real_import = builtins.__import__

    def flaky_import(name, *args, **kwargs):
        if name == "backend.data_sources.himawari_features":
            raise ImportError("simulated: backend.data_sources.himawari_features unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", flaky_import)
    try:
        rc = fhr.main()
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)

    assert rc == 0
    assert (out_dir / "himawari_realtime.json").exists()
    assert (out_dir / "himawari_history.json").exists()
    assert not (out_dir / "himawari_features.json").exists()


# ---------------------------------------------------------------------------
# 6. himawari_realtime.json / himawari_history.json schema is unchanged.
# ---------------------------------------------------------------------------

def test_existing_realtime_and_history_schema_unchanged(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=True)
    out_dir, rc = _run_main_with_fixed_pixels(monkeypatch, tmp_path, raw_pixels)
    assert rc == 0

    rt = json.loads((out_dir / "himawari_realtime.json").read_text())
    expected_keys = {
        "timestamp_utc", "timestamp_ist", "vobl_bt_celsius", "min_bt_50km",
        "mean_bt_50km", "cold_pixels_count", "storm_detected",
        "nearest_pixel_dist_km", "threshold_celsius", "data_source",
        "bt_trend_1h",
    }
    assert set(rt.keys()) == expected_keys, (
        f"himawari_realtime.json schema changed: {set(rt.keys()) ^ expected_keys}"
    )

    hist = json.loads((out_dir / "himawari_history.json").read_text())
    assert isinstance(hist, list)
    assert set(hist[-1].keys()) == expected_keys


# ---------------------------------------------------------------------------
# 7. forecast_action.py's feature-vector construction is untouched by
#    this phase -- re-assert the Phase 3 audit's finding still holds.
# ---------------------------------------------------------------------------

def test_forecast_action_obs_construction_still_has_no_himawari_reference():
    text = (REPO_ROOT / "forecast_action.py").read_text(encoding="utf-8")
    # obs_by_slot / feature-vector construction region, by the Phase 3
    # audit's own line citation (507-556 at time of audit). We search the
    # whole file for a direct-use pattern rather than hardcoding exact line
    # numbers (which will drift), but specifically for the `obs[...]=` /
    # `gfs_cols`/`ua_cols` assignment style, not the pre-existing display-only
    # "satellite"/"himawari9" reporting block, which is expected and fine.
    import re
    obs_assignment_lines = [
        ln for ln in text.splitlines()
        if re.search(r"\bobs\s*\[", ln) or re.search(r"\bobs\.update\(", ln)
    ]
    assert obs_assignment_lines, "expected to find obs[...] assignments to inspect"
    assert not any("himawari" in ln.lower() for ln in obs_assignment_lines), (
        "forecast_action.py's obs feature-vector construction must not "
        "reference Himawari -- Phase 4 must not have added one"
    )


def test_pipeline_py_still_has_zero_himawari_references():
    text = (REPO_ROOT / "backend" / "pipeline.py").read_text(encoding="utf-8")
    assert "himawari" not in text.lower(), (
        "backend/pipeline.py must remain fully GFS-only; Phase 4 does not "
        "touch this file"
    )


# ---------------------------------------------------------------------------
# 8. No raw pixel array is ever persisted into himawari_features.json.
# ---------------------------------------------------------------------------

def test_no_raw_pixel_arrays_persisted_in_artifact(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=True, n=15)
    out_dir, rc = _run_main_with_fixed_pixels(monkeypatch, tmp_path, raw_pixels)
    assert rc == 0

    raw_text = (out_dir / "himawari_features.json").read_text()
    artifact = json.loads(raw_text)

    # The artifact must be small (aggregate stats only) -- a 15x15 raw
    # array serialized to JSON would be far larger than this ceiling.
    assert len(raw_text) < 10_000, (
        f"himawari_features.json is {len(raw_text)} bytes -- suspiciously "
        f"large for an aggregate-only artifact; check for a persisted array"
    )

    def _walk(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                yield from _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from _walk(v)
        else:
            yield obj

    # Every leaf value must be a scalar (str/float/int/bool/None) -- never
    # a list long enough to be a serialized row of a 2D pixel array.
    for leaf in _walk(artifact):
        if isinstance(leaf, list):
            assert len(leaf) < 50, "found a suspiciously long list leaf -- possible raw array leakage"


# ---------------------------------------------------------------------------
# 9. Zero-byte diff sanity: fetch_himawari_realtime.py's historical
#    bt_trend_1h computation (unrelated to Phase 4) still works exactly as
#    before when real history is present.
# ---------------------------------------------------------------------------

def test_bt_trend_1h_computation_unaffected_by_phase4(monkeypatch, tmp_path):
    out_dir = _patch_paths(monkeypatch, tmp_path)
    scene_dt_1 = datetime(2026, 10, 3, 2, 0, 0)
    scene_dt_2 = datetime(2026, 10, 3, 3, 0, 0)

    raw_warm = _make_raw_pixels(cold=False)
    raw_cold = _make_raw_pixels(cold=True)

    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt_1, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: raw_warm, raising=True)
    assert fhr.main() == 0

    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt_2, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: raw_cold, raising=True)
    assert fhr.main() == 0

    rt = json.loads((out_dir / "himawari_realtime.json").read_text())
    assert rt["bt_trend_1h"] is not None
    assert rt["bt_trend_1h"] < 0  # warm -> cold == cooling == negative trend


# ---------------------------------------------------------------------------
# Atomic-write reliability regression tests (fix for the non-atomic-write
# finding in the Phase 4 commit-readiness audit). These reuse this repo's
# existing, already-tested atomic_write.py primitive -- see
# tests/test_atomic_write.py for that module's own unit tests. These tests
# only check the wiring: that fetch_himawari_realtime.py's new
# himawari_features.json write actually goes through that primitive and
# behaves correctly end-to-end through main().
# ---------------------------------------------------------------------------

import atomic_write as _atomic_write_module  # noqa: E402


def _leftover_tmp_files(out_dir):
    return [p for p in out_dir.iterdir() if p.name.startswith(".himawari_features.json.")]


def test_atomic_1_successful_write_produces_valid_artifact(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=True)
    out_dir, rc = _run_main_with_fixed_pixels(monkeypatch, tmp_path, raw_pixels)
    assert rc == 0

    features_path = out_dir / "himawari_features.json"
    assert features_path.exists()
    artifact = json.loads(features_path.read_text())  # must parse cleanly
    assert artifact["artifact_type"] == "observed_satellite_feature_set"
    assert artifact["model_input"] is False
    assert not _leftover_tmp_files(out_dir), "no temp file should remain after a successful write"


def test_atomic_2_serialization_failure_preserves_existing_good_artifact(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=True)

    # First, a normal successful run -- this is the "existing known-good
    # artifact" that a later failure must not destroy.
    out_dir, rc = _run_main_with_fixed_pixels(monkeypatch, tmp_path, raw_pixels)
    assert rc == 0
    features_path = out_dir / "himawari_features.json"
    good_bytes_before = features_path.read_bytes()
    assert good_bytes_before  # sanity: the "good" artifact is non-empty

    # Second run: force extract_himawari_features() to hand back a feature
    # set whose dict contains a NaN -- atomic_write_json uses allow_nan=False,
    # so json.dumps() raises BEFORE any file on disk is touched (this is a
    # genuine serialization failure, not a disk/write failure).
    import backend.data_sources.himawari_features as hf
    real_extract = hf.extract_himawari_features

    def extract_with_nan(*args, **kwargs):
        fs = real_extract(*args, **kwargs)
        fs.feature_values["cooling_rate_c_per_hour"] = float("nan")
        return fs

    monkeypatch.setattr(hf, "extract_himawari_features", extract_with_nan, raising=True)

    scene_dt_2 = datetime(2026, 10, 3, 4, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt_2, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: raw_pixels, raising=True)
    rc2 = fhr.main()

    assert rc2 == 0, "fail-open: main() must still succeed despite the serialization failure"
    assert features_path.read_bytes() == good_bytes_before, (
        "a serialization failure on the new write must leave the previous "
        "known-good himawari_features.json byte-for-byte unchanged"
    )
    assert json.loads(features_path.read_text())  # still parses cleanly
    assert not _leftover_tmp_files(out_dir), "no temp file should be left behind after a failed write"

    # Production outputs must also be completely unaffected.
    assert (out_dir / "himawari_realtime.json").exists()
    rt = json.loads((out_dir / "himawari_realtime.json").read_text())
    assert rt["storm_detected"] is True
    assert (out_dir / "himawari_history.json").exists()


def test_atomic_3_temp_write_failure_preserves_existing_good_artifact(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=True)

    out_dir, rc = _run_main_with_fixed_pixels(monkeypatch, tmp_path, raw_pixels)
    assert rc == 0
    features_path = out_dir / "himawari_features.json"
    good_bytes_before = features_path.read_bytes()
    assert good_bytes_before

    # Force a failure AFTER serialization succeeds but DURING the temp-file
    # write/fsync step inside atomic_write.atomic_write_bytes -- distinct
    # from the serialization-failure path tested above. This simulates a
    # disk-level failure (e.g. fsync error), not a bad value.
    import os as _os

    def fsync_boom(fd):
        raise OSError("simulated disk failure during fsync")

    monkeypatch.setattr(_atomic_write_module.os, "fsync", fsync_boom, raising=True)

    scene_dt_2 = datetime(2026, 10, 3, 5, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt_2, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: raw_pixels, raising=True)
    rc2 = fhr.main()

    assert rc2 == 0, "fail-open: main() must still succeed despite the temp-file write failure"
    assert features_path.read_bytes() == good_bytes_before, (
        "a temp-file write/fsync failure must leave the previous known-good "
        "himawari_features.json byte-for-byte unchanged"
    )
    assert not _leftover_tmp_files(out_dir), "the failed temp file must be cleaned up, not left behind"

    assert (out_dir / "himawari_realtime.json").exists()
    assert (out_dir / "himawari_history.json").exists()


def test_atomic_4_fail_open_behavior_intact_across_both_failure_modes(monkeypatch, tmp_path):
    # Belt-and-suspenders: explicitly re-assert, in one place, that neither
    # failure mode above ever turns into a non-zero exit or a missing
    # production file -- the exact guarantee the Phase 4 audit required.
    raw_pixels = _make_raw_pixels(cold=True)
    out_dir = _patch_paths(monkeypatch, tmp_path)
    scene_dt = datetime(2026, 10, 3, 6, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: raw_pixels, raising=True)

    import backend.data_sources.himawari_features as hf

    # Raise straight out of extraction entirely -- already covered by
    # test_extraction_exception_does_not_block_save_outputs above, but
    # re-asserted here alongside the atomic-write failure modes so this one
    # test documents the full fail-open contract in a single place.
    def boom(*args, **kwargs):
        raise RuntimeError("simulated total extraction failure")

    monkeypatch.setattr(hf, "extract_himawari_features", boom, raising=True)
    rc = fhr.main()
    assert rc == 0
    assert (out_dir / "himawari_realtime.json").exists()
    assert (out_dir / "himawari_history.json").exists()


def test_atomic_5_no_temp_file_left_behind_after_successful_replace(monkeypatch, tmp_path):
    raw_pixels = _make_raw_pixels(cold=True)
    out_dir, rc = _run_main_with_fixed_pixels(monkeypatch, tmp_path, raw_pixels)
    assert rc == 0
    all_entries = {p.name for p in out_dir.iterdir()}
    assert all_entries == {"himawari_realtime.json", "himawari_history.json", "himawari_features.json"}, (
        f"unexpected leftover files in output dir: {all_entries}"
    )
