"""
tests/test_unified_forecast_schema_integrity.py
=================================================
Phase 36.1 Blocker 2: deterministic integrity guard for
data/unified_forecast.json.

Root cause this guards against: tests/test_phase25_hazard_gaps.py used to
call scripts/phase24_unified_pipeline.main() with no output-path
redirection, so every regression run silently overwrote the real,
authoritative Phase 34/35/36 artifact with phase24_unified_pipeline.py's
own older research schema (keys like ts_probability,
cb_probability_baseline, latitude/longitude, target_timestamp_utc).
That test now redirects its output to a tmp path (see that file), but this
test exists so that if ANY other code path -- today or added later --
writes the Phase 24 schema (or any other non-conforming schema) back into
data/unified_forecast.json, the regression suite fails loudly instead of
the corruption being discovered by accident days later.

The single authoritative generator for this artifact is
scripts/phase34_build_unified_forecast.py (see docs/PIPELINE_OWNERSHIP.md).
No GitHub Actions workflow writes this file; it is a local/offline
artifact regenerated on demand and consumed by backend/unified_api.py's
GET /forecast/all as the OFFLINE_ARTIFACT fallback.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = REPO_ROOT / "data" / "unified_forecast.json"

# Keys that only ever appear in the old Phase 24 schema. Their presence
# anywhere in any record is an automatic failure -- this is the direct
# regression signature of the bug this test was written to catch.
PHASE24_ONLY_RECORD_KEYS = {
    "target_timestamp_utc",
    "latitude",
    "longitude",
    "ts_probability",
    "cb_probability_baseline",
    "cb_probability_neural_prototype",
    "ff_probability",
    "hazard_status",
    "hazard_status_reason",
    "data_provenance_source_cycle",
    "feature_missingness_fraction",
    "lead_time_status",
    "lead_time_caveat",
}

REQUIRED_TOP_LEVEL_KEYS = {
    "n_cells", "n_lead_times", "n_records", "records",
    "init_time_utc", "lead_hours_supported",
}
REQUIRED_RECORD_KEYS = {
    "cell_id", "lat", "lon", "init_time", "valid_time", "lead_hours",
    "TS", "CB", "FF", "terrain", "model_version",
}
REQUIRED_HAZARD_SUBKEYS = {"probability", "risk_category", "status", "model_version", "provenance"}


def _load_artifact():
    assert ARTIFACT_PATH.exists(), f"{ARTIFACT_PATH} does not exist"
    with open(ARTIFACT_PATH) as f:
        return json.load(f)


def test_artifact_is_not_phase24_schema():
    d = _load_artifact()
    assert isinstance(d, dict), "artifact root must be a dict (Phase 34/35/36 contract)"
    assert "artifact_type" in d
    assert "phase 24" not in str(d.get("artifact_type", "")).lower(), (
        "data/unified_forecast.json has reverted to the Phase 24 "
        "artifact_type label -- the Phase 24 pipeline wrote this file "
        "again; re-run scripts/phase34_build_unified_forecast.py and find "
        "the writer (see docs/PIPELINE_OWNERSHIP.md)"
    )
    records = d.get("records", [])
    assert records, "artifact has no records"
    first = records[0]
    offending = PHASE24_ONLY_RECORD_KEYS & set(first.keys())
    assert not offending, (
        f"record contains Phase-24-only keys {offending} -- the artifact "
        "has been overwritten with the old schema"
    )


def test_artifact_has_required_phase34_35_36_contract_keys():
    d = _load_artifact()
    missing_top = REQUIRED_TOP_LEVEL_KEYS - set(d.keys())
    assert not missing_top, f"artifact missing required top-level keys: {missing_top}"
    first = d["records"][0]
    missing_record = REQUIRED_RECORD_KEYS - set(first.keys())
    assert not missing_record, f"record missing required keys: {missing_record}"
    for hz in ("TS", "CB", "FF"):
        missing_hz = REQUIRED_HAZARD_SUBKEYS - set(first[hz].keys())
        assert not missing_hz, f"{hz} block missing required subkeys: {missing_hz}"


def test_artifact_has_exactly_992_cells_5_leads_4960_records():
    d = _load_artifact()
    records = d["records"]
    assert len(records) == 4960, f"expected 4960 records, got {len(records)}"
    assert d.get("n_records") == 4960
    assert d.get("n_cells") == 992
    assert d.get("n_lead_times") == 5
    unique_cells = {r["cell_id"] for r in records}
    assert len(unique_cells) == 992, f"expected 992 unique cells, got {len(unique_cells)}"
    unique_leads = {r["lead_hours"] for r in records}
    assert unique_leads == {2, 3, 4, 5, 6}, f"unexpected lead_hours set: {unique_leads}"
    assert all(cid.startswith("IND_") for cid in unique_cells), (
        "cell_id must be canonical IND_<lat>_<lon> form"
    )


def test_ff_never_carries_a_fabricated_probability():
    d = _load_artifact()
    for r in d["records"]:
        assert r["FF"]["probability"] is None, (
            f"record {r['cell_id']}@{r['lead_hours']}h: FF.probability must "
            "always be null -- it is a PU-ranking score, never a calibrated "
            "probability"
        )
        assert r["FF"]["risk_category"] == "NOT_AVAILABLE"


def test_no_unavailable_probability_mapped_to_a_risk_category():
    d = _load_artifact()
    for r in d["records"]:
        for hz in ("TS", "CB", "FF"):
            block = r[hz]
            if block["probability"] is None:
                assert block["risk_category"] == "NOT_AVAILABLE", (
                    f"record {r['cell_id']}@{r['lead_hours']}h {hz}: null "
                    "probability must map to NOT_AVAILABLE risk, never a "
                    "real risk category"
                )
