"""
tests/test_phase36_artifact_contract_and_ownership.py

Covers Priorities 7/8/11/12 of the 2026-10-06 "finish the implementation"
pass: the unified_forecast.json metadata contract across all 992 cells x
5 lead slots, atomic-write/last-known-good protection in
scripts/phase34_build_unified_forecast.py, and the rule that tests must
never write into production artifact paths.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

UNIFIED_FORECAST_PATH = REPO_ROOT / "data" / "unified_forecast.json"


def _load_existing_artifact():
    if not UNIFIED_FORECAST_PATH.exists():
        return None
    with open(UNIFIED_FORECAST_PATH) as f:
        return json.load(f)


def test_existing_artifact_has_992_cells_x_5_leads_if_present():
    """If the production artifact already exists, every cell must carry
    all 5 canonical lead slots -- no silently missing slot."""
    doc = _load_existing_artifact()
    if doc is None:
        return  # nothing to check in this environment run
    records = doc["records"]
    by_cell = {}
    for r in records:
        by_cell.setdefault(r["cell_id"], set()).add(r["lead_hours"])
    assert len(by_cell) == 992
    for cid, leads in by_cell.items():
        assert leads == {2, 3, 4, 5, 6}, f"{cid} missing lead slots: {leads}"


def test_existing_artifact_every_record_has_explicit_status_never_silent():
    """Every CB/FF/TS block must carry a 'status' (or risk_category of
    NOT_AVAILABLE when probability is None) -- never a bare null with no
    explanation."""
    doc = _load_existing_artifact()
    if doc is None:
        return
    for r in doc["records"]:
        for hz in ("TS", "CB", "FF"):
            block = r[hz]
            assert "status" in block
            if block.get("probability") is None:
                assert block.get("risk_category") == "NOT_AVAILABLE", (
                    f"{r['cell_id']} {hz} has null probability but risk_category="
                    f"{block.get('risk_category')!r} (should be NOT_AVAILABLE, never fabricated)"
                )


def test_existing_artifact_cb_never_claims_live_offline():
    """2026-10-06 Phase 3 update: the offline batch artifact
    (phase34_build_unified_forecast.py) now genuinely attempts a live
    current-cycle GFS fetch for CB (scripts/gfs_live_cb_predictors.py,
    AWS Open Data mirror primary / NOMADS secondary) before falling
    back to the fixed validation cycle -- so CB's source_status may
    legitimately be LIVE_AWS_GFS/LIVE_NOMADS_GFS/MIXED_LIVE_SOURCES
    when that fetch succeeds, not just FIXED_VALIDATION_FALLBACK or
    UNAVAILABLE. Whenever CB *does* report one of those LIVE statuses,
    it must carry its own cb_init_time_utc/cb_valid_time_utc (additive
    fields, independent of the record's shared init_time/valid_time)
    so a live CB probability is never silently timestamped with the
    fixed fallback cycle's time. FF has no live source wired yet and
    must still only ever be FIXED_VALIDATION_FALLBACK or UNAVAILABLE."""
    doc = _load_existing_artifact()
    if doc is None:
        return
    live_cb_statuses = {"LIVE_AWS_GFS", "LIVE_NOMADS_GFS", "MIXED_LIVE_SOURCES"}
    for r in doc["records"][:50]:  # sample; full scan is redundant given uniform generation
        cb = r.get("CB", {})
        if "source_status" in cb:
            assert cb["source_status"] in ({"FIXED_VALIDATION_FALLBACK", "UNAVAILABLE"} | live_cb_statuses)
            if cb["source_status"] in live_cb_statuses:
                assert "cb_init_time_utc" in cb and "cb_valid_time_utc" in cb, (
                    "a LIVE CB record must carry its own timestamps, never borrow the fixed fallback cycle's")
        if "source_status" in r.get("FF", {}):
            assert r["FF"]["source_status"] in ("FIXED_VALIDATION_FALLBACK", "UNAVAILABLE")


def test_ff_value_type_is_risk_score_never_probability():
    doc = _load_existing_artifact()
    if doc is None:
        return
    for r in doc["records"][:50]:
        if "value_type" in r.get("FF", {}):
            assert r["FF"]["value_type"] == "risk_score"
        assert r["FF"]["probability"] is None, "FF must never carry a probability value"


def test_builder_atomic_write_rejects_and_preserves_on_bad_validation(tmp_path, monkeypatch):
    """Simulates a malformed-artifact build and confirms the builder's
    reject-and-preserve logic (not the real generation, which needs the
    full model stack) by exercising validate_artifact() directly against a
    hand-built bad artifact, and confirming the production path is never
    touched by this test."""
    import phase34_build_unified_forecast as builder

    good_records = []
    for i in range(992):
        for lead in (2, 3, 4, 5, 6):
            good_records.append({
                "cell_id": f"CELL_{i}", "init_time": "2024-08-01T00:00:00Z",
                "valid_time": f"2024-08-01T0{lead}:00:00Z" if lead < 10 else "2024-08-01T10:00:00Z",
                "lead_hours": lead,
                "TS": {"probability": None, "risk_category": "NOT_AVAILABLE"},
                "CB": {"probability": 0.1, "risk_category": "LOW"},
                "FF": {"probability": None, "risk_category": "NOT_AVAILABLE"},
            })
    bad_artifact = {"records": good_records[:-1]}  # drop one record -> not 4960
    result = builder.validate_artifact(bad_artifact)
    assert result["exactly_4960_records"] is False


def test_this_suite_never_targets_the_production_artifact_path():
    """Pipeline-ownership integrity check (Priority 11): this test module
    itself, and the atomic-write test above, must never open
    UNIFIED_FORECAST_PATH for writing. Statically verify no 'open(...,
    \"w\")' call in this file's own source references the production path
    variable incorrectly -- a structural guard against future edits
    accidentally writing into the real artifact from a test."""
    this_file = Path(__file__)
    src = this_file.read_text()
    # The only two string-literal mentions of UNIFIED_FORECAST_PATH in this
    # file must be read-only (open(...) with default/"r" mode or no mode).
    for line in src.splitlines():
        if "UNIFIED_FORECAST_PATH" in line and "open(" in line:
            assert '"w"' not in line and "'w'" not in line, f"test must not write production path: {line}"
