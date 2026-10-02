# DRIFT — Pan-India Label Engine (Phase 4) — 2026-09-30

## CRITICAL CORRECTION TO TWO PRIOR PASSES OF THIS SESSION

Auditing `dev/Fetch cb ff labels.py` line-by-line this time (not just its docstring, which is what earlier passes relied on) found that the FF label already shipped in `data/bengaluru_6hr_training_dataset_cb_ff.csv` — which two prior reports in this session described as **"derived from INDOFLOODS flood events within 200km of BLR, non-circular"** — is **not actually derived from real flood events at all**.

Here's why: `derive_ff_labels()` calls `load_gauge_locations()`, which reads `data/catchment_characteristics_indofloods.csv` for gauge lat/lon. That file does not exist in this repo (confirmed missing in two prior passes already). So `load_gauge_locations()` returns `None`, `near` (gauges within 200km) is empty, `ff_dates` stays empty, and the code **falls through to its own documented fallback**: `"RF-based FF proxy (3-day cumsum >= 100mm AND daily >= 40mm)"` — a pure rainfall threshold, not an observed flood event. Verified directly by reading the fallback branch's code, and by confirming `data/floodevents_indofloods.csv` and `data/precipitation_variables_indofloods.csv` (the two INDOFLOODS files that do exist) both lack any lat/lon column — there is genuinely no way to spatially locate any INDOFLOODS event without the missing catchment file.

**Correction, stated plainly:** the `ff_label` column already in the CB/FF training CSV is a rainfall-only proxy, indistinguishable in kind from the CB label except for its thresholds. It is not a real, independent flood observation. The two prior passes' "AUROC 0.986 on real flood events" framing overstated what that number actually validates — it's real AUROC on a real rainfall-derived proxy, not on real flood observations. This doesn't mean the number is fabricated (the computation was genuinely run against genuinely-existing CSV data), but the label it's measured against is weaker than previously described. This correction is being surfaced now because Phase 4 required actually reading the fallback branch, which prior passes hadn't done.

## Phase 4 Approach, Given This Finding

## Thunderstorm (TS) Labels

**Geographic coverage found: VOBL only.** Searched for any additional pan-India TS station/event source (per your instruction not to assume VOBL is sufficient) — `grep` for lightning data, radiosonde archives beyond the 67-row real-time file, any second station's observations: none found. `ts_label` in the training CSV is a real IMD station observation at VOBL (43295), not derived from CAPE/K-index.

`scripts/build_panindia_ts_labels.py` does exactly what the honest data supports: maps VOBL's real `ts_label` time series onto the one canonical cell nearest VOBL (`IND_13.0_78.0`), and explicitly marks **every one of the other 991 cells as `UNKNOWN`** for every timestamp — not a fabricated negative. This is the single most important honesty check in this phase: it would have been easy to quietly extrapolate VOBL's TS climatology across the grid; the script does not do that.

## Cloudburst (CB) Labels

**Genuinely pan-India, using real independent data.** `imd_rain/rain/{2015-2025}.grd` is real IMD 0.25-degree gridded daily rainfall (verified this pass: binary file sizes match exactly `129 x 135 grid points x 4 bytes x {365 or 366} days`, confirming the documented grid dimensions and leap-year handling are correct, not assumed). `dev/Fetch cb ff labels.py`'s CB threshold (`RF >= 100mm/day`, IMD's own standard cloudburst definition) is **unchanged, not silently altered** — `scripts/build_panindia_cb_labels.py` reuses the identical threshold.

**Generalization method**: for each of the 992 canonical cells, the script finds every 0.25-degree IMD rain cell whose center falls within that canonical cell's `+-0.5deg` box (up to 16 fine cells per canonical cell at this resolution ratio) and takes the **max** daily rainfall among them — a conservative choice that doesn't dilute a genuine cloudburst reading by averaging it with a dry neighboring fine cell.

