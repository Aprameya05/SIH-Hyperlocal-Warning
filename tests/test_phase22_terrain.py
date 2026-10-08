"""tests/test_phase22_terrain.py -- Phase 22 Track 1 terrain/hydrology extension tests."""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import phase22_terrain_panindia as t  # noqa: E402

OUTPUT_PATH = REPO_ROOT / "data" / "pan_india_terrain_992.json"


def _load():
    with open(OUTPUT_PATH) as f:
        return json.load(f)


def test_output_file_exists_and_has_992_cells():
    out = _load()
    assert out["n_cells"] == 992
    assert len(out["cells"]) == 992


def test_no_cell_outside_blr_has_a_fabricated_dem_value():
    out = _load()
    for c in out["cells"]:
        if c["terrain_status"] == "BLOCKED_NETWORK_SRTM":
            assert c["elevation_m"] is None
            assert c["slope_deg"] is None
            assert c["flood_susceptibility_dem_proxy"] is None


def test_cells_with_real_dem_are_within_or_near_blr_bbox():
    out = _load()
    for c in out["cells"]:
        if c["terrain_status"] == "CACHED_REAL_SRTM_VOBL_ONLY":
            assert 11.0 <= c["lat"] <= 15.0
            assert 76.0 <= c["lon"] <= 79.5


def test_hydrology_never_interpolated_to_ungauged_cells():
    out = _load()
    for c in out["cells"]:
        if c["catchment_status"] == "MISSING_NO_GAUGE":
            assert c["gauge_ids_used"] == []
            assert all(v is None for v in c["hydrology"].values())
        else:
            assert c["catchment_status"] == "OBSERVED_SPARSE"
            assert len(c["gauge_ids_used"]) >= 1


def test_srtm_probe_is_single_attempt_not_a_retry_loop():
    import inspect
    src = inspect.getsource(t.probe_srtm_reachable)
    assert "for " not in src and "while " not in src


def test_dem_and_hydrology_counts_are_consistent_with_cell_records():
    out = _load()
    # Phase 27: real DEM now comes from TWO sources -- the Phase 26 pan-India
    # SRTM acquisition (REAL_SRTM_PANINDIA, 353/992 cells) takes priority, and
    # the pre-existing Bengaluru/VOBL-only cache (CACHED_REAL_SRTM_VOBL_ONLY)
    # covers any remaining cell inside its bbox. Both count toward the real total.
    n_dem_real = sum(1 for c in out["cells"] if c["terrain_status"] in
                      ("CACHED_REAL_SRTM_VOBL_ONLY", "REAL_SRTM_PANINDIA"))
    n_hydro_obs = sum(1 for c in out["cells"] if c["catchment_status"] == "OBSERVED_SPARSE")
    assert n_dem_real == out["dem_pipeline"]["n_cells_with_real_cached_dem"]
    assert n_hydro_obs == out["hydrology_pipeline"]["n_cells_with_observed_hydrology"]


def test_cell_ids_match_canonical_convention():
    out = _load()
    for c in out["cells"][:5]:
        assert c["cell_id"].startswith("IND_")


def test_pan_india_coverage_is_honestly_sparse_not_claimed_complete():
    """2026-10-08 update: DEM coverage reached genuine 992/992 once the
    AWS SRTM tile source's earlier network block was re-verified and
    found resolved (scripts/acquire_panindia_terrain.py) -- that is a
    real result, not something to force back to "partial" here.
    Hydrology remains genuinely gauge-limited (75/992 INDOFLOODS
    gauges, not a network issue) and must stay honestly reported as
    such -- that is what this test actually guards."""
    out = _load()
    assert out["dem_pipeline"]["n_cells_with_real_cached_dem"] <= 992
    assert out["hydrology_pipeline"]["n_cells_with_observed_hydrology"] < 992
    assert "NOT" in out["hydrology_pipeline"]["coverage_note"]
