# Command centre: component inventory and data contracts

| Area | Element | Source of its values | Functional behaviour |
|---|---|---|---|
| Header | Logo, nav pills (Command centre, Forecast, Satellite, Models, Explain, Regimes, Scores, Graphs) | n/a | `?tab=` routing, Back/Forward, arrow-key navigation, sliding indicator |
| Header | Notices bell | GFS cycle age, Himawari scan age, model season, alerting state, recomputed from artifact timestamps | Popover, Escape closes, dot colour = worst status |
| Hero strip | Live/Simulation badge, disclaimer, Guided tour, data freshness chip, Classic dashboard | app state, `forecast`/fields freshness | Tour, link to `legacy.html` (unchanged rollback) |
| Left rail | Hazard pathway cards | store status (`fieldsOk`, `tsOk`) | Switches hazard and layer set; no invented probabilities |
| Left rail | Layers and forecast hour | `layerDefs()` over validated fields | Select + threshold slider change map, legend, inspector |
| Left rail | Pathway status | store, satellite scene time, cycle age | Per-row FRESH / STALE / UNAVAILABLE |
| Map | 0.25 degree grid (4,643 cells), India outline | `india_grid_025.json`, `india_fields_latest.json` | Click, search, zoom, my-location, layer shortcut, fullscreen, selected-cell pulse |
| Timeline | Forecast-hour chips (UTC and IST) | fields `meta.files` | Updates map, inspector, hazards, compare; status recomputed from the clock |
| Inspector | Location banner, provenance, 8 expandable categories | `DriftLocation.record()` (parity-tested with the Python service) | Accordion with animated gauge and charts; open state kept across hour changes |
| Inspector | Hazards, Compare, Alerts and actions, Sources, Evaluation | same store plus evaluation artifacts | unchanged behaviour, restyled |
| Added tabs | Forecast, Satellite, Models, Explain, Regimes, Scores, Graphs | classic dashboard pages in one lazy frame | selected cell shared both ways |

Artwork: page backdrop `frontend/assets/images/earth_sunrise.webp` (published at the site root); inspector card art in `frontend/assets/ui_art/` cut from the supplied UI sheet.
Motion honours `prefers-reduced-motion`. Every displayed measurement keeps its unit, status and provenance; nothing is defaulted to zero.
