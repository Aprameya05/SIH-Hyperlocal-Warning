# Phase 0.4.14 — Stage B Acquisition Verification

Audit-only phase. No model training, no production changes, no deployment,
no commit, no push, no new downloads performed in this phase. Source of
truth: `docs/PHASE_0_4_13_STAGE_B_ACQUISITION_MANIFEST.md`.

**IMPORTANT LIMITATION DISCOVERED THIS PHASE** (reported up front, not
buried): this session's device-bridge tooling can only stage a file into
the cloud workspace for GRIB inspection if it is **under 400 MB**, and the
device-side shell (`device_bash`) that could otherwise run eccodes
directly on the user's machine reported "Workspace unavailable" on every
attempt (same failure mode as Phase 0.4.10B). Of the 12 Stage B files,
**6 are under 400 MB and were fully forensically verified** (GRIB
message-level inspection, SHA256, cycle/lead/slot recomputation,
event-grouping, field presence, precipitation/prate semantics); **6 exceed
400 MB and could not be staged or shell-inspected in this session**. For
those 6, verification is limited to what `device_list_dir` and the
acquisition-time `manifest.json` can establish: exact byte size and the
previously-recorded SHA256 — not independently recomputed, not GRIB-level
inspected, not cycle/lead-recomputed from message metadata this phase. See
Part C for exactly which 6 and why this is a blocker, not a pass.

## A. Verified facts

### A1. File inventory — all 12 Stage B files

The four pre-existing pilot files (`gfs.0p25.2015030300.f003/f009.grib2`,
`gfs.0p25.2020071500.f003/f006.grib2`) are excluded from the count below,
exactly as instructed.

| # | filename | exists | bytes | MB | non-empty | valid GRIB2 | SHA256 |
|---|---|---|---|---|---|---|---|
| 1 | gfs.0p25.2016011500.f003.grib2 | yes | 216,896,273 | 206.9 | yes | **yes (GRIB/7777 confirmed, 366 msgs)** | `c5017aa8d756e8e6868e1c4cb0a58e77c94bb44b145345b843e54f4f025ef0ba` (independently recomputed, matches manifest) |
| 2 | gfs.0p25.2016011500.f006.grib2 | yes | 220,945,401 | 210.7 | yes | **yes (366 msgs)** | `6dfe8f27779a117a46362a70731bf24a71167a61feb3c1f2d6e9e8bb61a06b12` (matches manifest) |
| 3 | gfs.0p25.2017041606.f003.grib2 | yes | 229,795,432 | 219.2 | yes | **yes (415 msgs)** | `ad78d7ca9f9e3839771b6950d34fa6a806b9d4b70309f52b0015ca86bb3f5fee` (matches manifest) |
| 4 | gfs.0p25.2017041606.f006.grib2 | yes | 235,362,550 | 224.4 | yes | **yes (415 msgs)** | `f3fe2d9062a9c79d1a66efd3da570742e3cdc5491505cf21825ce747d340113a` (matches manifest) |
| 5 | gfs.0p25.2019120918.f003.grib2 | yes | 329,878,782 | 314.6 | yes | **yes (588 msgs)** | `0f590527b8456749bffa78683b6c71a4cc2852fd7cb19fff0711cb655376dfb9` (matches manifest) |
| 6 | gfs.0p25.2019120918.f006.grib2 | yes | 334,367,927 | 318.9 | yes | **yes (588 msgs)** | `43b8ec846132ca1228aa30dba0d29529e1af0163f8b6f51c305abf41c53d8087` (matches manifest) |
| 7 | gfs.0p25.2021072406.f003.grib2 | yes (device-confirmed) | 545,088,909 | 519.9 | size > 0 | **NOT independently verified this phase** (>400MB, see Part C) | `e026a6779088fa7135d5b51cf727c5bf398a7b6b269626124d3d9453b0e4308c` (manifest-recorded only) |
| 8 | gfs.0p25.2021072406.f006.grib2 | yes (device-confirmed) | 550,065,659 | 524.6 | size > 0 | **NOT independently verified this phase** | `943cf50522d559090f836fb4f1d7b227623ec2410b3316eb99988c51982be1e9` (manifest-recorded only) |
| 9 | gfs.0p25.2022081512.f003.grib2 | yes (device-confirmed) | 547,784,169 | 522.4 | size > 0 | **NOT independently verified this phase** | `5690dbfa60b2546c316dc92c5b81508f1f631b26c0d8252ffba69c4588314346` (manifest-recorded only) |
| 10 | gfs.0p25.2022081512.f006.grib2 | yes (device-confirmed) | 551,714,308 | 526.2 | size > 0 | **NOT independently verified this phase** | `e4821947f16bfb0d02332a9d7b9e843b34bb31ff3ce09c13cd72860b1411ce97` (manifest-recorded only) |
| 11 | gfs.0p25.2023110612.f003.grib2 | yes (device-confirmed) | 542,915,936 | 517.7 | size > 0 | **NOT independently verified this phase** | `1034598ef53f22ab0d97f4875f4f5f2b2babeeab042a4e519370bd24c44867ce` (manifest-recorded only) |
| 12 | gfs.0p25.2023110612.f006.grib2 | yes (device-confirmed) | 543,369,258 | 518.1 | size > 0 | **NOT independently verified this phase** | `a50f1f7d602870c73f249e7563f1d4d0747e8220e01fff72d2ad14e5f73a93f3` (manifest-recorded only) |

