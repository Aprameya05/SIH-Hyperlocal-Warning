#!/usr/bin/env python3
"""
Phase 5.7 (research-only): train and evaluate PU flash-flood trigger
models on processed/ff_pu/ff_pu_training_table.csv.

Writes only under processed/ff_pu/. Never touches backend/pipeline.py or
any production file.

PU formulation implemented (Elkan-Noto / SCAR class-prior correction,
option (a) from the assignment):
  - We assume Selected Completely At Random (SCAR): among the true
    positive population, the labeled positives are a uniform random
    subset. This is a real assumption, not free of risk here (event
    reporting almost certainly favors better-monitored gauges), and is
    stated explicitly as a limitation.
  - c = P(labeled | positive) is estimated ONLY on a held-out slice of
    the TRAINING period's data via the standard Elkan-Noto e1 estimator:
    train a probabilistic classifier s(x) = P(label=1 | x) on
    (labeled-positive vs unlabeled) using the training period only, then
    c = mean(s(x)) over a validation fold of the labeled positives drawn
    from that same training period.
  - The corrected posterior P(y=1 | x) = s(x) / c (clipped to [0,1]).
  - A logistic-regression PU baseline and an XGBoost PU model both use
    this correction. Both are also run under a plain "unlabeled-as-weak-
    negative" naive baseline for comparison, always reported with PU
    caveats -- never presented as if U were confirmed negative.

Temporal split: train on 2015-01-01..2018-12-31, validate on
2019-01-01..2020-09-24. No shuffling across the boundary.

Spatial holdout: the single event cell with the most POSITIVE rows is
identified from the data (not assumed) and excluded entirely from
training and from c-estimation; evaluated separately.
"""
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import xgboost as xgb

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "processed", "ff_pu")
TABLE_CSV = os.path.join(OUT_DIR, "ff_pu_training_table.csv")

SEED = 42
TRAIN_START, TRAIN_END = pd.Timestamp("2015-01-01"), pd.Timestamp("2018-12-31")
VAL_START, VAL_END = pd.Timestamp("2019-01-01"), pd.Timestamp("2020-09-24")

RAIN_FEATURES = ["rain_1d", "rain_3d", "rain_5d", "rain_10d", "rain_max1d_5d",
                  "rain_recent_vs_antecedent_ratio", "rain_accel_3d_minus_prior3d"]
CATCHMENT_NUMERIC = [
    "Stream Order", "Drainage Density", "Drainage Texture", "Drainage Intensity",
    "Channel Frequency", "Infiltration Number", "No. of Firstorder Streams",
    "No. of Secondorder Streams", "No. of Thirdorder Streams", "Basin Magnitude",
    "Fitness Ratio", "Wandering Ratio", "Maximal Flow Length", "Downvalley Length",
    "Drainage Area", "Catchment Relief", "Catchment Length", "Catchment Perimeter",
    "Sinuosity Index", "Form Factor", "Relief Ratio", "Elongation Ratio",
    "Circularity Ratio", "Lemniscates Value", "Compactness Coefficient",
    "Ruggedness Number", "Annual Precipitation", "Precipitation Seasonality",
    "Precipitation of Wettest Month", "Precipitation of Wettest Quarter",
    "Annual Mean Temperature", "Temperature Seasonality",
]
CATCHMENT_CATEGORICAL = ["Soil type", "lithology type", "Land cover", "KoppenGeiger Climate Type"]

PRIOR_SENSITIVITY_GRID = [0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40]


def load_table():
    df = pd.read_csv(TABLE_CSV, parse_dates=["date"])
    for c in CATCHMENT_CATEGORICAL:
        df[c] = df[c].astype("category").cat.codes.replace(-1, np.nan)
    return df


def make_xy(df, feature_cols):
    X = df[feature_cols].copy()
    for c in feature_cols:
        X[c] = pd.to_numeric(X[c], errors="coerce")
    med = X.median()
    X = X.fillna(med)
    y_true_labeled = (df["label_status"] == "POSITIVE").astype(int).values
    return X, y_true_labeled, med


def elkan_noto_c(clf_scores_on_labeled_holdout):
    c = float(np.mean(clf_scores_on_labeled_holdout))
    return max(c, 1e-3)


def enrichment_at_k(scores, y_true, k_frac):
    n = len(scores)
    k = max(1, int(round(n * k_frac)))
    order = np.argsort(-scores)
    top_k_idx = order[:k]
    hits = y_true[top_k_idx].sum()
    base_rate = y_true.sum() / n if n > 0 else np.nan
    precision_at_k = hits / k
    enrichment = precision_at_k / base_rate if base_rate > 0 else np.nan
    recall_at_k = hits / y_true.sum() if y_true.sum() > 0 else np.nan
    return {"k": int(k), "k_frac": k_frac, "positives_in_top_k": int(hits),
            "precision_at_k": float(precision_at_k), "base_rate": float(base_rate),
            "enrichment_over_base_rate": float(enrichment), "recall_at_k": float(recall_at_k)}


