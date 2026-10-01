# SIH A-to-Z Execution Plan

Audit-only document. This is a dependency-ordered plan, not a schedule of work already started. Nothing here has been implemented as part of this audit phase.

Dependency chain respected throughout: **DATA → LABELS → FEATURES → MODEL → VALIDATION → INFERENCE → MAP → ALERTS → UI → DEPLOYMENT → DEMO**

Priority key: P0 = blocks scientific validity / core SIH claim. P1 = blocks core product functionality. P2 = important improvement. P3 = polish.

---

### P0-1. Confirm and complete historical GFS acquisition (DATA) — **DONE, verified Phase 0.4.21**
- **Objective**: establish, with reproducible evidence, exactly how many of the 40 manifest files are acquired and verified right now.
- **Files**: `docs/PHASE_0_4_17_ACQUISITION_MANIFEST.csv`, `data/external/historical_gfs/manifest.json`, `scripts/acquire_phase_0_4_19_batch.py`
- **Prerequisite**: none
- **Expected evidence**: a fresh run of the independent verifier against the real raw directory, output captured in a dated doc
- **Acceptance criteria**: 40/40 files present, SHA256-verified, with the verifier's literal output saved
- **Tests**: existing acquisition verification tests (if any) + manual verifier run
- **Affects production?** No. **Requires data acquisition?** Possibly (if incomplete). **GPU?** No. **Manual physical testing?** No.
- **STATUS: DONE.** Live-verified in `docs/PHASE_0_4_21_LIVE_STATE_VERIFICATION.md` — 40/40 files confirmed present with matching acquisition-time and build-time SHA256, 0 missing/invalid/partial/ambiguous.

### P0-2. Build and preserve the real Phase 0.4.20 TRAIN/HOLDOUT CSVs — **DONE, verified Phase 0.4.21**
- **Objective**: produce an actual, on-disk, built TRAIN and HOLDOUT dataset (not just a passing manifest/test suite).
- **Files**: `scripts/build_phase_0_4_20_dataset.py`
- **Prerequisite**: P0-1
- **Expected evidence**: `--partition train` and `--partition holdout` runs both exit GREEN and produce real CSVs with the exact row/label counts already verified in the manifest (28/12 rows).
- **Acceptance criteria**: both CSVs exist, event-group-key partition separation holds, 0 leakage violations.
- **Tests**: `tests/test_phase_0_4_20_dataset_builder.py` (41/41 passing already) + a manual real run.
- **Affects production?** No. **Data acquisition?** No (uses already-acquired files). **GPU?** No. **Manual testing?** No.
- **STATUS: DONE.** Both CSVs exist (28 TRAIN rows/14 groups, 12 HOLDOUT rows/6 groups), exactly match the manifest design, cross-check cleanly against the 40-file acquisition with zero discrepancies, and both report `validation_status: GREEN`. See `docs/PHASE_0_4_21_LIVE_STATE_VERIFICATION.md`. P0-3 (data-scale sufficiency) remains open and is now the active gate.

### P0-3. Acknowledge and plan around data-scale insufficiency
- **Objective**: formally document that N=28 train / N=12 holdout is not sufficient for a generalizable model, and decide the real target sample size before further model work.
- **Files**: new doc, e.g. `docs/DATA_SCALE_PLAN.md`
- **Prerequisite**: P0-2
- **Expected evidence**: a stated target N (e.g. hundreds of event groups across more years/stations) and a realistic acquisition plan to get there.
- **Acceptance criteria**: a written, defensible minimum-N justification exists before any "final model" claim is made.
- **Priority**: P0. **Affects production?** No.

### P0-4. Resolve IMDAA access
- **Objective**: obtain real IMDAA credentials/access and acquire at least a pilot slice of real IMDAA data.
- **Files**: `scripts/acquire_imdaa.py`
- **Prerequisite**: none (external dependency — institutional access)
- **Expected evidence**: real IMDAA NetCDF/GRIB files on disk with a provenance manifest.
- **Acceptance criteria**: at least one real IMDAA variable ingested and diffed against the GFS proxy currently used.
- **Requires data acquisition?** Yes. **GPU?** No.

