# Phase 0.4.6 — Historical GFS → Observed Hazard Label Alignment Audit

Audit only. No model trained, no large archive downloaded, no production
code touched, nothing deployed/committed/pushed. This report traces actual
repository code and data files (not README claims) to determine whether
historical GFS predictors (Phase 0.4.5) can be honestly joined to observed
TS/CB/FF labels.

## 1. Label-source inventory

| | A. Thunderstorm (TS) | B. Cloudburst (CB) | C. Flash flood (FF) |
|---|---|---|---|
| Physical phenomenon | Convective thunderstorm occurrence at a point | Extreme short-duration rainfall at a point/cell | Flood event triggered by heavy rainfall over a catchment |
| Observation/source | (1) Historical: IMD station table, VOBL/43295, baked into `data/bengaluru_6hr_training_dataset_v4.csv`'s `ts_label` column. (2) Going forward, operational: live METAR via `fetch_metar.py`/`metar_ground_truth.py`, aviationweather.gov, no auth, already wired into `forecast_update.yml` | IMD 0.25° gridded **daily** rainfall, `imd_rain/rain/{2015-2025}.grd` (real binary files, size-verified against `129×135×days×4 bytes` including leap years, per `docs/LABEL_ENGINE.md` and re-confirmed this phase) | INDOFLOODS (Kuntla & Saharia, BAMS 2025) gauge-level flood events, `data/floodevents_indofloods.csv` + `data/metadata_indofloods.csv` (gauge lat/lon) |
| Spatial resolution | Point (one station) | 0.25° grid, max-aggregated onto the 1.0° canonical grid | Point gauges (214 in the public release), event assigned to whichever canonical cell contains the gauge |
| Temporal resolution | Sub-slot (individual METAR obs, aggregated to 6h IST slots) for the live path; the historical `ts_label` column's own underlying resolution was not re-verified this phase (it predates this project's current pipeline and was inherited as-is) | **Daily** — no finer resolution exists in the source | **Daily** (event Start/End Date) |
| Coverage period | Historical CSV: 2015-01-02 onward (confirmed this phase: `processed/labels/ts_labels.csv` starts 2015-01-02). Live METAR: ongoing from whenever `forecast_update.yml` began running | 2015-2025 (file-name range; confirmed this phase: `processed/labels/cb_labels.csv` spans 2015-01-01 to 2025-12-05) | 1959-2020, but (per Phase 0.4.1) the public release (4,548 events) likely excludes some pre-restriction transboundary-basin events present in the full 8,342-event corpus |
| Coverage geography | VOBL only (confirmed: no second TS station or pan-India TS network found anywhere in the repo, per `docs/LABEL_ENGINE.md`'s explicit grep and re-confirmed this phase — no new search invalidated that finding) | Pan-India (wherever IMD's gridded product has data, clipped to the canonical grid's 6-37°N/68-98°E bounds) | 214 gauges, pan-India but sparse and uneven (concentrated wherever INDOFLOODS' source monitoring network existed) |
| Label timestamp semantics | IST slot string, e.g. `"2020-07-15T0601-1200"` | IST slot string, different format, e.g. `"2020-07-15T slot1"`, same daily value repeated across all 4 slots of that date | Event start/end dates (daily), not tied to any IST slot concept |
| Positive definition | Station/METAR reports TS in-window | Daily max rainfall ≥ 64.5mm at that cell (the ACTUAL executed threshold — see Part 2) | A. Gauge event window overlaps the date, spatially assigned. B. 3-day cumulative ≥100mm AND daily ≥40mm (proxy) |
| Negative definition | Window closed, ≥1 real observation, none show TS — genuinely confirmed | **Implicit only**: absence of a row in the sparse `cb_labels.csv` (verified this phase: the file stores ONLY positive rows — 452/452 real rows for `IND_13.0_77.0` are all `label=1`) | **None exists or should be manufactured** for (A); see Part 2 |
| Unknown/missing definition | Window not yet closed, or no observation exists; always applies to all 991 non-VOBL cells | Outside IMD's 2015-2025 coverage or outside its grid domain | Any cell/date without a gauge-assignable event (the overwhelming majority) |
| Negatives genuinely observed or merely unlabeled? | **Genuinely observed** (continuous station/METAR monitoring) | **Genuinely inferable** from continuous daily rainfall monitoring, though not materialized as explicit rows in the current file (a real, documented implementation detail, not a semantic gap) | **Merely unlabeled** — INDOFLOODS gauge monitoring is intermittent/event-triggered (Phase 5.5's finding, re-confirmed this phase: no change to that conclusion was found) |
| Operationally available historically? | Yes, 2015-onward (historical CSV); live METAR is forward-only | Yes, 2015-2025 | Yes, 1959-2020 (with the caveat above) |
| Event-based or continuous? | Continuous (station always reporting) | Continuous (daily gridded product) | Event-based (INDOFLOODS A); continuous-but-proxy (rainfall B) |

## 2. Exact label definitions, traced from actual code

**TS** (`scripts/build_panindia_ts_labels.py`, reads
`data/bengaluru_6hr_training_dataset_v4.csv`'s `ts_label` column directly —
no threshold computation, the label is pre-existing in that CSV): event
definition = whatever produced `ts_label` upstream (a pre-existing station
observation column, not recomputed here); observation source = IMD station
at VOBL/43295; timestamp = the CSV's `date`+`slot` columns, converted to an
IST slot string; forecast target window = the 6-hour IST slot; positive
threshold = `ts_label==1`; negative = `ts_label==0`; unknown handling =
every cell except VOBL's canonical cell gets `UNKNOWN` unconditionally
(verified directly in the script: the loop only ever assigns
`vobl_cell_id`, never any other cell).

Separately, the **live/operational** TS ground truth (`metar_ground_truth.py`,
traced this phase) aggregates every METAR observation inside a slot's IST
window and only finalizes `NO_TS_OBSERVED` once the window has closed —
never scoring an in-progress window as a negative. This is a different code
path from the historical CSV's `ts_label` column and was built later
(dated 2026-09-30 in its own docstring) specifically to fix a point-in-time-
snapshot bug (`fetch_metar.py`'s old `inject_ground_truth_label()`) — i.e.
the repository's TS labeling methodology itself improved over time, and the
historical `ts_label` column should not be assumed to have the newer path's
same slot-aggregation rigor (not independently re-verified this phase, since
that would require re-deriving the historical CSV from its own raw source,
out of scope for an audit).

**CB** (`scripts/build_panindia_cb_labels.py`): rainfall threshold =
**64.5mm/day** — re-verified this phase by reading the constant in the
script (`CB_THRESHOLD_MM = 64.5`), confirming `docs/LABEL_ENGINE.md`'s own
correction (the module's docstring elsewhere says "100 mm/day," which is
NOT what the code executes) is still accurate and has not regressed;
accumulation period = 1 calendar day (source has no finer resolution);
spatial aggregation = max over all IMD 0.25° fine cells whose center falls
in the canonical cell's ±0.5° box; timestamp = IST slot string, same daily
value applied to all 4 slots (`temporal_resolution="daily"` recorded
explicitly in each row); positive = daily max ≥ 64.5mm; negative = implicit
(absent row, Part 1); unknown = outside IMD's 2015-2025 coverage.

**FF — A. Observed INDOFLOODS event label**
(`scripts/map_indofloods_to_grid.py`, Phase 5 — re-traced this phase):
reads `data/metadata_indofloods.csv` for gauge lat/lon (214 gauges) and
`data/floodevents_indofloods.csv` for events (4,548 rows in the public
release), performs a real point-in-cell spatial join (gauge coordinate →
containing canonical cell), and writes
`processed/indofloods/indofloods_grid_mapping.csv` (214 rows, one per
gauge, confirmed this phase: `wc -l` = 215 including header) and
`processed/indofloods/indofloods_grid_events.csv` (4,549 rows including
header = 4,548 events, confirmed this phase). **A real, important
correction to `docs/LABEL_ENGINE.md` is required here**: that document
(dated the same day, "Phase 4") claims *"there is no gauge-coordinate file
anywhere in this repo"* and that genuine event-based FF labeling is
therefore impossible — this is **factually superseded**. `docs/LABEL_ENGINE.md`'s
Phase-4 scripts (`dev/Fetch cb ff labels.py`, `scripts/build_panindia_ff_labels.py`)
only checked for gauge coordinates in `data/catchment_characteristics_indofloods.csv`
(which genuinely lacks them), not in `data/metadata_indofloods.csv` (which
genuinely has them, confirmed this phase: `METADATA_CSV` is the file
`scripts/map_indofloods_to_grid.py` actually reads, and it is a distinct
file from the catchment-characteristics one). Phase 5's INDOFLOODS forensic
inventory found this file and built a working spatial join from it. **This
means `scripts/build_panindia_ff_labels.py` (Phase 4) and its output
(`processed/labels/ff_labels_proxy.csv`'s event-based "UNKNOWN everywhere"
summary) are STALE** relative to the later, correct Phase 5 artifacts —
the authoritative current event-based FF spatial mapping is
`processed/indofloods/indofloods_grid_events.csv`, not
`scripts/build_panindia_ff_labels.py`'s event-based output. This phase does
not modify or delete the stale script/output (out of scope — audit only);
it flags the discrepancy so a future phase doesn't cite the wrong one.

**FF — B. Rainfall proxy label** (`scripts/build_panindia_ff_labels.py`,
still current/valid for this half): 3-day cumulative rainfall ≥100mm AND
daily ≥40mm, same max-over-fine-cells spatial method as CB, explicitly
tagged `label_status=PROXY_NOT_OBSERVED` — never `POSITIVE`. Confirmed this
phase to remain correctly distinct and never merged with (A) anywhere in
`processed/labels/ff_labels_proxy.csv`'s schema.

**Re-verification of the three standing conclusions** (per the brief's
explicit instruction to verify, not merely restate):
- *"INDOFLOODS contains observed flood events"* — **CONFIRMED**:
  `processed/indofloods/indofloods_grid_events.csv` contains 4,548 real
  gauge-sourced event rows with real `Start Date`/`End Date`/`Flood Type`
  values (re-opened and spot-checked this phase).
- *"It does NOT provide confirmed true negatives"* — **CONFIRMED, still
  true**: no code path anywhere in `scripts/map_indofloods_to_grid.py` or
  the Phase 5.5 label-definition work assigns `label=0`/`NEGATIVE_CONFIRMED`
  to any cell/date absent from the event list; Phase 5.5's own
  `docs/INDOFLOODS_NEGATIVE_LABEL_AUDIT.md` conclusion (Case B: unknown, not
  confirmed non-flood) was re-read this phase and nothing found since then
  contradicts it. **However**, a real counter-example exists ELSEWHERE in
  the repo: `dev/Fetch cb ff labels.py`'s `derive_ff_labels()` function
  (the BLR-point-model path, separate from the pan-India Phase 5 path) DOES
  construct `ff_label = rain_df['date'].dt.date.isin(ff_dates).astype(int)`
  — which assigns a **literal 0 to every date not in the event-derived set**,
  i.e. exactly the "no event ⇒ confirmed negative" mistake the brief warns
  against, for the `near` (within-200km) INDOFLOODS-event branch of that
  function (its *fallback* branch, used when `near` is empty, is the
  rainfall proxy already discussed). Whether this branch ever actually
  executes depends on `load_gauge_locations()` finding
  `data/catchment_characteristics_indofloods.csv` (confirmed this phase: it
  exists on disk, so this is not automatically dead code the way
  `docs/LABEL_ENGINE.md`'s "file does not exist" claim implied for that
  function specifically — **this needs its own follow-up**, since
  `catchment_characteristics_indofloods.csv` existing does not by itself
  mean it has usable lat/lon columns; Phase 4's `docs/LABEL_ENGINE.md`
  separately asserted it lacks them. This phase does not re-derive whether
  `dev/Fetch cb ff labels.py`'s INDOFLOODS branch currently executes or
  silently falls through to its proxy branch — flagged as unresolved,
  Part 11.). Regardless of whether it currently executes, **this script
  contains, in principle, exactly the labeling error the brief describes**,
  and should not be used as a template for any future FF labeling work.
- *"the rainfall-threshold FF proxy is not an observed flood label"* —
  **CONFIRMED**: `PROXY_NOT_OBSERVED` is a distinct `label_status` value,
  never conflated with `POSITIVE`/event-based labeling anywhere in
  `processed/labels/ff_labels_proxy.csv`.
- *"the PU research model is not production-ready"* — **CONFIRMED**: Phase
  5.7's own report (`docs/PHASE_5_7_FF_PU_MODEL_REPORT.md`, read in a prior
  phase, re-cited not re-read verbatim this phase) explicitly states this;
  nothing in this audit found any change to that status.

## 3. Forecast target semantics

Traced `forecast_action.py`, `lead_time.py`, `metar_ground_truth.py`,
`gfs_fetcher.py` directly. **A "slot" is a fixed 6-hour IST calendar-day
bucket** (00:00-05:59 / 06:00-11:59 / 12:00-17:59 / 18:00-23:59 IST) — it is
the **target/observation window**, not a statement about forecast lead
time. This is explicitly NOT the same thing as "6 hours of lead," confirmed
by `lead_time.py`'s own stated purpose: *"forecast_action.py's SLOT_NAMES
only expressed each slot as a fixed 6-hour IST calendar bucket... Nothing
in forecast.json quantified how far ahead of the event window the forecast
was actually issued."*

`lead_time.py` defines `lead_time_hours` as the gap between
`reference_time` (when the GFS data actually used was fetched,
`gfs_row_select.GFSSelection.fetched_at_utc`) and `valid_from` (the slot
window's **start**) — this is the PS's "2-6 hour actionable lead time"
metric, and it is a **data-latency/actionability** measure, not identical
to "GFS forecast hour" (`source_fhour`). Separately, `gfs_fetcher.py`'s
own docstring documents a "t+12 rule": for example, Slot 1 (06:01-12:00
IST) is fed by the **previous day's 12Z cycle at forecast hour f012** (a
genuine 12-hour-lead GFS forecast), not an f000 analysis and not a small
lead like f003/f006. **This means the pan-India grid pipeline
(`backend/pipeline.py`, f000, Phase 0.4.4) and the VOBL slot-model pipeline
(`gfs_fetcher.py`, f012-class leads) use fundamentally different GFS leads
for conceptually related purposes** — a real, repository-wide
inconsistency in what "the forecast" actually represents, beyond what
Phase 0.4.4 already found for the pan-India grid alone. This report does
not resolve which of these (f000, f012, or the historical pilot's f003/f006)
should be the canonical target-defining lead; it documents that at least
three different conventions coexist in the codebase today.

**For the historical GFS pilot specifically**: init 2020-07-15 00Z, f003 →
valid 2020-07-15 03:00 UTC = **08:30 IST** (falls in IST slot 1,
06:00-11:59), f006 → valid 2020-07-15 06:00 UTC = **11:30 IST** (also falls
in IST slot 1). Both the pilot's two leads land in the **same** IST slot
for this particular cycle — a coincidence of this cycle's 00Z start time
and slot boundaries, not a general guarantee for every cycle/lead pair.

## 4. Formal label alignment contract

See the companion document,
`docs/PHASE_0_4_6_HISTORICAL_LABEL_ALIGNMENT_CONTRACT.md`, created this
phase with the full FORECAST RECORD / LABEL RECORD schema, the join
procedure (UTC→IST conversion, slot lookup, per-hazard rule table), and the
`UNKNOWN`-never-collapsed safety rule (re-verified: `tests/test_panindia_labels.py::test_7_unknown_not_collapsed`,
1/1 pass, part of that file's 11/11).

## 5. No invented negatives — compliance check

Applied the brief's rule (`1=confirmed positive, 0=confirmed negative,
null=unknown`) against each hazard's ACTUAL current pan-India label-engine
output (not the separate BLR-point `dev/` script flagged as non-compliant
in Part 2):
- TS: compliant — `label=0` only after a closed window with real negative
  observations.
- CB: compliant in spirit (continuous monitoring genuinely supports an
  inferred negative) but the *current file format* doesn't materialize
  negatives as rows — a real implementation gap worth closing before any
  CB classifier is trained on this file directly (a naive "all missing
  cell/date/slot combos are unlabeled" reading would be wrong; the correct
  reading, "missing means did-not-cross-threshold," is currently tribal
  knowledge, not enforced by the schema).
- FF (A, event-based, current Phase 5 artifacts): compliant — `UNKNOWN`
  used throughout, no fabricated zeros found in `processed/indofloods/*` or
  `scripts/map_indofloods_to_grid.py`.
- FF (B, proxy): compliant — `PROXY_NOT_OBSERVED`, never `POSITIVE` or
  `NEGATIVE_CONFIRMED`.
- **Non-compliant exception found**: `dev/Fetch cb ff labels.py`'s
  INDOFLOODS-branch `ff_label` construction (Part 2) — flagged, not fixed
  (out of scope for an audit; this is dev/ code, not currently wired into
  any production or research-pipeline path this audit traced).

## 6. Spatial alignment

| Source | Mapping type | Mechanism | Grid used |
|---|---|---|---|
| TS (VOBL) | Station-to-cell | Fixed: VOBL's real coordinates (12.97°N/77.58°E) → nearest canonical cell `IND_13.0_78.0`, hardcoded once in `build_panindia_ts_labels.py` | Existing canonical 992-cell grid (`regrid.cell_id_for`) — no second grid created |
| CB | Grid-to-grid | IMD 0.25° fine cells → canonical 1.0° cell by max-aggregation within a ±0.5° box | Same canonical grid |
| FF (A) | Point-to-cell (gauge→cell) | `scripts/map_indofloods_to_grid.py`: real gauge lat/lon → containing canonical cell, confirmed via re-reading the script this phase | Same canonical grid |
| FF (B) | Grid-to-grid | Same method as CB | Same canonical grid |

**INDOFLOODS mapping file status, re-confirmed this phase by direct file
read** (not re-run, read-only per the brief's scope):
- `processed/indofloods/indofloods_grid_mapping.csv`: 215 lines including
  header = **214 gauge rows**, matching the public INDOFLOODS release's
  station count.
- `processed/indofloods/indofloods_grid_events.csv`: 4,549 lines including
  header = **4,548 event rows**, matching Phase 5's established figure.
Both files are present, non-empty, and internally consistent with every
prior phase's reporting of them — **no drift or corruption found**.

No new/second grid was created anywhere in this audit or in any of the
traced label scripts — every one of them imports and reuses
`regrid.cell_id_for`.

## 7. Temporal overlap with historical GFS

| Source | Earliest usable date | Latest usable date | Temporal resolution | Overlap with GDEX d084001 archive (2015-01-15–present) |
|---|---|---|---|---|
| Historical TS (`ts_labels.csv`) | 2015-01-02 | not bounded by this audit (label file extends to "ALL"-tagged rows — not fully characterized; see Part 11) | Slot (6h, IST) / underlying station obs resolution unverified | **Real overlap confirmed for VOBL's single cell only**: 2015-01-15 onward |
| CB (`cb_labels.csv`) | 2015-01-01 | 2025-12-05 | Daily | **Real overlap confirmed, pan-India** (wherever IMD data exists): 2015-01-15–2025-12-05 ∩ archive = 2015-01-15–2025-12-05 (archive continues "to present," so CB's own end date is the binding constraint) |
| FF proxy (`ff_labels_proxy.csv`) | 2015-01-01 | 2025-12-05 | Daily | Same as CB |
| FF observed (INDOFLOODS, `indofloods_grid_events.csv`) | Events span the public release's actual date range (previously established: 2015-05-18–2020-09-24 for the subset with real IMD rainfall overlap, Phase 5.6 — this audit did not re-derive the full INDOFLOODS event date range from scratch, only re-confirmed the file's row count) | per above | Daily (event start/end) | **Only the 2015–2020 portion of INDOFLOODS overlaps the GDEX archive's 2015-01-15 start** — pre-2015 INDOFLOODS events (the dataset goes back to 1959) have **no** historical GFS archive equivalent at all, a hard, structural overlap boundary, not a data-quality issue |

**Overlap demonstrated with the actual GFS pilot date (2020-07-15), using
real, already-existing label rows — no new labels fabricated for this
exercise**:

- `grep` of `processed/labels/ts_labels.csv` for `2020-07-15T0601-1200,IND_13.0_78.0`
  returns a real row: `ts,0.0,NEGATIVE_CONFIRMED,"IMD station observation,
  VOBL/43295"`. Per Part 3, the pilot's f003 (08:30 IST) and f006 (11:30
  IST) both fall in this exact IST slot 1 window for this exact calendar
  date — **a real, demonstrable, non-fabricated alignment**: IF a
  historical GFS-based TS nowcast were trained for VOBL's cell at this
  slot, this is the real label it would join against.
- `processed/labels/cb_labels.csv` and `processed/labels/ff_labels_proxy.csv`
  contain real rows for 2020-07-15 slot1 at OTHER cells (e.g.
  `IND_16.0_73.0`), but **no row exists for `IND_13.0_77.0` or
  `IND_13.0_78.0` (the pilot's Bengaluru-area cells) at this date/slot in
  either CB or FF-proxy** — confirmed by direct `grep`, not assumed. Per
  Part 1/5's sparse-storage convention, this means Bengaluru's daily
  rainfall on 2020-07-15 did not cross either threshold (a real, inferable
  negative for CB; a real, inferable "proxy not triggered" for FF-B) — but
  this report does not manufacture that row; it states the inference and
  its basis plainly, and notes it is NOT the same as `UNKNOWN`/missing data,
  since IMD's rainfall coverage genuinely includes this date and cell.
- No INDOFLOODS event-based FF row was checked for this exact date/cell
  (would require re-opening `indofloods_grid_events.csv` for a specific
  gauge/date match, not performed this phase — flagged as a natural next
  step, Part 11, not fabricated here).

## 8. Lead-time matrix (2-6h conceptual window)

| Hazard | Forecast lead | Valid time (from 2020-07-15 00Z) | Observed label available? | Temporal alignment | Spatial alignment | Label quality |
|---|---|---|---|---|---|---|
| TS | +3h (native, f003) | 03:00 UTC / 08:30 IST, slot 1 | **Yes**, real row exists (VOBL cell only) | Exact — valid time falls inside the labeled slot window | Station-to-cell, VOBL only | High (genuine continuous-monitoring negative/positive) |
| TS | +6h (native, f006) | 06:00 UTC / 11:30 IST, slot 1 (same slot as +3h this cycle) | Same row as +3h (same slot) — **both leads alias to the same label** for this particular cycle | Exact, but **not independently informative**: a model trained on both f003 and f006 features against this single slot's label would be learning from two different lead times toward the identical target, not two different targets | Same | Same |
| TS | +2h, +4h, +5h | Would require interpolation between f003/f006 (not native to any single file) or additional native 3-hourly leads not yet downloaded (the archive documents a native 3-hourly cadence, Phase 0.4.2, so +2h/+4h/+5h would need, e.g., f001/f002/f004/f005 — **not confirmed present as separate files in this archive's documented product** beyond the 3-hourly steps already used; this audit does not claim interpolated values are native) | Not computed this phase (would need interpolation, explicitly out of scope — "do not call interpolated values native") | N/A | N/A | N/A | **Not attempted** |
| CB | +3h / +6h | Same valid times | **Yes**, real daily label exists for 2020-07-15 at many cells (not Bengaluru specifically that day, Part 7) | **Coarse**: the daily label is the same regardless of which hour within the day the valid time falls — +3h and +6h both alias to the SAME daily CB label, so lead-time granularity is lost entirely for this hazard | Grid-to-grid | **Medium** — real continuous source, but daily granularity cannot support genuine 2-6h-ahead sub-day nowcasting; a CB "nowcast" trained this way is really predicting "will today cross the threshold," not "will the next few hours" |
| FF (proxy) | +3h / +6h | Same | Same daily-proxy availability as CB | Same daily-granularity caveat as CB | Grid-to-grid | **Lower than CB** — proxy, not observed, same daily coarseness |
| FF (observed) | +3h / +6h | Same | Only at cells/dates with an actual assigned gauge event (sparse; not demonstrated for 2020-07-15/Bengaluru this phase) | Daily, event-window based | Point-to-cell | **Highest scientific validity when present, but extremely sparse** (4,548 events across 214 gauges over decades) — not something a dense slot-level training table can rely on for most cell/date combinations |

## 9. Hazard-specific readiness

- **TS**: **READY_FOR_HISTORICAL_SUPERVISED_PILOT** — but **only for VOBL's
  single cell**. Evidence: genuine confirmed positives and negatives exist
  (Part 1/2), a real demonstrated alignment exists for the exact pilot date
  (Part 7), and `UNKNOWN` is correctly applied everywhere else. Not
  READY pan-India — 991 of 992 cells have no TS observation at all, ever.
- **CB**: **READY_FOR_HISTORICAL_SUPERVISED_PILOT at daily granularity
  only**. Evidence: continuous, genuinely-negative-capable, pan-India
  source exists (Part 1/2/6), real overlap with the GFS archive confirmed
  (Part 7). **Not** ready for genuine sub-day (2-6h) supervised nowcasting
  — the label itself cannot distinguish any hour of the day from any
  other, so "predicting CB at +3h vs +6h" would be training a model to
  predict the same daily outcome from two different-but-correlated feature
  snapshots, not a real sub-day forecast target.
- **FF (observed, A)**: **READY_FOR_HISTORICAL_PU_PILOT only** — real
  positives exist and are spatially/temporally real (Part 1/2/6), but
  confirmed negatives do not and must not be manufactured (Part 5); any
  training must explicitly use PU framing (consistent with Phase 5.7's
  already-established approach), never naive binary supervision.
- **FF (proxy, B)**: **READY_FOR_HISTORICAL_SUPERVISED_PILOT at daily
  granularity, with the explicit caveat that it is a proxy, not an observed
  flood** — same daily-granularity limitation as CB, and additionally not a
  real flood observation at all.

**No single overall "ready" verdict is given** — each hazard's actual label
semantics materially differ, consistent with the brief's instruction not to
flatten them into one number.

## 10. The central scientific question: does this support "2-6h hyper-local nowcasting"?

**Only partially, and only for TS.** The project's "2-6 hour nowcasting"
claim (as implemented in `lead_time.py`'s actionable-lead-time framing) is
about **predicting a hazard at a future valid time using data available
some hours earlier** — genuinely a forecasting claim, not a
detection-after-the-fact claim, for the TS pathway specifically (METAR
ground truth is itself near-real-time but the *model* is scored against a
*future* slot relative to when its input GFS data was fetched).

For **CB and FF-proxy**, the daily-resolution label makes the 2-6h
distinction **largely moot**: a model "predicting" CB at +3h vs +6h lead is
not predicting two different outcomes — it is predicting the SAME
day-level outcome from two time-adjacent feature snapshots. This is closer
to **"detecting a hazard occurred at approximately the same time" (same
day) than to "predicting a future hazard at a specific sub-day valid
time."** This report does not say the daily CB/FF-proxy labels are
useless — they are real, continuous, honestly-sourced data — but training a
model against them and describing the result as "2-6 hour nowcasting" would
overstate what the label can actually discriminate.

For **FF-observed**, the label is event-based and daily, with the same
granularity caveat as CB, compounded by extreme sparsity — there is
currently no basis to claim sub-day flash-flood nowcasting is validated by
this label source at all; any such claim would need either higher-temporal-
resolution flood observations (stream gauge time series, not just event
start/end dates) or acceptance that the PU-pilot's role is coarser
flood-risk screening, not minute/hour-level nowcasting.

## 11. Minimum data requirement for blocked items

- **FF genuine supervision (beyond PU)**: requires **continuous
  stream-gauge or remote-sensing flood-extent monitoring** at a sub-daily
  cadence covering the same gauges/cells INDOFLOODS already identifies —
  not generically "more flood data," specifically monitoring that can
  support a real `NEGATIVE_CONFIRMED` state (a gauge reading showing
  normal/non-flood flow at a specific time), which no file in this
  repository currently provides.
- **Sub-day CB/FF-proxy resolution**: requires an **hourly or sub-6-hourly
  gridded rainfall product** covering the same 2015+ period (IMD's daily
  `.grd` product is the only rainfall archive currently in this repo at
  the needed pan-India spatial extent; a finer-resolution IMD product, if
  one exists and is accessible, or a satellite-based sub-daily rainfall
  estimate, would be the specific missing artifact — not re-searched this
  phase, since this is an audit of what exists, not a new acquisition
  search; Phase 0.2's external-data research already looked at this
  broader question and found no immediately usable pan-India sub-daily
  ground-truth rainfall source).
- **Pan-India TS observations**: requires a **second (or network of)
  station/lightning-detection source beyond VOBL** — none exists in this
  repository; Phase 0.2's research did not find an accessible one either.
- **+2h/+4h/+5h GFS leads**: requires downloading additional native
  3-hourly GDEX files (e.g. `f001`-`f002`/`f004`/`f005` if those exist in
  the product, or accepting interpolation with explicit labeling as such)
  — not yet downloaded, no claim of availability beyond f003/f006 made
  this phase.
- **CTT tendency** (carried from Phase 0.4.5, still blocking any CTT-based
  training regardless of label alignment): requires a second historical
  initialization cycle, exact candidate files already given in Phase 0.4.5
  Part 5.
- **Open, unresolved item from this audit**: whether
  `dev/Fetch cb ff labels.py`'s INDOFLOODS-event `ff_label` branch (Part 2's
  flagged non-compliant code) currently executes in practice depends on
  whether `data/catchment_characteristics_indofloods.csv` has usable
  lat/lon columns — this file's existence was reconfirmed this phase but
  its column contents were not re-inspected; this is a loose end for a
  future phase, not resolved here.
- **Open, unresolved item**: the full INDOFLOODS event date range and
  `ts_labels.csv`'s true upper date bound were not fully re-derived this
  phase (noted "ALL"/unbounded in Part 7's table) — a future phase should
  pin these down exactly before any dataset-scale join is attempted.

## 12. Final summary

### A. WHAT WE CAN TRAIN HONESTLY NOW
- TS, supervised, VOBL's single canonical cell only, at the native 6-hour
  IST slot granularity, using the pilot's f003/f006-style valid-time
  alignment demonstrated in Part 7.
- CB, supervised, pan-India (wherever IMD rainfall data exists), but only
  at **daily** granularity — not a genuine sub-day 2-6h target.
- FF proxy (B), supervised, pan-India, same daily-granularity caveat,
  explicitly labeled as a proxy, never presented as an observed flood.

### B. WHAT REQUIRES PU LEARNING
- FF observed (A) — INDOFLOODS event-based labels, pan-India but sparse,
  positives real and spatially/temporally resolved, no confirmed negatives,
  consistent with the already-established Phase 5.7 PU framing.

### C. WHAT CANNOT BE TRAINED YET
- Genuine sub-day (2-6h-distinguishing) CB or FF supervision of any kind —
  the only rainfall source available is daily.
- Pan-India TS supervision beyond VOBL — no second observation source
  exists.
- CTT-tendency-dependent features in any hazard's model — blocked pending
  a second historical GFS cycle (Phase 0.4.5).
- +2h/+4h/+5h-lead-specific historical training — not yet downloaded, and
  this audit does not claim they can be safely interpolated without
  revisiting that question explicitly.

### D. MINIMUM NEXT DATA ACQUISITION
1. A second historical GFS initialization cycle (for CTT tendency; exact
   files given in Phase 0.4.5 Part 5) — smallest, most tractable next step.
2. If sub-day CB/FF resolution is wanted: an hourly/sub-6-hourly gridded or
   satellite rainfall product for the same 2015+ period — not yet
   identified as available in this repository or confirmed accessible
   externally (would need its own Phase 0.2-style research pass).
3. If pan-India TS is wanted: a second TS observation network — not
   currently known to be accessible (per Phase 0.2).
4. If sub-daily genuine FF negatives are wanted: continuous stream-gauge
   time series for INDOFLOODS' 214 gauges — not currently in this
   repository.

### E. NEXT ENGINEERING STEP
Given (A) above, the most tractable, honestly-scoped next engineering step
is a **VOBL-only historical TS supervised pilot**: join the Phase 0.4.5
historical-GFS feature reconstruction (already real, already validated for
6 cells including VOBL's `IND_13.0_77.0`/nearby `IND_13.0_78.0`) against
`processed/labels/ts_labels.csv`'s real VOBL-cell rows, for whatever
initialization cycles are downloaded, respecting the UTC→IST→slot join
procedure formalized in `docs/PHASE_0_4_6_HISTORICAL_LABEL_ALIGNMENT_CONTRACT.md`.
This was **not performed this phase** (explicitly an audit, not a dataset
build) but is the most defensible, smallest-scope next step the evidence
here supports — a genuinely supervised, genuinely pan-temporal (not
daily-coarsened), single-cell pilot, free of the PU and daily-granularity
caveats that block every other hazard/scope combination.

## Testing

Ran the existing label-validation suite directly (read-only audit, no new
label-generation code written this phase):
- `tests/test_panindia_labels.py` — **11/11 pass** (re-confirmed, including
  the UNKNOWN-non-collapse test, correctly named
  `test_7_unknown_not_collapsed`, Part 4).
- Full existing suite: `pytest . --ignore=test_himawari.py
  --ignore=test_nomads.py --ignore=test_segments.py --ignore=test_segments_v2.py`
  → **176 passed** (unchanged from the Phase 0.4.5 baseline: 151 original +
  25 Phase 0.4.5 focused tests), same 4 pre-existing environment-only
  collection errors (donfig ×3, NOMADS 403 ×1). No test was weakened. No
  new test was needed this phase since no new code was written — this
  phase is read-only analysis of existing label artifacts and code.

## Scope confirmation

- **Files created this phase**: this report and the companion
  `docs/PHASE_0_4_6_HISTORICAL_LABEL_ALIGNMENT_CONTRACT.md`.
- **Files modified**: none.
- **Files read (not modified)**: `docs/LABEL_ENGINE.md`,
  `scripts/build_panindia_ts_labels.py`, `scripts/build_panindia_cb_labels.py`,
  `scripts/build_panindia_ff_labels.py`, `scripts/map_indofloods_to_grid.py`,
  `dev/Fetch cb ff labels.py`, `train_cb_ff_models.py`, `metar_ground_truth.py`,
  `lead_time.py`, `forecast_action.py`, `gfs_fetcher.py`,
  `tests/test_panindia_labels.py`, `processed/labels/*.csv`,
  `processed/indofloods/*.csv`.
- No large-scale data acquisition was performed. No model was trained. No
  production code, schema, canonical grid, workflow, or frontend file was
  changed. Nothing was deployed, committed, or pushed.
