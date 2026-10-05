"""
scripts/ff_feature_adapter.py
================================
Phase 25 Priority 2 / Phase 26 Part B2 / Phase 27 Part E -- the adapter that
makes FFHead (backend/models/unified_mtl/heads.py) runnable, built against
the EXACT 43-column feature set FFHead requires (7 rainfall + 36 catchment;
corrects Phase 25/26's "42" miscount -- see docs/PHASE_26... Section on the
43-column correction).

Phase 27 update: the 28 catchment columns previously reported
BLOCKED-DATA/missing-everywhere were in fact a code-level column-selection
oversight in scripts/phase22_terrain_panindia.py's CATCHMENT_FEATURE_COLUMNS
(it only read 8 of the real INDOFLOODS CSV's 36 required columns). That list
now reads all 36 (32 numeric + 4 categorical), including type-aware handling
for the 4 categorical columns (mode-aggregated across gauges, then encoded
with the EXACT category->code mapping the persisted FF-PU model was fit on --
see CATCHMENT_CATEGORICAL_CODE_MAP in phase22_terrain_panindia.py). This
closes the catchment gap for the SAME 75/992 cells that already had partial
(8-column) coverage -- it does not create new gauge coverage, which remains
genuinely sparse (75/992, real INDOFLOODS mapping only, never interpolated).

Phase 26 update: the 7 rainfall-history columns are NO LONGER a blocker.
scripts/imd_rainfall_adapter.py (Phase 26 Part B1) reads the real IMD
0.25deg gridded daily rainfall (imd_rain/rain/{year}.grd, 2015-2025,
confirmed real -- same file layout already used elsewhere in this repo
for flood-day labeling) and builds real rain_1d..rain_10d windows for
any historical target_date. For target_date=2024-08-01: 386/992 cells
have a COMPLETE real 7-column rainfall window (the other 606 are
outside the IMD coverage bbox or have a gap in the 10-day window --
reported MISSING, never filled).

The remaining, now ISOLATED blocker is the 32 catchment/hydrology
columns: data/pan_india_terrain_992.json (Phase 22 Track 1) supplies
only 8 of these, for 75/992 cells (INDOFLOODS gauge mapping). The other
24 columns (Annual Precipitation, Soil type, Land cover, KoppenGeiger
Climate Type, etc.) are not populated anywhere in this repo for any
cell -- genuinely BLOCKED-DATA, not something this adapter can legitimately
construct. Intersecting real rainfall coverage with real (partial)
catchment coverage: 73/992 cells have BOTH, but still only 15/42 of the
required columns (7 real rain + 8 of 32 real catchment) -- so FFHead,
which requires all 42, still cannot run for ANY cell without those 24
catchment columns. FF_STATUS remains UNAVAILABLE, but the report below
now states this exactly, with rainfall no longer counted as missing.

Interface preserved exactly from Phase 25: build_ff_input_table(rainfall_df=...)
still works unchanged (explicit injection, e.g. for testing or a future
real source). New Phase 26 convenience:
build_ff_input_table_for_date(target_date) auto-builds rainfall_df from
the real IMD adapter instead of requiring the caller to supply it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

TERRAIN_PATH = REPO_ROOT / "data" / "pan_india_terrain_992.json"
FF_METADATA_PATH = REPO_ROOT / "processed" / "ff_pu" / "model_metadata.json"

RAIN_COLS = ["rain_1d", "rain_3d", "rain_5d", "rain_10d", "rain_max1d_5d",
             "rain_recent_vs_antecedent_ratio", "rain_accel_3d_minus_prior3d"]


def _load_catchment_table() -> pd.DataFrame:
    """One row per cell. Numeric catchment columns come from hydrology{}
    unchanged. The 4 categorical columns are taken from
    hydrology_categorical_encoded{} (the training-consistent integer code),
    NOT from the raw string in hydrology{} -- FFHead.predict() applies
    pd.to_numeric(errors="coerce") to every feature column, which would
    silently turn a raw category string into NaN. The raw string and any
    cross-gauge disagreement are preserved under *_raw / *_disagreement
    columns for inspection, never fed to the model."""
    with open(TERRAIN_PATH) as f:
        terrain = json.load(f)
    rows = []
    for c in terrain["cells"]:
        hydro = c.get("hydrology")
        row = {"cell_id": c["cell_id"], "catchment_status": c.get("catchment_status")}
        if isinstance(hydro, dict):
            row.update(hydro)
        encoded = c.get("hydrology_categorical_encoded") or {}
        raw = c.get("hydrology_categorical_raw") or {}
        disagreement = c.get("hydrology_categorical_disagreement") or {}
        for col in ("Soil type", "lithology type", "Land cover", "KoppenGeiger Climate Type"):
            row[col] = encoded.get(col)  # overrides the raw string from hydrology{} with the numeric code
            row[f"{col} (raw)"] = raw.get(col)
            row[f"{col} (gauge_disagreement)"] = disagreement.get(col, False)
        rows.append(row)
    return pd.DataFrame(rows)


# Phase 27 Part E: per-feature-group availability enum. BLOCKED_CREDENTIAL
# is reserved for features genuinely gated on an unobtained API key/login
# (none currently apply here); BLOCKED_DATA is for features with no known
# legitimate source anywhere in this repo or acquirable without one.
FEATURE_PROVENANCE_ENUM = ["OBSERVED", "DERIVED", "PROXY", "MISSING", "BLOCKED_CREDENTIAL", "BLOCKED_DATA"]

CATCHMENT_NUMERIC_COLS = [
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
CATCHMENT_CATEGORICAL_COLS = ["Soil type", "lithology type", "Land cover", "KoppenGeiger Climate Type"]


def _feature_group_report(catchment_table: pd.DataFrame) -> list:
    """One entry per required catchment/hydrology column (36), stating
    exactly how many of the 992 cells have a real value, its provenance
    category, and its source. Never a single "catchment" blob."""
    out = []
    for col in CATCHMENT_NUMERIC_COLS:
        n_avail = int(catchment_table[col].notna().sum()) if col in catchment_table.columns else 0
        out.append({
            "feature": col, "group": "catchment_numeric",
            "n_cells_available": n_avail, "n_cells_missing": 992 - n_avail,
            "provenance": "OBSERVED" if n_avail else "BLOCKED_DATA",
            "source": "INDOFLOODS gauge catchment characteristics (data/catchment_characteristics_indofloods.csv), "
                      "mean-aggregated across gauges mapped to a cell" if n_avail else None,
        })
    for col in CATCHMENT_CATEGORICAL_COLS:
        n_avail = int(catchment_table[col].notna().sum()) if col in catchment_table.columns else 0
        out.append({
            "feature": col, "group": "catchment_categorical",
            "n_cells_available": n_avail, "n_cells_missing": 992 - n_avail,
            "provenance": "DERIVED" if n_avail else "BLOCKED_DATA",
            "source": "INDOFLOODS gauge catchment characteristics, mode-aggregated across gauges, "
                      "encoded via CATCHMENT_CATEGORICAL_CODE_MAP (phase22_terrain_panindia.py)" if n_avail else None,
        })
    return out


def missing_inputs_report(target_date=None) -> dict:
    """Exact, current gap -- no fabrication, just an honest count. If
    target_date is given, real IMD rainfall coverage for that date is
    measured and reported; otherwise the rainfall columns are reported as
    a static gap. Phase 27: catchment/hydrology is reported per-column,
    not as a single blob -- see feature_group_report."""
    with open(FF_METADATA_PATH) as f:
        required_cols = json.load(f)["model_c_combined"]["feature_columns"]
    catchment_table = _load_catchment_table()
    have_catchment = set(catchment_table.columns) & set(required_cols)
    missing_catchment_cols = sorted(set(required_cols) - have_catchment - set(RAIN_COLS))
    hydro_cell_ids = set(catchment_table[catchment_table["catchment_status"] == "OBSERVED_SPARSE"]["cell_id"])
    n_cells_with_partial_catchment = len(hydro_cell_ids)
    feature_group_report = _feature_group_report(catchment_table)

    report = {
        "required_feature_cols": required_cols,
        "n_required_cols": len(required_cols),
        "catchment_cols_missing_everywhere": missing_catchment_cols,
        "n_cells_with_any_real_catchment_data": n_cells_with_partial_catchment,
        "n_cells_with_complete_feature_set_today": 0,
        "feature_group_report": feature_group_report,
        "activation_path": "pass target_date to build_ff_input_table_for_date() for real IMD rainfall, "
                            "or supply rainfall_df directly to build_ff_input_table()",
    }
    if target_date is None:
        report["rainfall_cols_missing_everywhere"] = RAIN_COLS
        report["rainfall_status"] = "NOT_CHECKED (no target_date given)"
        return report

    from imd_rainfall_adapter import build_rainfall_history_table
    rain = build_rainfall_history_table(target_date)
    rain_complete_ids = set(rain[rain["missingness"] == "COMPLETE"]["cell_id"])
    report["rainfall_status"] = f"REAL: {len(rain_complete_ids)}/992 cells have a complete " \
                                 f"7-column IMD-observed rainfall window for {target_date}"
    report["n_cells_with_real_rainfall"] = len(rain_complete_ids)
    report["n_cells_with_rainfall_and_catchment"] = len(rain_complete_ids & hydro_cell_ids)
    n_fully_ready = len(rain_complete_ids & hydro_cell_ids)
    if missing_catchment_cols:
        report["remaining_blocker"] = (
            f"{len(missing_catchment_cols)} catchment/hydrology columns missing for ALL 992 cells "
            f"(no source anywhere in this repo) -- this, not rainfall, is the sole blocker"
        )
    else:
        report["remaining_blocker"] = (
            f"No catchment column is missing everywhere any more (Phase 27). The remaining "
            f"constraint is coverage, not column availability: only {n_cells_with_partial_catchment}/992 "
            f"cells have ANY real INDOFLOODS gauge mapping (catchment is inherently gauge-sparse, never "
            f"interpolated to ungauged cells), and of those, {n_fully_ready}/992 also have a complete "
            f"real rainfall window for {target_date}."
        )
    return report


def build_ff_input_table_for_date(target_date: str, cell_ids: list | None = None) -> dict:
    """Phase 26: auto-builds rainfall_df from the REAL IMD adapter
    (scripts/imd_rainfall_adapter.py) for target_date, then delegates to
    build_ff_input_table() unchanged -- the Phase 25 interface is
    preserved exactly; this is additive, not a replacement."""
    from imd_rainfall_adapter import build_rainfall_history_table
    rain = build_rainfall_history_table(target_date, cell_ids=cell_ids)
    rain_df = rain[rain["missingness"] == "COMPLETE"][["cell_id"] + RAIN_COLS]
    return build_ff_input_table(rainfall_df=rain_df)


def build_ff_input_table(rainfall_df: pd.DataFrame | None = None) -> dict:
    """If rainfall_df is None: returns the UNAVAILABLE report (no
    fabrication). If supplied, joins it with the existing real
    catchment/hydrology output and reports which cells now have a
    complete 42-column feature row ready for FFHead.predict() -- still
    does NOT invent the 24 catchment columns that are missing everywhere;
    those cells remain excluded even with rainfall supplied, and are
    reported as such."""
    report = missing_inputs_report()
    if rainfall_df is None or len(rainfall_df) == 0:
        return {"status": "UNAVAILABLE", **report}

    missing_rain_cols = [c for c in RAIN_COLS if c not in rainfall_df.columns]
    if missing_rain_cols:
        raise ValueError(f"rainfall_df is missing required columns: {missing_rain_cols}")

    catchment_table = _load_catchment_table()
    merged = rainfall_df.merge(catchment_table, on="cell_id", how="inner")
    required_cols = report["required_feature_cols"]
    # BUG FIX (Phase 26): a column entirely ABSENT from merged (not just
    # null) must count as missing for every row -- the previous version
    # silently restricted the completeness check to only the columns
    # that happened to be present, which could report a cell "ready"
    # while FFHead.predict_proba() would still raise ValueError for the
    # columns that were never even in the table. present_cols/missing_cols
    # are tracked separately and surfaced in the result below.
    present_cols = [c for c in required_cols if c in merged.columns]
    absent_cols = [c for c in required_cols if c not in merged.columns]
    if absent_cols or not present_cols:
        ready_cell_ids = []
    else:
        complete_mask = merged[present_cols].notna().all(axis=1)
        ready_cell_ids = merged.loc[complete_mask, "cell_id"].tolist() if len(merged) else []

    return {
        "status": "PU_RANKING" if ready_cell_ids else "UNAVAILABLE",
        "n_cells_ready_for_ff_prediction": len(ready_cell_ids),
        "ready_cell_ids": ready_cell_ids[:20],
        "ready_cell_ids_full": ready_cell_ids,
        "columns_entirely_absent": absent_cols,
        "feature_table": merged[["cell_id"] + present_cols] if len(merged) else merged,
        "note": "FF is a PU (positive-unlabeled) ranking model, never a supervised flood "
                "probability -- see docs/PHASE_22_FLASH_FLOOD_MODEL.md.",
    }


# ---------------------------------------------------------------------------
# Phase 28 Part B: OOD/reliability diagnostic -- NEVER used to alter or clip
# the raw PU score. The model's training population (processed/ff_pu/
# ff_pu_training_table.csv, 2015-01-01..2020-09-24, 69 real INDOFLOODS event
# cells) is a fixed, finite reference distribution. Any inference query
# (different date, different cells) can legitimately fall outside it --
# this flags that honestly instead of hiding it behind a clipped/smoothed
# score.
# ---------------------------------------------------------------------------
OOD_RELIABILITY_ENUM = ["RELIABLE", "MARGINAL_EXTRAPOLATION", "OOD_EXTRAPOLATION"]


def assess_ood_reliability(features_df: pd.DataFrame, feature_cols: list) -> list:
    """Per-row reliability tag, using the SAME persisted StandardScaler the
    model was fit with (so this measures standard deviations from the
    model's actual training distribution, not an ad-hoc reference). Returns
    one tag per row plus the max |z| and which feature drove it, for
    transparency. Thresholds (3 / 6 std) are a documented heuristic, not a
    calibrated boundary -- this is a diagnostic flag, not a correction."""
    import pickle
    import warnings as _warnings
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        with open(REPO_ROOT / "processed" / "ff_pu" / "RESEARCH_ONLY_model_c_logistic.pkl", "rb") as f:
            bundle = pickle.load(f)
    scaler = bundle["scaler"]
    X = features_df[feature_cols].apply(pd.to_numeric, errors="coerce")
    X = X.fillna(X.median())
    Xs = scaler.transform(X)
    max_abs_z = np.abs(Xs).max(axis=1)
    driver_idx = np.abs(Xs).argmax(axis=1)
    out = []
    for i in range(len(X)):
        z = float(max_abs_z[i])
        if z < 3:
            tag = "RELIABLE"
        elif z < 6:
            tag = "MARGINAL_EXTRAPOLATION"
        else:
            tag = "OOD_EXTRAPOLATION"
        out.append({
            "reliability": tag,
            "max_abs_feature_z": round(z, 2),
            "driving_feature": feature_cols[driver_idx[i]],
        })
    return out


def predict_ff_with_reliability(target_date: str) -> dict:
    """Phase 28: the FF prediction path that should be used going forward --
    wraps build_ff_input_table_for_date() + the unmodified FFHead.predict()
    + an honest OOD/reliability tag per cell. Never clips or rescales the
    raw PU score; never calls it a calibrated probability."""
    sys.path.insert(0, str(REPO_ROOT))
    from backend.models.unified_mtl.heads import FFHead

    res = build_ff_input_table_for_date(target_date)
    if res["status"] != "PU_RANKING":
        return res

    ft = res["feature_table"]
    ready = res["ready_cell_ids_full"]
    sub = ft[ft["cell_id"].isin(ready)].reset_index(drop=True)
    head = FFHead()
    raw_scores = head.predict(sub, pu_corrected=False)
    pu_scores = head.predict(sub, pu_corrected=True)
    reliability = assess_ood_reliability(sub, head.feature_cols)

    predictions = []
    for i, cid in enumerate(sub["cell_id"]):
        predictions.append({
            "cell_id": cid,
            "pu_flood_risk_ranking_score": round(float(pu_scores[i]), 6),
            "raw_pu_sigmoid_score_uncorrected": round(float(raw_scores[i]), 6),
            "status": "PU_RANKING",
            "provenance": "real IMD observed rainfall + real INDOFLOODS catchment hydrology",
            **reliability[i],
        })
    return {
        "status": "PU_RANKING",
        "n_cells": len(predictions),
        "predictions": predictions,
        "note": "pu_flood_risk_ranking_score is a PU (positive-unlabeled) flood-risk RANKING "
                "score, never a calibrated probability of flash flood. 'reliability' flags "
                "whether this cell/date's features fall inside (RELIABLE) or outside "
                "(MARGINAL_EXTRAPOLATION / OOD_EXTRAPOLATION) the model's real 2015-2020 "
                "69-cell training distribution -- see docs/PHASE_28_FLASH_FLOOD_PU_HARDENING.md.",
    }


if __name__ == "__main__":
    print(json.dumps(missing_inputs_report(), indent=2))
