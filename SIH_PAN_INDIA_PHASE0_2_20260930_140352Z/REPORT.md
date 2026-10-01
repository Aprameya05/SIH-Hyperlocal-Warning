# DRIFT / SIH — Pan-India Completion Master Pass — Phases 0-2 — 2026-09-30

Per your explicit execution rule ("do this in phases... do not implement everything blindly in one pass"), this delivery covers **Phase 0, Phase 1, Phase 2 only**. MTL training was not started. Nothing in production was touched.

## Phase 0 — Production Baseline Audit (read-only)

`docs/PAN_INDIA_BASELINE.md` covers all 14 requested areas (A-N). Headline finding, stated plainly because it shapes everything downstream: **"pan-India" today genuinely means only three things** — the 992-cell grid definition, GFS ingestion, and physics-proxy hazard scoring on that grid. Every trained, validated ML component that actually exists (CB/FF/TS XGBoost classifiers, SHAP, terrain, Himawari) is VOBL/Bengaluru-only. This isn't new information exactly, but Phase 0 makes it an explicit, itemized baseline rather than something scattered across five prior reports.

One new, concrete finding from actually reading `backend/pipeline.py`'s grid schema this time: `convergence_s` and `ctt_drop_rate_c_hr` fields are **defined in the schema but null on every cell** on the current on-disk file — they were added to the schema in an earlier pass but no pipeline run has populated them since. That's a one-line fix (re-run the pipeline) rather than a real gap, and is called out as such.

## Phase 1 — SIH Requirement Matrix

`docs/SIH_REQUIREMENT_MATRIX.md` — all 35 requested items, each scored honestly against the strict rule "code merely existing is not enough for IMPLEMENTED." Rollup:

- **IMPLEMENTED**: 4/35 (CAPE, wind shear, real-time refresh, deployment)
- **PARTIALLY IMPLEMENTED**: 19/35 — the largest bucket; almost everything that's real works at VOBL only
- **ARCHITECTURAL ONLY**: 6/35 — including a correction worth flagging directly: **the MTL backbone is not genuinely spatiotemporal today** (single-timestep input, no sequence, only a 4-dim lat/lon positional encoding) despite being described that way in older repo comments. This matrix states that plainly rather than repeating the old framing.
- **DATA BLOCKED**: 3 directly (IMDAA, INSAT, satellite IWV), several more indirectly through them (pan-India CTT, QPE, satellite fusion)
- **NOT IMPLEMENTED**: 2/35 — drainage/catchment data (the file `dev/Fetch cb ff labels.py` expects, `data/catchment_characteristics_indofloods.csv`, is genuinely missing from the repo — confirmed again this pass) and the alert API (doesn't exist yet).

## Phase 2 — Pan-India Data Acquisition

**Existing verified sources** (GFS, ERA5, IMD gridded rainfall, INDOFLOODS, Himawari, SRTM/terrain, VOBL observations) were re-confirmed present and real — no new inspection needed beyond what Phase 0 already covered.

**IMDAA**: still genuinely blocked. Two independent, freshly-verified reasons: this sandbox's network proxy returns `403` on any IMD/NCMRWF-adjacent host (confirmed again this pass), and no credentials exist. Built real scaffolding — `scripts/acquire_imdaa.py` (refuses to run without real credentials and a real, non-guessed endpoint) and `scripts/parse_imdaa.py` (opens a real file if you supply one, prints its actual variable names so the mapping is built from ground truth, refuses to run against a missing/fake file) — plus `docs/IMDAA_DATA_SPEC.md` documenting exactly what's still unverified and why I didn't fill in a guessed NCMRWF endpoint. Both scripts were actually run this pass and confirmed to fail safely and specifically (not silently) — see `tests/verify_phase2_scripts.py`.

**INSAT**: also still blocked, but this pass adds a genuine new finding from actually re-reading `backend/fetch_insat3d.py` line-by-line rather than just checking its docstring — the script's own comments admit its MOSDAC filename pattern and HDF5 dataset key are "may vary by product version" guesses, never confirmed against a real downloaded file. `docs/INSAT_DATA_SPEC.md` records this honestly: the existing integration code should be described as "scaffolded, unverified," not "ready." Its IWV-proxy labeling (`"note": "IWV proxy derived from WV brightness temperature. Not calibrated PWAT."`) was already honest and wasn't touched.

**What I explicitly did not do**: guess NCMRWF's or MOSDAC's actual current API shape from training-era general knowledge and present it as verified. Both spec docs say so directly.

## What's Needed From You Before Phase 2 Can Close

1. NCMRWF registration + the actual current product spec (endpoint, format, variable names, resolution) — from a machine with real internet, since this sandbox can't reach it.
2. MOSDAC credentials + confirmation that `backend/fetch_insat3d.py`'s endpoint assumption is still current, ideally with one real sample file to test the parser against.
3. Either `data/catchment_characteristics_indofloods.csv` (if you have it) or a decision to source a pan-India catchment dataset some other way, to unblock requirement #20.

## Tests Performed This Pass

- `scripts/acquire_imdaa.py` and `scripts/parse_imdaa.py` — both actually executed against realistic-but-missing inputs, confirmed to fail with clear, specific error messages and correct exit codes (1), not silent success or fabricated output. Re-runnable check in `tests/verify_phase2_scripts.py`, re-run just before packaging — passes.
- All markdown docs cross-checked against actual repository state (file existence, code content) rather than written from memory of prior passes.

## Files Created This Phase

`docs/PAN_INDIA_BASELINE.md`, `docs/SIH_REQUIREMENT_MATRIX.md`, `docs/IMDAA_DATA_SPEC.md`, `docs/INSAT_DATA_SPEC.md`, `scripts/acquire_imdaa.py`, `scripts/parse_imdaa.py`, `raw/imdaa/README.md`, `processed/imdaa/README.md`, `tests/verify_phase2_scripts.py`.

**Files modified: none.** Nothing in production was touched, as instructed.

## What's Blocked

IMDAA and INSAT integration (credentials), pan-India CTT/QPE/satellite-IWV (downstream of INSAT), drainage/catchment data (missing file), and — not yet started, correctly — Phases 3 onward (canonical grid schema extension, label engine, dataset construction, temporal alignment, MTL training). Per your instruction, I stopped here rather than continuing into Phase 3+ blind.

## Next Step

Waiting on your go-ahead to start Phase 3 (canonical grid schema — this doesn't need external credentials, just extending `regrid.py`'s provenance fields and formalizing the existing 992-cell definition) and Phase 4 (the label engine — also doesn't need IMDAA/INSAT, since it's built from IMD rainfall + INDOFLOODS + TS station observations, the same independent sources already validated in the VOBL label audit). Both of those can proceed without the blocked credentials; Phases that genuinely need IMDAA/INSAT data will stay marked BLOCKED until you provide what's listed above.
