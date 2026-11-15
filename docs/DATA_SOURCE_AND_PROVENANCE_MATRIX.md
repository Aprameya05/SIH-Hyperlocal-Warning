# Data source and provenance matrix

Provenance classes: **observation**, **NWP input**, **derived**, **trained prediction**, **calibrated probability**, **heuristic**, **official**, **simulation**.

| Source | Use | Class | Status | Access / blocker |
|---|---|---|---|---|
| NOAA GFS 0.25deg (AWS) | Fields for every land cell (CAPE, CIN, PWAT, RH, winds, T, APCP, soil water, ...) | NWP input | **LIVE** | Open; publication time = S3 `Last-Modified` (observed) |
| Derived physics (K-index, Total Totals, dew point, 850-500 hPa shear, lapse rate, convergence, PWAT gradient) | Predictors, display | derived | LIVE | Shear is 850-500 hPa, **not** 0-6 km |
| Thunderstorm model | +6/12/18/24 h | calibrated probability | LIVE, evaluated | Labels: IEM METAR TS/VCTS, 113 stations |
| Short-duration rain model | 20 mm / 3 h at f09 | calibrated probability | LIVE, evaluated, one target | Labels: NOAA CDR CMORPH hourly 0.25deg |
| Heavy-rain-day (`panindia_cb_v1`) | 1 deg daily ranking | trained score (uncalibrated) | shown as ranking only | negative Brier skill |
| Flash-flood screening index | context | heuristic | shown, labelled unvalidated | no flood labels |
| Terrain (DEM), gauge distance | static cell attributes | derived | LIVE | - |
| Boundaries: datameet states, geoBoundaries ADM2; GeoNames | cell -> state/district; search | official/reference | LIVE | 3,756 places |
| INSAT / MOSDAC | cloud-top, IWV nowcasting | observation | **UNAVAILABLE** | registration/credentials required (adapter stub, no fabricated data) |
| IMDAA reanalysis | training/climatology | reanalysis | **UNAVAILABLE** | registration required |
| GMGSI global IR mosaic | reachable | observation (8-bit counts) | **NOT USED** | uncalibrated counts; calibration not guessed |
| Himawari-8/9 calibrated IR | CTT | observation | **UNAVAILABLE** | blocked/unimplemented |
| Radar, lightning, hourly QPE | nowcasting | observation | **UNAVAILABLE** | no accessible archive |
| Flood events, river levels, flow accumulation, drainage | flash-flood model | observation | **UNAVAILABLE** | no time/location-labelled source |
| Alert delivery (SMS/webhook) | alerts | official | **DISABLED** | empty registry, no real alerts sent |

Every row marked UNAVAILABLE surfaces as `status: unavailable` with a reason in the API; nothing is imputed.
