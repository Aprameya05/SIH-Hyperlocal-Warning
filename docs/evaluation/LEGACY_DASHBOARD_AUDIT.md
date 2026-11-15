# Legacy Bengaluru dashboard (`frontend/index.html`) audit and dispositions

Scope: all 12,047 lines of the single-file legacy station dashboard (audited section by section; line numbers refer to commit `3140117`, before this change).
Rule applied: connect to a verified artifact and label scope, or remove from the live interface. A banner alone was not accepted. Locked in by `tests/test_frontend_claims.py`.

| # | Finding (original lines) | Disposition |
|---|---|---|
| 1 | Typed AUROC/POD/FAR/CSI/HSS/BIAS/Brier fallbacks (tile 6, summary row, 1335-1342, 5087-5090, 5755) | **Removed**; shown as unavailable when no verified live source |
| 2 | Per-slot model table with AUROC 0.8484/0.8317/0.8710, versions v3/v4/v5, feature counts, "Isotonic" (6151-6154, 6603-6606, 6706-6746, 7007, 7088-7090, 7186) | **Removed**; MODELS page now renders `data/hazard_evaluation.json` (VOBL walk-forward by year, with Brier skill including the negative years) |
| 3 | Hard-coded walk-forward table: AUROC and POD differed from the independent evaluation in every year (6784-6797, 6930, 6948-6977) | **Replaced by data**: `hazard_evaluation.json` folds and summary (mean AUROC 0.9002, POD 0.7574; the page had 0.8726 / 0.395) |
| 4 | Cloudburst/flash-flood per-slot AUROC 0.74-0.997 and thresholds (CB_METRICS/FF_METRICS, 6609-6618, 11088-11111) | **Removed**; CB/FF hazard buttons disabled ("n/a"), cards say UNAVAILABLE; MODELS page states there is no validated cloudburst or flash-flood model |
| 5 | Regime statistics: ts_rate and AUROC per regime (including 1.000), "model performs perfectly", October threshold-fix claims (1358-1399, 8058-8340, 7196-7222, 6746, 6977) | **Removed**; Regimes page shows only today's label from `forecast.json` (the producer's `synoptic_regime.auroc/ts_rate` fields have no provenance and are ignored) |
| 6 | Alert-log tiles (POD 83%, FAR 17%, CSI, mean lead 5.3 h, "Target > 75%", "Exceeds 2h minimum", "Doppler lightning networks") (2092-2113, 4566-4569) and per-event "+Nh lead" | **Removed**; only hit/false-alarm/miss counts of the hand-maintained log are shown, labelled unaudited; no lead time |
| 7 | Fake live feeds: Blitzortung lightning WebSocket, "IMD C-band DWR" radar PPI with fixed-offset storm cell, "S-BAND RADAR ACTIVE" literal, random lightning flashes (8570-9150, 5161, 1626-1661) | **Removed**; the page is now "Satellite" and shows only Himawari-9 band-13 values from `forecast.json` |
| 8 | Third-party REST API page and RAG "AI explanation engine" pointing at an unverified free-tier host (1396-1411, 7563-8056, 8369-8520) | **Removed** (not in repo, not deployed) |
| 9 | Synthetic map layers: Gaussian "risk" blob with hand-picked zone weights, animated cloud blobs as moisture field, drainage lines by latitude sort, pseudo-IWV from one BT value (2662-2704, 3308-3395, 2247-2420) | **Removed** |
| 10 | Invented defaults: DEFAULT_SLOTS probabilities 1.5-19.4%, `?? 0` probabilities, radar chart zeros, 'MINIMAL'/'PRE-CONVECTIVE'/'MONSOON BASELINE', sounding fallback with invented levels, SHAP fallback bars | **Removed**; null/"unavailable" instead |
| 11 | Climatology tables (days/month, diurnal, "35-year record" vs code comment 2015-2025, cloudburst/flash-flood days) | **Removed** (page replaced by an explanation); can return only if computed from the METAR record |
| 12 | Multi-day "probability" from typed weights, confidence bands SIGMA, cloudburst/flash-flood variants | **Removed** |
| 13 | Alerts, Contacts, What-if pages: subscription form, simulated "dispatched" send impersonating IMD, SIGMET/ATIS draft, "ALL CLEAR", toy cloudburst/flash-flood formulas, "operational forecasts use XGBoost v6 ... 47 features" | **Removed** |
| 14 | ATC fullscreen view: "operational action required", runway sector, radar badge | **Removed** |
| 15 | Browser-derived "operational lead" (valid minus generated time) and "inside/outside the 2-6 h target" text; NWP forecast hour labelled as operational lead | **Removed**; backend value only (null: DRIFT records no publication time); labels say NWP forecast hour |
| 16 | Wording: "Operational ATC-Grade" meta description, "DRIFT operational nowcast", "operational thresholds", "All systems operational", "LIVE" badges, "Doppler", "verified", "calibrated", "severe", "IMD nowcast", PDF footer "for official use by ATC", "HYPERLOCAL HAZARD BULLETIN" | **Relabelled** (experimental, station-only, "model score", "fresh", "fetched"); CAPE x K-index band "SEVERE" renamed |
| 17 | Typed source-status literals on the Unified page (`353_OF_992`, `READY`, `REAL_SAMPLE_VALIDATED`) | **Removed** |

## Still present (not data, not claims)
- Decorative particle background uses `Math.random` for positions only (no value is displayed from it).
- `forecast.json` is still written by the legacy producer with `synoptic_regime.auroc/ts_rate` and a hand-maintained `alert_log.json`; the frontend no longer displays those fields. Cleaning the producer is a follow-up.
- The legacy station model's slot scores (`forecast.json`) are shown as "model score". The independent evaluation shows negative Brier skill in 2 of 5 test years, so they are not presented as calibrated probabilities.
- Data & Graphs keeps reference lines (labelled "not a validated threshold") and a per-browser session history.
