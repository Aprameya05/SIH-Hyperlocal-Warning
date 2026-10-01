# Phase 0.4.18 — Acquisition Path Validation + Manifest Cleanup

Status: AUDIT/DESIGN + ONE PROOF-OF-CONCEPT ATTEMPT ONLY. No bulk download,
no model training, no production change, no commit, no push this phase.

## Scope confirmation (read first)

This phase did exactly two things: (1) attempted to actually test the
THREDDS/OPeNDAP subsetting path identified but never verified in Phase
0.4.2/0.4.3, and (2) cleaned up the Phase 0.4.17 candidate manifest's two
documented issues. The 21-event/42-file Phase 0.4.17 batch was **not**
downloaded. No model was trained. No file under `backend/`,
`forecast_action.py`, `canonical_forecast_writer.py`, or `location_engine.py`
was touched. Nothing was committed or pushed.

## PART 1 — THREDDS / OPeNDAP subsetting test

### 1.1 Endpoints used (from existing documentation, nothing invented)

Per `docs/PHASE_0_4_2_HISTORICAL_GFS_FEASIBILITY.md` and
`docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md`, three mechanisms are
documented for NCAR GDEX dataset `d084001`:

1. Direct HTTPS full-file download: `https://data.gdex.ucar.edu/d084001/{YYYY}/{YYYYMMDD}/gfs.0p25.{YYYYMMDDHH}.f{FFF}.grib2`
2. THREDDS Data Server / OPeNDAP: `https://tds.gdex.ucar.edu/thredds/catalog/catalog_d084001.html`, with a per-file catalog pattern also referenced at `https://thredds.rda.ucar.edu/thredds/catalog/files/g/d084001/{YYYY}/{YYYYMMDD}/catalog.html?dataset=...`
3. A login-gated "Get a Subset" web form (free GDEX account)

Test case used (already an acquired, verified cycle from this project — no
new cycle picked): **2020-07-15 00Z, forecast hours f003/f006**, the same
cycle Phase 0.4.16's dataset already has a verified GRIB2 extraction for.

### 1.2 What was actually attempted

From this sandbox:

```
curl -sS -o /dev/null -w "HTTP:%{http_code}" --max-time 20 \
  https://tds.gdex.ucar.edu/thredds/catalog/catalog_d084001.html
curl ... https://thredds.rda.ucar.edu/thredds/catalog/files/g/d084001/2020/20200715/catalog.html
curl ... https://data.gdex.ucar.edu/d084001/2020/20200715/gfs.0p25.2020071500.f003.grib2
curl ... https://gdex.ucar.edu/datasets/d084001/dataaccess/
```

Result: all four requests failed identically with `curl: (56) CONNECT
tunnel failed, response 403` — the sandbox's own egress proxy rejected the
CONNECT to `gdex.ucar.edu`, `data.gdex.ucar.edu`, and `thredds.rda.ucar.edu`
before any HTTP request reached the actual archive server. This is the
same `connect_rejected` / organization-policy failure documented in Phase
0.4.2 for the same hosts — reconfirmed, not newly discovered.

From the user's own machine, the only path to "the user's actual
environment" available to this phase is the `device_bash` tool (a shell on
the linked Windows machine). It was attempted once this phase:

```
curl -sS -o /dev/null -w "HTTP:%{http_code}" --max-time 20 \
  https://tds.gdex.ucar.edu/thredds/catalog/catalog_d084001.html
```

Result: `Workspace unavailable. The isolated Linux environment on this
device failed to start.` — the same environment failure documented across
Phases 0.4.14/0.4.15/0.4.16. This is a local sandbox-startup failure on the
device bridge's Linux shell, not a network-policy rejection — it means the
test could not run there at all, which is a different failure mode from
"ran and was blocked."

**Neither available execution environment could actually reach the
archive or run a real HTTP/OPeNDAP request against it this phase.** No
subset was retrieved. No GRIB2/NetCDF bytes of any kind were fetched for
this test.

### 1.3 Measurements (Part "WHAT TO MEASURE")

Every row below is either a number actually measured, or explicitly marked
not obtained — none are invented:

| Item | Result |
|---|---|
| A. Original full GRIB2 file size | Not re-measured this phase; already on record from Phase 0.4.15/0.4.16 Stage B manifests: 216.9–551.7 MB per single-cycle/single-lead global file |
| B. Retrieved subset size | **Not obtained — no subset request reached the archive** |
| C. Variables successfully retrieved | **None — zero bytes retrieved from either endpoint this phase** |
| D. Spatial extent retrieved | **Not obtained** |
| E. Time/forecast lead retrieved | **Not obtained** |
| F. Reconciliation against existing GRIB extraction | **Not performed — there is no subset data to reconcile** |
| G. Time required | Each attempt failed in 0.2–0.3s (immediate proxy-level rejection on this sandbox); the device-bridge attempt failed immediately with an environment-startup error, not a timeout |
| H. Reproducibility | Not assessable — a method that never successfully executed once cannot be judged reproducible |

