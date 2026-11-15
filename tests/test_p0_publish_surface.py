"""
P0: the public bundle is an explicit allow-list; subscriber PII is never published.
Regression for: data/subscribers.json (a real name and email) was deployed by the old deny-list rsync.
"""
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from pipelines.publishing import build_pages_bundle as bpd  # noqa: E402


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    out = tmp_path_factory.mktemp("pages")
    files = bpd.build(out)
    return out, files


def test_subscribers_file_is_not_published(bundle):
    out, _ = bundle
    assert not [p for p in out.rglob("*") if "subscriber" in p.name.lower()]


def test_tracked_subscribers_file_holds_no_personal_data():
    data = json.loads((REPO / "data" / "subscribers.json").read_text())
    assert data == []                                   # PII removed from the working tree
    assert "@" not in (REPO / "data" / "subscribers.json").read_text()


def test_nothing_sensitive_or_research_is_published(bundle):
    out, _ = bundle
    bad = [p.name for p in out.rglob("*") if p.is_file() and (p.suffix in (".csv", ".pkl", ".db", ".env")
           or "ctt_grid" in p.name or "alert_history" in p.name or p.name == "phase_0_4_19_results.json")]
    assert bad == []


def test_only_allow_listed_paths_exist(bundle):
    out, files = bundle
    on_disk = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    assert on_disk == set(files)
    assert bpd.check(out) == []


def test_every_json_the_frontend_fetches_is_allow_listed():
    """The allow-list must not silently break the site: every ./data/*.json or root json the page or service
    worker fetches by literal path has to be published."""
    texts = (REPO / "frontend" / "index.html").read_text() + (REPO / "frontend" / "sw.js").read_text() + (REPO / "frontend" / "drift.html").read_text() \
        if (REPO / "frontend" / "drift.html").exists() else (REPO / "frontend" / "index.html").read_text() + (REPO / "frontend" / "sw.js").read_text()
    wanted = set()
    for m in re.finditer(r"""['"`]\.?/?((?:data/)?[A-Za-z0-9_]+\.json)""", texts):
        wanted.add(m.group(1))
    # only files that exist in the repo are meaningful to require (others are API paths or generated at runtime)
    missing = sorted(w for w in wanted if (REPO / w).exists() and w not in bpd.ALLOW_FILES)
    # known non-fetched references (comments/strings) that are intentionally NOT published
    ignorable = {"data/subscribers.json", "data/alert_history.json", "data/pipeline_health.json",
                 "data/weekly_digest.json", "data/verification_today.json", "data/realtime_shap.json",
                 "data/himawari_realtime.json", "data/himawari_history.json", "data/himawari_b08_features.json",
                 "data/cape_climatology.json", "data/gfs_history_43295.json", "data/ctt_grid.json",
                 "data/pan_india_common_grid_992.json", "data/pan_india_terrain_992.json",
                 "data/pan_india_terrain_srtm_992.json", "phase_0_4_19_results.json"}
    assert [m for m in missing if m not in ignorable] == []


def test_workflow_uses_the_allow_list_builder_not_a_deny_list_rsync():
    wf = (REPO / ".github" / "workflows" / "forecast_update.yml").read_text()
    assert "python3 pipelines/publishing/build_pages_bundle.py pages_dist" in wf
    assert "rsync -a" not in wf


def test_forbidden_file_in_bundle_fails_the_build(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "subscribers.json").write_text("[]")
    assert any("forbidden" in p for p in bpd.check(tmp_path))