**A second correction, found while reading the threshold constant itself**: `dev/Fetch cb ff labels.py`'s own module docstring says `"CB label: RF >= 100 mm/day (IMD cloudburst threshold)"`, but the actual constant used in the executed code is `CB_THRESHOLD_MM = 64.5`. The docstring and the real behavior disagree — the CSV already shipped in this repo was built with 64.5mm, not 100mm. `scripts/build_panindia_cb_labels.py` uses **64.5mm**, matching what the code actually does (not what its comment claims), and this discrepancy is called out explicitly rather than silently picking whichever number looked more official. Also note: this script's IMD grid domain (`LAT_START/END = 6.5/38.5`, `LON_START/END = 66.5/100.0`) is slightly wider than the canonical 992-cell grid's bounds (`6/37`, `68/98`) — the pan-India label script clips to the canonical grid's bounds.

**Documented temporal limitation, not silently resolved**: IMD's gridded rainfall product is daily; the model target elsewhere in this project (`forecast_action.py`, the CB/FF slot models) is 6-hourly. This script does **not** invent a 6-hourly rainfall event from a daily total. Instead, a positive daily CB label is applied with `label_status=POSITIVE` and an explicit `temporal_resolution="daily"` field to **all 4 slots of that date**, with a note that this represents "a cloudburst occurred somewhere in this cell on this date," not "in this specific 6-hour window" — the honest granularity the source data supports, stated as a limitation rather than hidden.

## Flash Flood (FF) Labels

**This is where the correction above matters most.** Given no gauge-coordinate file exists anywhere in this repo, `scripts/build_panindia_ff_labels.py` cannot reliably assign any INDOFLOODS event to any of the 992 canonical cells — there is no coordinate to regrid from. Per your explicit instruction ("do NOT convert lack of an event record into a confirmed negative... where source coverage does not support a reliable negative"), the script does the only honest thing available: it marks **every cell, at every timestamp, `UNKNOWN` for genuine event-based FF** — not `NEGATIVE_CONFIRMED`, because the absence of an assignable event here reflects a missing coordinate file, not a genuine absence of floods.

Separately, and clearly labeled as a *different, weaker thing*, the script also outputs a **rainfall-based FF proxy** (reusing the exact fallback formula already in `dev/Fetch cb ff labels.py`: 3-day cumulative rainfall >= 100mm AND daily >= 40mm, generalized pan-India using the same max-over-fine-cells method as CB) with `label_status=PROXY_NOT_OBSERVED` — explicitly not `POSITIVE`/`NEGATIVE_CONFIRMED`, so nothing downstream can mistake it for a real flood observation. This mirrors what the existing BLR CSV's `ff_label` column actually is (see the correction above), just made honest about its own status instead of looking like an observed-event label.

## Label Schema (implemented, shared by all three scripts)

```
timestamp, cell_id, hazard, label, label_status, source, source_event_id,
source_timestamp, spatial_distance_km, temporal_distance_hours, quality_flag
```

`label_status` in `{POSITIVE, NEGATIVE_CONFIRMED, UNKNOWN, PROXY_NOT_OBSERVED}`. `UNKNOWN` is never collapsed into `0`/`NEGATIVE_CONFIRMED` anywhere in the output — verified by an explicit test (`test_unknown_not_collapsed_to_negative` in `tests/test_panindia_labels.py`).

## What's Now Genuinely Closer to SIH Completion vs. Not

**Closer**: cloudburst labeling (#4 in the requirement matrix) now has a real, tested, pan-India-generalized path with honest daily-granularity documentation, not just a BLR-only proxy.

**Not closer, and actively corrected downward**: flash-flood labeling (#5) — the prior passes' framing overstated how validated the existing FF numbers are. Genuine event-based FF labels remain blocked, now for a more precise reason than before: not "we haven't built the pan-India version yet" but "the specific coordinate file needed to do this at all, anywhere, is missing." Thunderstorm labeling (#3) pan-India: unchanged, still explicitly VOBL-only, now formally represented as `UNKNOWN` everywhere else rather than just being absent from the schema.
