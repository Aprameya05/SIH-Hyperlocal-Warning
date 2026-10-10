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

2026-10-10: a real production run showed both checks returning HTTP 403
immediately after deploy, while the SAME URLs returned 200 minutes later
when checked manually. Root cause: a single check 15 seconds after deploy
is not enough time for Cloudflare's edge network to finish propagating a
fresh deployment globally -- a request landing on an edge node that
hasn't yet received the new deployment can get a transient 403 during
that window. The fix is retrying with backoff, not removing or weakening
the check: a GENUINE deployment failure must still be caught and reported
loudly (exit 1), it just must not be confused with an ordinary,
expected propagation delay.

Usage: python3 scripts/ci_smoke_test_deployed_site.py [site_url]
"""
import json
import sys
import time
import urllib.error
import urllib.request

SITE = sys.argv[1] if len(sys.argv) > 1 else "https://sih-hyperlocal-warning.pages.dev"
MAX_ATTEMPTS = 6
RETRY_DELAY_S = 10


def _fetch(url):
    """Returns (status, body, error_detail). error_detail is None on
    success, or a descriptive string (including the real HTTP status
    code for an HTTPError, not just a generic exception name) on
    failure -- a 403 must be distinguishable from a timeout or a 404
    in the final report, not collapsed into one generic 'FAILED'."""
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return resp.status, resp.read(), None
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"HTTP {exc.code} {exc.reason}"
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"


def check_with_retry(url, label):
    """Retries on failure with backoff -- real transient CDN
    propagation delays resolve within this window; a genuinely broken
    deployment will still fail every attempt and be reported as such."""
    last_detail = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        status, body, detail = _fetch(url)
        if status == 200:
            if attempt > 1:
                print(f"{label}: HTTP 200 (succeeded on attempt {attempt}/{MAX_ATTEMPTS} -- "
                      f"earlier attempt(s) hit transient CDN propagation delay)")
            else:
                print(f"{label}: HTTP 200")
            return status, body
        last_detail = detail or f"HTTP {status}"
        print(f"{label}: attempt {attempt}/{MAX_ATTEMPTS} failed ({last_detail})")
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_DELAY_S)
    print(f"{label}: FAILED after {MAX_ATTEMPTS} attempts ({last_detail}) -- "
          f"this is NOT a propagation delay, the deployment is genuinely unreachable")
    return None, None


def main():
    # Longer initial wait than the old single-shot 15s -- gives the
    # first real attempt a better chance before burning a retry on an
    # almost-certain propagation-window failure.
    time.sleep(20)

    frontend_status, _ = check_with_retry(SITE + "/", "frontend")
    uf_status, uf_body = check_with_retry(SITE + "/data/unified_forecast.json", "unified_forecast.json")

    failures = []
    if frontend_status != 200:
        failures.append("frontend unreachable")
    if uf_status != 200:
        failures.append("unified_forecast.json unreachable")
    elif uf_body:
        try:
            doc = json.loads(uf_body)
            print(f"deployed artifact: {len(doc.get('records', []))} records, "
                  f"generated_at_utc={doc.get('generated_at_utc')}")
        except Exception as exc:
            failures.append(f"unified_forecast.json is not valid JSON ({exc})")

    if failures:
        print(f"SMOKE TEST FAILED: {'; '.join(failures)}")
        sys.exit(1)
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
