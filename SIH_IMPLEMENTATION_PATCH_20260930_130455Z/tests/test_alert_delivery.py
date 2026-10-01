#!/usr/bin/env python3
"""
Regression tests for alert_delivery.py. Mocks urllib.request.urlopen --
NO real network calls or live alert sends are performed by this test.
Run: python3 test_alert_delivery.py
"""

import io
import json
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

from alert_delivery import (
    send_whatsapp_tracked, skipped_no_alert, persist_delivery_log, _mask_phone,
)

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class FakeResponse:
    def __init__(self, code):
        self._code = code
    def getcode(self):
        return self._code
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_skipped_no_alert():
    print("test_skipped_no_alert (subscriber below threshold, no digest due)")
    r = skipped_no_alert("+919999999999")
    check("status is SKIPPED_NO_ALERT", r.status == "SKIPPED_NO_ALERT")
    check("no HTTP request implied (http_status is None)", r.http_status is None)
    check("phone is masked", r.subscriber_masked_phone.endswith("9999") and "*" in r.subscriber_masked_phone,
          r.subscriber_masked_phone)


def test_not_configured_missing_credentials():
    print("test_not_configured_missing_credentials")
    r = send_whatsapp_tracked("", "", "hello", trigger="ALERT")
    check("status is NOT_CONFIGURED", r.status == "NOT_CONFIGURED")
    r2 = send_whatsapp_tracked("+919999999999", "", "hello", trigger="ALERT")
    check("status is NOT_CONFIGURED when apikey missing even with phone", r2.status == "NOT_CONFIGURED")


def test_http_2xx_is_success():
    print("test_http_2xx_is_success")
    with mock.patch("alert_delivery.urllib.request.urlopen", return_value=FakeResponse(200)):
        r = send_whatsapp_tracked("+919999999999", "APIKEY", "hello", trigger="ALERT")
    check("status is SUCCESS on HTTP 200", r.status == "SUCCESS")
    check("http_status recorded as 200", r.http_status == 200)


def test_http_failure_is_failed_not_success():
    print("test_http_failure_is_failed_not_success")
    err = urllib.error.HTTPError(url="x", code=500, msg="Internal Server Error", hdrs=None, fp=None)
    with mock.patch("alert_delivery.urllib.request.urlopen", side_effect=err):
        r = send_whatsapp_tracked("+919999999999", "APIKEY", "hello", trigger="ALERT")
    check("status is FAILED on HTTP 500", r.status == "FAILED")
    check("http_status is 500, not silently dropped", r.http_status == 500)
    check("never reports SUCCESS merely because the request was made", r.status != "SUCCESS")


def test_timeout_is_failed_with_timeout_reason():
    print("test_timeout_is_failed_with_timeout_reason")
    with mock.patch("alert_delivery.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        r = send_whatsapp_tracked("+919999999999", "APIKEY", "hello", trigger="ALERT", timeout_s=5)
    check("status is FAILED on timeout", r.status == "FAILED")
    check("reason distinguishes timeout from a generic error", "timeout" in r.reason.lower(), r.reason)


def test_dry_run_never_hits_network():
    print("test_dry_run_never_hits_network")
    with mock.patch("alert_delivery.urllib.request.urlopen") as m:
        r = send_whatsapp_tracked("+919999999999", "APIKEY", "hello", trigger="DIGEST", dry_run=True)
        check("urlopen was never called in dry-run mode", m.call_count == 0)
    check("dry-run reports SUCCESS with an explicit dry-run reason", r.status == "SUCCESS" and "dry-run" in r.reason)


def test_no_secrets_or_full_phone_in_result():
    print("test_no_secrets_or_full_phone_in_result")
    with mock.patch("alert_delivery.urllib.request.urlopen", return_value=FakeResponse(200)):
        r = send_whatsapp_tracked("+919999999999", "SUPER_SECRET_KEY", "hello", trigger="ALERT")
    payload = json.dumps(r.to_json())
    check("apikey never appears in the persisted result", "SUPER_SECRET_KEY" not in payload)
    check("full phone number never appears in the persisted result", "+919999999999" not in payload)


def test_persist_delivery_log_writes_and_accumulates():
    print("test_persist_delivery_log_writes_and_accumulates")
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "alert_delivery_log.json"
        r1 = skipped_no_alert("+911111111111")
        run1 = persist_delivery_log([r1], log_path=log_path, run_meta={"note": "run1"})
        check("first run persisted with 1 run entry", log_path.exists())
        with open(log_path) as f:
            data = json.load(f)
        check("1 run recorded after first persist", len(data["runs"]) == 1)
        check("counts reflect SKIPPED_NO_ALERT=1", run1["counts"]["SKIPPED_NO_ALERT"] == 1)

        r2 = skipped_no_alert("+912222222222")
        persist_delivery_log([r2], log_path=log_path, run_meta={"note": "run2"})
        with open(log_path) as f:
            data2 = json.load(f)
        check("2 runs recorded after second persist (history preserved, not overwritten)",
              len(data2["runs"]) == 2)


def test_mask_phone():
    print("test_mask_phone")
    check("masks all but last 4 digits", _mask_phone("+919876543210") == "*********3210", _mask_phone("+919876543210"))
    check("empty phone masks to empty string", _mask_phone("") == "")


if __name__ == "__main__":
    test_skipped_no_alert()
    test_not_configured_missing_credentials()
    test_http_2xx_is_success()
    test_http_failure_is_failed_not_success()
    test_timeout_is_failed_with_timeout_reason()
    test_dry_run_never_hits_network()
    test_no_secrets_or_full_phone_in_result()
    test_persist_delivery_log_writes_and_accumulates()
    test_mask_phone()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed -> {FAILURES}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
