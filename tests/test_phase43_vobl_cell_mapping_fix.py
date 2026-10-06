"""
tests/test_phase43_vobl_cell_mapping_fix.py
==============================================
2026-10-06 (Phase D takeover pass): regression test for a confirmed
correctness bug -- VOBL_CELL_ID was hardcoded to "IND_13.0_77.0" in
scripts/ts_station_model_interface.py and
backend/models/unified_mtl/shared_target_schema.py, but that cell is
79.6km from VOBL (13.1979N, 77.7063E). "IND_13.0_78.0" is the
genuinely nearest canonical cell (38.68km) -- confirmed independently
here via haversine distance, and already used correctly elsewhere
(canonical_forecast_writer.py, scripts/build_panindia_dataset.py).

This proves the fix, not just that some cell is picked.
"""
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

VOBL_LAT, VOBL_LON = 13.1979, 77.7063


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def test_independently_verified_nearest_cell_is_78_not_77():
    d77 = _haversine_km(VOBL_LAT, VOBL_LON, 13.0, 77.0)
    d78 = _haversine_km(VOBL_LAT, VOBL_LON, 13.0, 78.0)
    assert d78 < d77, f"IND_13.0_78.0 ({d78:.1f}km) must be nearer than IND_13.0_77.0 ({d77:.1f}km)"
    assert d78 < 50.0, f"IND_13.0_78.0 should be a plausible nearest-cell distance, got {d78:.1f}km"


def test_ts_station_model_interface_uses_corrected_cell():
    import ts_station_model_interface as ts_mod
    assert ts_mod.VOBL_CELL_ID == "IND_13.0_78.0"
    assert ts_mod.VOBL_CELL_ID != "IND_13.0_77.0"


def test_shared_target_schema_uses_corrected_cell():
    from backend.models.unified_mtl import shared_target_schema as sts
    assert sts.VOBL_CELL_ID == "IND_13.0_78.0"
    assert sts.VOBL_CELL_ID != "IND_13.0_77.0"


def test_phase22_common_grid_vobl_station_cells_corrected():
    import phase22_common_grid as grid_mod
    assert grid_mod.VOBL_STATION_CELLS == {"IND_13.0_78.0"}


def test_canonical_forecast_writer_and_build_panindia_dataset_already_agree():
    """These two files already independently used the correct cell before
    this fix -- confirms we converged everything to the SAME value rather
    than introducing a third, different one."""
    import canonical_forecast_writer as cfw
    import build_panindia_dataset as bpd
    assert cfw.VOBL_CELL_ID == "IND_13.0_78.0"
    assert bpd.VOBL_CELL_ID == "IND_13.0_78.0"


def test_unified_forecast_artifact_metar_tag_is_on_the_corrected_cell():
    """Static artifact correction: data/pan_india_common_grid_992.json's
    METAR OBSERVED tag must be on IND_13.0_78.0, not IND_13.0_77.0."""
    import json
    with open(REPO_ROOT / "data" / "pan_india_common_grid_992.json", encoding="utf-8") as f:
        grid = json.load(f)
    by_id = {c["cell_id"]: c for c in grid["cells"]}
    assert by_id["IND_13.0_78.0"]["metar"]["category"] == "OBSERVED"
    assert by_id["IND_13.0_77.0"]["metar"]["category"] == "MISSING"
    assert by_id["IND_13.0_77.0"]["metar"]["status"] == "NO_STATION_IN_CELL"


def test_inference_engine_ts_domain_check_uses_corrected_cell():
    """The real production TS inference path must only accept the
    corrected cell as in-domain for the station baseline."""
    from backend.models.unified_mtl.inference_engine import UnifiedInferenceEngine
    from datetime import datetime, timezone
    engine = UnifiedInferenceEngine()
    init = datetime(2024, 8, 1, tzinfo=timezone.utc)
    # The old (wrong) cell must now be refused as out-of-domain.
    wrong_cell_pred = engine.predict_ts("IND_13.0_77.0", 3, init, None)
    assert wrong_cell_pred.probability is None
    assert "IND_13.0_78.0" in str(wrong_cell_pred.extra.get("reason", ""))
