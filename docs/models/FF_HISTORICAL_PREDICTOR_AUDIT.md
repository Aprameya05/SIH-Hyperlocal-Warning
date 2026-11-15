# FF Historical Predictor Audit — Phase 5.5, Parts 3 & 4

Searches this whole repo (not just `data/`) for existing continuous
historical/gridded precipitation or hydrological archives that could
supply per-cell-per-day predictors around INDOFLOODS event dates. Every
file below was actually opened this phase (`numpy`/`pandas`), not
inferred from its name.

## Candidates found

### 1. `imd_rain/rain/{2015..2025}.grd` — REAL, GRIDDED, USABLE (with caveats)

- Read directly with `numpy.fromfile(..., dtype=float32)` this phase.
  Confirmed real: file sizes match `days_in_year * 129 * 135 * 4` bytes
  exactly for 2015, 2020, 2025 (checked), i.e. a `(days, 129, 135)`
  float32 array per year, consistent with the documented 0.25-degree IMD
  gridded daily rainfall product.
- Grid (from `dev/Fetch cb ff labels.py`'s own documented constants,
  verified against the array shape): lat 6.5 to 38.5, lon 66.5 to 100.0,
  0.25-degree step -> 129 x 135 cells.
- Temporal coverage: **2015-01-01 to 2025-12-31** (one file per year,
  11 files, `2015.grd`...`2025.grd`, all present).
- Temporal resolution: daily.
- Variable: rainfall (mm/day), one variable, no units ambiguity (matches
  the IMD gridded-rainfall product's standard units).
- Missingness: **71.5% of grid points are the IMD sentinel -999 (missing)
  in every year checked** (2015/2020/2025 identical fraction) — this is
  the expected land/ocean and outside-India mask for this product, not a
  data-quality defect; real (non-masked) values range 0-679 mm/day across
  the sample years, physically plausible for extreme daily rainfall.
- Spatial coverage of the 75 event cells (Phase 5): the IMD grid's bounds
  (lat 6.5-38.5, lon 66.5-100.0) are a **superset** of the canonical
  992-cell grid's bounds (lat 6-37, lon 68-98), so every one of the 75
  event cells falls inside the IMD grid's extent. (Whether each specific
  0.25-degree IMD cell nearest an event cell's center is itself a
  masked/missing land point was not exhaustively checked for all 75; the
  prototype script in Part 4 checks it empirically for a real sample.)
- Overlap with the INDOFLOODS event period (1965-2020 in the mapped
  events table): **only 2015-2025 overlaps** — computed this phase:
  684 of 4,548 mapped events (15.0%) have a `Start Date` inside
  2015-2025, spanning 69 of the 75 event cells and 395 distinct dates.
  The other 85% of events (pre-2015) have **no** corresponding IMD daily
  rainfall in this repo.
- **Verdict: usable, but only for a 2015-2025 slice of INDOFLOODS events.**
  It is real, gridded, covers the right area, and is fine-grained enough
  (0.25 deg vs. the 1.0-deg canonical grid) to aggregate up. It does
  **not** span the full 1970-2020 INDOFLOODS period asked about in the
  brief — only its most recent decade.

### 2. `data/era5_6hrly_bengaluru_2015_2025.csv`, `..._with_indices.csv`, `data/era5_200_300hpa_winds_2015_2025.csv`

- Opened with `pandas`. All three are **single-point** time series (one
  location, Bengaluru/VOBL) at 6-hourly resolution, 2015-2025
  (16,072 data rows each in the two 16,073-line files). Columns include
  `ERA5_T2M`, `ERA5_D2M`, `ERA5_U10`, `ERA5_V10`, `ERA5_CAPE`,
  `ERA5_u_200hPa`/`ERA5_v_200hPa` etc.
- **Not gridded, not usable as a per-cell predictor.** These support the
  existing Bengaluru-specific nowcasting pipeline only; they have no
  spatial extent to map onto the 992-cell grid or the 75 event cells
  outside Bengaluru.

### 3. `raw/imdaa/`, `processed/imdaa/`, `scripts/acquire_imdaa.py`, `scripts/parse_imdaa.py`, `docs/IMDAA_DATA_SPEC.md`