### P0-5. Resolve INSAT-3D/3DR access
- **Objective**: obtain real INSAT access and replace the GFS-derived CTT proxy with real satellite CTT for at least a pilot window.
- **Files**: `backend/fetch_insat3d.py`
- **Prerequisite**: none (external access)
- **Expected evidence**: real INSAT brightness-temperature files on disk.
- **Acceptance criteria**: CTT/CTT-drop features computed from real INSAT data for at least one verified case.
- **Requires data acquisition?** Yes.

### P0-6. Establish an observed flash-flood label source (LABELS)
- **Objective**: replace or supplement the rainfall-threshold FF proxy with labels tied to actual observed flood events (INDOFLOODS or IMD flood reports).
- **Files**: `docs/INDOFLOODS_LABEL_DEFINITION.md`, `scripts/build_panindia_ff_labels.py`
- **Prerequisite**: none
- **Expected evidence**: a documented FF label definition with real event counts, positive/negative/unknown breakdown, spatial/temporal coverage.
- **Acceptance criteria**: at least a pilot FF-labeled dataset with real observed events, reviewed for leakage the same way TS labels were.
- **Requires data acquisition?** Possibly (if INDOFLOODS needs further processing).

### P0-7. Train and checkpoint the shared MTL backbone, or formally shelve it (MODEL)
- **Objective**: resolve the architecture-vs-trained gap for `backend/mtl_backbone.py` — either actually train it on real data (dependent on P0-1/P0-2/P0-6 for enough data) and preserve the checkpoint in the repo/artifact store, or explicitly document it as a research-only architecture not on the critical path, and remove/soften any UI/README language implying it's in production.
- **Files**: `backend/mtl_backbone.py`, `colab/train_mtl_blr.py`
- **Prerequisite**: P0-2, P0-3, P0-6 (need enough real labeled data to train meaningfully)
- **Expected evidence**: a saved checkpoint + training run log, or an explicit decision doc.
- **Acceptance criteria**: either `forecast_action.py`/`backend/pipeline.py` imports and uses the trained model with demonstrated skill over the independent-model baseline, or the README/UI no longer imply it is production.
- **Requires GPU?** Yes, for real training.

### P0-8. Confirm CB/FF model training parity with TS (MODEL)
- **Objective**: trace exactly what trains `models/cb_slot_*`/`models/ff_slot_*` and confirm they meet the same evidentiary bar as the TS models (real data, real labels, documented validation).
- **Files**: training scripts referenced by `models/cb_ff_training_summary.json`
- **Prerequisite**: none
- **Expected evidence**: a training log/manifest for CB and FF models equivalent to what exists for TS.
- **Acceptance criteria**: CB/FF either confirmed trained-and-validated to the same standard, or explicitly marked YELLOW/GRAY with the gap documented.

### P1-1. Train a pan-India ML model to replace the heuristic (MODEL, depends on P0-1..P0-6)
- **Objective**: once enough real labeled data exists beyond VOBL, train an actual model for pan-India cells instead of the fixed-coefficient formula.
- **Files**: `backend/pipeline.py`
- **Prerequisite**: P0-1 through P0-6
- **Acceptance criteria**: a trained model with validated skill beats the current heuristic on held-out data.
- **Requires GPU?** Likely yes.

### P1-2. Add visual provenance distinction between ML and heuristic map layers (UI)
- **Objective**: a user must be able to tell, without reading documentation, whether a cell's hazard score comes from a trained model or a heuristic.
- **Files**: `index.html` (map layer rendering)
- **Prerequisite**: none (can be done immediately, independent of model work)
- **Acceptance criteria**: a visible badge/legend distinguishing "VOBL — trained model" from "Pan-India — heuristic proxy".
- **Affects production?** Yes (UI change — out of scope for this audit phase, listed here for the roadmap only).

