"""
tests/test_phase48_transactional_deploy_gate.py
===================================================
2026-10-08: regression test for a real production incident. Run
37727128213 crashed inside "Generate unified SIH forecast artifact"
(ModuleNotFoundError, now fixed separately) but the workflow still
proceeded to build pages_dist and attempt deploy -- only an unrelated
missing-validator-script bug happened to stop it before Cloudflare
actually served the stale build. That was luck, not a real gate.

Fix: every step from "Build Cloudflare Pages deployment directory"
through the post-deploy smoke test is now gated on
`steps.unified_artifact.outcome == 'success'` -- the step's real
outcome, which (unlike `conclusion`) is not overridden by that step's
own `continue-on-error: true`. This test asserts the gate is actually
present on every one of those steps, structurally, so a future edit
can't silently remove it.
"""
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "forecast_update.yml"

GATED_STEP_NAMES = [
    "Build Cloudflare Pages deployment directory",
    "Validate unified artifact before deploy (reject malformed/empty)",
    "Deploy to Cloudflare Pages",
    "Post-deploy smoke test (public site actually serves the fresh artifact)",
]

EXPECTED_GATE = "steps.unified_artifact.outcome == 'success'"


def _steps():
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return doc["jobs"]["update-forecast"]["steps"]


def test_unified_artifact_step_has_the_id_the_gate_depends_on():
    steps = _steps()
    build_step = next(s for s in steps if s["name"].startswith("Generate unified SIH forecast artifact"))
    assert build_step.get("id") == "unified_artifact"


def test_every_deploy_path_step_is_gated_on_unified_artifact_success():
    steps = _steps()
    by_name = {s["name"]: s for s in steps}
    missing_gate = [name for name in GATED_STEP_NAMES if by_name.get(name, {}).get("if") != EXPECTED_GATE]
    assert not missing_gate, f"these steps must gate on {EXPECTED_GATE!r}: {missing_gate}"


def test_gate_uses_outcome_not_conclusion():
    """`conclusion` is overridden by continue-on-error (always shows
    'success' even when the underlying command failed); `outcome` is
    the real, unoverridden result. Using the wrong field here would
    silently defeat the entire gate -- this is the exact distinction
    that caused the earlier public-API misdiagnosis during this
    incident's investigation."""
    steps = _steps()
    for s in steps:
        if s.get("if") and "unified_artifact" in s["if"]:
            assert "outcome" in s["if"], f"step {s['name']!r} must gate on .outcome, not .conclusion"
            assert "conclusion" not in s["if"]


def test_commit_step_excludes_unified_forecast_json_on_build_failure():
    """2026-10-08: data/unified_forecast.json must be explicitly dropped
    from the committed file list when the real unified_artifact outcome
    wasn't 'success' -- on top of (not instead of) the backup/restore/
    JSON-validate protection already in this step."""
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        text = f.read()
    start = text.index('- name: Commit and push forecast outputs')
    end = text.index('- name: Build Cloudflare Pages deployment directory')
    script = text[start:end]
    assert "UNIFIED_ARTIFACT_OUTCOME" in script
    assert 'steps.unified_artifact.outcome' in script
    assert '"$UNIFIED_ARTIFACT_OUTCOME" = "success"' in script


def test_commit_step_is_not_gated_away_entirely():
    """The commit step itself must NOT be skipped on build failure --
    it still needs to run to commit the unrelated legacy-dashboard
    files (forecast.json etc, which are independent of the unified
    artifact and must keep refreshing even if the new pipeline hiccups).
    Safety for data/unified_forecast.json specifically comes from the
    backup/restore-before-commit logic (tests/test_phase45_commit_step_conflict_safety.py),
    not from skipping the whole step."""
    steps = _steps()
    commit_step = next(s for s in steps if s["name"] == "Commit and push forecast outputs")
    assert "if" not in commit_step
