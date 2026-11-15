#!/usr/bin/env python3
"""
End-to-end release-readiness gate for a built publish bundle. Fails closed (exit 1) unless EVERY check passes:

  1. required files are in the bundle and nothing forbidden is (allow-list contract)
  2. the national fields artifact re-verifies independently (valid times, publication times, APCP windows, ranges, grid hash)
  3. the GFS cycle is fresh on the SERVER clock (--max-age-hours)
  4. the model artifacts were produced from that same cycle (or are explicitly withheld with a reason)
  5. the live feed and unified artifact validators pass
  6. alert dispatch is disabled by policy

Usage: check_release_readiness.py BUNDLE_DIR [--max-age-hours 20]
A workflow whose individual steps are continue-on-error is NOT proof of health; this gate is.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.location_service import Store  # noqa: E402
from pipelines.publishing import build_pages_bundle as bpb  # noqa: E402

REQUIRED = ["index.html", "drift.html", "sw.js", "data/india_grid_025.json", "data/india_fields_latest.json", "data/india_static_025.json",
            "data/india_ts_model_latest.json", "data/india_rain_model_latest.json", "data/drift_live.json", "data/hazard_evaluation.json",
            "assets/drift_app.js", "assets/drift_location.js", "assets/drift_sim.js", "assets/vendor/maplibre-gl.js"]


def run(cmd):
    r = subprocess.run([sys.executable, *cmd], cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--max-age-hours", type=float, default=20.0)
    a = ap.parse_args()
    b = Path(a.bundle).resolve()
    checks = []
    missing = [f for f in REQUIRED if not (b / f).is_file()]
    checks.append(("required files present", not missing, f"missing: {missing}" if missing else f"{len(REQUIRED)} required files"))
    prob = bpb.check(b)
    checks.append(("allow-list / forbidden / size contract", not prob, "; ".join(prob[:2]) if prob else "clean"))
    if not missing:
        s = Store(b / "data")
        h = s.health(now=datetime.now(timezone.utc))
        fields = h["components"].get("gfs_fields", {})
        checks.append(("national fields verify independently", s.fields_ok, "; ".join(s.fields_problems[:2]) if not s.fields_ok else "all provenance re-derived"))
        checks.append((f"GFS cycle fresh (<= {a.max_age_hours} h, server clock)", fields.get("age_hours", 1e9) <= a.max_age_hours, f"age {fields.get('age_hours')} h"))
        for m in ("thunderstorm_model", "rainfall_model"):
            c = h["components"][m]
            checks.append((f"{m} consistent with fields cycle", c["status"] in ("OK", "WITHHELD"), f"{c['status']} {c.get('reason') or ''}".strip()))
        checks.append(("alert dispatch disabled by policy", h["components"]["alert_policy"]["automated_dispatch"] == "DISABLED", "fail-closed"))
        ok, tail = run(["pipelines/validation/validate_live_feed.py", str(b / "data/drift_live.json"), "--max-age-minutes", str(int(a.max_age_hours * 60))])
        checks.append(("live feed validator", ok, tail[0][:120]))
        ok, tail = run(["pipelines/validation/validate_national_fields.py", str(b / "data"), "--max-age-hours", str(a.max_age_hours)])
        checks.append(("national fields validator", ok, tail[0][:120]))
    w = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(w)}  {detail}")
    if not all(c[1] for c in checks):
        print("NOT READY: fail closed, nothing may be deployed")
        sys.exit(1)
    print("READY")


if __name__ == "__main__":
    main()
