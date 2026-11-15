# Phase 0.2 — External Data & Code Research — Final Report

Research-only, 2026-10-01. No code changes, no large downloads, no commits/deployment were made.
All claims below are sourced from live web search/fetch results this session (cited URLs in the
three detail docs); nothing was fabricated about dataset existence, license, or capability, and
every access-status claim is marked as "documented" vs. "actually tested from this sandbox" where
relevant (network egress in this sandbox did not allow authenticated downloads to be tested).

## Files created

- `/tmp/audit_repo/docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md`
- `/tmp/audit_repo/docs/PHASE_0_2_RECOMMENDATIONS.md`
- `/tmp/audit_repo/docs/PHASE_0_2_MODEL_OPTIONS.md`
- `/tmp/audit_repo/PHASE02_FINAL_REPORT.md` (this file)

## Candidate counts

- **Serious dataset candidates catalogued with real evidence: 9** — ERA5, ERA5-Land, GPM IMERG,
  IMD gridded rainfall (pre-existing), INSAT-3D/MOSDAC (confirmed still blocked), INDOFLOODS full
  corpus, Global Flood Database (MODIS/Cloud to Street), Dartmouth Flood Observatory, Bhuvan/NRSC
  CartoDEM (pan-India extension), HydroSHEDS/MERIT-Hydro. GSMaP and the Indian Lightning Location
  Network were searched but not fully catalogued — access/evidence was insufficient to classify
  them with confidence, so they are flagged as open leads, not counted as fully catalogued.
- **Serious code candidates catalogued: 5** — Earthformer, DGMR/`skillful_nowcasting`, the
  ConvLSTM-family (generic), a documented negative finding (no suitable external multimodal+MTL
  repo exists for this project's schema), and netcal for calibration.

## Top 3 USE NOW candidates

1. **ERA5 hourly reanalysis (Copernicus CDS)** — free, documented, working API access; gives every
   one of the 992 cells a real **hourly** (not daily) historical record of CAPE, shear-relevant
   wind fields, geopotential, and humidity, directly targeting the currently GRAY/null convergence
   field and the Bengaluru-only CIN coverage.
2. **Bhuvan/NRSC CartoDEM extended pan-India** — the exact same DEM family already validated and
   in production for Bengaluru; the gap is tile coverage, not a new access path, making this the
   lowest-risk item in the catalog.
3. **HydroSHEDS/MERIT-Hydro drainage and flow-accumulation layers** — free, no registration gate
   found, and genuinely new: the repo today has zero continuous pan-India drainage raster (only
   214 gauge-specific INDOFLOODS catchments), so this closes a real, previously-unaddressed gap.

## Top INVESTIGATE NEXT candidates

1. GPM IMERG Early Run promoted to live/near-real-time QPE (latency and orographic-accuracy
   validation needed before trusting it operationally).
2. ConvLSTM-family retraining directly on the 992-cell grid using the newly catalogued ERA5/
   CartoDEM/HydroSHEDS stack.
3. Reconciling INDOFLOODS' full published corpus (8,342 events, 214 stations, 1959–2020) against
   the repo's smaller filtered subset (4,548 events / 620 positives) — a data-engineering
   reconciliation task, not a new acquisition, but one that should happen before further FF label
   work.
4. Checking whether the CWC station discharge records underlying INDOFLOODS carry sub-daily
   timestamps (a real lead toward sub-day-resolution *observed* FF labels, not confirmed this
   phase).

## Key rejected candidates and why

- **MOSDAC/INSAT-3D/3DR**: still registration-gated; no new access path found or confirmed in this
  research pass. Status unchanged from the existing RED finding — this is a credentialing problem,
  not a research gap.
- **Indian Lightning Location Network bulk data**: described in academic papers but no public
  bulk-download/API access found — rejected on unconfirmed-access grounds.
- **DGMR (`skillful_nowcasting`)**: genuinely spatiotemporal, but assumes a dense pan-India radar
  composite input this project does not have and no open source for was found.
- **Earthformer as a drop-in**: built and validated for dense 384×384 radar-grid sequences, a
  fundamentally different spatial regime than this project's sparse 992-cell, 1.0° grid. Its
  ideas are a legitimate research inspiration; the checkpoint/repo itself is not adoptable as-is.
- **Dartmouth Flood Observatory** as a primary FF label source: its methodology blends news/
  government reports with satellite confirmation, which does not clear this project's own stated
  skepticism bar against inferring floods from social/report-based sources.
- **Any external multimodal+MTL repo as a wholesale import**: none found that targets this
  project's specific combination of coarse grid, heterogeneous modalities, PU-labeled FF, and three
  correlated hazard heads.

## Which current RED requirements could realistically move

- **#17 (drainage/catchment, pan-India half)**: could move from "zero pan-India drainage raster"
  toward YELLOW using HydroSHEDS/MERIT-Hydro — genuinely new capability, not previously available.
- **#9 (IWV/moisture)**: cannot move on IWV specifically (no satellite IWV substitute was found
  with confirmed open access; ERA5 and GFS are both model-derived PWAT-equivalents, not satellite
  observations) — this RED item is **not** closed by this research. Stated plainly: it remains
  blocked pending INSAT/Himawari-derived moisture retrieval, which this phase did not unblock.
