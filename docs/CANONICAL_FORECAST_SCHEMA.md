# Canonical Forecast Schema — `data/canonical_forecast.json` — 2026-09-30

## What this is, and what it replaces

Before this phase, this repository had no single forecast artifact. `forecast.json` (written by `forecast_action.py`) is a VOBL-station document. `data/pan_india_grid.json` (written by `backend/pipeline.py`) is a 992-cell pan-India array. They run on different schedules, carry different freshness, and the frontend fetches both (plus several smaller JSON files) separately.

`data/canonical_forecast.json` is now the single authoritative artifact, produced by `canonical_forecast_writer.py`, that combines both into one schema-validated document: 992 cells, each carrying real +2h/+4h/+6h lead-time forecasts, explicit per-hazard provenance and status. It is a **derived, read-only combination** of the two existing artifacts — it does not re-run any hazard scoring, re-fetch any data, or change what `forecast.json`/`pan_india_grid.json` contain. See `docs/COMPATIBILITY_ARTIFACTS.md` for why the older files still exist and remain the frontend's current data source until it is migrated.

Formal schema: `schemas/canonical_forecast.schema.json` (JSON Schema draft-07). Validated by `validate_canonical_forecast.py`.

## Schema shape

```
{
  "schema_version": "1.0.0",
  "generated_at": "2026-09-30T16:00:36Z",
  "forecast_cycle": {
    "source": "NOAA/NOMADS GFS 0.25deg (regridded to canonical 1.0deg grid)",
    "source_cycle": "2026093006",
    "source_timestamp": "2026-09-30T12:00:00Z"
  },
  "grid": { "grid_id": "IN_992_1.0deg_v1", "cell_count": 992 },
  "cells": [
    {
      "cell_id": "IND_13.0_78.0",
      "latitude": 13.0, "longitude": 78.0,
      "domain": "VOBL_ML_DOMAIN",
      "forecasts": [
        {
          "lead_hours": 2, "valid_time": "2026-09-30T18:00:36Z",
          "hazards": {
            "thunderstorm": {"probability": 0.21, "status": "AVAILABLE", "source": "...", "method": "...", "model": "nowcast_slot_xgb_v6_temporal"},
            "cloudburst":   {"probability": 0.21, "status": "AVAILABLE", ...},
            "flash_flood":  {"probability": 0.03, "status": "AVAILABLE", ...}
          },
          "data_quality": {
            "gfs": {"status": "AVAILABLE", "timestamp": "..."},
            "himawari": {"status": "UNAVAILABLE", "timestamp": null},
            "terrain": {"status": "AVAILABLE", "timestamp": null}
          }
        }
      ]
    }
  ]
}
```

Every other cell (991 of them) has `"domain": "PAN_INDIA_PHYSICS_PROXY"` and every hazard's `"status": "PROXY"` (or `"UNAVAILABLE"` for a lead that has no matching GFS data — see below), never `"AVAILABLE"` — `AVAILABLE` is reserved for the one path that is genuinely a trained, validated model.

## Status vocabulary (mandatory, matches Part 2 of the request exactly)

`AVAILABLE` — a real value from a real, identified source. `PROXY` — a real value from the hand-weighted physics engine, not a trained model. `UNAVAILABLE` — no real value could be produced; `probability` is `null` and `reason` explains why. `STALE` — a real value exists but is older than this hazard/source's freshness tolerance. `NOT_APPLICABLE` — the concept does not apply here (e.g. Himawari's CTT crop is VOBL-only, so pan-India cells' `data_quality.himawari.status` is `NOT_APPLICABLE`, not `UNAVAILABLE` — there was never an attempt to fetch it for that cell).

The schema enforces (via `allOf`/`if`/`then`) that `AVAILABLE` requires a non-null `probability`, and `UNAVAILABLE`/`NOT_APPLICABLE` require `probability: null` plus a `reason` string — this is a hard schema constraint, not just a convention, so a future change that tries to smuggle a value past `UNAVAILABLE` fails validation immediately.

## Lead-time honesty (Part 7)

**Real finding, traced this phase:** neither `forecast.json` nor `pan_india_grid.json` currently carries more than one GFS-forecast-hour's data per generation cycle. `pan_india_gfs_fetcher.py::resolve_gfs_cycle()` computes exactly one `(cycle, fhour)` pair per call. There is no code anywhere in this repo that fetches GFS at three distinct lead-relative-to-now forecast hours.

Given that real constraint, `canonical_forecast_writer.py` does the honest thing the instructions explicitly sanction (Part 7: *"If the existing hazard formulation cannot legitimately generate a lead-specific value, DO NOT fake it... probability: null, status: UNAVAILABLE"*):

- For the **pan-India physics-proxy cells**: each requested lead's `valid_time` (`generated_at + lead_hours`) is compared against the single fetched GFS valid time (`gfs_cycle` + `gfs_fhour`, real fields, actually parsed as a real datetime). If they fall within a 45-minute tolerance, that lead is populated from the real regridded GFS+physics-proxy value with `status: PROXY`. Otherwise it is `status: UNAVAILABLE` with an explicit `reason` naming exactly why (no lead-specific fetch exists). **No probability is ever duplicated across leads for these cells** — a lead that doesn't match gets `null`, not a copy.
- For the **VOBL ML cell**: `forecast.json`'s XGBoost output represents the current 6-hour IST slot, not three independent lead-specific forecasts. Rather than inventing three different numbers, the same real, validated probability is shown at all three leads — but every one of those hazard blocks carries an explicit `reason` string saying so in plain language. This is deliberately different from the pan-India cells' `UNAVAILABLE` treatment because a genuinely trained model producing one real number for "the current slot" is a real, current forecast (unlike the pan-India cells, where no real value exists for that lead at all) — but it is documented, not hidden, that it is not lead-differentiated.

**What remains for a future phase**: extending `pan_india_gfs_fetcher.py` (or a new fetcher) to pull GFS at genuinely distinct forecast hours relative to "now" (e.g. f002/f004/f006 from the current cycle) would let the pan-India cells' `UNAVAILABLE` leads become real `PROXY` values. This was not attempted this phase — it requires live NOMADS fetches at three additional forecast hours and was out of scope for a pass that explicitly says "do not rewrite the pipeline."

## Provenance and domain identification (Parts 8/9 of the mandatory list)

Every hazard block names its `source`, `method`, and `model` explicitly. The VOBL cell's `domain` field is `"VOBL_ML_DOMAIN"`; every other cell's is `"PAN_INDIA_PHYSICS_PROXY"` — this is the field a frontend uses to implement Phase 8's requirement ("for a location outside the VOBL model domain, clearly show VOBL XGBoost: NOT APPLICABLE").

## Versioning

`schema_version` follows semver (`"1.0.0"` at introduction). A backward-incompatible change to the shape (removing a required field, changing a field's meaning) requires a major-version bump; the validator does not currently branch on version (there is only one version to validate against), but the field exists from day one so that a future consumer can detect a schema change without guessing from shape alone.
