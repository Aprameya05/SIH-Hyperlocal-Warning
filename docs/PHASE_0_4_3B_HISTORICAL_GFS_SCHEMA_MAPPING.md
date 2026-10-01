# Phase 0.4.3B — Historical GFS → Production Feature Schema Forensic Mapping

## Scope and method

This report traces the **actual code** of the production pan-India GFS
pipeline (`backend/pipeline.py`, confirmed in Section 0 below to be the
sole, currently-wired writer of `data/pan_india_grid.json`) feature-by-feature,
and maps each feature against the **actual GRIB2 content** of the two real
historical files downloaded in Phase 0.4.3A:

- `data/external/historical_gfs/raw/gfs.0p25.2020071500.f003.grib2`
- `data/external/historical_gfs/raw/gfs.0p25.2020071500.f006.grib2`

Every GRIB fact below was re-verified this phase by direct ecCodes message
enumeration against the real files (not re-used blindly from Phase 0.4.3A's
JSON output, though it is consistent with it). Nothing here is inferred from
variable names alone; level types, units, and step semantics are from actual
GRIB keys.

## 0. Which code is actually "production" — resolving a real ambiguity found this phase

The repository contains **two** GFS-to-pan-India-grid pipelines, and before
building a mapping table it had to be determined which one is the real
"production feature schema," because a stale file on disk made this
ambiguous:

- `backend/pipeline.py`, wired into `.github/workflows/update_grid.yml`
  (`run: python backend/pipeline.py`, confirmed by reading the workflow file),
  is documented in `docs/PIPELINE_OWNERSHIP.md` as the sole writer of
  `data/pan_india_grid.json` since the 2026-09-30 Phase 4.5 fix, and the
  dashboard (`index.html`) reads only that file.
- `pan_india_gfs_fetcher.py`, wired into `.github/workflows/forecast_update.yml`,
  now writes only to the non-canonical `data/pan_india_grid_slotrun.json` per
  the same ownership doc — "nothing else currently reads that file."

**However**, the `data/pan_india_grid.json` actually present in this checkout
has a cell schema (`cape, cin, pwat, k_index, totals_totals, u850, v850, u500,
v500, wind_shear_ms, apcp_mm, t2m_c, rh2, thunderstorm_probability, ...`) that
matches `pan_india_gfs_fetcher.py`'s field names (`u500`/`v500`/`apcp_mm`/
`t2m_c`/`rh2`), **not** `backend/pipeline.py`'s actual output schema (which
instead writes `ctt_c`, `convergence_s`, `ctt_drop_rate_c_hr`, `qpe_mm`,
`pwat_mm`, `flash_flood_probability_terrain_adjusted`, `terrain`, and has no
`u500`/`v500`/`apcp_mm`/`t2m_c`/`rh2` keys at all). This means **the on-disk
`data/pan_india_grid.json` in this checkout is stale** — it predates the
Phase 4.5 ownership fix and has not been regenerated since (this sandbox has
no live NOMADS access to regenerate it, confirmed blocked in earlier phases).

**This matters for this report**: the feature inventory below is traced from
**`backend/pipeline.py`'s actual code**, which is the real current production
definition per the workflow wiring and ownership doc — not from the stale
on-disk JSON's incidental key names. This discrepancy is flagged here, not
silently resolved, because it is a genuine finding about repository state,
not something this phase is authorized to fix (out of scope: no production
file was modified).

