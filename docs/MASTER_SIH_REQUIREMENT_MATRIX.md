# MASTER SIH Requirement Matrix — 2026-10-01

This document supersedes nothing — it builds on and re-verifies `docs/SIH_REQUIREMENT_MATRIX.md`
(the prior-phase matrix, 34 rows, dated 2026-09-30). I re-ran or re-grepped the evidence behind
every claim below rather than copying it blind; where I could not re-verify a number I say so.

## Status definitions (strict — used uniformly across all four MASTER docs)

- **GREEN** — implementation + real data + actually executes in production + is consumed by the
  frontend/alerts + has a passing test + has validation evidence, ALL true at once.
- **YELLOW** — genuinely real and working for a real subset (e.g. VOBL station only, not pan-India;
  or a correct proxy honestly labeled as a proxy), but not the full SIH-stated scope.
- **RED** — claimed or implied to work, but on inspection is a heuristic/proxy standing in for the
  real requirement, is architecture with no trained/validated model behind it and is being presented
  as if it were, or is blocked on data/credentials the team does not have. This is the "overstatement
  risk" bucket.
- **GRAY** — scaffolded/designed, not wired to real data at all, not yet claimed as done anywhere
  user-facing — a legitimate "not started" rather than a misrepresentation.

Re-verified source facts used throughout:
- `docs/PAN_INDIA_HAZARD_COEFFICIENTS.md` + `grep` of `backend/pipeline.py::hazard_probabilities()`:
  pan-India TS/CB/FF are a **hand-weighted linear formula** (weights sum to 1.00, documented per
  hazard), not a trained model. Confirmed no `.fit(`/training/calibration code exists for this path.
- `forecast_action.py:725` comment, verbatim: `# IWV from GFS PRECIP_WATER (same variable used by
  the model)` — IWV is GFS PWAT, not a satellite-observed quantity. No INSAT/Himawari-derived IWV
  exists anywhere in the codebase.
- `raw/imdaa/README.md` and `processed/imdaa/README.md`: both directories are genuinely empty;
  IMDAA is **not integrated**, confirmed by directory listing, not just doc claims.
- `processed/ff_pu/ff_pu_training_table.csv`: `wc -l` = 144,487 lines (144,486 data rows). Verified
  by pandas: `label_status` = 620 POSITIVE / 143,866 UNLABELED — matches the Phase 5.6/5.7 reports
  exactly. This is a PU (positive-unlabeled) research table, not a labeled supervised dataset — there
  are no confirmed negatives.
- `processed/ff_pu/validation_results.json`: Model A (catchment-only) spatial-holdout
  `pu_ranking_auc_caveated = 0.500` (no signal). These are PU-ranking AUCs against an unlabeled
  population, explicitly caveated in the field name itself — not standard supervised AUROC, and the
  report is honest about that distinction.
