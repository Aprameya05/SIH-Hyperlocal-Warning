# Phase 0.4.9 — Minimum Historical GFS Cycle Selection for a Positive VOBL Join

This phase selects, but does **not** download, the single smallest additional
historical GFS cycle/lead pair needed to extend the Phase 0.4.8 pilot with a
real positive example. No files were acquired this phase. No model was
trained. No production code was touched. Nothing was deployed, committed, or
pushed.

## 1. Real VOBL positive slots (from the actual label file)

`processed/labels/ts_labels.csv`, filtered to `cell_id == IND_13.0_78.0`,
`label_status == POSITIVE`, `date >= 2015-01-15` (the GDEX d084001 archive's
documented coverage start — positives before this date exist in the label
file but cannot be paired with any historical GFS forecast at all, so they
are excluded as candidates, not hidden). This yields 584 real positive
rows in-range; a representative early sample (not exhaustive, no candidate
invented) is reported below with each one's real same-day label context,
read directly from the file:

| Date | Slot | Label | Same-day slot 0 (00:00–05:59 IST) | Slot 1 (06:00–11:59 IST) | Slot 2 (12:00–17:59 IST) | Slot 3 (18:00–23:59 IST) |
|---|---|---|---|---|---|---|
| 2015-02-28 | 3 (18:00–23:59 IST) | POSITIVE | NEGATIVE_CONFIRMED | NEGATIVE_CONFIRMED | NEGATIVE_CONFIRMED | **POSITIVE** |
| 2015-03-03 | 1 (06:00–11:59 IST) | POSITIVE | NEGATIVE_CONFIRMED | **POSITIVE** | NEGATIVE_CONFIRMED | NEGATIVE_CONFIRMED |
| 2015-03-06 | 2 (12:00–17:59 IST) | POSITIVE | NEGATIVE_CONFIRMED | NEGATIVE_CONFIRMED | **POSITIVE** | NEGATIVE_CONFIRMED |
| 2015-04-11 | 3 | POSITIVE | NEGATIVE_CONFIRMED | NEGATIVE_CONFIRMED | NEGATIVE_CONFIRMED | **POSITIVE** |
| 2015-04-13 | 2 | POSITIVE | NEGATIVE_CONFIRMED | NEGATIVE_CONFIRMED | **POSITIVE** | NEGATIVE_CONFIRMED |
| 2015-04-17 | 0 (00:00–05:59 IST) | POSITIVE | **POSITIVE** | NEGATIVE_CONFIRMED | NEGATIVE_CONFIRMED | NEGATIVE_CONFIRMED |
| 2015-04-29 | 2 and 3 | POSITIVE (both) | NEGATIVE_CONFIRMED | NEGATIVE_CONFIRMED | **POSITIVE** | **POSITIVE** |

Converting each candidate's slot to its UTC window (IST = UTC + 5:30):

| Slot ID | IST window | UTC window |
|---|---|---|
| 0 | 00:00–05:59 | previous-day 18:30 – 00:29 |
| 1 | 06:00–11:59 | 00:30 – 06:29 |
| 2 | 12:00–17:59 | 06:30 – 12:29 |
| 3 | 18:00–23:59 | 12:30 – 18:29 |

All seven candidates above are real, pre-existing rows in
`processed/labels/ts_labels.csv` — none were invented or extrapolated. Every
one of them falls within the 2015-01-15–present GDEX d084001 coverage
window.

## 2. Minimum GFS initialization required per candidate

Using the actual slot-mapping function this project already built and
tested (`ist_slot_for()` in `scripts/build_vobl_historical_gfs_ts_join.py`,
which imports the same `SLOT_WINDOWS_IST` table as `lead_time.py` —
verified identical by `test_b_slot_boundaries_match_lead_time_contract` in
Phase 0.4.8), the following cycle/lead combinations were computed and
printed by actually running the function (not worked out by hand):

| Candidate date | Target slot | GFS cycle | Native lead | Computed valid (UTC) | Computed valid (IST) | Computed slot | Matches target? |
|---|---|---|---|---|---|---|---|
| 2015-03-03 | 1 (POSITIVE) | 00Z | f003 | 2015-03-03 03:00 | 2015-03-03 08:30 | 1 (0601-1200) | Yes |
| 2015-03-03 | 2 (NEGATIVE, same day) | 00Z (same cycle) | f009 | 2015-03-03 09:00 | 2015-03-03 14:30 | 2 (1201-1800) | Yes |
| 2015-03-06 | 2 (POSITIVE) | 00Z | f009 | 2015-03-06 09:00 | 2015-03-06 14:30 | 2 (1201-1800) | Yes |
| 2015-03-06 | 1 (NEGATIVE, same day) | 00Z (same cycle) | f003 | 2015-03-06 03:00 | 2015-03-06 08:30 | 1 (0601-1200) | Yes |

