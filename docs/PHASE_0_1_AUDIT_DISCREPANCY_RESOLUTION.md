# Phase 0.1 — INDOFLOODS / FF-Labeling Audit Discrepancy Resolution

## 1. Claim under review

`MASTER_AUDIT_FINAL_REPORT.md`, point 4 (RED count) and point 7:

> "missing drainage/catchment join file for FF"
>
> "the FF gauge-coordinate join file needed for real flood-event labeling is also missing."

Tracing this to its source row in `docs/MASTER_SIH_REQUIREMENT_MATRIX.md`:

> Row 17 — Drainage/catchment (FF) | NOT IMPLEMENTED | Yes — `dev/Fetch cb ff
> labels.py` references `data/catchment_characteristics_indofloods.csv`; that
> exact path does not exist under `data/` (it exists only as
> `processed/indofloods/...` derivatives, not the raw gauge-coordinate file
> the script wants) | RED | Confirmed missing input; production FF pipeline
> silently falls back to the rainfall-only proxy

The same claim, worded almost identically, also appears in the older
`docs/SIH_REQUIREMENT_MATRIX.md` row 5/17, so this is not a one-off slip by
the most recent audit pass — it has been repeated across at least two audit
documents.

## 2. Direct verification against the actual repo

```
$ ls -la data/catchment_characteristics_indofloods.csv
-rw-r--r-- 1 root root 146653 Sep 30 15:56 data/catchment_characteristics_indofloods.csv

$ wc -l data/catchment_characteristics_indofloods.csv
156 data/catchment_characteristics_indofloods.csv   # 155 gauge rows + header

$ grep -n "CATCHMENT_FILE" "dev/Fetch cb ff labels.py"
CATCHMENT_FILE = "data/catchment_characteristics_indofloods.csv"
```

**The exact file the matrix row says is missing is physically present on
disk at the exact path the script references.** `data/catchment_characteristics_indofloods.csv`
has 155 gauge-level rows with real drainage-network, morphometry, soil,
lithology, land-cover, and climate-normal columns keyed by `GaugeID` — it is
the raw catchment-characteristics file, not a derivative.

Also directly verified, all present and non-empty under `data/`:
- `data/floodevents_indofloods.csv` (465 KB, the raw INDOFLOODS event table)
- `data/bengaluru_6hr_training_dataset_v4.csv` (the training table `dev/Fetch
  cb ff labels.py` merges labels into)

File timestamps confirm `data/catchment_characteristics_indofloods.csv`
(2026-09-30 15:56) predates both `docs/MASTER_SIH_REQUIREMENT_MATRIX.md`
(2026-10-01 02:25) and `MASTER_AUDIT_FINAL_REPORT.md` (2026-10-01 02:28) — the
file already existed when the audit was written, so this is not a case of
the file having been added after the audit ran.

## 3. The 214/4,548/4,106/620 figures

| Claimed | File | Verified |
|---|---|---|
| 214 gauges mapped | `processed/indofloods/indofloods_grid_mapping.csv` | **214 data rows** (215 lines incl. header) — matches exactly |
| 4,548 raw events joined | `processed/indofloods/indofloods_grid_events.csv` | **4,548 data rows** (4,549 lines incl. header) — matches exactly |
| 620 positives / 69 cells / 131 gauges (ff_pu, IMD overlap) | `processed/ff_pu/dataset_build_summary.json` | `n_positive: 620`, `documented_cells: 69`, `documented_gauges: 131`, `recomputed_cells: 69`, `recomputed_gauges: 131`, `match: true` — matches exactly |

The "4,106 unique cell/date events" figure in the task brief does not match
any number independently recomputed from `indofloods_grid_events.csv` in
this pass (a plain `(cell_id, Start Date)` dedup on that file yields 75
unique pairs, because most rows share a small number of (cell, date)
combinations across many gauges/events). The number that *is* independently
reproducible and documented is the **620** positives figure in
`processed/ff_pu/dataset_build_summary.json`, built with the documented,
narrower filter (`mapping_status==MAPPED`, `Start Date` in the 2015-01-01 to
2020-09-24 common window, deduplicated on `(cell_id, Start Date)`). No
dataset was recreated or duplicated in this phase — this section only
verifies files and counts that already existed.

## 4. Why the master audit's subagent likely concluded "missing"

Row 17's own text is specific about *what* it checked: whether
`dev/Fetch cb ff labels.py`'s `CATCHMENT_FILE` constant resolves to a real
path. That check, if actually run with a working-directory or `ls`
mismatch (e.g. checking from a different cwd, or checking `data/` before the
file had been written in that audit's own working copy), would produce a
false "missing" result. We cannot recover what the audit subagent actually
ran, but the claim as written is unambiguous and is directly falsified by
the file's current presence, size, and content.

## 5. Resolution

**The master audit was factually WRONG on this specific point.** The exact
file (`data/catchment_characteristics_indofloods.csv`) it says does not
exist under `data/` does exist there, with real content, and is the same
file whose derivatives (`processed/indofloods/...`, `processed/ff_pu/...`)
the audit itself (correctly) verified downstream. This is not a matter of
imprecise wording about a *different*, more specific file (e.g. a
gauge-to-DEM-catchment-polygon shapefile) — the audit names this exact CSV
path and says it is absent, and it is not absent.

The INDOFLOODS pipeline derivatives (grid mapping, grid events, ff_pu
training table and model artifacts) are real, present, and match their
documented row/positive/cell/gauge counts exactly. The production FF
pipeline (`backend/pipeline.py`) does **not** use any of this — see
`docs/PHASE_0_1_FF_CURRENT_STATE.md` — but that is a separate, correctly
documented fact (the rainfall-threshold-proxy RED finding at matrix row 5
is accurate and unaffected by this correction).

**No files were recreated or duplicated to produce this resolution; only
existing on-disk files were inspected (`ls`, `wc -l`, `head`, and direct
Python/pandas reads).**
