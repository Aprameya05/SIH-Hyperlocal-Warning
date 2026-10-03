"""
backend/data_sources/imerg.py
================================
GPM IMERG (NASA/GES DISC) QPE candidate -- see
docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md section B1.

That research pass found a plausible access path (FTP/HTTP via GES DISC,
free PPS/Earthdata account registration, `earthaccess`/`gpm_api` Python
clients) but explicitly flagged it as "NOT tested live from this sandbox
(egress-restricted)". This Phase 1 pass re-checked network reachability
directly from the current environment (both the cloud sandbox and the
user's linked machine) and got no route to any external host at all right
now -- see the Phase 1 report for the exact commands run. GitHub Actions'
own runner egress has also not been independently verified against the
real GES DISC endpoint.

Per the Phase 1 instructions: do not hard-code an unverified endpoint, and
do not fabricate data when access cannot be verified. This adapter
therefore implements the metadata contract only and reports UNAVAILABLE
(no credentials configured) or BLOCKED (credentials configured but the
endpoint/fetch path has not been implemented or verified) -- it never
attempts a guessed request.
"""
from __future__ import annotations

import os

from . import BaseSourceAdapter, SourceMetadata

# Earthdata/GES DISC account credentials, if the user registers one.
# Modeled on the same env-var pattern as backend/fetch_insat3d.py (MOSDAC)
# and backend/data_sources/imdaa.py (NCMRWF).
CREDENTIAL_ENV_VARS = ("EARTHDATA_USER", "EARTHDATA_PASS")


class IMERGSource(BaseSourceAdapter):
    source_name = "imerg"
    source_product = "GPM IMERG V07 (GES DISC) -- QPE candidate"

    def __init__(self, env: dict = None):
        self.env = env if env is not None else os.environ

    def get_metadata(self) -> SourceMetadata:
        missing_creds = [v for v in CREDENTIAL_ENV_VARS if not self.env.get(v)]

        if missing_creds:
            return self.unavailable(
                f"GPM IMERG requires a free NASA Earthdata/GES DISC account; no credentials "
                f"configured (missing env var(s): {', '.join(missing_creds)}). A plausible access "
                f"path (earthaccess/gpm_api, FTP/HTTP via GES DISC) is catalogued in "
                f"docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md section B1 but has never been tested "
                f"live from this repository's environments. Currently used in production as a "
                f"GFS precipitation-rate proxy, explicitly code-commented 'QPE PROXY'.",
                quality_flags=["no_credentials", "endpoint_access_never_verified_live", "gfs_proxy_used_in_production"],
            )

        # Credentials configured, but this Phase 1 pass has not verified
        # reachability of gpm1.gesdisc.eosdis.nasa.gov (or any external
        # host) from either the cloud sandbox or the user's machine, and
        # has not implemented a real earthaccess/gpm_api request. Report
        # BLOCKED with that exact reason rather than guessing a URL/schema.
        return self.blocked(
            "GPM IMERG credentials are configured, but this adapter has not implemented or "
            "verified a real fetch against GES DISC -- direct network checks from this "
            "environment (cloud sandbox and the user's linked machine) returned no route to "
            "any external host during this Phase 1 pass. Implement and test the real "
            "earthaccess/gpm_api request once outbound network access to GES DISC is confirmed "
            "working, before this source can report LIVE/CACHED.",
            quality_flags=["connectivity_unverified_this_session"],
        )


__all__ = ["IMERGSource", "CREDENTIAL_ENV_VARS"]
