# Phase 0.4.5 — Historical GFS Pilot Feature Contract

This freezes the feature set for the FIRST historical GFS pilot, implements
the two DERIVE-classified features as research-only, provenance-complete
helpers, and builds a tiny real-data pilot (6 representative cells, one
initialization cycle) using the already-downloaded f003/f006 files. CTT drop
rate is explicitly excluded (BLOCKED, Part 5). No files were downloaded this
phase; no production code was touched; no model was trained.

## PART 1 — Frozen historical pilot feature set

Every feature below was selected because its semantics are currently
defensible per `docs/PHASE_0_4_4_HISTORICAL_GFS_FEATURE_CONTRACT.md`. CTT
drop rate and the raw hazard-probability outputs are deliberately excluded.

| # | Feature | Exact source | Level | Unit | Direct/Derived | Derivation | Valid time | Lead | Spatial representation | Missing-value handling |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `cape` | GRIB `cape`, typeOfLevel=surface | surface | J/kg | Direct | — | per-file (f003: 03:00 UTC) | f003 (3h) | `regrid_to_cell` nearest-neighbor onto canonical cell | `None` + `missing_field_detail` entry if the message is absent |
| 2 | `cin` | GRIB `cin`, typeOfLevel=surface | surface | J/kg | Direct | — | same | f003 | same | same |
| 3 | `pwat_mm` | GRIB `pwat`, typeOfLevel=atmosphereSingleLayer | level 0 | kg/m² ≡ mm | Direct | — | same | f003 | same | same |
| 4 | `u850` | GRIB `u`, typeOfLevel=isobaricInhPa | 850 hPa | m/s | Direct | — | same | f003 | same | same |
| 5 | `v850` | GRIB `v`, typeOfLevel=isobaricInhPa | 850 hPa | m/s | Direct | — | same | f003 | same | same |
| 6 | `u200` | GRIB `u`, typeOfLevel=isobaricInhPa | 200 hPa | m/s | Direct | — | same | f003 | same | same |
| 7 | `v200` | GRIB `v`, typeOfLevel=isobaricInhPa | 200 hPa | m/s | Direct | — | same | f003 | same | same |
| 8 | `wind_shear_850_200_ms` | Derived from #4-7 | — | m/s | Derived | `sqrt((u200-u850)^2+(v200-v850)^2)` — identical to `backend/pipeline.py::compute_wind_shear` | same | f003 | computed after per-point regridding of #4-7 | `None` if any of #4-7 is `None` |
| 9 | `convergence_s` | GRIB `u`/`v` @ isobaricInhPa 850, full grid | 850 hPa | s⁻¹ | Derived | Finite-difference horizontal divergence, negated — identical to `backend/pipeline.py::compute_convergence_grid` | same | f003 | full-grid computation, then point extraction | **NOT included in the 6-row tiny pilot's row schema this phase** (the formula needs the full regional/global 850 hPa field, which the per-cell bbox extraction in `build_historical_gfs_tiny_pilot.py` does not retain at full-grid resolution for all 6 cells simultaneously) — classified DIRECTLY_REPRODUCIBLE in Phase 0.4.3B/0.4.4 and remains so, but is deferred from this specific tiny-pilot script's output rows for implementation-simplicity reasons, not a semantic blocker. Noted explicitly so its absence from the pilot JSON is not mistaken for a new finding. |
| 10 | `t850_k`, `t700_k`, `t500_k` | GRIB `t`, typeOfLevel=isobaricInhPa | 850/700/500 hPa | K | Direct | — | same | f003 | same | same |
| 11 | `rh850_pct`, `rh700_pct` | GRIB `r`, typeOfLevel=isobaricInhPa | 850/700 hPa | % | Direct | — | same | f003 | same | same |
| 12 | (pressure/geopotential) | Not required by any feature actually in this pilot's row schema — K-Index/Totals-Totals use only T and derived Td; CAPE/CIN/PWAT are themselves already-integrated quantities that do not require a separate pressure/geopotential lookup in `backend/pipeline.py`'s traced formulas | — | — | — | — | — | — | — | N/A — listed in the brief as a category to cover "if required by the traced feature path"; the traced path (Phase 0.4.4 Part 3) does not require it |
| 13 | `k_index_historical` | Derived via `scripts/historical_gfs_thermodynamics.py::compute_k_index_historical` | — | °C (converted from the raw Kelvin-scale formula output, same convention as production's `_ki_c`) | **Derived** | `(T850-T500)+Td850-(T700-Td700)`, with Td850/Td700 from Magnus-Tetens on T+RH at 850/700 hPa | same | f003 | computed per-cell from already-regridded #10/#11 | Full provenance object per row (`available`, `missing_reason`, `dewpoint_source`, `formula`, `feature_status`); never a bare number silently substituted for a missing input |
| 14 | `totals_totals_historical` | Derived via `scripts/historical_gfs_thermodynamics.py::compute_totals_totals_historical` | — | °C | **Derived** | `(T850+Td850)-2*T500`, Td850 from Magnus-Tetens | same | f003 | same | same |
| 15 | `gfs_precip_3h_interval_mm` | Derived via `scripts/historical_gfs_precipitation.py::compute_interval_precipitation`, from GRIB `tp` @ surface, stepType=accum, in BOTH f003 and f006 | surface | mm (kg/m² ≡ mm) | **Derived (REPLACE)** | `tp(f006) - tp(f003)`, valid only because both windows start at GRIB `startStep=0` (verified per-file, not assumed) | interval (03:00→06:00 UTC) | f003→f006 (3h→6h) | computed per-cell from regridded `tp` at both leads | Full provenance object; rejected (not silently zeroed) if windows don't both start at 0, if a value is missing, or if the difference is a physically-impossible negative beyond floating-point tolerance |

Explicitly excluded from this pilot, per the brief: `ctt_c` (the single-cycle
RH-threshold proxy is DIRECT and was already validated in Phase 0.4.3A/B, but
is not re-included in this specific 6-row schema since this phase's scope
was the three named blockers — CTT proxy itself was never a blocker and
remains available for a future pilot build), `ctt_drop_rate_c_hr` (BLOCKED,
see Part 5), and the raw `thunderstorm_probability`/`cloudburst_probability`/
`flash_flood_probability*` hazard outputs (their formula depends on inputs
this phase deliberately keeps separate and provenance-tagged, not silently
recombined into a single opaque score).

## PART 2 — K-Index / Totals-Totals research helper

Implemented in `scripts/historical_gfs_thermodynamics.py`. Summary (full
detail in the module's own docstrings):

- `derive_dewpoint(t_k, rh_pct)` — Magnus-Tetens inversion. Handles:
  `RH > 100` (clamped to 100, flagged via `rh_clamped`/`rh_clamped_from`,
  never silent), `RH <= 0` (clamped to a small floor `0.01%` rather than 0,
  since `RH=0` makes vapor pressure 0 and the inverse-Magnus log undefined;
  flagged the same way), missing T or RH (`available=False`,
  `missing_reason` names which), non-finite input (NaN/inf rejected
  explicitly, not passed into `math.log`/`math.exp` to crash or silently
  propagate).
- `compute_k_index_historical(...)` / `compute_totals_totals_historical(...)`
  — reproduce `backend/pipeline.py`'s exact formulas (duplicated verbatim
  as one-line expressions in this research module rather than imported, so
  that this research code can never accidentally alter production's
  formula by a shared-code edit — the brief's "do not alter production
  K/T-T code" is honored by **not importing production's formula functions
  at all**, not merely by not editing them). Every result carries
  `source="historical_gfs"`, `dewpoint_source="derived_from_temperature_rh"`,
  `formula="Magnus-Tetens"`, `feature_status="DERIVED"` — exactly the
  provenance fields the brief specifies.
- 16 focused unit tests in `tests/test_historical_gfs_thermodynamics.py`,
  covering: a physically sensible mid-range case, the saturated-air
  (RH=100%→Td=T) sanity check, RH>100 clamping, RH≤0 clamping (checked to
  not divide-by-zero or crash), missing T, missing RH, missing both, NaN
  input, inf input, a realistic K-Index/Totals-Totals computation, and each
  function's propagation of a missing/unavailable input. All 16 pass.

## PART 3 — Historical precipitation reconstruction helper

Implemented in `scripts/historical_gfs_precipitation.py`:

- `compute_interval_precipitation(...)` takes both `tp` values AND their
  GRIB `startStep`s explicitly, and **refuses** to compute a difference
  unless both accumulation windows start at step 0 (the validity condition
  established with real GRIB evidence in Phase 0.4.4 Part 2) — this is
  checked in code, not merely asserted in a docstring. It also rejects
  wrong lead ordering (`lead_end <= lead_start`), floors a tiny
  floating-point negative (≤0.05 mm tolerance, explicitly documented and
  flagged in the result, never silently discarded), and rejects a larger,
  physically-impossible negative outright with a clear reason rather than
  flooring it.
- The feature is named `gfs_precip_interval_mm` /
  `gfs_precip_3h_interval_mm` (for this specific 3h pair) — **never**
  `qpe_mm`. Production's `qpe_mm` computation in `backend/pipeline.py` is
  not imported, called, or modified anywhere in this module.
- `verify_same_initialization_cycle(a, b)` — a guard the pilot builder calls
  before trusting any interval computation, so a mismatched-cycle input
  (e.g. differencing across two different inits) is caught explicitly.
- 9 focused unit tests in `tests/test_historical_gfs_precipitation.py`:
  normal accumulation difference, equal accumulation → 0, tiny
  floating-point negative → floored+flagged, physically impossible negative
  → rejected, missing start value, missing end value, wrong lead ordering,
  nonzero `startStep` → rejected even when the raw numbers look plausible,
  and the initialization-cycle guard. All 9 pass.

## PART 4 — Tiny real historical pilot

Built by `scripts/build_historical_gfs_tiny_pilot.py`, using only the
already-downloaded `gfs.0p25.2020071500.f003.grib2` and `...f006.grib2`
files (no new downloads). Output:
`processed/historical_gfs_pilot/phase_0_4_5_tiny_pilot.json` (research
artifact, not a production data path).

**6 rows, one per representative region** (Bengaluru, Northern India,
Western India, Northeastern India, Southern India, and the SW-corner
boundary cell), each carrying:

- Identity: `region_label`, `cell_id` (via `regrid.cell_id_for`), `latitude`,
  `longitude`, `in_canonical_992_grid` (verified `True` for all 6 by direct
  membership check against `data/pan_india_grid.json`'s real `grid_cells`
  list).
- Time/lead: `initialization_time`, `valid_time`, `forecast_lead_hours`.
- Provenance: `source="gfs"`, `archive_source="NCAR_GDEX_d084001"`,
  `source_file`, `source_valid_time`.
- The DIRECT feature columns (1-7, 10-11 above), each via
  `regrid_to_cell()`.
- `wind_shear_850_200_ms` (derived, #8).
- `k_index_historical` / `totals_totals_historical` — full provenance
  objects (#13-14), not bare numbers.
- `gfs_precip_3h_interval_mm` — full provenance object (#15).
- `missing_field_detail` — explicit per-row record of anything not found
  (empty `{}` for all 6 rows in this run — every field was actually
  present for every cell).

**Real values produced** (sample, not fabricated — taken directly from the
script's actual run output this phase):

| Region | CAPE (J/kg) | CIN (J/kg) | PWAT (mm) | Shear (m/s) | K-Index (°C) | TT (°C) | 3h precip (mm) |
|---|---|---|---|---|---|---|---|
| Bengaluru | 107.0 | -24.64 | 49.1 | 27.21 | 33.87 | 41.33 | 0.25 |
| Northern India | 151.0 | -6.64 | 21.9 | 14.47 | 42.12 | 48.16 | 0.25 |
| Western India | 755.0 | -51.64 | 65.8 | 15.64 | 35.88 | 43.92 | 0.00 |
| Northeastern India | 315.0 | -23.64 | 65.9 | 20.69 | 39.00 | 41.87 | 2.5625 |
| Southern India | 509.0 | -0.64 | 43.1 | 23.35 | 35.15 | 41.91 | 0.9375 |
| Boundary (SW corner) | 1612.0 | -14.64 | 54.6 | 28.47 | 34.30 | 43.52 | 5.5625 |

All values are physically plausible for a 2020-07-15 monsoon-season
snapshot over India (no negative CAPE/PWAT, K-Index/TT in realistic ranges
for conditionally unstable monsoon soundings, no negative precipitation).

## PART 5 — CTT tendency decision

**`ctt_tendency = BLOCKED`.**

**Reason** (re-confirmed by code, not re-asserted from Phase 0.4.4): production's
`ctt_drop_rate_c_hr` (`backend/pipeline.py`, `load_prev_ctt_map`/
`ctt_hours_elapsed`/lines 655-663) compares the current run's `ctt_c`
against `data/ctt_grid.json`, which was written by the **previous
invocation of the same script** — i.e. a **different, earlier GFS
initialization cycle** (since every production invocation always requests
`fhour=0`, two successive runs are two different analysis-equivalent
snapshots of two different cycles, not two leads of one cycle). f003 and
f006 share one initialization (`2020-07-15 00:00 UTC`, confirmed identical
`dataDate`/`dataTime` in both files this phase and in Phase 0.4.3A/B) — so
no computation built from only these two files can be the cross-cycle
quantity production actually computes.

**Minimum next data acquisition required**: GFS data from a **second,
earlier initialization cycle**, at a lead matching production's own
cadence. Production's `CTT_PREV_MAX_AGE_HOURS = 7.0` gate and its 4x/day
cron (`00/06/12/18Z`, `update_grid.yml`) mean the natural "previous cycle"
for a `2020-07-15 00Z` run is the **`2020-07-14 18Z`** cycle, 6 hours
earlier — within the 7-hour gate. Since production always fetches `f000`,
the exact historical analog would be:

- `gfs.0p25.2020071418.f000.grib2` (the "previous" snapshot)
- `gfs.0p25.2020071500.f000.grib2` (the "current" snapshot — note this is
  **f000**, not f003/f006; the two files already downloaded this project
  are forecasts at the 00Z cycle, not the f000 analysis-equivalent field
  production actually uses for `ctt_c` itself)

Exact GDEX direct-download URLs (per the naming convention and base URL
documented in `docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md`,
not independently re-fetched this phase since no downloads were performed):

```
https://data.gdex.ucar.edu/d084001/2020/20200714/gfs.0p25.2020071418.f000.grib2
https://data.gdex.ucar.edu/d084001/2020/20200715/gfs.0p25.2020071500.f000.grib2
```

If instead the project decides CTT tendency should be reproduced at the
f003/f006 lead (matching what is already downloaded, rather than f000,
matching production's literal invocation), the equivalent pair would be:

```
https://data.gdex.ucar.edu/d084001/2020/20200714/gfs.0p25.2020071418.f003.grib2
https://data.gdex.ucar.edu/d084001/2020/20200715/gfs.0p25.2020071500.f003.grib2
```

Neither pair has been downloaded this phase. This report does not choose
between the "f000, matching production exactly" and "f003/f006, matching
what is already in hand" options — that choice interacts with the same
lead-time product decision flagged unresolved in Phase 0.4.4 Part 9 (whether
the historical pilot should target production's literal f000 fetch, or the
project's stated 2-6h nowcasting goal), and is not re-decided here.

## PART 6 — Precipitation product decision

Documented explicitly, not conflated:

- **CURRENT PRODUCTION**: `qpe_mm` is computed from a `tp`/`acpcp` lookup at
  whatever `fhour` the pipeline is invoked with; the live workflow always
  invokes with `fhour=0` (Phase 0.4.4 Part 1, re-confirmed by code this
  phase), so `qpe_mm` is **structurally near-zero** in live operation today.
  **Not modified this phase.**
- **HISTORICAL PILOT**: `gfs_precip_3h_interval_mm` is a physically
  meaningful, non-trivial interval-precipitation feature (values 0.0-5.56mm
  across the 6 pilot cells, Part 4), derived from a validated GRIB
  accumulation difference. It is **not the same quantity** as live
  production's `qpe_mm` and is never presented as such anywhere in this
  phase's code or output (distinct feature name, distinct provenance
  object, distinct module).
- **Flagged explicitly as a future product/model decision**: should
  production's `qpe_mm` eventually be redefined to request a non-zero-window
  lead (e.g. always fetch f006, or compute the same interval-difference
  production-side), or should the historical pilot's
  `gfs_precip_3h_interval_mm` simply be treated as a new, separately-named
  feature with no claimed production equivalent? This report takes no
  position — it only ensures the two are never silently equated.

## PART 7 — Validation (performed against the real tiny-pilot output)

| Check | Result |
|---|---|
| All mapped values are from real GRIB files | **PASS** — every value traced to `gfs.0p25.2020071500.f003.grib2` / `...f006.grib2`, read via ecCodes message enumeration; zero synthetic/placeholder values anywhere in the 6-row output |
| Initialization time correct | **PASS** — `2020-07-15 00:00 UTC` for both files, read from `dataDate`/`dataTime`, matching Phase 0.4.3A/B's independently-confirmed value |
| Valid time correct | **PASS** — f003 → `2020-07-15 03:00 UTC`, f006 → `2020-07-15 06:00 UTC`, read from `validityDate`/`validityTime` |
| +3h and +6h semantics correct | **PASS** — `forecastTime` fields read directly as 3 and 6 respectively; the precipitation interval explicitly spans `(03:00,06:00]` |
| Canonical cell IDs valid | **PASS** — all 6 target `(lat,lon)` pairs confirmed present in `data/pan_india_grid.json`'s real `grid_cells` list (`in_canonical_992_grid: True` for all 6); IDs constructed via the existing `regrid.cell_id_for`, not invented |
| No duplicate cell/time/lead rows | **PASS** — enforced by an in-script `assert` on `(cell_id, init_time, lead)` uniqueness; `n_unique_cell_time_lead_keys == n_rows == 6` |
| No synthetic values | **PASS** — every numeric value originates from `regrid_to_cell()` applied to a real extracted GRIB array; no fallback/default/placeholder numbers are substituted anywhere (missing inputs produce `None` + an explicit `missing_field_detail`/`missing_reason`, confirmed empty for all 6 rows this run) |
| Precipitation difference physically valid | **PASS** — all 6 cells: `gfs_precip_3h_interval_mm.available=True`, `rejected=False`, values in `[0.0, 5.5625]` mm, none negative, the `start_step=0` validity check passed for every cell (same two GRIB messages reused across all 6 cells, so this check ran once per cell but against the same validated window pair) |
| K/T-T derivations finite where inputs exist | **PASS** — all 6 rows: `k_index_historical.available=True` and `totals_totals_historical.available=True`, both `value_c` finite (checked via `math.isfinite` in the unit tests' pattern, and manually confirmed finite in this run's actual output) |
| Provenance complete | **PASS** — every derived field carries `source`/`dewpoint_source`/`formula`/`feature_status` (thermodynamics) or `feature_name`/`source_field`/`accumulation_semantics`/time+lead fields (precipitation); every row carries `source`, `archive_source`, `source_file`, `source_valid_time` |

**Tests run this phase**:
- New focused tests: `tests/test_historical_gfs_thermodynamics.py` (16 tests)
  and `tests/test_historical_gfs_precipitation.py` (9 tests) — **25/25 pass**.
- Full existing suite: `pytest . --ignore=test_himawari.py
  --ignore=test_nomads.py --ignore=test_segments.py --ignore=test_segments_v2.py`
  → **176 passed** (151 baseline + 25 new this phase), same 4 pre-existing
  environment-only collection errors (donfig ×3, NOMADS 403 ×1), nothing
  weakened, no freshness test touched.

## PART 8 — Final status

### A. FEATURES READY FOR HISTORICAL PILOT
`cape`, `cin`, `pwat_mm`, `u850`, `v850`, `u200`, `v200`,
`wind_shear_850_200_ms`, `t850_k`/`t700_k`/`t500_k`, `rh850_pct`/`rh700_pct`
— all DIRECT, all confirmed present and correctly regridded in the real
6-cell pilot this phase. `ctt_c` (the single-cycle RH-threshold proxy) also
remains DIRECT per Phase 0.4.3B/0.4.4 though not re-included in this
pilot's row schema (see Part 1 note). `convergence_s` is also DIRECT but
deferred from this tiny pilot's per-cell JSON rows for an implementation
reason (full-grid dependency), not a semantic one.

### B. FEATURES DERIVED FOR HISTORICAL PILOT
`k_index_historical`, `totals_totals_historical` (Magnus-Tetens dewpoint,
fully implemented and tested this phase, with explicit `DERIVED` tagging);
`gfs_precip_3h_interval_mm` (validated accumulation-differencing, fully
implemented and tested this phase, explicitly never called `qpe_mm`).

### C. FEATURES BLOCKED
`ctt_drop_rate_c_hr` / CTT temporal tendency — requires a second historical
initialization cycle not yet downloaded (exact candidate files/URLs given in
Part 5). Not fabricated this phase.

### D. FEATURES REQUIRING FUTURE PRODUCT DECISION
- Whether `qpe_mm` should be redefined in production (e.g. to fetch a
  non-zero-window lead) or whether `gfs_precip_3h_interval_mm` should simply
  stand as a new, separately-named feature with no claimed production
  equivalent (Part 6).
- Whether the historical pilot should target production's literal `f000`
  fetch or the project's stated 2-6h nowcasting window (carried over,
  unresolved, from Phase 0.4.4 Part 9) — this also determines which CTT
  tendency acquisition pair (f000 vs f003) in Part 5 is the "correct" one.
- Whether a Magnus-Tetens-derived K-Index/Totals-Totals is an acceptable
  training input given its approximation error relative to production's
  model-native dewpoint path (Phase 0.4.4 Part 3) — implemented and tested
  this phase, but not yet blessed as a modeling decision.

### E. EXACT NEXT DATA ACQUISITION REQUIRED
Two additional historical GFS files, from the initialization cycle 6 hours
before the one already downloaded, at a lead matching whichever semantic
target (f000 vs f003/f006) Part D above resolves to:
```
gfs.0p25.2020071418.f000.grib2  (or gfs.0p25.2020071418.f003.grib2)
gfs.0p25.2020071500.f000.grib2  (or gfs.0p25.2020071500.f003.grib2, already in hand for the f003 case)
```
Not downloaded this phase.

### FINAL STATUS: **HISTORICAL_PILOT_READY**

The tiny pilot (6 real cells, 1 cycle, 15 DIRECT/DERIVED feature values each,
zero synthetic data, zero fabricated semantics, CTT tendency correctly
excluded rather than faked) is complete, validated, and reproducible from
the files already in hand. This status applies strictly to the **tiny
pilot**, not to full production feature parity — Parts D and C above remain
open and must be resolved before any larger-scale historical dataset is
built.

## Scope confirmation

- **Files created this phase**: this report;
  `scripts/historical_gfs_thermodynamics.py`;
  `scripts/historical_gfs_precipitation.py`;
  `scripts/build_historical_gfs_tiny_pilot.py`;
  `tests/test_historical_gfs_thermodynamics.py`;
  `tests/test_historical_gfs_precipitation.py`;
  `processed/historical_gfs_pilot/phase_0_4_5_tiny_pilot.json` (pilot
  output data, not code).
- **Files modified**: none.
- **No files were downloaded.** `data/pan_india_grid.json` was read-only
  (membership check only), not modified, per the brief.
- No production code (`backend/pipeline.py`, `forecast_action.py`,
  `canonical_forecast_writer.py`, any schema, the canonical grid, any
  workflow, the frontend) was changed. No model was trained. No large-scale
  dataset was built — only a 6-row tiny pilot. Nothing was deployed,
  committed, or pushed.
