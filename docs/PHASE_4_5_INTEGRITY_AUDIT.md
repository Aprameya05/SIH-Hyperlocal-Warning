# Phase 4.5 — Production Integrity + Requirement Audit — 2026-09-30

Scope: fix the two critical integrity issues Phases 3-4 exposed, audit label/claim provenance repo-wide, add hard guardrails against the two failure modes found, and give an honest go/no-go for Phase 5. No dataset construction, no MTL training, no A100 run, no frontend/production-inference change, no deployment, no commit, no push. Everything below was written directly to disk in the audit clone and packaged for delivery to the user's PC — nothing here was committed or pushed.

---

## 1. Executive Summary

Phase 3-4 surfaced two integrity risks that had to be closed before any Phase 5 dataset work:

1. **Grid drift risk (now fixed):** `backend/pipeline.py` — the sole active production writer of `data/pan_india_grid.json`, run 4x/day by `.github/workflows/update_grid.yml` — contained a single ambiguous `GRID_STEP` constant set to `0.25`, while the live on-disk grid file is `grid_step_deg=1.0`, `n_cells=992`. Left unfixed, the next scheduled workflow run would have silently overwritten the 992-cell canonical grid with a 15,125-cell one. This is now fixed: the constant is split into `APPLICATION_GRID_STEP=1.0` (locked, canonical) and `SOURCE_GRID_STEP_FALLBACK_DEG=0.25` (source-grid estimate only), and a hard `RuntimeError` guardrail refuses to write the file unless the generated grid has exactly 992 cells with no duplicate cell IDs.
2. **Label/claim provenance drift (now corrected in docs):** the flash-flood label in `data/bengaluru_6hr_training_dataset_cb_ff.csv`, and the associated "AUROC 0.986" figure, had been described in this repo's own documentation (`docs/SIH_REQUIREMENT_MATRIX.md`) and README as evaluated against real INDOFLOODS flood events / a trained MTL ensemble member. Neither is accurate. The FF label is a rainfall-threshold proxy (the required gauge-coordinate file does not exist in this repo), and the MTL backbone is untrained architecture with no weights file and no production integration. Both are now corrected in place, with the corrected evidence stated explicitly rather than the claim deleted.

Both issues are now closed at the code/doc level and covered by new automated guardrail tests. Five hazard-label and requirement-scope findings from Phase 4 remain genuinely blocked on missing external data (see §9) — none of that changed this phase, and none of it is invented.

---

## 2. Canonical Grid Decision

**Decision (unchanged from Phase 3, now enforced in code): the 992-cell, 1.0° grid is the canonical APPLICATION grid.** Source data (GFS) may arrive at a finer native resolution (0.25°) and is regridded onto this canonical grid via `regrid.py`; the canonical grid itself never changes resolution based on a source's native resolution. This is now stated explicitly in the grid's own output metadata:

```
"grid_step_deg": 1.0,
"application_grid_resolution_deg": 1.0,
"input_sources_may_have_different_native_resolution": true,
"bounds": {"S": 6, "N": 37, "W": 68, "E": 98},
"n_cells": 992
```

`docs/CANONICAL_GRID.md` (Phase 3) already documented this; nothing in that document needed correcting, only the code needed to be brought into line with it.

---

## 3. Grid Writer Audit

Every code path capable of creating or overwriting `data/pan_india_grid.json` was traced:

| Path | Status | Finding |
|---|---|---|
| `backend/pipeline.py` | **Active production writer** | Invoked by `.github/workflows/update_grid.yml` (`run: python backend/pipeline.py`), which also does `git add data/pan_india_grid.json data/ctt_grid.json`. Runs on cron `30 4,10,16,22 * * *` (4x/day). **This was the drift risk — now fixed** (see §1, §6). |
| `pan_india_gfs_fetcher.py` (repo root) | Active, safe | Already writes to `pan_india_grid_slotrun.json` (not the canonical filename), `GRID_STEP=1.0` in its own local constant. Invoked by `.github/workflows/forecast_update.yml`, a different workflow than `update_grid.yml`. Does not race with `backend/pipeline.py`. No change needed. |
| `dev/pan_india_gfs_fetcher.py` | **Stale, unused, latent risk** | Still targets `OUT_PATH = DATA_DIR / "pan_india_grid.json"` directly (the canonical filename) with `GRID_STEP=1.0` hardcoded locally (not shared with `backend/pipeline.py`'s constants). Not invoked by any `.github/workflows/*.yml` — confirmed by grepping every workflow file for `dev/pan_india_gfs_fetcher`. Not an active drift risk today, but if anyone manually runs this script it would write directly to the canonical path using its own independent, unguarded grid construction. **Recommendation for a future pass (not done here, since Phase 4.5 is restricted to preventing silent drift, not deleting dev scripts):** either delete this stale duplicate or point it at a non-canonical filename the way the root-level script already does. |
| `backend/dispatch_alerts.py`, `backend/mtl_backbone.py`, `backend/fetch_insat3d.py`, `generate_location_bundle.py`, `location_engine.py` | Read-only | Grepped for `pan_india_grid` / `.json` writes; all are readers, none write this file. |

**Conclusion: exactly one active production writer exists (`backend/pipeline.py`), and it is now guarded.** One inactive latent-risk duplicate exists (`dev/pan_india_gfs_fetcher.py`) and is flagged above for a future cleanup pass; it is not wired into any scheduled job today.

---

## 4. Label Provenance Matrix

| | **TS (Thunderstorm)** | **CB (Cloudburst)** | **FF (Flash Flood)** |
|---|---|---|---|
| **Source** | VOBL METAR/station thunderstorm observations | IMD gridded daily rainfall `.grd` files (2015-2025) | Two sub-sources: (a) event-based — `data/floodevents_indofloods.csv` (4,548 rows, 155 gauges), intended to be spatially joined via `data/catchment_characteristics_indofloods.csv`; (b) proxy — IMD rainfall thresholds |
| **Observation vs. proxy** | **Genuine observation** at VOBL only | **Genuine observation** (IMD gauge-gridded rainfall), used as a threshold proxy for the cloudburst *event* itself (no independent CB-specific sensor exists) | (a) would be a genuine observation if spatial join were possible; (b) is a **pure rainfall proxy**, not an observation of a flood |
| **Spatial coverage** | 1/992 canonical cells (0.10%) — VOBL cell `IND_13.0_78.0` only | 992/992 canonical cells have some IMD land coverage; ocean/coastal cells are `MISSING` (real IMD land-only limitation, not a bug) | (a) 0/992 cells — spatial join is impossible without gauge coordinates, which do not exist anywhere in this repo (`floodevents_indofloods.csv` and `precipitation_variables_indofloods.csv` both lack any lat/lon column, confirmed by inspection); (b) 992/992 cells, same IMD coverage as CB |
| **Temporal resolution** | Per-slot (6-hourly), matches VOBL station reporting | Daily IMD value applied identically to all 4 six-hour slots per day (documented limitation — real sub-daily cloudburst timing is not resolvable from this source) | (a) N/A (blocked); (b) same daily-applied-to-4-slots pattern as CB |
| **Positive definition** | VOBL METAR reports thunderstorm (TS) in that slot | Daily rainfall ≥ 64.5mm (the constant actually executed in `dev/Fetch cb ff labels.py`, despite that file's own docstring claiming "100 mm/day (IMD cloudburst threshold)" — the 64.5 figure is what the code has always run, and is what this pass's `scripts/build_panindia_cb_labels.py` uses) | (a) N/A (blocked); (b) 3-day cumulative rainfall ≥100mm AND that day's rainfall ≥40mm — `label_status=PROXY_NOT_OBSERVED`, **never** `POSITIVE` |
| **Negative definition** | VOBL METAR reports no thunderstorm in that slot | Daily rainfall < 64.5mm and IMD data present for that cell/day | (a) N/A (blocked); (b) proxy threshold not met, and IMD data present |
| **UNKNOWN conditions** | All cells other than the VOBL cell, for every slot (991/992 cells) — explicitly `UNKNOWN`, never defaulted to negative | Cell/day where IMD `.grd` has a missing value (`<=-900` sentinel), mostly ocean/coastal | (a) **all 992 cells, always** — `genuine_event_based_ff.status` = `"UNKNOWN — no gauge-coordinate file exists in this repo to perform the spatial join required for genuine event-based FF labels"`; (b) same missing-value cells as CB |
| **Safe for supervised training?** | Yes, for a VOBL-only model — this is exactly what the production station XGBoost model already does | Yes, pan-India — this is the one hazard with genuinely pan-India, non-fabricated labels today | (a) No — does not exist; (b) only if the consumer of the label is told, and continues to be told at every consumption point, that it is a rainfall proxy and not an observed flood — using it as if it were "observed FF" would be a real leakage/mislabeling risk |
| **Leakage risks** | None found — label derived solely from that slot's own station observation, verified by `test_b5_no_future_leakage_in_ts_labels` | None found — label derived solely from that day's own IMD value, no lookahead | (b) none found in the proxy computation itself, but the **historical mislabeling of this proxy as "observed"** (§5) was itself a form of documentation-level leakage: a reader trusting the old README/matrix text would have believed a proxy was ground truth |

This matrix reconciles: `docs/LABEL_ENGINE.md` (Phase 4), `dev/Fetch cb ff labels.py` (read in full this phase), `data/bengaluru_6hr_training_dataset_cb_ff.csv`'s generation logic, and the real generated Phase 4 output files (`processed/labels/*_summary.json`).

---

## 5. FF Correction

Traced exactly how `data/bengaluru_6hr_training_dataset_cb_ff.csv`'s FF column was generated:

`dev/Fetch cb ff labels.py::derive_ff_labels()` calls `load_gauge_locations()`, which reads `data/catchment_characteristics_indofloods.csv` for gauge lat/lon. **That file does not exist in this repository** (confirmed by `ls`/`Path.exists()` this phase and again during the guardrail test run). When it is missing, `load_gauge_locations()` returns `None`, the "gauges within 200km of BLR" set (`near`) stays empty, `ff_dates` (genuine flood-event dates) stays empty, and the function falls through to its own already-documented fallback: `"RF-based FF proxy (3-day cumsum >= 100mm AND daily >= 40mm)"` — a pure rainfall threshold. Neither `data/floodevents_indofloods.csv` nor `data/precipitation_variables_indofloods.csv` contains a lat/lon column, so there is currently no way to perform the spatial join anywhere in this repo, confirming the fallback path is the *only* path that has ever actually executed.

**No production model has been trained or evaluated against a genuinely observed flood event.** The XGBoost FF model's AUROC 0.986 figure (referenced in `docs/SIH_REQUIREMENT_MATRIX.md`) was computed against the rainfall proxy.

**Repo-wide search performed** for the phrases `"INDOFLOODS observed"`, `"observed flood events"`, `"real flood events"`, `"real INDOFLOODS"`, `"observed flash flood"` across every `.py`, `.md`, `.txt`, `.ipynb` file in the repository. Result: **two files contained an overclaiming form of this** — both now corrected in place (not deleted, per the "do not rewrite history, make current evidence accurate" instruction):

- `docs/SIH_REQUIREMENT_MATRIX.md`, row #5 (Flash-flood prediction) — previously stated "ML-based, INDOFLOODS-derived labels" / "AUROC 0.986, verified" with no proxy caveat. Corrected to state explicitly that the 0.986 figure is against the rainfall proxy, not an observed INDOFLOODS event, and that genuinely observed FF labels are DATA BLOCKED on the missing gauge-coordinate file.
- `docs/LABEL_ENGINE.md` — already contained the correct, corrected framing from Phase 4 (no further change needed; re-verified this phase by re-reading it in full).

No occurrence of these phrases was found in `README.md`, any notebook, any evaluation script, or any model card. (README.md did contain a **separate, unrelated** overclaim about the MTL backbone — see §7 below — which is not about FF/INDOFLOODS but was found during the same repo-wide claim-drift pass and corrected for the same reason.)

---

## 6. SIH Requirement Matrix Status (re-audited independently)

`docs/SIH_REQUIREMENT_MATRIX.md` was re-read in full and cross-checked against the current implementation (not merely re-stated). Two rows required correction:

- **Row #1 (Hyperlocal India-wide coverage):** previously said "992-cell, 0.25° grid" — internally contradictory (0.25° over these bounds is 15,125 cells, not 992) and a symptom of the same drift documented in §1. Corrected to "992-cell, 1.0° canonical application grid," with the now-passing drift-guardrail tests cited as evidence.
- **Row #5 (Flash-flood prediction):** corrected per §5 above.

No other row's **status** value changed — this phase found no evidence to upgrade or downgrade any other requirement, and none was changed without a specific finding. Rollup is unchanged: **IMPLEMENTED 4/35, PARTIALLY IMPLEMENTED 19/35, ARCHITECTURAL ONLY 6/35, DATA BLOCKED 3/35 direct (+ several indirect via #8), NOT IMPLEMENTED 2/35.**

Per the user's specified attention list:
- **IMDAA, INSAT-3D/3DR, satellite IWV, QPE (satellite):** all **DATA BLOCKED** — no credentials, no network route from this environment (confirmed again this phase by the absence of any successful fetch anywhere in the repo's run history or outputs).
- **DEM, slope, drainage/catchment:** DEM/slope are **PARTIALLY IMPLEMENTED** (real SRTM, Bengaluru-only). Drainage/catchment for FF is **NOT IMPLEMENTED** — the specific missing dependency is `data/catchment_characteristics_indofloods.csv` (or an equivalent open gauge-coordinate dataset).
- **Common spatiotemporal grid:** **ARCHITECTURAL ONLY** — `regrid.py` is real and tested, but only GFS/Himawari/DEM have any real data to regrid; IMDAA/INSAT are blocked.
- **TS labels:** PARTIALLY IMPLEMENTED (VOBL-only, 0.1% pan-India coverage, explicitly UNKNOWN elsewhere — Phase 4 label engine, unchanged this phase).
- **CB labels:** PARTIALLY IMPLEMENTED at the model level, but note the label engine itself now has genuinely pan-India coverage (992/992 cells) — the remaining gap is training/validating a model on that pan-India label set, not the label set itself.
- **FF labels:** NOT IMPLEMENTED for genuine events (missing dependency: gauge-coordinate file); PARTIALLY IMPLEMENTED as a rainfall proxy, now correctly labeled as such everywhere it's referenced.
- **Unified MTL, transformer, cross-attention, simultaneous TS/CB/FF prediction:** **ARCHITECTURAL ONLY** — `backend/mtl_backbone.py` is a real, tested transformer architecture but is untrained and not in the production ensemble (see §7). "Cross-attention" specifically: not present in the current architecture at all (`mtl_backbone.py` uses shared-encoder + separate heads, not cross-attention between hazards) — no occurrence of a cross-attention claim was found anywhere in the repo, so nothing needed correcting there.
- **2-6 hour lead time:** PARTIALLY IMPLEMENTED — real, wired metadata at the station level (`lead_time.py`, `test_lead_time.py` 7/7 pass); not yet extended to the pan-India grid.
- **XAI:** PARTIALLY IMPLEMENTED — real SHAP for VOBL XGBoost only, explicitly not implemented for the (untrained) MTL transformer.
- **Alert API, automated alerts:** alert **API** is NOT IMPLEMENTED (no Flask/FastAPI route layer found). Automated alert **delivery** (WhatsApp, real SENT/FAILED/SKIPPED states) is PARTIALLY IMPLEMENTED, VOBL-only, no severity tiering.

---

## 7. Claim-Drift Audit

Repo-wide search performed for every term in the user's specified list (`real-time`, `real time`, `live`, `pan-India`, `nationwide`, `MTL`, `transformer`, `cross-attention`, `cloudburst`, `flash flood`, `observed`, `INDOFLOODS`, `IMDAA`, `INSAT`, `IWV`, `QPE`, `2-6 hour`, `5.3 hour`, `83%`, `17%`, `AUROC`, `POD`, `FAR`) across `.py`/`.md`/`.js`/`.html` files. `nationwide`, `cross-attention`, and `5.3 hour` returned **zero hits** anywhere in the repo — no claim to correct. Given the volume of legitimate, already-accurate metric references (`POD`/`FAR`/`AUROC` appear in dozens of test files and dev scripts reporting real, already-computed numbers), this phase focused verification effort on the highest-visibility, judge-facing claim surfaces (`README.md` and `docs/*.md`) rather than exhaustively re-litigating every internal comment.

One additional, previously-uncorrected overclaim was found in `README.md` (separate from the FF/INDOFLOODS issue in §5):

- **README's "Multi-Task Learning Backbone" section** previously described `mtl_backbone.py` as a "3-layer MLP" (it is actually a 4-layer transformer) that is "used as an ensemble member alongside the standalone models... weighted average: 0.6 XGBoost/RF/LR + 0.4 MTL head. This consistently outperformed either alone on the holdout." **This is unsupported by the code:** no trained MTL weights file (`mtl_backbone.pt` or any `.pt` MTL checkpoint) exists anywhere in the repository, and no production inference path imports or calls `mtl_backbone.py` — confirmed by `grep -rn "mtl" backend/forecast_action.py` and `forecast_action.py` (root) returning zero hits. Classified **UNSUPPORTED**, corrected in place (README.md, three locations: the architecture section itself, the file-tree listing, and the v3 changelog entry) to state plainly that the backbone is real, tested architecture but untrained and not part of the live ensemble.

Classification of the remaining terms found, at the doc level:

| Term | Where found | Classification |
|---|---|---|
| `real-time` / `live` | README (dashboard, GFS refresh cadence), `sw.js` network-first behavior | **SUPPORTED** — GFS grid does refresh 4x/day via a real scheduled workflow, dashboard is genuinely live-deployed on Cloudflare Pages |
| `pan-India` | README opening, grid docs | **SUPPORTED ONLY AS A PROXY** for CB/FF/TS hazard layers (physics-proxy, not validated ML, except CB labels which are genuinely pan-India at the label-engine level); **SUPPORTED** for the grid's geographic extent itself |
| `MTL`, `transformer` | README, `mtl_backbone.py`, requirement matrix | **ARCHITECTURAL/FUTURE** — real code, untrained, not in production (corrected this phase, see above) |
| `cloudburst` | throughout | **SUPPORTED ONLY FOR VOBL** (real ML) / **SUPPORTED ONLY AS A PROXY** pan-India (physics grid) / genuinely pan-India at the **label-engine** level as of Phase 4 |
| `flash flood` | throughout | **SUPPORTED ONLY FOR VOBL, AND ONLY AS A PROXY** — corrected this phase (§5) |
| `observed` | label engine docs, requirement matrix | **SUPPORTED** for TS (VOBL METAR) and CB (IMD gauge-gridded rainfall); **NOT SUPPORTED** for FF prior to this phase's correction |
| `IMDAA`, `INSAT`, `IWV` (satellite) | requirement matrix, data-spec docs | **DATA BLOCKED**, already honestly labeled as such since Phase 2 |
| `QPE` | requirement matrix | **SUPPORTED ONLY AS A PROXY** (GFS APCP), real satellite QPE blocked on INSAT |
| `83%` / `17%` | historical POD/FAR improvement figures in `forecast_action.py`/`dev/october_threshold_fix.py` comments | **SUPPORTED** — these are real, already-computed threshold-tuning results (`POD 0.379 -> 0.621, FAR 0.167 -> 0.474`), not the 83%/17% figures literally; no mismatch found once the actual surrounding text was read in context |

No documentation was deleted to fix a claim; every correction above kept the original sentence and added the accurate qualifying context next to it, consistent with the instruction not to remove useful documentation blindly.

---

## 8. New Guardrails

`tests/test_integrity_guardrails.py` (new, this phase) — 12 tests, **25/25 checks passed** (some tests assert multiple conditions):

**A. Grid drift (6 tests):** `pipeline.py`'s locked constants are present and correct; the locked-constant arithmetic reproduces exactly 992 cells; the live on-disk grid matches (992 cells, 1.0° step); the `cell_id_for()` convention is deterministic and collision-free; a synthetic duplicate-cell-id set is correctly caught; a 0.25° step is confirmed to *not* produce 992 cells (i.e. the guardrail would correctly fire if the old bug returned).

**B. Label semantic drift (6 tests):** no FF proxy row is ever bare `POSITIVE` (must be `PROXY_NOT_OBSERVED`); `UNKNOWN` rows never carry a concrete 0/1 label; the three label-construction scripts contain no predictor-field column reads (AST-checked, not just string-grepped); FF's genuine-event status is never claimed available without the coordinate source (and the coordinate file's absence is asserted directly); TS labels carry no future-dated source timestamps; no duplicate `(timestamp, cell_id, hazard)` rows exist in the generated CB output.

Also embedded directly in `backend/pipeline.py` itself (not just in the test suite, so it fires in production, not only in CI): a `RuntimeError` if the generated application grid has other than exactly 992 cells, and a second `RuntimeError` if any duplicate cell ID is detected — both raised *before* the file is ever written.

---

## 9. Remaining Blockers

Unchanged from Phase 4, confirmed still accurate this phase — none of these are new, and none were invented for this report:

1. **Pan-India TS labels:** blocked on the absence of any pan-India lightning/station network dataset in this repo. VOBL remains the only station with genuine TS observations.
2. **Genuinely observed FF labels:** blocked on the missing `data/catchment_characteristics_indofloods.csv` (or an equivalent gauge-coordinate source). Until this exists, FF can only ever be a rainfall proxy in this repository.
3. **IMDAA / INSAT-3D/3DR integration:** blocked on credentials and network access not available in this environment (confirmed again this phase — no new fetch attempt succeeded).
4. **Pan-India DEM/terrain:** blocked only on effort (public SRTM tiles, not a credential issue) — not attempted this phase, out of scope.
5. **Trained MTL model:** blocked on running the existing Colab/A100 package (delivered in an earlier pass) to completion on the user's own GPU — not something this sandbox can do.
6. **`dev/pan_india_gfs_fetcher.py` stale duplicate:** not a blocker for Phase 5, but flagged in §3 as a latent risk worth cleaning up in a future pass.

---

## 10. Exact Prerequisites for Phase 5

Given the above, Phase 5 (final MTL training-dataset construction) can proceed for **CB only** without any further prerequisite — CB has genuine pan-India labels, a locked and guarded grid, and no known integrity issue. Phase 5 must **not** claim pan-India TS or FF supervised coverage unless and until:

- a pan-India TS observation/lightning source is identified and integrated, or the dataset explicitly documents TS as VOBL-only (matching current label-engine behavior — this is an acceptable and honest scope, not a blocker, provided it is stated), and
- the FF gauge-coordinate file (or a substitute) is obtained, or the dataset explicitly documents FF as proxy-only (also acceptable and honest, provided it is stated).

Neither of these is a hard blocker to *starting* Phase 5 dataset construction, provided Phase 5's own documentation states the same scope limits this report states. They are hard blockers only to *claiming* genuine pan-India TS/FF supervision, which Phase 5 must not do.

### PHASE_5_READY = YES

Phase 5 may proceed, on the explicit condition that it inherits and does not silently drop the scope limits documented here: CB dataset construction may proceed as genuinely pan-India; TS and FF dataset construction may proceed only as VOBL-only / proxy-only respectively, each clearly labeled as such in whatever dataset manifest Phase 5 produces — mirroring exactly how Phase 4's label files already are.

---

## Files Changed This Phase

- `backend/pipeline.py` — grid-constant split, drift guardrail, duplicate-ID guardrail, metadata fields (see §1, §6).
- `regrid.py` — docstring correction only (the old docstring described the pre-fix ambiguous `GRID_STEP`; corrected to describe the two new constants). No functional change; `test_regrid.py` re-verified passing after the edit.
- `docs/SIH_REQUIREMENT_MATRIX.md` — rows #1 and #5 corrected (see §5, §6).
- `README.md` — "Multi-Task Learning Backbone" section, file-tree entry, and v3 changelog line corrected (see §7).

## Files Created This Phase

- `tests/test_integrity_guardrails.py` — 12 new tests, 25/25 checks passing.
- `docs/PHASE_4_5_INTEGRITY_AUDIT.md` — this file.

## Tests Run, Exact Counts

| Suite | Result |
|---|---|
| `test_regrid.py` (pre-existing) | ALL CHECKS PASSED (8/8) |
| `tests/test_canonical_grid.py` (Phase 3) | 28/28 passed |
| `tests/test_panindia_labels.py` (Phase 4) | 19/19 passed |
| `tests/test_integrity_guardrails.py` (new, this phase) | 25/25 passed |
| **Total** | **80/80 passed, 0 failed** |

No test was modified to make it pass. No test was deleted.

## Is 992-Cell Drift Now Impossible?

**Not impossible in the abstract (someone could still edit the constants), but now safely guarded against *silent* drift.** If `APPLICATION_GRID_STEP` or `BOUNDS` is ever changed such that the generated grid has other than exactly 992 cells, `backend/pipeline.py` raises a `RuntimeError` and refuses to write `data/pan_india_grid.json` — the scheduled workflow run would fail loudly (and visibly, in GitHub Actions logs) rather than silently overwriting the canonical file. A deliberate, intentional resolution change is still possible, but only by editing `EXPECTED_APPLICATION_CELL_COUNT` and `docs/CANONICAL_GRID.md` together — the guardrail's error message says so explicitly.
