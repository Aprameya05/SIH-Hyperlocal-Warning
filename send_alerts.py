#!/usr/bin/env python3
"""
send_alerts.py — CSIR Thunderstorm Alert Delivery (WhatsApp only)
Runs after forecast generation in forecast_update.yml.

Reads subscribers from the Cloudflare Worker API (live self-service list).
Falls back to data/subscribers.json if Worker is unreachable.

Sends WhatsApp via CallMeBot API.
Subscribers must have a phone number and whatsapp_apikey set.

Env vars (set as GitHub Secrets):
  WORKER_URL          — https://csir-ts-alerts.YOUR_SUBDOMAIN.workers.dev
  WORKER_ADMIN_KEY    — matches ADMIN_KEY secret in Cloudflare Worker

Usage:
    python send_alerts.py
    python send_alerts.py --dry-run
    python send_alerts.py --force-digest
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR      = Path("data")
FORECAST_PATH = Path("forecast.json")   # root-level, not data/
FALLBACK_SUB  = DATA_DIR / "subscribers.json"
IST_OFF       = timedelta(hours=5, minutes=30)


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_ist_str() -> str:
    return (datetime.now(timezone.utc) + IST_OFF).strftime("%Y-%m-%d %H:%M IST")


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        print(f"[alerts] ERROR reading {path}: {e}")
        return None


def risk_label(p: float) -> str:
    if p >= 0.55: return "HIGH"
    if p >= 0.35: return "MODERATE"
    if p >= 0.15: return "LOW"
    return "MINIMAL"


def risk_emoji(p: float) -> str:
    if p >= 0.55: return "🔴"
    if p >= 0.35: return "🟠"
    if p >= 0.15: return "🟡"
    return "🟢"


# ── Subscriber list from Worker ────────────────────────────────────────────────

def fetch_subscribers_from_worker(worker_url: str, admin_key: str) -> list[dict] | None:
    try:
        url = worker_url.rstrip('/') + '/subscribers'
        req = urllib.request.Request(url, headers={
            'X-Admin-Key': admin_key,
            'User-Agent':  'CSIR-TS-Pipeline/1.0',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        subscribers = data.get('subscribers', [])
        print(f"[alerts] Loaded {len(subscribers)} subscriber(s) from Worker")
        return subscribers
    except Exception as e:
        print(f"[alerts] Worker fetch failed: {e}")
        return None


def load_subscribers(worker_url: str, admin_key: str) -> list[dict]:
    if worker_url and admin_key:
        subs = fetch_subscribers_from_worker(worker_url, admin_key)
        if subs is not None:
            return subs
    fallback = load_json(FALLBACK_SUB)
    if fallback:
        print(f"[alerts] Using fallback subscribers.json ({len(fallback)} entries)")
        return fallback
    print("[alerts] No subscribers found.")
    return []


# ── WhatsApp via CallMeBot ────────────────────────────────────────────────────

def send_whatsapp(phone: str, apikey: str, message: str, dry_run: bool) -> bool:
    if not phone or not apikey:
        return False
    if dry_run:
        print(f"[alerts] DRY-RUN WhatsApp → {phone}: {message[:60]}...")
        return True
    try:
        url = (f"https://api.callmebot.com/whatsapp.php"
               f"?phone={urllib.parse.quote(phone)}"
               f"&text={urllib.parse.quote(message)}"
               f"&apikey={urllib.parse.quote(apikey)}")
        req = urllib.request.Request(url, headers={"User-Agent": "CSIR-TS/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.getcode() == 200
        if ok:
            print(f"[alerts]  ✓ WhatsApp → {phone}")
        else:
            print(f"[alerts]  ✗ WhatsApp HTTP error → {phone}")
        return ok
    except Exception as e:
        print(f"[alerts]  ✗ WhatsApp FAILED → {phone}: {e}")
        return False


# ── Message builder ───────────────────────────────────────────────────────────

def build_message(forecast: dict, sub: dict, is_alert: bool) -> str:
    peak_prob = forecast.get("peak_probability", 0)
    peak_slot = forecast.get("peak_slot", "—")
    gen_ist   = forecast.get("generated_at_ist", now_ist_str())
    name      = sub.get("name", "Researcher")
    slots     = forecast.get("slots", [])
    risk      = risk_label(peak_prob)
    emoji     = risk_emoji(peak_prob)

    slots_txt = "  |  ".join(
        f"{s.get('slot_label', s.get('slot_id','?'))}: {s.get('ts_probability',0)*100:.0f}%"
        for s in slots
    )

    if is_alert:
        return (
            f"{emoji} IMD TS ALERT — VOBL/43295\n"
            f"Hi {name}, thunderstorm probability has exceeded your threshold!\n"
            f"Risk: {risk}  |  Peak: {peak_prob*100:.0f}%  |  Slot: {peak_slot}\n"
            f"{slots_txt}\n"
            f"Updated: {gen_ist}\n"
            f"Dashboard: https://csir-thunderstorm-bengaluru.pages.dev"
        )
    else:
        return (
            f"☀️ IMD TS Daily Digest — VOBL/43295\n"
            f"Good morning {name}! Today's 6-hour outlook:\n"
            f"Peak risk: {risk}  |  {peak_prob*100:.0f}%\n"
            f"{slots_txt}\n"
            f"Updated: {gen_ist}\n"
            f"Dashboard: https://csir-thunderstorm-bengaluru.pages.dev"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-digest", action="store_true")
    args = ap.parse_args()

    forecast = load_json(FORECAST_PATH)
    if forecast is None:
        print("[alerts] ERROR: forecast.json not found — skipping")
        sys.exit(0)

    worker_url = os.environ.get("WORKER_URL", "")
    admin_key  = os.environ.get("WORKER_ADMIN_KEY", "")

    subscribers = load_subscribers(worker_url, admin_key)
    if not subscribers:
        print("[alerts] No subscribers — done.")
        sys.exit(0)

    peak_prob  = forecast.get("peak_probability", 0)
    gen_ist    = forecast.get("generated_at_ist", now_ist_str())
    now_ist    = datetime.now(timezone.utc) + IST_OFF
    is_morning = 8 <= now_ist.hour < 10

    print(f"[alerts] Forecast peak={peak_prob*100:.0f}% | {gen_ist} | {len(subscribers)} subscriber(s)")

    sent = 0
    for sub in subscribers:
        name      = sub.get("name", "Researcher")
        phone     = sub.get("phone", "")
        wa_key    = sub.get("whatsapp_apikey", "")
        threshold = sub.get("threshold", 30) / 100.0
        do_digest = sub.get("daily_digest", True)

        alert_fire  = peak_prob >= threshold
        digest_fire = do_digest and (is_morning or args.force_digest)

        if not alert_fire and not digest_fire:
            print(f"[alerts]  · {name}: skip (prob {peak_prob*100:.0f}% < {threshold*100:.0f}%, no digest)")
            continue

        if not phone or not wa_key:
            print(f"[alerts]  · {name}: skip (no phone/apikey configured)")
            continue

        is_alert = alert_fire
        msg      = build_message(forecast, sub, is_alert=is_alert)

        print(f"[alerts]  → {name} | {'ALERT' if is_alert else 'DIGEST'} | {phone}")
        if send_whatsapp(phone, wa_key, msg, args.dry_run):
            sent += 1

    print(f"[alerts] Done. {sent} WhatsApp message(s) sent.")


if __name__ == "__main__":
    main()
