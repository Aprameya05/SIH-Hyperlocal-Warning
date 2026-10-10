"""
tests/test_phase56_exit134_real_mitigation.py
=====================================================
2026-10-10: the earlier exit-134 ("double free or corruption (!prev)")
fix (os._exit(0) in scripts/phase34_build_unified_forecast.py's
__main__ guard) was verified only by running the script as a
subprocess on this session's own Windows development machine --
which cannot reproduce a glibc heap-corruption abort at all (Windows
doesn't use glibc's malloc). That "verification" was real but not
meaningful evidence for the actual failure.

Confirmed via the GitHub Checks annotations API (full job logs
require repo sign-in this session has no credentials for, but
check-run annotations are public) that the real Ubuntu runner was
STILL hitting "Process completed with exit code 134" on every
scheduled run for two days after that fix was deployed -- the crash
happens somewhere os._exit(0) never gets reached, most likely during
model inference in a native-extension worker thread (xgboost/OpenMP),
not only at interpreter shutdown.

Real mitigation: MALLOC_ARENA_MAX=1 / OMP_NUM_THREADS=1 on the
workflow step (the standard, widely-documented fix for exactly this
symptom from multi-threaded xgboost/OpenBLAS on Linux glibc), plus
nthread=1 set directly on both XGBoost model objects as defense in
depth (OMP_NUM_THREADS does not always fully constrain XGBoost's own
internal thread pool sizing).

This test cannot reproduce the Linux crash on Windows either -- it
verifies the mitigation is actually wired in, not that it fixes the
crash (only a real Ubuntu CI run can confirm that).
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "forecast_update.yml"


def test_workflow_sets_malloc_arena_max_and_omp_threads_on_the_crashing_step():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    start = text.index("Generate unified SIH forecast artifact")
    end = text.index("Send WhatsApp alerts")
    step_text = text[start:end]
    assert "MALLOC_ARENA_MAX" in step_text
    assert '"1"' in step_text
    assert "OMP_NUM_THREADS" in step_text


@pytest.mark.skipif(
    not (REPO_ROOT / "models" / "panindia_cb_v1" / "panindia_cb_v1_model.json").exists(),
    reason="panindia_cb_v1 model artifact not present in this checkout",
)
def test_cb_booster_nthread_is_constrained_to_one():
    import sys
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from panindia_cb_model_interface import PanIndiaCBModel

    m = PanIndiaCBModel()
    # XGBoost exposes its own params via save_config (a JSON string);
    # confirm nthread genuinely landed on the live Booster object,
    # not merely called without effect.
    import json
    config = json.loads(m.model.save_config())
    nthread = config.get("learner", {}).get("generic_param", {}).get("nthread")
    assert nthread == "1", f"expected nthread='1' on the Booster, got {nthread!r}"


@pytest.mark.skipif(
    not (REPO_ROOT / "models" / "thunderstorm_model.pkl").exists(),
    reason="thunderstorm_model.pkl not present in this checkout",
)
def test_ts_booster_nthread_is_constrained_to_one():
    import sys
    import json
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from ts_station_model_interface import VOBLThunderstormModel

    m = VOBLThunderstormModel()
    config = json.loads(m.model.get_booster().save_config())
    nthread = config.get("learner", {}).get("generic_param", {}).get("nthread")
    assert nthread == "1", f"expected nthread='1' on the Booster, got {nthread!r}"
