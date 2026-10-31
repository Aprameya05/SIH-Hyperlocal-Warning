#!/usr/bin/env python3
"""Fail-closed gate for data/pan_india_grid.json against the GRID contract (its own schema; not the unified one).
Usage: ci_validate_pan_india_grid.py PATH [--report-only (local development only; refused in CI)]"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import forecast_integrity as fi  # noqa: E402


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    report_only = "--report-only" in sys.argv
    if report_only and (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")):
        print("ERROR: --report-only is refused in CI (production gates fail closed)")
        sys.exit(2)
    path = Path(args[0] if args else "data/pan_india_grid.json")
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        print(f"NOT OK: {path} unreadable ({exc})")
        sys.exit(1)
    rep = fi.verify_grid_report(doc)
    if not rep["ok"]:
        print(f"NOT OK: {rep['n_violations']} grid violations (re-derived): {rep['counts_by_code']}")
        for x in rep["violations"][:8]:
            print(f"   - {x['code']}  {x['detail']}")
        sys.exit(0 if report_only else 1)
    print(f"OK: {path} satisfies grid contract v{doc.get('grid_contract_version')} "
          f"(generated {doc.get('generated_at_utc')}, cycle {doc.get('gfs_cycle')})")


if __name__ == "__main__":
    main()
