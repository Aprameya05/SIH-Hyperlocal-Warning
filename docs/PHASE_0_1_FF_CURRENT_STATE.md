# Phase 0.1 — Flash-Flood (FF) Current-State Trace: Production vs Research

## 1. Production FF code path (end to end)

`backend/pipeline.py::hazard_probabilities()` (lines ~425-514) is the sole
function that computes the `ff_prob` value that ends up in
`canonical_forecast.json` / `forecast.json`. It is called once per grid cell
per pipeline run (pan-India path) and once for VOBL (station path, same
function).

```python
ff_score = 0.0
if pwat is not None:
    ff_score += min(1.0, max(0.0, (pwat - 35) / 30.0)) * 0.45
if cape is not None:
    ff_score += min(1.0, cape / 2000.0) * 0.22
if ctt_c is not None:
    ff_score += min(1.0, max(0.0, (-ctt_c - 5) / 45.0)) * 0.18
if qpe_mm is not None and qpe_mm > 0:
    ff_score += min(1.0, qpe_mm / 30.0) * 0.10
if convergence is not None and convergence > 0:
    ff_score += min(1.0, convergence / 2e-4) * 0.05
ff_prob = min(1.0, ff_score) * min(1.0, ts_prob + 0.1)
```

This is a **hand-weighted linear heuristic over live GFS fields**
(PWAT, CAPE, CTT proxy, QPE/APCP proxy, 850 hPa convergence), not a trained
classifier of any kind. None of the five weights (0.45/0.22/0.18/0.10/0.05)
were fit to any flood-outcome label; they are engineered thresholds. This
matches, and this pass independently re-confirms, Phase 5.7's own read-only
inspection of the same function (`docs/PHASE_5_7_FF_PU_MODEL_REPORT.md`
Section 0).

After `ff_prob` is computed, `terrain_lookup.py::apply_terrain_to_ff()` is
applied as an explicit **post-hoc multiplier** (not a model input):
`ff_prob_terrain = ff_prob * (0.70 + 0.60 * flood_susceptibility)`. This is
a documented, bounded rescaling of the heuristic's own output by a
terrain-derived factor — it is not feeding terrain into any trained model's
feature vector (the pan-India path has no trained model at all; the
station-level XGBoost `ff_slot_*` models are trained against the
rainfall-threshold proxy label, not INDOFLOODS events, per the existing RED
finding at `docs/MASTER_SIH_REQUIREMENT_MATRIX.md` row 5). This phase did
not change this logic.

**Is any observed-label model currently loaded in production? No.** Neither
the pan-India heuristic nor the station XGBoost models consume any
INDOFLOODS-derived label or feature. Confirmed by grep: no reference to
`indofloods`, `catchment_characteristics`, or `processed/ff_pu/` anywhere in
`backend/pipeline.py`, `forecast_action.py`, or `canonical_forecast_writer.py`.

## 2. Does the Phase 5.7 PU research model have a usable inference artifact?

Yes, technically: `processed/ff_pu/RESEARCH_ONLY_model_c_xgboost.json` and
`RESEARCH_ONLY_model_c_logistic.pkl` are real, loadable model files (XGBoost
native JSON and a pickled scikit-learn logistic model), with
`processed/ff_pu/model_metadata.json` and `feature_catalog.json` documenting
their exact input schema. In principle these could be loaded with
`xgboost.Booster().load_model(...)` today.

**But a loadable artifact is not the same as a deployable one** — see the
gaps below, all of which block promotion regardless of the artifact's
technical loadability.

## 3. Are Model C's predictors available in real time in production today?

**No.** Model C's features are:
- **Rainfall (7 features):** `rain_1d/3d/5d/10d`, `rain_max1d_5d`,
  `rain_recent_vs_antecedent_ratio`, `rain_accel_3d_minus_prior3d` — all
  computed from `imd_rain/rain/*.grd`, IMD's **historical, offline** 0.25°
  gridded daily rainfall archive. Grepping `backend/pipeline.py` and
  `forecast_action.py` for any reference to `imd_rain`, `.grd` files, or a
  live IMD rainfall feed returns **zero matches** — the production pipeline
  has no real-time access to IMD gridded rainfall at all. It uses GFS
  forecast fields (PWAT, APCP) instead, which are a different product
  (model output, not a gauge/satellite-merged rainfall analysis) on a
  different schedule.
