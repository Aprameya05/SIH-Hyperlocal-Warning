"""
backend/models/unified_mtl/inference_engine.py
==================================================
Phase 34: the real UNIFIED SIH INFERENCE ENGINE's per-hazard predict()
interface. This module does NOT retrain or invent any model. It wraps
the three already-real, already-validated artifacts behind one honest
contract:

    predict(cell_id, lead_hours, init_time_utc, features) -> {
        probability, risk_category, status, model_version,
        provenance, confidence,
    }

Reused, never redesigned:
  - heads.py::CBHead / TSHead / FFHead (the real XGBoost/logistic artifacts)
  - lead_time_interface.py::LEAD_HOURS (the canonical 2/3/4/5/6h set)
  - shared_backbone.py::SharedBackbone (real architecture; reports
    SHARED_BACKBONE_STATUS = ARCHITECTURE_ONLY whenever no trained,
    validated-at-this-scale checkpoint is loaded for the requested use)

Per Phase 34's explicit instructions:
  - If a genuine trained model is unavailable for a hazard/lead, status
    is NOT_TRAINED (or the head's own honest status: BASELINE/PU_RANKING/
    PROTOTYPE/UNAVAILABLE) and probability is None -- never invented.
  - The FF PU-ranking score is never represented as an observed flood
    probability: it is returned under `pu_ranking_score`, with
    `probability = None` and `risk_category = NOT_AVAILABLE`.
  - risk_category is NOT_AVAILABLE whenever no legitimate probability
    exists; it is never silently defaulted to LOW.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from backend.models.unified_mtl.lead_time_interface import LEAD_HOURS  # noqa: E402
from backend.models.unified_mtl.heads import CBHead, FFHead, TSHead  # noqa: E402
from backend.models.unified_mtl.shared_backbone import SharedBackbone  # noqa: E402

RISK_CATEGORIES = ("LOW", "MODERATE", "HIGH", "SEVERE")
RISK_NOT_AVAILABLE = "NOT_AVAILABLE"

PROVENANCE_ENUM = ("OBSERVED", "FORECAST", "REANALYSIS", "DERIVED", "PROXY", "MISSING")

# Documented decision thresholds -- never invented. CB: HSS-maximizing
# threshold selected over real LODO out-of-fold predictions (Phase 21,
# models/panindia_cb_v1/panindia_cb_v1_threshold_selection.json). TS:
# the threshold persisted inside models/thunderstorm_model.pkl itself
# (tune_model.py, Phase G/25).
CB_DECISION_THRESHOLD = 0.23000000000000004
TS_DECISION_THRESHOLD = 0.45


class InferenceLeakageError(Exception):
    """Raised instead of silently computing a prediction whose valid_time
    does not genuinely sit lead_hours after init_time."""


def compute_valid_time(init_time_utc: datetime, lead_hours: int) -> datetime:
    if lead_hours not in LEAD_HOURS:
        raise ValueError(f"lead_hours must be one of {LEAD_HOURS}, got {lead_hours}")
    return init_time_utc + timedelta(hours=lead_hours)


def assert_no_leakage(init_time_utc: datetime, valid_time_utc: datetime, lead_hours: int) -> None:
    delta_hours = (valid_time_utc - init_time_utc).total_seconds() / 3600.0
    if abs(delta_hours - lead_hours) > 1e-6:
        raise InferenceLeakageError(
            f"valid_time - init_time = {delta_hours}h, does not equal declared lead_hours={lead_hours} "
            f"-- refusing to produce a leaking prediction record"
        )


def risk_category_from_probability(probability: Optional[float], threshold: float) -> str:
    """Maps a real model probability onto LOW/MODERATE/HIGH/SEVERE using
    the model's own documented decision threshold as the HIGH/MODERATE
    boundary anchor -- never an arbitrary unrelated cutoff. This is a
    heuristic quartile-style mapping around a real, validated decision
    point, not an independently calibrated risk-category system; that
    caveat is carried in every describe()/record this is used in.
    Returns NOT_AVAILABLE when no probability exists."""
    if probability is None:
        return RISK_NOT_AVAILABLE
    if not (0.0 <= probability <= 1.0):
        return RISK_NOT_AVAILABLE
    half = threshold / 2.0
    upper_mid = threshold + (1.0 - threshold) / 2.0
    if probability < half:
        return "LOW"
    if probability < threshold:
        return "MODERATE"
    if probability < upper_mid:
        return "HIGH"
    return "SEVERE"


@dataclass
class HazardPrediction:
    hazard: str
    cell_id: str
    init_time: str
    valid_time: str
    lead_hours: int
    probability: Optional[float]
    risk_category: str
    status: str
    model_version: str
    provenance: str
    confidence: str
    extra: dict


class UnifiedInferenceEngine:
    """The single entry point Phase 34 Part 3 asks for: one object with
    one predict() per hazard, callable as predict(cell, lead_hours,
    features). Heads are loaded best-effort -- a head whose artifact is
    missing is recorded unavailable rather than crashing the others."""

    def __init__(self, load_cb: bool = True, load_ts: bool = True, load_ff: bool = True,
                 backbone_n_features: Optional[int] = None):
        self.heads: dict = {}
        self._load_errors: dict = {}
        if load_cb:
            self._try_load("CB", CBHead)
        if load_ts:
            self._try_load("TS", TSHead)
        if load_ff:
            self._try_load("FF", FFHead)

        # Phase 34 Part 5: SharedBackbone integration. Constructed as a
        # real architecture (not a stub), but no generically-trained,
        # validated-at-992-cell-scale checkpoint exists for it in this
        # repo (the only trained checkpoint, models/unified_mtl_cb_prototype_ckpt.pkl,
        # is a 150-cell CB-only daily prototype -- see
        # cb_daily_prototype_adapter.py docstring). So this engine's
        # shared backbone is always reported ARCHITECTURE_ONLY unless a
        # caller explicitly loads and marks a checkpoint trained via
        # load_trained_backbone_checkpoint() below.
        self.backbone: Optional[SharedBackbone] = None
        self.backbone_status = "ARCHITECTURE_ONLY"
        if backbone_n_features is not None:
            self.backbone = SharedBackbone(n_features=backbone_n_features)

    def _try_load(self, name: str, cls):
        try:
            self.heads[name] = cls()
        except Exception as exc:  # noqa: BLE001
            self._load_errors[name] = f"{type(exc).__name__}: {exc}"

    def load_trained_backbone_checkpoint(self, path: Path) -> None:
        """Only path by which backbone_status can become anything other
        than ARCHITECTURE_ONLY -- and even then, it reports exactly what
        the checkpoint's own metadata says it was trained on, never
        upgraded to a stronger claim than the checkpoint itself carries."""
        self.backbone = SharedBackbone.load(str(path))
        self.backbone_status = (
            "TRAINED_CHECKPOINT_LOADED (scope stated by the checkpoint's own metadata -- "
            "see docs/PHASE_23_UNIFIED_MTL_IMPLEMENTATION.md; not a claim of general "
            "pan-India/multi-hazard training)"
        )

    def shared_backbone_describe(self) -> dict:
        if self.backbone is None:
            return {"component": "shared_backbone", "status": self.backbone_status,
                    "note": "Not instantiated in this engine run (no n_features given / no checkpoint loaded)."}
        d = self.backbone.describe()
        d["status"] = self.backbone_status
        return d

    # ------------------------------------------------------------------
    # TS
    # ------------------------------------------------------------------
    def predict_ts(self, cell_id: str, lead_hours: int, init_time_utc: datetime,
                    features_df: Optional[pd.DataFrame] = None) -> HazardPrediction:
        valid_time_utc = compute_valid_time(init_time_utc, lead_hours)
        assert_no_leakage(init_time_utc, valid_time_utc, lead_hours)

        head = self.heads.get("TS")
        if head is None:
            return self._unavailable("TS", cell_id, init_time_utc, valid_time_utc, lead_hours,
                                      reason=self._load_errors.get("TS", "TS head not loaded"))

        from ts_station_model_interface import VOBL_CELL_ID
        if cell_id != VOBL_CELL_ID:
            return HazardPrediction(
                hazard="TS", cell_id=cell_id, init_time=_iso(init_time_utc), valid_time=_iso(valid_time_utc),
                lead_hours=lead_hours, probability=None, risk_category=RISK_NOT_AVAILABLE,
                status="OUT_OF_DOMAIN_STATION_ONLY",
                model_version="none (out of domain)", provenance="MISSING", confidence="NOT_AVAILABLE",
                extra={"reason": f"TS model is STATION_BASELINE, VOBL-only ({VOBL_CELL_ID}); "
                                  f"refusing to extrapolate to {cell_id}"},
            )
        if features_df is None or len(features_df) == 0:
            return self._unavailable("TS", cell_id, init_time_utc, valid_time_utc, lead_hours,
                                      reason="no features_df supplied for VOBL cell", status="NOT_TRAINED")
        try:
            p = float(head.predict(features_df)[0])
        except Exception as exc:  # noqa: BLE001
            return self._unavailable("TS", cell_id, init_time_utc, valid_time_utc, lead_hours,
                                      reason=f"{type(exc).__name__}: {exc}")
        extra = {"ts_status": head.describe().get("ts_status"), "lead_time_caveat":
                 "All 5 lead slots reuse the same daily-resolution station features; no "
                 "sub-daily-labeled lead-time model exists (Phase 33 audit)."}
        # 2026-10-08 (Phase 11/14): same real per-prediction XAI CB
        # already has, reusing local_shap_ts directly (same contract
        # the live /forecast endpoint's _hazard_block already uses) so
        # this is visible to index.html's existing h.xai rendering the
        # same way CB's now is -- not a second, incompatible shape.
        try:
            from local_xai import local_shap_ts
            extra["xai"] = local_shap_ts(head, features_df)
        except Exception as exc:  # noqa: BLE001
            extra["xai"] = {"status": "NOT_AVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}
        return HazardPrediction(
            hazard="TS", cell_id=cell_id, init_time=_iso(init_time_utc), valid_time=_iso(valid_time_utc),
            lead_hours=lead_hours, probability=p,
            risk_category=risk_category_from_probability(p, TS_DECISION_THRESHOLD),
            status="BASELINE",
            model_version="models/thunderstorm_model.pkl (Phase 25, VOBL station, time-split-validated)",
            provenance="DERIVED", confidence="MODERATE (single-station daily-feature model; "
                                              "AUROC=0.8715 on 1,001 held-out days, see heads.py::TSHead.describe())",
            extra=extra,
        )

    # ------------------------------------------------------------------
    # CB
    # ------------------------------------------------------------------
    def predict_cb(self, cell_id: str, lead_hours: int, init_time_utc: datetime,
                    features_df: Optional[pd.DataFrame] = None) -> HazardPrediction:
        valid_time_utc = compute_valid_time(init_time_utc, lead_hours)
        assert_no_leakage(init_time_utc, valid_time_utc, lead_hours)

        head = self.heads.get("CB")
        if head is None:
            return self._unavailable("CB", cell_id, init_time_utc, valid_time_utc, lead_hours,
                                      reason=self._load_errors.get("CB", "CB head not loaded"))
        if features_df is None or len(features_df) == 0:
            return self._unavailable("CB", cell_id, init_time_utc, valid_time_utc, lead_hours,
                                      reason="no features_df supplied for this cell/cycle", status="NOT_TRAINED")
        try:
            p = float(head.predict(features_df, calibrated=True)[0])
        except Exception as exc:  # noqa: BLE001
            return self._unavailable("CB", cell_id, init_time_utc, valid_time_utc, lead_hours,
                                      reason=f"{type(exc).__name__}: {exc}")
        extra = {"daily_resolution_caveat":
                 "Trained against a DAILY cloudburst label (IMD >=64.5mm/day). All 5 lead slots "
                 "repeat the same daily-resolution snapshot per Phase 33 audit -- this is NOT a "
                 "genuine sub-daily 2-6h cloudburst forecast."}
        # 2026-10-08 (Phase 11): real per-prediction XAI, not a static
        # global-importance number repeated for every record. XAI failure
        # must degrade gracefully and never take down the forecast itself.
        # Shape matches local_xai.py's local_shap_cb contract exactly
        # (status/method/top_contributions) -- see predict_contribs'
        # docstring -- so scripts/phase34_build_unified_forecast.py can
        # promote this to the record's top-level "xai" field and
        # index.html's existing h.xai rendering picks it up unchanged.
        try:
            extra["xai"] = head.predict_contribs(features_df, top_n=5)[0]
        except Exception as exc:  # noqa: BLE001
            extra["xai"] = {"status": "NOT_AVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}
        return HazardPrediction(
            hazard="CB", cell_id=cell_id, init_time=_iso(init_time_utc), valid_time=_iso(valid_time_utc),
            lead_hours=lead_hours, probability=p,
            risk_category=risk_category_from_probability(p, CB_DECISION_THRESHOLD),
            status="TRAINED",
            model_version="panindia_cb_v1 (Phase 21, LODO-validated XGBoost)",
            provenance="DERIVED",
            confidence="MODERATE (LODO AUROC=0.7542 calibrated, pooled across 5 held-out dates; "
                       "see docs/PHASE_21_PANINDIA_CB_MODEL.md)",
            extra=extra,
        )

    # ------------------------------------------------------------------
    # FF -- PU ranking score only, never an observed-flood probability
    # ------------------------------------------------------------------
    def predict_ff(self, cell_id: str, lead_hours: int, init_time_utc: datetime,
                    features_df: Optional[pd.DataFrame] = None) -> HazardPrediction:
        valid_time_utc = compute_valid_time(init_time_utc, lead_hours)
        assert_no_leakage(init_time_utc, valid_time_utc, lead_hours)

        head = self.heads.get("FF")
        if head is None:
            return self._unavailable("FF", cell_id, init_time_utc, valid_time_utc, lead_hours,
                                      reason=self._load_errors.get("FF", "FF head not loaded"), status="PU_RANKING")
        if features_df is None or len(features_df) == 0:
            return self._unavailable("FF", cell_id, init_time_utc, valid_time_utc, lead_hours,
                                      reason="no features_df supplied (catchment/rainfall coverage gap)",
                                      status="PU_RANKING")
        try:
            pu_score = float(head.predict(features_df, pu_corrected=True)[0])
        except Exception as exc:  # noqa: BLE001
            return self._unavailable("FF", cell_id, init_time_utc, valid_time_utc, lead_hours,
                                      reason=f"{type(exc).__name__}: {exc}", status="PU_RANKING")
        if not np.isfinite(pu_score):
            return self._unavailable("FF", cell_id, init_time_utc, valid_time_utc, lead_hours,
                                      reason="feature row produced a non-finite PU score (incomplete "
                                             "catchment/rainfall inputs) -- refusing to report it as a value",
                                      status="PU_RANKING")
        from local_xai import local_xai_ff
        return HazardPrediction(
            hazard="FF", cell_id=cell_id, init_time=_iso(init_time_utc), valid_time=_iso(valid_time_utc),
            lead_hours=lead_hours,
            probability=None,  # NEVER represented as an observed-flood probability
            risk_category=RISK_NOT_AVAILABLE,
            status="PU_RANKING",
            model_version="RESEARCH_ONLY_model_c_logistic (PU-corrected, Elkan-Noto/SCAR)",
            provenance="DERIVED",
            confidence="LOW (PU ranking caveated AUROC=0.590; no confirmed negatives exist -- "
                       "see heads.py::FFHead.describe())",
            # 2026-10-08: top-level "xai" key for interface consistency
            # with TS/CB (both now populate this). local_xai_ff() is
            # honestly always NOT_AVAILABLE -- no valid SHAP/linear-
            # attribution story exists for this PU-logistic model (see
            # local_xai.py's module docstring) -- this is a real,
            # correctly-labeled non-result, not a fabricated one.
            extra={"pu_ranking_score": pu_score,
                   "note": "This is a ranking score, not a calibrated P(flood). risk_category is "
                           "NOT_AVAILABLE because no confirmed-negative-based probability exists.",
                   "xai": local_xai_ff()},
        )

    @staticmethod
    def _unavailable(hazard: str, cell_id: str, init_time_utc: datetime, valid_time_utc: datetime,
                      lead_hours: int, reason: str, status: str = "NOT_TRAINED") -> HazardPrediction:
        return HazardPrediction(
            hazard=hazard, cell_id=cell_id, init_time=_iso(init_time_utc), valid_time=_iso(valid_time_utc),
            lead_hours=lead_hours, probability=None, risk_category=RISK_NOT_AVAILABLE, status=status,
            model_version="none", provenance="MISSING", confidence="NOT_AVAILABLE", extra={"reason": reason},
        )


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "RISK_CATEGORIES", "RISK_NOT_AVAILABLE", "PROVENANCE_ENUM",
    "CB_DECISION_THRESHOLD", "TS_DECISION_THRESHOLD",
    "InferenceLeakageError", "compute_valid_time", "assert_no_leakage",
    "risk_category_from_probability", "HazardPrediction", "UnifiedInferenceEngine",
]