A third, separate GFS consumer exists — `gfs_fetcher.py` — which computes a
VOBL-point-only (Bengaluru airport, 12.97°N/77.58°E) stability-index time
series (`data/upperair_realtime_43295.csv`) for the original CSIR
thunderstorm model, independent of the 992-cell pan-India grid. It is
**out of scope** for this report, which addresses the pan-India grid pipeline
specifically (the one the historical-GFS pilot work has targeted since Phase
0.4.3's bbox/cell-mapping framing); its stability-index set overlaps heavily
with `backend/pipeline.py`'s (CAPE, CIN, K-Index, LI, TT, PW, wind) and the
findings below (dewpoint absence, precipitation semantics, f000-vs-forecast
lead time) apply to it directionally the same way, but it was not separately
code-traced here.

## 1. Production feature inventory (from `backend/pipeline.py`, traced line-by-line)

Fields requested from NOMADS (`GFS_VARS`, `PRESSURE_LEVELS`, lines 91-108):
`var_CAPE, var_CIN, var_PWAT, var_UGRD, var_VGRD, var_TMP, var_DPT, var_RH,
var_HGT, var_APCP` at levels `850/700/500/400/300/250/200 mb, surface, 2 m
above ground`.

Parsing: `read_grib_fields()` (line 263) uses `xarray`+`cfgrib`, opening the
file once per `typeOfLevel` in
`[isobaricInhPa, surface, heightAboveGround, atmosphereSingleLayer,
tropopause]` with `filter_by_keys`, falling back to raw eccodes
(`_read_grib_eccodes`, line 338) if cfgrib/xarray is unavailable. Fields are
keyed as `f"{var}_{level_type}_{level}"` (cfgrib's own variable short names,
e.g. `t`, `u`, `v`, `r`, `gh`, `cape`, `cin`, `pwat`).

| # | Production feature | Source lookup keys (`field(...)` call, in order tried) | typeOfLevel / level | Transformation | Direct/Derived |
|---|---|---|---|---|---|
| 1 | `cape` | `cape_surface`, `cape_convectivelyAvailablePotentialEnergy_surface`, `CAPE_surface` | surface | round(1) | Direct |
| 2 | `cin` | `cin_surface`, `cin_convectiveInhibition_surface`, `CIN_surface` | surface | round(1) | Direct |
| 3 | `pwat` → output key `pwat_mm` | `pwat_atmosphereSingleLayer`, `pwat_entireAtmosphere`, `PWAT_atmosphereSingleLayer` | atmosphereSingleLayer | round(1) | Direct |
| 4 | `T850`, `T700`, `T500` (intermediate) | `t_isobaricInhPa_{850,700,500}` | isobaricInhPa | — | Direct |
| 5 | `Td850`, `Td700` (intermediate) | `d_isobaricInhPa_{850,700}`, `dpt_isobaricInhPa_{850,700}` | isobaricInhPa | — | Direct **if present** (see Section 7 — NOT present in this archive) |
| 6 | `u850`, `v850` (intermediate) | `u_isobaricInhPa_850`, `v_isobaricInhPa_850` | isobaricInhPa | — | Direct |
| 7 | `u200`, `v200` (intermediate) | `u_isobaricInhPa_200`, `v_isobaricInhPa_200` | isobaricInhPa | — | Direct |
| 8 | `k_index` | `compute_k_index(T850, Td850, T700, T500, Td700)` = `(T850-T500)+Td850-(T700-Td700)` | derived from #4+#5 | °C conversion if >200 | Derived (blocked by #5) |
| 9 | `totals_totals` | `compute_totals_totals(T850, Td850, T500)` = `(T850+Td850)-2*T500` | derived from #4+#5 | °C conversion if >200 | Derived (blocked by #5) |
| 10 | `wind_shear_ms` | `compute_wind_shear(u850,v850,u200,v200)` = `sqrt((u200-u850)^2+(v200-v850)^2)` | derived from #6+#7 | round(2) | Derived — **850-200 hPa bulk shear**, not 850-500 hPa (that definition lives only in the non-canonical `pan_india_gfs_fetcher.py`) |
| 11 | `ctt_c` | `compute_ctt()`: scans 250/300/400/500/700 hPa for highest level with RH≥80%, returns T there | `r_isobaricInhPa_{lev}`, `t_isobaricInhPa_{lev}` | K→°C | Derived |
| 12 | `convergence_s` | `compute_convergence_grid(u850_arr, v850_arr, ...)`: finite-difference horizontal divergence of the full 850 hPa U/V field, negated | derived from #6 (full grid, not just the point) | round(6) | Derived |
| 13 | `ctt_drop_rate_c_hr` | `(prev_ctt - ctt_c) / prev_age_h` using the **previous pipeline run's** `ctt_grid.json` | temporal, cross-cycle | round(2) | Derived, **requires a second, earlier forecast cycle's output** |
| 14 | `qpe_mm` | `apcp_arr = field("tp_surface","tp_surface_0","APCP_surface","acpcp_surface","asnow_surface")`, floored at 0 | surface, accumulated | round(1) | Direct (with a precipitation-type fallback chain — see Section 4) |
| 15 | `thunderstorm_probability`, `cloudburst_probability`, `flash_flood_probability`, `flash_flood_probability_terrain_adjusted` | `hazard_probabilities()` — hand-weighted linear combination of #1,#2,#8,#9,#10,#11,#12,#13,#14, then `terrain_lookup.apply_terrain_to_ff()` | — | — | Derived (heuristic, not a trained model — consistent with the Master Audit's finding) |

Two intermediate fields (`T850/T700/T500`, `Td850/Td700`) are not written to
`pan_india_grid.json` directly; they only feed K-Index/Totals-Totals.

## 2. Historical GDEX file inventory (re-verified this phase, direct ecCodes enumeration)

Both files: 588 messages, global grid, Nx=1440/Ny=721, 0.25°, lat 90→-90, lon
0→359.75 (consistent with Phase 0.4.3A). Full shortName set actually present
in the file (every message, not just the ones matched by a keyword search):

```
10u, 10v, 2d, 2r, 2sh, 2t, 4lftx, 5wavh, ICSEV, SUNSD, VRATE, absv, acpcp,
aptmp, avg_al, avg_ishf, avg_pres, avg_slhtf, avg_t, avg_utaua, avg_vtaua,
cape, cfrzr, ci, cicep, cin, clwmr, cpofp, cpr, crain, csnow, cwat, cwork,
fldcp, gflux, gh, grle, gust, hindex, hlcy, icaht, icmr, iegwss, ingwss,
landn, lftx, lsm, mslet, o3mr, orog, plpl, prate, pres, prmsl, pt, pwat, q,
r, refc, rwmr, sde, sdlwrf, sdswrf, sdwe, snmr, soilw, sp, st, sulwrf,
suswrf, t, tcc, tmax, tmin, tozne, tp, trpp, u, unknown, ustm, v, vis, vstm,
vwsh, w, watr, wilt, wz
```

Notably: `t` (temperature) and `r` (RH) exist at isobaric levels including
850/700/500 hPa. `u`/`v` exist at isobaric levels `1, 2, 3, 5, 7, 10, 20, 30,
50, 70, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750,
800, 850, 900, 925, 950, 975, 1000` (both 850 and 200 confirmed present).
**No `d` or `dpt` shortName exists at any isobaric level** — the only
dewpoint-related field in the entire file is `2d` (2 m dewpoint,
`heightAboveGround`). `vwsh` (a direct vertical-wind-shear field) and
`4lftx`/`lftx` (lifted index) exist but are not used by `backend/pipeline.py`
today.

## 3. Mapping table

| Production feature | Historical GFS field | Direct / Derived | Unit conversion | Temporal issue | Spatial issue | Status |
|---|---|---|---|---|---|---|
| `cape` | `cape` @ `surface`, level 0, J/kg | Direct | None (both J/kg) | None | None | **EXACT_MATCH** |
| `cin` | `cin` @ `surface`, level 0, J/kg | Direct | None | None | None | **EXACT_MATCH** |
| `pwat_mm` | `pwat` @ `atmosphereSingleLayer`, level 0, kg/m² | Direct | kg/m² numerically equals mm of water depth, no conversion needed (same convention production already uses) | None | None | **EXACT_MATCH** |
| `T850/T700/T500` (intermediate) | `t` @ `isobaricInhPa` 850/700/500, K | Direct | None | None | None | **EXACT_MATCH** |
| `u850/v850`, `u200/v200` (intermediate) | `u`/`v` @ `isobaricInhPa` 850 and 200, m/s | Direct | None | None | None | **EXACT_MATCH** |
| `Td850/Td700` (intermediate) | **no field** — no `d`/`dpt` at `isobaricInhPa` 850/700 anywhere in either file | N/A | N/A | N/A | N/A | **NOT_AVAILABLE** (direct); see Section 7 for a derivable alternative |
| `k_index` | blocked by `Td850/Td700` absence | Derived, but one input is NOT_AVAILABLE | — | — | — | **UNRESOLVED** — not reproducible as currently coded; see Section 7 |
| `totals_totals` | same as above | Derived, blocked | — | — | — | **UNRESOLVED** — same reason |
| `wind_shear_ms` (850-200 hPa bulk shear) | `u`/`v` @ `isobaricInhPa` 200 and 850, both present | Derived, identical formula reproducible | None | None | None | **EXACT_MATCH** |
| `ctt_c` | `r`/`t` @ `isobaricInhPa` 250/300/400/500/700, all confirmed present | Derived, identical algorithm reproducible | K→°C already handled by existing code | None | None | **EXACT_MATCH** |
| `convergence_s` | `u`/`v` @ `isobaricInhPa` 850, full-grid field | Derived, identical finite-difference method reproducible | None | None | Needs the full regional/global 0.25° field, not just point values — available (this is a global file) | **EXACT_MATCH** |
| `ctt_drop_rate_c_hr` | requires `ctt_c` from **two different, sequential forecast cycles** (e.g. 00Z and the prior 18Z init) | Derived, cross-cycle | — | **Needs ≥2 historical cycles per training sample**, not obtainable from a single cycle's f003/f006 pair (those are two *leads of the same init*, not two *inits*) | — | **UNRESOLVED as currently framed** — reclassified under Section 8 as REQUIRES MULTIPLE FORECAST CYCLES |
| `qpe_mm` | `tp` @ `surface`, accum, kg/m²; also `acpcp`, `prate` present | Direct, but **accumulation window differs by lead**, see Section 4 | kg/m² ≈ mm, consistent with production's own convention | **Accumulation window is lead-dependent (0-3h for f003, 0-6h for f006), not a fixed "6-hour" window as the pipeline's own docstring assumes** | None | **SEMANTICALLY_DIFFERENT** — see Section 4 |
| `thunderstorm_probability` / `cloudburst_probability` / `flash_flood_probability*` | formula over the above | Derived | — | Depends on `k_index`/`totals_totals` (UNRESOLVED) and `qpe_mm` (SEMANTICALLY_DIFFERENT) | — | **UNRESOLVED** (inherits its inputs' status; cannot be honestly reproduced end-to-end until Section 7 and Section 4 are resolved) |

## 4. Special case: precipitation (`tp` / `acpcp` / `prate`)

Direct GRIB-metadata inspection of both files (re-verified this phase, not
assumed from Phase 0.4.3A):

| File | Field | `stepType` | `stepRange` | Meaning |
|---|---|---|---|---|
| f003 | `tp` | accum | `0-3` | Total precipitation accumulated from init (0h) to valid time (3h) — a **3-hour** accumulation |
| f003 | `acpcp` | accum | `0-3` | Convective-only precipitation, same 3h window |
| f003 | `prate` | instant | `3` | Instantaneous precip rate (kg/m²/s) at the valid time |
| f003 | `prate` | avg | `0-3` | Average precip rate over the 0-3h window |
| f006 | `tp` | accum | `0-6` | Total precipitation accumulated from init (0h) to valid time (6h) — a **6-hour** accumulation |
| f006 | `acpcp` | accum | `0-6` | Convective-only, 0-6h |
| f006 | `prate` | instant/avg | `6` / `0-6` | Same pattern as f003 |

**This is a real, load-bearing finding.** `backend/pipeline.py`'s own
docstring (line 15) says: *"QPE proxy: uses GFS APCP (6-hour accumulated
precipitation, mm)"* — but the pipeline's actual invocation
(`.github/workflows/update_grid.yml` runs `python backend/pipeline.py` with
**no `--fhour` argument**, so `run(fhour=0)`'s default applies) fetches
**f000**, not f006. At f000, any `accum` field's window is `0-0` (i.e.,
zero-width — GFS analysis fields at f000 typically report APCP as 0 or
missing, which is exactly consistent with the code's own defensive floor:
*"APCP can be negative (artifact) -- floor at 0"*). So in current live
production, `qpe_mm` is likely **near-always 0 or None in practice**, not a
true 6-hour QPE signal — the docstring describes an aspirational/designed
semantic that the actual default invocation does not realize.

For the **historical archive**, `tp`'s accumulation window is tied to the
requested lead (0-3h at f003, 0-6h at f006) — it is **not** a fixed 6-hour
window regardless of lead, and it is **not** the same quantity as whatever
non-zero accumulation a live f006 NOMADS fetch would produce (which this
audit has not independently verified, since production's wired invocation
never requests f006). Given this, `qpe_mm`/precipitation is marked
**SEMANTICALLY_DIFFERENT**, not EXACT_MATCH and not simply NOT_AVAILABLE: the
physical field exists and is well-defined, but equating "historical f006
`tp` (0-6h accum)" with "production's `qpe_mm`" would be asserting an
equivalence this audit cannot confirm, because production's actual default
run (f000) does not produce a comparable accumulation window at all. A future
phase choosing to train on historical f006's `tp` as a QPE analog would need
to either (a) explicitly redefine production's QPE semantics to always use a
fixed forecast lead (e.g. always fetch f006, not f000), or (b) accept that
historical-$tp$-based QPE and live-production QPE are not the same quantity
today.

## 5. Special case: PWAT / IWV

No further correction is needed here — `docs/PHASE_0_1_FF_CURRENT_STATE.md`
and the Phase 0.1 completion report already corrected the earlier mislabeling
of this field as "IWV" (which would have implied an INSAT-derived Integrated
Water Vapor product). `backend/pipeline.py` line 629-632 reads
`pwat_atmosphereSingleLayer` and stores it as `pwat_mm` — a plain GFS
Precipitable Water field, not an INSAT-derived quantity. The historical
archive's `pwat` field (`atmosphereSingleLayer`, level 0, kg/m², confirmed in
Phase 0.4.3A and re-confirmed here) is the **exact same physical quantity**
from the **same model family** (GFS). `pwat_mm` → **EXACT_MATCH**. No "IWV"
terminology exists anywhere in current production code to re-verify against.

