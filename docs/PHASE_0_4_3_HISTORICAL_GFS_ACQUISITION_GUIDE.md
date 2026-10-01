# Phase 0.4.3 — Historical GFS (NCAR GDEX d084001) Acquisition Guide

Audience: the project owner, running this from a normal network-connected machine
(not this sandbox — Phase 0.4.2 confirmed the sandbox's egress policy rejects every
relevant NCAR/UCAR/AWS host). This guide only documents what was directly confirmed
from the official GDEX pages fetched this phase — nothing below is an invented
endpoint or guessed URL.

## 1. Official source, verified this phase

- Dataset ID: **d084001** ("NCEP GFS 0.25 Degree Global Forecast Grids")
- Landing page: https://gdex.ucar.edu/datasets/d084001/
- Data-access page (fetched directly this phase): https://gdex.ucar.edu/datasets/d084001/dataaccess/
- License: CC-BY-4.0 (confirmed Phase 0.4.2)

## 2. Access mechanisms actually documented on the data-access page

Three real mechanisms were confirmed by fetching the official data-access page this
phase (not inferred):

1. **Direct HTTPS file download** — full global GRIB2 files, one per cycle/forecast-hour.
   Confirmed real example (fetched this phase): base URL
   `https://data.gdex.ucar.edu/d084001/{YYYY}/{YYYYMMDD}/`, file naming
   `gfs.0p25.{YYYYMMDDHH}.f{FFF}.grib2` (e.g.
   `gfs.0p25.2022052300.f000.grib2`). **Confirmed file sizes for this naming
   pattern: ~487–532 MB per single global file** (full global grid, all variables,
   one forecast hour) — there is no indication files are pre-subset by variable or
   region.
