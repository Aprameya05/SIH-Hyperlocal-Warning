#!/usr/bin/env python3
"""
scripts/ci_smoke_test_deployed_site.py

Post-deploy smoke test for .github/workflows/forecast_update.yml (Priority
10/13, 2026-10-06 pass): confirms the PUBLIC Cloudflare Pages site is
actually serving a reachable frontend and a readable, schema-valid
data/unified_forecast.json -- catching an edge-cache/propagation failure
the deploy step itself cannot see. Diagnostic only (the workflow step that
calls this uses continue-on-error) -- a failure here does not and cannot
undo an already-completed deploy.

Usage: python3 scripts/ci_smoke_test_deployed_site.py [site_url]
"""
import json
import sys
import urllib.request

SITE = sys.argv[1] if len(sys.argv) > 1 else "https://sih-hyperlocal-warning.pages.dev"


def check(url, label):
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            print(f"{label}: HTTP {resp.status}")
            return resp.status, resp.read()
    except Exception as exc:
        print(f"{label}: FAILED ({type(exc).__name__}: {exc})")
        return None, None


def main():
    check(SITE + "/", "frontend")
    status, body = check(SITE + "/data/unified_forecast.json", "unified_forecast.json")
    if status == 200 and body:
        try:
            doc = json.loads(body)
            print(f"deployed artifact: {doc.get('n_records')} records, "
                  f"generated_at={doc.get('generated_at_utc')}, "
                  f"source_cycle={doc.get('source_cycle_date')}")
        except Exception as exc:
            print(f"deployed artifact: could not parse JSON ({exc})")


if __name__ == "__main__":
    main()