- **#7/#8 (IMDAA, INSAT)**: **not moved**. Both remain genuinely credential-blocked; this research
  found no workaround, consistent with the existing honest labeling in the repo.
- **#5 (FF production, proxy label)**: **not moved to GREEN or even YELLOW** by this research
  alone. GPM IMERG and ERA5 address the *sub-daily predictor resolution* half of the Phase 5.7
  blocker, but the *real-time ingestion pipeline* and *calibration* halves still require actual
  engineering work this phase did not perform. The three structural gaps in
  `docs/PHASE_0_1_FF_CURRENT_STATE.md` are now more addressable in principle (IMERG solves the
  temporal-resolution gap conceptually) but none are closed in practice yet.

## Which current GRAY requirements could realistically move

- **#13 (convergence, null on disk)**: ERA5 hourly fields give a path to compute convergence
  reliably pan-India — GRAY could move toward YELLOW once the pipeline is actually wired to ERA5
  and the field is confirmed non-null on a live run (not verified this phase).
- **#16's full promotion / #18 (common spatiotemporal grid)**: CartoDEM pan-India + HydroSHEDS both
  give two of the "5 named sources" in `regrid.py` a path to real pan-India data, which is a
  concrete step toward #18, though #18 needs all five sources resolved, not just two.
- **#20/#21 (genuinely spatiotemporal / MTL)**: remain GRAY. This research found reference
  architectures (Earthformer, ConvLSTM-family) and new training data (ERA5 history), which makes
  *eventually* moving these out of GRAY more plausible, but no training was performed this phase —
  they are not closer to GREEN, only closer to "a documented path exists."

## What data would actually be required to train a genuine pan-India 2–6h model — honestly

A defensible pan-India 2–6h TS/CB/FF model needs, at minimum: (a) sub-daily (preferably hourly)
atmospheric predictors at every one of the 992 cells for a multi-year historical window — ERA5
closes this gap in principle, pending actual regridding/validation; (b) sub-daily, ideally sub-3h,
observed-or-near-observed precipitation/QPE at the same cells — GPM IMERG is the best candidate
found, with known orographic-accuracy caveats that must be validated against IMD gauges, not
assumed; (c) genuinely observed flood/thunderstorm/cloudburst event labels with sub-day timing,
pan-India, not just the 214 INDOFLOODS gauge catchments or the VOBL station — **this is the
biggest unresolved gap**: nothing found in this research provides pan-India, sub-daily, truly
observed TS/CB/FF event labels outside of the single VOBL station and the catchment-limited
INDOFLOODS/Global-Flood-Database sources; (d) a consistent pan-India drainage/terrain layer —
HydroSHEDS/CartoDEM close this; (e) a validated, leakage-audited train/test protocol with spatial
holdout, given the explicit lessons already documented from Phase 5.7's catchment-leakage findings.
**(c) remains the central, unclosed gap.** Everything else has at least a credible candidate path
now; genuinely observed, sub-daily, pan-India hazard labels do not.

