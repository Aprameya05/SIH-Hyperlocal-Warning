# Phase 0.4.2 — Historical GFS Training-Data Feasibility Audit

Status: RESEARCH ONLY. No production code, schema, or model touched. No large archive
downloaded. All access-status claims below distinguish "documented by the source" from
"actually tested from this sandbox" — none were silently assumed.

## 1. Required structure (restated, not assumed satisfied by any source below)

The target example shape is: `forecast_initialization_time (GFS cycle T) -> forecast
lead (+2h/+4h/+6h) -> 992-cell predictors at that lead -> observed hazard outcome at
the corresponding future valid time`. A reanalysis field (single "best estimate" state,
no init/lead structure) does **not** satisfy this — it was explicitly excluded from
consideration as a GFS-forecast substitute, consistent with the brief.

## 2. Candidate sources found

### A. NOAA GFS real-time bucket — `noaa-gfs-bdp-pds` (AWS Open Data)
- URL: https://registry.opendata.aws/noaa-gfs-bdp-pds/
- S3 bucket: `noaa-gfs-bdp-pds` (us-east-1), **no authentication required**
  (`aws s3 ... --no-sign-request`), per NOAA's own documentation.
- Update frequency: 4 cycles/day (00/06/12/18 UTC), 0.25°/28km base resolution.
- License: NOAA Open Data Dissemination (NODD) — open use, attribution requested,
  cannot claim NOAA endorsement.
- **Historical coverage: NOT a long-term archive.** NOAA's own real-time buckets are
  documented elsewhere (and consistent with general NOAA NODD practice) as rolling,
  short-retention mirrors of the *current* operational feed — this page does not state
  an explicit retention window, and no retention figure was found that would confirm
  multi-year history is available here. Treat this bucket as a **live/near-real-time
  source**, not a historical-cycle archive, unless a specific retention policy is
  found and confirmed (not found this phase).
- **Access tested from this sandbox: FAILED.** Direct `curl` to
  `noaa-gfs-bdp-pds.s3.amazonaws.com` returned a proxy-level connection rejection
  (`connect_rejected`, organization egress policy) — identical failure mode to every
  other data host attempted in Phase 0.3 (CDS, Bhuvan, HydroSHEDS). No pilot could be
  pulled from this sandbox.

### B. NCAR GDEX / RDA dataset `d084001` — NCEP GFS 0.25° forecast archive (BEST CANDIDATE)
- URL: https://gdex.ucar.edu/datasets/d084001/
- **This is a genuine historical forecast-cycle archive**, not a reanalysis:
  coverage **2015-01-15 00:00 UTC through (per the live page) 2026-10-15 12:00 UTC**,
  updated daily, with an explicit note that updates will stop in early 2026 due to an
  AWS migration — so continued availability beyond that point is uncertain and should
  be re-checked before relying on it operationally.
- Resolution: 0.25° × 0.25° global grid (1440×721 points, full global coverage,
  includes all of India).
- **Forecast-cycle structure, exactly as required**: 4 cycles/day (00/06/12/18 UTC),
  with forecast leads at **3-hourly intervals from 0 to 240 hours**, then 12-hourly
  from 240–384h.
- Variables: 40+ parameters documented — temperature, winds (U/V), pressure, moisture
  fields, radiation, soil moisture, vorticity, precipitation, and others (exact CAPE/
  CIN/precipitable-water field-by-field availability was not individually confirmed
  from the page text fetched this phase — see Section 4 caveat).
- Format: WMO GRIB2.
- Access: THREDDS Data Server, an AWS S3 mirror, or the NCEP operational server;
  **no authentication stated as required** for the documented access paths (optional
  GDEX account sign-in mentioned, not mandatory).
- License: **Creative Commons Attribution 4.0** — the most permissive license found
  across any candidate in this audit.
- Total volume: **662.15 TB** for the full archive — confirms this is something to
  pilot narrowly (one cycle, one small bounding box, one or two leads), never bulk-
  downloaded.
- **Access tested from this sandbox: FAILED.** `gdex.ucar.edu`, `rda.ucar.edu`, and
  `thredds.rda.ucar.edu` were all rejected at the proxy level with the identical
  `connect_rejected` / organization-policy failure seen for every other data host in
  this project's sandbox across all prior phases (Phase 0.3's CDS/Bhuvan/HydroSHEDS
  attempts, and this phase's AWS GFS attempt). No pilot could be pulled.

### C. Google Cloud / Earth Engine public GFS mirror
- URLs: https://console.cloud.google.com/marketplace/details/noaa-public/gfs and
  https://developers.google.com/earth-engine/datasets/catalog/NOAA_GFS0P25
