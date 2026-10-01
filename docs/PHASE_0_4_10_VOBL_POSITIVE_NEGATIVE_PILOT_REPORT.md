# Phase 0.4.10 — VOBL Positive/Negative Pilot: Acquisition Blocked

**Status: STOPPED at Part 2 (acquisition), per the brief's own stop
condition ("If the files cannot be downloaded: STOP").** Part 1
(parameterizing the research join script) was completed and tested
successfully, since it does not depend on the download. Parts 3–11 (GRIB
verification, feature reconstruction, label join, leakage audit,
2015-vs-2020 comparison, SHA256/manifest) are **not performed**, because
they require the two 2015-03-03 GRIB files that could not be obtained this
phase. Producing them without the real files would mean fabricating or
simulating data — explicitly prohibited by this project's standing rules
and by this phase's own brief.

## Part 1 — Research join script parameterized (completed)

`scripts/build_vobl_historical_gfs_ts_join.py` no longer hardcodes
`2020071500`/`f003`/`f006`. It now exposes:

```
python3 scripts/build_vobl_historical_gfs_ts_join.py --cycle 2020071500 --leads 003 006
python3 scripts/build_vobl_historical_gfs_ts_join.py --cycle 2015030300 --leads 003 009
```

With no arguments it defaults to `cycle=2020071500, leads=[3, 6]` and
writes to the exact original output paths
(`processed/historical_gfs_vobl/historical_gfs_vobl_pilot.csv` /
`..._manifest.json`) — **verified by actually re-running the script with no
arguments and confirming the output is byte-identical in structure and
content to the original Phase 0.4.8 pilot** (2 rows, 0 positives, 2
confirmed negatives, 0 unmatched, same source files).

For a non-default cycle/lead pair (e.g. the 2015-03-03 selection), the
script derives new, non-colliding output filenames
(`historical_gfs_vobl_{cycle}_{leads}.csv` /
`..._manifest.json`) automatically rather than overwriting the 2020 pilot.

The internal feature-extraction, label-join, temporal-validation, and
leakage-classification logic from Phase 0.4.8 was preserved exactly — only
the cycle/lead/file-path selection was generalized. No feature definition,
label logic, or provenance field was changed.

`grib_filename_for(cycle, lead)` produces the exact GDEX d084001 naming
convention (`gfs.0p25.{YYYYMMDDHH}.f{FFF}.grib2`) already confirmed from
real files in Phase 0.4.3A, so a missing file is detected and reported by
name before any GRIB parsing is attempted (see Part 2 below).

### Tests added (Phase 0.4.10)

`tests/test_vobl_historical_gfs_ts_join.py` gained 8 new tests, in addition
to the 23 already present from Phase 0.4.8:

- `test_param_grib_filename_matches_gdex_convention` — filename convention correctness
- `test_param_default_args_match_original_phase_0_4_8_pilot` — CLI defaults unchanged
- `test_param_2020_f003_f006_still_works_with_no_args` — **the 2020 pilot still reproduces exactly with no arguments**
- `test_param_2020_f003_f006_explicit_args_match_no_args` — explicit args equal implicit defaults
- `test_param_2015_cycle_is_selectable_and_reports_missing_files_honestly` — **the 2015 cycle can be selected, and correctly reports `MISSING_PILOT_FILES` by exact filename rather than crashing or fabricating rows**
- `test_param_arbitrary_cycle_lead_metadata_preserved_in_manifest` — cycle/lead metadata flows correctly through to the manifest for a non-default invocation
- `test_no_production_code_imports_research_builder` — checks `backend/pipeline.py`, `forecast_action.py`, `canonical_forecast_writer.py`, `location_engine.py` directly
- `test_no_production_code_imports_research_builder_repo_wide` — repo-wide grep confirms no file outside `tests/`/`scripts/` references this module

All 31 tests in this file pass. No existing Phase 0.4.8 test was modified
or weakened.

## Part 2 — Acquisition: BLOCKED

Attempted, in this sandbox, exactly the two files named in the Phase 0.4.9
selection, no others:

```
https://data.gdex.ucar.edu/d084001/2015/20150303/gfs.0p25.2015030300.f003.grib2
https://data.gdex.ucar.edu/d084001/2015/20150303/gfs.0p25.2015030300.f009.grib2
```

**Result: both requests failed identically**, at the TLS/CONNECT level,
before any HTTP response from the GDEX server itself was possible:

