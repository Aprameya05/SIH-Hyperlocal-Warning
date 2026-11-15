"""Repository hygiene: dependency declarations agree, required entry points exist, and no secrets/subscriber files are tracked."""
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _reqs(path):
    out = set()
    for line in (ROOT / path).read_text().splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-r"):
            out.add(line)
    return out


def test_pyproject_dev_extra_matches_requirements_files():
    py = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert set(py["project"]["optional-dependencies"]["dev"]) == _reqs("requirements/ml.txt") | _reqs("requirements/dev.txt")


def test_base_requirements_include_backend_file_only():
    assert "-r ../backend/requirements.txt" in (ROOT / "requirements/base.txt").read_text()
    assert not _reqs("requirements/base.txt")


def test_entry_points_exist():
    for p in ["README.md", "CONTRIBUTING.md", "Makefile", ".env.example", "pyproject.toml", ".github/pull_request_template.md", "docs/architecture/REPOSITORY_STRUCTURE.md"]:
        assert (ROOT / p).exists(), p


def test_makefile_targets_reference_existing_scripts():
    for m in re.findall(r"(?:pipelines|models|scripts)/[\w/]+\.py", (ROOT / "Makefile").read_text()):
        assert (ROOT / m).exists(), m


def test_no_tracked_secrets_or_subscriber_files():
    files = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    bad = [f for f in files if re.search(r"(^|/)\.env$|subscriber|\.pem$|id_rsa", f, re.I) and not f.startswith(("docs/", "tests/"))]
    # data/subscribers.json is a tracked, deliberately EMPTY registry; any content in it would be personal data.
    for f in list(bad):
        if f == "data/subscribers.json" and (ROOT / f).read_text().strip() in ("[]", "{}"):
            bad.remove(f)
    assert not bad, bad
    env = (ROOT / ".env.example").read_text()
    assert not re.search(r"=\s*(AC[0-9a-f]{32}|sk-|ghp_|gho_)", env)


def test_markdown_relative_links_resolve():
    import os
    files = [ROOT / "README.md", ROOT / "CONTRIBUTING.md"] + [p for p in (ROOT / "docs").rglob("*.md") if "archive" not in p.parts]
    bad = []
    for f in files:
        for m in re.finditer(r"\]\(([^)#\s]+)(#[^)]*)?\)", f.read_text()):
            t = m.group(1)
            if not t.startswith(("http", "mailto")) and not (f.parent / t).resolve().exists():
                bad.append((str(f.relative_to(ROOT)), t))
    assert not bad, bad
