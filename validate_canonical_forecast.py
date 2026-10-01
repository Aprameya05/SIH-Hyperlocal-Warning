#!/usr/bin/env python3
"""
validate_canonical_forecast.py -- schema + integrity validation for the
canonical forecast artifact. Two independent, separately-callable stages,
both used by canonical_forecast_writer.py BEFORE publication, and both
runnable standalone against an already-published file.

Neither stage repairs anything. Both return (ok: bool, errors: list[str]).

Run standalone: python3 validate_canonical_forecast.py [path]
(defaults to data/canonical_forecast.json)
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "canonical_forecast.schema.json"

VALID_STATUSES = {"AVAILABLE", "PROXY", "UNAVAILABLE", "STALE", "NOT_APPLICABLE"}
VALID_SOURCE_STATUSES = VALID_STATUSES | {"FETCHED", "CACHED", "FAILED"}
VALID_LEADS = {2, 4, 6}


def validate_schema(doc: dict):
    try:
        import jsonschema
    except ImportError:
        return False, ["jsonschema package not installed -- cannot validate schema"]
    schema = json.loads(SCHEMA_PATH.read_text())
    validator = jsonschema.Draft7Validator(schema)
    errors = [f"{'.'.join(str(p) for p in e.path)}: {e.message}" for e in validator.iter_errors(doc)]
    return (len(errors) == 0), errors


def _is_valid_iso(s):
    if not isinstance(s, str):
        return False
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate_integrity(doc: dict):
    """Checks that go beyond what JSON Schema alone can express: exactly 992
    cells, unique cell IDs, no duplicate (cell, lead) combinations, no
    NaN/Infinity anywhere in the tree, etc. Fails loudly -- never repairs."""
    errors = []

    cells = doc.get("cells", [])
    if len(cells) != 992:
        errors.append(f"expected exactly 992 cells, got {len(cells)}")

    cell_ids = [c.get("cell_id") for c in cells]
    if len(cell_ids) != len(set(cell_ids)):
        dupes = {cid for cid in cell_ids if cell_ids.count(cid) > 1}
        errors.append(f"duplicate cell_id(s) found: {dupes}")

    for c in cells:
        lat, lon = c.get("latitude"), c.get("longitude")
        if not isinstance(lat, (int, float)) or not (6 <= lat <= 37):
            errors.append(f"{c.get('cell_id')}: invalid latitude {lat}")
        if not isinstance(lon, (int, float)) or not (68 <= lon <= 98):
            errors.append(f"{c.get('cell_id')}: invalid longitude {lon}")

        seen_leads = set()
        for f in c.get("forecasts", []):
            lead = f.get("lead_hours")
            if lead not in VALID_LEADS:
                errors.append(f"{c.get('cell_id')}: invalid lead_hours {lead}")
            if lead in seen_leads:
                errors.append(f"{c.get('cell_id')}: duplicate lead_hours {lead} (duplicate cell+lead combination)")
            seen_leads.add(lead)

            if not _is_valid_iso(f.get("valid_time")):
                errors.append(f"{c.get('cell_id')} lead={lead}: invalid valid_time {f.get('valid_time')!r}")

            for hz_name, hz in (f.get("hazards") or {}).items():
                prob = hz.get("probability")
                status = hz.get("status")
                if status not in VALID_STATUSES:
                    errors.append(f"{c.get('cell_id')} lead={lead} {hz_name}: invalid status {status!r}")
                if prob is not None:
                    if isinstance(prob, float) and (math.isnan(prob) or math.isinf(prob)):
                        errors.append(f"{c.get('cell_id')} lead={lead} {hz_name}: probability is NaN/Infinity")
                    elif not (0 <= prob <= 1):
                        errors.append(f"{c.get('cell_id')} lead={lead} {hz_name}: probability {prob} out of [0,1]")
                if status == "AVAILABLE" and prob is None:
                    errors.append(f"{c.get('cell_id')} lead={lead} {hz_name}: status AVAILABLE but probability is null")
                if status in ("UNAVAILABLE", "NOT_APPLICABLE") and prob is not None:
                    errors.append(f"{c.get('cell_id')} lead={lead} {hz_name}: status {status} but probability is not null (fabricated value risk)")
                if status in ("UNAVAILABLE", "STALE", "NOT_APPLICABLE") and not hz.get("reason"):
                    errors.append(f"{c.get('cell_id')} lead={lead} {hz_name}: status {status} requires a 'reason'")
                if not hz.get("source") or not hz.get("method") or not hz.get("model"):
                    errors.append(f"{c.get('cell_id')} lead={lead} {hz_name}: missing provenance (source/method/model)")

            dq = f.get("data_quality") or {}
            for src_name in ("gfs", "himawari", "terrain"):
                src = dq.get(src_name)
                if not src or src.get("status") not in VALID_SOURCE_STATUSES:
                    errors.append(f"{c.get('cell_id')} lead={lead}: data_quality.{src_name} missing or invalid status")

        # Phase 3.5 Part 2 regression check: an AVAILABLE VOBL hazard value
        # must never be silently duplicated across leads that fall in
        # DIFFERENT real slot windows -- two leads sharing the same slot
        # window (and therefore the same model_used) legitimately share one
        # real value; two leads in different windows must not show the same
        # probability+model unless the underlying model genuinely produced
        # that (astronomically unlikely for independent slot models, so
        # treated as a red flag).
        if c.get("domain") == "VOBL_ML_DOMAIN":
            by_model = {}
            for f in c.get("forecasts", []):
                ts = (f.get("hazards") or {}).get("thunderstorm", {})
                if ts.get("status") == "AVAILABLE":
                    key = ts.get("model")
                    by_model.setdefault(key, []).append((f.get("lead_hours"), ts.get("probability")))
            for model, entries in by_model.items():
                probs = {p for _, p in entries}
                leads = {lh for lh, _ in entries}
                if len(entries) > 1 and len(probs) == 1 and len(leads) > 1:
                    # Same model + same probability across >1 lead is only
                    # legitimate if model naming embeds a slot index unique
                    # per lead's actual slot -- flag if it doesn't.
                    if "_slot" not in str(model):
                        errors.append(
                            f"{c.get('cell_id')}: identical AVAILABLE probability ({probs}) and model "
                            f"({model}) reused across leads {sorted(leads)} without a slot-qualified model "
                            "name -- possible duplicated-lead fabrication")

    if not _is_valid_iso(doc.get("generated_at")):
        errors.append(f"top-level generated_at invalid: {doc.get('generated_at')!r}")

    fc_cycle = doc.get("forecast_cycle", {})
    st = fc_cycle.get("source_timestamp")
    if st is not None and not _is_valid_iso(st):
        errors.append(f"forecast_cycle.source_timestamp invalid: {st!r}")

    # global no-NaN/no-Infinity sweep (belt and suspenders beyond the
    # per-hazard check above, in case a future field adds a bare float)
    def _walk(node, path=""):
        if isinstance(node, float):
            if math.isnan(node) or math.isinf(node):
                errors.append(f"{path}: NaN/Infinity found in canonical forecast")
        elif isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")

    _walk(doc, "$")

    return (len(errors) == 0), errors


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "data" / "canonical_forecast.json"
    if not path.exists():
        print(f"FATAL: {path} does not exist")
        return 1
    doc = json.loads(path.read_text())

    schema_ok, schema_errors = validate_schema(doc)
    print(f"Schema validation: {'PASS' if schema_ok else 'FAIL'}")
    for e in schema_errors[:20]:
        print(f"  - {e}")

    integrity_ok, integrity_errors = validate_integrity(doc)
    print(f"Integrity validation: {'PASS' if integrity_ok else 'FAIL'}")
    for e in integrity_errors[:20]:
        print(f"  - {e}")

    return 0 if (schema_ok and integrity_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
