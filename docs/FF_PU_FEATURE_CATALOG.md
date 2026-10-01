# FF PU Feature Catalog — Phase 5.7

Research-only. Feeds `processed/ff_pu/ff_pu_training_table.csv`, built by
`scripts/build_ff_pu_dataset.py`. Does not touch production hazard
coefficients or `backend/pipeline.py`.

## 0. Date semantics (documented decision)

`date` in the training table = the flood's `Start Date` as recorded in
INDOFLOODS (the day the flood event was first recorded at that gauge).

**Same-day (T) rainfall is excluded from every rainfall feature.** A
flash-flood trigger model is meant to fire *before or during* the event
develops, using rainfall observed up to the prior day. IMD's gridded
product used here (`imd_rain/rain/*.grd`) is a daily accumulation that is
only fully known after the day ends (T+1 availability in practice), so
including `date`'s own daily total in a feature would use information not
actually available at a realistic issue time. All `rain_Xd` features
therefore sum over `[T-w, T-1]`, strictly prior to the label date. This is
more conservative than Phase 5.5's `rainfall_context_preview.csv`, whose
antecedent window included T0 for a purely descriptive preview, not a
predictive feature set — the two are not in conflict, they serve different
purposes.

## 1. Rainfall features (dynamic, from `imd_rain/rain/{2015..2025}.grd`)

All computed from the same per-cell area-mean daily series used by
`scripts/prepare_indofloods_rainfall_context.py` (0.25-degree IMD points
inside the 1.0-degree canonical cell, averaged, NaN where IMD marks the
day as masked, i.e. `<= -998.0`).

| Feature | Formula | Units | Leakage status |
|---|---|---|---|
| `rain_1d` | rainfall on day T-1 | mm | SAFE |
| `rain_3d` | sum of rainfall over [T-3, T-1] | mm | SAFE |
| `rain_5d` | sum of rainfall over [T-5, T-1] | mm | SAFE |
| `rain_10d` | sum of rainfall over [T-10, T-1] | mm | SAFE |
| `rain_max1d_5d` | max single-day rainfall over [T-5, T-1] | mm | SAFE |
| `rain_recent_vs_antecedent_ratio` | `rain_1d / rain_10d` (NaN if `rain_10d`==0) | dimensionless | SAFE |
| `rain_accel_3d_minus_prior3d` | `rain_3d` (=[T-3,T-1]) minus rainfall summed over [T-6, T-4] | mm | SAFE |

`rain_recent_vs_antecedent_ratio` captures a sudden burst on top of a dry
antecedent period (flash-flood-favorable) vs. a soaked catchment.
`rain_accel_3d_minus_prior3d` captures whether rainfall intensity is
increasing (positive) or decreasing (negative) over two consecutive
3-day windows, a simple proxy for storm intensification.

## 2. Catchment features (static, from `data/catchment_characteristics_indofloods.csv`)

Allowlist only — 108 raw columns exist; the ones below were kept because
they describe basin hazard-relevant structure. A cell is mapped to one or
more gauges via `processed/indofloods/indofloods_grid_mapping.csv`
(source_type==gauge, status==MAPPED); numeric catchment features are
averaged across gauges mapped to that cell, categorical features take the
mode. This is a documented simplification: catchments are gauge-native
polygons, not native to the 1-degree canonical grid.

