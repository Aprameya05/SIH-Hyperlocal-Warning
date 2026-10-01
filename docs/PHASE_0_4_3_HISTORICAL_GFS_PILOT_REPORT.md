# Phase 0.4.3 / 0.4.3A — Historical GFS Pilot Report

## STATUS = VALIDATED_FROM_ACTUAL_FILE

Two real historical GFS forecast-cycle files, downloaded by the project owner
from NCAR GDEX dataset d084001 onto their own machine and placed at
`data/external/historical_gfs/raw/`, have been inspected directly in this
phase:

- `gfs.0p25.2020071500.f003.grib2` (335,676,071 bytes)
- `gfs.0p25.2020071500.f006.grib2` (342,167,764 bytes)

`scripts/validate_historical_gfs_pilot.py` was rewritten this phase (Phase
0.4.3A) to fix two real bugs found when the project owner first ran it
against these files, then re-run against the actual files. It now reports
`STATUS = VALIDATED_FROM_ACTUAL_FILE`. Nothing below is inferred from
filenames or documentation where actual-file verification was possible; every
item is tagged by its real evidence source.

## What was wrong, and what was fixed (Phase 0.4.3A)

The project owner's first run reported `CANONICAL_GRID_NOT_FOUND` and a real
`cfgrib` error: `"multiple values for unique key, try re-open the file with
one of: filter_by_keys={'typeOfLevel': ...}"`. Both were genuine bugs in the
validator script, not problems with the downloaded files:

1. **`CANONICAL_GRID_NOT_FOUND`**: the script looked for a `"cells"` key in
   `data/pan_india_grid.json`. Direct inspection of that real production file
   shows its actual top-level keys are `generated_at_utc, gfs_cycle,
   gfs_fhour, grid_step_deg, bounds, n_cells, summary, grid_cells` — the cell
   array is under **`grid_cells`**, not `cells`, and individual cell dicts
   carry `lat`/`lon` plus hazard/feature values but **no stored `cell_id`**.
   Fixed by reading `grid_cells` and constructing cell IDs via this project's
   own existing `regrid.py::cell_id_for(lat, lon)` convention (reused, not
   reimplemented), which produced 992 unique IDs with no duplicates.

2. **The `cfgrib.open_dataset()` crash**: a real GFS GRIB2 file mixes many
   `typeOfLevel` groups (surface, isobaricInhPa, heightAboveGround,
   atmosphereSingleLayer, pressureFromGroundLayer, planetaryBoundaryLayer,
   etc. — 158 distinct (shortName, typeOfLevel) groups in each file here), so
   asking cfgrib to build one unified `xarray.Dataset` from the whole file is
   inherently ambiguous and fails. Fixed by enumerating every GRIB message
   individually via raw ecCodes calls
   (`eccodes.codes_grib_new_from_file()` + `eccodes.codes_get(gid, key)` per
   message), never calling `cfgrib.open_dataset()` on the whole file, and
   never passing `filter_by_keys` to silently collapse the ambiguity.

3. **A third bug found and fixed while re-running after the above two**: the
   pilot mapping step initially passed `source_name=f"gdex_d084001_{shortName}"`
   into `regrid.py::regrid_to_cell()`. That module's `SUPPORTED_SOURCES` is a
   fixed tuple — `("gfs", "himawari", "dem", "insat", "imdaa")` — so every
   call silently hit the "unknown source" branch and returned
   `method_used="none"`, `available=False`, `value=None` for all 25 pilot
   rows (5 variables × 5 cells). This was a real bug in this script's call
   convention, not a missing-data finding about the GFS archive. Fixed by
   passing `source_name="gfs"` (accurate — this data is genuinely GFS, same
   model family as the live pipeline) and recording the archive identity
   separately as `archive_source: "NCAR_GDEX_d084001"` on each mapping row, so
   the historical-archive provenance is preserved without overloading
   `regrid.py`'s fixed source-name vocabulary.

## VERIFIED_FROM_ACTUAL_FILE (this phase, direct ecCodes/GRIB2 inspection)