### P1-3. Add alert de-duplication / cooldown (ALERTS)
- **Objective**: prevent repeat SMS/WhatsApp alerts for the same sustained hazard every cron cycle.
- **Files**: `backend/dispatch_alerts.py`, `send_alerts.py`
- **Prerequisite**: none
- **Acceptance criteria**: a documented cooldown window; a second alert within the window is suppressed or clearly marked as a continuation.
- **Affects production?** Yes.

### P1-4. Close the automated verification gap (VALIDATION)
- **Objective**: make `generate_alert_log.py`'s HIT/MISS/FAR/CSI/HSS summary actually reflect automated outcomes, or stop presenting it as a live rolling metric.
- **Files**: `generate_alert_log.py`, `.github/workflows/forecast_update.yml`
- **Prerequisite**: an automated ground-truth observation source (could depend on P0-6's work for flood observations; TS observation automation may be achievable sooner via METAR).
- **Acceptance criteria**: either CI passes real `--observed`/`--no-storm` values automatically, or the UI badge is downgraded from "VERIFIED" to "back-tested on manually-confirmed days."

### P1-5. Confirm or wire the subscription UI to the Worker `/subscribe` endpoint (API/UI)
- **Objective**: resolve the UNKNOWN of whether the dashboard's subscribe form actually calls `/subscribe`.
- **Files**: `index.html`, `worker/index.js`
- **Prerequisite**: none
- **Acceptance criteria**: a traced, confirmed call path from the UI form to the Worker endpoint, or a fix if it's missing.

### P2-1. Decide the fate of `backend/alerts.py` (API)
- **Objective**: either wire the FastAPI alerts service into a real workflow or clearly document it as a dev-only/manual tool, not a production API.
- **Files**: `backend/alerts.py`
- **Acceptance criteria**: README/docs no longer imply it's part of the live pipeline unless it actually is.

### P2-2. Add a real QPE source (FEATURES)
- **Objective**: replace the GFS precipitation-rate QPE proxy with IMERG or IMD radar QPE where feasible.
- **Prerequisite**: data access for IMERG/IMD radar.
- **Requires data acquisition?** Yes.

### P2-3. Extend DEM/terrain use to pan-India and as a trained feature (FEATURES)
- **Objective**: use terrain data as a trained model feature (not just a post-hoc multiplier) and extend its use beyond VOBL.
- **Prerequisite**: P1-1 (pan-India model).

### P3-1. Verify live deployment reachability
- **Objective**: confirm the Cloudflare Pages URLs are actually live and serving current data.
- **Requires manual/physical testing?** Yes (an actual HTTP check against the deployed URL, outside this sandboxed audit).

### P3-2. Add hazard-specific actionable guidance text (UI)
- **Objective**: replace generic "take precautions" alert text with hazard-specific guidance.

### P3-3. Responsive/mobile UI audit completion
- **Objective**: complete the UNKNOWN items from the frontend audit (mobile layout, loading/empty states) with a dedicated pass.

---

## Dependency diagram (textual)

```
P0-1 (acquire 40/40 GFS)
  -> P0-2 (build real TRAIN/HOLDOUT CSVs)
       -> P0-3 (scale-sufficiency plan)
       -> P0-7 (train/checkpoint MTL, needs enough data)
       -> P1-1 (train pan-India model, needs enough data + more sources)
P0-4 (IMDAA access) --independent--> feeds future feature work
P0-5 (INSAT access) --independent--> replaces CTT/CTT-drop proxy (rows 14-15 of matrix)
P0-6 (observed FF labels) --independent--> feeds P0-7, P1-1, and any FF model work
P0-8 (confirm CB/FF training parity) --independent-->  informs whether P1-1 can reuse VOBL model patterns

P1-2 (map provenance badge) --independent, UI only, no data dependency
P1-3 (alert cooldown) --independent, no data dependency
P1-4 (close verification gap) --depends on an automated ground-truth source
P1-5 (confirm subscribe UI wiring) --independent

P2/P3 items are independent polish, dependent only on P0/P1 being true before claiming them as "final."
```
