#!/usr/bin/env python3
"""
models/extreme_rainfall/predict_extreme_rainfall.py
================================
Applies the lead-specific SHORT-DURATION HEAVY RAINFALL models (CMORPH 3 h >= X mm, leads 9/15/21 h from 00Z) to the latest
verified fields artifact -> data/india_rain_model_latest.json. Always written; model_evaluated=false with a reason when no
target passed the gate (see models/extreme_rainfall/train_and_evaluate_extreme_rainfall_model.py), so the UI shows 'unavailable' instead of guessing.
Only 00Z cycles match the training design; a 12Z cycle yields model_evaluated=false.
This is NOT a cloudburst forecast.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.location_service import Store, parse_utc  # noqa: E402
from backend.model_features import build_feature_matrix  # noqa: E402

OUT = ROOT / "data" / "india_rain_model_latest.json"


def write(doc):
    tmp = OUT.with_suffix(".json.tmp"); tmp.write_text(json.dumps(doc, separators=(",", ":"))); tmp.replace(OUT)


def unavailable(reason, init=None):
    write({"meta": {"model_version": "rain_panindia_xgb_v1", "model_evaluated": False, "reason": reason, "cycle_init_utc": init, "targets": [],
                    "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, "cells": [], "targets": {}})
    print("UNAVAILABLE:", reason)


def main():
    s = Store()
    if not s.fields_ok:
        return unavailable("verified fields artifact missing: " + "; ".join(s.fields_problems[:2]))
    m = s.fields_doc["meta"]; init = parse_utc(m["cycle_init_utc"])
    mdir, evp = ROOT / "models" / "extreme_rainfall" / "artifacts" / "panindia_v1", ROOT / "data" / "rain_panindia_evaluation.json"
    if not (mdir / "meta.json").exists() or not evp.exists():
        return unavailable("no trained/evaluated short-duration rainfall model", m["cycle_init_utc"])
    meta, ev = json.load(open(mdir / "meta.json")), json.load(open(evp))
    if init.hour != 0:
        return unavailable("the rainfall models were trained for 00Z cycles only; this cycle is %02dZ" % init.hour, m["cycle_init_utc"])
    keys = [k for k in meta["gate_passed"] if (mdir / f"rain_{k}.json").exists() and int(k.split("_f")[1]) in m["fhours"]]
    if not keys:
        return unavailable("no target passed the evaluation gate", m["cycle_init_utc"])
    import xgboost as xgb
    fh = m["fhours"]; F = s.fields_doc["fields"]; n = len(s.fields_doc["cells"]); feats = meta["features"]
    arr = lambda k, h: np.array([np.nan if v is None else v for v in F[k][fh.index(h)]], dtype="float64")
    targets, expl, vt, info = {}, {}, {}, {}
    for key in keys:
        L = int(key.split("_f")[1]); thr = int(key.split("mm")[0])
        X = build_feature_matrix(s.fields_doc, feats, L, "rainfall")
        bst = xgb.XGBClassifier(); bst.load_model(str(mdir / f"rain_{key}.json")); iso = json.load(open(mdir / f"rain_{key}_isotonic.json"))
        p = np.interp(bst.predict_proba(X)[:, 1], iso["x"], iso["y"])
        targets[key] = [round(float(x), 5) for x in p]
        c = bst.get_booster().predict(xgb.DMatrix(X, feature_names=feats), pred_contribs=True)[:, :-1]; top = np.argsort(-np.abs(c), axis=1)[:, :3]
        expl[key] = [[x for j in top[i] for x in (int(j), round(float(c[i, j]), 2))] for i in range(n)]
        vt[key] = (init + timedelta(hours=L)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = ev["targets"][key]; A, B = r["A_temporal_holdout"]["xgboost_calibrated"], r["B_pooled"]
        info[key] = {"threshold_mm_per_3h": thr, "lead_h": L, "valid_time_utc": vt[key], "window": "3 h ending at valid time",
                     "temporal_holdout": {"auroc": A["auroc"], "pr_auc": A["pr_auc"], "pr_auc_no_skill": A["pr_auc_no_skill"], "brier_skill": A["bss_vs_climatology"], "pod": A["pod"], "far": A["far"], "csi": A["csi"]},
                     "leave_region_out": {"auroc": B["auroc"], "brier_skill": B["bss_vs_climatology"]}, "raw_gfs_rainfall_auroc": r["raw_gfs_rainfall_auroc_2025"]}
    write({"meta": {"model_version": "rain_panindia_xgb_v1", "model_evaluated": True, "cycle_init_utc": m["cycle_init_utc"], "targets": keys, "target_info": info,
                    "value_type": "CALIBRATED_PROBABILITY_OF_CMORPH_3H_RAINFALL_AT_OR_ABOVE_THRESHOLD", "calibrated": True, "evaluation_ref": "data/rain_panindia_evaluation.json",
                    "features": feats, "not_cloudburst": True, "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "limits": ev["limits"]}, "cells": s.fields_doc["cells"], "targets": targets, "explanations": expl})
    print("wrote", OUT, keys)


if __name__ == "__main__":
    main()
