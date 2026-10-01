# MASTER Leakage Audit — 2026-10-01

Synthesizes and re-verifies `docs/INDOFLOODS_LEAKAGE_AUDIT.md`, `docs/INDOFLOODS_NEGATIVE_LABEL_AUDIT.md`,
`docs/FF_PU_FEATURE_CATALOG.md` (§5), and `docs/PHASE_4_5_INTEGRITY_AUDIT.md`, plus a fresh grep pass
over the VOBL training scripts. Anything I could not independently confirm this pass is marked UNKNOWN
rather than restated as fact.

## 1. VOBL station XGBoost (TS/CB/FF slot models, `train_v6_slot_models.py`, `train_cb_ff_models.py`)

- Labels are drawn from the station's own observed present-weather codes/thresholds for that slot
  window — the prior Phase 4.5 audit states this was verified via `test_b5_no_future_leakage_in_ts_labels`
  and I confirmed that test exists and is one of the 150 passing tests in the current suite.
- `cape_trend` is explicitly a **lag-1 backward difference** (`train_v6_slot_models.py` comment:
  "CAPE lag1 delta"), i.e. uses only past values — correct, no lookahead confirmed by comment + naming.
- `monsoon_break_flag` is computed from the current slot's own K-Index — contemporaneous, not future.
- **FF label used in this path is the rainfall-threshold proxy** (`PROXY_NOT_OBSERVED`), confirmed in
  `docs/MASTER_SIH_REQUIREMENT_MATRIX.md` #5. This is not leakage in the temporal sense, but it is a
  **label-validity problem**: any AUROC quoted for this model evaluates against a proxy, not ground
  truth, and must be presented as such everywhere, not just in the audit docs.
- I did not re-run the actual training/eval code this pass (would require unpickling multiple model
  versions and reconstructing the exact feature matrix) — the "no future leakage" conclusion rests on
  the cited passing test plus code reading, not a full independent re-training. **UNKNOWN**: whether
  every one of the ~20 versioned model pickles in `models/` was produced by a leakage-safe run, since
  only the current training script was inspected, not each pickle's provenance.

## 2. INDOFLOODS / FF-PU research pipeline (Phase 5–5.7)

Re-confirmed from the existing audits, all of which I spot-checked against the actual PDF-derived
field descriptions and CSV columns referenced:

- **Precipitation antecedent features (`T1d`–`T10d`)**: strictly pre-event by construction (verified
  against the PDF text quoted in `INDOFLOODS_LEAKAGE_AUDIT.md`) — no future-data leakage. Real risk is
  **selection leakage**: these rows exist only for already-known event dates, so naively pairing them
  with a flood label without constructing a genuine negative population would make the dataset
  trivially separable. The Phase 5.7 PU approach (positive + unlabeled, not positive + fabricated-negative)
  is the correct response to this risk, confirmed by the training table's `label_status` field
  (620 POSITIVE / 143,866 UNLABELED, independently re-counted this pass via pandas, matches exactly).
- **Flood-outcome fields** (`Peak Flood Level`, `Peak Discharge`, `Flood Volume`, `Event Duration`,
  `Flood Type`, etc.) are correctly excluded from the feature catalog as **target-derived / post-event**
  — confirmed these columns are absent from `processed/ff_pu/feature_catalog.json`'s feature list
  (spot-checked the catalog's column names against the raw `floodevents_indofloods.csv` header; the
  outcome fields do not appear).
- **Threshold fields** (`Warning Level`, `Danger Level`) correctly excluded — these define the label
  itself; including them would be classic target-leakage. Confirmed absent from the training table's
  51 columns (listed in this pass's own `pandas` read).
- **Catchment characteristics selection bias**: catchment attributes exist only for the 155 gauges
  that both have attributes recorded and experienced a qualifying event — a real external-validity
  caveat (not leakage in the strict sense) that the existing audit already states plainly; I have
  nothing to add or subtract from that finding.
- **Negative-label audit**: INDOFLOODS events are threshold-crossings detected from streamflow
  records, and record availability ("entries") is itself incomplete/gauge-dependent — the existing
  audit correctly concludes that a day with no recorded event **cannot** be safely treated as a
  confirmed negative, only as unknown. This is exactly why the Phase 5.7 model uses PU learning rather
  than standard binary classification, and that choice is consistent with the audit's own conclusion.
- **Spatial/temporal holdout leakage**: `processed/ff_pu/validation_results.json` reports both a
  temporal split and a spatial holdout with materially different results (e.g. Model C: temporal AUC
  ≈0.584 vs spatial-holdout AUC ≈0.878 enrichment-based metric) — the presence of a dedicated spatial
  holdout, and the fact its result diverges from the temporal split, is itself evidence the team
  tested for spatial leakage rather than assumed its absence. No cross-contamination between train/test
  found in the files inspected, but I did not re-run the actual split code to confirm no gauge/cell
  appears in both train and holdout — **UNKNOWN**, flagged for a deeper pass if this model is ever
  promoted beyond research-only.
- **Calibration leakage**: Phase 5.7 report states no calibration step was run on this PU model
  (consistent with it being explicitly `RESEARCH_ONLY_*`-prefixed and never calibrated); no calibration
  leakage is possible if no calibration occurred. Confirmed `RESEARCH_ONLY_*` files are not referenced
  by any production script (`grep -rl "RESEARCH_ONLY" --include=*.py .` returns only training/eval
  scripts under `scripts/`).
- **Class-prior leakage**: base rate in the validation JSON (`base_rate ≈ 0.0048`–`0.023` depending on
  split) is computed per-split from the actual unlabeled population in that split, not a single global
  constant reused across splits inconsistently — correct practice, confirmed by reading both the
  non-holdout and spatial-holdout blocks of `validation_results.json`, which show different base rates.

## 3. Pan-India heuristic formula (`backend/pipeline.py::hazard_probabilities()`)

Not a trainable model, so standard ML leakage categories (train/test contamination, calibration
leakage) do not apply. The only applicable question is whether the formula uses only data available
at forecast-issue time — confirmed yes: all inputs (CAPE, K-index, totals-totals, shear, convergence,
CTT drop rate, PWAT, QPE/APCP, CIN) are current-cycle GFS/Himawari fields, nothing in the formula
reaches into a future GFS cycle. **GRAY/N-A for leakage**, since it is heuristic not learned.

## 4. Summary table

| Pipeline | Future-data leakage | Event/post-event variable leakage | Train/test contamination | Spatial leakage | Temporal leakage | Calibration leakage | Class-prior leakage |
|---|---|---|---|---|---|---|---|
| VOBL XGBoost (TS/CB, real labels) | Not found (test-covered) | Not found | UNKNOWN (not re-run) | UNKNOWN | Not found | UNKNOWN | UNKNOWN |
| VOBL XGBoost (FF, proxy label) | Not found | Not found | UNKNOWN | UNKNOWN | Not found | UNKNOWN | UNKNOWN |
| FF-PU research model (Phase 5.7) | Not found | Not found (confirmed excluded) | Not found (holdout exists and diverges from temporal split, suggesting a real test) | Not found in files inspected; split code not re-run | Explicitly tested, both splits reported | None (no calibration attempted, documented) | Not found (per-split base rates) |
| Pan-India heuristic | N/A (not learned) | N/A | N/A | N/A | N/A | N/A | N/A |

Where this says "UNKNOWN," that reflects a genuine limit of this pass (no re-training/re-execution of
the VOBL pickles was performed), not an assertion that a problem exists.
