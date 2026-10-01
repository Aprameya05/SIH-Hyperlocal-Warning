# Pan-India Supervised Dataset Schema — Phase 5 — 2026-09-30

This document defines the canonical row schema for the Phase 5 dataset. It is written before dataset construction so the builder has a fixed target, and is updated only if construction reveals the schema itself needs a change (that did not happen this phase).

## Core principle

No label is manufactured. No predictor is invented. A physically missing value is represented as missing, never as zero, unless the field's own definition makes zero the genuine physical reading. Every predictor and every label carries its own provenance, independent of every other field's provenance — a row is not "half real"; each column in it states its own truth.

## Identity fields (every row)

| field | type | description |
|---|---|---|
| `timestamp` | string | Reference time for this row, in the label engine's existing format (`YYYY-MM-DDT slotN`) |
| `cell_id` | string | Canonical grid cell, `IND_{lat:.1f}_{lon:.1f}` — one of exactly the 992 canonical cells |
| `lat`, `lon` | float | Cell center, matching the canonical grid definition (`docs/CANONICAL_GRID.md`) |

## Predictor provenance fields (every row, regardless of predictor status)

| field | description |
|---|---|
| `source_timestamp` | The actual timestamp of the source observation used (may differ from `timestamp` within the allowed temporal window) |
| `source_resolution` | Native resolution of the predictor source at this row (e.g. `"point:BLR"`, `"6-hourly"`, `"1.0deg:live-snapshot-only"`) |
| `source_provenance` | Free-text description of exactly which file/product this row's predictors came from |

## Predictor fields

Each of the twelve fields below (matching `backend/mtl_backbone.py::FEATURE_NAMES`) is stored as **five** columns, never one bare value column, so missingness is never silently lost:

`<name>_value`, `<name>_missing_flag`, `<name>_missing_reason`, `<name>_source`, `<name>_source_timestamp`

Fields: `cape`, `cin`, `pwat_mm`, `k_index`, `totals_totals`, `wind_shear_ms`, `t850`, `t700`, `t500`, `td850`, `td700`, `ctt_c`.

Two of these (`td850`, `td700`) are **derived, not raw-source** fields: they are computed from real ERA5 specific humidity + temperature via Bolton's (1980) inverted Magnus formula (the same, already-used formula from `colab/build_blr_dataset.py::dewpoint_from_q_t`) — a physical transform of real observed fields, not a fabricated or substituted variable. Their `<name>_source` value says so explicitly (`"derived: Bolton(1980) from ERA5 q+T"`), distinguishing them from directly-observed fields.

No predictor is ever zero-filled at dataset-construction time. If a downstream training pipeline needs imputation, that is that pipeline's own documented, separate decision — not something this raw dataset performs silently.

## Label fields

Separate fields per hazard, matching Phase 4's label engine exactly (`docs/LABEL_ENGINE.md`) — nothing about the label semantics changes in Phase 5, only how they are joined into one row:

| hazard | fields |
|---|---|
| TS | `ts_label`, `ts_label_status`, `ts_label_source` |
| CB | `cb_label`, `cb_label_status`, `cb_label_source` |
| FF | `ff_label`, `ff_label_status`, `ff_label_source` |

`ff_label`/`ff_label_status` refer **only** to the genuine, observed-event FF label (currently `UNKNOWN` for every cell — see `docs/LABEL_ENGINE.md`). The FF rainfall proxy is **never** written into these fields. It exists as an entirely separate set of columns:

`ff_proxy_label`, `ff_proxy_label_status`, `ff_proxy_label_source`

so that no consumer of this dataset can accidentally join, filter, or train against `ff_label` and get proxy values without an explicit, separate opt-in to the `ff_proxy_*` columns.

Allowed values for every `*_label_status` field: `POSITIVE`, `NEGATIVE_CONFIRMED`, `UNKNOWN`, `PROXY_NOT_OBSERVED` (the last one only ever appears in `ff_proxy_label_status`). `UNKNOWN` rows carry `*_label = NULL`, never `0`. `PROXY_NOT_OBSERVED` rows carry `ff_proxy_label ∈ {0,1}` (the proxy's own binary reading) but this is never written to `ff_label`.

## Two dataset partitions (not two schemas — one schema, two coverage regimes)

Phase 5's real-data audit (§3 of `docs/PHASE_5_DATASET_REPORT.md`) found that a genuinely joinable, real, historical **predictor archive at pan-India spatial coverage does not exist in this repository** — `data/pan_india_grid.json` is a single live snapshot, regenerated in place every 6 hours, not a time series. The only real historical predictor time series in this repo is point-based, at Bengaluru/VOBL (`data/era5_6hrly_bengaluru_2015_2025.csv`, 2015-2025, 6-hourly). Both partitions below share the exact schema above; they differ only in what is genuinely available to fill it with:

- **VOBL partition**: cell `IND_13.0_78.0` only, 6-hourly, 2015-2025. Real predictors (ERA5 point series) + real TS labels (VOBL METAR) + real CB labels (that cell, from IMD gridded rainfall) + real FF proxy for that cell. This is the only partition where predictors and all three hazard labels are simultaneously real for the same row.
- **Pan-India CB partition**: all 992 cells, daily, for every day IMD gridded rainfall has coverage (2015-2025). Real CB labels (`POSITIVE`/`NEGATIVE_CONFIRMED`/`UNKNOWN` per the real IMD threshold). Predictor columns are present in the schema but **every predictor's `missing_flag=True`** with `missing_reason="no historical pan-India gridded predictor archive exists in this repository; data/pan_india_grid.json is a live current snapshot only, not a time series"`. TS and FF (observed) are `UNKNOWN` for every row in this partition (matching Phase 4's label engine exactly — VOBL is the only TS-observed cell, and no cell has observed FF).

This two-partition structure is not a shortcut — it is the accurate shape of what real data actually supports today, stated explicitly rather than blended into one misleadingly uniform table.
