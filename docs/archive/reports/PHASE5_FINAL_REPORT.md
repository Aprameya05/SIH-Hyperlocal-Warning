# INDOFLOODS Phase 5 — Forensic Data-Science Final Report

All figures below were produced by running `pandas`, `pdftotext`, `unzip`,
raw `.shp` struct parsing, and this phase's own scripts against the real
files in `data/`. Nothing was inferred from filenames or assumed from the
task brief.

1. **Exact source files inspected**: `data/metadata_indofloods.csv`,
   `data/catchment_characteristics_indofloods.csv`,
   `data/floodevents_indofloods.csv`,
   `data/precipitation_variables_indofloods.csv`,
   `data/catchments_shapefiles_indofloods.zip`,
   `data/variables_description_indofloods.pdf`.

2. **Exact schema of each**: see
   `docs/INDOFLOODS_FORENSIC_INVENTORY.md` Sections 1-6 for full
   column/dtype listings. Summary: metadata 214 rows x 18 cols; catchment
   characteristics 155 rows x 108 cols; flood events 4,548 rows x 13 cols;
   precipitation 4,548 rows x 11 cols; shapefiles 155 gauges x 5 files
   (.shp/.shx/.dbf/.prj/.cpg) = 775 files; PDF 1,017 extracted text lines.

3. **Number of gauges**: 214 (in `metadata_indofloods.csv`, all unique
   `GaugeID`).

4. **Number of catchments (distinct from gauges)**: 155 catchments have
   characteristics/shapefiles/flood events — a strict subset of the 214
   gauges (59 gauges are metadata-only, with no catchment data or events).

5. **Number of flood events**: 4,548, across the 155 catchment-having
   gauges.

6. **Temporal coverage**: gauge-level `Start_date`/`End_date` in metadata
   range as early as 1970 to as late as 2020 (not a single common window);
   flood events carry their own ISO 8601 `Start Date`/`End Date`.

7. **Spatial coverage**: 214 gauges span lat 8.16-30.5669, lon
   72.7917-91.5919 — all inside the canonical grid's S=6/N=37/W=68/E=98
   bounds. Mapped, the 214 gauges occupy 106 of the 992 canonical cells;
   the 4,548 flood events occupy 75 of those cells.

8. **CRS**: metadata lat/lon has no explicit CRS tag but is consistent
   with WGS84 degrees; the catchment shapefiles' `.prj` files were read
   directly and are all geographic WGS84
   (`GEOGCS[...,DATUM["D_WGS_1984",...]]`) — verified for all 155, not
   assumed. The canonical grid (`backend/pipeline.py`, `data/pan_india_grid.json`)
   is likewise plain lat/lon degrees.

9. **Mapping method selected and why**: point-in-cell using each gauge's
   own `Latitude`/`Longitude`, floored to its enclosing 1.0-degree
   canonical grid cell. Chosen because the gauge point is where the flood
   was physically observed, and because a real check (catchment
   vertex/bbox centroid vs. gauge point, computed from the actual `.shp`
   geometry for all 155 catchments with `pyshp`) showed the two disagree
   on cell assignment for 89/155 gauges (57.4%) — a majority-rate
   disagreement that made this a real decision, not a formality. Full
   reasoning and the caveat this leaves (catchment-wide variables
   attributed to a cell that may not represent most of the catchment's
   area) is in `docs/INDOFLOODS_FORENSIC_INVENTORY.md` Section 4.

10. **Mapping statistics**: 214/214 gauges MAPPED, 0 OUTSIDE_GRID, 0
    INVALID_COORDS. 106 distinct cells occupied; 48 cells receive more
    than one gauge (not treated as an error — e.g. cell `9.0_76.0` holds
    10 gauges). All 4,548 flood events successfully joined to a mapped
    cell via their `GaugeID`.

11. **Unmapped/ambiguous statistics**: 0 gauges unmapped or with invalid
    coordinates. 59 of 214 gauges (27.6%) are geolocatable but have no
    catchment characteristics or flood-event history to attach. The
    ambiguity that does exist is the gauge-point-vs-catchment-centroid
    disagreement noted in item 9 (57.4% of the 155 catchment-bearing
    gauges).