## Is the current MTL architecture realistically trainable with available/public data?

**Conditional yes, with a major caveat.** The *predictor* side (atmospheric fields, terrain,
drainage) can now realistically be assembled pan-India using ERA5 + CartoDEM + HydroSHEDS, which
this research confirms are freely and practically accessible. But the *label* side cannot be
honestly assembled pan-India at sub-daily resolution with public data found in this research — at
best, the MTL model could be trained against (i) VOBL's real station-level TS/CB labels (not
pan-India), (ii) the existing rainfall-threshold proxy for FF (already flagged RED, and training
an MTL model against it would inherit the same proxy-label credibility risk, now across three
heads sharing a representation instead of one), or (iii) the INDOFLOODS/PU-ranking FF signal
(real but catchment-limited, daily-resolution, ranking-only). None of these support an honest claim
of "genuinely trained, pan-India, 2–6h, observed-label MTL model" today. The architecture could be
trained and would very likely produce *numbers*; whether those numbers mean anything beyond what
the existing XGBoost heuristics and PU model already show is unproven and should not be assumed
just because a bigger/fancier architecture produced them.

## Exact next recommended implementation phase

**Phase 0.3 should NOT be "build the MTL model."** It should be: (1) actually pull and regrid a
small ERA5 pilot (e.g., one year, 992 cells, core variables) and validate it against the existing
GFS-derived pan-India grid for consistency; (2) actually pull a CartoDEM pan-India tile set and
compute slope/aspect for all 992 cells, comparing against the Bengaluru-only baseline for sanity;
(3) actually pull HydroSHEDS flow-accumulation for India and validate catchment boundaries against
the 214 INDOFLOODS gauges as a cross-check; (4) reconcile the INDOFLOODS event-count discrepancy
(8,342 vs. 4,548) before any further FF labeling work; (5) only after (1)-(4) produce a real,
validated pan-India feature table, run a small comparative training experiment between the existing
MTL backbone and a ConvLSTM-family baseline on that table, with a proper spatial holdout, before
making any claim about which architecture is "better" or "genuinely spatiotemporal and working."
This keeps Phase 0.3 as data-engineering-and-validation, matching this project's own stated
priority of scientific defensibility over a greener-looking checklist.

## Honest verdict: can external data meaningfully close the 2–6h pan-India FF/TS/CB gap?

**Partially, and only for the predictor side.** This research found real, freely accessible,
working-per-documentation sources (ERA5, GPM IMERG, CartoDEM, HydroSHEDS) that can plausibly give
the project pan-India, sub-daily atmospheric and terrain/drainage predictors for the first time —
a genuine and previously-missing capability. But the central blocker for an honest 2–6h pan-India
hazard model is not predictor availability, it is **the absence of genuinely observed, sub-daily,
pan-India ground-truth labels** for thunderstorm, cloudburst, and flash-flood events outside of the
single VOBL station and the catchment-limited INDOFLOODS corpus. No dataset found in this research
— including the strongest label candidates, INDOFLOODS and the Global Flood Database — solves that
at the resolution and pan-India coverage this project's 2–6h nowcasting claim would require. IMDAA
and INSAT-3D/3DR remain genuinely blocked by credentials this research did not find a way around.
So the honest answer is: **external public data can upgrade the predictor/feature side of this
project substantially, but the label-side gap that makes a genuine pan-India 2–6h FF/TS/CB claim
defensible remains fundamentally blocked pending either official IMDAA/INSAT access or a
genuinely new pan-India observed-event label source this research did not find.** Anyone reading
this should not conclude that pulling in ERA5/IMERG/CartoDEM/HydroSHEDS, by itself, gets this
project to a trustworthy pan-India 2–6h hazard model — it gets the project meaningfully better
inputs for a model whose ground truth is still the unresolved problem.
