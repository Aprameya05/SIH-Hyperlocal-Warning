# Phase 0.4.7 — Hazard Target-Resolution Gate

Research/audit phase. No model trained, no large archive downloaded, no
negatives fabricated, no daily label converted to hourly, no event timing
inferred beyond what the source actually states, no production code
touched, nothing deployed/committed/pushed.

## PART 1 — Existing IMD rainfall temporal resolution

Files actually present: `imd_rain/rain/{2015..2025}.grd` (repository root
path — note the brief's stated path `data/imd_rain/rain/` does not match;
the real path has no `data/` prefix, confirmed by directory listing this
phase).

**Determined from actual byte-count math, not filenames**: each file's
size equals exactly `n_days × 129 × 135 × 4 bytes` (float32), e.g.
2015.grd = 365 × 129 × 135 × 4 = 25,425,900 bytes (matches exactly) and
2016.grd = 366 × 129 × 135 × 4 = 25,495,560 bytes (matches exactly,
correctly accounting for the leap day). **This structurally proves exactly
one rainfall value per grid cell per calendar day** — there is no hidden
sub-daily dimension (no ×4 or ×24 multiplier anywhere in the byte count).
This is a hard structural fact about the file layout, not an assumption.

**Content re-verified this phase** (not just shape): loaded 2015.grd,
read day index 200, found 4,964 of 17,415 grid cells with real (non-missing)
values, ranging 0.0–283.3mm with a 10.85mm mean — physically plausible
daily monsoon-season rainfall, not garbage or placeholder data. Missing
value sentinel confirmed as `-999.0`.

- **Temporal resolution**: daily (one 24-hour accumulation per grid cell
  per calendar date).
- **Spatial resolution**: 0.25° (129 × 135 grid over `6.5–38.5°N,
  66.5–100.0°E`, per the existing, already-validated grid parameters in
  `scripts/build_panindia_cb_labels.py`).
- **Date coverage**: 2015–2025 (one file per year, 11 files present).
- **Units**: mm (rainfall depth), consistent with the plausible value range
  found.
- **Accumulation semantics**: a single daily total per cell; no start/end
  timestamp is encoded in the binary file itself (IMD's standard gridded
  daily product is conventionally a 24h accumulation ending 03:00 UTC /
  08:30 IST — this is general knowledge about the IMD product family, not
  something this binary file's bytes themselves state, and this report
  does not claim to have verified that specific cutoff time from the file
  content).

**Answer: NO, the existing repository rainfall data cannot support an
hour-specific cloudburst target.** Precise reason: the file format itself
has no sub-daily axis — there is exactly one number per cell per day, with
no encoded information about when within that day the rainfall occurred.
This is not a processing limitation that could be fixed by reading the file
differently; the information needed (when within the day the rain fell)
was never recorded in this product at all. No interpolation or temporal
splitting was performed or is proposed to work around this, per the brief.

## PART 2 — Cloudburst target semantics

Traced `scripts/build_panindia_cb_labels.py` (re-read this phase): the
positive threshold (`CB_THRESHOLD_MM = 64.5`) is applied to the **daily**
IMD gridded total. This represents an **extreme-rainfall proxy for "a
cloudburst-magnitude rainfall total occurred somewhere in this cell on this
date"** — it is not a true cloudburst *event* observation (no report of an
actual sudden-onset, short-duration rainfall event; IMD's own operational
cloudburst definition is typically stated as "≥100mm in 1 hour," which this
daily-threshold proxy at 64.5mm/day cannot represent at all, regardless of
which exact mm value is used) — a genuinely different phenomenon
specification than the file's own `CB_THRESHOLD_MM` name suggests.

**Can the label legitimately align to +2h/+3h/+4h/+5h/+6h?** No. Since the
source has no sub-daily information (Part 1), every hour of a given
calendar date shares the identical label value. Attempting to "align" it to
a specific hour would not be wrong in the sense of picking the wrong
number — it would be asserting a false precision the data does not contain.

**Classification: DAILY_EVENT_TARGET**, explicitly not
`HOURLY_FORECAST_TARGET`.

**Exact additional data required to upgrade**: a gridded or
station-network rainfall product with genuine sub-daily (ideally hourly or
3-hourly) temporal resolution, covering the same pan-India extent and a
usable historical period overlapping the GFS archive (2015+). This report
does not identify a specific currently-accessible candidate for this (Phase
0.2's broader external-data research did not surface one with confirmed
access either) — stated as a real, unresolved gap, not glossed over with a
generic "get better data" recommendation.

## PART 3 — Official lightning / convective observation access