```
$ curl -v --max-time 30 "https://data.gdex.ucar.edu/d084001/2015/20150303/gfs.0p25.2015030300.f003.grib2"
* Establish HTTP proxy tunnel to data.gdex.ucar.edu:443
> CONNECT data.gdex.ucar.edu:443 HTTP/1.1
< HTTP/1.1 403 Forbidden
* CONNECT tunnel failed, response 403
curl: (56) CONNECT tunnel failed, response 403
```

The sandbox's own egress-proxy status endpoint independently confirms the
same rejection for both attempts:

```
"recentRelayFailures": [
  {"kind": "connect_rejected",
   "detail": "gateway answered 403 to CONNECT (policy denial or upstream failure)",
   "host": "data.gdex.ucar.edu:443"}
]
```

This is the exact same restriction already documented in Phase 0.4.2 ("the
sandbox's egress policy rejects every relevant NCAR/UCAR/AWS host") and in
`docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md`'s audience note
("Audience: the project owner, running this from a normal
network-connected machine (not this sandbox...)"). It is not a new
finding — it is a re-confirmation that the restriction still applies, now
specifically for the 2015-03-03 object.

Per the brief: **no substitute source was used**. No alternate mirror,
cached copy, or synthetic stand-in was created. The two already-downloaded
2020-07-15 files were left untouched and were not reused to fabricate a
2015 result.

## Parts 3–11 — not performed

GRIB verification, feature reconstruction, the positive/negative join
table, the leakage audit specific to the new cycle, the 2015-vs-2020
comparison, and the SHA256/provenance manifest for the new files all
require the two files that could not be downloaded. None of these were
attempted with placeholder, interpolated, or hand-typed values — doing so
would fabricate exactly the kind of data this project's standing rules
prohibit.

`processed/historical_gfs_vobl/historical_gfs_vobl_positive_negative_pilot.csv`
and
`processed/historical_gfs_vobl/historical_gfs_vobl_positive_negative_pilot_manifest.json`
were **not created** this phase.

## Part 12 — Tests (run as instructed, independent of the blocked acquisition)

```
python3 -m pytest tests/test_historical_gfs_thermodynamics.py \
    tests/test_historical_gfs_precipitation.py \
    tests/test_vobl_historical_gfs_ts_join.py \
    tests/test_panindia_labels.py -q
    -> 67 passed

python3 -m pytest . -q --ignore=test_himawari.py --ignore=test_nomads.py \
    --ignore=test_segments.py --ignore=test_segments_v2.py
    -> 207 passed   (199 prior baseline + 8 new Phase 0.4.10 parameterization tests)
```

No existing test was modified, skipped, or weakened. The 207 figure is the
previously-established 199/199 baseline (Phase 0.4.8's 176 + 23) plus
exactly the 8 new tests this phase adds.

## What is needed to unblock this phase

The project owner (outside this sandbox, per the acquisition guide's own
audience note) needs to fetch the two named files from
`https://data.gdex.ucar.edu/d084001/2015/20150303/` and place them at:

```
data/external/historical_gfs/raw/gfs.0p25.2015030300.f003.grib2
data/external/historical_gfs/raw/gfs.0p25.2015030300.f009.grib2
```

Once present, the exact same command already validated this phase
reproduces Parts 3–11 without any further code change:

```
python3 scripts/build_vobl_historical_gfs_ts_join.py --cycle 2015030300 --leads 003 009
```

This will write
`processed/historical_gfs_vobl/historical_gfs_vobl_2015030300_f003_f009.csv`
and its manifest (the generalized script's non-default-cycle naming — see
Part 1). The positive/negative pilot table named in the brief
(`historical_gfs_vobl_positive_negative_pilot.csv`) can then be produced by
passing explicit `--out-csv`/`--out-manifest` paths to the same command, a
one-line invocation, not a code change.

## Explicit statement

This phase did **not** fabricate any label, did **not** create a negative
from absence, did **not** substitute another data source for the blocked
GDEX files, did **not** modify production inference, `backend/pipeline.py`,
`forecast_action.py`, the canonical grid, or any production workflow, did
**not** train or tune a model, and did **not** deploy, commit, or push.
The only code change was the backward-compatible parameterization of the
research-only join script, verified by both reproducing the original 2020
pilot exactly and by 8 new passing tests.
