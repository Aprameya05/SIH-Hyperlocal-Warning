# INDOFLOODS Feature Catalog — `catchment_characteristics_indofloods.csv`

155 rows (one per `GaugeID`), 108 columns. All static (catchment-level
constants, no time dimension — GDP/HDI columns are the only ones with
multiple snapshot years, 1990-2015, but each is still a fixed historical
value, not a live time series). Definitions below are drawn from the PDF's
Table 3 where the PDF names the variable group; several individual
stream-order-topology and morphometric-index column names are standard
geomorphology terms not individually redefined in the PDF text extracted
here (noted as "standard morphometric index, not separately defined in the
extracted PDF text" below) but are named identically to the CSV header, so
the mapping from column to concept is unambiguous.

Susceptibility use = whether the column is a plausible static flash-flood
**susceptibility** feature (terrain predisposes a catchment/cell to flash
flooding) as opposed to an observed-event or proxy variable.

## Drainage network / stream-order topology (structural NaN group)

| Column(s) | Meaning | Spatial level | Susceptibility use | Limitation |
|---|---|---|---|---|
| `Stream Order` | Strahler stream order of the catchment's main channel | catchment | Yes — higher order roughly tracks larger, more organized drainage | none |
| `No. of {First..Eighth}order Streams` | Count of streams of each Strahler order in the catchment | catchment | Yes — stream density by order | NaN for orders above the catchment's own `Stream Order`: **structural absence**, verified 1:1 against `Stream Order` (e.g. all 62 rows with `Stream Order`<4 are exactly the 62 NaN rows in `Fourthorder Streams Length`); not to be treated as missing data |
| `{First..Eighth}order Streams Length` / `... Mean Length` | Total / mean channel length per stream order | catchment, km (unit not restated per-column in extracted text but consistent with standard Horton-Strahler analysis and the PDF's morphometric framing) | Yes | same structural-NaN caveat |
| `{Nth}{N+1th} Stream Length Ratio`, `{Nth}{N+1th} Bifurcation Ratio` | Ratio of stream lengths / stream counts between successive orders | catchment | Yes — classic drainage-network maturity indices | same structural-NaN caveat; 8th-order-adjacent ratios are NaN in all 155 rows (no catchment reaches 8th order) |

Affected-row counts (all pandas-verified): 8th-order length/count columns —
155/155 NaN (all rows); 7th-order — 150/155 NaN; 6th-order — 135/155 NaN;
5th-order — 97/155 (length/mean) and 94/155 (count) NaN; 4th-order — 62/155
NaN; 3rd-order — 20/155 NaN; 1st/2nd order — 0 NaN (every catchment has at
least a 1st- and 2nd-order stream).

## Morphometric indices

| Column | Meaning | Susceptibility use |
|---|---|---|
| `Maximal Flow Length`, `Downvalley Length`, `Drainage Area`, `Catchment Relief`, `Catchment Length`, `Catchment Perimeter` | Basic catchment geometry (lengths in km, area in km^2, relief in m — standard morphometric index, not separately defined in the extracted PDF text) | Yes — size/shape/relief are classic flash-flood-susceptibility drivers (smaller, steeper catchments concentrate runoff faster) |
| `Sinuosity Index`, `Form Factor`, `Elongation Ratio`, `Circularity Ratio`, `Lemniscates Value`, `Compactness Coefficient`, `Wandering Ratio` | Shape indices describing how elongated/circular/compact the catchment is | Yes — shape strongly affects concentration time |
| `Relief Ratio`, `Ruggedness Number` | Relief-over-length / relief-times-drainage-density indices | Yes — steep, rugged catchments flash more readily |
| `Drainage Texture`, `Drainage Density`, `Channel Frequency`, `Drainage Intensity`, `Infiltration Number`, `Basin Magnitude`, `Fitness Ratio` | Drainage-network density/intensity indices | Yes — denser drainage networks generally respond faster to rainfall |

None of these have missing values beyond the structural stream-order NaNs
already covered (they are catchment-wide scalars, always computable).

## Climate normals (Bioclim-style)

| Column | Meaning | Unit | Susceptibility use |
|---|---|---|---|
| `Annual Mean Temperature`, `Mean Diurnal Range`, `Isothermality`, `Temperature Seasonality`, `Max Temperature of Warmest Month`, `Min Temperature of Coldest Month`, `Temperature Annual Range`, `Mean Temperature of {Wettest/Driest/Warmest/Coldest} Quarter` | Standard Bioclim temperature normals | deg C (Bioclim convention) | Weak/indirect — climate context, not a direct flash-flood driver on its own |
| `Annual Precipitation`, `Precipitation of {Wettest/Driest} Month`, `Precipitation Seasonality`, `Precipitation of {Wettest/Driest/Warmest/Coldest} Quarter` | Standard Bioclim precipitation normals | mm | Yes — a catchment with high seasonality/wettest-month precipitation is more exposed to intense-rainfall-driven flash floods, as a **static climatological baseline**, distinct from the event-specific antecedent precipitation in `precipitation_variables_indofloods.csv` |
| `KoppenGeiger Climate Type` | Koppen-Geiger climate classification code | categorical | Weak/indirect — broad climate regime |

These are long-run climate normals (a fixed baseline per catchment), not
event-specific — do not confuse with the `T1d`-`T10d` antecedent
precipitation columns in `precipitation_variables_indofloods.csv`, which
are anchored to specific flood-event dates.

## Socioeconomic / exposure descriptors

| Column | Meaning | Susceptibility use |
|---|---|---|
| `{1990,1995,2000,2005,2010,2015}_GDP_PPP`, `..._GDP_PC_PPP` | Catchment-average GDP (PPP) and GDP per capita (PPP) for each snapshot year | Not a hazard-susceptibility feature — an **exposure/impact** proxy (how much economic activity/value is at risk), not a driver of flood likelihood |
| `{1990,...,2015}_HDI` | Catchment-average Human Development Index for each snapshot year | Same — exposure/impact proxy, not hazard driver |
| `Night Light` | Average nighttime light (2010) — proxy for settlement/economic activity intensity | Exposure proxy |
| `Population Count`, `Population Density` | Gridded population (GPW) count/density | Exposure proxy — critical for impact severity, not for hazard likelihood |
| `Road Density` | Road network density | Exposure proxy (also a minor infrastructure/drainage-interference factor) |
| `Urban percentage` | Percent of catchment classified urban | Mixed — urbanization affects both susceptibility (impervious surface -> faster runoff) and exposure |
| `Land cover`, `Soil type`, `lithology type` | Dominant categorical land cover / soil / lithology in the catchment | Yes — genuine physical susceptibility drivers (soil infiltration capacity, lithology permeability, land cover runoff coefficient) |

**Important distinction for any future modeling**: the GDP/HDI/population/
night-light columns describe **who and what is exposed**, not how likely a
flash flood is to occur. They belong in an impact/risk layer, not a hazard
probability layer, and mixing them into a susceptibility feature set risks
conflating "flood-prone" with "economically significant," which would bias
a model toward already-developed catchments purely because more flood
records or infrastructure exist there.

## Full column list (108, in original CSV order)

`GaugeID, Stream Order, Maximal Flow Length, Downvalley Length, Drainage
Area, Catchment Relief, Catchment Length, Catchment Perimeter, Sinuosity
Index, Form Factor, Relief Ratio, Elongation Ratio, Circularity Ratio,
Lemniscates Value, Drainage Texture, Drainage Density, Compactness
Coefficient, Wandering Ratio, Fitness Ratio, Basin Magnitude, Channel
Frequency, Drainage Intensity, Infiltration Number, Ruggedness Number, No.
of Firstorder Streams, No. of Secondorder Streams, No. of Thirdorder
Streams, No. of Fourthorder Streams, No. of Fifthorder Streams, No. of
Sixthorder Streams, No. of Seventhorder Streams, No. of Eigthorder
Streams, Firstorder Streams Length, Secondorder Streams Length, Thirdorder
Streams Length, Fourthorder Streams Length, Fifthorder Streams Length,
Sixthorder Streams Length, Seventhorder Streams Length, Eighthorder
Streams Length, Firstorder Streams Mean Length, Secondorder Streams Mean
Length, Thirdorder Streams Mean Length, Fourthorder Streams Mean Length,
Fifthorder Streams Mean Length, Sixthorder Streams Mean Length,
Seventhorder Streams Mean Length, Eighthorder Streams Mean Length,
FirstSecond Stream Length Ratio, SecondThird Stream Length Ratio,
ThirdForth Stream Length Ratio, FourthFifth Stream Length Ratio,
FifthSixth Stream Length Ratio, SixthSeventh Stream Length Ratio,
SeventhEighth Stream Length Ratio, FirstSecond Bifurcation Ratio,
SecondThird Bifurcation Ratio, ThirdForth Bifurcation Ratio, FourthFifth
Bifurcation Ratio, FifthSixth Bifurcation Ratio, Sixthseventh Bifurcation
Ratio, SeventhEighth Bifurcation Ratio, Annual Mean Temperature, Mean
Diurnal Range, Isothermality, Temperature Seasonality, Max Temperature of
Warmest Month, Min Temperature of Coldest Month, Temperature Annual
Range, Mean Temperature of Wettest Quarter, Mean Temperature of Driest
Quarter, Mean Temperature of Warmest Quarter, Mean Temperature of Coldest
Quarter, Annual Precipitation, Precipitation of Wettest Month,
Precipitation of Driest Month, Precipitation Seasonality, Precipitation of
Wettest Quarter, Precipitation of Driest Quarter, Precipitation of Warmest
Quarter, Precipitation of Coldest Quarter, KoppenGeiger Climate Type,
2015_GDP_PPP, 2010_GDP_PPP, 2005_GDP_PPP, 2000_GDP_PPP, 1995_GDP_PPP,
1990_GDP_PPP, 2010_GDP_PC_PPP, 2015_GDP_PC_PPP, 2005_GDP_PC_PPP,
2000_GDP_PC_PPP, 1995_GDP_PC_PPP, 1990_GDP_PC_PPP, Night Light, Road
Density, Urban percentage, 2010_HDI, 2015_HDI, 2005_HDI, 2000_HDI,
1995_HDI, 1990_HDI, Population Count, Population Density, Land cover, Soil
type, lithology type`