**First, a correction to the brief's premise, stated plainly rather than
silently accepted**: this phase searched the entire repository
(`grep` across every `.py` and `.md` file) for any prior reference to an
"NRSC/Bhuvan Lightning ECV" source specifically, and found **none**. The
only lightning-related research actually on file is in
`docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md` Section C3: the **Indian
Lightning Location Network (ILLN) / IITM-LLN**, which that document itself
flags as "insufficient confirmed access evidence" — not NRSC/Bhuvan, and
not a prior finding of actual availability. Bhuvan/NRSC is documented
elsewhere in this repository (`docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md`
Section E1, `docs/PHASE_0_3_CARTODEM_VALIDATION.md`) exclusively in
connection with **CartoDEM** (a terrain/elevation product), not lightning,
and that access attempt was **BLOCKED** at the network level (HTTP 403 /
connection rejected from this sandbox) in Phase 0.3. This report does not
invent an NRSC/Bhuvan lightning source to match the brief's framing; it
reports what is actually in the repository.

**Investigation performed, as instructed, into whether the project
currently has actual usable lightning data**:
- Repository files: `grep -rl "lightning"` across the whole tree finds only
  documentation/discussion of the topic (prior research reports, this
  report), zero data files.
- Downloaded artifacts: none found — no lightning-named file exists under
  `data/`, `processed/`, or anywhere else searched.
- Credentials/configuration: searched for `.env`/`credential`/config files;
  found only `models/ensemble_config.json` (a model-ensemble weighting
  file, unrelated to any external data source credential). No lightning
  API key, token, or registration record exists anywhere in this
  repository.
- Existing fetch scripts: none found for lightning (searched for any
  `fetch_*lightning*` or similarly named script — none exists).
- Existing data directories: none found.

**Result: `ACCESS_NOT_VERIFIED`.** No actual lightning data, credential, or
working fetch mechanism exists in this repository today, for any source
(ILLN/IITM, NRSC/Bhuvan, or otherwise). The user's registration status with
any external lightning data provider (mentioned in the brief) is
explicitly **not** treated as data access, per instruction — this report
makes no claim about what a pending or activated account might eventually
provide, only about what exists in hand right now, which is nothing.

**No public, credential-free lightning download/API endpoint was found or
verified this phase** — this phase did not attempt a new network fetch of
any lightning source (consistent with "do not retry previously failed
IMDAA/MOSDAC acquisition," and more broadly, no new acquisition attempt of
any kind was made for lightning, since none was previously attempted or
documented as blocked specifically for lightning — there was simply nothing
to retry).

**Would lightning observations actually improve the TS target's temporal
resolution, if access existed?** In principle, yes — lightning strike data
is typically timestamped to the second/minute, which would be a genuine
upgrade over METAR's periodic (roughly hourly) observation cadence for
sub-slot TS onset-timing precision. But this is a hypothetical answer about
a data source this project does not currently have access to verify or
use — it does not change the current `ACCESS_NOT_VERIFIED` status, and this
report does not recommend acquiring it as a near-term step given that
status (Part 8/9).

## PART 4 — INDOFLOODS event-time information

Direct column inspection this phase:

- `data/floodevents_indofloods.csv` columns: `EventID, Start Date, End Date,
  Peak Flood Level (m), Peak FL Date, Num Peak FL, Peak Discharge Q (cumec),
  Peak Discharge Date, Flood Volume (cumec), Event Duration (days),
  Time to Peak (days), Recession Time (day), Flood Type`.
- `data/metadata_indofloods.csv` columns: `GaugeID, Warning Level,
  Danger Level, Station, Latitude, Longitude, River Name/Tributory/
  SubTributory, Basin, State, Start_date, End_date, Level_Entries,
  Streamflow_Entries, Privacy, Source Catchment Area, Catchment Area,
  Area variation (%), Reliability`.
- `processed/indofloods/indofloods_grid_events.csv` columns: `EventID,
  GaugeID, cell_id, cell_lat, cell_lon, mapping_status, Start Date,
  End Date, Flood Type` (a subset/join of the above, no new temporal
  fields added).

**Exact temporal information available, and what is NOT available**:
- `Start Date` / `End Date`: **calendar dates only** (e.g. `2010-07-21`),
  no time-of-day component in any value inspected.
- `Peak FL Date` / `Peak Discharge Date`: also **calendar dates only** —
  these identify which *day* the peak flood level / discharge occurred,
  not an hour or minute.
- `Event Duration (days)`, `Time to Peak (days)`, `Recession Time (day)`:
  **day-granularity durations**, not hour counts — e.g. "Time to Peak (days)"
  tells you the event took N whole days to peak, not which hour.