- `raw/imdaa/` and `processed/imdaa/` are both **empty (8.0K = directory
  entry only, 0 data files)**, confirmed by direct listing this phase.
- `docs/IMDAA_DATA_SPEC.md` (an earlier phase's own audit) states plainly:
  "DATA BLOCKED" — no real IMDAA reanalysis data was ever downloaded into
  this repo (no network route from this sandbox, no credentials). The
  fetcher/parser scripts are scaffolding only, never run against real
  data.
- **Verdict: not usable — no actual data exists here, confirmed, not
  assumed from the filenames.**

### 4. `dev/era5_benchmark.py`, `dev/A5_retrain_with_6hr_era5.py`, and other `dev/A*_feature_engineering*.py` scripts

- These reference the same Bengaluru-point ERA5 CSVs above (or derive
  features from them) for the existing single-city nowcasting model, not
  a pan-India grid. Not a new candidate source.

## Summary

| Source | Real data present | Gridded | Spatial coverage of 75 event cells | Temporal coverage | Matches INDOFLOODS 1970-2020 span |
|---|---|---|---|---|---|
| `imd_rain/rain/*.grd` | Yes | Yes, 0.25 deg | Yes (bounds superset) | 2015-2025 | Partial (last 5-10 yrs only) |
| ERA5 Bengaluru CSVs | Yes | No (1 point) | No | 2015-2025 | No (wrong shape) |
| IMDAA (`raw/imdaa/`) | No (empty) | N/A | N/A | N/A | N/A |

**Only `imd_rain/rain/*.grd` is a real, gridded, usable historical
precipitation source in this repo**, and it only covers the most recent
~15% of the INDOFLOODS event period by event count (2015-2025 vs.
1965-2020 overall). See Part 4 below for what this supports concretely.

## What would be needed to cover the full 1970-2020 INDOFLOODS period

A gridded daily (or finer) precipitation product covering India at a
resolution matching or finer than the canonical 1.0-degree grid (0.25
degree IMD gridded rainfall is the natural fit, since it is the same
product family already partially present here), spanning at minimum
**1970 through 2020** to align with the bulk of INDOFLOODS's event
history (`floodevents_indofloods.csv` events run 1965-2020; the 1970-2020
window covers the large majority of them). This phase does **not** fetch
this — only inspects what already exists, per the hard constraint against
downloading new external data.

## Part 4: Prototype script against the existing IMD source

Since `imd_rain/rain/*.grd` is real, gridded, and covers (a slice of) the
event dates, a READ-ONLY prototype script was built and run this phase:
`scripts/prepare_indofloods_rainfall_context.py`. It reads the `.grd`
files, regrids their 0.25-degree cells onto the canonical 992-cell grid
(area-mean of the IMD cells whose centers fall inside each canonical
1.0-degree cell, reusing the canonical grid's own bounds/step rather than
inventing a new one), and produces an **event-centered rainfall context
window** (T-5 to T0 days, real IMD timestamps) for every mapped INDOFLOODS
event whose `Start Date` falls in 2015-2025.

Actual run output (this phase, against the real files):

- Events with `Start Date` in the IMD-covered window (2015-2025):
  **684** (of 4,548 total mapped events).
  events actually available for context extraction (i.e. valid,
  non-masked canonical-cell rainfall data existed within the window,
  not out-of-file-range or fully masked):
  see the script's own printed summary for the exact count, reproduced in
  `processed/indofloods/rainfall_context_preview.csv` (a preview output,
  not a production or "negative" dataset — no row in it is called a
  negative example anywhere in the script or this doc).
- Output is written only to `processed/indofloods/` and is explicitly
  documented, in the script's own docstring and in this file, as
  **rainfall context around known positive event dates**, not a negative
  or background dataset — building an actual negative/background sampling
  scheme is a Part 5 training-strategy question, not implemented here.

No production file, `backend/pipeline.py`, or grid definition was
modified to build this; the script reuses the canonical grid bounds
read-only, exactly as `scripts/map_indofloods_to_grid.py` did in Phase 5.
