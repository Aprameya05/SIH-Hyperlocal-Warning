#!/usr/bin/env python3
"""
canonical_forecast_writer.py -- the ONE authoritative production writer for
the canonical forecast artifact (data/canonical_forecast.json).

Where its inputs come from (traced this phase, not assumed):
  - data/pan_india_grid.json   -- written by backend/pipeline.py (via
    .github/workflows/update_grid.yml), the pan-India physics-proxy engine,
    992 canonical cells, ONE fetched GFS forecast hour per run.
  - forecast.json               -- written by forecast_action.py (via
    .github/workflows/forecast_update.yml), the VOBL station document: the
    real, trained, validated XGBoost model's output, plus Himawari/METAR/
    terrain sub-documents for that one station.

This module does NOT re-run the hazard engine, re-fetch GFS, or duplicate
any scoring logic. It reads the two existing, already-produced artifacts
and assembles them into ONE schema-validated, versioned, per-cell array
with explicit per-hazard provenance/status -- see
docs/CANONICAL_FORECAST_SCHEMA.md for the full rationale.

Lead times (2h/4h/6h): see docs/CANONICAL_FORECAST_SCHEMA.md's "Lead-time
honesty" section. Neither forecast.json nor pan_india_grid.json currently
carries more than one GFS-forecast-hour's worth of data per generation
cycle (verified this phase: pan_india_gfs_fetcher.py's resolve_gfs_cycle()
computes exactly one fhour per call). Where a lead hour's valid_time falls
within LEAD_TOLERANCE_MINUTES of the single fetched GFS valid time, that
lead is populated from the real data. Every other lead is marked
UNAVAILABLE with an explicit reason -- never a duplicated probability.

Run: python3 canonical_forecast_writer.py   (from repo root)
Exits non-zero and leaves the previous canonical_forecast.json untouched if
validation fails at any stage (schema or integrity).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
from atomic_write import AtomicWriteError, atomic_write_json, read_json_or_none  # noqa: E402
from regrid import cell_id_for  # noqa: E402

SCHEMA_VERSION = "1.1.0"
LEAD_HOURS = (2, 4, 6)
LEAD_TOLERANCE_MINUTES = 45  # a lead hour is considered "matched" to the
# single fetched GFS valid time if within this window -- wide enough to
# absorb normal fetch-cycle jitter, narrow enough that a stale/unrelated
# fetch is never mistaken for a different lead's real forecast.

CANONICAL_PATH = REPO_ROOT / "data" / "canonical_forecast.json"
PAN_INDIA_GRID_PATH = REPO_ROOT / "data" / "pan_india_grid.json"
FORECAST_JSON_PATH = REPO_ROOT / "forecast.json"

VOBL_CELL_ID = "IND_13.0_78.0"
VOBL_LAT, VOBL_LON = 13.0, 78.0

IST_OFFSET = timedelta(hours=5, minutes=30)

# Freshness thresholds are NOT invented as a universal number -- they follow
# each source's own real cadence, as required by Phase 3.5 Part 6:
#   - GFS: forecast_update.yml/update_grid.yml run 4x/day (~6h cadence);
#     a GFS-derived artifact older than 7h has missed its own refresh cycle.
#   - Himawari: fetched every pipeline run (multiple times/day, higher
#     cadence than GFS); an observation older than 2h is stale for a
#     geostationary sensor that updates every ~10 minutes.
GFS_STALE_AFTER_HOURS = 7.0
HIMAWARI_STALE_AFTER_HOURS = 2.0

IST_SLOT_WINDOWS = {
    0: (0, 6),    # 0001-0600 IST
    1: (6, 12),   # 0601-1200 IST
    2: (12, 18),  # 1201-1800 IST
    3: (18, 24),  # 1801-2400 IST
}


def _slot_for_ist_hour(hour: int) -> int:
    for slot, (start, end) in IST_SLOT_WINDOWS.items():
        if start <= hour < end:
            return slot
    return 3  # hour == 24 (midnight rollover written as 24) falls in slot 3's tail


def _parse_forecast_generated_at_ist(raw: str):
    """forecast.json's `generated_at` is a plain 'YYYY-MM-DD HH:MM IST'
    string (verified by reading the field directly, not assumed). Returns a
    naive IST datetime, or None if the field is missing/unparseable --
    never guesses a time."""
    if not raw:
        return None
    try:
        cleaned = raw.replace(" IST", "").strip()
        return datetime.strptime(cleaned, "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_gfs_cycle_valid_time(gfs_cycle: str, gfs_fhour) -> "datetime | None":
    """gfs_cycle is 'YYYYMMDDHH' (e.g. '2026093006'), gfs_fhour is an int
    forecast-hour offset. Returns the real valid datetime, or None if either
    is missing/unparseable -- never guesses."""
    if not gfs_cycle or gfs_fhour is None:
        return None
    try:
        cycle_dt = datetime.strptime(str(gfs_cycle), "%Y%m%d%H").replace(tzinfo=timezone.utc)
        return cycle_dt + timedelta(hours=int(gfs_fhour))
    except (ValueError, TypeError):
        return None


def _hazard_block(probability, status, source, method, model, reason=None):
    block = {"probability": probability, "status": status, "source": source, "method": method, "model": model}
    if reason is not None:
        block["reason"] = reason
    return block


def _source_status(status, timestamp=None):
    return {"status": status, "timestamp": timestamp}


def _file_freshness_state(path: Path, stale_after_hours: float, now: datetime, freshly_fetched: bool):  # noqa: E501
    """Determines a source's freshness state from the actual on-disk file
    mtime, never from the canonical document's own generated_at.

    - FAILED: file does not exist.
    - FETCHED: --freshly-fetched was passed for this run AND the file's
      mtime is within the last 10 minutes (i.e. a fetch step in *this same*
      pipeline run actually wrote it just now).
    - STALE: file exists but is older than the source's own real cadence.
    - CACHED: file exists, is not stale, but was not (verifiably) fetched
      by this run -- i.e. this run read an artifact left on disk by an
      earlier run/session, which is exactly what happened for every local
      validation performed in this sandbox (no live network fetch occurs
      here; see docs/CANONICAL_FORECAST_SCHEMA.md 'Freshness semantics').
    """
    if not path.exists():
        return "FAILED", None, None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_hours = (now - mtime).total_seconds() / 3600.0
    if freshly_fetched and age_hours * 60 <= 10:
        state = "FETCHED"
    elif age_hours > stale_after_hours:
        state = "STALE"
    else:
        state = "CACHED"
    return state, _iso(mtime), round(age_hours, 2)


def build_pan_india_cells(grid: dict, generated_at: datetime, gfs_valid_time):
    """Builds the 992 canonical cells' forecasts from the pan-India physics
    engine's grid_cells array. Every hazard is explicitly marked PROXY (this
    IS the hand-weighted physics engine documented in
    docs/PAN_INDIA_HAZARD_COEFFICIENTS.md -- never called ML)."""
    cells_raw = grid.get("grid_cells") or grid.get("cells") or []
    gfs_cycle = grid.get("gfs_cycle")
    gfs_fhour = grid.get("gfs_fhour")
    gfs_gen = grid.get("generated_at_utc")

    out_cells = []
    for c in cells_raw:
        lat, lon = c["lat"], c["lon"]
        cid = cell_id_for(lat, lon)
        forecasts = []
        for lead in LEAD_HOURS:
            requested_valid_time = generated_at + timedelta(hours=lead)
            matched = (
                gfs_valid_time is not None
                and abs((gfs_valid_time - requested_valid_time).total_seconds()) <= LEAD_TOLERANCE_MINUTES * 60
            )
            if matched:
                hazards = {
                    "thunderstorm": _hazard_block(
                        c.get("thunderstorm_probability"), "PROXY",
                        "GFS 0.25deg (regridded to canonical 1.0deg cell)",
                        "hand-weighted physics-proxy formula (see docs/PAN_INDIA_HAZARD_COEFFICIENTS.md)",
                        "pan_india_physics_proxy_v1"),
                    "cloudburst": _hazard_block(
                        c.get("cloudburst_probability"), "PROXY",
                        "GFS 0.25deg (regridded to canonical 1.0deg cell)",
                        "hand-weighted physics-proxy formula (see docs/PAN_INDIA_HAZARD_COEFFICIENTS.md)",
                        "pan_india_physics_proxy_v1"),
                    "flash_flood": _hazard_block(
                        c.get("flash_flood_probability"), "PROXY",
                        "GFS 0.25deg (regridded to canonical 1.0deg cell) + BLR-only terrain where applicable",
                        "hand-weighted physics-proxy formula (see docs/PAN_INDIA_HAZARD_COEFFICIENTS.md)",
                        "pan_india_physics_proxy_v1"),
                }
                gfs_dq = _source_status("AVAILABLE", gfs_gen)
            else:
                reason = ("no lead-specific GFS forecast fetch exists for this lead hour -- "
                          "pan_india_gfs_fetcher.py fetches a single current forecast hour per run "
                          f"(cycle={gfs_cycle}, fhour={gfs_fhour}), not distinct +2h/+4h/+6h-from-now "
                          "forecast fields; see docs/CANONICAL_FORECAST_SCHEMA.md 'Lead-time honesty'")
                hazards = {
                    "thunderstorm": _hazard_block(None, "UNAVAILABLE", "none", "none", "none", reason),
                    "cloudburst": _hazard_block(None, "UNAVAILABLE", "none", "none", "none", reason),
                    "flash_flood": _hazard_block(None, "UNAVAILABLE", "none", "none", "none", reason),
                }
                gfs_dq = _source_status("STALE" if gfs_valid_time is not None else "UNAVAILABLE", gfs_gen)

            forecasts.append({
                "lead_hours": lead,
                "valid_time": _iso(requested_valid_time),
                "hazards": hazards,
                "data_quality": {
                    "gfs": gfs_dq,
                    "himawari": _source_status("NOT_APPLICABLE"),  # pan-India cells: Himawari crop is VOBL-only
                    "terrain": _source_status("NOT_APPLICABLE" if cid != VOBL_CELL_ID else "AVAILABLE"),
                },
            })
        out_cells.append({
            "cell_id": cid,
            "latitude": lat,
            "longitude": lon,
            "domain": "PAN_INDIA_PHYSICS_PROXY",
            "forecasts": forecasts,
        })
    return out_cells


def overlay_vobl_ml_cell(cells: list, fc: dict, himawari_dq, terrain_dq):
    """Replaces the VOBL cell's forecasts with the real, trained, per-slot
    XGBoost model output from forecast.json's `slots` array, where present.

    Phase 3.5 correction: forecast.json's `slots` array carries FOUR real,
    independently-trained model outputs (nowcast_slot0..3_xgb_v6_temporal.pkl,
    verified by reading the field directly), one per 6-hour IST window
    (0001-0600, 0601-1200, 1201-1800, 1801-2400). This means the VOBL cell
    DOES support genuine lead-specific predictions -- quantized to which
    6-hour slot window a given lead's valid_time falls in, not a duplicated
    single number. This replaces the previous (Phase 2) representation,
    which showed one peak_probability at all three leads with a disclosure
    -- that representation is no longer used.

    For each of +2h/+4h/+6h from forecast.json's own generation moment:
      - if the resulting valid_time falls on the SAME IST calendar day as
        generation, look up that hour's real slot and use ITS OWN
        ts_probability/cb_probability/ff_probability/model_used -- a real,
        distinct model output, not an interpolation or duplication;
      - if the valid_time crosses into the next IST calendar day,
        forecast_action.py does not compute a next-day slot in this run --
        mark UNAVAILABLE with an explicit reason rather than reusing
        today's slot0 for a day it was never computed for.

    Two or more leads landing in the same slot window (e.g. +2h and +4h
    both inside the 1801-2400 slot) legitimately share that slot's one real
    value -- that is the slot's actual, singular prediction for that whole
    window, not fabrication.
    """
    slots = fc.get("slots") or []
    slots_by_index = {s.get("slot"): s for s in slots if isinstance(s, dict)}
    model_version = fc.get("model_version", "unknown")

    t0_ist = _parse_forecast_generated_at_ist(fc.get("generated_at"))

    for cell in cells:
        if cell["cell_id"] != VOBL_CELL_ID:
            continue
        cell["domain"] = "VOBL_ML_DOMAIN"
        for lead_fc in cell["forecasts"]:
            lead = lead_fc["lead_hours"]

            if t0_ist is None:
                reason = "forecast.json's generated_at field is missing or unparseable; cannot resolve a lead-specific slot"
                for hz_name in ("thunderstorm", "cloudburst", "flash_flood"):
                    lead_fc["hazards"][hz_name] = _hazard_block(None, "UNAVAILABLE", "none", "none", "none", reason)
                lead_fc["data_quality"]["himawari"] = himawari_dq
                lead_fc["data_quality"]["terrain"] = terrain_dq
                continue

            valid_ist = t0_ist + timedelta(hours=lead)
            valid_utc = valid_ist - IST_OFFSET
            # overwrite the pan-India-anchored valid_time with the VOBL
            # cell's own real generation-moment anchor -- these can differ
            # from the writer's own invocation time (see docs).
            lead_fc["valid_time"] = _iso(valid_utc)

            if valid_ist.date() != t0_ist.date():
                reason = (f"+{lead}h from this run's generation time ({t0_ist.isoformat()} IST) falls on the "
                          "next IST calendar day; forecast_action.py only computes the current day's 4 slots "
                          "per run -- no next-day slot model output exists yet, so this is not fabricated")
                for hz_name in ("thunderstorm", "cloudburst", "flash_flood"):
                    lead_fc["hazards"][hz_name] = _hazard_block(None, "UNAVAILABLE", "none", "none", "none", reason)
                lead_fc["data_quality"]["himawari"] = himawari_dq
                lead_fc["data_quality"]["terrain"] = terrain_dq
                continue

            slot_idx = _slot_for_ist_hour(valid_ist.hour)
            slot = slots_by_index.get(slot_idx)
            if slot is None:
                reason = f"forecast.json's slots array has no entry for slot {slot_idx} this cycle"
                for hz_name in ("thunderstorm", "cloudburst", "flash_flood"):
                    lead_fc["hazards"][hz_name] = _hazard_block(None, "UNAVAILABLE", "none", "none", "none", reason)
                lead_fc["data_quality"]["himawari"] = himawari_dq
                lead_fc["data_quality"]["terrain"] = terrain_dq
                continue

            slot_label = slot.get("label", f"slot{slot_idx}")
            slot_window = slot.get("time", "")
            model_used = slot.get("model_used", f"nowcast_slot{slot_idx}_xgb_{model_version}.pkl")

            # Phase 4 fix: forecast_action.py already tags each slot with a
            # real, honest degradation signal that Phase 2/3.5 ignored --
            # `source` (data provenance: "gfs+upperair" / "gfs" /
            # "model_defaults" / "climatology" / "climatology_model_error")
            # and `fallback` (model-selection fallback, a separate concern).
            # Verified by reading forecast_action.py's slot-loop directly:
            # "model_defaults" means the real trained model ran, but on
            # climatological DEFAULT feature values because no live GFS row
            # was available for today -- a genuine model inference, but NOT
            # one driven by today's actual observed atmosphere, and it must
            # never be presented identically to a "gfs+upperair"-sourced
            # value. "climatology"/"climatology_model_error" mean no trained
            # model ran at all (a bare lookup table), so that is PROXY, not
            # a genuine ML prediction, however AVAILABLE its number is.
            slot_source = slot.get("source", "unknown")
            slot_fallback = bool(slot.get("fallback"))
            slot_fallback_reason = slot.get("fallback_reason")

            if slot_source in ("climatology", "climatology_model_error"):
                hz_status = "PROXY"
                model_for_hazard = "climatology_lookup_table"
                method_note = (f"NOT a trained-model prediction -- {slot_source} lookup for slot {slot_idx} "
                                f"({slot_label}, {slot_window} IST): {slot_fallback_reason or 'no model artifact available'}")
            elif slot_source == "model_defaults":
                hz_status = "AVAILABLE"
                model_for_hazard = model_used
                method_note = (f"trained XGBoost, {model_version} -- ran on CLIMATOLOGICAL DEFAULT input "
                                f"features (no live GFS/upper-air row was available for today), NOT today's "
                                f"observed atmosphere, for slot {slot_idx} ({slot_label}, {slot_window} IST)")
            else:
                # "gfs" or "gfs+upperair": genuine live-data-driven prediction.
                hz_status = "AVAILABLE"
                model_for_hazard = model_used
                method_note = (f"trained XGBoost, {model_version}, on live {slot_source} input -- real "
                                f"per-slot model output for slot {slot_idx} ({slot_label}, {slot_window} IST), "
                                f"the slot whose window contains this lead's valid_time "
                                f"({valid_ist.strftime('%Y-%m-%d %H:%M')} IST)")
            if slot_fallback and slot_source not in ("climatology", "climatology_model_error"):
                method_note += f" [model-selection fallback: {slot_fallback_reason}]"

            ts_prob = slot.get("ts_probability")
            if isinstance(ts_prob, (int, float)):
                lead_fc["hazards"]["thunderstorm"] = _hazard_block(
                    float(ts_prob), hz_status, "IMD station observation, VOBL/43295",
                    method_note, model_for_hazard,
                    reason=(None if hz_status == "AVAILABLE" else method_note))
            else:
                lead_fc["hazards"]["thunderstorm"] = _hazard_block(
                    None, "UNAVAILABLE", "none", "none", "none",
                    f"slot {slot_idx} has no ts_probability this cycle")

            for src_key, hz_name in (("cb_probability", "cloudburst"), ("ff_probability", "flash_flood")):
                v = slot.get(src_key)
                if isinstance(v, (int, float)):
                    lead_fc["hazards"][hz_name] = _hazard_block(
                        float(v), hz_status, "IMD station observation, VOBL/43295",
                        method_note.replace("thunderstorm", hz_name),
                        (model_for_hazard if hz_status == "PROXY" else f"{hz_name}_slot{slot_idx}_{model_version}"),
                        reason=(None if hz_status == "AVAILABLE" else method_note))
                else:
                    lead_fc["hazards"][hz_name] = _hazard_block(
                        None, "UNAVAILABLE", "none", "none", "none",
                        f"slot {slot_idx} has no {src_key} this cycle")

            lead_fc["data_quality"]["himawari"] = himawari_dq
            lead_fc["data_quality"]["terrain"] = terrain_dq


def build_canonical_forecast(gfs_freshly_fetched: bool = False, himawari_freshly_fetched: bool = False) -> dict:
    grid = read_json_or_none(PAN_INDIA_GRID_PATH)
    fc = read_json_or_none(FORECAST_JSON_PATH)
    if grid is None:
        raise RuntimeError(f"cannot build canonical forecast: {PAN_INDIA_GRID_PATH} missing or unparseable")

    generated_at = datetime.now(timezone.utc)
    gfs_valid_time = _parse_gfs_cycle_valid_time(grid.get("gfs_cycle"), grid.get("gfs_fhour"))

    # Freshness states -- computed from real on-disk file mtimes, never from
    # generated_at. generated_at is when THIS writer ran; it proves nothing
    # about whether GFS/Himawari were actually fetched fresh this run.
    gfs_state, gfs_mtime_iso, gfs_age_h = _file_freshness_state(
        PAN_INDIA_GRID_PATH, GFS_STALE_AFTER_HOURS, generated_at, gfs_freshly_fetched)
    himawari_realtime_path = REPO_ROOT / "data" / "himawari_realtime.json"
    himawari_state, himawari_mtime_iso, himawari_age_h = _file_freshness_state(
        himawari_realtime_path, HIMAWARI_STALE_AFTER_HOURS, generated_at, himawari_freshly_fetched)

    # himawari9 is nested under forecast.json's "satellite" key, not
    # top-level -- verified by reading forecast.json's actual structure this
    # phase (Phase 2's overlay_vobl_ml_cell carried this same lookup bug via
    # fc.get("himawari9", {}), which silently always returned {} since the
    # key has never been top-level; fixed here).
    himawari = ((fc.get("satellite") or {}).get("himawari9") or {}) if fc else {}
    # Phase 4 fix: forecast_action.py computes
    # `"available": bool(himawari)` from the LOADED DICT, not from whether
    # an observation actually exists -- fetch_himawari_realtime.py's own
    # total-failure placeholder (both S3 and JAXA unreachable) still writes
    # a full dict of keys, just with vobl_bt_celsius/min_bt_50km etc. set to
    # None and data_source set to "...UNAVAILABLE...". bool() on that dict
    # is True, so forecast.json's himawari9.available is TRUE even on total
    # fetch failure -- verified by reading both fetch_himawari_realtime.py's
    # placeholder branch and forecast_action.py's `available` line directly.
    # Rather than trusting that flag, read data/himawari_realtime.json's own
    # `data_source` and a real observation field directly to determine
    # actual availability -- fixed here, in the canonical writer, without
    # touching forecast_action.py's own code (out of scope this phase).
    himawari_raw = read_json_or_none(himawari_realtime_path) or {}
    himawari_data_source = str(himawari_raw.get("data_source") or "")
    himawari_available = (
        himawari_raw.get("vobl_bt_celsius") is not None
        and "UNAVAILABLE" not in himawari_data_source.upper()
    )
    # The per-lead data_quality.himawari status combines: (a) whether a real
    # observation actually exists (computed above, not forecast.json's own
    # buggy flag), and (b) whether the on-disk artifact backing it is
    # fresh/cached/stale. An available observation can still be CACHED (this
    # run did not itself invoke fetch_himawari_realtime.py).
    himawari_dq = {
        "status": himawari_state if himawari_available else "FAILED",
        "timestamp": himawari.get("timestamp_utc"),
        "artifact_mtime": himawari_mtime_iso,
        "artifact_age_hours": himawari_age_h,
    }
    terrain_block = (fc.get("terrain") or {}) if fc else {}
    terrain_dq = _source_status("AVAILABLE" if terrain_block else "UNAVAILABLE")

    cells = build_pan_india_cells(grid, generated_at, gfs_valid_time)
    if fc is not None:
        overlay_vobl_ml_cell(cells, fc, himawari_dq, terrain_dq)

    canonical = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(generated_at),
        "forecast_cycle": {
            "source": "NOAA/NOMADS GFS 0.25deg (regridded to canonical 1.0deg grid)",
            "source_cycle": grid.get("gfs_cycle"),
            "source_timestamp": _iso(gfs_valid_time) if gfs_valid_time else None,
        },
        "pipeline_run": {
            "note": ("generated_at above is when this writer ran, NOT proof that upstream sources were "
                     "freshly fetched this run -- see gfs.status / himawari.status for that."),
            "gfs_freshly_fetched_flag_set": gfs_freshly_fetched,
            "himawari_freshly_fetched_flag_set": himawari_freshly_fetched,
            "gfs": {
                "status": gfs_state,  # FETCHED | CACHED | STALE | FAILED
                "artifact_mtime": gfs_mtime_iso,
                "artifact_age_hours": gfs_age_h,
                "artifact_path": str(PAN_INDIA_GRID_PATH.relative_to(REPO_ROOT)),
            },
            "himawari": {
                "status": himawari_state if himawari_available else "FAILED",
                "artifact_mtime": himawari_mtime_iso,
                "artifact_age_hours": himawari_age_h,
                "artifact_path": str(himawari_realtime_path.relative_to(REPO_ROOT)),
            },
        },
        "grid": {
            "grid_id": "IN_992_1.0deg_v1",
            "cell_count": len(cells),
        },
        "cells": cells,
    }
    return canonical


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gfs-freshly-fetched", action="store_true",
                         help="Pass this ONLY when backend/pipeline.py ran earlier in THIS SAME pipeline "
                              "invocation and refreshed data/pan_india_grid.json (update_grid.yml). Never "
                              "pass this for a standalone/local run against pre-existing on-disk data.")
    parser.add_argument("--himawari-freshly-fetched", action="store_true",
                         help="Pass this ONLY when fetch_himawari_realtime.py ran earlier in THIS SAME "
                              "pipeline invocation (forecast_update.yml). Never pass this for a standalone/"
                              "local run against pre-existing on-disk data.")
    args = parser.parse_args()

    try:
        canonical = build_canonical_forecast(
            gfs_freshly_fetched=args.gfs_freshly_fetched,
            himawari_freshly_fetched=args.himawari_freshly_fetched)
    except Exception as e:
        print(f"FATAL: could not build canonical forecast: {e}")
        print(f"Previous {CANONICAL_PATH} (if any) left untouched.")
        return 1

    # Schema + integrity validation BEFORE any publish attempt.
    sys.path.insert(0, str(REPO_ROOT))
    from validate_canonical_forecast import validate_schema, validate_integrity  # noqa: E402

    schema_ok, schema_errors = validate_schema(canonical)
    if not schema_ok:
        print("FATAL: canonical forecast failed schema validation:")
        for e in schema_errors:
            print(f"  - {e}")
        print(f"Previous {CANONICAL_PATH} (if any) left untouched. Not published.")
        return 1

    integrity_ok, integrity_errors = validate_integrity(canonical)
    if not integrity_ok:
        print("FATAL: canonical forecast failed integrity validation:")
        for e in integrity_errors:
            print(f"  - {e}")
        print(f"Previous {CANONICAL_PATH} (if any) left untouched. Not published.")
        return 1

    try:
        atomic_write_json(CANONICAL_PATH, canonical)
    except AtomicWriteError as e:
        print(f"FATAL: atomic publish failed: {e}")
        return 1

    print(f"Canonical forecast published: {CANONICAL_PATH}")
    print(f"  generated_at: {canonical['generated_at']}")
    print(f"  cells: {len(canonical['cells'])}")
    n_ts_avail = sum(1 for c in canonical["cells"] for f in c["forecasts"] if f["hazards"]["thunderstorm"]["status"] == "AVAILABLE")
    n_cb_avail = sum(1 for c in canonical["cells"] for f in c["forecasts"] if f["hazards"]["cloudburst"]["status"] == "AVAILABLE")
    n_ff_avail = sum(1 for c in canonical["cells"] for f in c["forecasts"] if f["hazards"]["flash_flood"]["status"] == "AVAILABLE")
    print(f"  TS AVAILABLE hazard-forecasts: {n_ts_avail}")
    print(f"  CB AVAILABLE hazard-forecasts: {n_cb_avail}")
    print(f"  FF AVAILABLE hazard-forecasts: {n_ff_avail}")
    pr = canonical.get("pipeline_run", {})
    print(f"  GFS source: {pr.get('gfs', {}).get('status')} (age {pr.get('gfs', {}).get('artifact_age_hours')}h)")
    print(f"  Himawari source: {pr.get('himawari', {}).get('status')} (age {pr.get('himawari', {}).get('artifact_age_hours')}h)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