- `Warning Level` / `Danger Level` (in `metadata_indofloods.csv`): these
  are **fixed station threshold values in meters** (gauge-specific flood
  stage thresholds), **not timestamps** — there is no "warning-level
  crossing time" or "danger-level crossing time" column anywhere in either
  file. The brief's Part 4 asks to specifically check for these; they do
  not exist in the public INDOFLOODS schema as distributed in this
  repository.
- **No exact timestamp (hour/minute) field exists anywhere** in any of the
  four files inspected.

**What INDOFLOODS can support**:
- **A. Daily FF target — YES.** `Start Date`/`End Date` directly support a
  daily positive-label assignment, exactly as Phase 0.4.6 already
  established.
- **B. Multi-hour FF target — NO**, in the sense of a specific multi-hour
  window within a day; what IS available is a **multi-day** window
  (`Start Date` to `End Date`, which can span multiple days for longer
  events), not a multi-hour one. This report does not blur "multi-day" into
  "multi-hour" — they are different granularities and the source only
  supports the former.
- **C. Exact-hour FF target — NO.** No field in any inspected file encodes
  an hour or finer time unit for any event milestone (start, end, peak).

## PART 5 — INDOFLOODS positive-unlabeled structure (quantified from real artifacts)

Directly computed from `processed/indofloods/indofloods_grid_events.csv`
this phase (4,548 total rows, all with `mapping_status == MAPPED`, 0
`GAUGE_NOT_IN_METADATA` rows — every event in the public release was
successfully assigned to a gauge with known coordinates):

- **Unique canonical cells with observed events**: **75**.
- **Unique cell/date positives** (`cell_id` + `Start Date` combinations):
  **4,106**.
- **Total event count**: **4,548**.
- **Multi-gauge/multi-event collisions** (cell/date combinations with more
  than one event mapping to the same cell on the same start date):
  **357** (e.g. cell `9.0_76.0` on `2019-08-08` has 6 separate gauge events
  mapping to it — a real, multi-gauge cell with several nearby gauges
  reporting on the same flood day).
- **Confirmed negatives**: **0** — none exist, and none were fabricated.
  Every cell/date not in the 4,106 positive set is, correctly, `UNKNOWN`
  under the Phase 0.4.6 alignment contract, never converted to `0`.
- **Unlabeled/background rows**: not separately materialized as rows in
  this file (per Phase 0.4.6's finding — `UNKNOWN` for the other ~190,000+
  theoretically possible cell/date combinations across 75+ cells and
  decades of dates is implicit, not stored as explicit rows, which is the
  correct behavior — materializing millions of `UNKNOWN` rows would add no
  information).

**Is a historical GFS + INDOFLOODS PU experiment technically possible?**
- **Daily target**: **Yes, technically possible** — real positives
  (4,106 unique cell/date combinations), real spatial mapping (75 cells),
  and the historical GFS archive's daily-aggregatable predictors (Phase
  0.4.5) could in principle be joined for these 75 cells on their positive
  dates (plus PU-framed unlabeled background). This report does not build
  this (out of scope — audit only) but confirms the technical precondition
  (real, non-fabricated positives with real spatial/temporal keys) is met.
- **Event-window target** (multi-day window matching `Start Date`–`End Date`):
  **Yes, technically possible** for the same reason, at the coarser
  day-range granularity the source actually supports.
- **Exact-hour target**: **Not possible** — no hour-level information
  exists in the source to construct such a target from (Part 4).

## PART 6 — Thunderstorm target quality