- Documented as a public BigQuery/Earth Engine-accessible mirror of NOAA GFS 0.25°
  forecast data, "384-Hour Predicted Atmosphere Data" per the Earth Engine catalog
  title — implying forecast-cycle (not just analysis) structure is present, consistent
  with source B.
- Access requires a Google Cloud / Earth Engine project and (for BigQuery) a GCP
  billing-enabled account even for public datasets' query costs; Earth Engine itself
  requires a registered (free, but gated) account.
- **Not tested this phase** — this sandbox's network restrictions make it very likely
  any GCP API endpoint would be rejected the same way as every AWS/UCAR/Copernicus
  host tried so far, and setting up a new GCP/Earth Engine account/credential is
  outside this phase's "do not invent access" constraint. Documented as a real,
  credible lead requiring external account setup, not attempted.

### D. Other sources checked and rejected as unsuitable
- **ECMWF/Copernicus ERA5 (already investigated in Phase 0.3)**: reanalysis, not a
  forecast-cycle archive — excluded per the brief's own definition in Section 1.
- No Kaggle, Hugging Face, or Zenodo dataset was found in this pass that hosts a
  genuine multi-year, multi-cycle, multi-lead GFS forecast archive at usable
  resolution; these platforms host occasional small GFS-derived extracts for ML
  competitions, none of which were found to cover India specifically with the
  required cycle/lead structure. Not individually catalogued as none cleared the
  "would this let us make a claim we can't currently make" bar at a pan-India,
  multi-year scale.

## 3. Smallest pilot identified (not obtained — network blocked)

The smallest defensible pilot would be: **one single GFS cycle (e.g. 2020-07-15 00Z)
from NCAR GDEX d084001, forecast hours f000/f003/f006 only, subset to a small India
bounding box (e.g. 10-20N/70-85E), for a handful of core variables (2m temp, specific
humidity, geopotential at 2-3 pressure levels, U/V at 850/500hPa, CAPE if present,
total precipitation)** — this would be on the order of tens of MB, not the full
662TB archive, and would be sufficient to prove the cycle→lead→992-cell mapping
pipeline end to end. **This pilot could not be attempted in this sandbox** because
every relevant host (`gdex.ucar.edu`, `rda.ucar.edu`, `thredds.rda.ucar.edu`) is
rejected at the network egress layer, not by authentication — the same failure
pattern documented for ERA5/CDS, Bhuvan/CartoDEM, and HydroSHEDS in Phase 0.3.

## 4. Variable classification (per NCAR GDEX d084001 documentation — not independently field-verified from this sandbox since no file could be downloaded)

| Variable | Classification | Note |
|---|---|---|
| Temperature | DIRECT | standard GFS GRIB2 field |
| Humidity / specific humidity | DIRECT | standard GFS GRIB2 field |
| Pressure | DIRECT | surface and multiple levels |
| Geopotential height | DIRECT | standard pressure-level field |
| U/V wind | DIRECT | multiple pressure levels, standard field |
| CAPE | **UNCONFIRMED** | GFS GRIB2 output does include a CAPE field operationally, consistent with this project's existing live-GFS pipeline (`gfs_row_select.py`/`forecast_action.py` already parse CAPE from live GFS), but the specific d084001 catalog page fetched this phase did not enumerate CAPE by name in the summary text retrieved — treat as DIRECT-by-strong-inference-from-existing-pipeline-precedent, not confirmed from the d084001 catalog text itself |
| CIN | **UNCONFIRMED**, same caveat as CAPE |
| Shear | DERIVED | computed from U/V at two pressure levels, same method already used in this project's live-GFS pipeline |
| Precipitation | DIRECT | APCP-equivalent accumulation field, same caveat as this project's existing live pipeline (already documented as a forecast-accumulation proxy, not observed QPE — Phase 0.1) |
| Precipitable water (PWAT) | DIRECT | standard GFS field, same as this project's existing (now correctly labeled, per Phase 0.1) `iwv_mm`/PWAT field |

No variable was found to be definitively UNAVAILABLE in this archive relative to
what the live-GFS pipeline already extracts — the open question is CAPE/CIN naming
precision in this specific archive's GRIB2 catalog, which would need confirming on
an actual downloaded file (not possible this phase).

## 5. Lead-time feasibility

