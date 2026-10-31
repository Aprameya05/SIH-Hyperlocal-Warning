#!/usr/bin/env python3
"""
scripts/ci_validate_unified_artifact.py
=======================================
Fail-closed pre-deploy gate for data/unified_forecast.json. It does NOT trust the artifact's own `validation`
block: every rule is re-derived from the data by backend.forecast_integrity.verify_artifact_report().

  ci_validate_unified_artifact.py PATH [--marker FILE --max-age-minutes N] [--ok-file FILE]

Structural checks (kept from the earlier gate): file readable; mtime newer than the pre-build marker (proves this run
wrote it); generated_at within N minutes; exactly 4960 records / 992 cells x 5 leads. Added: the integrity contract
(timestamps, provenance, lead semantics, withheld values, PU research-only, label cross-checks).

There is deliberately NO report-only mode in CI: any violation exits 1. (A local-development `--report-only` exists
and is refused when CI / GITHUB_ACTIONS is set.)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import forecast_integrity as fi  # noqa: E402


def fail(msg, ok_file):
    print(f"NOT OK: {msg}")
    if ok_file:
        Path(ok_file).write_text("false")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="pages_dist/data/unified_forecast.json")
    ap.add_argument("--marker")
    ap.add_argument("--max-age-minutes", type=float)
    ap.add_argument("--ok-file")
    ap.add_argument("--report-only", action="store_true", help="local development only; refused in CI")
    a = ap.parse_args()
    if a.report_only and (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")):
        print("ERROR: --report-only is refused in CI (production gates fail closed)")
        sys.exit(2)
    path = Path(a.path)
    if a.marker:
        try:
            marker = int(Path(a.marker).read_text().strip())
        except Exception as exc:
            fail(f"pre-build marker unreadable ({exc}); cannot prove this run wrote the artifact", a.ok_file)
        if path.exists() and path.stat().st_mtime < marker:
            fail(f"{path} mtime is OLDER than the pre-build marker: untouched last-known-good artifact", a.ok_file)
    try:
        art = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        fail(f"{path} unreadable ({type(exc).__name__}: {exc})", a.ok_file)
    gen = fi.parse_utc(art.get("generated_at_utc"))
    if gen is None:
        fail(f"generated_at_utc missing or unparseable: {art.get('generated_at_utc')!r}", a.ok_file)
    age = (datetime.now(timezone.utc) - gen).total_seconds() / 60.0
    if a.max_age_minutes is not None and age > a.max_age_minutes:
        fail(f"artifact is {age:.1f} min old (limit {a.max_age_minutes}) - not freshly written this run", a.ok_file)
    recs = art.get("records", [])
    cells = {r.get("cell_id") for r in recs}
    leads = {}
    for r in recs:
        leads.setdefault(r.get("cell_id"), set()).add(r.get("lead_hours"))
    if len(recs) != 4960 or len(cells) != 992 or any(len(v) != 5 for v in leads.values()):
        fail(f"structure: {len(recs)} records / {len(cells)} cells (expected 4960 / 992 x 5 leads)", a.ok_file)
    rep = fi.verify_artifact_report(art)
    if not rep["ok"]:
        print(f"NOT OK: {rep['n_violations']} integrity violations (re-derived; the artifact's own validation block "
              f"is ignored): {rep['counts_by_code']}")
        for x in rep["violations"][:8]:
            print(f"   - {x['code']}  {x['path']}  {x['detail']}")
        if a.report_only:
            print("(report-only, local development: exiting 0)")
            sys.exit(0)
        fail("artifact violates the integrity contract", a.ok_file)
    print(f"OK: {path} valid - {len(recs)} records, mode={art.get('artifact_mode')}, generated {age:.1f} min ago, "
          f"integrity contract v{art.get('schema_version')} re-derived with 0 violations")
    if a.ok_file:
        Path(a.ok_file).write_text("true")


if __name__ == "__main__":
    main()