## 6. CAPE / CIN

- `cape`: shortName `cape`, found at **three** level types in the historical
  archive — `surface` (level 0), and `pressureFromGroundLayer` (levels 18000
  and 25500, i.e. 0-180 hPa and 0-255 hPa mixed-layer variants). Production's
  `field()` lookup tries `cape_surface` first and matches it directly — same
  level type (`surface`), same units (J/kg) as the archive's `surface`-level
  `cape` message. **EXACT_MATCH** on the exact field production actually
  selects; the two `pressureFromGroundLayer` variants are unused by
  production and not evaluated further here.
- `cin`: identical structure — `cin` @ `surface` (used by production) plus
  two `pressureFromGroundLayer` variants (unused). **EXACT_MATCH**.
- No unit conversion or derivation occurs for either in production; both are
  stored as J/kg, rounded to 1 decimal.

## 7. Wind / shear

Production's `compute_wind_shear(u850, v850, u200, v200)` (line 397) computes
**bulk shear between 850 hPa and 200 hPa** as
`sqrt((u200-u850)^2 + (v200-v850)^2)` — a vector-difference magnitude, not a
layer-average or directional shear. Both required levels (850 and 200 hPa,
both U and V) are confirmed directly present in the historical archive at
`isobaricInhPa`. The identical derivation can be applied unchanged.
**EXACT_MATCH** for the production (`backend/pipeline.py`) definition.

