#!/usr/bin/env python3
"""
Static regression guard: fails if index.html references a floating
(major-version-only) maplibre-gl CDN URL instead of an exact x.y.z pin.

Why this exists: maplibre-gl@4.7.1 (resolved from an unpinned "@4" tag) was
found to throw ~100+ uncaught internal "Cannot read properties of undefined
(reading 'getLayer')" exceptions per page load (see docs/KNOWN_ISSUES.md).
maplibre-gl@3.6.2 reduces this to a small, parked residual. A floating "@4"
(or any other floating major/minor) tag would silently re-introduce the
regression the next time jsDelivr resolves it to a new release, with no
code change and no diff to review -- so this check exists to catch that
before it reaches the user's working copy.

Usage:
    python3 scripts/check_maplibre_pin.py [path/to/index.html]

Exit code 0 = pinned correctly. Exit code 1 = floating/unpinned reference
found, or the file doesn't reference maplibre-gl at all (which would also
be unexpected and worth a human look).
"""
import re
import sys
from pathlib import Path

EXACT_PIN = re.compile(r"maplibre-gl@\d+\.\d+\.\d+/")
FLOATING = re.compile(r"maplibre-gl@(?!\d+\.\d+\.\d+/)[^/\"']*")


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("index.html")
    if not path.exists():
        print(f"ERROR: {path} not found")
        return 1

    text = path.read_text(encoding="utf-8")
    floating_matches = FLOATING.findall(text)
    exact_matches = EXACT_PIN.findall(text)

    if floating_matches:
        print("FAIL: floating/unpinned maplibre-gl CDN reference(s) found:")
        for m in sorted(set(floating_matches)):
            print(f"  - {m}")
        print(
            "Pin to an exact version (e.g. maplibre-gl@3.6.2) instead. "
            "See docs/KNOWN_ISSUES.md for why this matters."
        )
        return 1

    if not exact_matches:
        print(
            f"WARNING: no maplibre-gl CDN reference found at all in {path}. "
            "If this is expected (library removed/replaced), update this "
            "check; otherwise something looks wrong."
        )
        return 1

    versions = sorted(set(exact_matches))
    print(f"OK: maplibre-gl pinned to exact version(s): {versions}")
    if any(v != "maplibre-gl@3.6.2/" for v in versions):
        print(
            "NOTE: pinned version is not the currently-documented 3.6.2 "
            "baseline. If this was an intentional, tested upgrade, update "
            "docs/KNOWN_ISSUES.md to match. If not, this may be accidental."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
