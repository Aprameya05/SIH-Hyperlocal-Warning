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

2026-10-10 update: `steps.unified_artifact.outcome` itself turned out
to be the wrong signal -- the real exit-134 native crash this process
hits on the Linux CI runner happens AFTER the artifact is already
correctly written (confirmed: the original incident showed
accepted=true already printed before the crash), so gating on the
Python process's own exit code permanently blocked every commit for
2+ days even once the artifact itself was genuinely good. The gate
now reads `steps.unified_artifact_check.outputs.ok` instead -- an
independent re-validation of the artifact's actual on-disk content
and freshness, decoupled from the process's exit code entirely. See
tests/test_phase58_artifact_freshness_check_decoupled_from_exit_code.py
for that step's own logic tests. This file's tests are updated to
match the new gate expression; the underlying principle they verify
(every deploy-path step must gate on SOME real, non-overridden
signal, never silently proceed) is unchanged.
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

EXPECTED_GATE = "steps.unified_artifact_check.outputs.ok == 'true'"


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


def test_gate_uses_the_independent_check_not_the_raw_process_outcome():
    """2026-10-10: `steps.unified_artifact.outcome` (the Python
    process's own exit code) turned out to be unreliable -- a native
    crash in cleanup code that runs AFTER the real work finished
    permanently reads as failure under that signal, no matter how good
    the artifact actually is. The gate must read
    steps.unified_artifact_check.outputs.ok (independent re-validation
    of the artifact's real content/freshness) instead."""
    steps = _steps()
    for s in steps:
        if s.get("if") and "unified_artifact" in s["if"]:
            assert "unified_artifact_check.outputs.ok" in s["if"], \
                f"step {s['name']!r} must gate on the independent artifact check, not the raw process outcome"


def test_commit_step_excludes_unified_forecast_json_on_build_failure():
    """2026-10-08: data/unified_forecast.json must be explicitly dropped
    from the committed file list when the artifact wasn't genuinely
    fresh and valid this run -- on top of (not instead of) the
    backup/restore/JSON-validate protection already in this step."""
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        text = f.read()
    start = text.index('- name: Commit and push forecast outputs')
    end = text.index('- name: Build Cloudflare Pages deployment directory')
    script = text[start:end]
    assert "UNIFIED_ARTIFACT_OUTCOME" in script
    assert 'steps.unified_artifact_check.outputs.ok' in script
    assert '"$UNIFIED_ARTIFACT_OUTCOME" = "true"' in script


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
