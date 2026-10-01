# SIH A-to-Z Final Completion Checklist

Audit-only. This is the release checklist to use going forward; nothing below is being marked complete just because related code exists. Each item's acceptance criterion is concrete and checkable against evidence, matching the Master Requirement Matrix and Execution Plan.

## DATA
- [x] All 40 Phase 0.4.17 manifest GRIB2 files present on disk and SHA256-verified by the independent verifier, with saved output (P0-1) — **verified Phase 0.4.21**, see `docs/PHASE_0_4_21_LIVE_STATE_VERIFICATION.md`
- [ ] IMDAA real data acquired for at least a pilot window (P0-4)
- [ ] INSAT-3D/3DR real data acquired for at least a pilot window (P0-5)
- [ ] A documented minimum viable sample size (N) for a defensible model is stated and justified (P0-3)

## LABELS
- [ ] TS label definition, source, and counts documented and reproducible from real files (currently: documented, VOBL-only, N≤40)
- [ ] CB label definition confirmed with the same rigor as TS (currently UNKNOWN — P0-8)
- [ ] An observed-flood-based FF label source exists, distinct from the rainfall proxy (P0-6)
- [x] Event-group leakage checks pass against the real built dataset, not only unit tests with synthetic fixtures (P0-2) — **verified Phase 0.4.21**: 0 cross-partition violations, 0 unexpected labels, confirmed against the real built TRAIN/HOLDOUT CSVs. Note: 3 of 6 holdout groups are concentrated in one 15-hour window (not leakage, but flagged as reduced sample diversity — see Phase 0.4.21 report §11)

## FEATURES
- [ ] Every feature in the dataset is correctly classified (real observation / real forecast / derived / proxy / heuristic) in public-facing docs, matching the Feature Audit in this effort
- [ ] CTT/CTT-drop computed from real satellite data, not the GFS proxy, for at least a pilot case (depends on P0-5)
- [ ] QPE computed from a real precipitation product (IMERG/radar), not the GFS rate proxy, for at least a pilot case (P2-2)

## MODELS
- [ ] CB/FF model training pipeline traced and confirmed equivalent in rigor to TS (P0-8)
- [ ] MTL transformer backbone either trained with a preserved checkpoint and demonstrated benefit, or formally documented as research-only / out of the production path (P0-7)
- [ ] A trained (not heuristic) pan-India model exists and is validated against held-out data (P1-1)
- [ ] Model versions are traceable (filenames/hashes/manifests) for every model that ships to production — currently true for VOBL XGBoost models, confirm for any new model

## VALIDATION
- [ ] Every metric shown anywhere (UI, docs, PPT) is traced to its exact dataset, split, model version, and label definition, with the trace recorded in a doc
- [ ] No metric computed on N<30 samples is presented without an explicit small-sample caveat
- [ ] Automated verification (HIT/MISS/FAR/CSI/HSS) reflects real automated outcomes, not a manually-gated process silently defaulting to PENDING (P1-4)
- [ ] Pan-India heuristic outputs are never presented using the same metrics/validation language as the VOBL trained model

## LEAD TIME
- [ ] 2/4/6-hour lead-hour population logic confirmed to use `UNAVAILABLE` rather than duplicated/stale values when the real GFS cycle can't support that lead (already implemented in `canonical_forecast_writer.py` — confirm it still holds after any future change)
- [ ] Lead-time claims in UI/docs match the actual LEAD_HOURS constant in code

## PAN-INDIA
- [ ] 992-cell canonical grid construction documented and reproducible
- [ ] Pan-India hazard output is clearly labeled heuristic vs ML wherever it appears (until P1-1 lands)
- [ ] A trained pan-India model exists and has replaced (or sits alongside, clearly labeled) the heuristic (P1-1)

## XAI
- [ ] Real per-run SHAP coverage measured (what fraction of slots/cells get real SHAP vs the fallback) and reported
- [ ] The static fallback visualization remains clearly labeled as illustrative whenever shown (already implemented — confirm it is not silently removed in a future UI change)

