# Forecast lead-time validation

Generated 2026-11-15 16:13 UTC from the artifacts.

## What is validated and what is not

DRIFT distinguishes **seven** times; only the first five are recorded:

| Time | Recorded? | Where |
|---|---|---|
| Model initialisation | yes | GRIB message `dataDate/dataTime`, cross-checked against the request |
| Forecast valid time | yes | init + forecast hour (recomputed by the verifier) |
| Input acquisition time | yes | `acquired_at_utc` per file |
| Input publication/availability time | **yes (observed)** | S3 `Last-Modified` of each GFS file on the AWS mirror |
| NWP forecast hour (valid - init) | yes | `nwp_forecast_hour` |
| Time the DRIFT forecast became available to DRIFT/users | **no** | not recorded: the artifact is committed by CI and deployed later, and no publication confirmation exists |
| User-facing operational lead (valid - DRIFT availability) | **no -> null** | `operational_lead_hours: null`, status `UNAVAILABLE_NO_DRIFT_PUBLICATION_TIME` |

Consequently **no operational 2-6 h lead time is claimed for any hazard** and automated alerting stays disabled. This is enforced by `backend/alert_policy.py` (empty registries) and tested.

## Supported forecast horizons (verified)

| Source | Horizons actually produced | Input latency (observed) | Evaluated? |
|---|---|---|---|
| GFS 0.25 deg fields (all land cells) | NWP hours [3, 6, 9, 12, 15, 18, 21, 24] from 2026-11-15T00:00:00Z | 3.55-3.84 h after init (this cycle) | fields are model guidance, not forecasts of an event |
| Thunderstorm model | +6, +12, +18, +24 h after a 00Z/12Z init | adds the GFS latency above | yes, per lead (HAZARD_MODEL_VALIDATION.md) |
| Heavy 3 h rainfall model | targets ['20mm_f9'] | same | yes, per lead |
| 2, 3, 4, 5 h leads | **not produced** | - | **not supported**: GFS cycles arrive every 6 h and the training design uses 6/12/18/24 h; no hourly-initialised system exists |

These are **NWP forecast hours** (valid - init). Measured from the moment the GFS input was published (S3 time) the effective lead is shorter by the 3.5-4 h publication delay; measured from DRIFT's own availability it is unknown. Examples for the current cycle:

| NWP forecast hour | valid UTC | GFS file published (UTC) | lead from input publication (h) |
|---|---|---|---|
| f03 | 2026-11-15T03:00:00Z | 2026-11-15T03:46:26Z | -0.77 |
| f06 | 2026-11-15T06:00:00Z | 2026-11-15T03:32:47Z | 2.45 |
| f09 | 2026-11-15T09:00:00Z | 2026-11-15T03:33:28Z | 5.44 |
| f12 | 2026-11-15T12:00:00Z | 2026-11-15T03:34:30Z | 8.43 |
| f15 | 2026-11-15T15:00:00Z | 2026-11-15T03:35:32Z | 11.41 |
| f18 | 2026-11-15T18:00:00Z | 2026-11-15T03:50:32Z | 14.16 |
| f21 | 2026-11-15T21:00:00Z | 2026-11-15T03:36:36Z | 17.39 |
| f24 | 2026-11-16T00:00:00Z | 2026-11-15T03:37:37Z | 20.37 |

A forecast valid 3 h after init was published 3.5 h after init, i.e. its lead from the first moment the *input* existed is NEGATIVE: such hours are shown as `PAST_VALID_TIME`/never as a warning. Only forecast hours >= about +6 h after init have a positive lead from input publication, and that lead does not include DRIFT's own processing and deployment time.

## Per-lead skill

Each model is trained and evaluated separately per lead (tables in HAZARD_MODEL_VALIDATION.md). Persistence (observation before issue) is compared for thunderstorms; for rainfall the raw GFS 3 h forecast is the baseline. A 2-6 h *nowcast* from observations (satellite cloud-top cooling, radar, lightning, QPE) is **not implemented**: the calibrated IR source is not accessible (see docs/data_sources/SATELLITE_AND_REANALYSIS_ACCESS.md).

## Exactly what is needed to claim a 2-6 h operational lead

1. Record the time each DRIFT forecast artifact becomes available to users (deployment confirmation) and compute lead from it.
2. A rapid-update (hourly or faster) initialisation: observation-driven nowcasting inputs (calibrated geostationary IR/WV, radar, lightning, hourly QPE) with an archive long enough to train and validate +2..+6 h targets.
3. Re-evaluate every lead with persistence and raw-NWP baselines, then register the lead window in the alert policy.
