# DRIFT / SIH — Pan-India Grid + Label Engine — Phases 3-4 — 2026-09-30

Stopped after Phase 4 as instructed. No dataset construction, no MTL training, no production/frontend changes. Nothing committed or pushed.

## Two Important Corrections To Prior Passes, Found By Actually Reading The Code This Time

**1. The FF label already shipped in `data/bengaluru_6hr_training_dataset_cb_ff.csv` is not what two prior reports in this session said it was.** `dev/Fetch cb ff labels.py`'s FF derivation requires gauge coordinates from `data/catchment_characteristics_indofloods.csv`, which doesn't exist. When that file is missing, the code silently falls through to its own documented fallback: a pure rainfall threshold (3-day cumsum >=100mm AND daily >=40mm), not a real flood observation. The prior "AUROC 0.986 against real INDOFLOODS flood events" framing was wrong about what the label represents — the AUROC computation itself was real, but the label it was measured against is a rainfall proxy, not an observed flood. Corrected in `docs/LABEL_ENGINE.md`.

**2. The CB threshold constant doesn't match its own docstring.** `dev/Fetch cb ff labels.py` says `"CB label: RF >= 100 mm/day"` in its module comment, but the actual executed constant is `CB_THRESHOLD_MM = 64.5`. This pass's pan-India CB script uses 64.5 — matching what the code actually does, not what its comment claims — and flags the discrepancy explicitly rather than silently picking whichever number sounded more official.

## Phase 3 — Canonical Grid (`docs/CANONICAL_GRID.md`)

Formalized the existing 992-cell grid as canonical, per your instruction — no second grid created. **Critical finding**: the live `data/pan_india_grid.json` file (992 cells, 1.0deg step, generated 2026-09-30T11:23Z) does not match what the *current* `backend/pipeline.py` code would produce if run today — the code's `GRID_STEP` was changed to `0.25` in an earlier commit (`7d52a42`), which would generate 15,125 cells, not 992. This was previously only hinted at in `regrid.py`'s own docstring; Phase 3 traces it to the exact commit and confirms it directly against the live file's own `grid_step_deg` field. **Not fixed** (production untouched, as instructed) — flagged as a decision you need to make before Phase 5, since the next scheduled `update_grid.yml` run will silently replace the 992-cell file with a 15,125-cell one.

Defined the canonical `cell_id` convention (`IND_{lat:.1f}_{lon:.1f}`) and extended `regrid.py` **additively** — a new `regrid_to_cell()`/`CellRegridResult` wrapper carrying the missing provenance fields (`source`, `source_timestamp`, `target_cell_id`, `missing_flag`, `missing_reason`) without touching `regrid_point()`'s existing signature. **All 8 pre-existing `test_regrid.py` checks still pass unmodified** — verified by actually re-running them after the edit, not assumed.

## Phase 4 — Pan-India Label Engine (`docs/LABEL_ENGINE.md`)

All three hazard label scripts were **actually run against the real repository data**, not just written. Real, verified numbers:

| Hazard | Geographic coverage | Positive | Negative confirmed | Unknown/proxy note |
|---|---|---|---|---|
| **TS** | 1/992 cells (0.101%) — VOBL only, explicitly | 584 | 14,692 | 991 cells marked `UNKNOWN`, never fabricated as negative |
| **CB** | 992/992 cells (genuinely pan-India) | 49,613 cell-days | 1,501,330 cell-days | 2,434,913 cell-days missing (mostly ocean/coastal cells where IMD's land rainfall product has no data — real, not a bug) — daily resolution, applied to all 4 slots per day, documented as a limitation |
| **FF** | 0/992 cells for genuine events (blocked) | — | — | **All 992 cells `UNKNOWN`** for event-based FF (no gauge-coordinate file exists anywhere in this repo — checked directly, not assumed). A separately-labeled rainfall proxy (`PROXY_NOT_OBSERVED`, never `POSITIVE`) computed for reference: 78,442 positive cell-days, 5.06% rate — explicitly not an observed-flood figure |

No label was derived from CAPE, CIN, K-index, wind shear, CTT, the physics proxy, or any XGBoost/MTL prediction — verified in the test suite (`test_1_no_predictor_derived_labels`).

## Tests — All Real, All Run Against Real Generated Output

- `tests/test_canonical_grid.py`: **28/28 passed** — live grid matches documented canonical definition, `cell_id_for()` is deterministic and collision-free between neighbors, `regrid_to_cell()` carries full provenance, missing/far-point cases correctly flagged rather than fabricated.
- `tests/test_panindia_labels.py`: **19/19 passed**, covering all 11 requested validation areas (no predictor-derived labels, no future leakage, deterministic mapping, duplicate handling, spatial/temporal boundary handling, UNKNOWN never collapsed to negative, label counts, geographic coverage, class imbalance, source provenance).
- `test_regrid.py` (pre-existing, 8 checks): **still passes unmodified** after the additive `regrid.py` extension.

## What's Now Genuinely Closer to SIH Completion

**Cloudburst (#4 in the requirement matrix)**: moved from "BLR-only proxy" to "genuinely pan-India, real IMD data, honestly daily-resolution, tested." This is real progress.

**Canonical grid / common spatiotemporal grid (#1, #21)**: the grid is now formally documented with a resolved cell-ID scheme and full-provenance regridding — but the 0.25-vs-1.0-degree discrepancy above means "canonical" is provisional pending your decision.

## What Got Corrected Downward (Not Progress, But More Honest)

**Flash flood (#5)**: prior passes' framing overstated this. It's now precisely characterized as blocked on a specific missing file, with the existing BLR CSV's FF column reclassified as a rainfall proxy rather than an observed-flood label.

**Thunderstorm (#3) pan-India**: unchanged in substance (still VOBL-only) but now formally represented — 991 cells explicitly `UNKNOWN` rather than simply absent from any schema.

## Files Created This Phase

`docs/CANONICAL_GRID.md`, `docs/LABEL_ENGINE.md`, `scripts/build_panindia_ts_labels.py`, `scripts/build_panindia_cb_labels.py`, `scripts/build_panindia_ff_labels.py`, `scripts/validate_panindia_labels.py`, `tests/test_canonical_grid.py`, `tests/test_panindia_labels.py`, plus generated outputs: `processed/labels/{ts_labels.csv, ts_labels_summary.json, cb_labels.csv, cb_labels_summary.json, ff_labels_proxy.csv, ff_labels_summary.json, coverage_report.json}`.

## Files Modified

**`regrid.py`** — additive only (new `CellRegridResult`/`regrid_to_cell()`/`cell_id_for()` appended; nothing existing changed or removed). Verified non-breaking by re-running the pre-existing test suite.

No other file was modified. Production inference, the frontend, and deployment workflows were not touched.

## Still Blocked / Not Started

Genuine pan-India TS and FF labels remain blocked — TS on the absence of any pan-India station/lightning network in this repo, FF on the missing gauge-coordinate file. Phase 5 (dataset construction) was explicitly not started, per your stop condition. **We do not have a complete pan-India supervised dataset — only cloudburst has real, genuinely pan-India labels today; TS and FF do not, and this report does not claim otherwise.**

## Decision Needed Before Phase 5

The GRID_STEP discrepancy (Phase 3 finding) needs a decision: keep the live 992-cell/1.0deg grid as canonical going forward (would require either reverting `backend/pipeline.py`'s `GRID_STEP` or preventing the next scheduled run from overwriting the file), or adopt the code's current 0.25deg/15,125-cell definition as canonical instead (would require re-deriving all downstream cell-ID mappings in this phase's scripts). Everything in this pass assumes the former (matching your explicit "use the existing 992-cell grid" instruction), but that assumption has an expiration date tied to the next `update_grid.yml` run.