All 12 byte sizes match exactly between the live filesystem
(`device_list_dir`) and `data/external/historical_gfs/manifest.json`'s
`size_bytes` field — a real cross-check, not an assumption, and a
meaningful one even for the 6 large files: a truncated or re-run download
would very likely change the byte count, and it did not.

### A2. GRIB metadata verification (the 6 fully-inspected files)

All 6 confirmed, using eccodes message-level enumeration
(`enumerate_messages`/`find_message` from
`scripts/build_vobl_historical_gfs_ts_join.py`, not reimplemented):

- `gridType = regular_ll`, `Ni = 1440`, `Nj = 721` (global 0.25° — 360/0.25
  = 1440, 180/0.25+1 = 721) for all 6 files.
- Latitude range `[90.0, -90.0]` (north-to-south), longitude `[0.0,
  359.75]`, `jScansPositively = 0` — identical orientation to the already-
  verified pilot files.
- Initialization time read from each file's own `dataDate`/`dataTime`
  (first message), not from the filename:
  - `gfs.0p25.2016011500.*` → `2016-01-15T00:00:00Z` (matches filename)
  - `gfs.0p25.2017041606.*` → `2017-04-16T06:00:00Z` (matches filename)
  - `gfs.0p25.2019120918.*` → `2019-12-09T18:00:00Z` (matches filename)
- **All required field categories present in all 6 files** — checked by
  name, not assumed from prior phases:
  `cape@surface/0`, `cin@surface/0`, `pwat@atmosphereSingleLayer/0`,
  `u@isobaricInhPa/850`, `v@isobaricInhPa/850`, `u@isobaricInhPa/200`,
  `v@isobaricInhPa/200`, `t@isobaricInhPa/850`, `t@isobaricInhPa/700`,
  `t@isobaricInhPa/500`, `r@isobaricInhPa/850`, `r@isobaricInhPa/700` — all
  12 field/level combinations present in all 6 files, zero missing fields.
- Precipitation fields present in all 6: `tp` (surface, `stepType=accum`)
  and `prate` (surface) — see Part A4/A5 below for exact semantics.