### 1–2. Files found and GRIB structure
Both files: **588 GRIB messages, 158 unique (shortName, typeOfLevel) groups**
each. Confirmed by full per-message enumeration, not a partial scan.

### 3. Forecast semantics (from actual GRIB metadata keys, not filenames)

| File | `dataDate`/`dataTime` (init) | `forecastTime` (lead) | `validityDate`/`validityTime` (valid) |
|---|---|---|---|
| f003 | 20200715 / 0000 UTC | 3 h | 20200715 / 0300 UTC |
| f006 | 20200715 / 0000 UTC | 6 h | 20200715 / 0600 UTC |

This matches the filename-implied `2020-07-15 00Z` init and +3h/+6h leads
documented as native in `docs/PHASE_0_4_2_HISTORICAL_GFS_FEASIBILITY.md`, but
is now confirmed from the actual `dataDate`, `dataTime`, `forecastTime`,
`validityDate`, `validityTime` GRIB keys rather than inferred from the
filename alone.

### 4. Variables: DIRECTLY_PRESENT / DERIVABLE / NOT_FOUND (both files identical)

| Required variable | Status | Exact GRIB evidence |
|---|---|---|
| 2m temperature | DIRECTLY_PRESENT | `2t` @ `heightAboveGround` level 2, units K (1 message) |
| Relative humidity | DIRECTLY_PRESENT | `r`/`2r` across 41 messages at multiple levels (`heightAboveGround`, `isobaricInhPa`, etc.) |
| Specific humidity | DIRECTLY_PRESENT | `2sh`/`q` across 3 messages (`heightAboveGround` 2m and 80m, `pressureFromGroundLayer`) |
| Surface/mean-sea-level pressure | DIRECTLY_PRESENT | `sp`/`mslet`/`prmsl`-family, 3 messages |
| Geopotential height | DIRECTLY_PRESENT | `gh`, 40 messages across pressure levels |
| U wind | DIRECTLY_PRESENT | `u`, 48 messages, including `isobaricInhPa` level **850** (confirmed present, used in the pilot mapping below), plus `heightAboveGround` (10/20/30/40/50/80/100 m), `planetaryBoundaryLayer`, `heightAboveSea` |
| V wind | DIRECTLY_PRESENT | `v`, 48 messages, same level set as U, including `isobaricInhPa` level **850** |
| CAPE | **DIRECTLY_PRESENT** — resolves the "UNCONFIRMED" tag carried since Phase 0.4.2 | `cape` @ `surface` level 0 (J/kg), plus `pressureFromGroundLayer` levels 18000 and 25500 — 3 messages total |
| CIN | **DIRECTLY_PRESENT** — resolves the "UNCONFIRMED" tag carried since Phase 0.4.2 | `cin` @ `surface` level 0 (J/kg), plus `pressureFromGroundLayer` levels 18000 and 25500 — 3 messages total |
| Precipitable water | DIRECTLY_PRESENT | `pwat` @ `atmosphereSingleLayer` level 0, units kg/m², 1 message |
| Precipitation | DIRECTLY_PRESENT, **but under different shortNames than the live pipeline's "apcp" naming** | `tp` (total precipitation) and `acpcp` (convective precip) @ `surface`, units kg/m² (accumulation-equivalent), plus `prate` (precip rate) @ `surface`, units kg/m²/s — 6 messages total. No message with shortName exactly `apcp` was found in either file; the live pipeline's `apcp_mm` field name (seen in `data/pan_india_grid.json`) does not have a literal `apcp`-shortName counterpart in this archive. **This naming difference is reported here, not silently reconciled** — a future phase would need to decide whether `tp` (total precip accumulation) is the correct analog, since `apcp` and `tp` represent the same physical quantity (accumulated precipitation) under different GRIB2 template/table conventions, but this script does not assert that equivalence as verified. |
| Wind shear (850–500 hPa) | DERIVABLE (not a discrete GRIB field) | Derivable from U/V at `isobaricInhPa` levels 850 and 500, both confirmed present — same derivation method already used by the live pipeline. Not independently computed in this phase; only the presence of its U/V inputs was confirmed. |

