#!/usr/bin/env python3
"""Retrain the VOBL station thunderstorm slot models using ONLY inputs that have a real live value at inference.

Why: the v6 models were trained with 28 of 64 inputs (station observations such as same-day rainfall and lagged labels, plus features the live
code never computes) that are constants at inference. This trains the same target (ts_label per 6-hour IST slot) on the remaining features.

Protocol (chronological, no test leakage):
  * features  = v6 feature list minus station_inputs.STATION_OBS_FEATURES and NEVER_COMPUTED_LIVE (+ doy derived from the date)
  * label quality: a (slot, year) with an implausible event drought (< 25% of that slot's median annual event count, e.g. 2019 for every slot
    and 2024-2025 for slots 0, 1, 3, which have exactly zero events against a 1-5% base rate) is treated as MISSING labels (observations
    stored as "no event"), and excluded from training and testing
  * TEST      = the last two label-valid years of each slot (slot 2: 2024-2025; slots 0, 1, 3: 2022-2023), never used for any choice;
    dev set   = the label-valid years before them
  * calibration and the decision threshold come from leave-one-YEAR-out out-of-fold predictions on the dev set
  * final model = fit on the whole dev set, fixed hyper-parameters (no tuning on the test years)
  * metrics on TEST: AUROC, PR-AUC, Brier, Brier skill vs climatology and vs slot-month climatology, POD, FAR, CSI, HSS, bias,
    reliability bins, day-block bootstrap 95% intervals
  * GATE (all must hold on the TEST years): >= 20 test events, lower 95% bound of the Brier skill vs slot-month climatology > 0, and lower 95% bound
    of AUROC >= 0.60. Otherwise no artifact is produced and the slot stays on the (flagged) fallback. Negative results are reported, not hidden.

Caveat that no metric here removes: the table's atmospheric predictors are reanalysis-like (ERA5_*), while live inference uses GFS
forecasts of the same quantities. Train/serve skew is NOT quantified by this evaluation.
Usage: python models/thunderstorm/station/train_live_inputs.py [--write]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")
from station_inputs import NEVER_COMPUTED_LIVE, STATION_OBS_FEATURES  # noqa: E402

DATA = ROOT / "data" / "bengaluru_6hr_training_dataset_cb_ff.csv"
OUT_EVAL = ROOT / "models" / "station_live_inputs_evaluation.json"
MIN_YEAR_EVENT_SHARE = 0.25     # a year with fewer events than this share of the slot's median annual count has missing labels
N_TEST_YEARS = 2
SEED = 20261123
PARAMS = dict(n_estimators=250, learning_rate=0.03, max_depth=3, min_child_weight=8, subsample=0.8, colsample_bytree=0.8,
              reg_lambda=8.0, gamma=0.5, tree_method="hist", random_state=SEED, n_jobs=2, eval_metric="logloss")
# Gate: skill must be real on the untouched test years. All three must hold.
GATE = {"min_test_events": 20, "min_bss_vs_slot_month_clim_ci_low": 0.0, "min_auroc_ci_low": 0.60}


def feature_list() -> list[str]:
    import joblib
    art = joblib.load(ROOT / "models" / "nowcast_slot2_xgb_v6_temporal.pkl")
    cols = art.get("feature_cols") or art.get("features")
    banned = STATION_OBS_FEATURES | NEVER_COMPUTED_LIVE
    return [c for c in cols if c not in banned]


def load():
    df = pd.read_csv(DATA)
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    df["doy"] = df["date"].dt.dayofyear
    return df.sort_values(["date", "slot"]).reset_index(drop=True)


def valid_years(d: pd.DataFrame) -> list[int]:
    cnt = d.groupby("year").ts_label.sum()
    med = float(cnt.median())
    return [int(y) for y, c in cnt.items() if c >= 1 and c >= MIN_YEAR_EVENT_SHARE * med]


def clim_by_month(train: pd.DataFrame) -> dict[int, float]:
    base = float(train.ts_label.mean())
    g = train.groupby("month").ts_label.agg(["sum", "count"])
    k = 20.0                                   # shrink thin months toward the slot base rate
    return {int(m): float((r["sum"] + k * base) / (r["count"] + k)) for m, r in g.iterrows()}


def brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def cat_metrics(p, y, thr):
    p, y = np.asarray(p), np.asarray(y).astype(int)
    pred = p >= thr
    a, b, c, d = int((pred & (y == 1)).sum()), int((pred & (y == 0)).sum()), int((~pred & (y == 1)).sum()), int((~pred & (y == 0)).sum())
    pod = a / (a + c) if a + c else None
    far = b / (a + b) if a + b else None
    csi = a / (a + b + c) if a + b + c else None
    den = (a + c) * (c + d) + (a + b) * (b + d)
    hss = 2.0 * (a * d - b * c) / den if den else None
    return {"threshold": round(float(thr), 4), "hits": a, "false_alarms": b, "misses": c, "correct_negatives": d,
            "POD": None if pod is None else round(pod, 4), "FAR": None if far is None else round(far, 4),
            "CSI": None if csi is None else round(csi, 4), "HSS": None if hss is None else round(hss, 4),
            "bias": round((a + b) / (a + c), 3) if a + c else None}


def best_threshold(p, y):
    qs = np.unique(np.quantile(p, np.linspace(0.80, 0.999, 120)))
    best, best_s = None, -9
    for t in qs:
        m = cat_metrics(p, y, t)
        s = (m["HSS"] if m["HSS"] is not None else -9) + 1e-3 * (m["CSI"] or 0)
        if m["hits"] >= 3 and s > best_s:
            best, best_s = float(t), s
    return best if best is not None else float(np.quantile(p, 0.95))


def reliability(p, y, bins=8):
    p, y = np.asarray(p), np.asarray(y)
    edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    return [{"mean_forecast": round(float(p[idx == i].mean()), 4), "observed_rate": round(float(y[idx == i].mean()), 4), "n": int((idx == i).sum())}
            for i in range(len(edges) - 1) if (idx == i).any()]


def day_block_bootstrap(dates, y, p, p_ref, n=600):
    from sklearn.metrics import roc_auc_score
    days = np.unique(dates)
    by = {d: np.where(dates == d)[0] for d in days}
    rng = np.random.default_rng(SEED)
    au, bs = [], []
    for _ in range(n):
        ix = np.concatenate([by[d] for d in rng.choice(days, len(days), replace=True)])
        if y[ix].min() == y[ix].max():
            continue
        au.append(roc_auc_score(y[ix], p[ix]))
        bs.append(1 - brier(p[ix], y[ix]) / brier(p_ref[ix], y[ix]))
    q = lambda a: [round(float(np.percentile(a, 2.5)), 4), round(float(np.percentile(a, 97.5)), 4)]
    return {"auroc_95ci": q(au), "bss_vs_slot_month_clim_95ci": q(bs), "n_resamples": len(au)}


def fit(X, y):
    import xgboost as xgb
    pos = max(1, int(y.sum()))
    m = xgb.XGBClassifier(**{**PARAMS, "scale_pos_weight": min(10.0, (len(y) - pos) / pos)})
    m.fit(X, y, verbose=False)
    return m


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write model artifacts for slots that pass the gate (always writes the evaluation JSON)")
    a = ap.parse_args(argv)
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import average_precision_score, roc_auc_score
    feats = feature_list()
    df = load()
    missing = [f for f in feats if f not in df.columns]
    assert not missing, f"training table lacks live features: {missing}"
    report = {"schema": "station_live_inputs_evaluation_v1", "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "target": "ts_label (thunderstorm in the 6-hour IST slot at VOBL)", "features": feats, "n_features": len(feats),
              "excluded_station_features": sorted(STATION_OBS_FEATURES), "excluded_never_computed_live": sorted(NEVER_COMPUTED_LIVE),
              "data_file": str(DATA.relative_to(ROOT)).replace("\\", "/"), "data_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
              "split": {"rule": "per slot: label-valid years only; test = last 2 valid years; dev = earlier valid years (leave-one-year-out for calibration/threshold)"}, "xgb_params": PARAMS, "gate": GATE,
              "caveat": "predictors in the table are reanalysis-like (ERA5_*); live inference uses GFS forecasts of the same quantities: train/serve skew is not quantified",
              "slots": {}}
    for slot in (0, 1, 2, 3):
        d = df[df.slot == slot].reset_index(drop=True)
        vy = valid_years(d)
        excluded = sorted(set(int(y) for y in d.year.unique()) - set(vy))
        test_years, dev_years = vy[-N_TEST_YEARS:], vy[:-N_TEST_YEARS]
        dev = d[d.year.isin(dev_years)].reset_index(drop=True)
        test = d[d.year.isin(test_years)].reset_index(drop=True)
        years = sorted(dev.year.unique())
        oof = np.full(len(dev), np.nan)
        for yv in years:                                          # leave-one-year-out on the dev set only
            tr, va = dev.year != yv, dev.year == yv
            if dev.loc[tr, "ts_label"].sum() < 5:
                continue
            oof[va.values] = fit(dev.loc[tr, feats], dev.loc[tr, "ts_label"]).predict_proba(dev.loc[va, feats])[:, 1]
        ok = np.isfinite(oof)
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(oof[ok], dev.loc[ok, "ts_label"])
        thr = best_threshold(iso.predict(oof[ok]), dev.loc[ok, "ts_label"].values)
        model = fit(dev[feats], dev.ts_label)
        p = iso.predict(model.predict_proba(test[feats])[:, 1])
        y = test.ts_label.values.astype(int)
        base = float(dev.ts_label.mean())
        cm = clim_by_month(dev)
        p_clim_m = test.month.map(cm).values.astype(float)
        row = {"dev_years": dev_years, "test_years": test_years, "excluded_label_drought_years": excluded, "n_dev": len(dev), "n_test": len(test), "test_events": int(y.sum()), "dev_base_rate": round(base, 4), "test_base_rate": round(float(y.mean()), 4)}
        if y.sum() >= 1 and y.sum() < len(y):
            row.update({"AUROC": round(float(roc_auc_score(y, p)), 4), "PR_AUC": round(float(average_precision_score(y, p)), 4),
                        "PR_AUC_no_skill": round(float(y.mean()), 4), "Brier": round(brier(p, y), 5),
                        "Brier_climatology": round(brier(np.full(len(y), base), y), 5), "Brier_slot_month_climatology": round(brier(p_clim_m, y), 5),
                        "BSS_vs_climatology": round(1 - brier(p, y) / brier(np.full(len(y), base), y), 4),
                        "BSS_vs_slot_month_climatology": round(1 - brier(p, y) / brier(p_clim_m, y), 4),
                        "AUROC_slot_month_climatology": round(float(roc_auc_score(y, p_clim_m)), 4),
                        "categorical_at_dev_threshold": cat_metrics(p, y, thr), "reliability": reliability(p, y),
                        "bootstrap": day_block_bootstrap(test.date.values, y, p, p_clim_m)})
            g = row
            row["gate"] = {"passed": bool(row["test_events"] >= GATE["min_test_events"] and g["bootstrap"]["bss_vs_slot_month_clim_95ci"][0] > GATE["min_bss_vs_slot_month_clim_ci_low"]
                                          and g["bootstrap"]["auroc_95ci"][0] >= GATE["min_auroc_ci_low"]),
                           "reasons": [r for r, bad in (("fewer test events than the minimum", row["test_events"] < GATE["min_test_events"]),
                                                         ("Brier skill over slot-month climatology is not significantly > 0 (95% lower bound)", g["bootstrap"]["bss_vs_slot_month_clim_95ci"][0] <= GATE["min_bss_vs_slot_month_clim_ci_low"]),
                                                         ("AUROC lower bound below the minimum", g["bootstrap"]["auroc_95ci"][0] < GATE["min_auroc_ci_low"])) if bad]}
        else:
            row["gate"] = {"passed": False, "reasons": ["test set has no positive (or no negative) cases"]}
        row["decision_threshold_from_dev_oof"] = round(thr, 4)
        report["slots"][str(slot)] = row
        print(f"slot {slot}: test events {row['test_events']}/{len(test)}  AUROC {row.get('AUROC')}  BSS(slot-month) {row.get('BSS_vs_slot_month_climatology')}  gate {row['gate']['passed']} {row['gate']['reasons']}")
        if a.write and row["gate"]["passed"]:
            import joblib
            art = {"model": model, "calibrator": iso, "calib_method": "isotonic", "feature_cols": feats, "features": feats, "threshold": float(thr), "slot": slot,
                   "model_version": "v7_live_inputs", "metrics": {k: row[k] for k in ("AUROC", "PR_AUC", "Brier", "BSS_vs_slot_month_climatology")},
                   "trained_at": report["generated_at_utc"], "evaluation_ref": "models/station_live_inputs_evaluation.json"}
            joblib.dump(art, ROOT / "models" / f"nowcast_slot{slot}_xgb_v7_live_inputs.pkl")
            print(f"   wrote models/nowcast_slot{slot}_xgb_v7_live_inputs.pkl")
    OUT_EVAL.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("evaluation ->", OUT_EVAL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
