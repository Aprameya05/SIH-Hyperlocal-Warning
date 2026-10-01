# Phase 0.3 Completion Report — Data Engineering + Validation

Scope: documentation and validation only. No model was trained, no production prediction logic,
hazard formula, schema, frontend, or alert logic was modified, nothing was deployed, committed, or
pushed. New files are confined to `docs/`, `processed/era5_pilot/`, and one new read-only script in
`scripts/`.

## 1. INDOFLOODS reconciliation result

**Verdict D — insufficient evidence**, with strong supporting detail (full writeup:
`docs/PHASE_0_3_INDOFLOODS_RECONCILIATION.md`). Confirmed real numbers:
- `data/floodevents_indofloods.csv`: **4,548** data rows (2,919 "Flood" + 1,629 "Severe Flood"),
  checksum-identical to the actual Zenodo record 14584655 (v1.0) archive the project downloaded —
  not a subset filtered by any script in this repo.
- The "8,342" figure (5,525 + 2,817) comes from `docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md`'s citation
  of the published paper, which this phase could not re-verify directly: WebFetch attempts against
  the paper's sources (AMS journal, ResearchGate) were blocked by proxy rate-limiting (HTTP 429)
  before the text could be read.
- Recommendation: re-attempt fetching the paper's exact text in Phase 0.4; until then, treat 4,548
  as the confirmed content of the dataset version this project holds, and do not scale any
  downstream count by an unverified ratio.

## 2. ERA5 pilot status

**BLOCKED.** No CDS API credentials exist in this sandbox (`~/.cdsapirc` absent) and the sandbox's
egress proxy rejects any connection to `cds.climate.copernicus.eu` (HTTP 403, organization policy).
`cdsapi` was installed to confirm the package itself works, but no retrieval call was attempted
since there is nothing to authenticate with and the host is unreachable regardless. No real ERA5
data was obtained, and none was substituted or fabricated. Full detail, including the exact CDS
dataset names, API call structure, and size estimate a human with credentials would need:
`docs/PHASE_0_3_ERA5_PILOT_VALIDATION.md`.

## 3. Canonical cells successfully mapped

**0 real cells** — no real external dataset (ERA5, CartoDEM, HydroSHEDS/MERIT-Hydro) was obtained
this phase, so no real-data mapping to the 992 canonical cells happened.

A **synthetic, explicitly-labeled** regrid test (`scripts/era5_synthetic_regrid_test.py`) mapped
30 of 992 canonical cells (a small regional pilot bbox, 11-15N/74-79E) against a structurally-shaped
but fabricated placeholder array, to validate the regridding *code path* in isolation from data
availability. Results: all 30 pilot-bbox cells covered, 0 duplicate cell IDs, 0 geographic shifts,
max snap distance 0.0 deg. Timezone and missingness handling were **not** exercised (the synthetic
grid has neither a time dimension nor NaN cells) — both need re-testing once real hourly data with
genuine gaps is available.

## 4. Variables successfully obtained

**None** — no real external variable data (ERA5, CartoDEM elevation, HydroSHEDS flow-accumulation)
was retrieved this phase.

## 5. Missing variables

All requested ERA5 variables (2m temp, specific humidity/dewpoint, geopotential at 850/500hPa,
U/V wind at 850/500hPa, CAPE, surface pressure, total precipitation, CIN) are missing from this
project's holdings as of this phase — all are DIRECT ERA5 fields per
`docs/PHASE_0_3_ERA5_PILOT_VALIDATION.md` section 4, none are UNAVAILABLE in principle, they simply
were not retrievable in this sandbox. CartoDEM elevation and HydroSHEDS/MERIT-Hydro
flow-accumulation are also both missing, for network-access reasons (see items 8-9).

## 6. Convergence derivation feasibility

**Feasible, documented, not implemented for ERA5.** `backend/pipeline.py::compute_convergence_grid`
already implements this exact formula (negative horizontal divergence of 850 hPa U/V wind via
centered finite differences, s^-1, positive = convergence) for GFS; the same method and grid-spacing
handling would carry over to ERA5's own 0.25deg native grid. See
`docs/PHASE_0_3_ERA5_PILOT_VALIDATION.md` section 5. No change was made to `compute_convergence_grid`
or any other production function.

## 7. CIN derivation feasibility

**Feasible, and simpler than from-scratch derivation.** ERA5 single-levels provides
`convective_inhibition` as a direct ECMWF-computed diagnostic field — no vertical-profile
integration is required by this project, unlike a from-scratch parcel-theory CIN calculation. See
`docs/PHASE_0_3_ERA5_PILOT_VALIDATION.md` section 6.

## 8. CartoDEM coverage status

**BLOCKED** — network egress to `bhuvan.nrsc.gov.in` rejected (HTTP 403, organization policy) before
any registration/auth layer could even be tested. Detail: `docs/PHASE_0_3_CARTODEM_VALIDATION.md`.

