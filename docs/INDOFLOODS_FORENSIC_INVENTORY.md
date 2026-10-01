# INDOFLOODS Forensic Inventory — Phase 5

Source: Kuntla & Saharia (IIT Delhi), "INDOFLOODS: A Comprehensive Database
for Flood Events in India Enhanced with Catchment Attributes"
(`data/variables_description_indofloods.pdf`, read with `pdftotext`).

Builds on `docs/INDOFLOODS_DATA_INVENTORY.md` (Phase 4.5, inspection-only).
That doc's join-key findings (GaugeID, EventID = GaugeID + sequence number,
real lat/lon in metadata) were re-verified here and hold. This doc corrects
one number from that earlier pass: `metadata_indofloods.csv` has **214 data
rows**, not "up to 219 lines" — the CSV's raw line count (219 incl. header)
is inflated by quoted multi-line fields; `pandas.read_csv` parses it to 214
records, each with a unique `GaugeID`.

All statistics below were produced by actually running `pandas`, `pdftotext`,
`unzip`, and a raw `.shp` header read against the files in `data/`; none are
inferred from filenames or the task description.

## 1. `metadata_indofloods.csv`

- Size: 40,058 bytes. Rows: **214** (header + 214, `pandas`-parsed). Columns: **18**.
- Columns / dtypes (pandas-inferred):
  `GaugeID` (str), `Warning Level` (float64), `Danger Level` (float64),
  `Station` (str), `Latitude` (float64), `Longitude` (float64),
  `River Name/ Tributory/ SubTributory` (str), `Basin` (str), `State` (str),
  `Start_date` (str), `End_date` (str), `Level_Entries` (int64),
  `Streamflow_Entries` (int64), `Privacy` (str), `Source Catchment Area`
  (float64), `Catchment Area` (float64), `Area variation (%)` (float64),
  `Reliability` (str).
- `GaugeID`: unique in all 214 rows (0 duplicates).
- Temporal coverage: per-gauge `Start_date`/`End_date` (not a fixed common
  range across gauges).
- Spatial identifier: `Latitude`/`Longitude`, decimal degrees.
  Range observed: lat 8.16 to 30.5669, lon 72.7917 to 91.5919. 0 missing
  values in either column. All 214 rows fall inside the canonical grid's
  bounds (see Section 3).
- Per the PDF (Table 1): `GaugeID` is a unique per-gauge station ID.
  `Warning Level` / `Danger Level` are streamflow levels (m) above which
  flow is classified "Flood" / "Severe Flood" respectively — these are the
  thresholds `floodevents_indofloods.csv`'s `Flood Type` field is derived
  from.
- Usable for: gauge identification, gauge geolocation, per-gauge flood/severe
  thresholds. Not usable alone for any spatial polygon (no catchment
  geometry).

## 2. `catchment_characteristics_indofloods.csv`

- Size: 146,653 bytes. Rows: **155**. Columns: **108**.
- Keyed by `GaugeID`; 155/155 unique, 0 duplicates.
- No latitude/longitude of its own — must be joined via `GaugeID` to
  `metadata_indofloods.csv` for a spatial location.
- Column groups (all 108 columns enumerated in
  `docs/INDOFLOODS_FEATURE_CATALOG.md`): stream-order/drainage-network
  topology (bifurcation ratios, stream lengths/counts by order 1-8),
  morphometric indices (form factor, circularity ratio, elongation ratio,
  drainage density, ruggedness number, etc.), Bioclim-style climate normals
  (temperature/precipitation seasonality, wettest/driest/warmest/coldest
  month and quarter), and socioeconomic descriptors (GDP PPP and GDP per
  capita PPP for 1990/1995/2000/2005/2010/2015, HDI for the same years,
  population count/density, night lights, road density, urban percentage,
  Koppen-Geiger climate type, dominant land cover / soil type / lithology
  type).
