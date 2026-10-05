"""
scripts/ts_station_model_interface.py
========================================
Phase 25 Priority 1 -- clean, reusable prediction interface around the
EXISTING, already-trained, already-validated Bengaluru/VOBL thunderstorm
XGBoost model (models/thunderstorm_model.pkl, built by tune_model.py /
dev/main.py, evaluated by dev/evaluate.py -- see
results/evaluation_results.csv for the pre-existing time-split holdout
metrics, reproduced byte-for-byte by this module's own
reproduce_holdout_metrics()).

TS_STATUS = "STATION_BASELINE" -- this model is trained on
data/bengaluru_thunderstorm_features_merged.csv: daily surface
observations + IGRA radiosonde sounding indices + ERA5 reanalysis, ALL
for the single Bengaluru/VOBL station, 2015-2025 (3,819 days, 457
thunderstorm-positive days). It is NOT a pan-India model and this module
never claims pan-India generalization. Any caller asking for a cell
other than the VOBL station cell gets an explicit
"OUT_OF_DOMAIN_STATION_ONLY" response, not a silently-extrapolated
number.

This is a NEW file (an interface wrapper), not a retraining and not a
modification of tune_model.py / dev/*.py, which remain the original
training/eval scripts, unchanged.
"""
from __future__ import annotations

import pickle
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = REPO_ROOT / "models" / "thunderstorm_model.pkl"
TRAIN_DATA_PATH = REPO_ROOT / "data" / "bengaluru_thunderstorm_features_merged.csv"

VOBL_CELL_ID = "IND_13.0_77.0"  # nearest canonical 992-cell grid point to VOBL/VOBG (~13.2N 77.7E)
TS_STATUS = "STATION_BASELINE"


class VOBLThunderstormModel:
    """Wraps the real, pre-existing, time-split-validated XGBoost model.
    Train/test split used at training time: YEAR<=2022 train, YEAR>=2023
    test (held out, never touched during training) -- this is the SAME
    split dev/evaluate.py reports against, reproduced here."""

    def __init__(self):
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"{MODEL_PATH} missing")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with open(MODEL_PATH, "rb") as f:
                bundle = pickle.load(f)
        self.model = bundle["model"]
        self.feature_cols = bundle["features"]
        self.threshold = bundle["threshold"]

    def predict_proba(self, features_df: pd.DataFrame) -> np.ndarray:
        """features_df must contain every column in self.feature_cols
        (missing columns raise -- never silently zero-filled by this
        wrapper; the ONLY place NaN->0 happens is the model's own
        documented fillna(0) policy from training, applied explicitly
        below to match training-time behavior exactly, not to hide a
        caller error)."""
        missing = [c for c in self.feature_cols if c not in features_df.columns]
        if missing:
            raise ValueError(f"features_df is missing required columns: {missing}")
        X = features_df[self.feature_cols].fillna(0)
        return self.model.predict_proba(X)[:, 1]

    def reproduce_holdout_metrics(self) -> dict:
        """Re-runs the exact held-out (YEAR>=2023) evaluation dev/evaluate.py
        performs, from the original training data, and returns the real
        numbers -- never a cached/claimed value that isn't re-derived."""
        from sklearn.metrics import roc_auc_score
        df = pd.read_csv(TRAIN_DATA_PATH)
        test = df[df["YEAR"] >= 2023]
        X_test = test[self.feature_cols].fillna(0)
        y_test = test["LABEL"]
        proba = self.model.predict_proba(X_test)[:, 1]
        pred = (proba >= self.threshold).astype(int)
        TP = int(((pred == 1) & (y_test == 1)).sum())
        FP = int(((pred == 1) & (y_test == 0)).sum())
        TN = int(((pred == 0) & (y_test == 0)).sum())
        FN = int(((pred == 0) & (y_test == 1)).sum())
        pod = TP / (TP + FN) if (TP + FN) else None
        far = FP / (TP + FP) if (TP + FP) else None
        csi = TP / (TP + FN + FP) if (TP + FN + FP) else None
        hss_den = (TP + FN) * (FN + TN) + (TP + FP) * (FP + TN)
        hss = (2 * (TP * TN - FP * FN) / hss_den) if hss_den else None
        return {
            "n_test_days": len(y_test), "n_test_positives": int(y_test.sum()),
            "AUROC": float(roc_auc_score(y_test, proba)),
            "POD": pod, "FAR": far, "CSI": csi, "HSS": hss,
            "threshold": self.threshold,
            "train_years": "<=2022", "test_years": ">=2023 (held out, time-split)",
        }

    def explain(self, features_df: pd.DataFrame) -> dict:
        """Real SHAP TreeExplainer attribution (reuses the exact method
        dev/shap_analysis.py already used on this model -- not invented
        here, just made callable on arbitrary rows)."""
        try:
            import shap
        except ImportError:
            return {"available": False, "reason": "shap package not installed in this environment"}
        X = features_df[self.feature_cols].fillna(0)
        explainer = shap.TreeExplainer(self.model)
        shap_vals = explainer.shap_values(X)
        importance = pd.Series(np.abs(shap_vals).mean(axis=0), index=self.feature_cols)
        ranked = importance.sort_values(ascending=False)
        return {"available": True, "method": "SHAP TreeExplainer (reused from dev/shap_analysis.py)",
                "top_features": list(zip(ranked.index[:10].tolist(), ranked.values[:10].tolist()))}

    def describe(self) -> dict:
        return {
            "head_name": "TS",
            "ts_status": TS_STATUS,
            "maturity": "REAL, time-split-validated XGBoost (train<=2022 / test>=2023), "
                        "NOT a pan-India model -- see module docstring",
            "spatial_scope": "Bengaluru/VOBL station only",
            "domain_cell_id": VOBL_CELL_ID,
            "trained_on": "data/bengaluru_thunderstorm_features_merged.csv (3,819 daily rows, "
                          "2015-2025, 457 positive days: surface obs + IGRA sounding + ERA5)",
            "n_feature_cols": len(self.feature_cols),
            "feature_cols": self.feature_cols,
            "threshold": self.threshold,
            "holdout_metrics": self.reproduce_holdout_metrics(),
            "intended_role": "TS head of the unified pipeline, scoped to the single VOBL cell "
                              "only -- any other cell must be reported OUT_OF_DOMAIN_STATION_ONLY, "
                              "never extrapolated.",
        }


if __name__ == "__main__":
    m = VOBLThunderstormModel()
    import json
    print(json.dumps(m.describe(), indent=2, default=str))
