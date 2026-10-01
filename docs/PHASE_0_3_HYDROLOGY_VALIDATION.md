# Phase 0.3-E/F — HydroSHEDS / MERIT-Hydro Access Validation + Cross-Check

## Attempt and result

A direct connection attempt (`curl -m 15 https://data.hydrosheds.org`) was made this phase, on the
premise that HydroSHEDS/MERIT-Hydro tiles are normally publicly downloadable without
authentication (unlike CartoDEM). The sandbox's egress proxy rejected the CONNECT outright:
`connect_rejected` / HTTP 403 ("organization policy or could not reach the destination") — the same
class of failure as the CDS and Bhuvan checks in the companion docs. This is a blanket sandbox
network policy, not specific to HydroSHEDS being gated or requiring credentials (it normally does
not).

No fetch of a HydroSHEDS void-filled DEM tile or a MERIT-Hydro flow-accumulation sample was
possible. No sample tile of any kind — real or placeholder — was downloaded or written to this
repo.

## Conclusion

**BLOCKED — no HydroSHEDS/MERIT-Hydro data obtained.** Unlike CartoDEM, this is a pure network
block: HydroSHEDS/MERIT-Hydro do not require an account for standard tile downloads, so the only
missing precondition identified this phase is network egress.

## Spatial cross-check against the 214 INDOFLOODS gauges / 992 cells — not possible this phase

The planned check (map elevation / flow-accumulation to the 214 gauges in
`processed/indofloods/indofloods_grid_mapping.csv` and the 992 canonical cells, purely as a spatial
sanity check, explicitly not for hazard-coefficient tuning) could not be run because no real
terrain/hydrology data was obtained in this phase (see above and the companion CartoDEM doc). This
section is reported as not-attempted-for-lack-of-input-data, not as a negative result.

## What would be needed for Phase 0.4

1. Network egress to `data.hydrosheds.org` (or `edcintl.cr.usgs.gov` / other HydroSHEDS/MERIT-Hydro
   mirror) allowed from wherever the pull is run — this is the only identified blocker; no
   credentials are normally required for HydroSHEDS.
2. Once a tile is obtained covering India (or at minimum the INDOFLOODS gauge footprint): extract
   point elevation and flow-accumulation at each of the 214 gauge coordinates and at the 992
   canonical cell centers, and run the descriptive sanity check described in
   `docs/PHASE_0_3_CARTODEM_VALIDATION.md` section "What would be needed for Phase 0.4," item 3.
