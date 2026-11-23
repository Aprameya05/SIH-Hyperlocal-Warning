"""Himawari-9 B13 brightness temperature per cell: geometry, binning, rates, scene selection and the fail-closed validator."""
import copy
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from pipelines.ingestion import ingest_himawari_ctt as ing  # noqa: E402
from pipelines.validation import validate_satellite_ctt as val  # noqa: E402

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)


def test_view_zenith_is_zero_at_the_subsatellite_point_and_grows_with_distance():
    assert float(ing.view_zenith_deg(0.0, ing.SUB_LON)) == pytest.approx(0.0, abs=1e-6)
    z_india = ing.view_zenith_deg(np.array([13.0, 20.0, 28.0]), np.array([80.0, 78.0, 77.0]))
    assert np.all(z_india > 50) and np.all(z_india < 90)
    assert float(ing.view_zenith_deg(20.0, 90.0)) < float(ing.view_zenith_deg(20.0, 70.0))   # farther west = steeper
    assert np.isnan(ing.view_zenith_deg(0.0, ing.SUB_LON + 120.0))                              # beyond the limb


def test_bin_to_cells_statistics_range_filter_and_bbox():
    idx = {(52, 320): 0, (53, 320): 1}                    # cells centred at 13.0N,80.0E and 13.25N,80.0E
    lat = np.array([13.0, 13.05, 13.1, 13.02, 13.26, 13.24, 50.0, 13.0])
    lon = np.array([80.0, 80.02, 79.95, 80.05, 80.0, 80.0, 80.0, 80.0])
    bt = np.array([300.0, 220.0, 280.0, 290.0, 250.0, 260.0, 200.0, 99.0])   # last: below the physical range; 7th: outside bbox
    o = ing.bin_to_cells(lat, lon, bt, idx, 2)
    assert o["n"][0] == 4 and o["min"][0] == 220.0 and o["cold_frac"][0] == pytest.approx(0.25)
    assert o["p10"][0] == pytest.approx(np.percentile([300, 220, 280, 290], 10))
    assert o["n"][1] == 2 and o["min"][1] == 250.0
    empty = ing.bin_to_cells(np.array([13.0]), np.array([80.0]), np.array([np.nan]), idx, 2)
    assert np.all(np.isnan(empty["p10"]))                                                  # missing stays missing, never a default


def test_cooling_rate_sign_and_missing():
    r = ing.cooling_rate_k_per_h(np.array([210.0, np.nan, 290.0]), np.array([230.0, 250.0, np.nan]), 60)
    assert r[0] == pytest.approx(-20.0) and np.isnan(r[1]) and np.isnan(r[2])           # negative = cooling
    assert ing.cooling_rate_k_per_h(np.array([220.0]), np.array([230.0]), 30)[0] == pytest.approx(-20.0)


def _keys(stamp, segs):
    return [f"AHI-L1b-FLDK/{stamp[:4]}/{stamp[4:6]}/{stamp[6:8]}/{stamp[9:13]}/HS_H09_{stamp}_B13_FLDK_R20_S{s:02d}10.DAT.bz2" for s in segs]


def test_scene_selection_needs_every_segment_and_is_recent():
    keys = _keys("20261123_0850", (2, 3, 4, 5)) + _keys("20261123_0900", (2, 3)) + _keys("20261123_0830", (2, 3, 4, 5, 6))
    assert ing.newest_complete_scene(keys, NOW) == datetime(2026, 11, 23, 8, 50, tzinfo=timezone.utc)   # 0900 is incomplete
    assert ing.newest_complete_scene(keys, NOW + timedelta(hours=8)) is None                           # too old
    assert ing.newest_complete_scene([], NOW) is None
    assert ing.hsd_key(datetime(2026, 11, 23, 8, 50, tzinfo=timezone.utc), 3).endswith("HS_H09_20261123_0850_B13_FLDK_R20_S0310.DAT.bz2")


