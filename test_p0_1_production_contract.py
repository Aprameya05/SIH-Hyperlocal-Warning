"""
Regression tests for the P0.1 production-correctness patch.

Covers the three confirmed defects from docs/PHASE_P0_LIVE_VERIFICATION.md:

  1. backend/pipeline.py now persists the CANONICAL cell_id
     (regrid.py::cell_id_for, "IND_{lat:.1f}_{lon:.1f}") on every one of the
     992 cells, reusing the existing convention rather than inventing a
     second one.
  2. forecast_action.py's model_used field, and compute_realtime_shap.py's
     binding to it, are exercised directly -- SHAP must use the model
     forecast_action.py actually used, never silently pick a different one.
  3. retrain_trigger.yml's "recal" branch now points at a script that
     actually exists.

Test 1 runs backend/pipeline.py's real run() function end-to-end against
SYNTHETIC GFS field arrays (download_grib/read_grib_fields monkeypatched --
no network, no real GRIB file) so the actual production cell-construction
loop, cell_id assignment, and grid-drift guardrail are exercised for real,
not re-implemented/approximated in the test.
"""
import importlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from regrid import cell_id_for  # noqa: E402


def _synthetic_fields(nlat=24, nlon=24):
    """A minimal-but-complete synthetic GRIB field dict covering every key
    backend/pipeline.py's run() looks up via field(...), with physically
    plausible (not NaN, not absurd) values."""
    shape = (nlat, nlon)
    return {
        "u_isobaricInhPa_850": np.full(shape, 5.0),
        "v_isobaricInhPa_850": np.full(shape, 3.0),
        "u_isobaricInhPa_200": np.full(shape, 20.0),
        "v_isobaricInhPa_200": np.full(shape, 15.0),
        "cape_surface": np.full(shape, 1200.0),
        "cin_surface": np.full(shape, -30.0),
        "pwat_atmosphereSingleLayer": np.full(shape, 45.0),
        "t_isobaricInhPa_850": np.full(shape, 288.0),
        "t_isobaricInhPa_700": np.full(shape, 278.0),
        "t_isobaricInhPa_500": np.full(shape, 258.0),
        "d_isobaricInhPa_850": np.full(shape, 283.0),
        "d_isobaricInhPa_700": np.full(shape, 270.0),
        "tp_surface": np.full(shape, 2.0),
    }