## ALERTS
- [ ] Alert de-duplication/cooldown implemented (P1-3)
- [ ] Delivery status fields (SUCCESS/FAILED/NOT_CONFIGURED/SKIPPED_NO_ALERT) continue to reflect genuine provider HTTP results, not optimistic defaults (already implemented — regression-test it)
- [ ] Subscribe UI confirmed wired to the real `/subscribe` endpoint (P1-5)

## API
- [ ] Every documented endpoint matches an actually-implemented route (confirm `dev/API_EXAMPLES.md` against `worker/index.js`/`backend/alerts.py`)
- [ ] `backend/alerts.py` either wired into production or documented as dev-only (P2-1)
- [ ] `/subscribers` endpoint's data exposure (unmasked phone/API key behind admin key) reviewed for acceptable risk given it's an internal admin route

## REAL-TIME PIPELINE
- [ ] Atomic writes confirmed for every artifact that gates a user-facing claim (forecast.json, canonical_forecast.json, pan_india_grid.json — already implemented; pipeline_health.json/alert_history.json/alert_log.json are not atomic — decide if that's acceptable)
- [ ] Concurrency protection extended to `drift_check.yml`/`retrain_trigger.yml`/`weekly_digest.yml` if concurrent runs become a real risk
- [ ] The documented residual git-pull race between workflows is either closed or formally accepted as low-risk with reasoning recorded

## RELIABILITY
- [ ] Recalibration failure in `drift_check.yml` escalates (alerts a human) rather than silently continuing with stale models indefinitely
- [ ] `forecast_update.yml`'s non-blocking staleness warning (">3h old") is reviewed — decide if it should block deploy in some cases

## DEPLOYMENT
- [ ] Live reachability of both Cloudflare Pages deployments (`sih-hyperlocal-warning`, `csir-thunderstorm-bengaluru`) confirmed with an actual HTTP check (P3-1)
- [ ] Clarify whether both deployments are intentionally maintained or one is stale

## FRONTEND
- [ ] Map layer provenance badge added (ML vs heuristic) (P1-2)
- [ ] Responsive/mobile behavior audited across the stated target devices (P3-3)
- [ ] Full inventory of loading/empty/error states completed (P3-3)

## MAP
- [ ] Legend accurately reflects what's shown (risk scale, proxy labels)
- [ ] Tile/data fetch failure has a visible, honest fallback state (confirm, not yet independently verified)

## MOBILE
- [ ] Desktop/tablet/Android/iPhone/Safari/Chrome rendering confirmed (not done this audit pass — explicitly UNKNOWN)

## DOCUMENTATION
- [ ] All 14 items in `SIH_A_TO_Z_CLAIM_AUDIT.md` addressed (reworded or the underlying capability actually built)
- [ ] README reflects only verified capabilities (this phase's README update is the first pass — re-verify before every future claim change)
- [ ] Phase reports cross-reference each other where they could otherwise seem contradictory (e.g. "PRODUCTION DEPLOYED: NO" vs "Status: Live")

## PPT
- [ ] No PPT slide claims a metric without the dataset/split/N caveat that applies to it
- [ ] No PPT slide uses "pan-India AI model" language for the heuristic pan-India output
- [ ] No PPT slide claims IMDAA/INSAT/observed-FF/trained-MTL as currently operational

## DEMO
- [ ] Demo script explicitly states, out loud, which parts are VOBL-trained-ML vs pan-India-heuristic vs research-only (MTL)
- [ ] Demo does not rely on the SHAP fallback being mistaken for a live explanation

## REPRODUCIBILITY
- [ ] Environment/dependency versions pinned and documented (scikit-learn/xgboost already pinned in CI — confirm doc matches)
- [ ] Exact acquisition command, manifest path, and builder command documented and tested end-to-end by someone other than the original author
- [ ] Test suite command and expected pass count documented and kept current (this audit's verified count: 285/285 — re-verify before quoting elsewhere)