Traced `processed/labels/ts_labels.csv` directly this phase (re-querying
the real file, not re-citing Phase 0.4.6's numbers unverified):

- **Temporal resolution**: 6-hour IST slot (dense — every slot from
  2015-01-02 through 2025-12-31 has a real row for VOBL's cell; confirmed
  15,276 real per-slot rows for VOBL across that span, with the other 991
  cells represented by a single `ALL`-timestamp `UNKNOWN` summary row each,
  991 such rows, totaling the file's 16,267 rows exactly).
- **Positive definition**: `ts_label==1` in the source training CSV
  (station/METAR-class TS observation in that slot).
- **Negative definition**: `ts_label==0`, i.e. a closed slot window with a
  real observation showing no TS — genuinely confirmed, not inferred from
  absence.
- **Unknown handling**: applied unconditionally to all 991 non-VOBL cells
  for all time; VOBL's own cell has **zero** `UNKNOWN` rows in the
  2015-01-02–2025-12-31 span (every slot in that range is either `POSITIVE`
  or `NEGATIVE_CONFIRMED` — a fully dense label).
- **Forecast alignment**: demonstrated concretely in Phase 0.4.6 (the
  2020-07-15 slot-1 join against the real historical GFS pilot's f003/f006
  valid times) and re-confirmed structurally sound this phase — nothing
  new found that would invalidate that alignment.
- **Usable date range**: 2015-01-02 to 2025-12-31 (confirmed this phase,
  correcting the ambiguous "ALL"-polluted min/max read from the raw file
  without first excluding summary rows).
- **Number of positive examples**: **584**.
- **Number of confirmed negative examples**: **14,692**.

**Is the existing VOBL target sufficient for a SMALL first historical GFS
experiment?** **Yes.** The label is dense (no gaps across an 11-year span),
genuinely binary-supervised (both classes are real observations, not
proxies or inferred), has a demonstrated real alignment mechanism to
historical GFS valid times, and has enough positive examples (584) to be
statistically non-trivial for a small pilot-scale experiment (not a
claim about enough examples for a production-grade model — only that a
small, honest first experiment is scientifically legitimate to attempt,
which is the question this phase was asked to answer; no training was
performed to validate that further).

## PART 7 — Target-resolution matrix

See the companion document, `docs/PHASE_0_4_7_TARGET_RESOLUTION_MATRIX.md`,
created this phase with the full table (Hazard / Current target / Temporal
resolution / Spatial coverage / True negatives? / Supports +2h to +6h? /
Training mode / Status), using only the five allowed status values.

## PART 8 — Minimum data needed for each upgrade

- **TS**: **Nothing is strictly needed beyond VOBL for the first
  experiment.** The existing VOBL target (Part 6) is dense, real, and
  already has a demonstrated alignment mechanism — a first historical-GFS
  TS experiment can proceed with zero new data acquisition. (A second TS
  station would be needed to extend coverage beyond VOBL's single cell, but
  that is an *expansion*, not a precondition for a first, honestly-scoped
  single-cell experiment.)
- **CB**: minimum upgrade path is a **sub-daily (ideally hourly or
  3-hourly) gridded or station-network rainfall product** for the same
  pan-India extent and 2015+ period — no such product is currently in this
  repository or confirmed accessible (Part 2). This is the smallest
  concrete artifact that would materially change CB's status from
  `DAILY_ONLY`; nothing smaller would close this specific gap, since the
  limitation is structural (the existing source has zero sub-daily
  information to extract any other way).
- **FF**: minimum upgrade path from event-date PU toward a genuine
  multi-hour target is **continuous or high-frequency stream-gauge
  discharge/stage time series** for INDOFLOODS' own 214 gauges (or a
  meaningful subset of the 75 cells with existing event coverage) — this
  would both (a) supply real sub-daily timestamps for already-known events
  and (b) be the only way to ever construct genuine `NEGATIVE_CONFIRMED`
  FF labels (continuous monitoring showing non-flood flow at a specific
  time). No smaller artifact would achieve either of those two things —
  more event records alone (without timestamps or continuous monitoring)
  would not change FF's `PU_ONLY` status or its daily granularity.

No large/generic dataset is recommended for any hazard — each
recommendation above names the smallest artifact that would close that
hazard's specific, identified gap.

## PART 9 — Decision gate

### A. WHAT IS READY FOR A REAL MODEL EXPERIMENT
Thunderstorm, VOBL's single canonical cell only. Dense, genuinely-binary,
11-year label history; real historical-GFS-to-label alignment already
demonstrated (Phase 0.4.6); 584 real positive examples. A small, honestly
single-cell-scoped historical experiment is scientifically legitimate to
attempt (not yet attempted — no training performed this phase).

### B. WHAT IS ONLY READY FOR PU LEARNING
Flash flood, observed (INDOFLOODS event-based): 4,106 real unique
cell/date positives across 75 canonical cells, zero confirmed negatives,
none to be fabricated. Daily (or multi-day event-window) granularity only
— not an hour-specific target.

### C. WHAT IS DAILY-RESOLUTION ONLY
Cloudburst (IMD rainfall threshold) and the flash-flood rainfall proxy —
both pan-India-coverage, both genuinely supervised (real positive AND
inferable-negative structure for CB; proxy-only for FF-B), both
structurally incapable of distinguishing any hour of a calendar day from
any other, because their single source (`imd_rain/rain/*.grd`) has no
sub-daily information at all.

### D. WHAT IS BLOCKED
Nothing in this phase's scope is flatly `BLOCKED` in the sense of "no path
forward exists at all" — every hazard has at least a daily or PU path
available today. The one item classified outside the hazard-readiness
spectrum entirely is **lightning-based convective observation**, which is
**`ACCESS_NOT_VERIFIED`**: no data, credential, or working fetch mechanism
exists in this repository for any lightning source (NRSC/Bhuvan or
ILLN/IITM), and this phase did not and should not attempt a new acquisition
of it.

### E. MINIMUM NEXT DATA ACQUISITION
If the project wants to upgrade beyond today's state, in order of smallest
concrete artifact to largest: (1) none needed for a first TS experiment;
(2) a sub-daily pan-India rainfall product, for CB; (3) continuous
stream-gauge time series for INDOFLOODS' gauges, for FF. None of these
three is recommended as mandatory before the next engineering step below —
(1) requires nothing new at all.

### F. RECOMMENDED NEXT ENGINEERING STEP

**Build the small, VOBL-only historical GFS → TS supervised join
(previously identified in Phase 0.4.6 Part 12.E as the most tractable next
step, and re-confirmed here as the one hazard whose target genuinely
supports it without any new data acquisition): join the existing historical
GFS feature reconstruction for VOBL's canonical cell (`IND_13.0_78.0` /
`IND_13.0_77.0`, Phase 0.4.5's tiny pilot already includes a Bengaluru-area
cell) against the real, dense `processed/labels/ts_labels.csv` rows for
that cell, using the UTC→IST→slot join procedure formalized in
`docs/PHASE_0_4_6_HISTORICAL_LABEL_ALIGNMENT_CONTRACT.md`, for whatever
additional historical GFS cycles are acquired next.** This is one concrete
action — not a list — and it requires no new external data acquisition,
no interpolation, no fabricated labels, and stays within a single
hazard/cell scope where every semantic question this and the prior two
phases raised has already been answered honestly in the affirmative. It
was not performed this phase (research/audit scope only; no training
occurred).