- `pytest` run (excluding 4 pre-existing environment blockers): **150 passed, 1 failed** out of the
  full non-blocked suite (`test_gfs_row_select.py::test_out_of_order_rows_pick_newest` fails because
  it hardcodes a `fetched_at_utc` that is now stale relative to today's date, 2026-10-01 vs. the
  2026-09-30 commit — a time-bound test artifact, not a code regression). 4 modules fail to collect:
  `test_himawari.py`, `test_segments.py`, `test_segments_v2.py` (all `ModuleNotFoundError: donfig`,
  a `satpy` sub-dependency not installed in this sandbox) and `test_nomads.py` (live NOMADS fetch,
  `403 Forbidden` through the sandbox's egress proxy). These match the known environment failures
  named in the task brief and are environment issues, not evidence the underlying code is broken.
- `.github/workflows/*.yml`: `update_grid.yml` and `forecast_update.yml` each declare a `concurrency:`
  group (grep-confirmed), and both reference `concurrency` text explaining the two workflows
  deliberately do NOT share a group with each other but each is internally serialized — re-verified
  present in the current files, not just claimed in an old phase report.
- `alert_delivery.py`: `VALID_STATUSES = ("SUCCESS", "FAILED", "NOT_CONFIGURED", "SKIPPED_NO_ALERT")`
  is a real, enforced tuple; every return path in the file returns one of these four and
  `send_alerts.py` tallies and prints all four counts per run — failures cannot silently vanish into
  a generic "sent" bucket. This re-confirms `docs/ALERT_SYSTEM_AUDIT.md`'s claim by direct code read.

## Core requirement table (carried forward from the prior matrix, re-coded to GREEN/YELLOW/RED/GRAY)

| # | Requirement | Prior status | Re-verified? | MASTER status | Why |
|---|---|---|---|---|---|
| 1 | Pan-India 992-cell grid | PARTIALLY IMPLEMENTED | Yes — grid file present, cell-count guardrail exists | YELLOW | Grid mechanics real; hazard values on it are heuristic, not ML |
| 2 | 2–6h lead time | PARTIALLY IMPLEMENTED | Yes — `lead_time.py` wired into `forecast_action.py`, station only | YELLOW | Real at VOBL only; no per-cell lead time on the pan-India grid |
| 3 | Thunderstorm prediction | PARTIALLY IMPLEMENTED | Yes | YELLOW | Real trained XGBoost + IMD labels at VOBL; pan-India is the heuristic formula |
| 4 | Cloudburst prediction | PARTIALLY IMPLEMENTED | Not re-run (would require loading pickles and historical data) | YELLOW | Station model claim (AUROC 0.924) not independently re-run this pass — treat as unverified-but-plausible, not re-confirmed |
| 5 | Flash-flood prediction (production) | PARTIALLY IMPLEMENTED | Yes — confirmed the production FF XGBoost trains against a **rainfall-threshold proxy label**, not an observed flood event | **RED** | The production system's FF label is `PROXY_NOT_OBSERVED`; an AUROC quoted against a proxy label must not be read as "flash flood prediction works" — this is the single largest credibility risk in the repo |
| 6 | Simultaneous multi-hazard shared representation (MTL) | ARCHITECTURAL ONLY / PARTIALLY IMPLEMENTED (XGBoost) | Partially — file exists, confirmed untrained | GRAY (MTL) / YELLOW (independent XGBoost heads) | No trained weights behind the MTL backbone; independent per-hazard XGBoost at VOBL is real |
| 7 | IMDAA reanalysis | DATA BLOCKED | Yes — both `raw/imdaa/` and `processed/imdaa/` confirmed empty | RED | Genuinely blocked (needs NCMRWF registration), correctly labeled in-repo as blocked, but still a real gap against the SIH requirement |
| 8 | INSAT-3D/3DR | DATA BLOCKED | Not re-run live (would need live MOSDAC network access, unavailable here) | RED | Credentials unset, code is a stub — cannot be claimed as live. Phase 0.1: fixed one `index.html` "DATA FUSION INPUTS" row that paired the real live source (Himawari-9) with `spec: 'INSAT-3D/3DR (MOSDAC)'` in a way that could read as INSAT being the live source; now explicitly labeled "INSAT-3D/3DR not live — scaffolded only" |
| 9 | IWV / moisture | DATA BLOCKED (proxy in place) | Yes — `forecast_action.py:725` comment confirms PWAT-as-IWV | RED | Must never be called "satellite IWV" in any SIH-facing material; it is GFS PWAT (PROXY). Phase 0.1: corrected the two user-facing `index.html` strings that implied a Himawari/satellite IWV source ("IWV Satellite Moisture Tracker", "PRECIP WATER · HIMAWARI-9 ANCHOR") and added a `met_parameters.iwv_source` provenance string in `forecast_action.py` stating it is GFS-derived, not satellite-observed — no functional/schema-breaking change |
| 10 | CAPE | IMPLEMENTED | Yes — real GFS/ERA5 variable, used in production formula and training data | GREEN | Fully real end to end |
| 11 | CIN | PARTIALLY IMPLEMENTED | Yes — present in pan-India grid schema, absent from one BLR training CSV | YELLOW | |
| 12 | Wind shear | IMPLEMENTED | Yes — multiple layers computed, used in production and training | GREEN | Fully real end to end |
| 13 | Convergence | ARCHITECTURAL ONLY | Not re-run against a live pipeline execution (no network in this sandbox) | GRAY | Field defined in schema but reported `null` on the on-disk file as of prior pass; could not re-run the pipeline here to check if still null |
| 14 | CTT / CTT drop rate | PARTIALLY IMPLEMENTED / ARCHITECTURAL ONLY | Not re-run | YELLOW / GRAY | Real for Himawari-covered Bengaluru only; pan-India needs INSAT (blocked) |
| 15 | QPE | PARTIALLY IMPLEMENTED (proxy) | Yes — GFS APCP used, correctly labeled a proxy in schema | YELLOW | Proxy, honestly labeled |
| 16 | DEM / elevation / slope | PARTIALLY IMPLEMENTED | Yes — `data/blr_terrain.json` exists, Bengaluru-only | YELLOW | Real SRTM data, not pan-India |
| 17 | Drainage/catchment (FF) | NOT IMPLEMENTED (in production) | Corrected Phase 0.1: `data/catchment_characteristics_indofloods.csv` **does exist** (155 gauge rows, verified via `wc -l`/`head`) at the exact path `dev/Fetch cb ff labels.py` references — the earlier "missing input file" claim in this row (and in `docs/SIH_REQUIREMENT_MATRIX.md`) was factually wrong; see `docs/PHASE_0_1_AUDIT_DISCREPANCY_RESOLUTION.md`. The INDOFLOODS derivatives built from it (`processed/indofloods/*.csv`, `processed/ff_pu/*`) are also real and match their documented row/positive/cell/gauge counts exactly | RED | Status unchanged — the real gap is not a missing file, it is that `backend/pipeline.py`/`forecast_action.py` never consume any INDOFLOODS-derived catchment or event data (grep-confirmed zero references); production FF still falls back to the rainfall-threshold proxy. See `docs/PHASE_0_1_FF_CURRENT_STATE.md` for why the Phase 5.7 PU research model built from this data is not yet promotable (no real-time rainfall feed, daily resolution incompatible with the 2-6h lead time, uncalibrated ranking-only output) |
| 18 | Common spatiotemporal grid across sources | ARCHITECTURAL ONLY | Yes — `regrid.py` only has real data for 3 of 5 named sources | GRAY | |
| 19 | Multimodal fusion | PARTIALLY IMPLEMENTED | Yes | YELLOW | GFS + Bengaluru-only terrain; no pan-India satellite fusion |
| 20 | Genuinely spatiotemporal model | ARCHITECTURAL ONLY | Yes — `backend/mtl_backbone.py` is single-timestep, sin/cos lat/lon only, no sequence dimension | GRAY | Correctly self-corrected in the prior matrix; re-confirmed, not actually spatiotemporal |
| 21 | Multi-task learning | ARCHITECTURAL ONLY | Yes — no training run exists for this architecture | GRAY | |
| 22 | Separate TS/CB/FF heads | IMPLEMENTED (architecturally) | Yes, for XGBoost; MTL heads untrained | YELLOW | |
| 23 | Unified risk maps (UI) | PARTIALLY IMPLEMENTED | Yes — `index.html` renders MapLibre layers for all three hazards from the physics-proxy grid | YELLOW | Map is real; what it shows pan-India is the heuristic score, not validated ML |
| 24 | Explainability (SHAP) | PARTIALLY IMPLEMENTED | Yes — `compute_realtime_shap.py` + `index.html` SHAPWaterfallCard wired to `realtime_shap`/`cb_realtime_shap`/`ff_realtime_shap`, with a hardcoded `SHAP_DATA_FALLBACK` used when live data is absent | YELLOW | Real SHAP for VOBL XGBoost only; the UI silently falls back to canned example SHAP values when live data is missing — a misleading-UI risk, see UI audit below |
| 25 | Categorized/actionable alerts | PARTIALLY IMPLEMENTED | Yes — `alert_delivery.py` four-state model confirmed in code | YELLOW | Alert dispatch logic is real and distinguishable; delivery is WhatsApp/CallMeBot only (not SMS as README architecture diagram implies) |
| 26 | Lightweight/real-time API | IMPLEMENTED (core PWA) | Not re-run live (external Render host unreachable from this sandbox) | YELLOW | Static-JSON design is real and genuinely lightweight; the separate RAG/WebSocket backend's current uptime could not be verified live here |
| 27 | Real-time operation / deployment | PARTIALLY IMPLEMENTED | Yes — 5 scheduled workflows confirmed, concurrency groups present | YELLOW | Cron-driven, not continuously running; acceptable for this architecture but not literally "real-time" |

## Net tally for this table (34 lines collapse to 27 numbered rows above; split sub-rows counted separately in the final report)

See `/tmp/audit_repo/MASTER_AUDIT_FINAL_REPORT.md` for the authoritative GREEN/YELLOW/RED/GRAY counts,
which also fold in the UI/map/alert items enumerated in `docs/ALERT_SYSTEM_AUDIT.md` and the UI
section below.

## UI/UX and map audit (read from `index.html`, 597KB single-file React app, static code reading only)

Verified by `grep`/read, not a live browser session (none available in this sandbox):

- **Map**: MapLibre GL JS v4 from CDN (`maplibregl.Map`), NavigationControl, click popups for cell
  detail, VOBL-specific popup — **implemented**.
- **SHAP waterfall**: real component (`SHAPWaterfallCard`) that reads live `realtime_shap` data when
  present — **implemented**, but ships a hardcoded `SHAP_DATA_FALLBACK` array that renders identical
  "example" bars when the live field is missing, with no visible "this is example data" label found
  in the surrounding markup from this grep pass — **potentially misleading-UI**, flagged as a finding.
- **RAG/explainability chat**: calls `https://csir-thunderstorm-api.onrender.com/rag/explain` — real
  wiring, but the endpoint is a free-tier Render service the README itself says spins down after 15
  minutes idle; a cold call will look broken for 30–60s with no obvious loading state confirmed from
  this grep (would need live browser testing to confirm the loading UI, which this sandbox cannot do).
- **PWA/offline**: `sw.js` service worker registered and unregister-cleanup logic present at the top
  of `index.html` — **implemented** per prior matrix, re-confirmed by grep (`serviceWorker` present).
- **Dark/light mode, error/stale-data states**: not independently re-verified this pass beyond what
  the prior matrix states; would require a live render to assess visual consistency — **not
  re-verified, treat prior matrix's claims here as unconfirmed**.

## Alert/API audit (from direct code read)

`alert_delivery.py` + `send_alerts.py`: subscription list → per-subscriber threshold check →
`DeliveryResult` with one of 4 statuses → printed per-run tally (`SUCCESS=`, `FAILED=`,
`NOT_CONFIGURED=`, `SKIPPED_NO_ALERT=`). This is a real, auditable state machine — **YELLOW**
overall only because delivery channel is WhatsApp-via-CallMeBot (a third-party free API), not the
SMS/Web-Push the architecture diagram in the README implies, and live delivery could not be tested
from this sandbox (no network to CallMeBot).