2. **THREDDS Data Server (TDS)**, with **OPeNDAP** access — at
   `https://tds.gdex.ucar.edu/thredds/catalog/catalog_d084001.html`. OPeNDAP is the
   relevant mechanism for a **small pilot**, because it supports requesting a
   bounding-box/variable subset of a single file over HTTP **without downloading the
   full ~500 MB file first** — this is the recommended pilot path. A specific
   per-file OPeNDAP catalog URL pattern was also observed in search results, e.g.
   `https://thredds.rda.ucar.edu/thredds/catalog/files/g/d084001/{YYYY}/{YYYYMMDD}/catalog.html?dataset=files/g/d084001/{YYYY}/{YYYYMMDD}/gfs.0p25.{YYYYMMDDHH}.f{FFF}.grib2`
   (confirmed to exist via search results this phase; the catalog page itself could
   not be fetched directly this phase due to a transient `robots.txt` 503 on the
   fetch tool used, so the project owner should verify this URL loads before relying
   on it — it is documented as "found," not "individually re-verified by opening it
   this session").
3. **"Get a Subset" request tool** — a login-gated (free GDEX/RDA account) web form
   supporting temporal range, spatial-area, and parameter (variable) selection,
   producing a reduced extract rather than the full global file. This requires
   creating a free account at the GDEX site — **no credentials were invented or
   assumed by this phase**; the project owner must register if they want to use this
   path instead of OPeNDAP.

**Recommendation for the pilot: try OPeNDAP first (no account needed per the
documentation found), fall back to the login-gated "Get a Subset" tool if OPeNDAP
subsetting proves awkward, and only use the raw ~500MB-per-file HTTPS download as a
last resort** (e.g. two files for f003+f006 would be roughly 1 GB — acceptable as a
one-time pilot, but not something to repeat per cycle without the subsetting path).

## 3. Recommended pilot specification

- **One cycle**: pick a date already represented in this project's INDOFLOODS/VOBL
  work for relevance, e.g. `2020-07-15 00Z` (within the archive's 2015-01-15–present
  coverage and within INDOFLOODS' 1959–2020 event window).
- **Two native forecast hours**: `f003` and `f006` (NOT f002/f004/f005 — those are
  not native to this archive, per Phase 0.4.2's finding).
- **Geographic subset**: a small box around Bengaluru/VOBL first, e.g.
  **10–16°N, 74–80°E** (covers VOBL and a meaningful chunk of the existing 992-cell
  grid's southern region) — expand to full-India bounds only after this small pilot
  validates successfully.
- **Variables**: the minimum set needed to exercise the existing live-GFS feature
  pipeline (see Section 4) — temperature, specific/relative humidity, geopotential
  height, U/V wind (at least 850 hPa and 500 hPa for shear), CAPE, surface pressure,
  precipitable water, total precipitation. CIN if present (unconfirmed, see Phase
  0.4.2).

## 4. Variables required by the current live pipeline (traced this phase, read-only)

Read `gfs_row_select.py` and `forecast_action.py` (production, unmodified) to confirm
the exact column set the live pipeline already expects: `CAPE`, `K_INDEX`,
`TOTALS_TOTALS`, `LIFTED_INDEX`, `ERA5_T2M` (naming is historical/misleading — this
column is actually populated from GFS 2m temperature in the live pipeline, not ERA5;
not renamed in this phase since that would touch production code), plus the
documented PWAT/precipitation/wind fields referenced in `canonical_forecast_writer.py`
and `docs/PHASE_0_1_FF_CURRENT_STATE.md`/`PHASE_0_1_COMPLETION_REPORT.md`'s IWV/QPE
corrections. Classification (same table as Phase 0.4.2, repeated here for the
project owner's convenience, still **UNCONFIRMED-from-an-actual-file** since no pilot
exists yet):

| Variable | GDEX d084001 classification | Note |
|---|---|---|
| Temperature | DIRECT | standard field |
| Specific/relative humidity | DIRECT | standard field |
| Geopotential height | DIRECT | standard pressure-level field |
| U/V wind | DIRECT | multiple pressure levels |
| CAPE | UNCONFIRMED (strong precedent from live pipeline) | needs confirming on actual downloaded file |
| CIN | UNCONFIRMED | same caveat |
| Shear | DERIVED | from U/V at 2 levels, same method as live pipeline |
| Precipitation | DIRECT | accumulation field, same proxy caveat as live GFS (Phase 0.1) |
| Precipitable water | DIRECT | same field already relabeled correctly in Phase 0.1 |

## 5. Expected local directory and provenance

```
data/external/historical_gfs/
  manifest.json                     <- provenance manifest (see Section 6)
  raw/
    gfs.0p25.{YYYYMMDDHH}.f003.grib2
    gfs.0p25.{YYYYMMDDHH}.f006.grib2
```

This directory is explicitly **outside** `data/` (which holds production/INDOFLOODS
inputs) at a path clearly marked `external` — it must never be confused with a
production data source, and nothing in this phase wires it into
`backend/pipeline.py`, `canonical_forecast_writer.py`, or `forecast_action.py`.

## 6. Provenance manifest

`scripts/acquire_historical_gfs_pilot.py` writes
`data/external/historical_gfs/manifest.json` recording: dataset ID (`d084001`),
source URL, retrieval date, cycle, forecast lead(s), file name(s), SHA-256
checksum(s), variables requested, geographic extent, temporal extent, and license —
see the script itself for the exact schema.

## 7. How to run the acquisition tool (from a normal network-connected machine)

```
python3 scripts/acquire_historical_gfs_pilot.py --dry-run
python3 scripts/acquire_historical_gfs_pilot.py \
    --cycle 2020071500 --leads 003 006 \
    --bbox 10 16 74 80 \
    --out-dir data/external/historical_gfs
```

The script defaults to `--dry-run` behavior requiring explicit `--execute` to perform
a real network fetch, refuses anything over a configurable size ceiling unless
`--allow-large` is passed, and never embeds credentials — if the OPeNDAP path fails
and the user wants to use the login-gated "Get a Subset" tool instead, the script
prints the manual URL and stops rather than attempting to log in on the user's behalf.

## 8. Validation command (after download)

```
python3 scripts/validate_historical_gfs_pilot.py --dir data/external/historical_gfs
```

This reads whatever GRIB2 files are actually present, parses their metadata
(init time, valid time, forecast step, variables, grid), maps the subset to the
canonical 992-cell grid, and reports pass/fail — it does **not** fabricate results if
no file is present; it reports `NO_PILOT_FILE_FOUND` and stops.

## 9. Troubleshooting

- **OPeNDAP URL returns an error or the catalog page doesn't load**: the exact
  `thredds.rda.ucar.edu` catalog URL pattern found via search this phase could not be
  independently re-opened from this sandbox (network blocked) or fully confirmed
  live (one fetch attempt hit a transient `robots.txt` 503) — if it doesn't work,
  fall back to the GDEX "Get a Subset" tool (requires free account) or the direct
  ~500MB-per-file HTTPS download.
- **GDEX login required and you don't have an account**: registration is free per
  the documentation found; this phase does not have or need credentials to document
  the mechanism.
- **CAPE/CIN fields missing from the downloaded file**: this was flagged as
  UNCONFIRMED in Phase 0.4.2 — if genuinely absent, document this in the validation
  report rather than deriving a substitute silently.
- **Checksum mismatch after download**: re-download; do not proceed to validation
  with a file whose integrity can't be confirmed.

## Sources

- [GDEX d084001 landing page](https://gdex.ucar.edu/datasets/d084001/)
- [GDEX d084001 data access page](https://gdex.ucar.edu/datasets/d084001/dataaccess/)
- [GDEX d084001 2022-05-23 file list](https://gdex.ucar.edu/datasets/d084001/filelist/20220523/)
- [THREDDS/RDA catalog example (found via search, not independently re-opened this phase)](https://thredds.rda.ucar.edu/thredds/catalog/files/g/d084001/2025/20250713/catalog.html?dataset=files/g/d084001/2025/20250713/gfs.0p25.2025071300.f012.grib2)

## Scope confirmation

No production code, schema, model, or data file was modified. No large archive was
downloaded. Nothing committed, pushed, or deployed.
