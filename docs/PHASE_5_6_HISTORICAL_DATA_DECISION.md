# Phase 5.6 — Historical Data & FF Learning-Formulation Decision

Data-resolution and design phase only. No FF model trained, no production
file modified (re-verified by checksum, Task 12). All numbers below are
either carried over unchanged from Phase 5/5.5 (cited) or computed fresh
this phase by `scripts/compute_indofloods_imd_overlap.py` (read-only,
reuses Phase 5.5's grid/regridding logic from
`scripts/prepare_indofloods_rainfall_context.py` verbatim).

## 1. Historical rainfall sources actually available

Whole-repo search (all extensions, all directories, not just `data/`) for
IMD/ERA5/GFS/MERA/IMDAA/GRIB/NetCDF/.grd/.nc/rainfall/precipitation/
GPM/TRMM/CHIRPS references, with every candidate file actually opened:

| Source | Real data present | Gridded | Spatial | Temporal | Independent of INDOFLOODS | Usable for background | Usable for event predictors |
|---|---|---|---|---|---|---|---|
| `imd_rain/rain/{2015..2025}.grd` | Yes | Yes, 0.25 deg (129x135) | Superset of 992-cell grid | Daily, 2015-01-01 to 2025-12-31 | Yes | Yes (2015-2025 only) | Yes (2015-2025 only) |
| `data/era5_6hrly_bengaluru_2015_2025.csv` + variants | Yes | No, single point (VOBL) | Bengaluru only | 6-hourly, 2015-2025 | Yes | No | No (wrong shape for pan-India) |
| `data/era5_200_300hpa_winds_2015_2025.csv` | Yes | No, single point | Bengaluru only | 6-hourly, 2015-2025 | Yes | No | No |
| `dev/fetch_imerg_realtime.py` (GPM IMERG) | No archive, live-fetch scaffolding only, 50km VOBL box | N/A | VOBL box only | 30-min, real-time only | N/A | No | No |
| `raw/imdaa/`, `processed/imdaa/` | No (both empty, confirmed by direct listing) | N/A | N/A | N/A | N/A | No | No |
| `scripts/build_panindia_ff_labels.py` / `processed/labels/ff_labels_proxy.csv` | Yes, but this is a **rainfall-threshold proxy label** (3-day cumsum >=100mm AND daily >=40mm), reusing `imd_rain/*.grd` — not an independent source | Same grid as above | Pan-India, 992 cells | Daily x 4 slots, 2015-2025 | No — derived from the same `.grd` files, and explicitly marked `PROXY_NOT_OBSERVED` | No (it's a label artifact, not a predictor) | No |

No GFS/GPM/TRMM/CHIRPS historical archive files were found anywhere in the
repo (only live-fetch scripts for the current-day operational pipeline,
e.g. `dev/fetch_gfs_realtime.py`, `dev/fetch_imerg_realtime.py`,
`dev/fetch_himawari_realtime.py` — none write a historical archive).

**Note on `processed/labels/ff_labels_proxy.csv`:** this is a pre-existing
artifact from an earlier, since-superseded pass that incorrectly claims
"no gauge-coordinate file exists anywhere in this repo" — a claim Phase 5
directly contradicts (`data/catchment_characteristics_indofloods.csv`
exists and 155/214 gauges do have events mapped to cells via
`scripts/map_indofloods_to_grid.py`). This phase does not modify or delete
that file (read-only constraint), but future work should treat
`processed/labels/ff_labels_proxy.csv` and its rainfall-threshold "proxy"
label as stale/superseded by the Phase 5/5.5/5.6 INDOFLOODS-based pipeline,
not as a second valid label source.

Only `imd_rain/rain/*.grd` is a real, gridded, independent, historical
precipitation source — unchanged conclusion from Phase 5.5.

## 2. Official sources worth acquiring

- **IMD gridded rainfall, pre-2015 (1970-2014):** same product family
  already in the repo; would directly extend the existing pipeline with
  no format-mapping work. Highest-value acquisition (see Section 6).
- **NCMRWF IMDAA reanalysis:** would add CAPE/K-index/wind-shear-class
  predictors the `.grd` rainfall alone cannot supply. Currently blocked
  (Section 5).
- **NCMRWF MERA:** would add 4km hourly resolution, but its 2020-2025
  coverage barely overlaps the INDOFLOODS event record (Section 3).

## 3. MERA assessment

NCMRWF MERA (Modern-Era Retrospective Analysis for India, ~4km, hourly,
satellite+radar-informed rainfall) is a real, documented NCMRWF product per
its official public description (ncmrwf.gov.in), reportedly covering
roughly 2020 onward. This repo has **no MERA credentials or config of any
kind** — a repo-wide case-insensitive grep for `mera`/`ncmrwf` finds only
the existing IMDAA scaffolding (`scripts/acquire_imdaa.py`,
`scripts/parse_imdaa.py`, `docs/IMDAA_DATA_SPEC.md`) and comment
references to registering with NCMRWF in general; nothing MERA-specific
exists. Per NCMRWF's own public access model (same registration-gated
pattern as IMDAA), access would require: NCMRWF account registration,
institutional/project justification, and approval — not an open/no-auth
download. This sandbox cannot reach NCMRWF-adjacent hosts at all (403 from
the sandbox's own egress proxy, confirmed in `docs/IMDAA_DATA_SPEC.md`),
so this is a blocker independent of credentials.

**Materiality given 2020-2025-only coverage:** of the 684 INDOFLOODS events
inside the IMD-covered 2015-2025 window, only events with `Start Date` on
or after 2020-01-01 would fall inside a 2020-2025 MERA window. Per Phase
5.5, the **last INDOFLOODS event date in the mapped events table is
2020-09-24** — so at most a handful of months of overlap (2020-01-01 to
2020-09-24) exist between MERA's stated coverage and any actual INDOFLOODS
event. MERA would not materially improve the FF dataset given INDOFLOODS'
own event record ends before MERA's coverage window is substantially
underway. Verdict: **not a near-term priority**, independent of the access
blocker.

## 4. IMD assessment

Real, verified, already partially present (Section 1). 0.25-degree daily
gridded rainfall, 2015-2025, 71.5% masked (expected ocean/outside-India
mask). This is the only source this repo can extend without new
credentials or network access — **but only if an earlier-years file
(pre-2015) of the same product is separately obtained**; nothing in this
repo currently references or partially configures a pre-2015 IMD file
(grep of code/docs for IMD access mechanisms and download scripts other
than the `.grd` files already present found no fetcher, no config, no API
keys — only the raw historical files themselves, whose provenance/download
method is not documented in this repo). If the same product's earlier-year
files were placed at `imd_rain/rain/{1970..2014}.grd` in the same format,
they would close the 1970-2014 gap directly (same regridding logic already
built in Phase 5.5's `scripts/prepare_indofloods_rainfall_context.py`
would work unmodified).

## 5. IMDAA assessment

Per the repo's own existing `docs/IMDAA_DATA_SPEC.md` (Phase 2, re-read
this phase, not recomputed differently): **DATA BLOCKED**. No network
route from this sandbox (403 from the sandbox's own egress proxy to
IMD/NCMRWF-adjacent domains — an environment limitation, not an NCMRWF
outage), no credentials, `raw/imdaa/` and `processed/imdaa/` both
confirmed empty. Scaffolding (`scripts/acquire_imdaa.py`,
`scripts/parse_imdaa.py`) exists but has never run against real data.
Status unchanged this phase.