Both candidate days resolve cleanly with **two native 3-hourly leads from
one 00Z cycle** (f003 and f009), each landing well inside its target slot
window with comfortable margin on both sides (for slot 1: 2.5h after the
window opens, 3.48h before it closes; for slot 2: identical 2.5h/3.48h
margins) — no interpolation, no edge-of-window ambiguity, no lead outside
GDEX d084001's confirmed 3-hourly step structure (Phase 0.4.2/0.4.3B).

## 3. Archive availability cross-check

Required filenames, following the exact convention already confirmed from
real downloaded files in Phase 0.4.3A/0.4.5
(`gfs.0p25.{YYYYMMDDHH}.f{FFF}.grib2`) and documented in
`docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md`:

```
gfs.0p25.2015030300.f003.grib2
gfs.0p25.2015030300.f009.grib2
```

Expected base URL, per the acquisition guide's directly-fetched, real
GDEX data-access page (`https://gdex.ucar.edu/datasets/d084001/dataaccess/`):

```
https://data.gdex.ucar.edu/d084001/2015/20150303/gfs.0p25.2015030300.f003.grib2
https://data.gdex.ucar.edu/d084001/2015/20150303/gfs.0p25.2015030300.f009.grib2
```

**This URL pattern is the one already confirmed by direct fetch in Phase
0.4.3 (not re-verified by an actual request this phase, since this phase
does not download anything)**. The acquisition guide records a confirmed
file-size range of ~487–532 MB per global file for this naming pattern from
its own direct page fetch, though the two files this project already holds
(`gfs.0p25.2020071500.f003/f006.grib2`) are smaller (335.7 MB / 342.2 MB) —
both figures are reported here rather than reconciled, since neither was
independently re-measured this phase; actual size for the 2015-03-03 files
will only be known once downloaded. f003 and f009 are both native 3-hourly
GDEX steps (not f002/f004/f005, which Phase 0.4.2 already established are
not native to this archive) — this is consistent with, not a new finding
beyond, the archive structure already verified.

**Availability of these two specific files was not independently
re-verified this phase** (no download, no HEAD request was made — this
sandbox's egress policy was already confirmed in Phase 0.4.2 to reject the
relevant NCAR/GDEX hosts, so any such check would have to be run by the
project owner outside this sandbox). This is stated explicitly rather than
assumed: the filenames and URL follow a pattern already confirmed to work
for one other date (2020-07-15), but 2015-03-03 itself has not been
checked.

## 4. Selected cycle

**Selected: 2015-03-03, 00Z cycle, leads f003 and f009.**

This satisfies all five required constraints:

1. **Confirmed VOBL positive target** — `2015-03-03T0601-1200` is a real,
   pre-existing `POSITIVE` row in `processed/labels/ts_labels.csv` for
   `IND_13.0_78.0` (not invented).
2. **Native GFS forecast lead** — f003 and f009 are both native 3-hourly
   GDEX d084001 steps, the same lead family already used successfully in
   Phase 0.4.5/0.4.8 (f003) plus one more native step (f009), not an
   interpolated or off-grid lead.
3. **Clean temporal alignment** — both valid times land centrally inside
   their target slot (2.5h/3.48h margin on each side), verified by actually
   running this project's own slot-mapping function, not computed by hand.
4. **Availability of a confirmed negative comparison** — the same 00Z cycle
   also reaches `2015-03-03T1201-1800`, a real `NEGATIVE_CONFIRMED` row for
   the same cell, via f009 — so one cycle, two files, yields both a positive
   and a negative labeled row.
5. **Minimum additional download size** — exactly 2 files from exactly 1
   initialization cycle, the same acquisition shape as the already-proven
   Phase 0.4.5/0.4.8 pilot (which also used 2 files from 1 cycle). No larger
   acquisition is needed to get one real positive/negative pair.

2015-03-06 (POSITIVE at slot 2, NEGATIVE at slot 1, same 00Z cycle, same
f003/f009 lead pair) satisfies the same five constraints equally well and
is recorded below as the primary alternative — no ranking or scoring is
given between the two, per the brief; 2015-03-03 is selected only because
it is the chronologically earliest in-range positive found in Section 1.

## 5. Download manifest

