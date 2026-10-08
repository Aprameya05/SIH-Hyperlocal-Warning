"""
backend/models/unified_mtl/local_xai.py
===========================================
Phase 35 Part 4: real, per-prediction (local) feature attribution for the
unified inference engine's API responses.

TS/CB: genuine local Shapley-value feature attribution, computed on
demand against the SAME persisted booster/classifier heads.py already
loads -- never a separately-retrained or approximated model. It is not
the pre-computed GLOBAL mean-|SHAP| importance artifact Phase 21/34
already expose (that remains available separately as the "global" XAI
block).

2026-10-08: switched from shap.TreeExplainer to XGBoost's own built-in
pred_contribs=True. This is the SAME mathematical result (exact
Shapley values for a tree model -- verified in
tests/test_phase50_cb_real_per_prediction_xai.py by checking
sum(contributions) + bias == the model's real raw margin), but this
module runs INSIDE the live API server process on every request that
asks for local XAI -- unlike the one-shot batch pipeline in
scripts/phase34_build_unified_forecast.py, this process cannot safely
call os._exit(0) after a crash, because it needs to keep serving
requests. Importing `shap` here combines with xgboost in the exact
native-C-extension way that caused this pipeline's real exit-134
double-free crash (see that script's __main__ guard) -- a crash in
THIS process would take the whole API server down, not just one
batch job. XGBoost's own pred_contribs avoids the `shap` package
entirely, closing that risk for the live server without changing the
output contract below.

FF: the PU-logistic model has no valid SHAP story documented in this
repo (TreeExplainer does not apply to a LogisticRegression, and no
LinearExplainer background dataset has ever been validated for this
model). Rather than bolt one on now, FF local XAI is honestly
NOT_AVAILABLE. A real, pre-existing, non-SHAP alternative (coefficient
magnitude) is still exposed separately at the artifact level (Phase 34's
xai.FF block) and is never relabeled as this module's SHAP output.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

XAI_METHOD_SHAP = "xgboost pred_contribs (exact Shapley values for tree models, local per-prediction)"


def _top_contributions(feature_names, values_row, shap_row, k: int = 8) -> list:
    pairs = list(zip(feature_names, values_row, shap_row))
    pairs.sort(key=lambda t: -abs(t[2]))
    return [
        {"feature": fname, "value": (float(v) if v is not None and np.isfinite(v) else None),
         "contribution": float(c), "provenance": "DERIVED"}
        for fname, v, c in pairs[:k]
    ]


def _booster_pred_contribs(booster, row: pd.DataFrame):
    """row: exactly one row. Returns (feature_contribs_array, bias) for
    that row using XGBoost's own pred_contribs=True -- no `shap`
    package import, see module docstring for why that matters here."""
    import xgboost as xgb
    dmat = xgb.DMatrix(row, missing=np.nan)
    contribs = booster.predict(dmat, pred_contribs=True)[0]
    return contribs[:-1], contribs[-1]


def local_shap_cb(cb_head, features_df: pd.DataFrame, k: int = 8) -> dict:
    """cb_head: heads.py::CBHead instance (already loaded). features_df:
    exactly one row, the same row passed to cb_head.predict()."""
    try:
        cols = cb_head._model.feature_cols
        row = features_df[cols]
        feature_contribs, _bias = _booster_pred_contribs(cb_head._model.model, row)
        contributions = _top_contributions(cols, row.iloc[0].tolist(), feature_contribs, k=k)
        return {"status": "AVAILABLE", "method": XAI_METHOD_SHAP, "top_contributions": contributions}
    except Exception as exc:  # noqa: BLE001
        return {"status": "NOT_AVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}


def local_shap_ts(ts_head, features_df: pd.DataFrame, k: int = 8) -> dict:
    """ts_head: heads.py::TSHead instance (already loaded)."""
    try:
        cols = ts_head.feature_cols
        row = features_df[cols]
        booster = ts_head._model.model.get_booster()
        feature_contribs, _bias = _booster_pred_contribs(booster, row)
        contributions = _top_contributions(cols, row.iloc[0].tolist(), feature_contribs, k=k)
        return {"status": "AVAILABLE", "method": XAI_METHOD_SHAP, "top_contributions": contributions}
    except Exception as exc:  # noqa: BLE001
        return {"status": "NOT_AVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}


def local_xai_ff(*_args, **_kwargs) -> dict:
    """Always NOT_AVAILABLE -- see module docstring. Accepts the same
    call shape as the other two for a uniform call site, but never
    computes or fabricates a local attribution for the FF PU model."""
    return {"status": "NOT_AVAILABLE",
            "reason": "no valid local SHAP/linear-attribution story is documented for the FF "
                      "PU-logistic model in this repo; see the artifact-level coefficient-magnitude "
                      "XAI block (Phase 34) for a real, non-local, non-SHAP alternative"}


__all__ = ["local_shap_cb", "local_shap_ts", "local_xai_ff", "XAI_METHOD_SHAP"]
