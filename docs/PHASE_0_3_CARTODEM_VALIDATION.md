# Phase 0.3-D — CartoDEM / Bhuvan Access Validation

## Attempt and result

A direct TCP connection attempt (`curl -m 15 https://bhuvan.nrsc.gov.in`) was made this phase. The
sandbox's egress proxy rejected the CONNECT outright: `connect_rejected` / HTTP 403
("organization policy or could not reach the destination"). No request ever reached NRSC/Bhuvan's
servers — this is a network policy block at the sandbox boundary, not a Bhuvan-side auth failure or
timeout.

This matches the expectation going in: Bhuvan/NRSC CartoDEM is an Indian government geoportal that
in any case typically requires account registration before bulk DEM tile download is possible, so
even with network access, credentialed registration would likely still be required — this phase
could not get far enough to test that second layer because the first (network) layer already
blocked.

## Conclusion

**BLOCKED — no CartoDEM data obtained, no sanity checks against the 214 INDOFLOODS gauges or the
992 canonical cells were possible.**

## What would be needed for Phase 0.4

1. Network egress to `bhuvan.nrsc.gov.in` (or NRSC's direct download host, if different from the
   portal host) allowed from wherever the pull is run.
2. A registered Bhuvan account (the portal is believed to gate bulk/tile downloads behind login —
   not independently confirmed this phase since the network layer blocked first).
3. Once both are available: fetch a DEM tile set covering the INDOFLOODS gauge coordinates in
   `processed/indofloods/indofloods_grid_mapping.csv` (214 gauges), extract point elevations at each
   gauge lat/lon, and join for a spatial sanity check (e.g., gauges reporting "Severe Flood" events
   sitting in lower-elevation, higher-accumulation terrain than non-flood-reporting gauges) — purely
   descriptive, no hazard coefficient tuning, per instruction.

No CartoDEM file of any kind — real or placeholder — was written to this repo this phase.