- Structural (not random) missingness confirmed by direct check: e.g. all
  155 rows have a `Stream Order` value (1-7 observed: 4/16/42/32/41/15/5
  rows respectively); `Fourthorder Streams Length` is NaN in exactly the 62
  rows whose `Stream Order` < 4, and the same alignment holds for every
  higher stream-order column pair inspected (5th/6th/7th/8th order columns
  are NaN in exactly the rows lacking that many stream orders; 8th-order
  columns are NaN in all 155 rows because no catchment in this set reaches
  8th order). These are catchments that structurally lack that stream
  order, not missing measurements, and must be treated as such (not
  imputed as 0 or dropped as "bad data") in any future feature pipeline.
- Usable for: a documented static flash-flood **susceptibility** feature
  set (terrain/drainage/climate/exposure), once joined to a grid cell via
  `metadata_indofloods.csv`. Not usable for time-varying prediction — every
  field here is a static, catchment-level constant.

## 3. `catchments_shapefiles_indofloods.zip`

- Size: 664,787 bytes. Extracted: **775 files** in one directory
  (`catchments_shapefiles_indofloods/`), i.e. 155 gauges x 5 files
  (`.shp`, `.shx`, `.dbf`, `.prj`, `.cpg`) = 775 (not 776 — no separate
  directory entry was present in the actual archive).
- 155 `.shp` files, one per `GaugeID` in `catchment_characteristics_indofloods.csv`
  (filenames are the `GaugeID`, e.g. `INDOFLOODS-gauge-650.shp`).
- Shape type read directly from a `.shp` header (struct-unpacked, byte
  offset 32-36): **type 5 = Polygon** for the sample checked
  (`INDOFLOODS-gauge-650`), bounding box lon 76.24-77.93, lat 20.14-21.67 —
  plausible India-region decimal degrees.
- CRS, read directly from the `.prj` files (not assumed): all 155 `.prj`
  files are geographic WGS84 —
  `GEOGCS[...,DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],...,UNIT["Degree",...]]`.
  Two distinct `.prj` byte strings exist (116 files name the GCS
  `"GCS_unknown"`, 39 files name it `"GCS_WGS_1984"`), but the datum,
  spheroid and unit are identical in both — i.e. all 155 catchments are in
  the same CRS (geographic WGS84, decimal degrees, equivalent to EPSG:4326)
  and only the GCS label string differs cosmetically.
- Usable for: real catchment-polygon geometry, per gauge, in WGS84
  degrees. See Section 4 for whether polygon overlap changes the grid
  mapping versus a simple gauge-point method.

## 4. `floodevents_indofloods.csv`

- Size: 465,179 bytes. Rows: **4,548**. Columns: **13**.
- Columns: `EventID, Start Date, End Date, Peak Flood Level (m), Peak FL
  Date, Num Peak FL, Peak Discharge Q (cumec), Peak Discharge Date, Flood
  Volume (cumec), Event Duration (days), Time to Peak (days), Recession
  Time (day), Flood Type`.
- `EventID` is unique across all 4,548 rows (0 duplicates), format
  `<GaugeID>-<sequence>` (PDF: e.g. `INDOFLOODS-gauge-118-10`, "10" is the
  per-station serial number). Splitting on the last `-` recovers exactly
  155 distinct `GaugeID`s, all present in both `metadata_indofloods.csv`
  and `catchment_characteristics_indofloods.csv`.