### 1.4 Classification

Per the brief's own three-way classification, this is explicitly **not**
forced to GREEN:

```
SUBSETTING_STATUS = SUBSETTING_ENVIRONMENT_BLOCKED
```

Both available execution environments (this sandbox, and the linked
Windows machine via `device_bash`) failed to even reach the archive —
one via an explicit network-policy rejection (403 at the proxy CONNECT
layer, reconfirmed from Phase 0.4.2), the other via a local shell-startup
failure unrelated to the archive itself. This is a genuine "cannot test
from here" result, not a disguised failure of the subsetting mechanism —
the mechanism itself (THREDDS/OPeNDAP on `tds.gdex.ucar.edu`) remains
exactly as undetermined as it was after Phase 0.4.2/0.4.3: documented as
existing by NCAR's own data-access page, never empirically exercised by
this project.

**The existing full-file HTTPS download strategy is preserved as the only
confirmed-working acquisition path** (it is how all 16 rows of the current
Phase 0.4.16 dataset were actually obtained, via the human-operated
`scripts/acquire_historical_gfs_pilot.py` path on a real network — not
from this sandbox). This phase makes no change to that.

### 1.5 What would resolve this

The project owner, running from a real network-connected machine (their
own PC, outside this sandbox and outside the device-bridge's currently
non-functional isolated shell), can run:

```
curl -sS -o /dev/null -w "%{http_code}\n" \
  "https://tds.gdex.ucar.edu/thredds/catalog/catalog_d084001.html"
```

and, if that succeeds, attempt an actual OPeNDAP constrained-variable
request against one file in the 2020-07-15 00Z cycle, compare the
retrieved values against the already-extracted GRIB values for the same
cycle in `data/processed/historical_gfs_ts/historical_gfs_ts_dataset.csv`,
and report the result using this phase's same measurement table. This
phase does not recommend investing more sandbox time on this test, since
two separate execution environments have now failed to reach these hosts
for unrelated reasons.

## PART 2 — Candidate manifest cleanup

### Issue A: January-1 TRAIN-negative clustering (fixed)

**Root cause**: `select_candidates()`'s per-year TRAIN-negative fill
picked the *earliest* `NEGATIVE_CONFIRMED` slot in each target year
(`year_neg.iloc[0]` after sorting by date). Because the label archive has
negative slots on essentially every day of the year, "earliest" always
resolved to January 1st, for every one of the four fill years
(2017/2018/2021/2023) — a selection-order artifact, not a deliberate
choice.

**Fix**: each fill year now has a target month
(`neg_target_month = {2017: 4, 2018: 7, 2021: 10, 2023: 11}`), chosen to
diversify season coverage (pre-monsoon / monsoon / post-monsoon /
post-monsoon) rather than cluster all four in winter. The search takes the
earliest negative on or after that month with a confirmed, non-ambiguous
GFS cycle/lead mapping (falling back to the full year if none exists after
the target month — not triggered for any of the four years here).

**Result, actually run**:

| Year | Old pick | New pick | New season |
|---|---|---|---|
| 2017 | 2017-01-01 (winter) | 2017-04-01 | pre-monsoon |
| 2018 | 2018-01-01 (winter) | 2018-07-17 | monsoon |
| 2021 | 2021-01-01 (winter) | 2021-10-01 | post-monsoon |
| 2023 | 2023-01-01 (winter) | 2023-11-01 | post-monsoon |

Zero January-1 dates remain in the TRAIN-negative batch. Year coverage,
partition assignment, and non-overlap with the 9 already-acquired event
groups are all preserved — this was a pure re-selection within the same
already-unused candidate pool, not a change to which years are covered.
No negative was fabricated; all four are real `NEGATIVE_CONFIRMED` rows
from the existing label archive with a verified cycle/lead mapping.

### Issue B: clustered winter positives (partially resolved, documented)

