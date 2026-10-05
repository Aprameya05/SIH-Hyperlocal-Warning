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

This module is NOT wired into backend/pipeline.py or any production path.
It is research/historical-model code only.
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

    def describe(self) -> dict:
        """Returns the model's identity/provenance -- intended for an MTL
        wrapper or an audit log to record which head version produced a
        given prediction."""
        return {
            "head_name": "cloudburst (CB)",
            "artifact": self.name,
            "trained_on": "data/external/historical_gfs/phase20_full_predictor_dataset.csv (Phase 20, 10 cycles)",
            "n_feature_cols": len(self.feature_cols),
            "status": "HISTORICAL/RESEARCH MODEL -- not deployed to production inference",
            "intended_role": "one head (CB) of a future shared-encoder TS/CB/FF multi-task architecture; "
                              "this head is currently an independent tabular model, NOT yet connected to a shared encoder",
        }
