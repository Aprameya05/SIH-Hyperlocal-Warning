"""
backend/models/unified_mtl/
=============================
Phase 22 Track 4 -- foundation for the eventual shared-encoder
{TS, CB, FF} multi-task architecture.

HONEST STATUS: there is no trained shared encoder/backbone yet. This
package defines the TARGET INTERFACE (Common Grid -> Multimodal Temporal
Encoder -> Shared Backbone -> {TS, CB, FF} heads) and wires in the three
heads as INDEPENDENTLY TRAINED models behind that interface:

  - CB head: scripts/panindia_cb_model_interface.py::PanIndiaCBModel
    (Phase 21, real XGBoost model, LODO-validated).
  - TS head: the Phase G logistic-regression baseline
    (docs/PHASE_G_TS_BASELINE_RESULT.json) -- explicitly RESEARCH_ONLY,
    28 training rows, not production-grade.
  - FF head: the Phase 5.7 PU-corrected logistic model
    (processed/ff_pu/RESEARCH_ONLY_model_c_logistic.pkl) -- explicitly a
    PU ranking model, not a confirmed-negative classifier.

`shared_backbone.py::SharedBackbone` is a STUB: it does not learn a
shared representation across heads. Calling its forward/encode method
raises NotImplementedError with a clear message. It exists so the
target interface shape is fixed now (what a future shared encoder must
accept and produce) without pretending a joint model already exists.
`unified_model.py::UnifiedMTLModel` is the pluggable container that
calls each head independently today and will call the shared backbone
once it exists.
"""
