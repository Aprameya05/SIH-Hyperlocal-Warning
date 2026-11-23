"""Static safety checks over every GitHub Actions workflow (runs on Linux CI)."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

WF_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
WORKFLOWS = sorted(WF_DIR.glob("*.yml"))


def _bash_ok() -> bool:
    exe = shutil.which("bash")
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "-c", "echo ok"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "ok"


def _run_blocks(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if step.get("run"):
                yield step.get("name", "?"), step["run"], step


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_yaml_parses_and_has_permissions(wf):
    doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
    assert "jobs" in doc
    perms = doc.get("permissions") or any("permissions" in j for j in doc["jobs"].values())
    assert perms, f"{wf.name}: declare explicit permissions"


@pytest.mark.skipif(not _bash_ok(), reason="a working POSIX bash is required")
@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_every_run_block_is_valid_bash(wf):
    for name, script, _ in _run_blocks(wf):
        script = re.sub(r"\$\{\{.*?\}\}", "X", script)
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8", newline="\n") as f:
            f.write(script)
        r = subprocess.run([shutil.which("bash"), "-n", f.name], capture_output=True, text=True)
        Path(f.name).unlink()
        assert r.returncode == 0, f"{wf.name} step '{name}': {r.stderr}"


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_no_force_push_and_no_repo_root_deploy(wf):
    text = wf.read_text(encoding="utf-8")
    assert not re.search(r"git push[^\n]*(--force\b|-f\b|--force-with-lease)", text), f"{wf.name}: force push"
    assert not re.search(r"pages deploy \.(\s|$)", text), f"{wf.name}: deploys the repository root"
