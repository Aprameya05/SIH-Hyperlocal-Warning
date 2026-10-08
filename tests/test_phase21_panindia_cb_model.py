"""
tests/test_phase21_panindia_cb_model.py

Validation suite for the Phase 21 pan-India CB model pipeline. Checks the
feature engineering, label filtering, grouped (never random-row) splitting,
and the saved artifacts/model-interface contract. Does not touch raw GRIB
files, production files, or the existing VOBL cb_slot_* models.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from panindia_cb_features import load_full_dataset, engineer_daily_features, get_feature_columns  # noqa: E402
from train_panindia_cb_model import filter_labels, build_splits  # noqa: E402

ARTIFACT_DIR = REPO_ROOT / "models/panindia_cb_v1"


@pytest.fixture(scope="module")
def raw_df():
    return load_full_dataset()


@pytest.fixture(scope="module")
def feat_df(raw_df):
    return engineer_daily_features(raw_df, include_prate=False, include_9000pa=False)


def test_feature_engineering_produces_one_row_per_cycle_cell(feat_df):
    assert len(feat_df) == 10 * 992
    assert feat_df.groupby(["cycle", "cell_id"]).size().max() == 1


def test_missing_rows_never_coerced_to_negative_in_filter(feat_df):
    labeled = filter_labels(feat_df)
    assert "MISSING" not in labeled["label_status"].unique()
    assert set(labeled["label_status"].unique()) <= {"POSITIVE", "CONFIRMED_NEGATIVE"}
    assert set(labeled["cb_label_daily"].unique()) <= {0, 1}
    n_missing_in_full = (feat_df["label_status"] == "MISSING").sum()
    assert len(feat_df) - len(labeled) == n_missing_in_full


def test_label_counts_match_phase20(feat_df):
    labeled = filter_labels(feat_df)
    assert int((labeled["cb_label_daily"] == 1).sum()) == 400
    assert int((labeled["cb_label_daily"] == 0).sum()) == 3460


def test_splits_are_grouped_by_date_never_by_row(feat_df):
    """The core Phase 21 requirement: no row-level random split anywhere.
    Every split's train/test partition must be a clean date-level
    partition (no date appears in both train and test)."""
    labeled = filter_labels(feat_df)
    splits = build_splits(labeled)
    assert len(splits) == 10  # one fold per target date present in the labeled data
    for split in splits:
        train_dates = set(labeled.loc[split["train_idx"], "target_date"].unique())
        test_dates = set(labeled.loc[split["test_idx"], "target_date"].unique())
        assert train_dates.isdisjoint(test_dates)
        assert test_dates == {split["held_out_date"]}


def test_degenerate_dates_flagged_not_silently_dropped(feat_df):
    labeled = filter_labels(feat_df)
    splits = build_splits(labeled)
    degenerate = [s for s in splits if s["degenerate"]]
    non_degenerate = [s for s in splits if not s["degenerate"]]
    assert len(degenerate) == 5  # the 5 confirmed-negative-only target dates
    assert len(non_degenerate) == 5  # the 5 positive-heavy target dates
    for s in degenerate:
        assert s["n_test_positive"] == 0 or s["n_test_negative"] == 0


def test_feature_columns_documented():
    from panindia_cb_features import FEATURE_DOCS
    cols_with_prate_9000 = get_feature_columns(include_prate=True, include_9000pa=True)
    for col in cols_with_prate_9000:
        assert col in FEATURE_DOCS, f"feature {col} is not documented in FEATURE_DOCS"
        doc = FEATURE_DOCS[col]
        assert "formula" in doc and "source" in doc and "units" in doc


def test_ablation_feature_sets_differ_only_in_ablation_columns():
    base = set(get_feature_columns(include_prate=False, include_9000pa=False))
    with_prate = set(get_feature_columns(include_prate=True, include_9000pa=False))
    with_9000 = set(get_feature_columns(include_prate=False, include_9000pa=True))
    assert with_prate - base == {"prate_kgm2s_max", "prate_kgm2s_mean"}
    assert with_9000 - base == {"cape_9000pa_jkg_mean", "cin_9000pa_jkg_mean", "cape_9000pa_available"}


def test_artifacts_saved_under_new_name_not_overwriting_vobl_models():
    vobl_files = ["cb_slot_0_model.json", "cb_slot_1_model.json",
                  "cb_slot_2_model.json", "cb_slot_3_model.json"]
    for fname in vobl_files:
        assert (REPO_ROOT / "models" / fname).exists(), f"VOBL artifact {fname} should still exist unmodified"
    assert (ARTIFACT_DIR / "panindia_cb_v1_model.json").exists()
    assert (ARTIFACT_DIR / "panindia_cb_v1_feature_list.json").exists()
    assert (ARTIFACT_DIR / "panindia_cb_v1_metrics.json").exists()
    assert (ARTIFACT_DIR / "panindia_cb_v1_shap.json").exists()


def test_model_interface_contract():
    from panindia_cb_model_interface import PanIndiaCBModel
    model = PanIndiaCBModel()
    desc = model.describe()
    # 2026-10-08 correction: this model was confirmed wired into
    # backend/models/unified_mtl/heads.py:CBHead, called for every live
    # CB prediction in scripts/phase34_build_unified_forecast.py -- the
    # original "HISTORICAL/RESEARCH, not deployed" status this test
    # asserted was accurate when written but is stale now. Assert the
    # corrected, current claim instead of the old one.
    assert "LIVE PRODUCTION MODEL" in desc["status"]
    assert "CBHead" in desc["status"]
    with open(ARTIFACT_DIR / "panindia_cb_v1_feature_list.json") as f:
        expected_cols = json.load(f)
    assert model.feature_cols == expected_cols


def test_model_interface_rejects_missing_columns():
    from panindia_cb_model_interface import PanIndiaCBModel
    import pandas as pd
    model = PanIndiaCBModel()
    incomplete_df = pd.DataFrame({"cape_sfc_jkg_max": [100.0]})
    with pytest.raises(ValueError):
        model.predict_proba(incomplete_df)


def test_metrics_report_includes_required_fields():
    with open(ARTIFACT_DIR / "panindia_cb_v1_metrics.json") as f:
        metrics = json.load(f)
    pooled = metrics["lodo_pooled_uncalibrated"]
    for key in ("auroc", "pr_auc", "brier_score", "pod_recall", "far",
                "precision", "csi", "hss", "confusion_matrix", "threshold"):
        assert key in pooled, f"missing required metric: {key}"


def test_lodo_is_the_headline_validation_not_random_split():
    """Guards against ever reporting a random row-split as the primary
    result: the metrics artifact must be keyed by LODO terminology, and
    per_date_results must cover all 10 dates."""
    with open(ARTIFACT_DIR / "panindia_cb_v1_metrics.json") as f:
        metrics = json.load(f)
    assert "lodo_pooled_uncalibrated" in metrics
    assert len(metrics["per_date_results"]) == 10
