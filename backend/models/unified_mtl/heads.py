"""
backend/models/unified_mtl/heads.py
======================================
Phase 22 Track 4 -- the three independently-trained heads, wrapped
behind a common `BaseHead` interface (predict(features_df) -> probs,
describe() -> status dict) so UnifiedMTLModel can call them uniformly
today and swap in shared-embedding inputs later without changing this
contract.

Each head wraps a REAL, pre-existing model artifact. None are retrained
here. Each describe() states its true training status (real,
LODO-validated / research-only, tiny sample / PU-ranking, not a
confirmed-negative classifier) -- never upgraded to look more mature
than it is.
"""
from __future__ import annotations

import json
import pickle
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


class BaseHead:
    head_name: str = "base"

    def predict(self, features_df: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    def describe(self) -> dict:
        raise NotImplementedError


class CBHead(BaseHead):
    """Cloudburst head -- wraps the Phase 21 PanIndiaCBModel. Real,
    LODO-validated XGBoost model. See docs/PHASE_21_PANINDIA_CB_MODEL.md."""

    head_name = "CB"

    def __init__(self, artifact_name: str = "panindia_cb_v1"):
        from panindia_cb_model_interface import PanIndiaCBModel  # noqa: E402
        self._model = PanIndiaCBModel(artifact_name=artifact_name)

    def predict(self, features_df: pd.DataFrame, calibrated: bool = True) -> np.ndarray:
        return self._model.predict_proba(features_df, calibrated=calibrated)

    def describe(self) -> dict:
        d = self._model.describe()
        d["head_name"] = self.head_name
        d["maturity"] = "REAL_VALIDATED (LODO cross-validated, see PHASE_21 doc)"
        return d


class TSHead(BaseHead):
    """Thunderstorm head -- Phase 25 Priority 1: now wraps the REAL,
    persisted, time-split-validated VOBLThunderstormModel
    (scripts/ts_station_model_interface.py -> models/thunderstorm_model.pkl),
    not the unpersisted 28-row Phase G research baseline this head used
    to point at (that result is still in docs/PHASE_G_TS_BASELINE_RESULT.json
    for the record, but is superseded here by a model with a real,
    reproducible held-out evaluation on 1,001 test days -- see
    reproduce_holdout_metrics() in the wrapped class).

    TS_STATUS = STATION_BASELINE: this model is trained ONLY on
    Bengaluru/VOBL station data. It is NOT a pan-India model.
    predict() only accepts rows for the single VOBL cell
    (ts_station_model_interface.VOBL_CELL_ID); any other cell raises
    rather than silently extrapolating a station model pan-India."""

    head_name = "TS"

    def __init__(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from ts_station_model_interface import VOBLThunderstormModel, TS_STATUS, VOBL_CELL_ID
        self._model = VOBLThunderstormModel()
        self._status = TS_STATUS
        self._domain_cell_id = VOBL_CELL_ID
        self.feature_cols = self._model.feature_cols

    def predict(self, features_df: pd.DataFrame, cell_ids: list | None = None) -> np.ndarray:
        """If cell_ids is given, every entry must equal the VOBL domain
        cell -- this head refuses to extrapolate a station model to
        other cells rather than silently returning a number for them."""
        if cell_ids is not None:
            bad = [c for c in cell_ids if c != self._domain_cell_id]
            if bad:
                raise ValueError(
                    f"TSHead is STATION_BASELINE (VOBL only, cell_id={self._domain_cell_id}); "
                    f"refusing to predict for out-of-domain cell_ids: {sorted(set(bad))[:5]}"
                )
        return self._model.predict_proba(features_df)

    def describe(self) -> dict:
        d = self._model.describe()
        d["head_name"] = self.head_name
        d["ts_status"] = self._status
        return d


class FFHead(BaseHead):
    """Flash-flood head -- wraps the Phase 5.7 PU-corrected logistic
    model (model_c_combined, the only PU model artifact actually
    persisted to disk). PU-ranking model: predictions are NOT confirmed
    P(flood) since no confirmed negatives exist. See
    docs/PHASE_22_FLASH_FLOOD_MODEL.md."""

    head_name = "FF"
    MODEL_PKL = REPO_ROOT / "processed" / "ff_pu" / "RESEARCH_ONLY_model_c_logistic.pkl"
    METADATA_JSON = REPO_ROOT / "processed" / "ff_pu" / "model_metadata.json"
    VALIDATION_JSON = REPO_ROOT / "processed" / "ff_pu" / "validation_results.json"

    def __init__(self):
        if not self.MODEL_PKL.exists():
            raise FileNotFoundError(f"{self.MODEL_PKL} missing")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with open(self.MODEL_PKL, "rb") as f:
                bundle = pickle.load(f)
        self._model = bundle["model"]
        self._scaler = bundle["scaler"]
        with open(self.METADATA_JSON) as f:
            self.feature_cols = json.load(f)["model_c_combined"]["feature_columns"]
        with open(self.VALIDATION_JSON) as f:
            val = json.load(f)
        self._c_estimate = val["model_c_combined"]["logistic_pu"]["elkan_noto_c_estimate"]
        self._validation = val["model_c_combined"]["logistic_pu"]["validation"]

    def predict(self, features_df: pd.DataFrame, pu_corrected: bool = True) -> np.ndarray:
        missing = [c for c in self.feature_cols if c not in features_df.columns]
        if missing:
            raise ValueError(f"features_df is missing required columns: {missing}")
        X = features_df[self.feature_cols].apply(pd.to_numeric, errors="coerce")
        X = X.fillna(X.median())
        X_scaled = self._scaler.transform(X)
        # Compute sigmoid(X @ coef_.T + intercept_) directly from the
        # fitted coefficients rather than calling predict_proba(): the
        # persisted pickle was fit under a newer scikit-learn than this
        # environment's (InconsistentVersionWarning at load time), and
        # LogisticRegression.predict_proba() in this older version
        # dereferences a `multi_class` attribute the newer version no
        # longer sets, raising AttributeError. The decision function
        # itself (coef_/intercept_) is stable across these versions and
        # gives numerically identical probabilities for a binary model.
        z = X_scaled @ self._model.coef_[0] + self._model.intercept_[0]
        s = 1.0 / (1.0 + np.exp(-z))
        if pu_corrected:
            return np.clip(s / self._c_estimate, 0.0, 1.0)
        return s

    def describe(self) -> dict:
        return {
            "head_name": self.head_name,
            "maturity": "PU_RANKING_MODEL (Elkan-Noto/SCAR corrected; NOT a confirmed-negative "
                        "classifier -- no confirmed negatives exist for flash-flood in this data)",
            "model_type": "LogisticRegression (PU, combined catchment+rainfall features)",
            "elkan_noto_c_estimate": self._c_estimate,
            "pu_ranking_auc_caveated": self._validation.get("pu_ranking_auc_caveated"),
            "feature_cols": self.feature_cols,
            "n_feature_cols": len(self.feature_cols),
            "label_taxonomy": "see docs/PHASE_22_FLASH_FLOOD_MODEL.md -- OBSERVED_FLOOD / "
                               "POSITIVE_UNLABELED / UNKNOWN / PROXY(unused)",
            "intended_role": "flash-flood (FF) head of the future shared-encoder MTL architecture; "
                              "currently an independent PU-ranking model, not yet connected to a "
                              "shared encoder",
        }