### 5. Spatial grid (confirmed by direct ecCodes grid-definition keys)

Both files: **Nx=1440, Ny=721** (global 0.25° grid), latitude **90.0 → -90.0**
(descending), longitude **0.0 → 359.75** (0–360° convention, not -180/180°),
`d_lat`=`d_lon`=0.25°. This is a real structural fact to carry forward: any
future code reusing this archive must convert longitude convention (e.g.
India's 68–98°E stays positive and needs no wraparound, but this is not true
for all regions) before comparing against the live pipeline's own GFS grid
handling.

The Bengaluru-area bounding box (10–16°N, 74–80°E) was confirmed to exist in
both files by a real, small extraction: **49 of the canonical grid's 992
cells fall inside that bbox**, and real GRIB values were successfully
extracted there (see pilot mapping below) — not merely assumed present
because the file is global.

### 6. Canonical grid used (confirmed, not redefined)

- Source file: `data/pan_india_grid.json` (the actual production artifact,
  unmodified, read-only).
- Key used: `grid_cells` (992 entries).
- `len(grid_cells) == 992`: **True**.
- Unique cell IDs via `regrid.py::cell_id_for(lat, lon)`: **992 unique IDs,
  zero duplicates**.
- `bounds`: `{south: 6.0, north: 37.0, west: 68.0, east: 98.0}`, matching the
  documented canonical grid definition.
- No new grid, no redefinition of the 992 cells — this validator only reads
  the existing production file.

### 7. Small real pilot mapping (Bengaluru-area, 5 cells × 5 variables, f003 only)

Using this project's own existing `regrid.py::regrid_to_cell()` /
`cell_id_for()` (reused, not reimplemented) against **real extracted GRIB
values** — no synthetic data anywhere in this mapping:

- Pilot cells: first 5 of the 49 canonical cells inside bbox 10–16°N/74–80°E
  — `IND_10.0_74.0`, `IND_10.0_75.0`, `IND_10.0_76.0`, `IND_10.0_77.0`,
  `IND_10.0_78.0`.
- Variables mapped: 2m temperature (`2t`), CAPE (`cape` @ surface), PWAT
  (`pwat`), U850 (`u` @ isobaricInhPa 850), V850 (`v` @ isobaricInhPa 850).
- Source: GFS 0.25° grid (archive: NCAR GDEX d084001), init 2020-07-15 00:00
  UTC, valid 2020-07-15 03:00 UTC, lead 3h.
- Method: nearest-neighbor (`method_used="nearest"`, matching
  `method_requested="nearest"` — no silent fallback occurred).
- All 25 mapped values are real, non-missing (`missing=false`), with
  `distance_deg=0.0` for every cell (the 0.25° GFS source grid contains exact
  integer-degree points at each 1.0° target-cell center, so nearest-neighbor
  snapping is exact here — this is a property of this specific bbox's
  integer-degree cell centers, not a general guarantee for all 992 cells).
- Example real values (cell `IND_10.0_74.0`, f003, valid 2020-07-15 03:00
  UTC): 2m temp = 301.49 K (≈28.3°C), CAPE = 921 J/kg, PWAT = 59.6 kg/m²,
  U850 = 11.00 m/s, V850 = -8.17 m/s. Full 25-row table is in the script's
  JSON output (`pilot_mapping_f003`), not reproduced in full here.
- Each row carries full provenance: variable, level, units, archive_source,
  target_cell_id, target_lat/lon, method_used, method_requested,
  missing_reason, distance_deg, init_utc, valid_utc, lead_hours, value,
  missing flag — per the brief's requirement.

### 8. Scope of what was and was NOT validated