**Investigation**: the full label archive has exactly 7 winter (Dec/Jan/Feb)
`POSITIVE` slots across all 11 years for this cell/hazard. Of the 5 that
fall in TRAIN, 2 (2015-02-28, 2018-02-09) are already used as per-year
fills (Step 4/Part of 0.4.17's main loop). The remaining 3 are the cluster
flagged in the audit: `2021-02-19|2`, `2021-02-19|3`, `2021-02-20|2` — all
within approximately 30 hours of each other, plausibly one synoptic
system.

**Finding**: there is **no other TRAIN-partition winter positive anywhere
in the archive** to substitute in — the 2024-12-02/03 winter positives
exist but fall in HOLDOUT (2024-2025), not TRAIN, and using them would
violate the partition contract. The brief's instruction ("replace at most
one... with a temporally separated positive event... if doing so improves
event independence") cannot be satisfied by a like-for-like substitution,
because the substitute pool is empty.

**Resolution taken**: rather than force a same-cluster swap or leave all 3
in place, Phase 0.4.18 drops the cluster member closest to the already-
picked year-fill (`2021-02-19|3`, ~6 hours from `2021-02-19|2`) and keeps
only the most temporally separated member (`2021-02-20|2`, ~24 hours from
the year-fill). This reduces the extra-winter-positive count from 2 to 1
(documented in the script as `max_extra_winter = 1`), improving
independence without removing winter coverage entirely or touching
year/season coverage elsewhere. This is reported honestly as "reduced the
cluster from 3 to 2 members" — not as "fully resolved to independent
samples," since the two remaining winter-2021 dates are still only ~24h
apart and should be treated as correlated in any later statistical use
(Step 4/7's caution from Phase 0.4.17 still applies to the one remaining
pair, at lower degree than before).

## PART 3 — Strategy B numerical inconsistency (fixed)

**Root cause**: Phase 0.4.17's Step 4.B derived "~150 positive event
groups" as the EPV≥10 floor for 15 predictors, but Section 10's Strategy B
then stated "positive target: ~100–150 event groups" — citing a different,
lower number as if it were the same EPV≥10 floor, without explaining the
gap.

**Fix applied to `docs/PHASE_0_4_17_ACQUISITION_PLAN.md`** (the underlying
EPV≥10 statistical guideline itself was **not** changed): Section 4.B now
explicitly distinguishes two tiers —

- **Minimum-attempt tier (~100 positive event groups)**: fits a model at
  all, but EPV ≈ 6.7 at 15 predictors — **below** the named guideline,
  must be reported as a preliminary/exploratory fit only.
- **EPV≥10-defensible tier (~150 positive event groups)**: the actual
  floor the guideline requires for 15 predictors.

Strategy B's positive target is corrected to **~150 event groups** (the
actual EPV≥10 floor), with an explicit note that a partial acquisition
landing only at the ~100 minimum-attempt tier is sub-EPV and should not be
described as "meeting Strategy B." Both sections now also state plainly
that meeting EPV≥10 is a necessary sample-size condition only — it proves
nothing about calibration, feature quality, or 2024-2025 holdout
performance, which still require their own separate evaluation once the
data exists.

## PART 4 — Regenerated acquisition manifest

`docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv` and
`docs/PHASE_0_4_17_ACQUISITION_SUMMARY.json` were regenerated in place by
re-running `scripts/design_phase_0_4_17_acquisition_candidates.py` (now
carrying the Issue A/B fixes described above). All 13 original columns are
retained unchanged: `priority, target_ist_date, target_slot, label,
event_group_key, target_valid_time, selected_gfs_cycle, selected_lead,
reason, partition, season, expected_file, expected_role`.

New totals: **20 event groups, 40 files** (down from 21/42 — the net
effect of fixing Issue A, which is a pure re-selection with no count
change, and Issue B, which removed 1 event group). This stays within the
brief's requested ~20–30 event-group range; the batch was not expanded.

## PART 5 — Leakage / duplication audit (re-run)

All checks below were actually run against the regenerated manifest, not
assumed:

- **Overlap with the 9 already-acquired event groups**: 0 (verified by
  set intersection against `ALREADY_ACQUIRED_EVENT_GROUPS`)
- **Duplicate candidate event groups**: 0 (verified via
  `(event_group_key, selected_lead)` duplicate check — each event group
  appears exactly once per lead)
- **f003/f006 share the same event_group_key where appropriate**: verified
  for all 20 event groups — each has exactly one f003 row and one f006 row
- **Initialization precedes target valid time**: guaranteed structurally —
  `find_cycle_and_leads` only ever searches `day_offset` in `[0, -1, -2]`
  relative to the target date with `init <= target + 23h`, and only
  accepts a cycle if the computed valid time (via `ist_slot_for`) lands in
  the target slot; it cannot select an initialization after its own valid
  time
- **Target slot independently recomputed**: yes — `ist_slot_for()` is
  called on the actual computed UTC→IST valid time for each lead, not
  assumed from the label's stored slot
- **No candidate crosses TRAIN/HOLDOUT**: verified — `assign_partition()`
  is called once per event group and is consistent for both its f003 and
  f006 rows (same date, same partition by construction)
