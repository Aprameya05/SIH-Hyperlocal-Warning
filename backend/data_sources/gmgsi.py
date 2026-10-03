"""
backend/data_sources/gmgsi.py
================================
NOAA GMGSI (Global Mosaic of Geostationary Satellite Imagery) -- a
potential CTT/cloud-top-temperature candidate to reduce reliance on the
current GFS pressure-level RH+temperature CTT proxy (see
docs/LIVE_OPERATIONAL_AUDIT.md CTT row).

Unlike GFS/Himawari/METAR/DEM, GMGSI has NO prior work anywhere in this
repository -- it does not appear in docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md
or any other audit document, and no endpoint for it has ever been
verified from this repo's environments. Per the Phase 1 instructions:
verify the actual accessible NOAA product/endpoint before wiring it in,
and do not invent URLs or schemas.

This Phase 1 pass could not perform that verification -- direct network
checks from the current environment (both the cloud sandbox and the
user's linked machine) found no route to any external host at all (see
the Phase 1 report). Rather than guess a NOAA/NESDIS/STAR URL from
training data and present it as confirmed, this adapter reports
UNAVAILABLE unconditionally and names the missing verification step.
"""
from __future__ import annotations

from . import BaseSourceAdapter, SourceMetadata


class GMGSISource(BaseSourceAdapter):
    source_name = "gmgsi"
    source_product = "NOAA GMGSI (unverified -- no confirmed endpoint)"

    def get_metadata(self) -> SourceMetadata:
        return self.unavailable(
            "NOAA GMGSI has no prior implementation or verified endpoint in this repository. "
            "This Phase 1 pass attempted to verify outbound network reachability from both the "
            "cloud sandbox and the user's linked machine and found no route to any external host "
            "at all during this session. Per the Phase 1 instructions, no URL or schema has been "
            "invented or wired in. Before this source can be implemented: (1) confirm the actual "
            "accessible NOAA/NESDIS product and endpoint for GMGSI from an environment with "
            "working network access, (2) confirm whether the production GitHub Actions runner "
            "can reach it, (3) only then implement a real fetch here.",
            quality_flags=["no_prior_implementation", "endpoint_never_verified", "connectivity_unverified_this_session"],
        )


__all__ = ["GMGSISource"]
