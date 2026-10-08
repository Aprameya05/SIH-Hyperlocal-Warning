#!/usr/bin/env python3
"""
scripts/ci_validate_unified_artifact.py

Pre-deploy gate for .github/workflows/forecast_update.yml (Priority 8/15,
2026-10-06 production-plumbing pass): refuses to let a malformed or empty
data/unified_forecast.json reach Cloudflare Pages. Exits 1 (CI fails this
step) if the artifact is unreadable or fails any required validation key
that scripts/phase34_build_unified_forecast.py's own validate_artifact()
computes and stores in the artifact itself.

Usage: python3 scripts/ci_validate_unified_artifact.py <path-to-artifact.json>
"""
import json
import sys

REQUIRED_TRUE_KEYS = [
    "exactly_992_cells",
    "exactly_4960_records",
    "all_5_lead_slots_present_per_cell",
    "lead_time_arithmetic_correct_for_every_record",
    "all_present_probabilities_in_0_1_range",
    "ff_never_carries_a_fabricated_probability",
    "no_unavailable_probability_mapped_to_a_risk_category",
]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "pages_dist/data/unified_forecast.json"
    try:
        with open(path) as f:
            doc = json.load(f)
    except Exception as exc:
        print(f"ERROR: unified artifact at {path} is unreadable ({exc}) -- refusing to deploy")
        sys.exit(1)

    validation = doc.get("validation", {})
    failed = [k for k in REQUIRED_TRUE_KEYS if not validation.get(k)]
    if failed:
        print(f"ERROR: unified artifact at {path} failed validation checks: {failed} -- refusing to deploy")
        sys.exit(1)

    print(f"OK: {path} valid -- {doc.get('n_records')} records, "
          f"source_cycle_date={doc.get('source_cycle_date')}, "
          f"generated_at={doc.get('generated_at_utc')}")


if __name__ == "__main__":
    main()