- **No label used as a predictor**: unchanged from Phase 0.4.17 — the
  manifest records label status only for acquisition-prioritization and
  TRAIN/HOLDOUT bookkeeping; it is not written into any feature column of
  the actual Phase 0.4.16-style extraction pipeline
- **No candidate requires fabricated data**: all 20 event groups and their
  GFS cycle/lead mappings are derived directly from the real
  `ts_labels.csv` archive and the real `ist_slot_for()`/`assign_partition()`
  functions already used in production research tooling — nothing was
  invented
- **Ambiguous cycle mappings**: 0 of 20

**Breakdown** (event-group level, from the regenerated manifest):

- Positive event groups: **13**
- Negative event groups: **7**
- TRAIN: **14**
- HOLDOUT: **6**
- Year distribution: 2015:1, 2016:1, 2017:2, 2018:2, 2019:1, 2020:1,
  2021:3, 2022:1, 2023:2, 2024:6
- Season distribution: winter:7, pre-monsoon:6, monsoon:4, post-monsoon:3
- Month distribution: Jan:3 (all HOLDOUT 2024 negatives, not TRAIN —
  see note below), Feb:4, Mar:4, Apr:1, May:1, Jun:3, Jul:1, Oct:2, Nov:1
- Slot distribution: slot0:2, slot1:2, slot2:12, slot3:4

Note on the 3 remaining January dates: these are the HOLDOUT-reserve
negatives (`2024-01-01` slots 0/1/2, plus one `2023-12-31` slot that maps
to the same local cycle), which were never part of Issue A (Issue A only
concerned the TRAIN-partition January-1 clustering) and are left
unchanged — HOLDOUT reserve dates were not in scope for the Issue A fix,
and clustering concerns there are lower-stakes since these rows are never
used for training in the first place.

## Overclaim guardrails (unchanged from Phase 0.4.17, restated)

This phase's manifest cleanup and the one subsetting test attempt do not
change what the existing 16-row dataset proves. It still proves the
extraction pipeline only — not predictive skill, generalization,
operational accuracy, pan-India skill, or production readiness. Nothing
in this phase trains a model or evaluates one.

## Tests

New tests this phase: `tests/test_phase_0_4_18_acquisition_validation.py`
(9 tests, covering duplicate-free manifest, no overlap with acquired
groups, the January-1 artifact removal, the winter-cluster reduction,
partition correctness, non-ambiguous cycle mapping, the Strategy B
internal-consistency fix, batch-size range, and the production-import
isolation check).

Results actually run this phase, in this sandbox:

- New Phase 0.4.17 + 0.4.18 design tests: **19/19 passed**
  (`test_phase_0_4_17_acquisition_design.py` + `test_phase_0_4_18_acquisition_validation.py`)
- Historical-GFS-related test selection (`-k "historical or vobl or gfs_ts or phase_0_4_1"`): **129/129 passed**
- Full test suite (`pytest tests/`): **217/217 passed, 0 failed, 0 skipped, 0 errors**

No existing test was weakened, removed, or had its assertions loosened.
The full-suite count (217) is reported exactly as observed in this
sandbox this run; it is not claimed to match any previously reported
full-suite count from the user's own machine, since this sandbox and the
user's machine are two different environments with their own history of
environment-specific skips (e.g. the long-standing `device_bash`
unavailability documented above) that can make raw totals diverge between
them without any test content having changed.

## Final status

```
SUBSETTING_STATUS = SUBSETTING_ENVIRONMENT_BLOCKED
PHASE_0_4_18 = YELLOW
```

Reasoning: the manifest cleanup (Part 2) and the Strategy B consistency
fix (Part 3) are both fully resolved and verified — those alone would
support GREEN. But the phase's primary open question from 0.4.17 — whether
THREDDS/OPeNDAP subsetting actually works for this archive — remains
exactly as unresolved as before, because neither available execution
environment (this sandbox, or the linked machine's currently-broken
`device_bash` shell) could reach the archive this phase. This is not a
new problem introduced this phase, and it is not RED (nothing was found
to be scientifically wrong with the design) — it stays YELLOW, carried
over from Phase 0.4.17 with a now fully-documented, twice-attempted
reason, and a concrete unblocking step left for the project owner's own
real network.

## Scope confirmation

No production code, schema, model, or data file was modified. No bulk
GFS acquisition occurred — the only network activity this phase was four
small `curl` HEAD/metadata-style requests to confirm reachability (all
failed before reaching the archive) and one failed `device_bash` attempt.
No model was trained. Nothing was committed, pushed, or deployed.
