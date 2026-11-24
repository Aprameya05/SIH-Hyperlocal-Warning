#!/usr/bin/env python3
"""Decide whether the public site must be re-published, and whether any data feed is overdue.

Used by .github/workflows/publish_site.yml. The data workflows (India GFS fields, Himawari-9 satellite) only COMMIT their artifacts;
the site shows them only after a deploy. This compares what is in the repository (what a deploy would publish) with what the
public site currently serves, and reports the age of each feed so the workflow can re-trigger a feed that has gone quiet.

Writes key=value lines to $GITHUB_OUTPUT (and prints them): publish_needed, reason, fields_cycle_age_h, satellite_age_min,
fields_overdue, satellite_overdue. Standard library only. Network failures never block publishing (they mean 'publish').
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SITE = os.environ.get("SITE_URL", "https://sih-hyperlocal-warning.pages.dev").rstrip("/")
ROOT = Path(__file__).resolve().parent.parent
UA = "DRIFT-CI-publish/1.0 (+https://github.com/Aprameya05/SIH-Hyperlocal-Warning)"   # Cloudflare answers 403 to the default urllib agent
FIELDS_OVERDUE_H = 11.0        # a 6-hourly GFS cycle is on the mirror ~4.5 h after init: older than 11 h means a cycle was missed
SAT_OVERDUE_MIN = 75.0         # scans arrive every 10 min; the app withholds satellite values after 90 min


def _live(path: str):
    req = urllib.request.Request(f"{SITE}/{path}?_={int(datetime.now().timestamp())}", headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def _local(path: str):
    p = ROOT / path
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _utc(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def main() -> int:
    now = datetime.now(timezone.utc)
    out: dict[str, str] = {}
    f, s = _local("data/india_fields_latest.json"), _local("data/india_satellite_ctt_latest.json")
    cyc = _utc(f["meta"]["cycle_init_utc"]) if f else None
    scene = _utc(s["meta"]["scene_nominal_utc"]) if s else None
    out["fields_cycle_age_h"] = f"{(now - cyc).total_seconds() / 3600:.1f}" if cyc else "unknown"
    out["satellite_age_min"] = f"{(now - scene).total_seconds() / 60:.0f}" if scene else "unknown"
    out["fields_overdue"] = str(cyc is None or (now - cyc).total_seconds() / 3600 > FIELDS_OVERDUE_H).lower()
    out["satellite_overdue"] = str(scene is None or (now - scene).total_seconds() / 60 > SAT_OVERDUE_MIN).lower()

    reasons = []
    for label, path, getter in (("fields", "data/india_fields_latest.json", lambda d: d["meta"]["generated_at_utc"]),
                                ("satellite", "data/india_satellite_ctt_latest.json", lambda d: d["meta"]["generated_at_utc"]),
                                ("forecast", "forecast.json", lambda d: d.get("generated_at")),
                                ("live feed", "data/drift_live.json", lambda d: d.get("generated_at_utc") or d.get("generated_at"))):
        loc = _local(path)
        if loc is None:
            continue
        try:
            lv = _live(path)
            if getter(lv) != getter(loc):
                reasons.append(f"{label}: site serves {getter(lv)}, repository has {getter(loc)}")
        except Exception as exc:   # unreachable or unreadable site: publishing is the safe answer
            reasons.append(f"{label}: could not read the live copy ({type(exc).__name__})")
    out["publish_needed"] = str(bool(reasons)).lower()
    out["reason"] = "; ".join(reasons) if reasons else "site already serves the repository's artifacts"

    for k, v in out.items():
        print(f"{k}={v}")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as fh:
            for k, v in out.items():
                fh.write(f"{k}={v}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