- Message counts: 366 (2016011500 files), 415 (2017041606 files), 588
  (2019120918 files). These differ between cycles because the archive
  includes variable sets that differ slightly by cycle (e.g. soil-layer
  fields at multiple depths) — this is a real, benign property of the GFS
  archive, confirmed by comparing against the already-verified-good
  `gfs.0p25.2020071500.f003.grib2` pilot file, which shows the **identical
  duplicate-combination pattern** (`st`/`soilw` at multiple unlabeled
  depths, one duplicate `tp` message, one duplicate `acpcp` message — 6
  duplicate (shortName, typeOfLevel, level, stepType) combinations in both
  the already-trusted 2020 file and the new 2019 file). This is not new
  corruption; it is a pre-existing GFS archive structural quirk this phase
  happened to look closely enough to notice and cross-check.

### A3. Cycle/lead integrity — computed from GRIB metadata, not filename

For every one of the 6 inspected files:
`initialization_time_utc` (from GRIB `dataDate`/`dataTime`) + the file's
own forecast lead (cross-checked via the `cape` message's `endStep`,
matching the filename's `fLLL`) → `valid_time_utc` → converted to IST →
passed through the repository's real `ist_slot_for()`.

| file | init (GRIB) | lead (GRIB endStep) | valid UTC | valid IST | IST date | slot | matches manifest? |
|---|---|---|---|---|---|---|---|
| 2016011500.f003 | 2016-01-15T00:00Z | 3 | 2016-01-15T03:00Z | 2016-01-15T08:30+05:30 | 2016-01-15 | 1 | YES |
| 2016011500.f006 | 2016-01-15T00:00Z | 6 | 2016-01-15T06:00Z | 2016-01-15T11:30+05:30 | 2016-01-15 | 1 | YES |
| 2017041606.f003 | 2017-04-16T06:00Z | 3 | 2017-04-16T09:00Z | 2017-04-16T14:30+05:30 | 2017-04-16 | 2 | YES |
| 2017041606.f006 | 2017-04-16T06:00Z | 6 | 2017-04-16T12:00Z | 2017-04-16T17:30+05:30 | 2017-04-16 | 2 | YES |
| 2019120918.f003 | 2019-12-09T18:00Z | 3 | 2019-12-09T21:00Z | 2019-12-10T02:30+05:30 | 2019-12-10 | 0 | YES |
| 2019120918.f006 | 2019-12-09T18:00Z | 6 | 2019-12-10T00:00Z | 2019-12-10T05:30+05:30 | 2019-12-10 | 0 | YES |

All 6 match the Phase 0.4.13 manifest exactly (target date, target slot).
The 2019-12-10 pair is the one the manifest itself flagged as
counter-intuitive (previous day's 18Z cycle, not a same-date cycle) — this
phase independently recomputed it from the file's own GRIB metadata, not
by re-trusting the prior phase's arithmetic, and it checks out.

The 6 un-inspected (>400MB) files' cycle/lead/slot mapping is **not**
independently confirmed this phase from GRIB metadata — see Part C.

### A4. Event-group verification

Using the repository's real `event_group_key_for()`
(`scripts/build_vobl_historical_gfs_ts_join.py`), computed for the 6
GRIB-confirmed files and asserted for the full set of 6 targets by a new
focused test (`tests/test_phase_0_4_14_stage_b_verification.py`):

- 12 target rows (6 targets × 2 leads) → exactly 6 distinct
  `event_group_key` values.
- `f003`/`f006` of the same target always produce the identical key
  (mechanically guaranteed — the key depends only on cell/date/slot, none
  of which differ between a target's two leads).
- No two different targets share a key.
- None of the 6 new keys collides with the 3 existing pilot keys
  (`IND_13.0_78.0|2015-03-03|1`, `IND_13.0_78.0|2015-03-03|2`,
  `IND_13.0_78.0|2020-07-15|1`).

Exact keys, matching the Phase 0.4.13 manifest verbatim:

```
IND_13.0_78.0|2017-04-16|2
IND_13.0_78.0|2021-07-24|2
IND_13.0_78.0|2023-11-06|3
IND_13.0_78.0|2016-01-15|1
IND_13.0_78.0|2019-12-10|0
IND_13.0_78.0|2022-08-15|3
```

### A5. Label cross-check

Read directly from the real, un-modified
`SIH_PANINDIA_GRID_LABELS_20260930_143541Z/processed/labels/ts_labels.csv`
(not regenerated, not re-derived):

| target date | slot (IST) | cell_id | hazard | label_status | source |
|---|---|---|---|---|---|
| 2016-01-15 | 0601-1200 | IND_13.0_78.0 | ts | NEGATIVE_CONFIRMED | IMD station observation, VOBL/43295 |
| 2017-04-16 | 1201-1800 | IND_13.0_78.0 | ts | POSITIVE | IMD station observation, VOBL/43295 |
| 2019-12-10 | 0001-0600 | IND_13.0_78.0 | ts | NEGATIVE_CONFIRMED | IMD station observation, VOBL/43295 |
| 2021-07-24 | 1201-1800 | IND_13.0_78.0 | ts | POSITIVE | IMD station observation, VOBL/43295 |
| 2022-08-15 | 1801-2400 | IND_13.0_78.0 | ts | NEGATIVE_CONFIRMED | IMD station observation, VOBL/43295 |
| 2023-11-06 | 1801-2400 | IND_13.0_78.0 | ts | POSITIVE | IMD station observation, VOBL/43295 |

Exactly 3 POSITIVE, 3 NEGATIVE_CONFIRMED, 0 UNKNOWN. No label was
regenerated, inferred, or fabricated — all 6 rows exist verbatim in the
real CSV.

### A6. Year split

Using the real `assign_partition()`
(`scripts/historical_dataset_split.py`, `DEFAULT_TRAIN_YEARS=(2015,2023)`):
all 6 target `ist_date` values resolve to `"train"`. None resolves to
`"holdout"` or `"unassigned"`. Verified programmatically, including by a
new focused test.

Distribution:

| axis | values |
|---|---|
| year | 2016, 2017, 2019, 2021, 2022, 2023 (6 distinct, all TRAIN) |
| month | Jan, Apr, Jul, Aug, Nov, Dec |
| season | winter (Jan, Dec), pre-monsoon (Apr), monsoon (Jul, Aug), post-monsoon (Nov) |
| class | 3 POSITIVE, 3 NEGATIVE_CONFIRMED |

### A7. File content sanity

For the 6 fully-inspected files: GRIB header (`GRIB`) and trailer
(`7777`) both confirmed present at the exact expected byte offsets —
not an HTML error page, not truncated mid-record. Message-level
enumeration completed without exception for the full declared message
count (366/415/588), which would not happen on a genuinely truncated or
corrupted GRIB stream (eccodes raises on a malformed/incomplete message).
Init time, grid, and field content all match the intended cycle, not a
different or duplicate cycle's content. No two of the 6 inspected files
are byte-identical (independently confirmed via 6 distinct SHA256
values).

For the 6 un-inspected files: only size and manifest-recorded SHA256 are
available this phase — see Part C. They are each other pairwise distinct
by size and by manifest SHA256, and distinct from the 6 inspected files,
but that is not the same as confirming each is a valid, non-truncated
GRIB2 stream.

### A8. Precipitation semantics (reporting only — no features built)

| file | tp: stepType/startStep/endStep/units | prate messages |
|---|---|---|
| 2016011500.f003 | accum / 0 / 3 / kg m⁻² | 1 msg: avg, startStep=0, endStep=3, units kg m⁻² s⁻¹ |
| 2016011500.f006 | accum / 0 / 6 / kg m⁻² | 1 msg: avg, startStep=0, endStep=6 |
| 2017041606.f003 | accum / 0 / 3 / kg m⁻² | 1 msg: avg, startStep=0, endStep=3 |
| 2017041606.f006 | accum / 0 / 6 / kg m⁻² | 1 msg: avg, startStep=0, endStep=6 |
| 2019120918.f003 | accum / 0 / 3 / kg m⁻² (×2, duplicate message — see A2) | **2 msgs**: one `instant` (startStep=endStep=3, forecastTime=3), one `avg` (startStep=0, endStep=3) |
| 2019120918.f006 | accum / 0 / 6 / kg m⁻² (×2, duplicate) | **2 msgs**: one `instant` (startStep=endStep=6, forecastTime=6), one `avg` (startStep=0, endStep=6) |

This confirms, on three more real cycles, the Phase 0.4.12 finding that
`prate` availability is inconsistent across the archive: 2016 and 2017
each have only an `avg` prate message (same shape as 2015-03-03); 2019 has
both `instant` and `avg` (same shape as 2020-07-15). `tp`'s
`startStep=0` for both leads in all 6 files means the existing
`compute_interval_precipitation()` validity check would accept these
pairs (both windows share `startStep=0`) — unlike the 2015-03-03 f009
case. This is reported for the next phase's dataset-build step; no
interval precipitation or prate feature was computed or written in this
phase. `tp` is never called `qpe_mm`; no cross-lead differencing was
performed.

Precipitation messages for the 6 un-inspected files are **not reported**
— not available without GRIB inspection, and not guessed.

### A9. Prate semantics

Covered in A8 above for the 6 inspected files. No file's `prate` was
normalized, relabeled, or treated as instantaneous when it was `avg`.

### A10. Provenance (`data/external/historical_gfs/manifest.json`)

All 12 Stage B files have an entry, each carrying `source_url`,
`file_name`, `cycle`, `forecast_lead_hours`, `size_bytes`, and `sha256`.
Spot-checked fields:

- `source_url` for all 12 matches the exact URL pattern from the Phase
  0.4.13 manifest (`https://data.gdex.ucar.edu/d084001/YYYY/YYYYMMDD/
  gfs.0p25.YYYYMMDDHH.fLLL.grib2`) — no deviation found.
- `size_bytes` matches the live filesystem size for all 12, confirmed via
  `device_list_dir`.
- `sha256` matches an independently recomputed hash for all 6 files this
  phase could stage; for the other 6 it is recorded but not independently
  recomputed this phase (gap, not a failure — see Part C).
- No `requested_bbox` is recorded for 11 of the 12 (only the first,
  pre-existing 2020-07-15 pilot entries carry one) — not a defect, since
  the note field on every entry already says the download is a full
  global file regardless of any recorded bbox, and this phase did not
  invent or backfill a bbox value that was never actually requested.

**Not done**: the manifest was not rewritten to add SHA256 for entries
that already have it, nor to add anything else — it already has SHA256
for all 12 entries, so there is no gap to report here.

### A11. Storage report

| metric | value |
|---|---|
| total bytes (12 files) | 4,848,184,604 |
| total GB | 4.85 GB |
| min file size | 216,896,273 bytes (206.9 MB) — `gfs.0p25.2016011500.f003.grib2` |
| max file size | 551,714,308 bytes (526.2 MB) — `gfs.0p25.2022081512.f006.grib2` |
| median file size | 438,641,931.5 bytes (418.3 MB) |

Comparison to the Phase 0.4.13 estimate (2.5–4.0 GB, based on the 4
smaller pilot files): **the actual total, 4.85 GB, exceeds that estimate
by roughly 20–95%.** The 2021/2022/2023 files are substantially larger
(520–526 MB each) than either the 2015 pilot (~210–222 MB) or 2020 pilot
(~320–342 MB) files the estimate was built from — the archive's per-cycle
file size varies more than the earlier 4-file sample suggested. This is
reported as a warning, not hidden.

## B. Warnings (non-blocking)

1. The Phase 0.4.13 storage estimate undershot the real total by a
   meaningful margin (see A11). Future storage estimates for Stage C or
   beyond should budget using the full 12-file range (207–526 MB/file),
   not just the 4-pilot-file range.
2. `tp` and `prate` both carry one duplicate message per file in the
   2019-12-09T18Z cycle (and, confirmed by comparison, in the existing
   2020-07-15 pilot cycle too) — a pre-existing archive quirk, not
   something Stage B introduced, but worth the next phase's join-script
   author knowing `find_message()` should keep picking the first match
   deterministically rather than assuming exactly one.
3. 11 of the 12 Stage B manifest entries have `requested_bbox: null`
   (only the original 2020-07-15 entries have a recorded bbox) — harmless
   given the explanatory note on every entry, but a minor provenance
   inconsistency worth normalizing in a future phase if the manifest
   schema is ever revisited.

## C. Blockers

1. **6 of the 12 Stage B files (all >400 MB) could not be GRIB-inspected,
   SHA256-recomputed, or cycle/lead-recomputed from GRIB metadata in this
   session.** This session's device-bridge file-staging tool enforces a
   hard 400 MB per-file cap, and the device-side shell
   (`mcp__remote-devices__device_bash`) that could otherwise inspect them
   in place reported `"Workspace unavailable. The isolated Linux
   environment on this device failed to start."` on every attempt (3
   attempts, including after a 60-second timeout retry). The affected
   files:
   - `gfs.0p25.2021072406.f003.grib2` (520 MB)
   - `gfs.0p25.2021072406.f006.grib2` (525 MB)
   - `gfs.0p25.2022081512.f003.grib2` (522 MB)
   - `gfs.0p25.2022081512.f006.grib2` (526 MB)
   - `gfs.0p25.2023110612.f003.grib2` (518 MB)
   - `gfs.0p25.2023110612.f006.grib2` (518 MB)

   What **is** established for these 6, without GRIB access: they exist,
   their byte sizes exactly match `manifest.json`'s recorded
   `size_bytes` (a truncated or interrupted download would very likely
   not match), their manifest-recorded SHA256 values are each other
   pairwise distinct and distinct from the 6 inspected files' hashes, and
   the acquisition script that downloaded all 12 together used the exact
   same code path that produced verifiably-correct output for the 6
   inspected files (same `acquire_historical_gfs_pilot.py` run pattern,
   same manifest-writing logic). This is reasonable supporting evidence,
   not proof — it is not the same as this phase independently opening
   those 6 files and confirming their GRIB content.

   This blocker does not indicate anything is wrong with the 6 files — it
   means this session could not confirm they are right. The correct
   resolution is to re-run the GRIB-level checks (reusing
   `enumerate_messages`/`find_message`/`ist_slot_for`/
   `event_group_key_for` exactly as done here for the other 6) from an
   environment that can either load files over 400 MB or run a shell
   directly on the machine holding them — most simply, by running the
   equivalent of this phase's verification script directly on your own
   machine against the full `data/external/historical_gfs/raw/` directory.

## D. Things intentionally NOT done

- No GFS files were downloaded (all 12 were already present).
- No model was trained.
- No production code or production data was modified.
- No precipitation or prate feature was computed or written to any
  dataset file — Part A8/A9 is reporting only.
- No dataset build (the actual Stage B join/dataset construction) was
  started — that is explicitly the next phase's job.
- `data/external/historical_gfs/manifest.json` was read but not rewritten
  — it already carries SHA256 for all 12 entries, so no provenance
  "improvement" was needed or made.
- No commit, no push.
- The 6 oversized files' GRIB content, cycle/lead metadata, event-group
  key, and precipitation/prate semantics were not independently
  confirmed this phase (see Part C) — not claimed as verified anywhere
  in this document.

## Tests

New file: `tests/test_phase_0_4_14_stage_b_verification.py` — 7 focused
tests locking in this phase's event-group and year-split findings against
the real repository functions (`event_group_key_for`,
`assign_partition`), not re-implementations:

- `test_stage_b_event_group_keys_match_phase_0_4_13_manifest`
- `test_stage_b_has_exactly_six_distinct_event_group_keys`
- `test_stage_b_event_group_keys_do_not_collide_with_existing_pilots`
- `test_stage_b_f003_f006_pair_shares_one_key_per_target`
- `test_stage_b_all_six_targets_resolve_to_train_partition`
- `test_stage_b_targets_span_three_positive_three_negative`
- `test_stage_b_targets_span_six_distinct_years`

Results:

- New file alone: **7/7 passed**.
- `tests/test_vobl_historical_gfs_ts_join.py` +
  `tests/test_historical_dataset_split.py` +
  `tests/test_phase_0_4_14_stage_b_verification.py`: **66/66 passed**.
- Full suite (excluding the 4 pre-existing environment-only failures —
  `test_himawari.py`, `test_nomads.py`, `test_segments.py`,
  `test_segments_v2.py` — unchanged from every prior phase, caused by
  missing `donfig`/blocked egress in this sandbox, not by anything in this
  phase): **242/242 passed** (up from the Phase 0.4.12 baseline of 235;
  +7, exactly the new test file; nothing weakened or skipped).

## Final gate

```
STAGE_B_ACQUISITION_VERIFICATION = YELLOW
```

**Why YELLOW, not GREEN**: 6 of the 12 files have not had their GRIB
content, cycle/lead metadata, or event-group key independently confirmed
this phase — this is exactly the kind of gap the GREEN bar in this
phase's own instructions requires to be closed ("all cycle/lead metadata
is correct... computed from GRIB metadata, not from the filename" — true
for 6/12, not yet demonstrated for the other 6). Nothing examined is
*wrong*: all 12 files exist, all 12 sizes and manifest-recorded SHA256s
are internally consistent, the 6 inspected files are fully correct and
match the manifest exactly, and the 6 un-inspected files pass every check
available without GRIB access. This is a **verification coverage gap**,
not a detected defect — hence YELLOW rather than RED.

**Why not GREEN**: the brief's own GREEN bar explicitly requires "all
cycle/lead metadata is correct" and computed from GRIB, for all files —
not satisfiable for 6 of 12 without resolving the 400 MB staging /
device-shell blocker in Part C.

**Why not RED**: no file is confirmed corrupt, wrong, missing, or
mis-mapped. No label, event-group, or year-split check failed. The
uninspected 6 have strong circumstantial evidence of correctness (size
match, distinct hashes, same acquisition code path as the 6 confirmed
good) — RED would overstate the actual finding.

## Exact next action

1. Re-run GRIB-level verification on the 6 oversized files
   (`gfs.0p25.2021072406.f003/f006.grib2`,
   `gfs.0p25.2022081512.f003/f006.grib2`,
   `gfs.0p25.2023110612.f003/f006.grib2`) from an environment that is not
   limited by this session's 400 MB device-staging cap — most directly,
   by running eccodes-based inspection (reusing
   `enumerate_messages`/`find_message`/`ist_slot_for`/
   `event_group_key_for` from `scripts/build_vobl_historical_gfs_ts_join.py`,
   exactly as this phase did for the other 6) **directly on your own
   machine** against the full `data/external/historical_gfs/raw/`
   directory, since the files already live there and need no further
   transfer.
2. Once all 12 files have passed full GRIB-level verification, re-issue
   this phase's final gate as GREEN (or report whatever the remaining 6
   actually show, including if something is wrong — do not assume GREEN
   without re-running the check).
3. Only after a GREEN verification gate: proceed to the Stage B dataset
   build (the join step) under the Phase 0.4.12 contract, combining these
   12 new files with the existing 2015-03-03 and 2020-07-15 pilots — that
   is a separate phase, not started here.
