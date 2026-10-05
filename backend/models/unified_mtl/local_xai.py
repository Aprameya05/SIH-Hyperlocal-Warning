"""
backend/models/unified_mtl/local_xai.py
===========================================
Phase 35 Part 4: real, per-prediction (local) feature attribution for the
unified inference engine's API responses.

TS/CB: genuine local SHAP values (shap.TreeExplainer) computed on demand
against the SAME persisted booster/classifier heads.py already loads --
never a separately-retrained or approximated model. This is the real
SHAP library, run locally per-request; it is not the pre-computed GLOBAL
mean-|SHAP| importance artifact Phase 21/34 already expose (that remains
available separately as the "global" XAI block).

FF: the PU-logistic model has no valid SHAP story documented in this
repo (TreeExplainer does not apply to a LogisticRegression, and no
LinearExplainer background dataset has ever been validated for this
model). Rather than bolt one on now, FF local XAI is honestly
NOT_AVAILABLE. A real, pre-existing, non-SHAP alternative (coefficient
magnitude) is still exposed separately at the artifact level (Phase 34's
xai.FF block) and is never relabeled as this module's SHAP output.
"""
from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd

XAI_METHOD_SHAP = "SHAP (TreeExplainer, local per-prediction)"


def _top_contributions(feature_names, values_row, shap_row, k: int = 8) -> list:
    pairs = list(zip(feature_names, values_row, shap_row))
    pairs.sort(key=lambda t: -abs(t[2]))
    return [
        {"feature": fname, "value": (float(v) if v is not None and np.isfinite(v) else None),
         "contribution": float(c), "provenance": "DERIVED"}
        for fname, v, c in pairs[:k]
    ]


def local_shap_cb(cb_head, features_df: pd.DataFrame, k: int = 8) -> dict:
    """cb_head: heads.py::CBHead instance (already loaded). features_df:
    exactly one row, the same row passed to cb_head.predict()."""
    try:
        import shap
    except ImportError:
        return {"status": "NOT_AVAILABLE", "reason": "shap library not importable"}
    try:
        cols = cb_head._model.feature_cols
        row = features_df[cols]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            explainer = shap.TreeExplainer(cb_head._model.model)
            sv = explainer.shap_values(row)
        contributions = _top_contributions(cols, row.iloc[0].tolist(), np.asarray(sv)[0], k=k)
        return {"status": "AVAILABLE", "method": XAI_METHOD_SHAP, "top_contributions": contributions}
    except Exception as exc:  # noqa: BLE001
        return {"status": "NOT_AVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}


def local_shap_ts(ts_head, features_df: pd.DataFrame, k: int = 8) -> dict:
    """ts_head: heads.py::TSHead instance (already loaded)."""
    try:
        import shap
    except ImportError:
        return {"status": "NOT_AVAILABLE", "reason": "shap library not importable"}
    try:
        cols = ts_head.feature_cols
        row = features_df[cols]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            explainer = shap.TreeExplainer(ts_head._model.model)
            sv = explainer.shap_values(row)
        contributions = _top_contributions(cols, row.iloc[0].tolist(), np.asarray(sv)[0], k=k)
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
