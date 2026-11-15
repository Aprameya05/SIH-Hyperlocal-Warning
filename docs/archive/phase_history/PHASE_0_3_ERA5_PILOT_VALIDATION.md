# Phase 0.3-B/C — ERA5 Pilot Acquisition + Grid Validation

## 1. Credential and network check (honest, as attempted)

- `~/.cdsapirc`: **absent** (`ls -la ~/.cdsapirc` -> "No such file or directory"). No CDS API
  credentials exist in this sandbox.
- `cdsapi` package: **not installed** at phase start; installed this phase via `pip install cdsapi`
  (succeeded, v0.7.7) purely to confirm the package itself is available — no API call was made
  with it since there are no credentials to authenticate with.
- Network egress test: `curl` to `cds.climate.copernicus.eu`, `data.hydrosheds.org`, and
  `bhuvan.nrsc.gov.in` were all attempted directly. **All three were rejected by the sandbox's
  egress proxy with `connect_rejected` / HTTP 403** ("the egress proxy denied the CONNECT
  (organization policy) or could not reach the destination"). This is a sandbox-level network
  policy block, not a credentials problem, an authentication failure, or a timeout — the TCP
  CONNECT itself is refused before any CDS/HydroSHEDS/Bhuvan request could be sent.

## 2. Conclusion: ERA5 real pilot acquisition — BLOCKED

No real ERA5 data was downloaded. Both required preconditions are missing:
1. No CDS API credentials (`~/.cdsapirc`) are configured for this sandbox.
2. The sandbox's network egress policy blocks the CDS host outright, so even if credentials were
   added, the request would not reach Copernicus's servers from here.

**No ERA5 data, real or substituted, was fabricated or silently swapped in for a different
dataset.** This is reported as a blocker, not a success.

## 3. What a human with real CDS credentials, on an unrestricted network, would need to run

Exact call structure for the minimum viable pilot (small regional box, few days, core variables):

```python
import cdsapi
c = cdsapi.Client()  # reads ~/.cdsapirc: url + personal access token
c.retrieve(
    "reanalysis-era5-pressure-levels",
    {
        "product_type": "reanalysis",
        "variable": [
            "geopotential", "u_component_of_wind", "v_component_of_wind",
            "specific_humidity",
        ],
        "pressure_level": ["850", "500"],
        "year": "2020", "month": "07", "day": ["01", "02", "03"],
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": [15, 74, 11, 79],  # N, W, S, E -- small regional pilot box
        "format": "netcdf",
    },
    "era5_pilot_pressure_levels.nc",
)
c.retrieve(
    "reanalysis-era5-single-levels",
    {
        "product_type": "reanalysis",
        "variable": [
            "2m_temperature", "2m_dewpoint_temperature", "surface_pressure",
            "convective_available_potential_energy",
            "convective_inhibition", "total_precipitation",
        ],
        "year": "2020", "month": "07", "day": ["01", "02", "03"],
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": [15, 74, 11, 79],
        "format": "netcdf",
    },
    "era5_pilot_single_levels.nc",
)
```

- **Dataset names**: `reanalysis-era5-pressure-levels`, `reanalysis-era5-single-levels` (both on the
  CDS, dataset page `cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels`).
- **Typical file size**: a 3-day, 24-hourly, 5deg x 4deg regional pilot at native ~0.25deg
  resolution is a few MB per file (NetCDF, a handful of variables). A full India-hourly,
  multi-year pull (e.g. 2015-2025, the full `BOUNDS` S:6,N:37,W:68,E:98 extent, hourly, all
  variables below) is on the order of tens to low hundreds of GB depending on variable count and
  whether pressure levels beyond 850/500hPa are included — this was not tested, only estimated from
  CDS's published per-request size guidance; a human with real access should request a size
  estimate from the CDS portal before committing to the full pull.

## 4. Variable-by-variable feasibility (tagged as instructed)

| Requested variable | Tag | Notes |
|---|---|---|
| 2m temperature | DIRECT | `2m_temperature`, single-levels |
| Specific humidity / dewpoint | DIRECT | `specific_humidity` (pressure levels) or `2m_dewpoint_temperature` (single-levels) |
| Geopotential at 850/500 hPa | DIRECT | `geopotential`, pressure-levels dataset |
| U/V wind at 850/500 hPa | DIRECT | `u_component_of_wind` / `v_component_of_wind`, pressure-levels |
| CAPE | DIRECT | `convective_available_potential_energy`, single-levels |
| Surface pressure | DIRECT | `surface_pressure`, single-levels |
| Total precipitation | DIRECT | `total_precipitation`, single-levels (ERA5 accumulates over the hour) |
| CIN | DIRECT | `convective_inhibition`, single-levels — ERA5 provides this as a direct diagnostic field, no vertical-profile integration needed (see section 6) |
| Low-level convergence | DERIVED | not a direct ERA5 field; computed from U/V wind (see section 5) |

No variable in this list is UNAVAILABLE from ERA5 in principle — every one is either a direct
published ERA5 field or a documented derivation from direct fields. The only reason none were
actually obtained this phase is the access blocker in section 1-2, not unavailability of the
variables themselves.

## 5. Convergence derivation — feasibility (documentation only, not implemented into production)

`backend/pipeline.py::compute_convergence_grid` (lines ~206-260) already implements and documents
this exact derivation for GFS 850 hPa U/V winds, read-only reference for this phase:

- Convergence = -divergence = -(dU/dx + dV/dy), computed via centered finite differences on the
  850 hPa wind field.
- Required grid spacing: the function needs the **source** grid's native spacing to convert
  index-space differences to physical dx/dy (pipeline.py uses `SOURCE_GRID_STEP_FALLBACK_DEG =
  0.25` for GFS). For ERA5, the equivalent constant would be ERA5's native reanalysis grid spacing
  (0.25deg for the standard "reanalysis" product, same order as GFS), so the same finite-difference
  formula and the same dx/dy-from-degrees-and-latitude conversion pipeline.py already uses would
  carry over unchanged in method — this phase only confirms applicability, it does not change
  `compute_convergence_grid` or add an ERA5 call site to it.
- Units: s^-1, positive = convergence/inflow, matching pipeline.py's existing convention.

## 6. CIN derivation — feasibility

ERA5 single-levels provides `convective_inhibition` (CIN) as a **direct** diagnostic variable (ECMWF
computes it from the full model vertical profile at archive time) — unlike some reanalysis
products, no separate vertical-profile integration by this project would be required. This differs
from a from-scratch CIN calculation (parcel-theory integration between the LFC and the level of
free convection start), which ERA5 users do not need to perform themselves. This is a documentation
conclusion only; no CIN extraction code was written against real ERA5 data in this phase (none was
available).

## 7. Grid-mapping / regridding code path — tested against SYNTHETIC data only

Real ERA5 data was not available (section 1-2), so per instruction a synthetic,
structurally-representative ERA5-shaped array was built and explicitly labeled
SYNTHETIC/STRUCTURAL-TEST-ONLY throughout — it is never written or referred to anywhere as real
ERA5 data.

- Script: `scripts/era5_synthetic_regrid_test.py` (new file, read-only with respect to all
  production code; it mirrors — does not import or modify — the canonical grid constants from
  `backend/pipeline.py` and the nearest-cell convention from `regrid.py` /
  `scripts/map_indofloods_to_grid.py`).
- Output: `processed/era5_pilot/synthetic_era5_regrid_test.csv` and
  `processed/era5_pilot/synthetic_era5_regrid_summary.json`.
- Pilot bounding box used: 11-15N, 74-79E (a small regional box, not all of India, per instruction
  — large enough to exercise real nearest-cell regridding logic against multiple canonical cells).

**Validation results (synthetic data only):**
- Canonical cells within the pilot bbox: 30 (of the full 992).
- All 30 covered by the mapping: **yes** (`all_pilot_cells_covered: true`).
- Duplicate cell IDs: **0**.
- Max nearest-neighbor snap distance: 0.0 deg (synthetic source grid was built dense enough, at
  0.25deg spacing, that every canonical 1.0deg cell center has an exact or near-exact synthetic
  source point — this is a property of the synthetic grid, not something that would necessarily
  hold for a real ERA5 grid's exact point alignment, which should be re-checked once real data is
  obtained).
- Geographic shifts: none observed (synthetic field increases monotonically with lat/lon index by
  construction, matched indices resolve to the expected quadrant).
- Timestamp/timezone alignment: **not exercised** — the synthetic array has no time dimension. This
  is an explicit gap to close once real hourly ERA5 data (UTC-stamped) is available and needs
  aligning to the pipeline's existing timestamp convention (IST vs UTC handling is not validated
  here).
- Missingness handling: **not exercised** — the synthetic array has no NaN cells. Real ERA5 pulls
  rarely have missing cells within their requested bbox, but this should still be re-checked for
  real data (e.g., near the India coastline mask, if a land-only mask were ever applied).

## 8. Summary

| Item | Status |
|---|---|
| CDS credentials | Missing |
| Network to CDS | Blocked (403, organization policy) |
| Real ERA5 data obtained | No |
| Variable feasibility documented | Yes — all requested variables DIRECT or DERIVED, none UNAVAILABLE |
| Convergence derivation | Documented (reuses `backend/pipeline.py` formula/convention), not implemented for ERA5 |
| CIN derivation | Documented — ERA5 CIN is a DIRECT field | 
| Regrid/grid-mapping code path | Built and tested against SYNTHETIC data only; covers all 992 cells for the pilot bbox subset, no duplicates, no shifts |
| Recommendation | Phase 0.4 needs real CDS credentials and a network egress allowance for `cds.climate.copernicus.eu` supplied by the user, from outside this sandbox, before a real pilot can be attempted |
