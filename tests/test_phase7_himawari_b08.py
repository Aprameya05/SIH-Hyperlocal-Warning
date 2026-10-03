"""
tests/test_phase7_himawari_b08.py

Phase 7 -- Himawari-9 AHI Band 8 (6.185um, water-vapor-sensitive IR
channel) acquisition and feature-extraction tests.

Scope, per docs/PHASE_7_HIMAWARI_B08_IMPLEMENTATION.md: B08 is an
independent, additively-wired, fail-open OBSERVED/DERIVED artifact
(data/himawari_b08_features.json). It must NEVER affect B13's existing
production output (data/himawari_realtime.json, data/himawari_history.json,
data/himawari_features.json), NEVER be described as IWV or a retrieved
IWV product, and NEVER appear as a model input anywhere in
forecast_action.py.

All tests here use mocks/fixtures -- no real network call is made
anywhere in this file, and nothing here is claimed as live verification.
Real-run validation (if the environment's network allows it) is reported
separately in docs/PHASE_7_HIMAWARI_B08_IMPLEMENTATION.md, not asserted
by a test.
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
# Helpers (self-contained -- deliberately not imported from
# test_phase4_himawari_wiring.py, to keep this file's fixtures independent
# of that file's internal structure)
# ---------------------------------------------------------------------------

def _make_pixels(bt_c, n=15, jitter=None):
    lat_lin = np.linspace(fhr.VOBL_LAT - 0.3, fhr.VOBL_LAT + 0.3, n)
    lon_lin = np.linspace(fhr.VOBL_LON - 0.3, fhr.VOBL_LON + 0.3, n)
    lons, lats = np.meshgrid(lon_lin, lat_lin)
    if jitter is not None:
        lons = lons + jitter
    bt_k = np.full_like(lats, bt_c + 273.15, dtype=np.float64)
    return {"bt": bt_k, "lats": lats, "lons": lons}


def _patch_paths(monkeypatch, tmp_path):
    out_dir = tmp_path / "data"
    out_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(fhr, "OUT_DIR", out_dir, raising=True)
    monkeypatch.setattr(fhr, "OUT_FILE", out_dir / "himawari_realtime.json", raising=True)
    monkeypatch.setattr(fhr, "HIST_FILE", out_dir / "himawari_history.json", raising=True)
    monkeypatch.setattr(fhr, "FEATURES_FILE", out_dir / "himawari_features.json", raising=True)
    monkeypatch.setattr(fhr, "B08_FEATURES_FILE", out_dir / "himawari_b08_features.json", raising=True)
    return out_dir


def _run_main(monkeypatch, tmp_path, b13_pixels, b08_pixels, scene_dt=None):
    out_dir = _patch_paths(monkeypatch, tmp_path)
    scene_dt = scene_dt or datetime(2026, 10, 3, 3, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: b13_pixels, raising=True)
    monkeypatch.setattr(fhr, "try_scene_b08", lambda dt: b08_pixels, raising=True)
    rc = fhr.main()
    return out_dir, rc


# ---------------------------------------------------------------------------
# 1. B08 key generation for S04/S05/S06
# ---------------------------------------------------------------------------

def test_01_b08_key_generation_s04_s05_s06():
    dt = datetime(2026, 10, 3, 3, 0, 0)
    for seg in (4, 5, 6):
        key = fhr.build_s3_key(dt, seg, band="B08")
        assert key == f"AHI-L1b-FLDK/2026/10/03/0300/HS_H09_20261003_0300_B08_FLDK_R20_S0{seg}10.DAT.bz2"
        # B13's own key generation must be byte-identical to before (no band kwarg)
        b13_key = fhr.build_s3_key(dt, seg)
        assert "_B13_" in b13_key
        assert "_B08_" not in b13_key


# ---------------------------------------------------------------------------
# 2. B08 JAXA fallback URL construction
# ---------------------------------------------------------------------------

def test_02_b08_jaxa_url_construction(monkeypatch):
    captured_urls = []

    class FakeResp:
        status_code = 404
        content = b""

    def fake_get(url, timeout=None):
        captured_urls.append(url)
        return FakeResp()

    import requests
    monkeypatch.setattr(requests, "get", fake_get, raising=True)
    dt = datetime(2026, 10, 3, 3, 0, 0)
    result = fhr.fetch_segments_jaxa_b08(dt)
    assert result is None  # all 404s -> no files
    assert len(captured_urls) == 3
    for url in captured_urls:
        assert "/B08/FLDK/" in url
        assert "_B08_FLDK_R20_S0" in url
        assert "/B13/" not in url


# ---------------------------------------------------------------------------
# 3. B08 fetch success / 4. B08 fetch failure is isolated
# ---------------------------------------------------------------------------

def test_03_b08_fetch_success_reaches_artifact(monkeypatch, tmp_path):
    b13 = _make_pixels(-30.0)
    b08 = _make_pixels(-10.0)
    out_dir, rc = _run_main(monkeypatch, tmp_path, b13, b08)
    assert rc == 0
    b08_path = out_dir / "himawari_b08_features.json"
    assert b08_path.exists()
    artifact = json.loads(b08_path.read_text())
    assert artifact["band"] == "B08"
    assert artifact["b08_observed"]["brightness_temperature_vobl_c"] is not None


def test_04_b08_fetch_failure_is_isolated(monkeypatch, tmp_path):
    b13 = _make_pixels(-30.0)
    out_dir = _patch_paths(monkeypatch, tmp_path)
    scene_dt = datetime(2026, 10, 3, 3, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: b13, raising=True)
    monkeypatch.setattr(fhr, "try_scene_b08", lambda dt: None, raising=True)  # total B08 failure
    rc = fhr.main()
    assert rc == 0
    assert not (out_dir / "himawari_b08_features.json").exists()


# ---------------------------------------------------------------------------
# 5. B13 output remains written if B08 fails
# ---------------------------------------------------------------------------

def test_05_b13_output_written_even_when_b08_fails(monkeypatch, tmp_path):
    b13 = _make_pixels(-50.0)  # colder than THRESHOLD_C (-40.0) -> storm_detected True
    out_dir = _patch_paths(monkeypatch, tmp_path)
    scene_dt = datetime(2026, 10, 3, 3, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: b13, raising=True)

    def boom(dt):
        raise RuntimeError("simulated total B08 pipeline failure")

    monkeypatch.setattr(fhr, "try_scene_b08", boom, raising=True)
    rc = fhr.main()
    assert rc == 0
    rt = json.loads((out_dir / "himawari_realtime.json").read_text())
    assert rt["storm_detected"] is True
    assert (out_dir / "himawari_history.json").exists()


# ---------------------------------------------------------------------------
# 6. B08 artifact atomic-write behavior / 7. survives failed replacement
# ---------------------------------------------------------------------------

def test_06_and_07_b08_artifact_atomic_write_and_survival(monkeypatch, tmp_path):
    b13 = _make_pixels(-30.0)
    b08_good = _make_pixels(-10.0)
    out_dir, rc = _run_main(monkeypatch, tmp_path, b13, b08_good)
    assert rc == 0
    b08_path = out_dir / "himawari_b08_features.json"
    good_bytes = b08_path.read_bytes()
    assert good_bytes

    # Force a serialization failure on the NEXT run via a NaN value, and
    # confirm the previous good artifact survives byte-for-byte.
    import backend.data_sources.himawari_features as hf  # noqa: F401 -- ensure importable path parity
    scene_dt_2 = datetime(2026, 10, 3, 3, 10, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt_2, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: b13, raising=True)

    real_compute = fhr.compute_b08_signal

    def compute_with_nan(result):
        sig = real_compute(result)
        sig["brightness_temperature_vobl_c"] = float("nan")
        return sig

    monkeypatch.setattr(fhr, "compute_b08_signal", compute_with_nan, raising=True)
    monkeypatch.setattr(fhr, "try_scene_b08", lambda dt: b08_good, raising=True)

    rc2 = fhr.main()
    assert rc2 == 0
    assert b08_path.read_bytes() == good_bytes, (
        "a NaN-triggered serialization failure must leave the previous "
        "known-good B08 artifact byte-for-byte unchanged"
    )
    leftover = [p for p in out_dir.iterdir() if p.name.startswith(".himawari_b08_features.json.")]
    assert not leftover, "no temp file should be left behind after a failed write"


# ---------------------------------------------------------------------------
# 8. Geometry verification / 9. mismatch rejects derived feature
# ---------------------------------------------------------------------------

def test_08_geometry_verification_passes_for_matching_grids():
    lons = np.array([[77.0, 77.1], [77.2, 77.3]])
    lats = np.array([[13.0, 13.1], [13.2, 13.3]])
    g = fhr.verify_geometry(lons, lats, lons.copy(), lats.copy())
    assert g["compatible"] is True
    assert g["status"] == "verified_match"


def test_09_geometry_mismatch_rejects_b13_minus_b08(monkeypatch, tmp_path):
    b13 = _make_pixels(-30.0)
    b08_shifted = _make_pixels(-10.0, jitter=0.5)  # same shape, different lon/lat values
    out_dir, rc = _run_main(monkeypatch, tmp_path, b13, b08_shifted)
    assert rc == 0
    artifact = json.loads((out_dir / "himawari_b08_features.json").read_text())
    assert artifact["geometry_verification"]["compatible"] is False
    assert artifact["b13_minus_b08_derived"]["available"] is False
    assert "geometry_not_compatible" in artifact["b13_minus_b08_derived"]["reason"]
    assert any("geometry_" in f for f in artifact["quality_flags"])
    # B08's own observed fields must still be present -- they don't depend on B13's grid
    assert artifact["b08_observed"]["brightness_temperature_vobl_c"] is not None


# ---------------------------------------------------------------------------
# 10. B13-B08 difference uses matched observations
# 11. Missing B13 => no fabricated difference
# 12. Missing B08 => no fabricated difference
# ---------------------------------------------------------------------------

def test_10_b13_minus_b08_uses_matched_observations(monkeypatch, tmp_path):
    b13 = _make_pixels(-30.0)
    b08 = _make_pixels(-10.0)
    out_dir, rc = _run_main(monkeypatch, tmp_path, b13, b08)
    assert rc == 0
    artifact = json.loads((out_dir / "himawari_b08_features.json").read_text())
    diff = artifact["b13_minus_b08_derived"]
    assert diff["available"] is True
    assert diff["category"] == "derived_from_observation"
    assert diff["b13_minus_b08_brightness_temperature_vobl_c"] == pytest.approx(-20.0, abs=0.01)


def test_11_missing_b13_signal_no_fabricated_difference():
    b08_sig = {"brightness_temperature_vobl_c": -10.0, "brightness_temperature_mean_50km_c": -10.0}
    geometry = {"compatible": True, "status": "verified_match"}
    diff = fhr.compute_b13_minus_b08(None, b08_sig, geometry)
    assert diff["available"] is False
    assert diff["reason"] == "b13_observation_unavailable"
    assert diff["b13_minus_b08_brightness_temperature_vobl_c"] is None


def test_12_missing_b08_signal_no_fabricated_difference():
    b13_sig = {"vobl_bt_celsius": -30.0, "mean_bt_50km": -30.0}
    geometry = {"compatible": True, "status": "verified_match"}
    diff = fhr.compute_b13_minus_b08(b13_sig, None, geometry)
    assert diff["available"] is False
    assert diff["reason"] == "b08_observation_unavailable"
    assert diff["b13_minus_b08_brightness_temperature_vobl_c"] is None


# ---------------------------------------------------------------------------
# 13. Temporal B08 uses actual elapsed time / 14. no fabricated t-10/t-30
# ---------------------------------------------------------------------------

def test_13_temporal_uses_actual_elapsed_time(monkeypatch, tmp_path):
    b13 = _make_pixels(-30.0)
    b08_warm = _make_pixels(-5.0)
    b08_cold = _make_pixels(-15.0)

    out_dir, rc = _run_main(monkeypatch, tmp_path, b13, b08_warm,
                             scene_dt=datetime(2026, 10, 3, 2, 40, 0))
    assert rc == 0

    out_dir2, rc2 = _run_main(monkeypatch, tmp_path, b13, b08_cold,
                               scene_dt=datetime(2026, 10, 3, 3, 0, 0))
    assert rc2 == 0
    artifact = json.loads((out_dir / "himawari_b08_features.json").read_text())
    temporal = artifact["temporal"]
    assert temporal["available"] is True
    assert temporal["actual_gap_minutes"] == 20.0  # true elapsed time, not an assumed interval
    assert temporal["previous_observation_time_utc"] == "2026-10-03T02:40:00"
    assert temporal["brightness_temperature_change_c"] < 0  # warm -> cold


def test_14_no_fabricated_t10_t30_values():
    sig = {"brightness_temperature_min_50km_c": -15.0}
    temporal = fhr.compute_b08_temporal(sig, "2026-10-03T03:00:00", None)
    assert temporal["available"] is False
    assert temporal["actual_gap_minutes"] is None
    # No field anywhere in this module claims a fixed t-10/t-20/t-30 offset
    import ast
    import inspect
    full_src = inspect.getsource(fhr.compute_b08_temporal)
    tree = ast.parse(full_src)
    func_node = tree.body[0]
    # Drop the docstring (which legitimately *describes* the no-fabrication
    # guarantee using the strings "t-10"/"t-30") and inspect only the
    # executable body for an actual fabricated fixed-offset computation.
    body_without_docstring = func_node.body[1:] if (
        func_node.body and isinstance(func_node.body[0], ast.Expr)
        and isinstance(func_node.body[0].value, ast.Constant)
        and isinstance(func_node.body[0].value.value, str)
    ) else func_node.body
    body_src = "\n".join(ast.unparse(n) for n in body_without_docstring)
    assert "t-10" not in body_src and "t-20" not in body_src and "t-30" not in body_src
    assert "actual_gap_minutes" in full_src


# ---------------------------------------------------------------------------
# 15. Terminology guard -- no B08 feature described as IWV
# ---------------------------------------------------------------------------

def test_15_terminology_guard_no_iwv_anywhere(monkeypatch, tmp_path):
    b13 = _make_pixels(-30.0)
    b08 = _make_pixels(-10.0)
    out_dir, rc = _run_main(monkeypatch, tmp_path, b13, b08)
    assert rc == 0
    artifact = json.loads((out_dir / "himawari_b08_features.json").read_text())
    # "note" is the one field explicitly allowed to mention "iwv" -- it
    # exists specifically to disclaim that B08 is NOT a retrieved IWV
    # product (Phase 7 requirement: never call B08 IWV, but be explicit
    # that it is not IWV). Every other field must never mention it.
    scanned = dict(artifact)
    scanned.pop("note", None)
    artifact_text = json.dumps(scanned).lower()
    assert "iwv" not in artifact_text
    assert "retrieved_iwv" not in artifact_text
    assert "integrated water vapor" not in artifact_text
    # the disclaimer itself must read as a negation, not an assertion
    assert "not a retrieved iwv" in artifact.get("note", "").lower() or \
           "not iwv" in artifact.get("note", "").lower()

    raw_src = Path("fetch_himawari_realtime.py").read_text(encoding="utf-8").lower()
    # Legitimate disclaimer lines ("... NOT a retrieved iwv product ...",
    # "never iwv/retrieved_iwv/...") are explicitly required by Phase 7 to
    # state what B08 is NOT. Drop only those disclaiming lines before
    # scanning the rest of the module for an actual (non-disclaiming) use.
    src = "\n".join(
        line for line in raw_src.splitlines()
        if not ("not a retrieved iwv" in line or "never iwv" in line
                or "not iwv" in line or "/retrieved_iwv/" in line)
    )
    # the only acceptable mentions of "water vapor" are descriptive (the
    # channel's physical meaning), never as a feature name/value claiming
    # IWV has been retrieved
    assert "retrieved_iwv" not in src
    assert '"iwv"' not in src


# ---------------------------------------------------------------------------
# 16. Model-input contamination guard
# ---------------------------------------------------------------------------

def test_16_forecast_action_has_no_b08_derived_model_feature():
    text = Path("forecast_action.py").read_text(encoding="utf-8")
    assert "b08" not in text.lower()
    assert "B08_FEATURES_FILE" not in text
    assert "b13_minus_b08" not in text.lower()


# ---------------------------------------------------------------------------
# 17. B13 regression -- existing behavior/output unchanged
# ---------------------------------------------------------------------------

def test_17_b13_regression_existing_output_unchanged(monkeypatch, tmp_path):
    b13 = _make_pixels(-30.0)
    out_dir = _patch_paths(monkeypatch, tmp_path)
    scene_dt = datetime(2026, 10, 3, 3, 0, 0)
    monkeypatch.setattr(fhr, "latest_scene_dt", lambda now_utc: scene_dt, raising=True)
    monkeypatch.setattr(fhr, "try_scene", lambda dt: b13, raising=True)
    monkeypatch.setattr(fhr, "try_scene_b08", lambda dt: None, raising=True)
    rc = fhr.main()
    assert rc == 0
    rt = json.loads((out_dir / "himawari_realtime.json").read_text())
    expected_keys = {
        "timestamp_utc", "timestamp_ist", "vobl_bt_celsius", "min_bt_50km",
        "mean_bt_50km", "cold_pixels_count", "storm_detected",
        "nearest_pixel_dist_km", "threshold_celsius", "data_source",
        "bt_trend_1h",
    }
    assert set(rt.keys()) == expected_keys, "B13's own output schema must be unchanged by Phase 7"


# ---------------------------------------------------------------------------
# 18. No arbitrary B08 cold threshold inherited from B13
# ---------------------------------------------------------------------------

def test_18_no_cold_threshold_inherited_for_b08():
    sig = fhr.compute_b08_signal(_make_pixels(-50.0))
    assert "cold_pixel" not in str(sig).lower()
    assert "storm_detected" not in sig
    assert "threshold" not in str(sig).lower()
    import inspect
    src = inspect.getsource(fhr.compute_b08_signal)
    assert "THRESHOLD_C" not in src  # B13's -40C threshold must not be referenced here at all


# ---------------------------------------------------------------------------
# 19. Artifact schema validation
# ---------------------------------------------------------------------------

def test_19_artifact_schema_validation(monkeypatch, tmp_path):
    b13 = _make_pixels(-30.0)
    b08 = _make_pixels(-10.0)
    out_dir, rc = _run_main(monkeypatch, tmp_path, b13, b08)
    assert rc == 0
    artifact = json.loads((out_dir / "himawari_b08_features.json").read_text())
    required_keys = {
        "artifact_type", "model_input", "feature_extraction_version",
        "satellite", "instrument", "band", "wavelength_um", "resolution",
        "source", "segments", "calibration_method", "observation_time_utc",
        "processing_timestamp_utc", "raw_pixels_available_this_run",
        "geometry_verification", "b08_observed", "b13_minus_b08_derived",
        "temporal", "quality_flags",
    }
    assert required_keys.issubset(artifact.keys())
    assert artifact["model_input"] is False
    assert artifact["band"] == "B08"
    assert artifact["wavelength_um"] == 6.185
    assert artifact["segments"] == [4, 5, 6]


# ---------------------------------------------------------------------------
# 20. No temp files left after atomic write
# ---------------------------------------------------------------------------

def test_20_no_temp_files_left_after_atomic_write(monkeypatch, tmp_path):
    b13 = _make_pixels(-30.0)
    b08 = _make_pixels(-10.0)
    out_dir, rc = _run_main(monkeypatch, tmp_path, b13, b08)
    assert rc == 0
    all_names = {p.name for p in out_dir.iterdir()}
    assert all_names == {
        "himawari_realtime.json", "himawari_history.json",
        "himawari_features.json", "himawari_b08_features.json",
    }, f"unexpected files in output dir: {all_names}"
