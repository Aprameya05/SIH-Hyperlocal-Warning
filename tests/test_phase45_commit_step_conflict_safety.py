"""
tests/test_phase45_commit_step_conflict_safety.py
=====================================================
2026-10-08: regression test for a real production bug, root-caused by
diagnosing 9 consecutive failed scheduled runs of .github/workflows/
forecast_update.yml. All 9 failed instantly at the "Validate unified
artifact before deploy" step -- consistent with a corrupted (not slow)
JSON file, not a real validation-content failure.

Root cause: the "Commit and push forecast outputs" step's
`git pull origin main --no-rebase || true` swallowed a genuine MERGE
CONFLICT, not just "nothing new to pull". On conflict, git leaves
literal `<<<<<<<`/`=======`/`>>>>>>>` markers written into the working
tree, and the very next `git add`/`git commit` then committed that
broken JSON. forecast.json was already protected by an explicit
backup-before-pull / restore-after-pull pattern; data/unified_forecast.json
(and every other generated file in that step) was not -- and this was
masked until CB started genuinely varying across most of its 4,960
records every run (2026-10-06), at which point concurrent/back-to-back
runs' pulls started conflicting on this file almost every time,
exactly matching when the failures began (first failure:
2026-10-06T16:50:06Z, immediately after that change).

This test asserts the structural fix is present: every generated file
is backed up before the pull, restored after it, and the restored
unified_forecast.json is validated as parseable JSON before being
staged -- without needing to actually simulate a git conflict (which
would require a throwaway git repo and is out of scope for a fast
unit test; the Python test_phase38_production_deployment_plumbing.py
module already covers broader workflow-structure assertions).
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "forecast_update.yml"


def _commit_step_script() -> str:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    start = text.index('- name: Commit and push forecast outputs')
    end = text.index('- name: Build Cloudflare Pages deployment directory')
    return text[start:end]


def test_generated_files_are_backed_up_before_the_pull():
    script = _commit_step_script()
    backup_idx = script.index("generated_backup")
    pull_idx = script.index("git pull origin main --no-rebase")
    assert backup_idx < pull_idx, "backup must happen before the pull that could conflict"


def test_unified_forecast_json_is_restored_after_the_pull_not_just_forecast_json():
    """The exact gap that caused the bug: forecast.json alone was
    protected before this fix. data/unified_forecast.json (and the
    other generated files) must now be restored too."""
    script = _commit_step_script()
    pull_idx = script.index("git pull origin main --no-rebase")
    after_pull = script[pull_idx:]
    # The restore loop must run over the same GENERATED_FILES set that
    # includes data/unified_forecast.json, after the pull.
    assert "GENERATED_FILES" in script
    assert "data/unified_forecast.json" in script
    restore_idx = after_pull.index("cp \"/tmp/generated_backup/$f\" \"$f\"")
    assert restore_idx > 0  # exists somewhere after the pull


def test_a_merge_conflict_is_explicitly_aborted_not_silently_ignored():
    """The old `|| true` swallowed a real conflict. The fix must
    explicitly abort a failed merge rather than leaving the working
    tree in a half-conflicted state."""
    script = _commit_step_script()
    assert "git merge --abort" in script
    # The bare `|| true` immediately after the pull (the original bug)
    # must be gone -- the pull's failure branch must do something
    # (abort), not just swallow the exit code.
    assert 'git pull origin main --no-rebase || true\n' not in script


def test_unified_forecast_json_is_json_validated_before_commit():
    """A conflict-corrupted file must never reach `git add`/`git commit`
    even if the abort logic itself has a gap -- this is the last line
    of defense."""
    script = _commit_step_script()
    add_idx = script.index("git add $GENERATED_FILES")
    validate_idx = script.index("is not valid JSON after restore")
    assert validate_idx < add_idx, "JSON validity check must run before staging the file"


def test_pyarrow_dependency_present_for_the_live_cb_cache():
    """scripts/gfs_live_cb_predictors.py's cache writes a parquet file
    (pandas.DataFrame.to_parquet), which requires pyarrow -- confirmed
    missing from this workflow's dependency list during the same
    investigation. Wrapped in try/except so it wouldn't crash the
    pipeline, but caching silently never worked on CI without it."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "pyarrow" in text