def pu_ranking_auc(scores, y_true):
    """AUC treating U as the negative class for RANKING purposes only --
    explicitly NOT an estimate of true discriminative performance against
    confirmed negatives (none exist). Caveated throughout as required."""
    from sklearn.metrics import roc_auc_score
    if y_true.sum() == 0 or y_true.sum() == len(y_true):
        return None
    return float(roc_auc_score(y_true, scores))


def run_models(feature_sets, train_df, val_df, holdout_df, med_lookup=None):
    results = {}
    for model_name, feature_cols in feature_sets.items():
        Xtr, ytr, med = make_xy(train_df, feature_cols)
        Xval, yval, _ = make_xy(val_df, feature_cols)
        Xval = Xval.fillna(med)
        Xhold, yhold, _ = make_xy(holdout_df, feature_cols)
        Xhold = Xhold.fillna(med)

        scaler = StandardScaler()
        Xtr_s = scaler.fit_transform(Xtr)
        Xval_s = scaler.transform(Xval)
        Xhold_s = scaler.transform(Xhold)

        # --- Logistic PU (naive labeled-vs-unlabeled probabilistic classifier s(x)) ---
        logit = LogisticRegression(max_iter=2000, random_state=SEED, class_weight="balanced")
        logit.fit(Xtr_s, ytr)
        s_train = logit.predict_proba(Xtr_s)[:, 1]

        # Elkan-Noto c: score on a held-out fold of TRAIN-period labeled positives only.
        pos_idx = np.where(ytr == 1)[0]
        rng = np.random.RandomState(SEED)
        holdout_pos_idx = rng.choice(pos_idx, size=max(1, len(pos_idx) // 5), replace=False)
        c_logit = elkan_noto_c(s_train[holdout_pos_idx])

        s_val_logit = logit.predict_proba(Xval_s)[:, 1]
        s_hold_logit = logit.predict_proba(Xhold_s)[:, 1]
        corrected_val_logit = np.clip(s_val_logit / c_logit, 0, 1)
        corrected_hold_logit = np.clip(s_hold_logit / c_logit, 0, 1)

        # --- XGBoost PU ---
        dtrain = xgb.DMatrix(Xtr, label=ytr)
        params = {"objective": "binary:logistic", "eval_metric": "auc", "seed": SEED,
                  "max_depth": 4, "eta": 0.1, "subsample": 0.8, "colsample_bytree": 0.8,
                  "scale_pos_weight": float((ytr == 0).sum() / max(1, (ytr == 1).sum()))}
        booster = xgb.train(params, dtrain, num_boost_round=150)
        s_train_xgb = booster.predict(xgb.DMatrix(Xtr))
        c_xgb = elkan_noto_c(s_train_xgb[holdout_pos_idx])
        s_val_xgb = booster.predict(xgb.DMatrix(Xval))
        s_hold_xgb = booster.predict(xgb.DMatrix(Xhold))
        corrected_val_xgb = np.clip(s_val_xgb / c_xgb, 0, 1)
        corrected_hold_xgb = np.clip(s_hold_xgb / c_xgb, 0, 1)

        def pack(scores_val, scores_hold, c_est):
            return {
                "elkan_noto_c_estimate": float(c_est),
                "validation": {
                    "pu_ranking_auc_caveated": pu_ranking_auc(scores_val, yval),
                    "enrichment_top1pct": enrichment_at_k(scores_val, yval, 0.01),
                    "enrichment_top5pct": enrichment_at_k(scores_val, yval, 0.05),
                },
                "spatial_holdout": {
                    "pu_ranking_auc_caveated": pu_ranking_auc(scores_hold, yhold),
                    "enrichment_top1pct": enrichment_at_k(scores_hold, yhold, 0.01),
                    "enrichment_top5pct": enrichment_at_k(scores_hold, yhold, 0.05),
                    "n_positives_in_holdout_cell": int(yhold.sum()),
                    "n_rows_in_holdout_cell": int(len(yhold)),
                },
            }

        results[model_name] = {
            "feature_columns": feature_cols,
            "logistic_pu": pack(corrected_val_logit, corrected_hold_logit, c_logit),
            "logistic_pu_naive_uncorrected": pack(s_val_logit, s_hold_logit, 1.0),
            "xgboost_pu": pack(corrected_val_xgb, corrected_hold_xgb, c_xgb),
            "xgboost_pu_naive_uncorrected": pack(s_val_xgb, s_hold_xgb, 1.0),
        }
        results[model_name]["_objects"] = {
            "logit": logit, "scaler": scaler, "booster": booster, "median": med,
            "Xval": Xval, "yval": yval, "Xhold": Xhold, "yhold": yhold,
            "feature_cols": feature_cols,
        }
    return results


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    df = load_table()

    labeled = df[df["label_status"] == "POSITIVE"]
    holdout_cell = labeled.groupby("cell_id").size().sort_values(ascending=False).index[0]
    holdout_cell_n_pos = int(labeled.groupby("cell_id").size().max())
    print(f"Spatial holdout cell (highest positive volume, computed): {holdout_cell} "
          f"({holdout_cell_n_pos} positives)")

    non_holdout = df[df["cell_id"] != holdout_cell].copy()
    holdout_df = df[df["cell_id"] == holdout_cell].copy()

    train_df = non_holdout[(non_holdout["date"] >= TRAIN_START) & (non_holdout["date"] <= TRAIN_END)].copy()
    val_df = non_holdout[(non_holdout["date"] >= VAL_START) & (non_holdout["date"] <= VAL_END)].copy()
    assert train_df["date"].max() < val_df["date"].min(), "train must be strictly before validation"
    assert holdout_cell not in train_df["cell_id"].unique()
    assert holdout_cell not in val_df["cell_id"].unique()

    print(f"Train rows: {len(train_df)} (positives={int((train_df.label_status=='POSITIVE').sum())})")
    print(f"Val rows: {len(val_df)} (positives={int((val_df.label_status=='POSITIVE').sum())})")
    print(f"Holdout-cell rows: {len(holdout_df)} (positives={int((holdout_df.label_status=='POSITIVE').sum())})")

    feature_sets = {
        "model_a_catchment_only": CATCHMENT_NUMERIC + CATCHMENT_CATEGORICAL,
        "model_b_rainfall_only": RAIN_FEATURES,
        "model_c_combined": RAIN_FEATURES + CATCHMENT_NUMERIC + CATCHMENT_CATEGORICAL,
    }

    results = run_models(feature_sets, train_df, val_df, holdout_df)

    # ---- Sensitivity analysis: vary assumed class prior directly (bypassing
    # the estimated c) for Model C's XGBoost PU scores, to show how
    # ranking/enrichment respond to the SCAR-prior assumption. ----
    sens_rows = []
    objs = results["model_c_combined"]["_objects"]
    s_val_xgb_raw = objs["booster"].predict(xgb.DMatrix(objs["Xval"]))
    s_hold_xgb_raw = objs["booster"].predict(xgb.DMatrix(objs["Xhold"]))
    for c_assumed in PRIOR_SENSITIVITY_GRID:
        corrected_val = np.clip(s_val_xgb_raw / c_assumed, 0, 1)
        corrected_hold = np.clip(s_hold_xgb_raw / c_assumed, 0, 1)
        e1 = enrichment_at_k(corrected_val, objs["yval"], 0.01)
        e5 = enrichment_at_k(corrected_val, objs["yval"], 0.05)
        eh1 = enrichment_at_k(corrected_hold, objs["yhold"], 0.01)
        auc_val = pu_ranking_auc(corrected_val, objs["yval"])
        sens_rows.append({
            "assumed_class_prior_c": c_assumed,
            "val_pu_ranking_auc": auc_val,
            "val_enrichment_top1pct": e1["enrichment_over_base_rate"],
            "val_enrichment_top5pct": e5["enrichment_over_base_rate"],
            "val_recall_at_top5pct": e5["recall_at_k"],
            "holdout_enrichment_top1pct": eh1["enrichment_over_base_rate"],
        })
    sens_df = pd.DataFrame(sens_rows)
    sens_df.to_csv(os.path.join(OUT_DIR, "sensitivity_results.csv"), index=False)
    print("Sensitivity analysis (Model C, XGBoost PU):")
    print(sens_df.to_string(index=False))

    # ---- Feature importance + SHAP for Model C's XGBoost model ----
    booster_c = objs["booster"]
    fi = booster_c.get_score(importance_type="gain")
    fi_rows = []
    for feat, gain in fi.items():
        group = "rainfall_dynamic" if feat in RAIN_FEATURES else "catchment_static"
        fi_rows.append({"feature": feat, "gain_importance": gain, "group": group})
    fi_df = pd.DataFrame(fi_rows).sort_values("gain_importance", ascending=False)
    fi_df.to_csv(os.path.join(OUT_DIR, "feature_importance.csv"), index=False)
    print("\nFeature importance (Model C, XGBoost, gain):")
    print(fi_df.to_string(index=False))

    shap_written = False
    try:
        import shap
        explainer = shap.TreeExplainer(booster_c)
        pos_val_mask = objs["yval"] == 1
        Xval_pos = objs["Xval"][pos_val_mask]
        sample = Xval_pos.head(min(10, len(Xval_pos)))
        if len(sample) > 0:
            shap_values = explainer.shap_values(sample)
            shap_df = pd.DataFrame(shap_values, columns=objs["feature_cols"])
            shap_df.insert(0, "row_index_in_validation_positives", sample.index)
            shap_df.to_csv(os.path.join(OUT_DIR, "shap_examples.csv"), index=False)
            shap_written = True
            print(f"\nWrote SHAP values for {len(sample)} representative confirmed-positive validation examples.")
    except Exception as e:
        print(f"SHAP computation skipped: {e}")

    # ---- Save validation results (strip non-serializable objects) ----
    def clean(d):
        if isinstance(d, dict):
            return {k: clean(v) for k, v in d.items() if k != "_objects"}
        return d

    validation_results = {name: clean(res) for name, res in results.items()}
    validation_results["spatial_holdout_cell"] = holdout_cell
    validation_results["spatial_holdout_cell_positive_count"] = holdout_cell_n_pos
    with open(os.path.join(OUT_DIR, "validation_results.json"), "w") as f:
        json.dump(validation_results, f, indent=2, default=str)
    print("\nWrote validation_results.json")

    # ---- model_metadata.json / feature_catalog.json ----
    model_metadata = {
        name: {"feature_columns": res["feature_columns"]} for name, res in results.items()
    }
    with open(os.path.join(OUT_DIR, "model_metadata.json"), "w") as f:
        json.dump(model_metadata, f, indent=2)

    feature_catalog = {
        "rainfall_features": RAIN_FEATURES,
        "catchment_numeric_features": CATCHMENT_NUMERIC,
        "catchment_categorical_features": CATCHMENT_CATEGORICAL,
    }
    with open(os.path.join(OUT_DIR, "feature_catalog.json"), "w") as f:
        json.dump(feature_catalog, f, indent=2)

    # ---- Save research-only model artifacts ----
    booster_c.save_model(os.path.join(OUT_DIR, "RESEARCH_ONLY_model_c_xgboost.json"))
    import pickle
    with open(os.path.join(OUT_DIR, "RESEARCH_ONLY_model_c_logistic.pkl"), "wb") as f:
        pickle.dump({"model": results["model_c_combined"]["_objects"]["logit"],
                     "scaler": results["model_c_combined"]["_objects"]["scaler"]}, f)

    # ---- Training manifest ----
    try:
        versions = subprocess.run(
            [sys.executable, "-c", "import numpy,pandas,sklearn,xgboost;"
             "print(numpy.__version__, pandas.__version__, sklearn.__version__, xgboost.__version__)"],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        versions = "unavailable"
    manifest = {
        "seed": SEED,
        "train_period": [str(TRAIN_START.date()), str(TRAIN_END.date())],
        "validation_period": [str(VAL_START.date()), str(VAL_END.date())],
        "spatial_holdout_cell": holdout_cell,
        "spatial_holdout_cell_positive_count": holdout_cell_n_pos,
        "feature_sets": {k: v for k, v in feature_sets.items()},
        "xgboost_hyperparameters": {"max_depth": 4, "eta": 0.1, "subsample": 0.8,
                                     "colsample_bytree": 0.8, "num_boost_round": 150,
                                     "objective": "binary:logistic"},
        "pu_method": "Elkan-Noto SCAR class-prior correction (e1 estimator), "
                     "c estimated on a 20%% held-out fold of training-period labeled "
                     "positives only; posterior = min(s(x)/c, 1)",
        "prior_sensitivity_grid": PRIOR_SENSITIVITY_GRID,
        "shap_computed": shap_written,
        "library_versions_numpy_pandas_sklearn_xgboost": versions,
        "artifacts": [
            "ff_pu_training_table.csv", "dataset_build_summary.json",
            "validation_results.json", "sensitivity_results.csv",
            "feature_importance.csv",
        ] + (["shap_examples.csv"] if shap_written else []) + [
            "model_metadata.json", "feature_catalog.json",
            "RESEARCH_ONLY_model_c_xgboost.json", "RESEARCH_ONLY_model_c_logistic.pkl",
            "training_manifest.json",
        ],
    }
    with open(os.path.join(OUT_DIR, "training_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print("Wrote training_manifest.json")


if __name__ == "__main__":
    main()
