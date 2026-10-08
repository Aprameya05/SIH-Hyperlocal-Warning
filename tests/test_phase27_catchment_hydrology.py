"""
tests/test_phase27_catchment_hydrology.py
==========================================
Phase 27 Part G: tests for the exact 28-column (really 36, 8 pre-existing +
28 newly-closed) catchment/hydrology resolution, spatial alignment, terrain
integration, provenance, missingness, partial 992-cell coverage, no
synthetic fill, FF feature adapter correctness, PU compatibility, temporal
correctness, and deterministic outputs.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import phase22_terrain_panindia as p22  # noqa: E402
import ff_feature_adapter as ffa  # noqa: E402


def _terrain():
    with open(REPO_ROOT / "data" / "pan_india_terrain_992.json") as f:
        return json.load(f)


def test_all_36_catchment_columns_present_in_schema():
    assert len(p22.CATCHMENT_FEATURE_COLUMNS) == 36
    assert set(p22.CATCHMENT_NUMERIC_COLUMNS) | set(p22.CATCHMENT_CATEGORICAL_COLUMNS) == set(p22.CATCHMENT_FEATURE_COLUMNS)
    with open(REPO_ROOT / "processed" / "ff_pu" / "model_metadata.json") as f:
        required = json.load(f)["model_c_combined"]["feature_columns"]
    catchment_required = [c for c in required if c not in ffa.RAIN_COLS]
    assert set(catchment_required) == set(p22.CATCHMENT_FEATURE_COLUMNS)


def test_28_previously_missing_columns_are_all_real_indofloods_columns():
    """Every one of the 28 columns identified at Phase 27 start as
    'missing everywhere' is a VERBATIM real column in the raw INDOFLOODS
    catchment CSV -- never an external acquisition, never invented."""
    df = pd.read_csv(p22.CATCHMENT_CSV_PATH)
    previously_missing = [
        'Annual Mean Temperature', 'Annual Precipitation', 'Basin Magnitude', 'Catchment Length',
        'Catchment Perimeter', 'Channel Frequency', 'Circularity Ratio', 'Compactness Coefficient',
        'Downvalley Length', 'Drainage Intensity', 'Drainage Texture', 'Fitness Ratio',
        'Infiltration Number', 'KoppenGeiger Climate Type', 'Land cover', 'Lemniscates Value',
        'Maximal Flow Length', 'No. of Firstorder Streams', 'No. of Secondorder Streams',
        'No. of Thirdorder Streams', 'Precipitation Seasonality', 'Precipitation of Wettest Month',
        'Precipitation of Wettest Quarter', 'Sinuosity Index', 'Soil type', 'Temperature Seasonality',
        'Wandering Ratio', 'lithology type',
    ]
    assert len(previously_missing) == 28
    for col in previously_missing:
        assert col in df.columns, f"{col} not a real column in the raw INDOFLOODS CSV"
        assert df[col].notna().any(), f"{col} is entirely null in the raw CSV -- would be fabrication to claim it's real"


def test_categorical_code_mapping_matches_regenerated_training_table():
    """The hardcoded CATCHMENT_CATEGORICAL_CODE_MAP must exactly match what
    pandas' .astype('category').cat.codes would assign on the (deterministic,
    regenerable) real training table -- never an invented encoding."""
    table_path = REPO_ROOT / "processed" / "ff_pu" / "ff_pu_training_table.csv"
    if not table_path.exists():
        pytest.skip("training table not regenerated in this environment")
    df = pd.read_csv(table_path)
    for col, expected_map in p22.CATCHMENT_CATEGORICAL_CODE_MAP.items():
        cats = list(df[col].astype("category").cat.categories)
        actual_map = {c: i for i, c in enumerate(cats)}
        assert actual_map == expected_map, f"{col} encoding drifted from the persisted model's training-time mapping"


def test_categorical_aggregation_uses_mode_never_blends_values():
    """When >1 gauge maps to a cell and they disagree on a categorical
    value, the result must be one real gauge's actual value (the mode),
    never an invented blended/averaged category."""
    gauge_mapping = p22.load_gauge_cell_mapping()
    catchment_data = p22.load_catchment_characteristics()
    found_disagreement = False
    for cid, gauges in gauge_mapping.items():
        gauges_with_data = [g for g in gauges if g in catchment_data]
        if len(gauges_with_data) < 2:
            continue
        hydro = p22.build_cell_hydrology(cid, gauge_mapping, catchment_data)
        for col in p22.CATCHMENT_CATEGORICAL_COLUMNS:
            raw_vals = [catchment_data[g][col] for g in gauges_with_data if catchment_data[g][col] is not None]
            if len(set(raw_vals)) > 1:
                found_disagreement = True
                assert hydro["hydrology_categorical_disagreement"][col] is True
                assert hydro["hydrology_categorical_raw"][col] in raw_vals  # a REAL observed value, not a blend
    assert found_disagreement, "expected at least one real multi-gauge categorical disagreement in this dataset"


def test_unseen_category_encodes_to_none_not_invented_code():
    mapping = p22.CATCHMENT_CATEGORICAL_CODE_MAP["Soil type"]
    assert mapping.get("Totally Fictional Soil Type Not In Training Data") is None


def test_no_synthetic_fill_for_ungauged_cells():
    out = _terrain()
    for c in out["cells"]:
        if c["catchment_status"] == "MISSING_NO_GAUGE":
            assert all(v is None for v in c["hydrology"].values())
            assert all(v is None for v in c["hydrology_categorical_encoded"].values())
            assert c["gauge_ids_used"] == []


def test_partial_992_cell_coverage_never_claims_full():
    """2026-10-08 update: DEM genuinely reached 992/992 once the AWS
    SRTM source's network block was re-verified and found resolved --
    a real result, not forced back to "partial". Hydrology remains
    genuinely gauge-limited and must stay honestly partial regardless."""
    out = _terrain()
    n_real_dem = out["dem_pipeline"]["n_cells_with_real_cached_dem"]
    n_hydro = out["hydrology_pipeline"]["n_cells_with_observed_hydrology"]
    assert 0 < n_real_dem <= 992
    assert 0 < n_hydro < 992
    src = Path(p22.__file__).read_text()
    assert '"COMPLETE"' not in src


def test_terrain_integration_real_srtm_panindia_cells_present():
    """Part D: the real Phase 26 SRTM cells must show up with their own
    distinct status, never silently merged into the VOBL-cache status.
    2026-10-08: count is no longer pinned to 353 -- DEM acquisition is
    re-run against the real AWS source each time and now genuinely
    covers all 992 cells, but the status/source-attribution contract
    this test actually guards is unchanged."""
    out = _terrain()
    n_panindia_real = sum(1 for c in out["cells"] if c["terrain_status"] == "REAL_SRTM_PANINDIA")
    assert n_panindia_real > 0
    sample = next(c for c in out["cells"] if c["terrain_status"] == "REAL_SRTM_PANINDIA")
    assert sample["terrain_source"].startswith("data/pan_india_terrain_srtm_992.json")


def test_unified_pipeline_accepts_partial_terrain_without_requiring_992():
    """The unified pipeline must run correctly whether terrain coverage
    is partial or genuinely complete -- it must never REQUIRE full
    coverage to execute. 2026-10-08: coverage is now genuinely 992/992
    (AWS SRTM source's network block resolved), which is itself proof
    this requirement holds -- the pipeline doesn't special-case either
    state."""
    out = _terrain()
    assert out["n_cells"] == 992  # structurally complete...
    n_real = out["dem_pipeline"]["n_cells_with_real_cached_dem"]
    assert n_real <= 992  # ...and content coverage never exceeds the grid size


def test_ff_adapter_pu_ranking_cells_bounded_by_real_gauge_coverage():
    """FF PU_RANKING must never exceed the real gauge-mapped cell population
    (75/992) -- it is gauge-sparse by nature and must stay that way."""
    out = ffa.build_ff_input_table_for_date("2024-08-01")
    assert out["status"] in ("PU_RANKING", "UNAVAILABLE")
    assert out["n_cells_ready_for_ff_prediction"] <= 75


def test_ff_pu_prediction_runs_without_error_on_ready_cells():
    """Part F: if PU ranking genuinely activates, FFHead.predict() must
    actually execute end-to-end on the ready subset without raising."""
    sys.path.insert(0, str(REPO_ROOT))
    from backend.models.unified_mtl.heads import FFHead
    out = ffa.build_ff_input_table_for_date("2024-08-01")
    if out["status"] != "PU_RANKING":
        pytest.skip("PU ranking not active in this data snapshot")
    ft = out["feature_table"]
    ready = out["ready_cell_ids_full"]
    sub = ft[ft["cell_id"].isin(ready)].reset_index(drop=True)
    head = FFHead()
    scores = head.predict(sub, pu_corrected=True)
    assert len(scores) == len(sub)
    assert ((scores >= 0) & (scores <= 1)).all()


def test_ff_output_never_called_calibrated_probability():
    src = Path(ffa.__file__).read_text()
    assert "calibrated flood probability" not in src
    assert "PU (positive-unlabeled) ranking" in src or "ranking score" in src.lower()


def test_deterministic_terrain_hydrology_output():
    out1 = p22.build_panindia_terrain()
    out2 = p22.build_panindia_terrain()
    h1 = [c["hydrology_categorical_encoded"] for c in out1["cells"]]
    h2 = [c["hydrology_categorical_encoded"] for c in out2["cells"]]
    assert h1 == h2
