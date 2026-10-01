# IMDAA Data Specification — Access Audit (Phase 2) — 2026-09-30

## Access Status: DATA BLOCKED

No IMDAA fetcher existed anywhere in this repo before this pass (confirmed by `grep` in an earlier audit this session — only comments/mentions in `README.md`, `index.html`, `regrid.py`, none of them a working fetch path). This pass adds real scaffolding (`scripts/acquire_imdaa.py`, `scripts/parse_imdaa.py`) but **cannot download or parse real IMDAA data from this sandbox**, for two independent reasons, both verified this session:

1. **No network route.** A direct connectivity test from this sandbox to IMD/NCMRWF-adjacent domains returns a `403` from this sandbox's own egress proxy — the request never reaches NCMRWF at all. This is a sandbox limitation, not an NCMRWF outage.
2. **No credentials.** `NCMRWF_USER`/`NCMRWF_PASS` (or whatever the real registered-account variables turn out to be) are not set anywhere in this environment, and no registration has been completed.

## What I Did NOT Do

I did not fill in `NCMRWF_BASE_URL` or the exact request/response shape in `scripts/acquire_imdaa.py` from memory. IMDAA is a real, known reanalysis product (jointly produced by NCMRWF and the UK Met Office) and I have general background knowledge of how such regional reanalyses are typically packaged (NetCDF, CF-convention variable naming, multi-level pressure fields), but I do **not** have verified, current knowledge of NCMRWF's actual current download API, exact current product catalog, or exact file-naming convention — that page could have changed since my training data, and I have no network access this session to check. Shipping a guessed endpoint as if it were confirmed would be the kind of fabrication this pass explicitly forbids. `scripts/parse_imdaa.py` is written to print whatever variable names a real downloaded file actually contains, specifically so the mapping gets built from ground truth on the first real run rather than from assumption.

## Required From You

1. Register at the official NCMRWF data service (from a machine with real internet access — this sandbox cannot reach it).
2. Confirm and share: the actual current download endpoint/API shape, the actual product name and file format (NetCDF vs. GRIB vs. something else), the actual variable names as they appear in a real downloaded file, the actual available date range, and the actual resolution.
3. Provide either the real credentials (as environment variables, for a future pass to wire into `acquire_imdaa.py`) or a small real sample file placed at `raw/imdaa/` so `parse_imdaa.py` can be run and corrected against it.

## Prioritized Variables (once access exists)

Matched against what `backend/mtl_backbone.py` and the pan-India grid schema already use, so a real IMDAA integration would slot into existing code paths rather than requiring new ones: temperature, specific humidity, geopotential height, U/V wind — each at 850/700/500hPa (the same levels already extracted from ERA5 elsewhere in this repo) — plus CAPE, and CIN if the product actually carries it (CIN's presence in IMDAA's public product is not confirmed this session).

## Storage Discipline

`scripts/acquire_imdaa.py` is written to require an explicit `--start`/`--end` date range and refuses to run without real credentials — no default that would pull a large or unbounded archive. The brief's instruction to "prioritize a scientifically useful subset first" is enforced by the script's own argument requirements, not left to the caller's discretion.
