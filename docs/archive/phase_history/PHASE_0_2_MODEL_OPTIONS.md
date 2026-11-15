# Phase 0.2 — Model Options Comparison

Comparing five candidates on the required axes. No winner is picked unless evidence makes it
obvious — per the axes below, it is not obvious, and that is stated plainly rather than forced.

## (1) Current production XGBoost (VOBL `ff_slot_*`/TS/CB heads + pan-India heuristic)

- **Data requirements**: station-level GFS/ERA5/Himawari features (real, working) for VOBL;
  hand-weighted heuristic formula (no training data needed) for pan-India.
- **Training requirements**: already trained, isotonic-calibrated, for VOBL; pan-India has no
  training step at all (it is a formula, not a model).
- **Inference cost**: very low — tree ensembles are cheap, already running in production cron jobs.
- **Integration effort**: zero — already integrated.
- **Scientific suitability**: real and calibrated at VOBL only; pan-India is explicitly not ML
  (RED per MASTER_SIH_REQUIREMENT_MATRIX #1/#5).
- **992-cell compatibility**: NO as a trained model — only the heuristic runs pan-India; the
  trained XGBoost does not generalize beyond VOBL without pan-India training data, which this
  phase's catalog (ERA5, CartoDEM, HydroSHEDS) could now supply but has not yet been used to retrain
  anything.
- **2-6h horizon**: YES at VOBL (real `lead_time.py` wiring); NO pan-India.
- **TS/CB/FF support**: TS/CB real at VOBL; FF at VOBL trained against a rainfall-threshold PROXY
  label (RED), not an observed flood event.
- **Validation requirements already met**: isotonic calibration + SHAP exist for VOBL heads;
  pan-India heuristic has no validation because it is not a fitted model.

## (2) Existing untrained MTL backbone in repo (`backend/mtl_backbone.py`)

- **Data requirements**: currently only sin/cos lat/lon + a single timestep of features — would
  need the newly catalogued ERA5 hourly stack, CartoDEM pan-India, and HydroSHEDS drainage layers
  fed in as a real multi-timestep, multi-modal input to become meaningfully spatiotemporal.
- **Training requirements**: none have been run (GRAY — confirmed untrained, no `.fit(` for this
  path).
- **Inference cost**: unknown until trained; architecture choice (likely small transformer/MLP per
  the repo's description) suggests moderate GPU cost for training, low for inference if kept small.
- **Integration effort**: MEDIUM-HIGH — the architecture exists, but turning it from single-timestep
  to genuinely sequence-aware requires real engineering, not just feeding in more data (per
  MASTER_SIH_REQUIREMENT_MATRIX #20, it structurally lacks a sequence dimension today).
- **Scientific suitability**: currently none — it is architecture only, not a working model.
- **992-cell compatibility**: designed for it (unlike the radar-scale external architectures below).
- **2-6h horizon**: unproven — would need real training + validation against a held-out horizon.
- **TS/CB/FF support**: designed for all three as a shared representation (the MTL point), but
  nothing has been trained, so this is a design intent, not a demonstrated capability.
- **Validation requirements**: everything — train/val split, leakage audit, calibration, SHAP for
  the pan-India path, spatial holdout given the lessons from Phase 5.7's catchment-leakage findings.

## (3) Best external spatiotemporal architecture found — Earthformer (space-time transformer)

- **Data requirements**: built and validated against dense radar-style imagery (SEVIR: 384×384
  grids, 5-min cadence) — a fundamentally different data regime from this project's 992-cell,
  1.0°, hourly-at-best grid.
- **Training requirements**: would require training essentially from scratch on this project's own
  data; no pretrained checkpoint compatible with a 992-cell irregular/coarse grid exists.
- **Inference cost**: transformer-based, higher than XGBoost or ConvLSTM at comparable scale;
  exact cost depends on how much the architecture is shrunk for the coarser grid.
- **Integration effort**: HIGH — grid-shape mismatch, no existing adapter from this project's
  feature schema to Earthformer's expected cuboid-attention input tensor shape.
- **Scientific suitability**: genuinely spatiotemporal (real strength over the current MTL
  backbone), but its inductive biases are tuned for dense imagery, not a sparse national grid;
  unproven whether that architecture choice is even the right one at 992-cell resolution.
- **992-cell compatibility**: NOT out of the box — would need a custom rework, which is really "a
  new architecture inspired by Earthformer," not integrating Earthformer itself.
- **2-6h horizon**: its SEVIR benchmark targets a comparable few-hour lead time conceptually, but
  that transfers in horizon-length spirit only, not in grid/resolution specifics.
- **TS/CB/FF support**: single-task reference implementation (precipitation nowcasting); would need
  multi-head modification for TS/CB/FF.
- **Validation requirements**: full retraining, full validation — effectively a research project,
  not an integration task.

## (4) Best external multimodal architecture found

- **Finding**: no existing open-source repo was found this pass that performs multimodal fusion
  (atmospheric + terrain + satellite + static catchment) at anything resembling this project's
  specific schema and grid. This is a genuine gap in available reference implementations, not a
  search shortfall to gloss over.
- **Conclusion**: there is no "best" external multimodal candidate to compare on these axes — this
  row is N/A by evidence, not by omission. The most realistic multimodal path remains extending the
  repo's own existing architecture (option 2) with the newly catalogued data sources, informed by
  general fusion-architecture literature rather than a specific adoptable repo.

## (5) Other genuinely suitable candidate — ConvLSTM-family (ConvLSTM / ConvLSTM2D-style sequence
models, generic implementations)

- **Data requirements**: works directly on the project's existing coarse-grid feature stack (no
  need for dense radar imagery) — this is its main advantage over Earthformer/DGMR.
- **Training requirements**: train from scratch on this project's ERA5/GFS/terrain history; no
  pretrained India-specific weights exist, so this is a real training effort, but a much smaller,
  more tractable one than adapting a radar-scale transformer.
- **Inference cost**: moderate — convolutional recurrent nets are cheaper than full attention
  transformers at comparable sequence length.
- **Integration effort**: MEDIUM — architecture is simple enough to implement directly against the
  992-cell grid (reshaped as a small 2D grid or graph), genuinely spatiotemporal by construction
  (it is an LSTM with convolutional gates over a sequence of grids).
- **Scientific suitability**: appropriate match for this project's actual spatial resolution,
  unlike the two external transformer/GAN options benchmarked at radar scale.
- **992-cell compatibility**: YES, directly, once the 992 cells are reshaped into a regular or
  near-regular grid representation (the project already treats them as a 1.0° grid, which is
  naturally array-shaped).
- **2-6h horizon**: plausible but unproven — would need an actual training run with a 2-6h-ahead
  target and a proper spatial/temporal holdout evaluation.
- **TS/CB/FF support**: would need a multi-head output layer added (straightforward engineering,
  unlike the grid-mismatch problem with Earthformer/DGMR).
- **Validation requirements**: same rigor needed as option 2 — spatial holdout, leakage audit
  (especially re: the catchment-leakage lessons already documented for Phase 5.7), calibration.

## Verdict on picking a winner

**No winner is picked.** The evidence does not make one obvious:
- Option (1) is the only one that is real and validated *today*, but only at VOBL, and its FF head
  is built on a proxy label (RED) — it cannot be called a pan-India or genuinely-validated-FF
  solution regardless of its production status.
- Options (2) and (5) are the two most *structurally appropriate* candidates for this project's
  actual 992-cell grid resolution, but both are currently untrained — choosing between them is a
  research decision (custom MTL transformer vs. ConvLSTM-family) that should follow a real
  comparative training experiment using the newly catalogued ERA5/CartoDEM/HydroSHEDS data, not be
  made from documentation research alone.
- Option (3) (Earthformer) is the most architecturally sophisticated, but its data-regime mismatch
  with this project's coarse grid is severe enough that recommending it as the next step would be
  recommending a research project disguised as an integration task.
- Option (4) has no real external candidate — it is not applicable as a comparison, only as a
  gap finding.

**Recommended next step is procedural, not a model pick**: train and validate (2) and (5) against
each other on the same ERA5/CartoDEM/HydroSHEDS-enriched 992-cell dataset, with a proper spatial
holdout, before any model-selection claim is made. This phase's job was to find candidates and
evidence, not to simulate a training run that was not actually performed.
