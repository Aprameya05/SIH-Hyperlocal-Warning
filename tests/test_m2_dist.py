"""Tests the BUILT publish bundle (what Cloudflare would serve), not the dev tree."""
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from pipelines.publishing import build_pages_bundle as bpd  # noqa: E402
from backend import forecast_integrity as fi  # noqa: E402


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    d = tmp_path_factory.mktemp("dist") / "pages_dist"
    bpd.build(d)
    return d


def _refs(text):
    return set(re.findall(r"""(?:src|href)=["']([^"'#?]+)""", text)) | set(re.findall(r"""fetch\(\s*['"]([^'"?]+)""", text))


def test_entry_point_links_both_ways(dist):
    index, drift, legacy = [(dist / n).read_text() for n in ("index.html", "drift.html", "legacy.html")]
    assert index == drift                                            # the site root IS the modern dashboard
    assert 'href="legacy.html"' in index and 'href="index.html"' not in index   # legacy stays reachable, no self-link
    assert 'href="drift.html"' in legacy and "DRIFT PAN-INDIA COMMAND CENTRE" in legacy


@pytest.mark.parametrize("page", ["drift.html", "index.html", "legacy.html"])
def test_every_local_reference_exists_in_the_bundle(dist, page):
    text = (dist / page).read_text()
    if page in ("drift.html", "index.html"):
        text += (dist / "assets/drift_app.js").read_text()
    missing = [r for r in _refs(text) if not r.startswith(("http", "data:", "//", "/", "mailto", "javascript")) and
               not (dist / r).exists() and r not in ("forecast/all",)]
    assert missing == [], missing


def test_drift_assets_and_all_fetched_json_are_published(dist):
    for f in ("drift.html", "assets/drift_app.js", "assets/drift_sim.js", "assets/india_outline.geojson",
              "data/drift_live.json", "data/hazard_evaluation.json", "data/pan_india_grid.json", "data/unified_forecast.json"):
        assert (dist / f).is_file(), f
    for p in re.findall(r"j\('([^']+)'\)", (dist / "assets/drift_app.js").read_text()):
        assert (dist / p).is_file(), p


def test_validators_pass_on_the_bundle_at_its_own_generation_time(dist):
    """Ages are evaluated at the artifacts' generation time so this test does not rot as wall-clock time passes."""
    art = json.load(open(dist / "data/unified_forecast.json"))
    gen = fi.parse_utc(art["generated_at_utc"])
    rep = fi.verify_artifact_report(art, now=gen + timedelta(minutes=5))
    assert rep["ok"], rep["counts_by_code"]
    assert fi.verify_grid_report(json.load(open(dist / "data/pan_india_grid.json")))["ok"]
    from pipelines.validation import validate_live_feed as v
    live = json.load(open(dist / "data/drift_live.json"))
    assert v.check(dist / "data/drift_live.json", max_age_min=1e9, require_cells=True) == []
    assert (dist / "data/hazard_evaluation.json").exists() and live["alerts"]["automated_dispatch"] == "DISABLED"


def test_bundle_leaks_nothing_and_has_headroom(dist):
    assert bpd.check(dist) == []
    email = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)
    for f in dist.rglob("*.json"):
        assert not email.search(f.read_text(errors="ignore")), f"e-mail address in published {f.name}"
    assert max(f.stat().st_size for f in dist.rglob("*") if f.is_file()) <= 0.85 * bpd.MAX_FILE_BYTES
    assert not list(dist.rglob("*subscribers*")) and not list(dist.rglob("*.csv")) and not list(dist.rglob("*.pkl"))


def test_service_worker_never_serves_stale_data_or_drift_pages():
    sw = (ROOT / "frontend" / "sw.js").read_text()
    assert "networkFirst" in sw and "/data/" in sw and "drift" in sw


def test_hero_background_images_referenced_by_the_homepage_are_published(dist):
    text = (dist / "legacy.html").read_text()
    for img in set(re.findall(r"\./([A-Za-z0-9_-]+\.png)", text)):
        assert (dist / img).is_file(), f"index.html references ./{img} but it is not published"


def test_release_readiness_gate_passes_on_a_good_bundle_and_fails_closed_on_damage(dist, tmp_path):
    import shutil
    import subprocess
    import sys as _sys
    gate = [_sys.executable, str(ROOT / "pipelines/validation/check_release_readiness.py")]
    ok = subprocess.run([*gate, str(dist), "--max-age-hours", "100000"], capture_output=True, text=True)
    assert ok.returncode == 0 and "READY" in ok.stdout, ok.stdout
    bad = tmp_path / "bad"; shutil.copytree(dist, bad)
    (bad / "data/india_fields_latest.json").unlink()
    r = subprocess.run([*gate, str(bad)], capture_output=True, text=True)
    assert r.returncode == 1 and "NOT READY" in r.stdout
    bad2 = tmp_path / "bad2"; shutil.copytree(dist, bad2)
    (bad2 / "data" / "subscribers.json").write_text("[]")                         # a forbidden file in the bundle
    assert subprocess.run([*gate, str(bad2), "--max-age-hours", "100000"], capture_output=True, text=True).returncode == 1
    r = subprocess.run([*gate, str(dist), "--max-age-hours", "0.001"], capture_output=True, text=True)   # an old cycle is not ready
    assert r.returncode == 1 and "FAIL" in r.stdout