## 6. Best common historical period

**2015-01-01 to 2020-09-24** (unchanged from Phase 5.5, re-verified this
phase by `scripts/compute_indofloods_imd_overlap.py`): the intersection of
real IMD gridded-rainfall availability (2015-2025) and actual INDOFLOODS
event dates present in that window (last event: 2020-09-24; IMD data
continues to 2025 with no further mapped events to pair against it).

## 7. Number of usable INDOFLOODS events

**684** of 4,548 raw mapped events fall inside the IMD-covered window
(15.0%), collapsing to **620 unique (cell, date) positive pairs** across
**69 unique cells** and **131 unique gauges** (computed this phase; the
620/69/131 figures narrow Phase 5.5's whole-dataset 4,106/75/155 figures to
just the IMD-overlap subset — both figures are correct at their own scope
and are not in conflict).

## 8. Candidate background/unlabeled population

Computed this phase across the 69 in-window event cells: **277,242**
grid-cell-days have real (non-masked) IMD rainfall data in 2015-2025, of
which **276,622** are NOT a recorded INDOFLOODS event day for that cell —
these are candidate **unlabeled/background** cell-days (never "negative"
days; see Section 9).

## 9. Whether true negatives are possible

**No** — unchanged Case B conclusion from Phase 5.5
(`docs/INDOFLOODS_NEGATIVE_LABEL_AUDIT.md`,
`docs/FF_LABEL_READINESS.md`). INDOFLOODS events are extracted from
whatever streamflow data happens to be available per gauge (50.5% of
gauges have <0.9 level-entries/day; 17.3% have <0.5), so an unlabeled day
cannot be read as a confirmed non-flood day dataset-wide. The 276,622
candidate background cell-days above are unlabeled, not negative.