**A loose end surfaced this phase, worth noting for whoever performs that
next step**: `scripts/map_indofloods_to_grid.py` constructs its own
`cell_id_str(lat, lon) = f"{lat:.1f}_{lon:.1f}"` — **without** the `IND_`
prefix that `regrid.py::cell_id_for()` (the convention the historical-GFS
pilot, Phase 0.4.3 onward, and `scripts/build_panindia_ts_labels.py`/
`build_panindia_cb_labels.py` all use) produces. This is a real,
previously-unflagged schema mismatch between the INDOFLOODS grid-mapping
output and the rest of the project's canonical cell-ID convention — it
does not affect the TS experiment recommended above (TS labels already use
the `IND_`-prefixed convention correctly), but it would silently break any
future direct join between historical-GFS features and
`processed/indofloods/indofloods_grid_events.csv` unless reconciled first.
Not fixed this phase (out of scope — no code was modified).

## Testing

- `tests/test_panindia_labels.py` — re-run this phase: **11/11 pass**
  (unchanged).
- Full existing suite: `pytest . --ignore=test_himawari.py
  --ignore=test_nomads.py --ignore=test_segments.py --ignore=test_segments_v2.py`
  → **176 passed**, identical to the Phase 0.4.5/0.4.6 baseline, same 4
  pre-existing environment-only collection errors (donfig ×3, NOMADS 403
  ×1). No test weakened. No new test added this phase (read-only audit, no
  new code written).

## Scope confirmation

- **Files created this phase**: this report and
  `docs/PHASE_0_4_7_TARGET_RESOLUTION_MATRIX.md`.
- **Files modified**: none.
- **Files read this phase**: `imd_rain/rain/{2015,2016}.grd` (binary,
  shape + sampled content only), `scripts/build_panindia_cb_labels.py`,
  `data/floodevents_indofloods.csv`, `data/metadata_indofloods.csv`,
  `processed/indofloods/indofloods_grid_events.csv`,
  `processed/indofloods/indofloods_grid_mapping.csv`,
  `processed/labels/ts_labels.csv`, `scripts/map_indofloods_to_grid.py`,
  `docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md`,
  `docs/PHASE_0_3_CARTODEM_VALIDATION.md`, and a repository-wide grep for
  lightning/NRSC/Bhuvan/MOSDAC/credential references.
- No new network acquisition was attempted for any source (lightning,
  IMDAA, MOSDAC, or otherwise). No negatives were fabricated. No daily
  label was converted to hourly. No event timing was inferred beyond what
  the source files actually contain. No production code, schema, canonical
  grid, workflow, or frontend file was changed. No model was trained.
  Nothing was deployed, committed, or pushed.