| Feature | Meaning | Units | Static/Dynamic | Hazard relevance | Leakage status |
|---|---|---|---|---|---|
| Stream Order | Strahler order of the outlet stream | ordinal | static | higher order = larger integrated network | SAFE |
| Drainage Density | stream length / basin area | km/km^2 | static | denser network = faster runoff concentration | SAFE |
| Drainage Texture | stream frequency per unit perimeter | ratio | static | terrain dissection, runoff response | SAFE |
| Drainage Intensity | stream frequency / drainage density | ratio | static | runoff efficiency | SAFE |
| Channel Frequency | number of streams / basin area | count/km^2 | static | runoff concentration | SAFE |
| Infiltration Number | drainage density x stream frequency | ratio | static | inverse of infiltration capacity | SAFE |
| No. of Firstorder Streams | count of 1st-order streams | count | static | network size | SAFE |
| No. of Secondorder Streams | count of 2nd-order streams | count | static | network size | SAFE |
| No. of Thirdorder Streams | count of 3rd-order streams | count | static | network size | SAFE |
| Basin Magnitude | total number of 1st-order streams (basin complexity) | count | static | drainage complexity | SAFE |
| Fitness Ratio | channel length / perimeter | ratio | static | basin efficiency | SAFE |
| Wandering Ratio | main channel length / valley length | ratio | static | channel sinuosity, flow travel time | SAFE |
| Maximal Flow Length | longest flow path | km | static | time-of-concentration proxy | SAFE |
| Downvalley Length | straight-line valley length | km | static | basin extent | SAFE |
| Drainage Area | catchment area | km^2 | static | scales peak discharge | SAFE |
| Catchment Relief | max minus min elevation | m | static | steeper relief = faster runoff | SAFE |
| Catchment Length | basin length along main channel | km | static | time-of-concentration proxy | SAFE |
| Catchment Perimeter | basin boundary length | km | static | basin shape | SAFE |
| Sinuosity Index | channel length / valley length | ratio | static | channel meander | SAFE |
| Form Factor | area / (basin length)^2 | ratio | static | elongated vs. rounded basin shape (rounded -> flashier) | SAFE |
| Relief Ratio | relief / basin length | ratio | static | slope steepness proxy | SAFE |
| Elongation Ratio | diameter of equal-area circle / basin length | ratio | static | basin shape | SAFE |
| Circularity Ratio | area / area of circle with same perimeter | ratio | static | basin shape, flashiness | SAFE |
| Lemniscates Value | shape index | ratio | static | basin shape | SAFE |
| Compactness Coefficient | perimeter / circumference of equal-area circle | ratio | static | basin shape, flashiness | SAFE |
| Ruggedness Number | relief x drainage density | ratio | static | combined steepness+dissection hazard proxy | SAFE |
| Soil type | dominant soil class (categorical) | class | static | infiltration capacity | SAFE |
| lithology type | dominant lithology class (categorical) | class | static | permeability / runoff generation | SAFE |
| Land cover | dominant land cover class (categorical) | class | static | vegetation/impervious runoff modifier | SAFE |
| Annual Precipitation | long-run mean annual precipitation (WorldClim normal) | mm/yr | static (climate normal) | baseline wetness regime | SAFE |
| Precipitation Seasonality | coefficient of variation of monthly precipitation | % | static | monsoon concentration | SAFE |
| Precipitation of Wettest Month | long-run mean of wettest month | mm | static | monsoon intensity regime | SAFE |
| Precipitation of Wettest Quarter | long-run mean of wettest quarter | mm | static | monsoon intensity regime | SAFE |
| Annual Mean Temperature | long-run mean annual temperature | deg C | static | climate regime | SAFE |
| Temperature Seasonality | std dev of monthly temperature x 100 | dimensionless | static | climate regime | SAFE |
| KoppenGeiger Climate Type | climate classification (categorical) | class | static | broad climate regime | SAFE |

## 3. Explicitly excluded (assignment hard constraint — exposure/impact, not hazard)

`2015/2010/2005/2000/1995/1990_GDP_PPP`, `2010/2015/2005/2000/1995/1990_GDP_PC_PPP`,
`Night Light`, `Road Density`, `Urban percentage`, `2010/2015/2005/2000/1995/1990_HDI`,
`Population Count`, `Population Density` — all dropped, never used as predictors.

## 4. Also excluded, per assignment

- INDOFLOODS `T1d`-`T10d` antecedent-precipitation columns — event-conditioned
  only (exist only for known event rows), would only ever appear
  alongside positives, so are trivially separable — **LEAKAGE**, dropped.
- Any post-event field (`Peak Level`/`Peak Date`, `Peak Discharge`,
  `Duration`, `Flood Type`, `Warning Level`, `Danger Level`) — these are
  observed *after* or *because of* the event, and in several cases define
  the label itself — **LEAKAGE**, dropped.

## 5. Leakage audit summary

Every feature actually used in `processed/ff_pu/ff_pu_training_table.csv`
is marked SAFE above (backward-looking rainfall, or long-run static
catchment/climate descriptors unrelated to the specific flood outcome).
No feature is marked UNKNOWN; none were dropped for being unresolved.
