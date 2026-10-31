#!/usr/bin/env python3
"""
backend/dispatch_alerts.py   (runs in update_grid.yml after the grid is built)
=============================
Automated, grid-driven SMS alerts are WITHHELD unless a candidate passes backend/alert_policy.evaluate.

This script used to text recipients whenever ANY grid cell had a "probability" >= 0.35 for thunderstorm,
cloudburst OR flash flood. Those grid fields are heuristic risk scores and a daily heavy-rain-day proxy (not
calibrated probabilities), flash flood has no validated target, and nothing here checked freshness, lead time or
provenance. Owner decisions Q4/Q14/Q16/Q18: no heuristic score or PU score may trigger an operational alert, and
all alertability registries start empty. So every candidate is evaluated and refused with an explicit reason; the
reasons are printed (and written to the job summary) so the silence is never mysterious. No SMS is sent.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import alert_policy  # noqa: E402

DATA_FILE = Path(__file__).parent.parent / "data" / "pan_india_grid.json"
HAZARD_FIELDS = (("TS", "thunderstorm_probability"), ("CB", "cloudburst_probability"), ("FF", "flash_flood_probability"))
CANDIDATE_THRESHOLD = 0.35     # only used to count what the old rule WOULD have fired on (never sent)


def evaluate_grid(grid: dict) -> dict:
    cells = grid.get("grid_cells", [])
    reasons, would_have_fired, eligible = Counter(), 0, []
    for c in cells:
        for hz, field in HAZARD_FIELDS:
            p = c.get(field)
            if p is None or p < CANDIDATE_THRESHOLD:
                continue
            would_have_fired += 1
            cand = {
                "probability": p, "temporal_status": "CURRENT",
                "value_type": ("MODEL_SCORE_UNCALIBRATED" if str(c.get("cloudburst_probability_source", "")).startswith("panindia")
                               and hz == "CB" else c.get("value_type") or "HEURISTIC_RISK_SCORE"),
                "event_class": c.get("cloudburst_event_class") if hz == "CB" else None,
                "is_daily_outlook": hz == "CB", "lead_specific": False,
                "forecast_available_time_utc": None, "operational_lead_hours": None,
                "valid_time_utc": c.get("forecast_valid_at_utc"), "generated_at_utc": grid.get("generated_at_utc"),
            }
            ok, why = alert_policy.evaluate(cand, hazard=hz)
            if ok:
                eligible.append((c.get("cell_id"), hz))
            for r in why:
                reasons[r] += 1
    return {"n_cells": len(cells), "legacy_rule_would_have_fired": would_have_fired,
            "eligible": eligible, "reason_counts": dict(reasons)}


def main():
    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE} not found -- run pipeline.py first")
        sys.exit(1)
    res = evaluate_grid(json.loads(DATA_FILE.read_text()))
    msg = (f"Automated grid alerts WITHHELD. Grid cells: {res['n_cells']}; cell-hazards the old >=35% rule would have "
           f"texted: {res['legacy_rule_would_have_fired']}; eligible under the alert policy: {len(res['eligible'])}. "
           f"Reasons: {res['reason_counts']}. Explanation: {alert_policy.explain(sorted(res['reason_counts']))}")
    print(msg)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### Automated alert dispatch\n" + msg + "\n")
    # An eligible alert would be dispatched here by an approved sender. None can be eligible while the production
    # registries are empty (Q18), so this script never sends.
    return 0


if __name__ == "__main__":
    sys.exit(main())