def _doc(n=20):
    ids = [f"C{i}" for i in range(n)]
    p10 = [280.0 + i for i in range(n)]
    return {"meta": {"schema": "drift_satellite_ctt_v1", "is_observation": True, "is_forecast": False, "is_retrieved_cloud_top_temperature": False,
                     "valid_bt_range_k": [150.0, 340.0], "scene_nominal_utc": "2026-11-23T08:50:00Z", "n_cells": n,
                     "cells_sha256": hashlib.sha256("|".join(ids).encode()).hexdigest(), "frames": {"t0": {"files": [{"key": "k"}]}}},
            "cells": ids, "ctt_p10_k": p10, "ctt_min_k": [v - 5 for v in p10], "cold_frac_235k": [0.0] * n, "n_pixels": [200] * n,
            "dtdt_30min_k_per_h": [-1.0] * n, "dtdt_60min_k_per_h": [-2.0] * n, "view_zenith_deg": [70.0] * n}, ids


def test_validator_accepts_a_good_artifact():
    doc, ids = _doc()
    assert val.problems(doc, now=NOW, max_age_minutes=180, grid_ids=ids) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["meta"].update(is_retrieved_cloud_top_temperature=True), "honesty flags"),
    (lambda d: d["meta"].update(is_forecast=True), "honesty flags"),
    (lambda d: d["meta"].update(schema="x"), "schema"),
    (lambda d: d["ctt_p10_k"].__setitem__(3, 500.0), "outside"),
    (lambda d: d["ctt_p10_k"].__setitem__(3, 100.0), "outside"),
    (lambda d: d["ctt_min_k"].__setitem__(4, 400.0), "outside"),
    (lambda d: d["dtdt_60min_k_per_h"].__setitem__(2, 999.0), "implausible"),
    (lambda d: d["dtdt_60min_k_per_h"].__setitem__(2, float("nan")), "implausible"),
    (lambda d: d["cold_frac_235k"].__setitem__(1, 1.5), "outside 0-1"),
    (lambda d: d["ctt_p10_k"].pop(), "wrong length"),
    (lambda d: d["meta"].update(cells_sha256="0" * 64), "cells_sha256"),
    (lambda d: d["meta"].update(scene_nominal_utc="2026-11-23T03:00:00Z"), "stale"),
    (lambda d: d["meta"].update(scene_nominal_utc="2026-11-23T11:00:00Z"), "future"),
    (lambda d: d["meta"].update(scene_nominal_utc="garbage"), "scene_nominal_utc"),
    (lambda d: d["meta"].update(frames={}), "provenance"),
    (lambda d: [d["ctt_p10_k"].__setitem__(i, None) or d["ctt_min_k"].__setitem__(i, None) for i in range(10)], "partial scene"),
    (lambda d: d["ctt_min_k"].__setitem__(0, d["ctt_p10_k"][0] + 3), "must exist with p10"),
])
def test_validator_rejects_corrupt_artifacts(mutate, needle):
    doc, ids = _doc()
    d = copy.deepcopy(doc)
    mutate(d)
    bad = val.problems(d, now=NOW, max_age_minutes=180, grid_ids=ids)
    assert any(needle in b for b in bad), bad


def test_validator_rejects_misaligned_grid():
    doc, ids = _doc()
    assert any("do not match" in b for b in val.problems(doc, now=NOW, grid_ids=list(reversed(ids))))


def test_missing_values_are_null_never_defaults():
    doc, ids = _doc()
    doc["ctt_p10_k"][5] = None
    doc["ctt_min_k"][5] = None
    doc["dtdt_60min_k_per_h"][5] = None
    doc["dtdt_30min_k_per_h"][5] = None
    assert val.problems(doc, now=NOW, max_age_minutes=180, grid_ids=ids) == []
    assert ing.to_json_array(np.array([1.234, np.nan, np.inf]), 1) == [1.2, None, None]
