#!/usr/bin/env python3
"""
scripts/predict_ts_panindia.py
==============================
Applies the lead-specific pan-India thunderstorm models to the latest national fields artifact and writes
data/india_ts_model_latest.json. The artifact ALWAYS exists: when no lead passed the evaluation gate (or fields/models are
missing) it says so (model_evaluated=false) and carries no values, so the app shows the pathway as unavailable instead
of failing or silently guessing.

Inputs  : data/india_fields_latest.json (verified), models/ts_panindia/*, data/ts_panindia_evaluation.json
Features: exactly the training features, from the same GFS fields at the same lead; apcp_6h = cum(L) - cum(L-6).
Output  : calibrated probabilities (isotonic, fitted on out-of-fold TRAIN predictions) that a METAR-reporting airport at
          this location would report a thunderstorm (TS or VCTS) within +-1.5 h of the valid time, plus the top-3
          TreeSHAP contributions per cell and lead (location-specific explanation).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.location_service import Store, parse_utc  # noqa: E402

OUT = ROOT / "data" / "india_ts_model_latest.json"
MODEL_VERSION = "ts_panindia_xgb_v1"


def write(doc):
    tmp = OUT.with_suffix(".json.tmp"); tmp.write_text(json.dumps(doc, separators=(",", ":"))); tmp.replace(OUT)


def unavailable(reason, init=None):
    write({"meta": {"model_version": MODEL_VERSION, "model_evaluated": False, "reason": reason, "cycle_init_utc": init, "leads": [], "valid_times": {},
                    "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, "cells": [], "leads": {}})
    print("UNAVAILABLE:", reason)


def main():
    s = Store()
    if not s.fields_ok:
        return unavailable("verified fields artifact missing: " + "; ".join(s.fields_problems[:2]))
    ev_p, mdir = ROOT / "data" / "ts_panindia_evaluation.json", ROOT / "models" / "ts_panindia"
    if not ev_p.exists() or not (mdir / "meta.json").exists():
        return unavailable("no trained/evaluated pan-India thunderstorm model", s.fields_doc["meta"]["cycle_init_utc"])
    ev, meta = json.load(open(ev_p)), json.load(open(mdir / "meta.json"))
    import xgboost as xgb
    m = s.fields_doc["meta"]; init = parse_utc(m["cycle_init_utc"]); fh = m["fhours"]; F = s.fields_doc["fields"]
    n = len(s.fields_doc["cells"]); arr = lambda k, h: np.array([np.nan if v is None else v for v in F[k][fh.index(h)]], dtype="float64")
    leads = [L for L in meta["gate_passed_leads"] if L in fh and (mdir / f"ts_lead{L:02d}.json").exists()]
    if not leads:
        return unavailable("no lead passed the evaluation gate (Brier skill > 0 and AUROC >= 0.65 in temporal and spatial holdout)", m["cycle_init_utc"])
    feats = meta["features"]; probs, expl, vt = {}, {}, {}
    for L in leads:
        v = init + timedelta(hours=L); hh = v.hour + v.minute / 60
        cols = {k: arr(k, L) for k in ("cape", "cin", "lftx", "pwat", "rh2m", "rh850", "rh700", "rh500", "w850", "w700", "w500", "soilw0_10", "k_index", "total_totals", "lapse_850_500", "ws10", "shear_850_500", "t2m_c", "mslp_hpa")}
        cols["dewpoint_depression"] = cols["t2m_c"] - arr("d2m_c", L)
        cum = arr("apcp_cum", L); cols["apcp_total"] = cum
        cols["apcp_6h"] = cum - arr("apcp_cum", L - 6) if (L - 6) in fh else cum
        cols["valid_hour_sin"], cols["valid_hour_cos"] = np.full(n, np.sin(2 * np.pi * hh / 24)), np.full(n, np.cos(2 * np.pi * hh / 24))
        X = np.column_stack([cols[k] for k in feats])
        bst = xgb.XGBClassifier(); bst.load_model(str(mdir / f"ts_lead{L:02d}.json"))
        iso = json.load(open(mdir / f"ts_lead{L:02d}_isotonic.json"))
        p = np.interp(bst.predict_proba(X)[:, 1], iso["x"], iso["y"])
        probs[str(L)] = [round(float(x), 4) for x in p]
        contrib = bst.get_booster().predict(xgb.DMatrix(X, feature_names=feats), pred_contribs=True)[:, :-1]
        top = np.argsort(-np.abs(contrib), axis=1)[:, :3]
        expl[str(L)] = [[x for j in top[i] for x in (int(j), round(float(contrib[i, j]), 2))] for i in range(n)]
        vt[str(L)] = v.strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = []
    for L in leads:
        r = ev["leads"][str(L)]; A, B = r["A_temporal_holdout"]["xgboost_calibrated"], r["B_pooled"]
        lines.append(f"+{L} h: temporal holdout AUROC {A['auroc']}, Brier skill {A['bss_vs_climatology']}; leave-region-out AUROC {B['auroc']}, Brier skill {B['bss_vs_climatology']}")
    d = ev["data"]
    summary = (f"Pilot: {d['n_stations']} METAR stations, {d['n_init_dates']} init dates (Apr-Jun 2024/2025), event rate {d['event_rate']}. " + "; ".join(lines) +
               ". Calibrated to airport METAR reports; wide uncertainty; pre-monsoon season only.")
    write({"meta": {"model_version": MODEL_VERSION, "model_evaluated": True, "cycle_init_utc": m["cycle_init_utc"], "leads": leads, "valid_times": vt,
                    "value_type": "CALIBRATED_PROBABILITY_OF_TS_REPORT_AT_METAR_STATION", "calibrated": True, "evaluation_ref": "data/ts_panindia_evaluation.json",
                    "evaluation_summary": summary, "features": feats, "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "applicability": "probabilities are calibrated to airport METAR reports; for other cells they are model estimates of the same event"},
           "cells": s.fields_doc["cells"], "leads": probs, "explanations": expl})
    print("wrote", OUT, "leads", leads)


if __name__ == "__main__":
    main()
