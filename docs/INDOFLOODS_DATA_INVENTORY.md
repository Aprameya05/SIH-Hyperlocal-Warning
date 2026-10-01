# INDOFLOODS Data Inventory (inspection only, no integration) — 2026-09-30

Source archive: `Downloads/14584655.zip` on the user's PC (2,185,271 bytes), located via the device bridge and inspected directly. All 6 expected files were present with their expected names — nothing was assumed.

Per this phase's explicit instruction, this is an inventory only. No flood labels were built, no catchment-to-cell mapping was created, no hazard probability was touched.

## Files now in `data/`

| File | Size | Status |
|---|---|---|
| `floodevents_indofloods.csv` | 469,728 (repo, LF) / 465,179 archive is CRLF-identical content | already present, content-verified identical (line-ending only difference — archive is CRLF, repo copy is LF-normalized; not re-copied, per "do not overwrite without verification") |
| `precipitation_variables_indofloods.csv` | 677,548 / 672,999 | same as above — content-identical, not re-copied |
| `catchment_characteristics_indofloods.csv` | 146,653 | **newly copied**, checksum-verified against archive |
| `catchments_shapefiles_indofloods.zip` | 664,787 | **newly copied**, checksum-verified against archive |
| `metadata_indofloods.csv` | 40,058 | **newly copied**, checksum-verified against archive |
| `variables_description_indofloods.pdf` | 185,357 | **newly copied**, checksum-verified against archive |

## Per-file structure (inspected directly, not assumed)

### `metadata_indofloods.csv` — 214 rows × 18 columns
`GaugeID, Warning Level, Danger Level, Station, Latitude, Longitude, River Name/Tributory/SubTributory, Basin, State, Start_date, End_date, Level_Entries, Streamflow_Entries, Privacy, Source Catchment Area, Catchment Area, Area variation (%), Reliability`

**This is the file that was missing before and is the actual unlock for spatial work**: it carries real `Latitude`/`Longitude` per `GaugeID`, plus station name, river/basin, state, and the gauge's operational date range. This is what a future Phase 4 would need to spatially join INDOFLOODS gauges to the canonical 992-cell grid.

Spatial identifier: `GaugeID` (matches the `GaugeID` used in `catchment_characteristics_indofloods.csv` and the `EventID` prefix in `floodevents_indofloods.csv`, e.g. `INDOFLOODS-gauge-394`). Temporal coverage: `Start_date`/`End_date` per gauge, spanning from as early as 1970 to as late as 2020 across different gauges (not a fixed common range).

### `catchment_characteristics_indofloods.csv` — 155 rows × 108 columns
Keyed by `GaugeID`. **No latitude/longitude of its own** — purely geomorphological, climatological, and socioeconomic catchment attributes: stream order and drainage-network topology (bifurcation ratios, stream lengths by order), morphometric indices (form factor, circularity, elongation ratio, drainage density, ruggedness number), climate normals (temperature/precipitation seasonality, wettest/driest month and quarter), and non-hydrological catchment descriptors (GDP PPP by year, HDI by year, population count/density, night lights, road density, urban percentage, dominant land cover/soil type/lithology).

What this appears usable for: a genuine, documented flash-flood **susceptibility** feature set (terrain/drainage/climate-based), if joined to a spatial location via `metadata_indofloods.csv`'s `GaugeID` → lat/lon.

What cannot be concluded yet: whether these 108 columns are directly usable as model features without further cleaning (several show `NaN` for higher stream orders where a catchment simply has no 5th/6th/7th-order streams — that's a real structural absence, not missing data, and would need to be distinguished from genuine missingness before any future modeling).

### `floodevents_indofloods.csv` — 4,548 rows (already known from Phase 4.5)
`EventID, Start Date, End Date, Peak Flood Level (m), Peak FL Date, Num Peak FL, Peak Discharge Q (cumec), Peak Discharge Date, Flood Volume (cumec), Event Duration (days), Time to Peak (days), Recession Time (day), Flood Type`. `EventID` embeds the `GaugeID` as a prefix (e.g. `INDOFLOODS-gauge-1010-1`) plus a per-gauge event sequence number — this is how individual flood events join back to a gauge, and (via `metadata_indofloods.csv`) to a real lat/lon.

### `precipitation_variables_indofloods.csv` — keyed by `EventID`
Columns `T1d` through `T10d` — antecedent cumulative rainfall (mm, inferred from magnitude and column naming; not stated as a unit anywhere inspected yet) over the 1 to 10 days preceding each flood event's `EventID`. Real, per-event precipitation predictor variables, not a proxy.

### `catchments_shapefiles_indofloods.zip` — 776 files
One shapefile set (`.shp`, `.shx`, `.dbf`, `.prj`, `.cpg`) per gauge, 155 gauges × 5 files + 1 directory entry = 776. Real catchment boundary polygons, in whatever CRS the `.prj` files specify (not yet parsed — would need `pyshp`/`geopandas` to read, neither of which was invoked this phase since the instruction was inspection-only). This is the file that would support a real catchment-polygon-based spatial join to the canonical grid, more precise than a gauge-point-plus-radius approach.

### `variables_description_indofloods.pdf` — 185,357 bytes, text-extractable
Confirmed readable (`pdftotext` succeeded). First page identifies the source publication: Kuntla & Saharia, IIT Delhi, "INDOFLOODS: A Comprehensive Database for Flood Events in India Enhanced with Catchment Attributes," with three documented tables (metadata fields, flood-event variables, catchment-scale variables). Not fully parsed this phase — inventory only.

## What this inventory does NOT establish (left for the real Phase 4)

- Whether `metadata_indofloods.csv`'s gauge coordinates fall within the canonical grid's 992-cell bounds (`S:6,N:37,W:68,E:98`) — not checked this phase.
- Whether a gauge-point-radius join or a real catchment-polygon join (via the shapefiles) is the right spatial method — not decided this phase.
- Any flood label, susceptibility score, or coefficient derived from this data — none was built.
- Units for `precipitation_variables_indofloods.csv`'s `T1d`...`T10d` columns — inferred as millimeters from magnitude, not confirmed against the PDF's Table 2 description text (not yet fully read).

This inventory is a map of what exists, not a judgment on how to use it. Integration is explicitly deferred to a future Phase 4, per this phase's instruction.
