#!/usr/bin/env python3
"""
scripts/panindia_cb_model_interface.py

Phase 21 -- a clean, minimal inference interface around the trained
pan-India CB model (models/panindia_cb_v1/), designed so a LATER phase can
plug this in as one head of the eventual shared-encoder architecture:

    shared encoder
          |
     +----+----+
    TS   CB    FF
   head  head  head

This file does NOT implement a shared encoder or multi-task training --
doing so would misrepresent this phase's actual deliverable, which is a
single real CB head using engineered tabular features, not a learned
shared representation. What this interface DOES provide is a stable
call signature (`PanIndiaCBModel.predict_proba(features_df)`) that a
future MTL wrapper can call without needing to know this head's internal
feature engineering -- the documented contract is "give me a dataframe
with these exact feature_list.json columns, get back a calibrated
cloudburst probability per row."

2026-10-08 correction: this module IS now the live CB head used by
backend/models/unified_mtl/heads.py's CBHead (which production's
backend/models/unified_mtl/inference_engine.py calls for every CB
prediction in scripts/phase34_build_unified_forecast.py). The
"NOT wired into any production path" claim below is now stale --
left in place only as a dated note of this module's original scope
at creation, not a current status claim. See describe() for the
current, accurate status string.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

ARTIFACT_DIR = Path(__file__).resolve().parent.parent / "models/panindia_cb_v1"


class PanIndiaCBModel:
    """Loads the trained pan-India CB model + calibrator + feature list.
    Call signature intended to be stable across future MTL integration:
    `predict_proba(df)` takes a dataframe with (at least) the columns in
    `self.feature_cols` and returns calibrated P(cloudburst) per row.
    """

    def __init__(self, artifact_name: str = "panindia_cb_v1"):
        self.name = artifact_name
        with open(ARTIFACT_DIR / f"{artifact_name}_feature_list.json") as f:
            self.feature_cols: list[str] = json.load(f)
        # Loaded as a raw Booster rather than the XGBClassifier sklearn
        # wrapper: load_model() on the wrapper does not reliably restore
        # classes_/n_classes_ bookkeeping across xgboost versions, and the
        # Booster'''s own predict() for a binary:logistic objective
        # already returns P(class=1) directly -- no wrapper needed.
        self.model = xgb.Booster()
        self.model.load_model(str(ARTIFACT_DIR / f"{artifact_name}_model.json"))
        # 2026-10-10: real production crash (exit 134, "double free or
        # corruption (!prev)") traced to this exact class of multi-
        # threaded xgboost/OpenBLAS native cleanup on Linux glibc --
        # confirmed still happening in CI via the GitHub Checks
        # annotations API after an earlier, Windows-only-verified fix
        # turned out not to address it. nthread=1 here is defense in
        # depth alongside the workflow's own MALLOC_ARENA_MAX=1 /
        # OMP_NUM_THREADS=1 env vars -- OMP_NUM_THREADS does not always
        # fully constrain XGBoost's own internal thread pool sizing.
        self.model.set_param({"nthread": 1})
        cal_path = ARTIFACT_DIR / f"{artifact_name}_calibrator.pkl"
        self.calibrator = None
        if cal_path.exists():
            with open(cal_path, "rb") as f:
                self.calibrator = pickle.load(f)

    def predict_proba(self, features_df: pd.DataFrame, calibrated: bool = True) -> np.ndarray:
        """features_df must contain every column in self.feature_cols
        (missing columns raise, rather than silently defaulting to NaN/0
        for a feature the caller forgot to compute)."""
        missing = [c for c in self.feature_cols if c not in features_df.columns]
        if missing:
            raise ValueError(f"features_df is missing required columns: {missing}")
        X = features_df[self.feature_cols]
        dmat = xgb.DMatrix(X, missing=np.nan)
        raw_probs = self.model.predict(dmat)
        if calibrated and self.calibrator is not None:
            return self.calibrator.predict(raw_probs)
        return raw_probs

    def predict_contribs(self, features_df: pd.DataFrame, top_n: int = 5) -> list[dict]:
        """2026-10-08 (Phase 11, real per-prediction XAI): returns, for
        each row, a dict in the SAME contract as
        backend/models/unified_mtl/local_xai.py's local_shap_cb (the
        live single-request XAI path, and what index.html's `h.xai`
        rendering actually expects: {status, method, top_contributions:
        [{feature, value, contribution, provenance}]}) -- so the batch
        pipeline's XAI is visible to the same frontend code, not a
        second incompatible shape nothing reads.

        Computed via XGBoost's own built-in pred_contribs=True
        (Shapley values for tree models, exact and additive:
        sum(contribs) + bias == raw margin for that row). Deliberately
        NOT the `shap` package -- same mathematical result for a tree
        model, but avoids adding another native C-extension to the
        exact class of library (xgboost+shap combination) that caused
        the real exit-134 double-free crash this pipeline already hit
        once."""
        missing = [c for c in self.feature_cols if c not in features_df.columns]
        if missing:
            raise ValueError(f"features_df is missing required columns: {missing}")
        X = features_df[self.feature_cols]
        dmat = xgb.DMatrix(X, missing=np.nan)
        contribs = self.model.predict(dmat, pred_contribs=True)  # shape (n_rows, n_features + 1)
        bias = contribs[:, -1]
        feature_contribs = contribs[:, :-1]
        method = "xgboost pred_contribs (exact Shapley values for tree models, local per-prediction)"
        out = []
        for row_idx in range(feature_contribs.shape[0]):
            row = feature_contribs[row_idx]
            order = np.argsort(-np.abs(row))[:top_n]
            out.append({
                "status": "AVAILABLE",
                "method": method,
                "top_contributions": [
                    {"feature": self.feature_cols[j],
                     "value": float(X.iloc[row_idx, j]) if not pd.isna(X.iloc[row_idx, j]) else None,
                     "contribution": float(row[j]), "provenance": "DERIVED"}
                    for j in order
                ],
                "bias_term": float(bias[row_idx]),
            })
        return out

    def describe(self) -> dict:
        """Returns the model's identity/provenance -- intended for an MTL
        wrapper or an audit log to record which head version produced a
        given prediction."""
        return {
            "head_name": "cloudburst (CB)",
            "artifact": self.name,
            "trained_on": "data/external/historical_gfs/phase20_full_predictor_dataset.csv (Phase 20, 10 cycles)",
            "n_feature_cols": len(self.feature_cols),
            "status": "LIVE PRODUCTION MODEL (2026-10-08 correction) -- this is the CB head actually "
                      "called by backend/models/unified_mtl/heads.py:CBHead for every pan-India "
                      "cloudburst prediction in scripts/phase34_build_unified_forecast.py. The daily-"
                      "resolution (not genuinely lead-aware) caveat still applies -- see "
                      "docs/PHASE_21_PANINDIA_CB_MODEL.md -- this status string is about deployment, "
                      "not about lead-time granularity.",
            "intended_role": "one head (CB) of a future shared-encoder TS/CB/FF multi-task architecture; "
                              "this head is currently an independent tabular model, NOT yet connected to a shared encoder",
        }
