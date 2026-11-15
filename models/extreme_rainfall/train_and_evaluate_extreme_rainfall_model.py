#!/usr/bin/env python3
"""
models/extreme_rainfall/train_and_evaluate_extreme_rainfall_model.py
===================================
Short-duration heavy rainfall: P(CMORPH 3 h rainfall >= X mm at a 0.25 deg cell), lead-specific (9/15/21 h from 00Z).
Not a cloudburst model: 0.25 deg / 3 h satellite estimates cannot verify >= 100 mm/h events at ~10 km.

Validation, leakage controls and gate are the same as models/thunderstorm/train_and_evaluate_thunderstorm_model.py (temporal holdout 2024->2025; leave-one-region-
out; calibration and threshold from date-grouped out-of-fold TRAIN predictions; fixed hyperparameters). Strong simple
baselines: training base rate, and the RAW GFS 3 h forecast rainfall used as a score (the obvious operational baseline).
Extra gate: the calibrated model's AUROC must be >= the raw-GFS-rainfall AUROC, otherwise it adds nothing and is withheld.
Outputs: models/extreme_rainfall/artifacts/panindia_v1/*, data/rain_panindia_evaluation.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from models.thunderstorm import train_and_evaluate_thunderstorm_model as T  # noqa: E402

FEATS = ["gfs_p3h", "apcp_total", "pwat", "cape", "cin", "lftx", "k_index", "total_totals", "lapse_850_500", "rh2m", "rh850", "rh700", "rh500", "w850", "w700", "w500",
         "ws10", "shear_850_500", "t2m_c", "dewpoint_depression", "mslp_hpa", "soilw0_10"]
THRESHOLDS = [20.0, 40.0]
T.FEATS = FEATS
T.LOGIT_FEATS = ["gfs_p3h", "pwat", "cape", "w700", "rh700", "k_index"]


def load():
    d = pd.read_csv(ROOT / "cache" / "rain_panindia" / "features.csv")
    d["year"] = pd.to_datetime(d["init"]).dt.year
    d["date"] = pd.to_datetime(d["init"]).dt.strftime("%Y-%m-%d")
    d["region"] = [T.region(a, b) for a, b in zip(d["lat"], d["lon"])]
    return d


def evaluate(tr, te):
    base = tr["y"].mean()
    model, iso, thr = T.fit_xgb_calibrated(tr)
    p = iso.predict(model.predict_proba(te[FEATS].values)[:, 1])
    lg = T.fit_logit(tr); pl = lg(te); thr_l = T.best_csi_threshold(tr["y"].values, lg(tr))
    q = tr["gfs_p3h"].fillna(0).values; qthr = T.best_csi_threshold(tr["y"].values, q / 200.0)
    pq = np.clip(te["gfs_p3h"].fillna(0).values / 200.0, 0, 1)
    res = {"xgboost_calibrated": T.metrics(te["y"], p, thr, base, te["date"].values), "logistic": T.metrics(te["y"], pl, thr_l, base),
           "raw_gfs_rainfall_rule": {k: v for k, v in T.metrics(te["y"], pq, qthr, base).items() if k not in ("brier", "bss_vs_climatology")},
           "climatology": {"n": int(len(te)), "base_rate_from_training": round(float(base), 4)}}
    res["raw_gfs_rainfall_rule"]["threshold_mm"] = round(qthr * 200, 2)
    res["reliability_xgboost"] = T.reliability(te["y"].values, p)
    return res, (model, iso, thr), p


def main():
    d = load()
    print("rows", len(d), "init dates", d.date.nunique(), "cells", d.cell.nunique())
    report = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "data": {"labels": "NOAA CDR CMORPH V1.0 hourly 0.25 deg, 3 h accumulation ending at the valid time (satellite estimate, gauge-adjusted over land)",
                       "predictors": "GFS 0.25 deg init 00Z, leads 9/15/21 h, cell centre = GFS grid point", "n_rows": int(len(d)), "n_init_dates": int(d.date.nunique()),
                       "years": sorted(map(int, d.year.unique())), "season": "monsoon, 15 Jun - 15 Sep, every 6th day"},
              "target": "3 h CMORPH rainfall at the 0.25 deg cell >= threshold (short-duration heavy rainfall; NOT cloudburst)",
              "gate": {"rule": "calibrated model: Brier skill >= 0.01, AUROC >= 0.65 in temporal AND pooled leave-region-out holdout, and AUROC >= raw GFS rainfall rule", "per_target": {}},
              "targets": {}, "limits": ["CMORPH is a satellite estimate; heavy convective rainfall in mountains is under-estimated",
                                        "monsoon only, two seasons, every 6th day: wide uncertainty", "leads 9/15/21 h from 00Z only",
                                        "0.25 deg / 3 h cannot represent cloudbursts (>= 100 mm/h over ~10 km)"]}
    out_dir = ROOT / "models" / "extreme_rainfall" / "artifacts" / "panindia_v1"; out_dir.mkdir(parents=True, exist_ok=True); models = {}
    for old in out_dir.glob("*.json"):
        old.unlink()          # never leave a model from an earlier run that did not pass this run's gate
    for thr_mm in THRESHOLDS:
        d["y"] = (d["cmorph_3h"] >= thr_mm).astype(int)
        for L in sorted(d.lead.unique()):
            key = f"{int(thr_mm)}mm_f{int(L)}"; dl = d[d.lead == L]; tr, te = dl[dl.year == 2024], dl[dl.year == 2025]
            if tr["y"].sum() < 200 or te["y"].sum() < 80:
                report["targets"][key] = {"status": "INSUFFICIENT_EVENTS", "train_events": int(tr.y.sum()), "test_events": int(te.y.sum())}; report["gate"]["per_target"][key] = False; continue
            A, _, _ = evaluate(tr, te)
            pooled_y, pooled_p, pooled_g, B_by = [], [], [], {}
            for R in sorted(dl.region.unique()):
                trR, teR = tr[tr.region != R], te[te.region == R]
                if teR["y"].sum() < 20 or trR["y"].sum() < 100:
                    B_by[R] = {"status": "TOO_FEW_EVENTS", "n": int(len(teR)), "n_events": int(teR.y.sum())}; continue
                r, _, p = evaluate(trR, teR); B_by[R] = r["xgboost_calibrated"] | {"raw_gfs_rule_csi": r["raw_gfs_rainfall_rule"].get("csi")}
                pooled_y += list(teR["y"]); pooled_p += list(p); pooled_g += list(teR["date"])
            pooled = T.metrics(np.array(pooled_y), np.array(pooled_p), T.best_csi_threshold(np.array(pooled_y), np.array(pooled_p)), tr["y"].mean(), np.array(pooled_g)) if pooled_y else {}
            qa = A["raw_gfs_rainfall_rule"]; xa = A["xgboost_calibrated"]
            qauc = float(roc_auc_score(te["y"], te["gfs_p3h"].fillna(0))) if te["y"].nunique() > 1 else 0
            ok = (xa.get("bss_vs_climatology", -1) >= T.GATE_BSS and xa.get("auroc", 0) >= T.GATE_AUROC and pooled.get("bss_vs_climatology", -1) >= T.GATE_BSS and pooled.get("auroc", 0) >= T.GATE_AUROC
                  and xa.get("auroc", 0) >= qauc)
            report["targets"][key] = {"threshold_mm_per_3h": thr_mm, "lead_h": int(L), "A_temporal_holdout": A, "raw_gfs_rainfall_auroc_2025": round(qauc, 4), "B_by_region_spatial_temporal": B_by,
                                      "B_pooled": pooled, "passes_gate": bool(ok)}
            report["gate"]["per_target"][key] = bool(ok)
            print(f"{key}: events tr={int(tr.y.sum())} te={int(te.y.sum())}  A auroc={xa.get('auroc')} bss={xa.get('bss_vs_climatology')} (raw GFS auroc {qauc:.3f})  B auroc={pooled.get('auroc')} bss={pooled.get('bss_vs_climatology')}  gate={ok}")
            if ok:
                m, iso, thr = T.fit_xgb_calibrated(dl); models[key] = (m, iso, thr)
    for key, (m, iso, thr) in models.items():
        m.save_model(str(out_dir / f"rain_{key}.json"))
        json.dump({"x": [float(x) for x in iso.X_thresholds_], "y": [float(x) for x in iso.y_thresholds_], "threshold": thr}, open(out_dir / f"rain_{key}_isotonic.json", "w"))
    json.dump({"features": FEATS, "gate_passed": [k for k, v in report["gate"]["per_target"].items() if v], "thresholds_mm": THRESHOLDS, "leads": [9, 15, 21]}, open(out_dir / "meta.json", "w"), indent=1)
    (ROOT / "data" / "rain_panindia_evaluation.json").write_text(json.dumps(report, indent=1))
    print("gate:", report["gate"]["per_target"])


if __name__ == "__main__":
    main()