- **Catchment (31 numeric + 4 categorical features):** from
  `data/catchment_characteristics_indofloods.csv`, static per-gauge
  morphometry/soil/climate data. This part *is* available (it's a static
  file, not a real-time feed), but static features alone showed
  near-chance performance in Phase 5.7 (Model A validation AUC 0.496).

So one of Model C's two feature groups (the one shown to carry almost all
the day-to-day predictive signal, Section 10 of the Phase 5.7 report) simply
does not exist as a production data source today.

## 4. Does the research pipeline's temporal resolution support the SIH's claimed 2-6h lead time?

**No, plainly.** The FF_PU rainfall features are **daily accumulations**
(`rain_1d` through `rain_10d`, one row per (cell, calendar date)), and the
label itself (`Start Date`) is event-day granularity, not event-hour. A
daily-resolution rainfall feature set cannot, by construction, support a
2-6 hour nowcast lead time — there is no sub-day timing information in
either the features or the label to localize *when within a flood day* a
flash flood begins. This is an inherent property of the INDOFLOODS +
IMD-gridded-daily data combination the research model was built from, not
an implementation gap that more engineering effort would close.

## 5. Is the research model's output a calibrated probability or a proxy/ranking score?

Per `docs/PHASE_5_7_FF_PU_MODEL_REPORT.md` Section 6-9 (re-confirmed by
reading `processed/ff_pu/validation_results.json` and
`sensitivity_results.csv` directly): it is a **PU-corrected ranking score**,
explicitly not validated as a calibrated probability. The report states the
reported enrichment/recall-at-k numbers are robust to the exact value of the
estimated class prior `c`, but any absolute-probability decision threshold
(e.g. "flag if P(y=1|x) > 0.5") would *not* be — i.e. the model has only
been validated for relative ranking, not as a calibrated probability
suitable for a fixed alert threshold the way `ff_slot_*`'s isotonic
calibration is.

## 6. Conclusion: can the Phase 5.7 PU pipeline be promoted toward production this phase?

**No — not yet, and not primarily for engineering-effort reasons.** Three
independent, structural gaps block it:

1. **No real-time predictor feed.** The feature group that actually carries
   the signal (antecedent rainfall) comes from an offline historical `.grd`
   archive with no production equivalent; there is no live IMD gridded
   rainfall ingestion in this repo today.
2. **Temporal resolution mismatch.** Daily rainfall accumulations cannot
   support a 2-6h nowcast lead time, independent of data availability.
3. **Uncalibrated/ranking-only output.** The model has only been validated
   as a PU ranking score (enrichment/recall-at-k), not as a calibrated
   probability — it is not a drop-in replacement for the existing
   `ff_slot_*` isotonic-calibrated probability output even where the other
   two gaps did not exist.

Any one of these would block promotion on its own; together they mean this
is a research finding (rainfall + static catchment features *can* rank
known historical flash-flood (cell, day) pairs well above chance, especially
on spatial generalization) rather than a deployable nowcasting component.
**No production code was changed as part of this determination** — this is
a read-only trace, as instructed.

## 7. What would be needed to promote it (for a future phase, not implemented here)

- A live, low-latency rainfall or precipitation-nowcast feed at sub-daily
  resolution (e.g. hourly/3-hourly) replacing the IMD daily `.grd` archive —
  IMDAA reanalysis or a radar/satellite QPE product, both currently blocked
  per `docs/MASTER_DATA_INVENTORY.md`.
- Recomputing the rainfall features at that finer temporal resolution and
  re-validating whether the signal (Model B's AUC 0.65-0.87) survives the
  resolution change.
- A calibration pass (e.g. isotonic, matching the existing `ff_slot_*`
  convention) before any probability from this model is shown as an
  operational threshold rather than a relative ranking.
- Addressing the SCAR/reporting-bias limitation already flagged in
  `docs/PHASE_5_7_FF_PU_MODEL_REPORT.md` Section 12, which is unresolved,
  not newly discovered here.