**Only the 49-cell Bengaluru-area subset, 5 variables, 1 forecast cycle
(2020-07-15 00Z), 1 lead (f003), was actually mapped and checked.** This
report does **not** claim full 992-cell pan-India compatibility — the
remaining ~943 cells, the other required variables (RH, specific humidity,
surface pressure, geopotential height, derived shear), and the f006 lead were
confirmed present in the raw GRIB structure (Section 4) but were **not**
individually regridded and value-checked in this pilot. A global 0.25° file
containing a variable everywhere does not by itself prove every one of the
992 canonical cells will regrid cleanly (e.g. coastal/boundary cells,
cells near the 0°/360° seam if any existed in this domain, or the
precipitation shortName mismatch noted above) — those remain
**NOT_YET_VERIFIED**.

## VERIFIED_FROM_DOCUMENTATION (carried from Phase 0.4.2/0.4.3, not re-verified against a file this phase)

- Overall archive coverage (2015-01-15–present, 4 cycles/day, 3-hourly steps
  to 240h), license (CC-BY-4.0), and the three access mechanisms (direct
  HTTPS, THREDDS/OPeNDAP, login-gated subset tool) remain as documented in
  `docs/PHASE_0_4_2_HISTORICAL_GFS_FEASIBILITY.md` and
  `docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md` — unaffected by this
  phase's file-level inspection.

## NOT_YET_VERIFIED

- Full 992-cell regridding and value-sanity-checking across all of pan-India
  (only the 49-cell Bengaluru subset was mapped).
- The f006 file's pilot mapping (only f003 was run through the mapping step;
  f006's GRIB structure was independently confirmed identical in shape, but
  its own values were not separately mapped).
- Whether `tp`/`acpcp`/`prate` in this archive are numerically equivalent,
  under matching accumulation windows, to whatever field populates the live
  pipeline's `apcp_mm`.
- Whether bilinear interpolation (vs. the nearest-neighbor used here) would
  materially change values for cells that are not already grid-aligned with
  the 0.25° source (this pilot's 1.0°-cell-center alignment happened to make
  nearest-neighbor exact; that will not hold everywhere).
- Any compatibility check against the live pipeline's actual column names,
  scaling, or unit conventions beyond what is stated in
  `docs/PHASE_0_4_3_HISTORICAL_GFS_ACQUISITION_GUIDE.md` Section 4 (that
  guide's variable table itself was written before any file existed and is
  now partially superseded by the DIRECTLY_PRESENT confirmations above for
  CAPE/CIN).

## Tests run this phase

- `scripts/validate_historical_gfs_pilot.py` (no `--dir`/`--bbox` override):
  runs cleanly end-to-end against the two real files, exits 0, status
  `VALIDATED_FROM_ACTUAL_FILE`.
- Full existing test suite (`pytest .`, excluding the 4 pre-existing
  environment-only collection errors — `test_himawari.py`,
  `test_segments.py`, `test_segments_v2.py` due to a missing `donfig` module
  used by `satpy`, and `test_nomads.py` due to this sandbox's proxy returning
  403 for `nomads.ncep.noaa.gov`, all pre-existing and unrelated to this
  phase): **151 passed, 0 failed** — identical pass count to the established
  baseline from Phase 0.1. No freshness test was weakened or skipped.
- No dedicated unit test file exists yet for `validate_historical_gfs_pilot.py`
  itself (none was found under `tests/` or the repo root); this phase
  validated it by direct execution against the real files rather than adding
  new test infrastructure, per the brief's scope (only the validator script,
  helpers, and this report were in scope — not new test files).

## Scope confirmation

- **Files modified this phase**: `scripts/validate_historical_gfs_pilot.py`
  (fixed canonical-grid key, replaced `cfgrib.open_dataset()` with per-message
  ecCodes enumeration, fixed the `regrid_to_cell` source_name bug) and this
  report. Nothing else.
- **Not touched**: `backend/pipeline.py`, `gfs_fetcher.py`,
  `pan_india_gfs_fetcher.py`, `forecast_action.py`, any schema, the canonical
  grid file (`data/pan_india_grid.json`, read-only), the frontend, and all
  GitHub Actions workflows.
- **No files were (re-)downloaded** this phase — the two real GRIB2 files
  were supplied by the project owner from a prior download and only read.
- No model was trained. Nothing was deployed, committed, or pushed.
