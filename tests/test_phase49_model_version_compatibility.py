"""
tests/test_phase49_model_version_compatibility.py
=====================================================
2026-10-08: a real production run surfaced sklearn
InconsistentVersionWarning (IsotonicRegression pickled under 1.6.1,
loaded under 1.7.2) and an XGBoost serialized-model-compatibility
warning. Per instruction: determine whether the models are actually
NUMERICALLY SAFE under the current runtime before considering a
version pin or retrain -- a version mismatch warning is a caution,
not proof of a correctness bug.

Traced every .pkl in models/ + processed/ff_pu/ + rag/ and found the
warning's real sources: the LEGACY VOBL dashboard's
cb_slot_{0-3}_calibrator.pkl / ff_slot_{0-3}_calibrator.pkl (trained
under sklearn 1.6.1), plus processed/ff_pu/RESEARCH_ONLY_model_c_logistic.pkl
(trained under sklearn 1.8.0 -- a NEWER version than this environment's
1.7.2, the riskier direction for pickle compatibility). The production
panindia_cb_v1 calibrator triggers no warning at all under the current
environment.

This test empirically verifies every one of IsotonicRegression's
documented safety invariants (bounded to [0,1], monotonic
non-decreasing, never NaN) still hold when unpickled under the
current sklearn -- not just "it imports without raising." If a future
sklearn upgrade genuinely breaks these calibrators, this is where it
fails first, rather than silently shipping an invalid probability.
"""
import warnings
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

LEGACY_CALIBRATORS = [
    "models/cb_slot_0_calibrator.pkl", "models/cb_slot_1_calibrator.pkl",
    "models/cb_slot_2_calibrator.pkl", "models/cb_slot_3_calibrator.pkl",
    "models/ff_slot_0_calibrator.pkl", "models/ff_slot_1_calibrator.pkl",
    "models/ff_slot_2_calibrator.pkl", "models/ff_slot_3_calibrator.pkl",
]


def _load_pickle(rel_path: str):
    import pickle
    path = REPO_ROOT / rel_path
    if not path.exists():
        pytest.skip(f"{rel_path} not present in this checkout")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with open(path, "rb") as f:
            return pickle.load(f)


@pytest.mark.parametrize("rel_path", LEGACY_CALIBRATORS)
def test_legacy_calibrator_output_stays_within_safety_invariants(rel_path):
    """IsotonicRegression's contract: output always in [0,1], monotonic
    non-decreasing in its input, never NaN. Verified against the
    REAL pickled calibrator under the CURRENT sklearn -- this is the
    actual numerical-safety check the version-mismatch warning asked
    for, not a guess."""
    cal = _load_pickle(rel_path)
    raw = np.linspace(0, 1, 101)
    calibrated = cal.predict(raw)
    assert np.all((calibrated >= 0) & (calibrated <= 1)), f"{rel_path}: output left [0,1]"
    assert np.all(np.diff(calibrated) >= -1e-9), f"{rel_path}: output is not monotonic non-decreasing"
    assert not np.any(np.isnan(calibrated)), f"{rel_path}: produced NaN"


def test_production_cb_calibrator_loads_without_a_version_warning():
    """The actual production model (panindia_cb_v1) must not even
    trigger the warning -- confirming its pickle version already
    matches this environment's sklearn, unlike the legacy VOBL
    calibrators."""
    import pickle
    path = REPO_ROOT / "models" / "panindia_cb_v1" / "panindia_cb_v1_calibrator.pkl"
    if not path.exists():
        pytest.skip("panindia_cb_v1 calibrator not present in this checkout")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with open(path, "rb") as f:
            pickle.load(f)
    version_warnings = [w for w in caught if "version" in str(w.message).lower()]
    assert not version_warnings, f"unexpected version warning on the production calibrator: {version_warnings}"
