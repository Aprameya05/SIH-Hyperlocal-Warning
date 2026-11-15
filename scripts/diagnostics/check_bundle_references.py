#!/usr/bin/env python3
"""Checks a built Pages bundle the way Cloudflare Pages serves it (static, case-sensitive, 25 MiB per-file limit):
every local src/href in the HTML pages, every precache entry in sw.js and every data/*.json path named by the frontend
JavaScript must exist with EXACT case; no file may exceed 25 MiB; index.html and drift.html must be present.
Usage: check_bundle_references.py <bundle_dir>   (exit 1 on any problem)"""
import re
import sys
from pathlib import Path

LIMIT = 25 * 1024 * 1024


def exact_exists(root: Path, rel: str) -> bool:
    cur = root
    for part in Path(rel).parts:
        if not cur.is_dir() or part not in {p.name for p in cur.iterdir()}:   # exact-case listing, like Linux
            return False
        cur = cur / part
    return cur.is_file()


def check(bundle: Path) -> list[str]:
    bad = []
    for need in ("index.html", "drift.html", "sw.js", "manifest.json", "404.html"):
        if not (bundle / need).is_file():
            bad.append(f"missing required page/file {need}")
    for p in bundle.rglob("*"):
        if p.is_file() and p.stat().st_size > LIMIT:
            bad.append(f"{p.relative_to(bundle)} is {p.stat().st_size / 2**20:.1f} MiB (> 25 MiB Pages limit)")
    refs = {}
    for page in ("index.html", "drift.html"):
        f = bundle / page
        if f.exists():
            for m in re.finditer(r"""(?:src|href)=["']([^"'#?]+)""", f.read_text(errors="ignore")):
                refs.setdefault(m.group(1), page)
    sw = bundle / "sw.js"
    if sw.exists():
        for m in re.finditer(r"""["'](\./|/)?((?:assets|data)/[\w./-]+\.\w+)["']""", sw.read_text(errors="ignore")):
            refs.setdefault(m.group(2), "sw.js")
    for js in (bundle / "assets").glob("*.js"):
        for m in re.finditer(r"""["'](?:\./|/)?(data/[\w./-]+\.json)["']""", js.read_text(errors="ignore")):
            refs.setdefault(m.group(1), f"assets/{js.name}")
    for ref, origin in sorted(refs.items()):
        if ref.startswith(("http:", "https:", "data:", "mailto:", "//", "javascript:")):
            continue
        rel = ref.lstrip("/").removeprefix("./")
        if rel and not exact_exists(bundle, rel):
            bad.append(f"{origin} references {ref} which is not in the bundle (exact-case)")
    for forbidden in ("data/subscribers.json",):
        if (bundle / forbidden).exists():
            bad.append(f"forbidden file published: {forbidden}")
    return bad


if __name__ == "__main__":
    b = Path(sys.argv[1])
    problems = check(b)
    print("\n".join(problems) or f"OK: {b} bundle references resolve, no file over 25 MiB, no forbidden files")
    sys.exit(1 if problems else 0)
