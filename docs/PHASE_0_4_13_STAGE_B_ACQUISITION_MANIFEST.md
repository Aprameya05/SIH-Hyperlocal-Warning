# Phase 0.4.13 — Stage B Historical GFS Acquisition Manifest

Status: **manifest only — nothing in this phase was downloaded, trained, or
committed.** This document defines the exact 6 cycles / 12 files Stage B
should acquire next, selected entirely from the real, already-existing
`ts_labels.csv`, under the contracts adopted in
`docs/PHASE_0_4_12_HISTORICAL_GFS_TS_DATASET_CONTRACT.md`.

Label source (real file, not regenerated):
`SIH_PANINDIA_GRID_LABELS_20260930_143541Z/processed/labels/ts_labels.csv`,
filtered to `cell_id = IND_13.0_78.0`, `hazard = ts`,
`label_status in {POSITIVE, NEGATIVE_CONFIRMED}` — 15,276 matching rows
(584 POSITIVE, 14,692 NEGATIVE_CONFIRMED), spanning 2015-01-02 through
2026-01-01.

## 6-target summary

| # | class | target date (IST) | slot | slot window (IST) | why selected |
|---|-------|--------------------|------|--------------------|--------------|
| 1 | POSITIVE | 2017-04-16 | 2 | 12:00-17:59 | pre-monsoon (April), 2017 — fills a training year with no existing pilot coverage |
| 2 | POSITIVE | 2021-07-24 | 2 | 12:00-17:59 | monsoon (July), 2021 — different season and year from #1, >1500 days from the 2020-07-15 pilot event |
| 3 | POSITIVE | 2023-11-06 | 3 | 18:00-23:59 | post-monsoon (November), 2023 — latest usable training year, third distinct season |
| 4 | NEGATIVE_CONFIRMED | 2016-01-15 | 1 | 06:00-11:59 | winter (January), 2016 — dry-season negative, far from all positives |
| 5 | NEGATIVE_CONFIRMED | 2019-12-10 | 0 | 00:00-05:59 | post-monsoon/winter (December), 2019 — a training year otherwise underrepresented (only 2 POSITIVE rows all of 2019) |
| 6 | NEGATIVE_CONFIRMED | 2022-08-15 | 3 | 18:00-23:59 | monsoon (August), 2022 — negative drawn from inside the wet season, not just the dry season |

All 6 target rows were read directly from the real label file (verified by
exact-timestamp lookup, shown in Part 1 below). None were inferred,
interpolated, or regenerated. Years used: 2016, 2017, 2019, 2021, 2022,
2023 — six distinct calendar years, all inside the 2015-2023 TRAIN range.
Seasons represented: winter (Jan/Dec), pre-monsoon (Apr), monsoon (Jul/Aug),
post-monsoon (Nov) — four of the four. Neither 2015-03-03 nor 2020-07-15
(the existing pilot dates) appears.

## Part 1 — exact label rows backing each target

```
2017-04-16T1201-1800  IND_13.0_78.0  ts  POSITIVE            IMD station observation, VOBL/43295  2017-04-16
2021-07-24T1201-1800  IND_13.0_78.0  ts  POSITIVE            IMD station observation, VOBL/43295  2021-07-24
2023-11-06T1801-2400  IND_13.0_78.0  ts  POSITIVE            IMD station observation, VOBL/43295  2023-11-06
2016-01-15T0601-1200  IND_13.0_78.0  ts  NEGATIVE_CONFIRMED  IMD station observation, VOBL/43295  2016-01-15
2019-12-10T0001-0600  IND_13.0_78.0  ts  NEGATIVE_CONFIRMED  IMD station observation, VOBL/43295  2019-12-10
2022-08-15T1801-2400  IND_13.0_78.0  ts  NEGATIVE_CONFIRMED  IMD station observation, VOBL/43295  2022-08-15
```

No UNKNOWN-status row was used. No negative was inferred from a missing
row — every negative above is a materialized `NEGATIVE_CONFIRMED` row with
a real station observation backing it.

## Part 2/3/4 — cycle and lead selection