Note: the separate, **non-canonical** `pan_india_gfs_fetcher.py` computes
shear between 850 and **500** hPa instead (`wind_shear =
sqrt((u500-u850)^2+(v500-v850)^2)`, line 199). That file no longer writes
`pan_india_grid.json` (Section 0), so its shear definition is not the
production one this report targets — but both 500 hPa and 200 hPa U/V are
present in the archive, so either definition would be reproducible from this
archive if that file's output ever becomes canonical again.

Separately: the archive contains a **direct** `vwsh` (vertical wind shear)
field that production does not use at all today — not evaluated further
since it is not part of the current feature schema, but noted as an unused
opportunity.

**Td-dependent features (k_index, totals_totals) are blocked**: as found in
Section 2, no dewpoint field exists at isobaric levels in this archive.
Production's own code has no fallback derivation (it only tries the direct
`d_isobaricInhPa_*`/`dpt_isobaricInhPa_*` keys and returns `None` if absent).
A derivable alternative exists in principle — dewpoint can be computed from
temperature and relative humidity via a standard approximation (e.g. the
Magnus-Tetens formula, `Td = T - ((100-RH)/5)` as a rough estimate, or the
more accurate inverted Magnus equation using actual vapor pressure from
T and RH) — and both `t` and `r` are confirmed present at 850/700 hPa. **This
derivation does not exist anywhere in the current codebase** (neither
`backend/pipeline.py` nor `regrid.py` nor any script inspected this phase);
building it would be new code, not a reuse of an existing, validated
production formula, and this phase does not add it (out of scope — no
production logic was modified, and adding an unvalidated meteorological
formula to produce training labels would risk exactly the kind of
unverified-accuracy problem this project's standing instructions prohibit).
`k_index` and `totals_totals` are therefore marked **UNRESOLVED**, not
NOT_AVAILABLE outright and not DERIVABLE outright — reproducing them requires
a new, independently-validated derivation step that does not exist today.

## 8. Derived features — reproducibility classification

| Feature | Classification |
|---|---|
| `wind_shear_ms` (850-200 hPa) | **DIRECTLY REPRODUCIBLE** — same formula, both inputs present |
| `convergence_s` (850 hPa finite-difference divergence) | **DIRECTLY REPRODUCIBLE** — same formula, full-grid U/V present |
| `ctt_c` (RH-threshold cloud-top-temperature proxy) | **DIRECTLY REPRODUCIBLE** — same formula, RH+T present at all 5 required levels |
| `k_index`, `totals_totals` | **CANNOT BE REPRODUCED FROM f003/f006 ALONE** (or any single historical file) **without new, unvalidated dewpoint-derivation code** — the blocking input (isobaric dewpoint) does not exist in this archive at all, so this is not a lead/cycle-availability problem, it is a missing-field problem |
| `ctt_drop_rate_c_hr` (cross-cycle cooling rate) | **REQUIRES MULTIPLE FORECAST CYCLES** — needs two different *initializations* (e.g. 00Z and the previous 18Z), not two leads of the same initialization; a single `gfs.0p25.{cycle}.f{lead}.grib2` download (even a pair like f003+f006) cannot provide this, since both leads share one init and therefore one "current" atmospheric state description at their respective valid times — reproducing this would require downloading at least two separate cycles (e.g. `...2020071418.f*` and `...2020071500.f*`) and computing the delta between their `ctt_c` values at matching valid times, exactly as production's `load_prev_ctt_map`/`ctt_hours_elapsed` logic already does operationally |
| `qpe_mm` | See Section 4 — **SEMANTICALLY_DIFFERENT**, tied to the production-default-vs-documented-intent mismatch (f000 vs f006), not purely a reproducibility question |
| Hazard probabilities (`thunderstorm_probability`, etc.) | Inherit the status of their worst input — currently **UNRESOLVED** end-to-end because `k_index`/`totals_totals` are blocked and `qpe_mm` is semantically ambiguous |

## 9. Lead-time semantics

Explicit distinction, confirmed from actual GRIB metadata (not filenames):

- **Initialization time**: `dataDate`+`dataTime` — `2020-07-15 00:00 UTC` for
  both files (same init, as expected for f003/f006 from one cycle).
- **Forecast lead**: `forecastTime` — `3` hours for f003, `6` hours for f006.
- **Forecast valid time**: `validityDate`+`validityTime` — `2020-07-15 03:00
  UTC` for f003, `2020-07-15 06:00 UTC` for f006.

**Production's own default invocation uses `fhour=0`** (the workflow calls
`python backend/pipeline.py` with no override, and `run()`'s signature
default is `fhour: int = 0`) — meaning the live pan-India pipeline, as
actually wired into `update_grid.yml`, fetches a **0-hour analysis-equivalent
field** (init = valid time, lead = 0), not a forecast. This is a materially
different product class from f003/f006: an f000 field is GFS's own
short-range analysis/first-guess, not a projection of future atmospheric
state.

