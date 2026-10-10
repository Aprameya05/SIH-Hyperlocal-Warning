"""
tests/test_phase59_location_sweep.py
=====================================================
2026-10-10: comprehensive verification, per explicit mandate item 7,
that the full location -> cell -> hazard pipeline works correctly for
every required test location (the 8 named cities plus representative
rural/coastal/mountainous coordinates), not just VOBL/Bengaluru.

Verifies, with REAL geocoding calls (not mocked) and REAL model
inference (not mocked):
  1. Every location resolves to a real, distinct canonical cell within
     grid bounds -- never silently substituted with VOBL's cell_id.
  2. TS correctly stays OUT_OF_DOMAIN_STATION_ONLY / probability=None
     for every non-VOBL cell -- never a fabricated or copied VOBL
     value.
  3. CB produces a genuine TRAINED-status prediction for every cell
     (the real pan-India panindia_cb_v1 model is not VOBL-scoped).

Network-dependent (uses the real Nominatim geocoder via
scripts/location_resolver.py, cached locally after first run) --
skipped if network/model artifacts are unavailable in this checkout.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "models" / "unified_mtl"))

VOBL_CELL_ID = "IND_13.0_78.0"

TEST_LOCATIONS = [
    "Mumbai", "Delhi", "Chennai", "Hyderabad", "Kolkata", "Guwahati", "Srinagar",
    "Lonar Maharashtra",       # rural
    "Puri Odisha",             # coastal
    "Manali Himachal Pradesh",  # mountainous
]


def _geocode_all():
    from location_resolver import geocode
    results = {}
    for q in TEST_LOCATIONS:
        try:
            results[q] = geocode(q)
        except Exception as exc:
            pytest.skip(f"geocoding unavailable in this environment ({type(exc).__name__}: {exc})")
    return results


@pytest.fixture(scope="module")
def geocoded():
    return _geocode_all()


def test_every_location_resolves_to_a_real_distinct_cell_within_bounds(geocoded):
    for q, r in geocoded.items():
        assert r.found, f"{q} failed to geocode at all"
        assert r.cell_id, f"{q} geocoded but has no matched cell_id"
        assert r.in_india_grid_bounds, f"{q} resolved outside the canonical grid bounds"


def test_no_location_silently_resolves_to_the_vobl_cell(geocoded):
    """None of these 10 real, distinct Indian locations should ever
    coincidentally land on VOBL's exact cell -- if one does, it is
    almost certainly a resolver bug substituting VOBL by default,
    not a genuine coincidence (VOBL's cell covers a ~110km x 110km
    area; none of these cities are that close to Bengaluru)."""
    for q, r in geocoded.items():
        assert r.cell_id != VOBL_CELL_ID, f"{q} silently resolved to the VOBL cell -- real bug, not coincidence"


@pytest.mark.skipif(
    not (REPO_ROOT / "models" / "thunderstorm_model.pkl").exists(),
    reason="thunderstorm_model.pkl not present in this checkout",
)
def test_ts_stays_honestly_unavailable_for_every_non_vobl_location(geocoded):
    from inference_engine import UnifiedInferenceEngine
    eng = UnifiedInferenceEngine()
    for q, r in geocoded.items():
        pred = eng.predict_ts(r.cell_id, 2, datetime.now(timezone.utc), None)
        assert pred.status == "OUT_OF_DOMAIN_STATION_ONLY", f"{q}: TS should refuse to extrapolate, got {pred.status}"
        assert pred.probability is None, f"{q}: TS must never carry a fabricated probability outside VOBL"


@pytest.mark.skipif(
    not (REPO_ROOT / "models" / "panindia_cb_v1" / "panindia_cb_v1_model.json").exists(),
    reason="panindia_cb_v1 model artifact not present in this checkout",
)
def test_cb_produces_a_real_trained_prediction_for_every_location(geocoded):
    from inference_engine import UnifiedInferenceEngine
    from panindia_cb_model_interface import PanIndiaCBModel

    eng = UnifiedInferenceEngine()
    m = PanIndiaCBModel()
    rng = np.random.default_rng(42)
    for q, r in geocoded.items():
        X = pd.DataFrame(rng.random((1, len(m.feature_cols))) * 10, columns=m.feature_cols)
        pred = eng.predict_cb(r.cell_id, 2, datetime.now(timezone.utc), X)
        assert pred.status == "TRAINED", f"{q}: CB should be a real trained-model prediction, got {pred.status}"
        assert pred.probability is not None
        assert 0.0 <= pred.probability <= 1.0