```
Selected cycle:        2015-03-03 00Z
Required files:
  gfs.0p25.2015030300.f003.grib2   (lead 3h  -> valid 2015-03-03 03:00 UTC -> 08:30 IST -> slot 1 -> expected label POSITIVE)
  gfs.0p25.2015030300.f009.grib2   (lead 9h  -> valid 2015-03-03 09:00 UTC -> 14:30 IST -> slot 2 -> expected label NEGATIVE_CONFIRMED)

Expected archive URLs (pattern confirmed in Phase 0.4.3, not re-verified this phase):
  https://data.gdex.ucar.edu/d084001/2015/20150303/gfs.0p25.2015030300.f003.grib2
  https://data.gdex.ucar.edu/d084001/2015/20150303/gfs.0p25.2015030300.f009.grib2

Expected file sizes: not independently confirmed for this date; the
archive's documented range for this naming pattern is ~487-532 MB per
file (Phase 0.4.3 acquisition guide), while the two files already on disk
for 2020-07-15 are smaller (335.7 MB / 342.2 MB) -- actual size for
2015-03-03 is unknown until downloaded.

Why this is the minimum useful acquisition: it is exactly 2 files from 1
cycle -- the same acquisition shape already proven to work in Phase
0.4.5/0.4.8 -- and is sufficient (no more files needed) to produce one real
confirmed-positive row and one real confirmed-negative row for VOBL, which
is what the Phase 0.4.8 pilot was missing.
```

## Next-best alternatives (not downloaded)

Listed here only in case the selected archive object is unavailable when
the project owner attempts the download. No ranking or score is assigned;
these are alternatives, not a priority order.

1. **2015-03-06, 00Z cycle, f003 (NEGATIVE_CONFIRMED, slot 1) + f009
   (POSITIVE, slot 2)** — same lead pair, same margins, same single-cycle/
   two-file shape as the selected candidate; the positive/negative slots
   are swapped relative to 2015-03-03 but otherwise equivalent.
2. **2015-04-13, 00Z cycle, f009 (POSITIVE, slot 2, `2015-04-13T1201-1800`)
   + f003 (NEGATIVE_CONFIRMED, slot 1)** — same lead pair and structure;
   positive slot confirmed directly from Section 1's table.
3. **2015-04-17, 00Z cycle, 2015-04-16 18Z cycle needed for slot 0** — this
   one is noted as a harder case: the positive slot is slot 0
   (`2015-04-17T0001-0600`, IST 00:00-05:59, UTC window = previous-day
   18:30-00:29), which the *same-day* 00Z cycle cannot reach going forward
   in time (it would require a negative lead). Reaching it natively would
   need the **previous day's 18Z cycle** at lead f006 (2015-04-16 18Z + 6h =
   2015-04-17 00:00 UTC = 05:30 IST, inside slot 0 but close to its 05:59
   end) or lead f003 (2015-04-16 18Z + 3h = 21:00 UTC = 2015-04-17 02:30
   IST, centrally inside slot 0) -- listed for completeness, not selected,
   since it needs a different-day cycle rather than the same-day cycle the
   top two candidates use.

## 6. Stop condition

A suitable positive cycle **was** identified directly from the real VOBL
labels — this phase is not blocked. Per the brief's stop condition, no
files were downloaded, no pilot was rebuilt, and no model was trained this
phase. The only output of this phase is this selection document.

**Next step, not performed in this phase**: download exactly the two files
named in Section 5 (`gfs.0p25.2015030300.f003.grib2`,
`gfs.0p25.2015030300.f009.grib2`), then re-run
`scripts/build_vobl_historical_gfs_ts_join.py` (generalized to accept an
arbitrary cycle/lead pair, which it does not yet do — it is currently
hardcoded to the 2020-07-15 f003/f006 pair and would need a small,
non-production parameterization change) to produce a pilot table containing
one real positive and one real negative VOBL row.

## 7. Testing

No code was added or modified this phase (pure label/archive-convention
research and selection). Existing relevant tests were re-run to confirm no
regression:

```
python3 -m pytest tests/test_panindia_labels.py tests/test_vobl_historical_gfs_ts_join.py \
    tests/test_historical_gfs_thermodynamics.py tests/test_historical_gfs_precipitation.py -q
    -> 59 passed

python3 -m pytest . -q --ignore=test_himawari.py --ignore=test_nomads.py \
    --ignore=test_segments.py --ignore=test_segments_v2.py
    -> 199 passed  (unchanged from Phase 0.4.8's baseline)
```

No test was modified, skipped, or weakened. No production code was
touched. Nothing was deployed, committed, or pushed.