This has a direct, honest consequence for training-data construction: if a
future model is meant to predict the *same quantity the live pipeline
currently serves* (an f000-equivalent nowcast), training on f003/f006
historical data introduces a **lead-time mismatch** — the historical
predictors would describe a genuine forecast 3-6 hours ahead of their valid
time's "true" state, while the live production feature set is built from
GFS's own 0-hour field. Conversely, if the intent (consistent with this
project's stated 2-6h nowcasting window, per the standing SIH framing) is to
predict thunderstorm/cloudburst/flash-flood risk *for* a 2-6h-ahead valid
time, then f003/f006 are **the semantically correct choice** and it is
production's current f000-only fetch that is mismatched with the project's
own stated nowcasting goal — not the historical archive. **This report does
not resolve which of these two framings is intended** (that is a product
decision, not a data-availability fact); it states plainly that the two are
different and that reproducing "production's current features" and
reproducing "a 2-6h-ahead nowcast" are not the same target, and the GDEX
archive is well-suited to the latter but requires re-scoping to equal the
former (the archive does natively support f000 too, per
`docs/PHASE_0_4_2_HISTORICAL_GFS_FEASIBILITY.md`'s documented 3-hourly range
from 0h — an f000 pilot download was not performed this phase and would be
needed to confirm an exact match to production's literal current fetch).

## 10. Spatial regridding — representative-sample test

Using the real f003 file and this project's own `regrid.py::regrid_to_cell()`
/ `cell_id_for()` (reused, not reimplemented), six representative canonical
cells were tested (Bengaluru, northern, western, northeastern, southern
India, and one boundary cell), using the 2m-temperature field (`2t`, K) as
the test variable — chosen because it is a single, simple, directly-present
field with no shear/level-selection ambiguity, suitable as a pure
regridding-correctness check:

| Region | Target cell | Variable | Value (K) | Missing | Method | Distance (deg) |
|---|---|---|---|---|---|---|
| Bengaluru | `IND_13.0_77.0` | `2t` @ heightAboveGround 2m | 295.84 | False | nearest | 0.0 |
| Northern India | `IND_32.0_77.0` | `2t` @ heightAboveGround 2m | 291.67 | False | nearest | 0.0 |
| Western India | `IND_23.0_70.0` | `2t` @ heightAboveGround 2m | 302.60 | False | nearest | 0.0 |
| Northeastern India | `IND_26.0_94.0` | `2t` @ heightAboveGround 2m | 298.19 | False | nearest | 0.0 |
| Southern India | `IND_10.0_77.0` | `2t` @ heightAboveGround 2m | 295.17 | False | nearest | 0.0 |
| Boundary (SW corner, (6,68)) | `IND_6.0_68.0` | `2t` @ heightAboveGround 2m | 301.77 | False | nearest | 0.0 |

All six target cells exist in the actual canonical 992-cell grid (confirmed
by direct membership check against `data/pan_india_grid.json`'s `grid_cells`
list, not assumed from the bounds formula). All six regridded successfully
via the project's existing `regrid.py::regrid_to_cell()`, `method_used`
matched `method_requested` (`nearest`, no silent fallback), and
`distance_deg=0.0` for every cell (the 0.25° GFS source grid contains an
exact point at every integer-degree canonical cell center, so nearest-
neighbor snapping is exact for this particular grid pairing — this is a
property of the two grids' alignment, not a general interpolation-quality
guarantee away from exact grid points). The boundary cell `IND_6.0_68.0`
(the grid's southwest corner) regridded with no edge-of-array failure,
confirming the ±2° extraction window used here safely covers corner cells
too. Source: f003 file, init 2020-07-15 00:00 UTC, valid 03:00 UTC, lead 3h,
source_name recorded as `"gfs"` per the same convention established in
Phase 0.4.3A (archive identity `NCAR_GDEX_d084001` is the implicit source of
every value in this table).

This sample test only exercised one variable (`2t`) across 6 cells, chosen to
validate the regridding **mechanism** (coordinate alignment, corner handling,
`regrid_to_cell` plumbing) across pan-India's geographic spread. It does not
extend the per-variable production-feature findings in Sections 3, 6, and 7,
which were established from the actual GRIB field inventory (Section 2), not
from this spatial sample.

## 11. Final decision

### A. SAFE TO REUSE DIRECTLY

- `cape` (surface) — EXACT_MATCH, same field, same units, same level type.
- `cin` (surface) — EXACT_MATCH, same field, same units, same level type.
- `pwat_mm` — EXACT_MATCH, same field, same units, correctly distinguished
  from any INSAT-derived "IWV" (no such confusion exists in current code).
- `T850/T700/T500`, `u850/v850`, `u200/v200` (intermediate fields feeding
  other derived features) — EXACT_MATCH as raw inputs.
- `wind_shear_ms` (850-200 hPa) — DIRECTLY REPRODUCIBLE, identical formula,
  both inputs present.
- `ctt_c` — DIRECTLY REPRODUCIBLE, identical formula, all 5 required RH/T
  level pairs present.
- `convergence_s` — DIRECTLY REPRODUCIBLE, identical finite-difference
  formula, full-grid 850 hPa U/V present.
- Spatial regridding onto the canonical 992-cell grid via the existing
  `regrid.py::regrid_to_cell()` — confirmed working, including at a boundary
  cell, in the representative 6-cell sample (Section 10).

### B. REQUIRES DERIVATION / ADDITIONAL DATA

- `k_index`, `totals_totals` — blocked today by the complete absence of an
  isobaric dewpoint field in this archive; reproducible only via a new,
  independently-validated T+RH→Td derivation that does not exist in the
  codebase today (Section 7). Must not be added speculatively without
  separate validation against a known-good source, per this project's
  standing no-fabrication constraint.
- `ctt_drop_rate_c_hr` — requires downloading **two separate forecast
  cycles** (two different inits), not achievable from any single cycle's
  f003+f006 pair (Section 8).
- `qpe_mm` — the physical field (`tp`) exists, but its accumulation window
  is lead-dependent in the archive (0-3h at f003, 0-6h at f006) and does not
  match what production's actual default invocation (f000) would produce;
  using it requires first deciding what production's QPE semantics *should*
  be (Section 4), which is a product decision this report does not make.

### C. NOT SAFE TO REUSE WITHOUT FURTHER VALIDATION

- Any feature or label built on the assumption that f003/f006 represent the
  "same" product as production's current f000 default fetch (Section 9) —
  they are forecasts at a real lead, not a 0-hour analysis equivalent; this
  is a semantic difference, not a data-quality gap.
- The hazard probability outputs themselves (`thunderstorm_probability`,
  `cloudburst_probability`, `flash_flood_probability*`) — these are a
  hand-weighted heuristic formula (not a trained model, consistent with the
  Master Audit's prior finding) over a feature set that includes two
  currently-UNRESOLVED inputs (`k_index`, `totals_totals`) and one
  SEMANTICALLY_DIFFERENT input (`qpe_mm`); reproducing them from historical
  GDEX data today would silently substitute `None`/zero for the unresolved
  inputs, which would change the formula's behavior in an undocumented way.
- Only a 6-cell, single-variable, single-cycle spatial sample has been
  validated (Section 10) — full 992-cell, all-variable, multi-cycle
  regridding has not been attempted and should not be assumed to behave
  identically everywhere (e.g. near coastlines, at higher latitudes, or
  across the 0°/360° longitude seam, none of which pan-India's domain
  actually touches, but genuinely untested all the same).

### Final answer

**Can the historical GDEX GFS archive be used as a training-time predictor
source while preserving the semantics of the current live GFS predictor
pipeline?**

**CONDITIONAL.**

Justification, using only evidence gathered in this audit: three of the
production pipeline's core thermodynamic/dynamic features (`cape`, `cin`,
`pwat_mm`) and three of its derived features (`wind_shear_ms`, `ctt_c`,
`convergence_s`) map EXACT_MATCH or DIRECTLY REPRODUCIBLE onto real fields
confirmed present in the actual downloaded archive files — a genuine majority
of the feature set survives semantics intact. But the answer cannot be an
unconditional YES, because: (1) two features (`k_index`, `totals_totals`)
are blocked by a field that simply does not exist in this archive
(isobaric dewpoint) and would require new, unvalidated derivation code;
(2) one feature (`qpe_mm`) is tied to an accumulation-window semantic that
production's own current default invocation (f000) does not actually
realize, so there is no unambiguous "production QPE" to match against yet;
(3) one feature (`ctt_drop_rate_c_hr`) needs a data-acquisition pattern
(multiple distinct forecast cycles) not yet attempted; and (4) the
fundamental lead-time question — whether historical forecasts (f003/f006)
should be compared against production's literal f000 fetch, or against the
project's stated 2-6h nowcasting *goal* (which f003/f006 arguably serve
better than f000 does) — is a product decision, not a technical fact this
audit can resolve. The answer is therefore CONDITIONAL on: fixing or
accepting the dewpoint gap, deciding production's true target lead/window,
and acquiring multi-cycle data for the one cross-cycle feature — not on any
single unresolvable blocker.

## Tests

No new mapping logic was added to production code this phase (per the
brief's constraints), so no new production-facing unit test was required.
The representative-sample regridding test (Section 10) was executed and
verified directly (all 6 cells succeeded, confirmed real-time in this report)
rather than being encoded as a separate pytest file, consistent with the
brief's "create focused tests for any mapping logic added" — no new
mapping *logic* (code) was added; the sample test reused
`regrid.py::regrid_to_cell()` exactly as Phase 0.4.3A's validator already
does, and that existing code path's behavior was already exercised by
Phase 0.4.3A's own run. The full existing test suite was re-confirmed
unaffected: `pytest . --ignore=test_himawari.py --ignore=test_nomads.py
--ignore=test_segments.py --ignore=test_segments_v2.py` → **151 passed**
(same baseline as Phase 0.4.3A, same 4 pre-existing environment-only
collection errors, nothing new broken).

## Scope confirmation

- **Files created this phase**: this report only
  (`docs/PHASE_0_4_3B_HISTORICAL_GFS_SCHEMA_MAPPING.md`).
- **Files read (not modified)**: `backend/pipeline.py`,
  `pan_india_gfs_fetcher.py`, `gfs_fetcher.py`, `regrid.py`,
  `docs/PIPELINE_OWNERSHIP.md`, `docs/PHASE_0_1_FF_CURRENT_STATE.md`,
  `.github/workflows/update_grid.yml`, `.github/workflows/forecast_update.yml`,
  `data/pan_india_grid.json` (read-only, used only to confirm canonical cell
  membership), and the two real historical GRIB2 files (read-only, via
  ecCodes enumeration).
- **No files were redownloaded.** No production code, schema, canonical
  grid, workflow, or frontend file was modified. No model was trained.
  Nothing was deployed, committed, or pushed.
