"""The bundle's root is the modern dashboard, and returning visitors cannot be stuck on a cached legacy page."""
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from pipelines.publishing import build_pages_bundle as bpb  # noqa: E402


def test_bundle_root_is_modern_dashboard_and_legacy_is_preserved():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "b"
        files = bpb.build(out)
        assert {"index.html", "drift.html", "legacy.html"} <= set(files)
        modern = (REPO / "frontend" / "drift.html").read_text(encoding="utf-8")
        legacy = (REPO / "frontend" / "index.html").read_bytes()
        assert (out / "legacy.html").read_bytes() == legacy             # legacy page published unchanged
        assert (out / "index.html").read_text(encoding="utf-8") == (out / "drift.html").read_text(encoding="utf-8")
        assert 'href="legacy.html"' in (out / "index.html").read_text(encoding="utf-8")
        assert modern.replace('href="index.html"', 'href="legacy.html"') == (out / "index.html").read_text(encoding="utf-8")
        assert bpb.check(out) == []


def test_service_worker_cache_bumped_and_entry_pages_network_first():
    sw = (REPO / "frontend" / "sw.js").read_text(encoding="utf-8")
    m = re.search(r"CACHE_NAME = 'drift-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 6, "cache version must stay bumped so clients drop the cached legacy index.html"
    assert "'/'" in sw and "html|js|css|json|geojson" in sw     # entry pages, scripts and data are network-first, never served stale cache-first
    assert "drift(_app|_sim)" not in sw                          # the old allow-list let drift_location.js be cache-first (stale-script bug)
