"""
backend/data_sources/gmgsi.py
================================
NOAA GMGSI (Global Mosaic of Geostationary Satellite Imagery) -- a
potential CTT/cloud-top-temperature candidate to reduce reliance on the
current GFS pressure-level RH+temperature CTT proxy (see
docs/LIVE_OPERATIONAL_AUDIT.md CTT row).

Unlike GFS/Himawari/METAR/DEM, GMGSI has NO prior work anywhere in this
repository -- it does not appear in docs/archive/phase_history/PHASE_0_2_EXTERNAL_DATA_CATALOG.md
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
            "VERIFIED 2026-11-15 from this repository's environment: the NOAA GMGSI longwave-IR mosaic IS reachable without credentials "
            "(s3://noaa-gmgsi-pds/GMGSI_LW, hourly, ~7 MB NetCDF, ~0.072 deg pixels, 'instrument: Himawari-AHI, GOES-ABI, MSG-SEVIRI'; S3 "
            "Last-Modified ~39 min after the hour). It is NOT usable as a calibrated cloud-top temperature: the 'data' variable is an "
            "8-bit enhanced-imagery count ('0-255 Brightness Temperature', values 11-255, inverse to temperature: warm clear land ~75, "
            "cold Tibet ~187) with NO scale_factor/add_offset or lookup table in the file, so converting counts to kelvin would be a guess. "
            "No brightness temperature is therefore published from it. Needed to finish: the official OSPO count-to-temperature table, or a "
            "calibrated source (Himawari AHI L1b/L2 via satpy, or INSAT-3D/3DR via MOSDAC credentials). See "
            "docs/data_sources/SATELLITE_AND_REANALYSIS_ACCESS.md.")