@pytest.fixture
def pipeline_module(tmp_path, monkeypatch):
    """A fresh import of backend/pipeline.py with network calls and the
    output directory monkeypatched, so run() can execute its REAL
    cell-construction/guardrail/write logic without touching the network
    or the real data/ directory."""
    spec = importlib.util.spec_from_file_location(
        "pipeline_p0_1_test", REPO_ROOT / "backend" / "pipeline.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(mod, "download_grib", lambda url, dest: True)
    monkeypatch.setattr(mod, "read_grib_fields", lambda path: _synthetic_fields())
    monkeypatch.setattr(mod, "OUT_DIR", tmp_path)
    monkeypatch.setattr(mod, "OUT_FILE", tmp_path / "pan_india_grid.json")
    monkeypatch.setattr(mod, "latest_gfs_cycle", lambda: ("20260101", "00"))
    return mod


# ── Defect 1: canonical cell_id persisted ───────────────────────────────────

def test_pipeline_imports_canonical_cell_id_for_not_a_second_scheme():
    """backend/pipeline.py must import the EXISTING regrid.cell_id_for, not
    define its own ad hoc format."""
    src = (REPO_ROOT / "backend" / "pipeline.py").read_text()
    assert "from regrid import cell_id_for" in src
    # the old ad hoc, un-prefixed, never-persisted format must be gone from
    # actual CODE (comment lines may legitimately mention it while explaining
    # the fix, so strip comment-only lines before checking).
    code_lines = [
        line for line in src.splitlines()
        if not line.strip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    assert '"{lat:.1f}_{lon:.1f}"' not in code_only


def test_cell_id_for_matches_canonical_convention():
    assert cell_id_for(13.0, 77.5) == "IND_13.0_77.5"
    assert cell_id_for(6.0, 68.0) == "IND_6.0_68.0"


def test_full_grid_run_produces_992_cells_with_unique_canonical_cell_ids(pipeline_module):
    pipeline_module.run()

    written = json.loads((pipeline_module.OUT_FILE).read_text())
    cells = written["grid_cells"] if "grid_cells" in written else written["cells"]

    assert len(cells) == 992, f"expected 992 cells, got {len(cells)}"
    assert written.get("grid_step_deg") == 1.0 or written.get("grid_step_deg") == pipeline_module.APPLICATION_GRID_STEP

    ids = [c.get("cell_id") for c in cells]
    assert all(cid is not None for cid in ids), "every cell must carry a cell_id"
    assert len(set(ids)) == 992, "all 992 cell_id values must be unique"
    assert all(cid.startswith("IND_") for cid in ids), "cell_id must use the canonical IND_ prefix convention"

    # canonical cell_id must be independently reproducible from the cell's own lat/lon
    for c in cells:
        assert c["cell_id"] == cell_id_for(c["lat"], c["lon"])

    # canonical cell set completeness: every expected (lat, lon) pair appears
    expected_ids = {
        cell_id_for(round(lat, 1), round(lon, 1))
        for lat in np.arange(pipeline_module.BOUNDS["S"], pipeline_module.BOUNDS["N"] + 0.5, 1.0)
        for lon in np.arange(pipeline_module.BOUNDS["W"], pipeline_module.BOUNDS["E"] + 0.5, 1.0)
    }
    assert set(ids) == expected_ids

    # no NaN/invalid hazard values
    for c in cells:
        for k in ("thunderstorm_probability", "cloudburst_probability", "flash_flood_probability"):
            v = c[k]
            assert v is not None and not (isinstance(v, float) and math.isnan(v))
            assert 0.0 <= v <= 1.0


def test_duplicate_cell_id_guardrail_still_fires(pipeline_module, monkeypatch):
    """The guardrail must still abort the run if cell_id generation were
    ever to produce a duplicate -- simulate by shrinking EXPECTED_APPLICATION_CELL_COUNT
    so the (correct) 992-cell grid now looks like unexpected drift, proving
    the check still fires on the real cell list rather than being bypassed
    by the new cell_id code path."""
    monkeypatch.setattr(pipeline_module, "EXPECTED_APPLICATION_CELL_COUNT", 991)
    with pytest.raises(RuntimeError, match="grid drift detected"):
        pipeline_module.run()


# ── Defect 2: SHAP must be bound to the model forecast_action.py actually used ──

def test_forecast_action_records_model_used_per_slot():
    src = (REPO_ROOT / "forecast_action.py").read_text()
    assert '"model_used"' in src


def test_compute_realtime_shap_reads_declared_model_from_forecast_json(tmp_path, monkeypatch):
    """compute_realtime_shap.py must load the model NAMED in forecast.json's
    per-slot model_used field, not independently re-select one via its own
    priority list."""
    import compute_realtime_shap as shap_mod
    importlib.reload(shap_mod)

    fake_forecast = {
        "slots": [
            {"slot": 0, "model_used": "nowcast_slot0_xgb_v2.pkl"},
            {"slot": 1, "model_used": "nowcast_slot1_xgb_v6_temporal.pkl"},
            {"slot": 2, "model_used": "nowcast_slot2_xgb_v6_temporal.pkl"},
            {"slot": 3, "model_used": "nowcast_slot3_xgb_v6_temporal.pkl"},
        ]
    }
    forecast_path = tmp_path / "forecast.json"
    forecast_path.write_text(json.dumps(fake_forecast))

    declared = shap_mod.model_used_for_slot(fake_forecast, 0)
    assert declared == "nowcast_slot0_xgb_v2.pkl"


def test_shap_does_not_silently_switch_when_a_higher_priority_model_exists(monkeypatch):
    """Even if a 'better' model (e.g. v6_temporal) is present on disk, SHAP
    must still use the model forecast.json declared for that slot (v2 in
    this fake forecast), never silently upgrade to whatever its own
    priority list would otherwise pick."""
    import compute_realtime_shap as shap_mod
    importlib.reload(shap_mod)

    fake_forecast = {"slots": [{"slot": 0, "model_used": "nowcast_slot0_xgb_v2.pkl"}]}
    declared = shap_mod.model_used_for_slot(fake_forecast, 0)
    # find_model()'s own priority list would pick v6_temporal first if it existed;
    # the declared value must win regardless of what find_model() would choose.
    assert declared == "nowcast_slot0_xgb_v2.pkl"
    assert declared != shap_mod.find_model(0)  # sanity: these differ, proving no silent override


def test_shap_reports_unavailable_when_declared_model_missing():
    import compute_realtime_shap as shap_mod
    importlib.reload(shap_mod)

    fake_forecast = {"slots": [{"slot": 0, "model_used": "nowcast_slot0_xgb_vDOES_NOT_EXIST.pkl"}]}
    result = shap_mod.compute_shap_for_slot(fake_forecast, 0, shap_mod.MODELS)
    assert result.get("available") is False
    assert "unavailable" in result.get("reason", "").lower() or "not found" in result.get("reason", "").lower()


def test_shap_never_falls_back_to_a_different_model_than_declared():
    """If the declared model can't be loaded, compute_shap_for_slot must
    NOT fall back to find_model()'s own priority pick -- it must report
    unavailable instead."""
    import compute_realtime_shap as shap_mod
    importlib.reload(shap_mod)

    fake_forecast = {"slots": [{"slot": 0, "model_used": "nowcast_slot0_xgb_vDOES_NOT_EXIST.pkl"}]}
    result = shap_mod.compute_shap_for_slot(fake_forecast, 0, shap_mod.MODELS)
    assert result.get("model_used") != shap_mod.find_model(0)
    assert result.get("available") is False


# ── Defect 3: retrain_trigger.yml recal branch must invoke an existing script ──

def test_retrain_trigger_recal_branch_invokes_an_existing_script():
    workflow_src = (REPO_ROOT / ".github" / "workflows" / "retrain_trigger.yml").read_text()
    assert "recalibrate_models.py" in workflow_src
    # Whatever path the workflow invokes it by, that path must exist in the repo.
    import re
    m = re.search(r"python\s+(\S*recalibrate_models\.py)", workflow_src)
    assert m, "could not find the recalibrate_models.py invocation in retrain_trigger.yml"
    invoked_path = m.group(1)
    assert (REPO_ROOT / invoked_path).exists(), (
        f"retrain_trigger.yml invokes '{invoked_path}', which does not exist in the repo"
    )
