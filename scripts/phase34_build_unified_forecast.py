"""
scripts/phase34_build_unified_forecast.py
=============================================
Phase 34: builds the real UNIFIED SIH INFERENCE ENGINE output artifact,
data/unified_forecast.json, for all 992 canonical cells x the 5 canonical
lead hours (2,3,4,5,6h) = 4960 records.

Reuses, never re-invents:
  - backend/models/unified_mtl/inference_engine.py::UnifiedInferenceEngine
    (Phase 34's predict() contract over the real CB/TS/FF heads)
  - scripts/panindia_cb_features.py (real CB feature engineering)
  - scripts/ff_feature_adapter.py (real FF rainfall+catchment feature table)
  - scripts/ts_station_model_interface.py (real VOBL TS features)
  - data/pan_india_terrain_992.json (real/blocked SRTM DEM + real/missing
    INDOFLOODS catchment hydrology, Phase 26/27)
  - data/pan_india_common_grid_992.json (canonical cell_id/lat/lon order)

No model is trained here. No production file is modified. This script
only reads existing real artifacts and writes data/unified_forecast.json.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from backend.models.unified_mtl.inference_engine import (  # noqa: E402
    UnifiedInferenceEngine, compute_valid_time, assert_no_leakage,
)
from backend.models.unified_mtl.lead_time_interface import LEAD_HOURS  # noqa: E402

OUTPUT_PATH = REPO_ROOT / "data" / "unified_forecast.json"
SOURCE_CYCLE_DATE = "2024-08-01"  # the one real, Phase-32/32B-validated IMERG day; also present in
                                   # the Phase 20 GFS predictor dataset and used as FF's target_date.
INIT_TIME_UTC = datetime(2024, 8, 1, 0, 0, tzinfo=timezone.utc)

# GFS predictor list actually exposed by the Phase 19/20/21 extraction
# pipeline this engine's CB head is trained on (CORE_NUMERIC_COLS +
# panindia_cb_v1's own 35 engineered columns). Provenance is FORECAST
# because these are GFS model-cycle forecast fields, not observations.
GFS_PREDICTORS = [
    "cape_sfc_jkg", "cin_sfc_jkg", "t2m_k", "rh2m_pct", "q2m_kgkg", "td2m_k",
    "sp_pa", "mslp_pa", "pwat_mm", "u850_ms", "v850_ms", "u500_ms", "v500_ms",
    "u200_ms", "v200_ms", "gh850_gpm", "gh500_gpm", "gh200_gpm",
    "target_day_precip_tp_mm", "target_day_precip_acpcp_mm",
    "shear_mag_850_200_ms",
]
# Himawari: only B13 IR is actually fetched/wired anywhere in this repo
# (see scripts referenced by test_phase7_himawari_b08.py / Phase 4/6/7
# docs) -- B08/WV is explicitly NOT claimed.
HIMAWARI_VARIABLES = ["himawari_b13_ir_brightness_temp_k"]
# METAR: VOBL/VOBG only (Bengaluru), per scripts/metar_ts_label_parser.py.
METAR_SCOPE_CELLS = ["IND_13.0_77.0"]


def build_feature_provenance_block(n_cells_terrain_real: int, n_cells_terrain_blocked: int,
                                    n_cells_hydrology_real: int, n_cells_hydrology_missing: int) -> dict:
    return {
        "GFS": {"provenance": "FORECAST", "variables": GFS_PREDICTORS,
                "coverage": "all 992 cells, 1 cycle (2024-08-01), Phase 19/20 extraction"},
        "IMERG": {"provenance": "OBSERVED", "product": "GPM_3IMERGHH", "version": "V07B", "run": "Final",
                   "temporal_resolution": "30min", "spatial_resolution": "0.1deg",
                   "coverage": "1 real validated day (2024-08-01, Phase 32/32B); "
                               "FULL_ARCHIVE_NOT_ACQUIRED, AUTOMATED_IMERG_ACQUISITION_BLOCKED (unchanged)",
                   "role": "observed-precipitation input evidence only -- never an automatic CB label "
                           "(Phase 33/34 constraint)"},
        "Himawari": {"provenance": "OBSERVED", "variables": HIMAWARI_VARIABLES,
                     "coverage": "B13 IR only -- B08/WV is NOT implemented in this repo and is not claimed"},
        "METAR": {"provenance": "OBSERVED", "scope_cells": METAR_SCOPE_CELLS,
                  "coverage": "VOBL/VOBG station only; MISSING (never 0) for all other cells"},
        "terrain": {"provenance": "DERIVED", "source": "AWS Terrarium SRTM tiles (Phase 26)",
                    "n_cells_real": n_cells_terrain_real, "n_cells_blocked": n_cells_terrain_blocked,
                    "note": "live DEM acquisition for the remaining cells is network-blocked "
                            "(403 on S3 egress); no elevation/slope value is fabricated for them"},
        "hydrology": {"provenance": "OBSERVED", "source": "INDOFLOODS gauge catchment characteristics (Phase 27)",
                      "n_cells_real": n_cells_hydrology_real, "n_cells_missing": n_cells_hydrology_missing,
                      "note": "gauge-sparse by nature; never interpolated to ungauged cells"},
    }


def main():
    t0 = time.time()
    warnings.filterwarnings("ignore")

    print("[1/7] Loading canonical grid + terrain...")
    with open(REPO_ROOT / "data" / "pan_india_common_grid_992.json") as f:
        common_grid = json.load(f)
    cells = common_grid["cells"]
    assert len(cells) == 992

    with open(REPO_ROOT / "data" / "pan_india_terrain_992.json") as f:
        terrain_doc = json.load(f)
    terrain_by_cell = {c["cell_id"]: c for c in terrain_doc["cells"]}
    n_terrain_real = sum(1 for c in terrain_doc["cells"] if c["terrain_status"] == "REAL_SRTM_PANINDIA")
    n_terrain_blocked = sum(1 for c in terrain_doc["cells"] if c["terrain_status"] == "BLOCKED_NETWORK_SRTM")
    n_hydro_real = sum(1 for c in terrain_doc["cells"] if c["catchment_status"] == "OBSERVED_SPARSE")
    n_hydro_missing = sum(1 for c in terrain_doc["cells"] if c["catchment_status"] == "MISSING_NO_GAUGE")

    print("[2/7] Loading CB engine features (Phase 20 GFS dataset, cycle 2024-08-01)...")
    from panindia_cb_features import engineer_daily_features
    raw_df = pd.read_csv(REPO_ROOT / "data" / "external" / "historical_gfs" / "phase20_full_predictor_dataset.csv")
    cycle_rows = raw_df[raw_df["target_date"] == SOURCE_CYCLE_DATE].copy()
    cb_engineered = engineer_daily_features(cycle_rows, include_prate=False, include_9000pa=False)

    print("[3/7] Loading FF engine features (real IMD rainfall + INDOFLOODS catchment, cycle 2024-08-01)...")
    from ff_feature_adapter import build_ff_input_table_for_date
    ff_result = build_ff_input_table_for_date(SOURCE_CYCLE_DATE)
    ff_table = ff_result["feature_table"]
    ff_ready_ids = set(ff_result.get("ready_cell_ids_full") or ff_result.get("ready_cell_ids") or [])
    # Only cells in ready_cell_ids_full have a genuinely complete feature
    # row (real rainfall AND real catchment hydrology, no NaNs). The wider
    # feature_table (386 rows) includes cells with real rainfall but
    # catchment columns entirely NaN (MISSING_NO_GAUGE, 917/992 cells) --
    # feeding those into FFHead.predict() would hit its single-row
    # X.fillna(X.median()) imputation, which is a no-op on an all-NaN
    # column and silently produces a NaN "score" masquerading as a real
    # PU ranking value. Restricting to ready_cell_ids_full avoids ever
    # computing a score for a cell with no real catchment data at all.
    ff_table_by_cell = ({cid: ff_table[ff_table["cell_id"] == cid].iloc[[0]] for cid in ff_ready_ids}
                         if "cell_id" in ff_table.columns and len(ff_table) else {})

    print("[4/7] Loading TS engine features (real VOBL station row)...")
    ts_df = pd.read_csv(REPO_ROOT / "data" / "bengaluru_thunderstorm_features_merged.csv").tail(1)
    from ts_station_model_interface import VOBL_CELL_ID

    print("[5/7] Constructing UnifiedInferenceEngine...")
    engine = UnifiedInferenceEngine()

    print("[6/7] Running predict() for 992 cells x 5 lead hours = 4960 records...")
    cb_feature_cols = engine.heads["CB"]._model.feature_cols if "CB" in engine.heads else []
    cb_by_cell = {}
    if cb_feature_cols:
        cb_ready = cb_engineered.dropna(subset=cb_feature_cols)
        for cid in cb_ready["cell_id"]:
            cb_by_cell[cid] = cb_ready[cb_ready["cell_id"] == cid].iloc[[0]]

    now_iso = datetime.now(timezone.utc).isoformat()
    records = []
    n_ts_available = n_cb_available = n_ff_pu_scored = 0

    for cell in cells:
        cid = cell["cell_id"]
        terr = terrain_by_cell.get(cid, {})
        for lead_h in LEAD_HOURS:
            valid_time = compute_valid_time(INIT_TIME_UTC, lead_h)
            assert_no_leakage(INIT_TIME_UTC, valid_time, lead_h)

            ts_pred = engine.predict_ts(cid, lead_h, INIT_TIME_UTC,
                                         ts_df if cid == VOBL_CELL_ID else None)
            cb_pred = engine.predict_cb(cid, lead_h, INIT_TIME_UTC, cb_by_cell.get(cid))
            ff_pred = engine.predict_ff(cid, lead_h, INIT_TIME_UTC, ff_table_by_cell.get(cid))

            if ts_pred.probability is not None:
                n_ts_available += 1
            if cb_pred.probability is not None:
                n_cb_available += 1
            if ff_pred.extra.get("pu_ranking_score") is not None:
                n_ff_pu_scored += 1

            records.append({
                "cell_id": cid,
                "lat": cell["lat"],
                "lon": cell["lon"],
                "init_time": _iso(INIT_TIME_UTC),
                "valid_time": _iso(valid_time),
                "lead_hours": lead_h,
                "TS": {"probability": ts_pred.probability, "risk_category": ts_pred.risk_category,
                       "status": ts_pred.status, "model_version": ts_pred.model_version,
                       "provenance": ts_pred.provenance, "confidence": ts_pred.confidence,
                       "extra": ts_pred.extra},
                "CB": {"probability": cb_pred.probability, "risk_category": cb_pred.risk_category,
                       "status": cb_pred.status, "model_version": cb_pred.model_version,
                       "provenance": cb_pred.provenance, "confidence": cb_pred.confidence,
                       "extra": cb_pred.extra},
                "FF": {"probability": ff_pred.probability, "risk_category": ff_pred.risk_category,
                       "status": ff_pred.status, "model_version": ff_pred.model_version,
                       "provenance": ff_pred.provenance, "confidence": ff_pred.confidence,
                       "extra": ff_pred.extra},
                "terrain": {
                    "elevation_m": terr.get("elevation_m"), "slope_deg": terr.get("slope_deg"),
                    "terrain_status": terr.get("terrain_status"),
                    "catchment_status": terr.get("catchment_status"),
                },
                "model_version": {
                    "CB": cb_pred.model_version, "TS": ts_pred.model_version, "FF": ff_pred.model_version,
                    "shared_backbone": engine.backbone_status,
                },
                "confidence_reliability": {"TS": ts_pred.confidence, "CB": cb_pred.confidence, "FF": ff_pred.confidence},
            })

    print("[7/7] Computing XAI, provenance block, and writing artifact...")
    cb_shap_path = REPO_ROOT / "models" / "panindia_cb_v1" / "panindia_cb_v1_shap.json"
    cb_xai = {"method": "SHAP (TreeExplainer, Phase 21)", "available": False}
    if cb_shap_path.exists():
        with open(cb_shap_path) as f:
            shap_doc = json.load(f)
        cb_xai = {"method": "SHAP (TreeExplainer, Phase 21)", "available": True,
                   "global_importance_mean_abs_shap": shap_doc.get("global_importance_mean_abs_shap")}

    ff_coef_xai = {"method": "logistic_coefficient_magnitude (NOT SHAP)", "available": False}
    try:
        ff_head = engine.heads["FF"]
        coefs = dict(zip(ff_head.feature_cols, [float(c) for c in ff_head._model.coef_[0]]))
        top = sorted(coefs.items(), key=lambda kv: -abs(kv[1]))[:8]
        ff_coef_xai = {"method": "logistic_coefficient_magnitude (NOT SHAP)", "available": True,
                        "top_features_by_abs_coefficient": top}
    except Exception as exc:  # noqa: BLE001
        ff_coef_xai["reason"] = f"{type(exc).__name__}: {exc}"

    ts_xai = {"method": "NOT_AVAILABLE", "available": False,
               "reason": "no SHAP artifact verified against the deployed thunderstorm_model.pkl "
                         "(threshold=0.45) model exists in this repo; dev/shap_analysis.py reads a "
                         "different, unverified feature CSV and is not reused here to avoid a "
                         "mismatched-model XAI claim"}

    feature_provenance = build_feature_provenance_block(n_terrain_real, n_terrain_blocked,
                                                           n_hydro_real, n_hydro_missing)

    artifact = {
        "artifact_type": "unified_forecast (Phase 34 UNIFIED SIH INFERENCE ENGINE -- NOT a claim "
                          "that all 15 hazard x lead combinations are trained; see hazard status "
                          "per record and docs/PHASE_34_UNIFIED_SIH_INFERENCE.md)",
        "generated_at_utc": now_iso,
        "init_time_utc": _iso(INIT_TIME_UTC),
        "lead_hours_supported": list(LEAD_HOURS),
        "n_cells": 992,
        "n_lead_times": len(LEAD_HOURS),
        "n_records": len(records),
        "source_cycle_date": SOURCE_CYCLE_DATE,
        "risk_category_enum": ["LOW", "MODERATE", "HIGH", "SEVERE", "NOT_AVAILABLE"],
        "provenance_enum": ["OBSERVED", "FORECAST", "REANALYSIS", "DERIVED", "PROXY", "MISSING"],
        "shared_backbone_status": engine.backbone_status,
        "feature_provenance": feature_provenance,
        "xai": {"CB": cb_xai, "FF": ff_coef_xai, "TS": ts_xai},
        "coverage_summary": {
            "n_records_total": len(records),
            "n_ts_records_with_probability": n_ts_available,
            "n_cb_records_with_probability": n_cb_available,
            "n_ff_records_with_pu_ranking_score": n_ff_pu_scored,
            "n_ff_records_with_probability": 0,
            "note": "FF never reports a probability -- only a PU ranking score (see FF.extra."
                    "pu_ranking_score per record) -- per the explicit Phase 34 prohibition on "
                    "calling PU ranking an observed flood probability.",
        },
        "records": records,
    }

    validation = validate_artifact(artifact)
    artifact["validation"] = validation

    with open(OUTPUT_PATH, "w") as f:
        json.dump(artifact, f, indent=1)

    elapsed = time.time() - t0
    print(f"\nWrote {OUTPUT_PATH.relative_to(REPO_ROOT)}: {len(records)} records "
          f"({len(cells)} cells x {len(LEAD_HOURS)} leads) in {elapsed:.1f}s")
    print("TS available:", n_ts_available, "/ CB available:", n_cb_available, "/ FF PU-scored:", n_ff_pu_scored)
    print("Validation:", json.dumps(validation, indent=2))
    return artifact, validation


def validate_artifact(artifact: dict) -> dict:
    records = artifact["records"]
    cell_ids = sorted({r["cell_id"] for r in records})
    n_unique_cells = len(cell_ids)

    lead_slots_per_cell = {}
    for r in records:
        lead_slots_per_cell.setdefault(r["cell_id"], set()).add(r["lead_hours"])
    all_leads_present = all(s == set(LEAD_HOURS) for s in lead_slots_per_cell.values())

    leakage_ok = True
    for r in records:
        init_dt = datetime.strptime(r["init_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        valid_dt = datetime.strptime(r["valid_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        delta_h = (valid_dt - init_dt).total_seconds() / 3600.0
        if abs(delta_h - r["lead_hours"]) > 1e-6:
            leakage_ok = False
            break

    probs_in_range = True
    missing_never_zero_fabricated = True
    for r in records:
        for hz in ("TS", "CB"):
            p = r[hz]["probability"]
            if p is not None and not (0.0 <= p <= 1.0):
                probs_in_range = False
        if r["FF"]["probability"] is not None:
            missing_never_zero_fabricated = False  # FF must never carry a "probability"

    risk_never_low_for_unavailable = all(
        not (r[hz]["probability"] is None and r[hz]["risk_category"] not in ("NOT_AVAILABLE",))
        for r in records for hz in ("TS", "CB", "FF")
    )

    return {
        "exactly_992_cells": n_unique_cells == 992,
        "n_unique_cells": n_unique_cells,
        "exactly_4960_records": len(records) == 4960,
        "all_5_lead_slots_present_per_cell": all_leads_present,
        "lead_time_arithmetic_correct_for_every_record": leakage_ok,
        "all_present_probabilities_in_0_1_range": probs_in_range,
        "ff_never_carries_a_fabricated_probability": missing_never_zero_fabricated,
        "no_unavailable_probability_mapped_to_a_risk_category": risk_never_low_for_unavailable,
    }


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    main()
