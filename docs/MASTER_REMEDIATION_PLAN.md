# MASTER Remediation Plan — 2026-10-01

Grouped P0 (SIH-critical scientific/data blockers) through P6 (docs/demo). Ranked by actual scientific
and credibility risk, not by UI polish.

## P0 — SIH-critical scientific/data blockers

### P0.1 — Production flash-flood model is trained/evaluated against a proxy label, not an observed flood
- **Problem**: `forecast_action.py`'s FF XGBoost is trained against `processed/labels/ff_labels_proxy.csv`
  (`label_status=PROXY_NOT_OBSERVED`, a rainfall-threshold rule), not a real INDOFLOODS flood event.
  Any accuracy number quoted for this model does not demonstrate flash-flood prediction.
- **Root cause**: the gauge-coordinate file needed to spatially join real INDOFLOODS events to pan-India
  cells was never obtained; the labeling script silently falls back to the rainfall proxy.
- **Required data**: the missing gauge-coordinate/catchment join file, or an equivalent open dataset.
- **Files involved**: `dev/Fetch cb ff labels.py`, `processed/labels/ff_labels_proxy.csv`,
  `train_cb_ff_models.py`, `forecast_action.py`.
- **Implementation approach**: either (a) source the missing file and re-run the real labeling path, or
  (b) if unobtainable before the SIH deadline, relabel every FF output surface (API, dashboard, docs) to
  say explicitly "rainfall-proxy flash-flood risk," not "flash-flood prediction."
- **Validation method**: re-run the label-provenance test (`test_panindia_labels.py::test_11_source_provenance`)
  against the new label source; confirm `label_status` flips from PROXY to OBSERVED for a non-trivial
  fraction of training rows.
- **Risk**: presenting current FF numbers as-is to SIH judges without this caveat is the single biggest
  credibility exposure in the project.
- **Dependencies**: none technical; data-acquisition only.
- **Definition of done**: either real observed-flood labels are used, or every user-facing FF claim is
  relabeled as proxy-based, with no exceptions.

### P0.2 — IMDAA and INSAT-3D/3DR are not integrated; "satellite IWV" and "QPE" are proxies
- **Problem**: IMDAA (`raw/imdaa/` empty) and INSAT (credentials unset) are not wired in. "IWV" in the
  code is GFS PWAT; "QPE" is GFS APCP. These are reasonable proxies but must never be presented as the
  real satellite-observed variables the SIH problem statement names.
- **Root cause**: both require external registration/credentials not available to the team.
- **Required data**: NCMRWF IMDAA registration; MOSDAC INSAT credentials.
- **Files involved**: `raw/imdaa/`, `processed/imdaa/`, `backend/fetch_insat3d.py` (stub).
- **Implementation approach**: pursue registration in parallel with everything else; in the meantime,
  audit every doc/UI string that says "satellite IWV" or "QPE" and ensure it says "GFS-derived proxy."
- **Validation method**: grep sweep for "IWV"/"QPE"/"satellite" across `index.html` and `README.md`
  after relabeling; manual confirmation no string overstates the source.
- **Risk**: judges catching a mislabeled variable undermines trust in every other claim in the project.
- **Dependencies**: none technical.
- **Definition of done**: zero instances of proxy data described as the real satellite product anywhere
  user-facing.

### P0.3 — Pan-India TS/CB/FF scores are an uncalibrated heuristic formula, not ML
- **Problem**: `backend/pipeline.py::hazard_probabilities()` is a hand-weighted linear formula with no
  fitting, training, or validation against labeled data. It drives the pan-India map that is the
  project's visual centerpiece.
- **Root cause**: no pan-India labeled dataset existed at the time these coefficients were chosen.
- **Required data**: the Phase 4/4.5 pan-India CB labels (described as genuinely observed in the prior
  matrix) are the nearest candidate; would need equivalent labels for TS and FF.
- **Files involved**: `backend/pipeline.py`, `docs/PAN_INDIA_HAZARD_COEFFICIENTS.md`.
- **Implementation approach**: fit at minimum a logistic regression against the available pan-India CB
  labels as a first calibration pass; keep TS/FF heuristic but explicitly labeled as such until their
  own labels exist.
- **Validation method**: held-out AUROC/reliability diagram for the newly calibrated CB model vs. the
  current heuristic, on the same held-out cells.
- **Risk**: medium-high — if demoed as "ML-driven pan-India hazard map" without this caveat, a technical
  judge who reads `backend/pipeline.py` will find an uncalibrated formula.
- **Dependencies**: P0.1's labeling work shares infrastructure with this.
- **Definition of done**: either a calibrated model replaces the heuristic for at least one hazard, or
  every pan-India map legend/doc explicitly says "physics-based heuristic score, not a trained model."

## P1 — Model/architecture gaps presented as more complete than they are

