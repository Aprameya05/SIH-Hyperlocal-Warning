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


EXTERNAL_EFFECTS = re.compile(r"(git push[^\n]*\bmain\b|pages deploy|send_alerts\.py|dispatch_alerts\.py|ntfy\.sh|wrangler)")


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_external_effects_only_run_on_main(wf):
    """A manual run from any other ref must be a safe dry run: no push to main, no deploy, no outbound notifications."""
    doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            blob = (step.get("run") or "") + " " + str((step.get("uses") or ""))
            if EXTERNAL_EFFECTS.search(blob) or "wrangler-action" in blob:
                cond = str(step.get("if", ""))
                assert "refs/heads/main" in cond, f"{wf.name}: step '{step.get('name')}' has external effects but is not main-only"


def test_forecast_update_fails_when_nothing_was_deployed():
    text = (WF_DIR / "forecast_update.yml").read_text(encoding="utf-8")
    assert "Fail loudly when nothing was deployed" in text and "unified_artifact_check.outputs.ok != 'true'" in text


def test_no_swallowed_failures_in_commit_deploy_or_smoke_steps():
    for wf in WORKFLOWS:
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for job in doc["jobs"].values():
            for step in job.get("steps", []):
                name = str(step.get("name", "")).lower()
                if any(k in name for k in ("commit", "deploy", "smoke", "release-readiness", "validate")):
                    assert not step.get("continue-on-error"), f"{wf.name}: '{step.get('name')}' must not continue-on-error"