12. **Exact flood-event semantics** (from the PDF, Table 2, verified by
    text extraction): `Start Date`/`End Date` = when streamflow crossed
    above/back below the gauge's `Warning Level`. `Flood Type` =
    "Severe Flood" if `Peak Flood Level` exceeds the gauge's `Danger
    Level`, else "Flood" — a deterministic relabeling, not an independent
    observation. `Flood Volume (cumec)` = sum of daily discharge over the
    event. `Time to Peak` = days from start to peak; `Recession Time` =
    days from peak to end.

13. **Exact precipitation semantics** (PDF, Table 2, verified): `T1d` =
    daily precipitation (mm) one day before the flood's `Start Date`.
    `T2d`-`T10d` = cumulative 2-10 days' daily precipitation (mm) before
    `Start Date`. All values are strictly antecedent/pre-event (none
    include the event's own start-day rainfall), catchment-area averages
    derived from the EM-Earth 0.1-degree gridded product (Tang et al.,
    2022). Observed magnitude ranges: T1d 0-367.7 mm (mean 39.3), T10d
    0-2123.6 mm (mean 227.0).

14. **Exact catchment characteristics summary**: 108 columns per
    catchment (155 catchments) spanning stream-order/drainage-network
    topology (with confirmed structural, not random, NaNs for higher
    stream orders — e.g. 8th-order columns are NaN in all 155 rows, 4th
    order NaN in exactly the 62 rows with `Stream Order` < 4), morphometric
    shape/relief/drainage indices, Bioclim-style climate normals, and
    socioeconomic/exposure descriptors (GDP PPP and GDP-per-capita PPP for
    six snapshot years, HDI for six years, population count/density,
    night lights, road density, urban percentage, land cover/soil/
    lithology type). Full catalog: `docs/INDOFLOODS_FEATURE_CATALOG.md`.

15. **Potential susceptibility features**: stream-order/drainage-network
    topology, all morphometric shape/relief/drainage-density indices,
    Bioclim climate normals, and land cover/soil type/lithology type — all
    flagged STATIC SUSCEPTIBILITY in `docs/INDOFLOODS_LEAKAGE_AUDIT.md`.

16. **Variables that can support observed-event labels**: `floodevents_
    indofloods.csv`'s `Start Date` (as the label-anchoring timestamp) and
    the gauge->cell mapping — these can mark (cell, date) as a positive
    flash-flood/flood occurrence for the 75 cells with events. Nothing
    else in the dataset currently supplies negative examples (see item
    18/21).

17. **Proxy variables**: GDP PPP/GDP-per-capita PPP (all years), HDI (all
    years), night lights, population count/density, road density, urban
    percentage — these describe exposure/impact, not hazard likelihood,
    and are explicitly flagged not to be used as susceptibility features
    (`docs/INDOFLOODS_LEAKAGE_AUDIT.md`).

18. **Leakage findings**: (a) every non-`Start Date` field in
    `floodevents_indofloods.csv` (peak level/date, discharge, volume,
    duration, time-to-peak, recession, flood type) is computed from the
    completed event and must never be used as a predictor of that same
    event. (b) `Warning Level`/`Danger Level` define the very threshold
    used to construct the label and must not be used as predictor
    features. (c) The `Txd` precipitation columns are confirmed strictly
    antecedent (no event-day leakage) but exist only for already-known
    event dates, so they cannot supply the negative-class days a general
    label table needs. (d) Catchment characteristics exist only for
    gauges India chose to instrument and that already flooded — a
    selection-bias risk for any susceptibility model trained only on
    those 155.

19. **Exact derived files created** (all under `processed/indofloods/`,
    none touching the originals):
    `processed/indofloods/indofloods_grid_mapping.csv` (214 gauge rows:
    source_id, source_type, lat, lon, cell_id, cell_lat, cell_lon,
    mapping_method, distance_km, status) and
    `processed/indofloods/indofloods_grid_events.csv` (4,548 event rows:
    EventID, GaugeID, cell_id, cell_lat, cell_lon, mapping_status, Start
    Date, End Date, Flood Type), produced by
    `scripts/map_indofloods_to_grid.py`, confirmed byte-identical across
    two consecutive runs (deterministic, no randomness or wall-clock
    dependency).

20. **Exact tests and pass/fail counts**: `tests/test_indofloods_phase5.py`
    — 51/51 checks passed (source presence/checksums, CSV schemas, row
    counts, coordinate validity, shapefile CRS parsing, mapping-script
    determinism, valid-cell-ID and mapping-stat checks, event-join sample
    checks, and confirmation that `backend/pipeline.py` and the production
    JSONs are untouched). Full existing repo suite (24 `test_*.py` files,
    run via `--with-full-suite`): **20 passed, 4 failed** — the 4 failures
    are the pre-existing, known environment blockers (`test_himawari.py`,
    `test_segments.py`, `test_segments_v2.py`: `ModuleNotFoundError:
    No module named 'donfig'`; `test_nomads.py`: NOMADS proxy 403), not
    newly introduced by this phase. All INDOFLOODS-touching checksums and
    the guardrail test (`tests/test_canonical_forecast.py`) were verified
    to still pass after this phase's files were added.

21. **Can INDOFLOODS support a defensible observed flash-flood label on
    the 992-cell grid?** Partially, and not yet as a complete supervised
    label table. It supplies real, dated, geolocated positive examples
    (4,548 events, 75 of 992 cells) and a genuine static susceptibility
    feature set (155 of 992 cells' worth of catchments), but it has no
    negative (non-flood) examples and no continuous per-cell-per-day
    precipitation series — every `Txd` record exists only because a flood
    already happened there. See `docs/INDOFLOODS_LEAKAGE_AUDIT.md` for
    the full reasoning.

22. **What's still missing before it can influence the live flash-flood
    engine**: (a) a per-cell-per-day precipitation time series to
    construct comparable negative examples; (b) an explicit
    aggregation/tie-breaking rule for cells holding multiple gauges (up to
    10 in one cell); (c) a resolution of the gauge-point-vs-catchment-
    centroid mismatch (57.4% of catchments) for any catchment-wide
    variable attributed to a single cell; (d) a decision on how to treat
    the 59 gauges with coordinates but no catchment/event data; (e) none
    of this phase's derived files or reasoning have been wired into
    `backend/pipeline.py`, `canonical_forecast.json`, `forecast.json`, or
    `pan_india_grid.json` — confirmed by direct grep and checksum, per
    this phase's constraints.