For each target, the GFS cycle was chosen by the same logic later used in
`ist_slot_for()` (`scripts/build_vobl_historical_gfs_ts_join.py`): compute
`valid_time_utc = initialization_time + forecast_lead`, convert to IST,
and confirm the IST slot the result falls into — never assumed from the
calendar date or filename. The selection rule applied uniformly: the
**latest-available 00Z/06Z/12Z/18Z cycle whose two shortest native leads
(f003 and f006) both land inside the target slot.** This is the same
f003/f006 structure already verified against real files in the 2020-07-15
pilot, and it minimizes forecast lead (shorter lead = more skillful
forecast, smaller download, consistent with "prefer a cycle that provides
two native leads inside the same target slot").

The mapping is mechanical and the same for every slot:

| target slot (IST) | cycle used | lead 1 | lead 2 |
|---|---|---|---|
| 00:00-05:59 (slot 0) | previous day's 18Z | f003 | f006 |
| 06:00-11:59 (slot 1) | same day's 00Z | f003 | f006 |
| 12:00-17:59 (slot 2) | same day's 06Z | f003 | f006 |
| 18:00-23:59 (slot 3) | same day's 12Z | f003 | f006 |

## Part 9 — the 12-file acquisition table

| # | label | target date | slot | event_group_key | cycle | lead | valid UTC | valid IST | expected URL | archive verification status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | POSITIVE | 2017-04-16 | 2 | `IND_13.0_78.0\|2017-04-16\|2` | 2017-04-16T06Z | f003 | 2017-04-16T09:00:00Z | 2017-04-16T14:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2017/20170416/gfs.0p25.2017041606.f003.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 2 | POSITIVE | 2017-04-16 | 2 | `IND_13.0_78.0\|2017-04-16\|2` | 2017-04-16T06Z | f006 | 2017-04-16T12:00:00Z | 2017-04-16T17:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2017/20170416/gfs.0p25.2017041606.f006.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 3 | POSITIVE | 2021-07-24 | 2 | `IND_13.0_78.0\|2021-07-24\|2` | 2021-07-24T06Z | f003 | 2021-07-24T09:00:00Z | 2021-07-24T14:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2021/20210724/gfs.0p25.2021072406.f003.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 4 | POSITIVE | 2021-07-24 | 2 | `IND_13.0_78.0\|2021-07-24\|2` | 2021-07-24T06Z | f006 | 2021-07-24T12:00:00Z | 2021-07-24T17:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2021/20210724/gfs.0p25.2021072406.f006.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 5 | POSITIVE | 2023-11-06 | 3 | `IND_13.0_78.0\|2023-11-06\|3` | 2023-11-06T12Z | f003 | 2023-11-06T15:00:00Z | 2023-11-06T20:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2023/20231106/gfs.0p25.2023110612.f003.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 6 | POSITIVE | 2023-11-06 | 3 | `IND_13.0_78.0\|2023-11-06\|3` | 2023-11-06T12Z | f006 | 2023-11-06T18:00:00Z | 2023-11-06T23:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2023/20231106/gfs.0p25.2023110612.f006.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 7 | NEGATIVE_CONFIRMED | 2016-01-15 | 1 | `IND_13.0_78.0\|2016-01-15\|1` | 2016-01-15T00Z | f003 | 2016-01-15T03:00:00Z | 2016-01-15T08:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2016/20160115/gfs.0p25.2016011500.f003.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 8 | NEGATIVE_CONFIRMED | 2016-01-15 | 1 | `IND_13.0_78.0\|2016-01-15\|1` | 2016-01-15T00Z | f006 | 2016-01-15T06:00:00Z | 2016-01-15T11:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2016/20160115/gfs.0p25.2016011500.f006.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 9 | NEGATIVE_CONFIRMED | 2019-12-10 | 0 | `IND_13.0_78.0\|2019-12-10\|0` | 2019-12-09T18Z | f003 | 2019-12-09T21:00:00Z | 2019-12-10T02:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2019/20191209/gfs.0p25.2019120918.f003.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 10 | NEGATIVE_CONFIRMED | 2019-12-10 | 0 | `IND_13.0_78.0\|2019-12-10\|0` | 2019-12-09T18Z | f006 | 2019-12-10T00:00:00Z | 2019-12-10T05:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2019/20191209/gfs.0p25.2019120918.f006.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 11 | NEGATIVE_CONFIRMED | 2022-08-15 | 3 | `IND_13.0_78.0\|2022-08-15\|3` | 2022-08-15T12Z | f003 | 2022-08-15T15:00:00Z | 2022-08-15T20:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2022/20220815/gfs.0p25.2022081512.f003.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |
| 12 | NEGATIVE_CONFIRMED | 2022-08-15 | 3 | `IND_13.0_78.0\|2022-08-15\|3` | 2022-08-15T12Z | f006 | 2022-08-15T18:00:00Z | 2022-08-15T23:30:00+05:30 | https://data.gdex.ucar.edu/d084001/2022/20220815/gfs.0p25.2022081512.f006.grib2 | EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN |

Note on row 9/10: the target IST date is 2019-12-10, slot 0
(00:00-05:59 IST = 18:30-00:00 UTC the previous day). The correctly
computed cycle is the **previous UTC day's 18Z** (2019-12-09T18Z), not a
same-calendar-date cycle — this is exactly the kind of filename-only
assumption the brief warned against, and it was caught by computing
`init + lead -> valid_time -> IST slot` explicitly rather than assumed.

URL pattern: `https://data.gdex.ucar.edu/d084001/YYYY/YYYYMMDD/gfs.0p25.YYYYMMDDHH.fLLL.grib2`,
identical in form to the 4 URLs already used successfully for the
2015-03-03 and 2020-07-15 pilot downloads.

## Part 5 — archive URL verification status

The repository already has a HEAD-only (no-download) size-probe utility,
`head_request_size()` in `scripts/acquire_historical_gfs_pilot.py`, reused
here rather than writing a new one. It was run against 2 of the 12 URLs as
a check. Result: **both HEAD requests failed** — `Tunnel connection failed:
403 Forbidden` — because this sandbox's outbound network proxy does not
allowlist `data.gdex.ucar.edu`. This is an environment limitation, not a
statement about the remote files. No URL in the table above has been
confirmed to currently return 200, and none is claimed to. Every row is
marked `EXPECTED_FROM_VERIFIED_ARCHIVE_PATTERN`: the URL *shape* is
identical to the 4 URLs that were already downloaded successfully in
earlier phases, but individual existence/size for these 12 specific files
has not been checked. This should be re-attempted from an environment with
real internet access (e.g. your machine) before or during the actual
download step, using the same `head_request_size()` helper if useful.

## Part 6 — event-grouping verification

Computed directly with the repository's own `event_group_key_for()`
(`scripts/build_vobl_historical_gfs_ts_join.py`), not reimplemented:

- 12 rows -> exactly 6 distinct `event_group_key` values, one per target.
- Each pair (f003/f006) of the same target shares its `event_group_key`
  exactly — verified programmatically, not by inspection.
- No two different targets share a key (all 6 keys differ by date and/or
  slot and/or, trivially, cell).
- None of the 6 new keys collides with the two existing pilot keys
  (`IND_13.0_78.0|2015-03-03|1`, `IND_13.0_78.0|2015-03-03|2`,
  `IND_13.0_78.0|2020-07-15|1`) — target dates do not overlap.

## Part 7 — year-split compatibility

Checked with `scripts/historical_dataset_split.py`'s `assign_partition()`
(default `DEFAULT_TRAIN_YEARS=(2015,2023)`): all 6 target `ist_date`
values resolve to `"train"`. None resolves to `"holdout"` or
`"unassigned"`. This was verified programmatically against the actual
function, not asserted by inspection of the year numbers alone.

Distribution:

| axis | values |
|---|---|
| year | 2016, 2017, 2019, 2021, 2022, 2023 (6 distinct years, all TRAIN) |
| month | Jan, Apr, Jul, Aug, Nov, Dec (6 distinct months) |
| season | winter (Jan, Dec), pre-monsoon (Apr), monsoon (Jul, Aug), post-monsoon (Nov) — 4 of 4 |
| class | 3 POSITIVE, 3 NEGATIVE_CONFIRMED |

## Part 8 — storage estimate

The four files already downloaded for the existing pilots are the only
real size evidence available:

```
gfs.0p25.2015030300.f003.grib2   209.2 MB
gfs.0p25.2015030300.f009.grib2   212.1 MB
gfs.0p25.2020071500.f003.grib2   320.1 MB
gfs.0p25.2020071500.f006.grib2   326.3 MB
```

Observed range: **~209-327 MB per file** (varies by cycle/date, not by
lead alone — 2020 files ran noticeably larger than 2015 files in this
small sample). Applying that observed range to 12 files:

- expected per-file range: **~0.2-0.33 GB**
- estimated total for 12 files: **roughly 2.5-4.0 GB**

This is an estimate from 4 observed files, not a guarantee — actual sizes
for these specific 12 files are unknown until downloaded, since archive
file size can vary with data content, not just date.

## Part 10 — final audit

| requirement | result |
|---|---|
| exactly 6 cycles | PASS (6 distinct cycles, one per target) |
| exactly 12 files | PASS |
| exactly 3 positive target slots | PASS |
| exactly 3 negative target slots | PASS |
| no pilot duplicates | PASS (checked against 2015-03-03 and 2020-07-15) |
| all target years 2015-2023 | PASS (2016, 2017, 2019, 2021, 2022, 2023) |
| multiple years represented | PASS (6 distinct years) |
| at least two seasons represented | PASS (4 distinct seasons) |
| every pair maps to the intended slot | PASS (computed via `ist_slot_for()`, not assumed) |
| every pair has two native 3-hourly leads | PASS (f003 + f006 for every target) |
| no fabricated labels | PASS (all 6 rows read verbatim from `ts_labels.csv`) |
| no UNKNOWN labels | PASS (all are POSITIVE or NEGATIVE_CONFIRMED) |
| no event_group_key duplication across different target outcomes | PASS (6 unique keys, verified programmatically) |

All Part 10 requirements are satisfied. No conflict to report, no
correction needed.

## Exact commands to run later (download only, no training)

Run from the repository root, once real internet access is available.
These reuse the existing `scripts/acquire_historical_gfs_pilot.py`
CLI (confirmed this phase by reading its `argparse` block directly, not
from memory) — no new download tooling was written for this phase. The
script defaults to a dry run; `--execute` is required to actually fetch.
Run a dry run first for each cycle to confirm the size-ceiling/URL logic
before adding `--execute`:

```bash
# dry run (default) -- verify URLs/sizes before committing to a real fetch
python scripts/acquire_historical_gfs_pilot.py --cycle 2017041606 --leads 003 006
python scripts/acquire_historical_gfs_pilot.py --cycle 2021072406 --leads 003 006
python scripts/acquire_historical_gfs_pilot.py --cycle 2023110612 --leads 003 006
python scripts/acquire_historical_gfs_pilot.py --cycle 2016011500 --leads 003 006
python scripts/acquire_historical_gfs_pilot.py --cycle 2019120918 --leads 003 006
python scripts/acquire_historical_gfs_pilot.py --cycle 2022081512 --leads 003 006

# once each dry run looks correct, re-run with --execute to actually download
python scripts/acquire_historical_gfs_pilot.py --cycle 2017041606 --leads 003 006 --execute
python scripts/acquire_historical_gfs_pilot.py --cycle 2021072406 --leads 003 006 --execute
python scripts/acquire_historical_gfs_pilot.py --cycle 2023110612 --leads 003 006 --execute
python scripts/acquire_historical_gfs_pilot.py --cycle 2016011500 --leads 003 006 --execute
python scripts/acquire_historical_gfs_pilot.py --cycle 2019120918 --leads 003 006 --execute
python scripts/acquire_historical_gfs_pilot.py --cycle 2022081512 --leads 003 006 --execute
```

If a file exceeds the script's default size ceiling, it will refuse
unless `--allow-large` is also passed — check that output rather than
adding the flag preemptively.

Each command should be followed by a `--cycle`/`--leads` join run (same
pattern as the existing two pilots) once Stage B's full 12-file batch is
on disk, to build the research dataset under the Phase 0.4.12 contract —
that join run is the next phase's job, not this one's.
