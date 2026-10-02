#!/usr/bin/env python3
"""
validate_panindia_labels.py

Builds the Phase 4 coverage/validation report from the actual outputs of
build_panindia_ts_labels.py, build_panindia_cb_labels.py, and
build_panindia_ff_labels.py (must be run first). Computes real coverage
percentages and class-imbalance figures -- nothing here is estimated.

Output: processed/labels/coverage_report.json
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LABELS_DIR = REPO_ROOT / "processed" / "labels"


def main():
    ts_summary = json.loads((LABELS_DIR / "ts_labels_summary.json").read_text())
    cb_summary = json.loads((LABELS_DIR / "cb_labels_summary.json").read_text())
    ff_summary = json.loads((LABELS_DIR / "ff_labels_summary.json").read_text())

    report = {
        "TS": {
            "geographic_coverage": f"1/{ts_summary['total_canonical_cells']} canonical cells "
                                    f"({ts_summary['pan_india_coverage_pct']}%) -- VOBL only",
            "positive_events": ts_summary["vobl_positive"],
            "negative_confirmed": ts_summary["vobl_negative"],
            "unknown_cells": ts_summary["other_cells_marked_unknown"],
            "class_imbalance": round(
                ts_summary["vobl_positive"] / (ts_summary["vobl_positive"] + ts_summary["vobl_negative"]) * 100, 3
            ),
        },
        "CB": {
            "geographic_coverage": f"{cb_summary['canonical_cells_with_imd_coverage']}/{cb_summary['total_canonical_cells']} "
                                    f"canonical cells have IMD rain data (genuinely pan-India)",
            "positive_cell_days": cb_summary["cell_days_positive"],
            "negative_confirmed_cell_days": cb_summary["cell_days_negative_confirmed"],
            "missing_cell_days": cb_summary["cell_days_missing"],
            "class_imbalance_pct": cb_summary["positive_rate_pct"],
            "temporal_resolution": cb_summary["temporal_resolution"],
        },
        "FF": {
            "event_based": ff_summary["genuine_event_based_ff"],
            "rainfall_proxy": ff_summary["rainfall_proxy_ff"],
            "warning": "The event-based figure is UNKNOWN everywhere, not a class-imbalance figure -- "
                       "do not read this as '0% FF events found'. The proxy figure is NOT an observed-flood rate.",
        },
        "overall_note": "No hazard has genuine pan-India POSITIVE/NEGATIVE_CONFIRMED coverage: "
                         "TS is VOBL-only (0.1% of cells), CB is genuinely pan-India but daily-resolution "
                         "and rainfall-only (not a multimodal ML label), FF has no genuine event-based "
                         "labels anywhere due to a missing coordinate file.",
    }

    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    (LABELS_DIR / "coverage_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nWritten: {LABELS_DIR / 'coverage_report.json'}")


if __name__ == "__main__":
    main()
