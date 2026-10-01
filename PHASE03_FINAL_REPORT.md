# Phase 0.3 Final Report (top-level mirror)

Full detail lives in `docs/PHASE_0_3_COMPLETION_REPORT.md` and its companion docs:
`docs/PHASE_0_3_INDOFLOODS_RECONCILIATION.md`, `docs/PHASE_0_3_ERA5_PILOT_VALIDATION.md`,
`docs/PHASE_0_3_CARTODEM_VALIDATION.md`, `docs/PHASE_0_3_HYDROLOGY_VALIDATION.md`.

## Headline results

1. **INDOFLOODS 8,342 vs 4,548**: Verdict **D (insufficient evidence)**. The repo's 4,548-row
   `data/floodevents_indofloods.csv` is checksum-identical to the actual downloaded Zenodo record
   14584655 (v1.0) archive — not a subset filtered by this project's own code. The "8,342" figure
   traces to a paper citation this phase could not re-verify directly (WebFetch to the paper's
   hosts was rate-limited, HTTP 429, both attempts).
2. **ERA5 pilot**: **Blocked.** No `~/.cdsapirc` credentials exist in this sandbox, and network
   egress to `cds.climate.copernicus.eu` is rejected by the sandbox proxy (HTTP 403, organization
   policy). No real ERA5 data obtained; none fabricated or substituted.
3. **CartoDEM**: **Blocked** — network egress to `bhuvan.nrsc.gov.in` rejected (HTTP 403).
4. **HydroSHEDS/MERIT-Hydro**: **Blocked** — network egress to `data.hydrosheds.org` rejected
   (HTTP 403).
5. **Canonical cells mapped with real data**: **0.** A synthetic/structural-test-only regrid test
   (new script `scripts/era5_synthetic_regrid_test.py`) validated the mapping *code path* against
   30 of 992 cells in a small pilot bbox using a fabricated, explicitly-labeled placeholder array —
   this proves the method, not real data acquisition.
6. **Convergence and CIN derivations**: both documented as feasible (convergence reuses
   `backend/pipeline.py`'s existing finite-difference formula; CIN is a direct ERA5 field needing
   no vertical-profile integration) — neither was implemented into production code.

## Files created this phase

- `docs/PHASE_0_3_INDOFLOODS_RECONCILIATION.md`
- `docs/PHASE_0_3_ERA5_PILOT_VALIDATION.md`
- `docs/PHASE_0_3_CARTODEM_VALIDATION.md`
- `docs/PHASE_0_3_HYDROLOGY_VALIDATION.md`
- `docs/PHASE_0_3_COMPLETION_REPORT.md`
- `scripts/era5_synthetic_regrid_test.py` (new, read-only w.r.t. all production code)
- `processed/era5_pilot/synthetic_era5_regrid_test.csv` (synthetic data only)
- `processed/era5_pilot/synthetic_era5_regrid_summary.json` (synthetic data only)
- `PHASE03_FINAL_REPORT.md` (this file)

No existing production file (`backend/pipeline.py`, `canonical_forecast_writer.py`,
`forecast_action.py`, `index.html`, `regrid.py`) was modified. No existing data file in `data/` or
`processed/` was modified. Nothing was trained, deployed, committed, or pushed.

## Honest bottom line

This sandbox cannot, on its own, assemble a pan-India historical feature foundation: every external
data source Phase 0.3 needs (ERA5 via CDS, CartoDEM via Bhuvan, HydroSHEDS/MERIT-Hydro) is blocked
here either by missing credentials (ERA5) or by the sandbox's network egress policy rejecting the
relevant hosts outright (all three, plus the paper-verification web fetches). The INDOFLOODS
question also could not be fully closed for the same reason (rate-limited web access to the source
paper). What Phase 0.3 *could* do without those — verify existing in-repo row counts and chains of
custody, confirm variable-level feasibility, and build and validate the regridding code path against
synthetic data — was done and is documented above. Phase 0.4 is blocked pending the user supplying
CDS credentials and/or running the acquisition step from a machine/environment with network access
to `cds.climate.copernicus.eu`, `bhuvan.nrsc.gov.in`, and `data.hydrosheds.org`.