## 10. Recommended FF learning formulation

Five options compared against label assumptions, leakage risk, data
requirements, calibration, validation, suitability for 2-6h operational
lead times, and fit to this project's actual data (94.9% of events with no
predictor pairing at all; no true negatives):

| Option | Label assumption | Leakage risk | Data requirement | Calibrated probability? | Validatable? | 2-6h lead-time fit | Fit to actual data |
|---|---|---|---|---|---|---|---|
| A. Standard binary classification | Requires confirmed positive AND negative | High — any "negative" here is actually unlabeled, so the model learns an artifact of what wasn't recorded, not what didn't flood | Needs true negatives (not available) | Only if labels are correct — they aren't | Yes, but validates against a false ground truth | Poor — precision/recall are meaningless against fabricated negatives | Poor fit — violates Case B directly |
| B. Positive-unlabeled (PU) learning | Only positives are trusted; unlabeled = mixture of hidden positives and (probably) non-events | Low if the unlabeled set is honestly treated as unlabeled, not silently relabeled negative | Needs a large unlabeled pool with real predictors — exactly what the 276,622 candidate background cell-days provide | Yes, with standard PU calibration corrections (e.g. estimating the positive-class prior) | Yes, with PU-appropriate metrics (e.g. lift over base rate, not naive ROC-AUC) | Good — well suited to sparse, under-reported hazard labels like this one | **Best fit** — matches Case B and the 2015-2025/69-cell data exactly as it exists |
| C. One-class / anomaly detection | Learns the "normal" distribution from positives (or from background), flags departures | Low | Needs a well-characterized background distribution — available, but purely rainfall-based background is a weak "normal" model for a hydrological hazard | Poor — anomaly scores are not natively probabilities | Harder — no natural negative set to validate precision against | Moderate — can work for extreme-rainfall triggers but ignores catchment susceptibility entirely | Workable as a secondary/fallback method, not a primary formulation |
| D. Event-based case-control modeling | Matches each positive event to control (non-event) days/cells on confounders (season, cell, antecedent rainfall regime) | Moderate — control selection itself introduces assumptions about what a "fair" control is | Needs many controls per case — available from the background pool | Odds ratios, not calibrated probabilities directly | Yes, well-established epidemiological validation methods | Good for understanding drivers, weaker for producing a real-time probability score | Reasonable complement to B, not a full replacement |
| E. Hybrid susceptibility + event-trigger model | Static catchment susceptibility (already have: catchment characteristics for 155 gauges) x dynamic rainfall trigger (from PU-learned trigger model) | Low, if each half stays in its own leakage boundary (catchment features must never leak post-event data) | Needs both catchment characteristics (have) and a trigger model (needs B built first) | Best of the group — susceptibility gives a prior, PU-trigger gives a conditional | Yes, and validates each half separately | **Best long-term operational fit** — matches how flash-flood risk is actually structured (where + when) | Best long-term target, but depends on B existing first |

**Recommendation: build B (PU learning, restricted to the 2015-2025/69-cell
window) first, as the foundation for E (susceptibility x trigger hybrid)
once B is validated.** Standard binary classification (A) is not
defensible against this project's actual label integrity (Case B), and C/D
are useful diagnostics but weaker as the primary operational formulation.
This carries forward Phase 5.5's own PU recommendation unchanged, now
grounded in the exact overlap counts computed this phase.

## 11. Recommended predictor set (event-centered FF dataset schema, design only)

| Field | Tag | Source |
|---|---|---|
| `cell_id`, `date` | OBSERVED | canonical grid, `imd_rain/rain/*.grd` dates |
| `rain_t0_mm` ... `rain_t-5_mm` (daily rainfall, 0 to -5 days) | OBSERVED | `imd_rain/rain/*.grd`, regridded (Phase 5.5 logic) |
| `rain_3day_cumsum_mm` | DERIVED | computed from the above |
| `catchment_area_km2`, `catchment_slope`, other static catchment fields | OBSERVED | `data/catchment_characteristics_indofloods.csv` (155 gauges only — PROXY/UNKNOWN for the other 837 cells) |
| `cape`, `k_index`, `wind_shear` (pan-India gridded) | UNKNOWN | not available pan-India; only single-point Bengaluru ERA5 exists (PROXY at best, and only for VOBL) |
| `cape_imdaa`, `k_index_imdaa` etc. | UNKNOWN | would become OBSERVED only if IMDAA access is resolved (Section 5) — do not include until then |
| `mera_hourly_rainfall` | UNKNOWN | explicitly excluded — Task 3 did not establish real access (Section 3) |
| `label` (PU positive / unlabeled) | OBSERVED for positives, by definition UNKNOWN for unlabeled | INDOFLOODS events for positives; never fabricated for unlabeled rows |
| `month`/`season` | DERIVED | from `date` |