## 9. Hydrology (HydroSHEDS/MERIT-Hydro) coverage status

**BLOCKED** — network egress to `data.hydrosheds.org` rejected (HTTP 403, organization policy).
Unlike CartoDEM, HydroSHEDS normally needs no account, so network access alone is the identified
blocker. Detail: `docs/PHASE_0_3_HYDROLOGY_VALIDATION.md`.

## 10. INDOFLOODS spatial cross-check results

**Not run** — no real terrain or hydrology data was obtained in items 8-9, so there was nothing to
cross-check the 214 INDOFLOODS gauges or 992 cells against. This is reported as
not-attempted-for-lack-of-input, not as a negative finding.

## 11. Data provenance status

No new real external data file was obtained this phase, so no new provenance record (source, URL,
license, retrieval date, checksum) was created. The one new data artifact produced —
`processed/era5_pilot/synthetic_era5_regrid_test.csv` — is explicitly and repeatedly labeled
SYNTHETIC/STRUCTURAL-TEST-ONLY in its own content, the script that produced it, and this report; it
carries no provenance fields because it is fabricated test data, not an external acquisition.

## 12. Blockers (exact)

1. **ERA5**: no `~/.cdsapirc` credentials in this sandbox; network egress to
   `cds.climate.copernicus.eu` rejected by the sandbox proxy (`connect_rejected`, HTTP 403,
   "organization policy").
2. **CartoDEM**: network egress to `bhuvan.nrsc.gov.in` rejected the same way; registration
   requirement untested because network blocked first.
3. **HydroSHEDS/MERIT-Hydro**: network egress to `data.hydrosheds.org` rejected the same way; no
   credential issue identified (HydroSHEDS does not normally require one).
4. **INDOFLOODS reconciliation**: WebFetch to the BAMS paper's hosting sites (AMS journal,
   ResearchGate) returned HTTP 429 (rate-limited) both times attempted, before the paper's exact
   total-event-count text could be read.

All four blockers are external-access constraints on this sandbox (credentials the user has not
supplied here, and/or an organization network policy that does not allow these specific hosts from
this environment) — none are a result of the data not existing or not being obtainable in
principle.

## 13. Exact recommendation for Phase 0.4

1. Supply real CDS API credentials (`~/.cdsapirc`) from the user's own machine/account, and run the
   ERA5 pilot retrieval (section 2 / the exact `cdsapi` call in
   `docs/PHASE_0_3_ERA5_PILOT_VALIDATION.md` section 3) from an environment where
   `cds.climate.copernicus.eu` egress is allowed — this sandbox is not such an environment unless its
   network policy changes.
2. Run the CartoDEM and HydroSHEDS/MERIT-Hydro fetches from an environment with unrestricted
   egress to `bhuvan.nrsc.gov.in` and `data.hydrosheds.org` respectively; register a Bhuvan account
   first if required.
3. Once any of (1)-(2) produce real data, re-run the regridding code path against it (the synthetic
   test in this phase already validates the method; it needs re-running against real data to
   validate timezone alignment and missingness handling, which the synthetic test could not
   exercise) and complete the INDOFLOODS gauge/cell spatial cross-check.
4. Re-attempt fetching the BAMS paper's exact text (once rate-limiting clears) to close the
   INDOFLOODS reconciliation to A/B/C instead of the current D.
5. Do not begin any model training (MTL, transformer, XGBoost) until at least one of ERA5 or
   CartoDEM/HydroSHEDS is actually in hand with real, provenance-documented data — Phase 0.3 found
   this sandbox cannot assemble that foundation on its own.

## Success-criteria answers

| # | Question (paraphrased from the task) | Answer |
|---|---|---|
| 1 | Was the INDOFLOODS 8,342 vs 4,548 discrepancy reconciled to one of A/B/C/D? | Yes — D (insufficient evidence), with exact row counts and chain of custody documented |
| 2 | Was a real ERA5 pilot acquired? | No — blocked (no credentials, network egress denied) |
| 3 | Were ERA5 variables tagged DIRECT/DERIVED/UNAVAILABLE? | Yes — all requested variables are DIRECT or DERIVED, none UNAVAILABLE |
| 4 | Was the grid-mapping/regridding code path built and tested? | Yes, against synthetic/structural-test-only data (real ERA5 unavailable) |
| 5 | Was convergence derivation documented? | Yes — reuses the existing `backend/pipeline.py` formula, not implemented for ERA5 |
| 6 | Was CIN derivation documented? | Yes — ERA5 provides CIN directly, no integration needed |
| 7 | Was CartoDEM/HydroSHEDS accessed and cross-checked against INDOFLOODS gauges? | No — both blocked by network policy before any data could be fetched |
| 8 | Is provenance documented for everything obtained? | Yes by omission — nothing real was obtained, so no provenance record was fabricated; the one synthetic artifact is explicitly labeled as such |