### P1.1 — MTL/transformer backbone has no trained weights
- **Problem**: `backend/mtl_backbone.py` exists and is referenced in docs/README badges ("XGBoost + RF
  + MTL") implying a working multi-task model; it is untrained, single-timestep, and not spatiotemporal.
- **Files involved**: `backend/mtl_backbone.py`, `README.md` badges.
- **Implementation approach**: either actually train it against available multi-hazard labels (requires
  P0.3's labels first) or remove/soften the MTL badge claim until it is trained.
- **Validation method**: held-out multi-task metrics vs. the independent XGBoost heads as baseline.
- **Risk**: medium — a direct question ("show me the MTL model's validation metrics") has no answer today.
- **Dependencies**: P0.3.
- **Definition of done**: MTL badge either backed by a trained+validated model, or removed/qualified.

### P1.2 — Convergence and CTT-drop-rate fields may still be `null` on the live pan-India grid
- **Problem**: prior audit found these schema fields defined but `null` on the on-disk file; not
  re-verified live this pass (sandbox has no network to re-run the pipeline).
- **Files involved**: `backend/pipeline.py`, `data/pan_india_grid.json`.
- **Implementation approach**: re-run the pipeline once network access exists; add a CI assertion that
  fails the workflow if either field is null in the output.
- **Validation method**: field-presence check added to `tests/test_integrity_guardrails.py`.
- **Risk**: low-medium — silently-null fields degrade the heuristic formula without anyone noticing.
- **Dependencies**: none.
- **Definition of done**: fields populated and CI-enforced non-null.

## P2 — Data hygiene

### P2.1 — Duplicate/ambiguous Bengaluru training datasets
- **Problem**: four near-duplicate training CSVs on disk (`bengaluru_6hr_training_dataset.csv`, `_v3`,
  `_v4`, `_cb_ff`) with no documented canonical version.
- **Files involved**: `data/bengaluru_6hr_training_dataset*.csv`, `train_v6_slot_models.py`,
  `train_cb_ff_models.py`.
- **Implementation approach**: add a `docs/TRAINING_DATA_PROVENANCE.md` stating which file each current
  model version was trained on; archive or delete the rest.
- **Validation method**: manual review; no automated test needed.
- **Risk**: low but real — wrong-dataset retraining risk.
- **Dependencies**: none.
- **Definition of done**: one documented canonical file per model family.

### P2.2 — Many versioned model pickles with unclear runtime selection
- **Problem**: `models/` has v2 through v6/ensemble/calibrated variants per slot; which one
  `forecast_action.py` actually loads at runtime was not traced to completion this pass.
- **Files involved**: `forecast_action.py`, `models/*.pkl`.
- **Implementation approach**: add a single `MODEL_VERSION` manifest or trace+document the exact load
  path per slot; archive unused versions out of the live repo.
- **Validation method**: a test asserting the loaded model file for each slot matches the documented one.
- **Risk**: low-medium — risk of an unintended stale model being loaded.
- **Dependencies**: none.
- **Definition of done**: documented, test-enforced model-version mapping.

## P3 — Production reliability spot-checks (mostly confirmed intact, minor residual work)

- Atomic writes (`atomic_write.py`) confirmed used by `backend/pipeline.py`, `forecast_action.py`,
  `canonical_forecast_writer.py` — intact, no action needed.
- Workflow concurrency groups confirmed present in `update_grid.yml`/`forecast_update.yml` — intact.
- Freshness gating (`_file_freshness_state` in `canonical_forecast_writer.py`, FRESH/CACHED/STALE)
  confirmed present in code — intact.
- One flaky test (`test_gfs_row_select.py::test_out_of_order_rows_pick_newest`) fails today because it
  hardcodes a `fetched_at_utc` that has aged past its own staleness window as real calendar time has
  moved on — **action**: parametrize the test relative to `datetime.now()` instead of a fixed timestamp.

## P4 — UI/UX

- SHAP waterfall falls back to a hardcoded example array (`SHAP_DATA_FALLBACK`) with no confirmed
  "example data" label in the surrounding markup from this pass's grep — add an explicit on-screen
  indicator when fallback data is shown, so a judge never mistakes example SHAP values for live output.
- RAG/explainability backend is a free-tier Render service that cold-starts in 30-60s; confirm (live,
  with a browser) that the UI shows a clear loading state during this window rather than appearing broken.

## P5 — Alert/API polish

- README's architecture diagram implies SMS delivery; actual delivery channel is WhatsApp via
  CallMeBot only — fix the diagram or add real SMS as a second channel.

## P6 — Docs/demo

- Consolidate the 27 existing `docs/*.md` audit files behind this MASTER set with a short index doc so
  a judge or new contributor can find the authoritative current-state doc quickly instead of wading
  through phase-by-phase history.
- Update README badges ("XGBoost + RF + MTL") to match P1.1's resolution once decided.

## Recommended execution order

P0.1 and P0.2 first (data-acquisition parallel tracks, no code dependency on each other) → P0.3 (shares
infrastructure with P0.1) → P1.1 (depends on P0.3's labels) → P1.2 → P2 → P3 (quick, low-risk, do
anytime) → P4 → P5 → P6.