**Direct, exact +2h/+4h/+5h support is NOT available anywhere in this archive.**
d084001's documented lead structure is 3-hourly steps (f000, f003, f006, f009, ...).
This supports **+3h and +6h directly**, but **+2h, +4h, and +5h would require
interpolation between adjacent 3-hourly forecast steps**, not a native archive value.
Per the brief's own instruction ("do not call a fixed 6-hour bucket a 2-6h forecast
unless the initialization/valid-time relationship supports it"), this must be stated
plainly: **a genuine +6h example is directly supportable; +2h and +4h are not
natively present and would need to be either (a) honestly relabeled as +3h, or
(b) constructed via temporal interpolation and explicitly flagged as INTERPOLATED,
never presented as a native forecast valid time.** This is a real, now-documented
limitation of this specific archive for the project's "2-6h" claim.

## 6. Compatibility with the current live-GFS pipeline

The current production pipeline (`gfs_row_select.py` → `forecast_action.py` →
`canonical_forecast_writer.py`) already parses live GFS GRIB2/derived fields into a
cell-level row schema (CAPE, K-index, totals-totals, lifted index, PWAT, etc. — see
`gfs_row_select.py`'s `make_row`/column list). Because d084001 is the same underlying
NCEP GFS model family (just archived historically rather than fetched live), the
**variable names, units, and general GRIB2 structure should be schema-compatible in
principle** — this is a materially better fit than ERA5, which is a different model
family (ECMWF reanalysis) with different variable naming/units conventions that would
require a translation layer. This compatibility claim is based on the shared GFS
origin documented by both sources, not on an actual side-by-side field comparison
(impossible without a downloaded file). No schema change was made or proposed this
phase.

## 7. Estimated storage requirements for a real pilot vs. full use

- Single-cycle, small-bbox, few-variable pilot (as described in Section 3): tens of MB.
- A defensible multi-year, pan-India, multi-cycle training archive (e.g. daily 00Z
  cycle only, f000/f003/f006, core variables, full India bounding box, 2015-2020):
  rough order-of-magnitude estimate in the tens-to-low-hundreds of GB range (not
  precisely computed — would need an actual sample file size to extrapolate
  reliably; flagged as an estimate, not a measurement).
- Full archive as published: 662.15 TB — explicitly not a candidate for bulk download
  under any circumstance relevant to this project.

## 8. Blockers

- **This sandbox's network egress policy rejects every relevant host** —
  `gdex.ucar.edu`, `rda.ucar.edu`, `thredds.rda.ucar.edu` (NCAR/UCAR), and
  `noaa-gfs-bdp-pds.s3.amazonaws.com` (AWS) — with the identical `connect_rejected`
  failure mode already documented for CDS/Copernicus, Bhuvan/NRSC, and HydroSHEDS in
  Phase 0.3. This is a sandbox-level network policy blocker, not a credentials or
  data-availability problem: NCAR GDEX d084001 in particular is documented as fully
  open (CC-BY-4.0, no authentication required).
- Google Cloud/Earth Engine access was not attempted (requires new account setup,
  outside this phase's scope) and is unverified either way.
- CAPE/CIN field-naming precision in d084001's actual GRIB2 catalog could not be
  confirmed without a downloaded sample file.
- The NCAR GDEX page itself warns updates will stop in early 2026 due to an AWS
  migration — long-term availability of this specific access path should be
  re-verified before committing to it as a long-term operational source.

## 9. Should historical GFS become the primary operational training-predictor source?

**Not a decision this phase makes** (per the brief), but the evidence supports a
clear directional statement: **NCAR GDEX d084001 is the strongest historical
atmospheric-predictor candidate found across Phases 0.2–0.4.2** — same model family
as the existing live pipeline (lower schema-translation risk than ERA5), genuinely
open license, genuine forecast-cycle/lead structure (unlike reanalysis), and
documented free access. Its real limitations are: (a) this sandbox cannot reach it
to prove any of this empirically, (b) its native lead structure gives clean +3h/+6h
but not native +2h/+4h/+5h, and (c) its own maintainers flag discontinuation risk in
early 2026. A defensible next step would be a human-operated pilot pull from an
environment with real network access to `rda.ucar.edu`/`gdex.ucar.edu`, followed by
the same kind of structural validation already designed (but blocked) for ERA5 in
Phase 0.3.

## Sources

- [NOAA GFS on AWS Open Data Registry](https://registry.opendata.aws/noaa-gfs-bdp-pds/)
- [NCAR GDEX dataset d084001](https://gdex.ucar.edu/datasets/d084001/)
- [NOAA GFS on Google Cloud Marketplace](https://console.cloud.google.com/marketplace/details/noaa-public/gfs)
- [NOAA GFS0P25 on Google Earth Engine](https://developers.google.com/earth-engine/datasets/catalog/NOAA_GFS0P25)

## Scope confirmation

No production code, schema, model, or data file was modified. No large archive was
downloaded (none could be — every host attempted was rejected at the network layer).
Nothing was committed, pushed, or deployed.