- Per the PDF (Table 2): `Start Date`/`End Date` are ISO 8601 dates when
  streamflow crossed the gauge's `Warning Level` threshold (start) and
  dropped back below it (end). `Flood Type` is derived directly from
  `metadata_indofloods.csv`'s `Danger Level`: "Severe Flood" if `Peak Flood
  Level` exceeds the gauge's `Danger Level`, else "Flood". `Flood Volume
  (cumec)` is the sum of daily discharge over the event. `Time to Peak
  (days)` = days from event start to peak; `Recession Time (day)` = days
  from peak to event end.
- Usable for: the actual observed-flood-event record per gauge (dates,
  severity, magnitude). This is the closest thing in the dataset to a
  ground-truth flash-flood/flood label, but it is anchored to a
  **gauge**, not a grid cell or calendar day series (see the leakage audit,
  Section 9-equivalent doc, for what's still missing to build a
  cell x day label table).

## 5. `precipitation_variables_indofloods.csv`

- Size: 672,999 bytes. Rows: **4,548** (identical `EventID` set to
  `floodevents_indofloods.csv`, verified: `set(EventID)` equal between the
  two files). Columns: **11**: `EventID, T1d, T2d, ..., T10d`.
- Per the PDF (Table 2, "Event-scale precipitation" section, page ~6),
  confirmed by direct extraction (not assumed):
  - **`T1d`** = "Daily precipitation a day before the flood start date.
    Units: mm." I.e., **strictly antecedent, pre-event** rainfall for the
    single day immediately preceding the event's `Start Date` — it does
    NOT include the event's own start-day rainfall.
  - **`T2d`-`T10d`** = "Cumulative 2 days, 3 days, ..., 10 days daily
    precipitation before the flood start date. Units: mm." — cumulative
    antecedent rainfall over the 2-10 days preceding `Start Date`,
    likewise excluding the event day itself.
  - Source: "corrected mean of EM-Earth daily precipitation probabilistic
    estimates" at 0.1 degree resolution (Tang et al., 2022), averaged over
    each catchment polygon.
  - This resolves the open question left by the Phase 4.5 inventory: units
    are confirmed as **mm** from the PDF text, not inferred from magnitude.
- Usable for: genuine antecedent-rainfall predictor variables anchored to
  a known flood event's start date. NOT usable, on their own, to build a
  general per-cell-per-day precipitation time series (see the leakage
  audit) — they exist only for the 4,548 already-known flood-event dates,
  not for arbitrary days.

## 6. `variables_description_indofloods.pdf`

- Size: 185,357 bytes. Extracted 1,017 lines of text with `pdftotext`
  (no OCR needed — the PDF is text-based).
- Confirms authorship (Kuntla & Saharia, IIT Delhi Civil Engineering / AI
  School) and three tables: Table 1 (metadata fields), Table 2
  (flood-event and precipitation variables), Table 3 (catchment-scale
  variables). All variable definitions quoted above were taken from this
  extraction, not guessed.

## Coordinate / Spatial Reference Analysis

- Point data (`metadata_indofloods.csv`): `Latitude`/`Longitude` columns,
  decimal degrees, no stated CRS in the CSV itself, but consistent with
  WGS84 lat/lon (matches the shapefile CRS and India's real geography).
- Polygon data (shapefiles): verified WGS84 geographic
  (`GEOGCS[...D_WGS_1984...]`) from the actual `.prj` files, decimal
  degrees — see Section 3.
- Geographic bounds actually observed in `metadata_indofloods.csv`:
  lat [8.16, 30.5669], lon [72.7917, 91.5919] — all 214 gauges within
  India's extent.
- Canonical grid bounds (read from `data/pan_india_grid.json` and
  `backend/pipeline.py`'s `BOUNDS`/`APPLICATION_GRID_STEP`, not modified):
  S=6.0, N=37.0, W=68.0, E=98.0, 1.0-degree step, 32 lat x 31 lon = 992
  cells (verified: `pan_india_grid.json` contains exactly 992 `grid_cells`
  entries with 32 distinct lat values 6..37 and 31 distinct lon values
  68..98, both inclusive).
- Result: **all 214 of 214 gauges** fall inside the canonical grid's
  bounds. 0 missing coordinates, 0 out-of-bounds coordinates.

## 4. Spatial mapping method: point-in-cell vs. catchment polygon

**Decision: point-in-cell using the gauge's own `Latitude`/`Longitude`
from `metadata_indofloods.csv`**, floored to the enclosing 1.0-degree
canonical grid cell.

Verification performed before committing to this (not assumed):

- Catchment area distribution (`metadata_indofloods.csv`'s `Catchment
  Area`, km^2, n=214): min 24.7, 25th pct 1,500, median 6,928, 75th pct
  25,620, max 305,684. A canonical 1.0-degree grid cell near India's
  latitudes covers on the order of 10,000-12,000 km^2. So the median
  catchment (~6,900 km^2) is smaller than a cell, but a large fraction of
  catchments (roughly the top quartile, >25,000 km^2) span several cells —
  this is not a case where "the catchment is obviously much bigger/smaller
  than a cell" in one direction.
- Because catchments frequently span multiple cells, whether "gauge point"
  vs. "catchment polygon" produces a different cell assignment was
  checked directly, not assumed: for all 155 gauges that have both a
  metadata lat/lon and a shapefile, the vertex-average centroid and the
  bounding-box centroid of each catchment polygon were computed from the
  actual `.shp` geometry (`pyshp`) and mapped to a grid cell the same way
  as the gauge point.
  - Gauge-point cell vs. polygon-vertex-centroid cell: **disagree for 89 of
    155 gauges (57.4%)**.
  - Gauge-point cell vs. polygon-bbox-centroid cell: disagree for 91 of 155
    (58.7%).
  - Script used: see `/tmp` scratch script referenced in this phase's
    working notes (not committed to the repo — a one-off check, not a
    deliverable); the result CSV logic is reproduced in this doc's numbers
    above and can be re-derived from the shapefiles and
    `scripts/map_indofloods_to_grid.py`'s cell math.
- **This is a real, majority-rate disagreement** — it means "point vs.
  polygon" is not a cosmetic choice here. The reasoning for still using
  the gauge point rather than a catchment centroid or overlap method:
  1. A gauge is a fixed, physical streamflow-measurement location — the
     flood event in `floodevents_indofloods.csv` was **observed at that
     point**, not "somewhere in the catchment." Assigning the event to the
     cell containing the gauge keeps the label attached to where the
     measurement actually happened.
  2. A catchment is an elongated upstream drainage basin, not a
     cell-shaped area — its centroid is a geometric average of far-flung
     upstream terrain, often nowhere near where flooding is reported or
     the population/infrastructure the operational grid cares about sits.
     A centroid-based or full polygon-overlap assignment would frequently
     assign a flood event to a cell that contains none of the actual gauge
     and reflects only where the basin geometrically balances.
  3. Full polygon-overlap (splitting a flood record's weight across every
     grid cell the catchment intersects) is not attempted in this phase
     because it would require deciding how to apportion event severity /
     precipitation across cells — an actual labeling decision, which
     Phase 5's constraints explicitly defer ("do not build a training-ready
     label table yet").
  - The known limitation this leaves undocumented-away: for the ~57% of
    gauges where polygon centroid and gauge point disagree, catchment-wide
    variables (e.g. the antecedent precipitation in
    `precipitation_variables_indofloods.csv`, which is itself a
    catchment-area average) are being attributed to a single grid cell
    that may not spatially coincide with most of the catchment's area.
    This is flagged, not resolved, here and in the leakage audit.

## Row/entity counts summary (all pandas-verified)

- Gauges in `metadata_indofloods.csv`: 214.
- Gauges with catchment characteristics: 155 (exact same 155 as have flood
  events — `catchment_characteristics_indofloods.csv`'s `GaugeID` set
  equals `floodevents_indofloods.csv`'s derived `GaugeID` set exactly).
- Gauges in metadata but with **no** catchment characteristics and **no**
  flood events: 59 (214 - 155).
- Gauges in catchment characteristics/flood events but missing from
  metadata: 0 (no orphans in that direction).
- Flood events: 4,548, across 155 distinct gauges.
- Precipitation records: 4,548, 1:1 with flood events by `EventID`.
- Catchment shapefiles: 155, 1:1 with `catchment_characteristics_indofloods.csv`'s
  `GaugeID`s.
