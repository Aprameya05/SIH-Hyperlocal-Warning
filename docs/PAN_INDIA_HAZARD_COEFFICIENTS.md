# Pan-India Hazard Engine Coefficients — documentation only — 2026-09-30

Per Part 8 of this phase's instructions: this document records the current coefficients exactly as they exist in `backend/pipeline.py::hazard_probabilities()`. **No numerical value was changed in this phase.** This is documentation, not calibration.

## What this function is, plainly

A hand-weighted linear scoring formula. Each atmospheric variable contributes an additive term (a normalized 0–1 sub-score times a fixed weight) to a running score, which is then clipped to `[0, 1]`. **These are heuristic, physics-motivated coefficients chosen by a person, not weights learned from labeled data by any training process.** No calibration run, no regression, no cross-validation, no loss function, and no dataset-fitting step exists anywhere in this repository that produced these numbers. They must never be described as "model weights," "learned parameters," or "ML." The one place in this repo where real learned weights exist is the VOBL XGBoost models (`models/*.pkl`, `models/*.json`) — a completely separate code path, applied only to the VOBL cell.

## Thunderstorm score — coefficients and the variable each operates on

| Weight | Variable | Normalization applied before the weight |
|---|---|---|
| 0.30 | CAPE (J/kg) | `min(1, CAPE / 3000)` |
| 0.22 | K-index | `min(1, max(0, (K_index_C - 20) / 20))` |
| 0.18 | Totals-totals | `min(1, max(0, (TT_C - 44) / 12))` |
| 0.15 | Wind shear (m/s) | `min(1, shear / 30)` |
| 0.10 | 850hPa convergence (s⁻¹, inflow only) | `min(1, convergence / 2e-4)` |
| 0.05 | CTT drop rate (°C/hr, cooling only) | `min(1, drop_rate / 10)` |
| penalty, up to −0.15 | CIN (J/kg) | `min(0.15, |CIN| / 1000 * 0.15)`, subtracted |

Weights sum to 1.00 (0.30+0.22+0.18+0.15+0.10+0.05), before the CIN penalty. Final score clipped to `[0,1]`.

## Cloudburst score — coefficients and the variable each operates on

| Weight | Variable | Normalization applied before the weight |
|---|---|---|
| 0.40 | PWAT (mm) | `min(1, max(0, (PWAT - 30) / 35))` |
| 0.25 | CAPE (J/kg) | `min(1, CAPE / 2500)` |
| 0.20 | CTT (°C, cold only) | `min(1, max(0, (-CTT - 10) / 30))` |
| 0.08 | CTT drop rate (°C/hr) | `min(1, drop_rate / 10)` |
| 0.07 | QPE / 6h APCP (mm) | `min(1, QPE / 50)` |

Weights sum to 1.00. The clipped score is then **multiplied by `ts_prob`** (the thunderstorm score computed above) — cloudburst probability is gated on some baseline thunderstorm likelihood, not scored independently.

## Flash flood score — coefficients and the variable each operates on

| Weight | Variable | Normalization applied before the weight |
|---|---|---|
| 0.45 | PWAT (mm) | `min(1, max(0, (PWAT - 35) / 30))` |
| 0.22 | CAPE (J/kg) | `min(1, CAPE / 2000)` |
| 0.18 | CTT (°C, cold only) | `min(1, max(0, (-CTT - 5) / 45))` |
| 0.10 | QPE / 6h APCP (mm) | `min(1, QPE / 30)` |
| 0.05 | 850hPa convergence (s⁻¹) | `min(1, convergence / 2e-4)` |

Weights sum to 1.00. The clipped score is then **multiplied by `min(1, ts_prob + 0.1)`** — flash flood probability, like cloudburst, is gated on thunderstorm likelihood (with a small +0.1 floor so FF isn't zeroed out purely by a low TS score).

## Where this is applied

`backend/pipeline.py::hazard_probabilities()` is called once per canonical cell, per pipeline run (`update_grid.yml`, 4x/day), for all 992 cells — this is the pan-India path. It is never applied to the VOBL cell's headline forecast (that comes from the separate, real, trained XGBoost models in `forecast_action.py`); the VOBL cell in `data/pan_india_grid.json` does still get scored by this same function alongside every other cell (since `backend/pipeline.py` doesn't special-case VOBL), so two different numbers exist for VOBL's TS probability in this repo today — the real XGBoost one (in `forecast.json`) and this heuristic one (in `pan_india_grid.json`, same formula as every other cell). `canonical_forecast_writer.py` (introduced this phase) resolves this by always preferring the real XGBoost value for the VOBL cell in the canonical artifact and explicitly not blending it with this heuristic score.

## What statistical calibration would mean, and why it's a separate phase

Per this phase's explicit instruction, no calibration was attempted. A real calibration would mean: assembling a labeled dataset (e.g. the Phase 4/4.5 pan-India CB labels, which are genuinely observed), fitting a proper model (logistic regression at minimum, ideally the already-existing but untrained MTL architecture) against those labels, and validating it on a held-out set — turning these hand-picked weights into either (a) documented, still-heuristic constants with a stated rationale for each number (not present today — no comment in the code explains why CAPE gets 0.30 specifically, rather than 0.25 or 0.35), or (b) replacing them outright with learned weights and calling the result what it is: a trained model, not a physics proxy. Both are legitimate future directions; this phase does neither, and this document's only job is to make sure nobody downstream mistakes the current numbers for either one.
