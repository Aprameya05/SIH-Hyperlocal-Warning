#!/usr/bin/env python3
"""Fail-closed validator for data/india_satellite_ctt_latest.json (Himawari-9 B13 brightness temperature per cell).

Re-derives every rule from the data (the artifact's own claims are not trusted): schema, cell alignment with the national grid,
array lengths, physical ranges, rate plausibility, coverage, age, and the honesty flags (observation, not a retrieved CTT).
Usage: validate_satellite_ctt.py [path] [--max-age-minutes N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "data" / "india_satellite_ctt_latest.json"
GRID = ROOT / "data" / "india_grid_025.json"
MIN_COVERAGE = 0.90        # share of cells that must have a valid p10 BT (India is entirely inside the footprint)
MAX_ABS_RATE = 120.0       # K/h; a physically possible convective cooling is far smaller, beyond this it is a data error


def _parse(t):
    try:
        return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def problems(doc: dict, now: datetime | None = None, max_age_minutes: float | None = None, grid_ids: list | None = None) -> list[str]:
    bad: list[str] = []
    m = doc.get("meta") or {}
    if m.get("schema") != "drift_satellite_ctt_v1":
        bad.append("schema is not drift_satellite_ctt_v1")
    if m.get("is_observation") is not True or m.get("is_forecast") is not False or m.get("is_retrieved_cloud_top_temperature") is not False:
        bad.append("honesty flags missing: must be observation, not forecast, not a retrieved CTT")
    ids = doc.get("cells") or []
    if grid_ids is None and GRID.exists():
        grid_ids = [c[0] for c in json.load(open(GRID, encoding="utf-8"))["cells"]]
    if grid_ids is not None and ids != grid_ids:
        bad.append("cells do not match data/india_grid_025.json (order or membership)")
    if m.get("cells_sha256") != hashlib.sha256("|".join(ids).encode()).hexdigest():
        bad.append("cells_sha256 does not match the cell list")
    n = len(ids)
    arrays = ("ctt_p10_k", "ctt_min_k", "cold_frac_235k", "n_pixels", "dtdt_30min_k_per_h", "dtdt_60min_k_per_h", "view_zenith_deg")
    for k in arrays:
        a = doc.get(k)
        if not isinstance(a, list) or len(a) != n:
            bad.append(f"{k}: missing or wrong length")
            return bad
    lo, hi = m.get("valid_bt_range_k") or [150.0, 340.0]
    for k in ("ctt_p10_k", "ctt_min_k"):
        for i, v in enumerate(doc[k]):
            if v is not None and not (isinstance(v, (int, float)) and math.isfinite(v) and lo <= v <= hi):
                bad.append(f"{k}[{i}]={v} outside {lo}-{hi} K")
                break
    for i, (p, mn) in enumerate(zip(doc["ctt_p10_k"], doc["ctt_min_k"])):
        if (p is None) != (mn is None) or (p is not None and mn > p + 1e-6):
            bad.append(f"cell {i}: min BT must exist with p10 and not exceed it")
            break
    for i, v in enumerate(doc["cold_frac_235k"]):
        if v is not None and not 0.0 <= v <= 1.0:
            bad.append(f"cold_frac_235k[{i}]={v} outside 0-1")
            break
    for k in ("dtdt_30min_k_per_h", "dtdt_60min_k_per_h"):
        for i, v in enumerate(doc[k]):
            if v is not None and (not math.isfinite(v) or abs(v) > MAX_ABS_RATE):
                bad.append(f"{k}[{i}]={v} implausible (> {MAX_ABS_RATE} K/h)")
                break
            if v is not None and doc["ctt_p10_k"][i] is None:
                bad.append(f"{k}[{i}] present without a current BT")
                break
    cov = sum(v is not None for v in doc["ctt_p10_k"]) / max(n, 1)
    if cov < MIN_COVERAGE:
        bad.append(f"only {cov:.0%} of cells have a valid BT (< {MIN_COVERAGE:.0%}): partial scene")
    scene = _parse(m.get("scene_nominal_utc"))
    if scene is None:
        bad.append("scene_nominal_utc missing or malformed")
    elif max_age_minutes is not None:
        age = ((now or datetime.now(timezone.utc)) - scene).total_seconds() / 60.0
        if age < -1:
            bad.append("scene is in the future")
        elif age > max_age_minutes:
            bad.append(f"scene is {age:.0f} min old (> {max_age_minutes:.0f}): stale")
    fr = m.get("frames") or {}
    if "t0" not in fr or not fr["t0"].get("files"):
        bad.append("provenance for the current frame is missing")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(DEFAULT))
    ap.add_argument("--max-age-minutes", type=float)
    a = ap.parse_args(argv)
    try:
        doc = json.load(open(a.path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("NOT OK: unreadable:", e)
        return 1
    bad = problems(doc, max_age_minutes=a.max_age_minutes)
    if bad:
        print("NOT OK:", *bad, sep="\n  - ")
        return 1
    m = doc["meta"]
    print(f"OK: {len(doc['cells'])} cells, scene {m['scene_nominal_utc']}, "
          f"{sum(v is not None for v in doc['ctt_p10_k'])} with BT, {sum(v is not None for v in doc['dtdt_60min_k_per_h'])} with a 60-min rate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
