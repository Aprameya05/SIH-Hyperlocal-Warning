#!/usr/bin/env python3
"""
scripts/eval_hazard_baselines.py
================================
Re-derives skill-versus-baseline numbers for the hazard models from the COMMITTED evaluation artifacts and writes
data/hazard_evaluation.json. Everything here is arithmetic on stored counts, so it is reproducible with no data
download. It adds the one thing the stored files lack: a climatological-baseline comparison (Brier skill score).

Sources
  * models/walkforward_results.json                    VOBL station thunderstorm, expanding-window yearly folds
  * models/panindia_cb_v1/panindia_cb_v1_metrics.json  pan-India heavy-rain-day model, leave-one-date-out pooled

Not covered (stated in the output, never implied): flash floods have no validated observed-flood target; neither
model has an evaluation of lead-specific (2-6 h) skill; the cloudburst model targets a heavy-rain DAY, not a cloudburst.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "hazard_evaluation.json"


def _bss(brier, base_rate):
    ref = base_rate * (1 - base_rate)          # Brier score of always forecasting the climatological frequency
    return None if ref <= 0 else round(1 - brier / ref, 4)


def thunderstorm():
    wf = json.load(open(ROOT / "models" / "walkforward_results.json"))
    folds = []
    for yr, r in sorted(wf.items()):
        n = r["n_test"]
        pos = r["hits"] + r["misses"]
        p = pos / n if n else None
        folds.append({"test_year": int(yr), "train_years": r["train_years"], "n": n, "n_event_slots": pos,
                      "event_rate": round(p, 4), "auroc": round(r["auroc"], 4), "brier": round(r["brier"], 4),
                      "brier_climatology": round(p * (1 - p), 4), "bss_vs_climatology": _bss(r["brier"], p),
                      "pod": r["pod"], "far": r["far"], "hss": r["hss"],
                      "csi": round(r["hits"] / (r["hits"] + r["misses"] + r["false_alarms"]), 4)})
    mean = lambda k: round(sum(f[k] for f in folds) / len(folds), 4)
    return {
        "hazard": "thunderstorm",
        "target": "VOBL (Bengaluru airport) thunderstorm in a 6-hour IST slot window (station-level observation label)",
        "scope": "ONE STATION (VOBL), not pan-India",
        "validation": "walk-forward by year (train on earlier years only)",
        "baseline": "climatological event frequency of the test year (Brier = p(1-p))",
        "folds": folds,
        "summary": {"mean_auroc": mean("auroc"), "mean_bss_vs_climatology": mean("bss_vs_climatology"),
                    "mean_pod": mean("pod"), "mean_far": mean("far"), "mean_hss": mean("hss"),
                    "years_with_positive_bss": sum(1 for f in folds if (f["bss_vs_climatology"] or 0) > 0)},
        "limits": ["Station-only: no pan-India thunderstorm model has been validated.",
                   "Skill is for slot windows, not for a lead-specific 2-6 h forecast.",
                   "FAR rises in later years (see folds); per-year n_event_slots is small."],
    }


def extreme_rainfall():
    m = json.load(open(ROOT / "models" / "panindia_cb_v1" / "panindia_cb_v1_metrics.json"))
    out = {"hazard": "extreme_rainfall",
           "target": "IMD gridded daily rainfall >= 64.5 mm/24 h at 0.25 deg (heavy-rain DAY proxy). NOT a cloudburst.",
           "scope": "pan-India grid, 10 held-out dates (leave-one-date-out), 6 informative", "variants": {}}
    for key in ("lodo_pooled_uncalibrated", "lodo_pooled_calibrated"):
        r = m[key]
        p = r["n_positive"] / r["n"]
        out["variants"][key.replace("lodo_pooled_", "")] = {
            "n": r["n"], "event_rate": round(p, 4), "auroc": round(r["auroc"], 4), "pr_auc": round(r["pr_auc"], 4),
            "baseline_pr_auc_no_skill": round(p, 4), "brier": round(r["brier_score"], 4),
            "brier_climatology": round(p * (1 - p), 4), "bss_vs_climatology": _bss(r["brier_score"], p),
            "pod": round(r["pod_recall"], 4), "far": round(r["far"], 4), "csi": round(r["csi"], 4),
            "hss": round(r["hss"], 4)}
    unc, cal = out["variants"]["uncalibrated"], out["variants"]["calibrated"]
    out["summary"] = {
        "uncalibrated_beats_climatology_brier": (unc["bss_vs_climatology"] or 0) > 0,
        "calibrated_beats_climatology_brier": (cal["bss_vs_climatology"] or 0) > 0,
        "note": "The uncalibrated score has NEGATIVE Brier skill versus climatology: it is a ranking score, not a "
                "probability. Calibration improves Brier but collapses POD, so no threshold is validated for alerting."}
    out["limits"] = ["Only 10 held-out dates; spatial autocorrelation inflates effective sample size.",
                     "Whole-day aggregate over GFS f006-f027: carries no lead-specific skill.",
                     "Target is a heavy-rain day proxy; verified cloudbursts are not in the label set."]
    return out


def flash_flood():
    return {"hazard": "flash_flood", "validated": False,
            "target": "none validated: no observed flash-flood event labels are wired to a pan-India model",
            "scope": "unavailable", "baseline": None,
            "limits": ["The only flood model is a PU-learning research ranking (not a probability).",
                       "Needed for live operation: observed flood event labels with times and locations, "
                       "river-gauge/soil-moisture/antecedent-rainfall predictors, catchment hydrology per cell."]}


def main():
    doc = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "kind": "EVALUATION_RECOMPUTED_FROM_COMMITTED_RESULT_FILES",
           "claims_policy": "No accuracy claim in DRIFT may exceed what is shown here. Operational 2-6 h skill is "
                            "NOT evaluated for any hazard.",
           "hazards": {"thunderstorm": thunderstorm(), "extreme_rainfall": extreme_rainfall(),
                       "flash_flood": flash_flood()}}
    OUT.write_text(json.dumps(doc, indent=1))
    print(f"wrote {OUT.relative_to(ROOT)}")
    for k, h in doc["hazards"].items():
        print(" ", k, h.get("summary", {}))


if __name__ == "__main__":
    main()
