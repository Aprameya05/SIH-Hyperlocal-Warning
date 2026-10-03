"""
backend/data_sources/imdaa.py
================================
IMDAA (IMD-NCMRWF regional reanalysis) credential-aware adapter.

Per docs/LIVE_OPERATIONAL_AUDIT.md and
SIH_PAN_INDIA_PHASE0_2_20260930_140352Z/scripts/acquire_imdaa.py: this
source has never had working credentials or a verified endpoint anywhere
in this repository. `raw/imdaa/` is confirmed empty. The production
system substitutes GFS fields and labels that substitution honestly in
the UI ("IMDAA Reanalysis proxy") -- it does not claim IMDAA itself is
live.

This adapter NEVER reports LIVE. It checks for real credentials
(NCMRWF_USER/NCMRWF_PASS, matching the env-var pattern already used by
backend/fetch_insat3d.py for MOSDAC) and a real, non-placeholder endpoint
(NCMRWF_BASE_URL). Absent either, it returns BLOCKED with an explicit
reason -- it does not attempt a network call, and it does not fabricate
data.
"""
from __future__ import annotations

import os

from . import BaseSourceAdapter, SourceMetadata

CREDENTIAL_ENV_VARS = ("NCMRWF_USER", "NCMRWF_PASS")
ENDPOINT_ENV_VAR = "NCMRWF_BASE_URL"


class IMDAASource(BaseSourceAdapter):
    source_name = "imdaa"
    source_product = "IMD-NCMRWF IMDAA regional reanalysis"

    def __init__(self, env: dict = None):
        self.env = env if env is not None else os.environ

    def get_metadata(self) -> SourceMetadata:
        missing_creds = [v for v in CREDENTIAL_ENV_VARS if not self.env.get(v)]
        endpoint = self.env.get(ENDPOINT_ENV_VAR, "")

        if missing_creds:
            return self.blocked(
                f"IMDAA/NCMRWF credentials not configured (missing env var(s): {', '.join(missing_creds)}). "
                f"Register at NCMRWF to obtain access, then set {' and '.join(CREDENTIAL_ENV_VARS)} "
                f"as environment variables or GitHub Secrets. GFS fields are used as an honestly-labeled "
                f"proxy in production until this is resolved -- see docs/LIVE_OPERATIONAL_AUDIT.md.",
                quality_flags=["credential_gate", "gfs_proxy_used_in_production"],
            )

        if not endpoint:
            return self.blocked(
                f"IMDAA/NCMRWF credentials are present but {ENDPOINT_ENV_VAR} is unset. "
                f"The real NCMRWF endpoint has not been verified anywhere in this repository "
                f"(see acquire_imdaa.py) -- this adapter will not guess one. Set {ENDPOINT_ENV_VAR} "
                f"to the real, registered-account endpoint before this source can be attempted.",
                quality_flags=["unverified_endpoint"],
            )

        # Credentials and an endpoint are configured, but this Phase 1 pass
        # does not implement the actual NCMRWF fetch/auth flow -- doing so
        # without ever having tested against the real API would risk
        # shipping unverified request shapes as if they were confirmed
        # working. Report BLOCKED with a distinct, honest reason rather
        # than attempting (and likely failing) a guessed request, and
        # rather than silently claiming LIVE.
        return self.blocked(
            f"IMDAA/NCMRWF credentials and {ENDPOINT_ENV_VAR} are configured, but the actual fetch/auth "
            f"flow against NCMRWF has not been implemented or verified in this codebase yet -- "
            f"acquire_imdaa.py is scaffolding only. Implement and verify the real request against the "
            f"registered account before this source can report LIVE/CACHED.",
            quality_flags=["fetch_not_yet_implemented"],
        )


__all__ = ["IMDAASource", "CREDENTIAL_ENV_VARS", "ENDPOINT_ENV_VAR"]