Fields not already available pan-India in this repo (CAPE/K-index/shear at
grid scale) are marked UNKNOWN and excluded from a first build; they would
only become usable if IMDAA access is resolved.

## 12. Recommended validation strategy (design only)

- **Temporal split:** train on 2015-01-01 to 2018-12-31, validate on
  2019-01-01 to 2020-09-24 (the actual end of the event record found in
  Section 6) — a genuine held-out later period, not a random shuffle,
  avoiding temporal leakage.
- **Spatial consideration:** hold out entire gauges/cells (not just dates)
  in a second, spatial-generalization validation fold — e.g. hold out all
  events from the highest-volume cell (`9.0_76.0`, 413 raw events per
  Phase 5.5) to test whether the model generalizes beyond its
  best-represented location, rather than memorizing it.
- **Explicit exclusions:** no post-event fields (`Warning Level`, `Danger
  Level`, any field derived from them) as predictors; no `Txd`
  antecedent-precipitation columns used as if they were available for
  non-event days (they are event-anchored only); antecedent rainfall
  windows must never extend past a row's own date (T0 inclusive, never
  T+1); same-cell/same-date collisions (442 rows, Phase 5.5 Part 1) must be
  deduplicated before any per-cell counting.

## 13. VOBL baseline assessment (read-only inspection)

`backend/pipeline.py`'s `hazard_probabilities()` (lines ~425-514) computes
`ff_prob` from a **hand-weighted heuristic formula** over `pwat`, `cape`,
`ctt_c`, `qpe_mm`, and low-level convergence (each capped and linearly
scaled with fixed coefficients, e.g. `pwat` contributes up to 0.45 of the
score), multiplied by `min(1.0, ts_prob + 0.1)`. This is **not trained on
any label** — there is no INDOFLOODS or any other flood-event label
anywhere in this function or its call sites, no train/validation split,
and no calibration step (the output is a raw weighted-and-capped score,
not a calibrated probability against observed outcomes). Its FF target is
a **rainfall/atmospheric-instability proxy heuristic**, not an observed
flood outcome. A terrain modifier (`apply_terrain_to_ff`, from
`terrain_lookup.py`) is applied afterward using DEM/slope/drainage data,
also not learned from labels. **Transfer potential:** the input variables
themselves (rainfall accumulation, CAPE-family indices) are conceptually
compatible with the predictor set in Section 11, but the current
coefficients and the heuristic structure itself do not transfer — a
pan-India PU-learned trigger model (Section 10) would need to be fit fresh
against the INDOFLOODS-derived labels, using VOBL's variable choices as a
reference for which atmospheric fields are worth pursuing (e.g. via IMDAA)
rather than reusing VOBL's specific formula or weights.

## 14. Remaining blockers

- No gridded precipitation exists in this repo for 1965/1970-2014 (85% of
  raw INDOFLOODS events by count have no predictor pairing at all).
- IMDAA: fully blocked (no network route from this sandbox, no
  credentials, empty `raw/imdaa/`).
- MERA: no credentials/config in this repo; official access requires
  NCMRWF registration/approval (not open); and even if resolved, its
  2020-2025-only coverage overlaps almost none of the actual INDOFLOODS
  event record (last event 2020-09-24).
- No true negatives are establishable from INDOFLOODS itself (Case B,
  dataset-wide) — this is a data-integrity limit, not an access blocker,
  and is not fixable without the raw per-gauge streamflow time series this
  repo does not have.
- No pan-India gridded atmospheric-instability predictors (CAPE/K-index/
  shear) exist outside the single-point Bengaluru ERA5 series.
- `processed/labels/ff_labels_proxy.csv` and its generating script contain
  a stale, superseded claim (no gauge coordinates exist) that Phase 5
  already contradicts; it should not be treated as a second valid label
  source going forward (Section 1 note).

## 15. Recommended next implementation phase

Implement the PU-learning FF trigger model (Section 10, option B) trained
on the 620 in-window (cell, date) positives against the 276,622 candidate
background cell-days from the 2015-2018 temporal training split (Section
12), using only the OBSERVED/DERIVED predictor fields from Section 11
(rainfall-window features and catchment characteristics), holding out
2019-2020-09-24 temporally and the highest-volume cell spatially for
validation — deferring any CAPE/K-index/shear predictor until IMDAA access
is resolved.
