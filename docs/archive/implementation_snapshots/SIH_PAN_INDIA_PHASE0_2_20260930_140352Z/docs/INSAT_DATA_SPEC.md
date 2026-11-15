# INSAT-3D/3DR Data Specification — Access Audit (Phase 2) — 2026-09-30

## Access Status: DATA BLOCKED (unchanged from the prior pass), plus a new finding from actually auditing the existing fetcher's code

`backend/fetch_insat3d.py` already existed before this pass (233 lines, written in an earlier pass of this session) and was re-read in full this time, as instructed ("audit it against the current official MOSDAC interface"). Two things follow from that audit:

### 1. Credentials/network — still blocked, unchanged

`MOSDAC_USER`/`MOSDAC_PASS` are unset. This sandbox cannot reach `mosdac.gov.in` — same `403`-from-proxy result as the NCMRWF test in `docs/IMDAA_DATA_SPEC.md`. No file exists at the script's own output path (`data/insat3d_iwv.json`).

### 2. New finding: the existing script's own internals are self-admittedly speculative, not confirmed against a real download

Reading the code closely (not just its docstring) surfaces several places where the script itself says its assumptions are unverified:

- The MOSDAC filename pattern (`3DIMG_<date>_<time>_L1C_ASIA_MER_BIMG.h5`) is annotated in its own comment as `"may vary by product version"`.
- The HDF5 dataset key it looks for (`IMG_TIR2`) is annotated `"Typical INSAT-3D L1C dataset path -- may vary by file version"`, with a fallback that just grabs the first 2D dataset it finds if that key is missing — a reasonable defensive fallback, but evidence the exact schema was never confirmed against a real file.
- The brightness-temperature-to-IWV conversion (`iwv = max(0, (270 - BT) * 1.8)`) is a rough empirical heuristic, not a calibrated retrieval algorithm, and the script's own output already labels it honestly: `"note": "IWV proxy derived from WV brightness temperature. Not calibrated PWAT."` — this labeling is correct and was not weakened in this audit.

**Conclusion: this script is real, reasonable, defensively-written scaffolding, but it has never been run against a real MOSDAC file, and several of its structural assumptions are explicitly marked in its own comments as unconfirmed.** It should not be described as "INSAT integration ready" without that caveat — it's "INSAT integration scaffolded, unverified."

## What I Did NOT Do

I did not attempt to confirm or correct the filename pattern, HDF5 key, or conversion formula from memory. I have no current, verified visibility into MOSDAC's actual live API or file structure (no network access this session), so guessing corrections would be exactly the kind of unverified claim this pass prohibits. The honest state is: the script is a best-effort draft awaiting a real sample to test against.

## Required From You

1. Register at https://mosdac.gov.in, obtain `MOSDAC_USER`/`MOSDAC_PASS`.
2. Confirm the current API/download path is still what `MOSDAC_BASE = "https://mosdac.gov.in/live"` assumes — this has changed before per the script's own docstring.
3. Ideally, download one real sample file and place it somewhere accessible so `extract_iwv_from_hdf5()` can be run against real data and its key/format assumptions corrected against ground truth, the same way `scripts/parse_imdaa.py` is designed to self-correct on first real use.

## Prioritized Products (per SIH architecture)

TIR (cloud-top temperature — currently substituted by Himawari at VOBL, would extend pan-India), WV (the IWV proxy this script already targets), QPE, and precipitable water / sounder-derived humidity products where MOSDAC actually offers them — not assumed present. Until a real product catalog page is fetched by you (this sandbox cannot fetch it), no additional product beyond what's already coded is claimed as available.

## What "INSAT Live" Would Actually Require

A successful download producing a real HDF5 file, `extract_iwv_from_hdf5()` run against it and its key assumptions corrected, and the result wired into `regrid.py`/the pan-India grid — none of which has happened. Himawari continues to serve as the (honestly labeled, never renamed) CTT source at VOBL in the meantime.
