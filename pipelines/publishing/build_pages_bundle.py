#!/usr/bin/env python3
"""
pipelines/publishing/build_pages_bundle.py
===========================
Builds the Cloudflare Pages bundle from an explicit ALLOW-LIST (never "everything except a deny-list").

Why: the old deploy rsynced the whole repo minus a deny-list, which published data/subscribers.json (a real
name and email), research CSVs, internal state files and stale proxies. Only files the frontend or service
worker actually fetch are published now, and a FORBIDDEN check fails the build if anything sensitive slips in.

Usage:  python3 pipelines/publishing/build_pages_bundle.py [OUT_DIR]      (default: pages_dist)
"""
from __future__ import annotations

import fnmatch
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Single files that are published (relative to the repo root).
ALLOW_FILES = [
    "index.html", "drift.html", "sw.js", "manifest.json",
    "forecast.json", "alert_log.json",
    "data/pan_india_grid.json", "data/canonical_forecast.json", "data/unified_forecast.json",
    "data/drift_live.json", "data/gfs_multiday_43295.json", "data/skill_scores.json",
    "data/blr_terrain.json", "data/hazard_evaluation.json",
    "data/india_grid_025.json", "data/india_places.json", "data/india_fields_latest.json", "data/india_static_025.json",
    "data/india_ts_model_latest.json", "data/india_rain_model_latest.json",
]
# Files published under a different name: index.html references ./night1.png etc. at the site root, but the sources
# live in dev/. They were never published before (the hero backgrounds 404ed); publish them at the referenced path.
ALIAS_FILES = {
    "morning1.png": "frontend/assets/images/morning1.png", "afternoon1.png": "frontend/assets/images/afternoon1.png",
    "evening1.png": "frontend/assets/images/evening1.png", "night1.png": "frontend/assets/images/night1.png",
    "favicon.ico": "frontend/assets/favicon.ico",
}
# Directories published recursively (bundle-relative name -> source directory in the repository).
ALLOW_DIRS = ["assets"]
SOURCE_DIRS = {"assets": "frontend/assets"}
# Frontend entry files live under frontend/ in the repository but are published at the bundle ROOT (Cloudflare Pages
# serves the bundle root; the service worker must be at the root to control the whole site).
FRONTEND_FILES = {"index.html", "drift.html", "sw.js", "manifest.json"}


def source_of(rel: str, repo_root: Path = None) -> Path:
    repo_root = repo_root or REPO_ROOT
    return repo_root / ("frontend/" + rel if rel in FRONTEND_FILES else rel)

# Nothing matching these may ever be in the bundle (checked after the copy; the build fails if found).
FORBIDDEN_GLOBS = [
    "*subscribers*", "*.env", "*secret*", "*credential*", "*.pkl", "*.csv", "*.grib2", "*.grb2",
    "phase_0_4_19_results.json", "*ctt_grid*", "*alert_history*", "*.db",
]
MAX_FILE_BYTES = 25 * 1024 * 1024  # Cloudflare Pages per-file limit


def _write_slim_unified(src: Path, dst: Path) -> None:
    """The public copy omits per-record research/historical references and explanations. They are withheld values
    (never current forecasts), and keeping them pushes the file past the 25 MiB hosting limit. The full artifact
    stays in the repository and behind the API."""
    art = json.load(open(src))
    for r in art.get("records") or []:
        for hz in ("TS", "CB", "FF"):
            b = r.get(hz)
            if isinstance(b, dict):
                for k in ("research_reference", "historical_reference", "xai", "extra"):
                    b.pop(k, None)
    art["published_variant"] = "SLIM_NO_RESEARCH_REFERENCES"
    dst.write_text(json.dumps(art, separators=(",", ":")))


def build(out_dir: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    copied: list[str] = []
    for rel in ALLOW_FILES:
        src = source_of(rel, repo_root)
        if not src.exists():
            continue  # optional files (e.g. drift_live.json before its first build) are simply absent
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if rel == "data/unified_forecast.json":
            _write_slim_unified(src, dst)
        else:
            shutil.copy2(src, dst)
        copied.append(rel)
    for dst_rel, src_rel in ALIAS_FILES.items():
        src = repo_root / src_rel
        if src.exists():
            shutil.copy2(src, out_dir / dst_rel)
            copied.append(dst_rel)
    for d in ALLOW_DIRS:
        base = repo_root / SOURCE_DIRS.get(d, d)
        for src in sorted(base.rglob("*")) if base.exists() else []:
            if src.is_file() and "images" not in src.relative_to(base).parts:      # hero images are published by alias at the root
                rel = d + "/" + str(src.relative_to(base))
                dst = out_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(rel)
    problems = check(out_dir)
    if problems:
        raise SystemExit("ERROR: publish bundle violates the allow-list contract:\n  " + "\n  ".join(problems))
    return copied


def check(out_dir: Path) -> list[str]:
    problems = []
    for f in out_dir.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(out_dir))
        for pat in FORBIDDEN_GLOBS:
            if fnmatch.fnmatch(f.name.lower(), pat) or fnmatch.fnmatch(rel.lower(), pat):
                problems.append(f"forbidden file published: {rel} (matches {pat})")
        if f.stat().st_size > MAX_FILE_BYTES:
            problems.append(f"file over 25 MiB: {rel}")
        if rel not in ALLOW_FILES and rel not in ALIAS_FILES and not any(rel.startswith(d + "/") for d in ALLOW_DIRS):
            problems.append(f"not on the allow-list: {rel}")
    return problems


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "pages_dist"
    files = build(out)
    total = sum((out / f).stat().st_size for f in files)
    print(f"Published {len(files)} files ({total / 1e6:.1f} MB) to {out} from the allow-list:")
    for f in files:
        print("  ", f)
