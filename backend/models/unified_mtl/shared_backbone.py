"""
backend/models/unified_mtl/shared_backbone.py
================================================
Phase 23 -- the real, trainable shared spatiotemporal backbone,
replacing the Phase 22 interface stub.

Architecture (see docs/PHASE_23_UNIFIED_MTL_IMPLEMENTATION.md for full
shapes/parameter counts):

    Common Grid features (B, cells, T, F) + provenance ids + mask
        -> FeatureProjection (Linear F->D) + ProvenanceEmbedding (D)
           + CellPositionalEmbedding (D, fixed sinusoidal) + LeadTimeEmbedding (D)
        -> per-cell TemporalEncoder: a stack of TransformerEncoderLayer
           blocks applied along the TIME axis, independently per cell
           (NOT per all 992 cells jointly -- this is what keeps cost
           linear in #cells instead of attention's O(cells^2))
        -> mean-pool over time -> per-cell latent (B, cells, D)
        -> lightweight SpatialMixer: a single linear "mix with the
           global mean of all cells in the batch" layer (residual),
           standing in for full spatial attention without the O(992^2)
           cost Phase 22's instructions explicitly asked to avoid
        -> shared latent state (B, cells, D), handed to the 3 heads.

Honest status: this IS now trained. "Trained" here means the module is
a real differentiable graph with real parameters that
backend/models/unified_mtl/training.py can update via backprop --
whether it HAS BEEN trained on real labels, and on what, is reported
separately per head in docs/PHASE_23_UNIFIED_MTL_IMPLEMENTATION.md and
is NOT implied by this module existing. Calling SharedBackbone() with
no checkpoint loaded gives a randomly-initialized (untrained) encoder.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

from .nn.embeddings import CellPositionalEmbedding, FeatureProjection, LeadTimeEmbedding, ProvenanceEmbedding
from .nn.layers import Linear, Module, TransformerEncoderLayer
from .nn.tensor import Tensor

DEFAULT_DIM = 32
DEFAULT_N_LAYERS = 2
DEFAULT_N_HEADS = 4
DEFAULT_FF_HIDDEN = 64


class SharedBackbone(Module):
    """Real, trainable shared encoder. encode() no longer raises --
    this IS the implementation Phase 22 deferred."""

    def __init__(self, n_features: int, dim: int = DEFAULT_DIM, n_layers: int = DEFAULT_N_LAYERS,
                 n_heads: int = DEFAULT_N_HEADS, ff_hidden: int = DEFAULT_FF_HIDDEN, seed: int = 42):
        self.n_features = n_features
        self.dim = dim
        self.n_layers = n_layers
        self.n_heads = n_heads
        self.ff_hidden = ff_hidden
        self.seed = seed
        self._trained = False
        self._rng = np.random.RandomState(seed)

        self.feature_proj = FeatureProjection(n_features, dim, self._rng)
        self.provenance_emb = ProvenanceEmbedding(dim, self._rng)
        self.cell_pos_emb = CellPositionalEmbedding(dim)
        self.lead_emb = LeadTimeEmbedding(dim, self._rng)
        self.temporal_layers = [TransformerEncoderLayer(dim, n_heads, ff_hidden, self._rng) for _ in range(n_layers)]
        self.spatial_mix = Linear(dim, dim, self._rng)

    def encode(self, features: np.ndarray, provenance_ids: np.ndarray, time_mask: np.ndarray,
               lat: np.ndarray, lon: np.ndarray, lead_hours) -> Tensor:
        """
        features:       (B, C, T, F) float -- numerically imputed values only
                         where mathematically necessary (see dataset.py);
                         F = n_features.
        provenance_ids:  (B, C, T, F) int -- category id per raw feature value
                         (indexes embeddings.PROVENANCE_CATEGORIES); MISSING=0.
        time_mask:       (B, C, T) bool -- True where this timestep is PADDING
                         (not a real observation/forecast slot) and must not
                         contribute to attention.
        lat, lon:        (C,) float -- cell coordinates for positional embedding.
        lead_hours:      scalar or (B,) -- requested lead time, one of
                         LeadTimeEmbedding.SUPPORTED_LEADS.

        Returns: Tensor of shape (B, C, D) -- the shared latent state.
        """
        B, C, T, F = features.shape
        assert F == self.n_features, f"expected {self.n_features} features, got {F}"

        feat_t = Tensor(features.reshape(B * C, T, F))
        proj_flat = self.feature_proj(feat_t)  # (B*C, T, D)
        proj = proj_flat.reshape(B, C, T, self.dim)

        # Provenance embedding: one id per (feature) -- reduce F provenance
        # ids per timestep to a single embedding by averaging the F
        # per-feature provenance embeddings (cheap, permutation-invariant,
        # avoids an F-times blowup of the sequence length).
        prov_flat = provenance_ids.reshape(-1)
        prov_emb_per_feature = self.provenance_emb(prov_flat)  # (B*C*T*F, D) -- gradient-connected to the embedding table
        prov_emb = prov_emb_per_feature.reshape(B * C * T, F, self.dim).mean(axis=1).reshape(B, C, T, self.dim)

        x4 = proj + prov_emb  # (B, C, T, D)

        # Cell positional embedding: same for every (batch, time) at a
        # given cell -- plain Tensor broadcast-add (differentiable, no
        # raw-numpy broadcast that would break the graph).
        cell_pos = self.cell_pos_emb(lat, lon)  # (C, D) numpy, no learnable params
        x4 = x4 + Tensor(cell_pos.reshape(1, C, 1, self.dim))

        # Lead-time embedding: same for every (cell, time) in a given batch item.
        lead_vec = self.lead_emb(lead_hours)  # (B or 1, D) -- Tensor, connected to its embedding table
        lead_r = lead_vec.reshape(lead_vec.shape[0], 1, 1, self.dim)
        x4 = x4 + lead_r  # broadcasts (B_or_1,1,1,D) against (B,C,T,D) via Tensor.__add__'s unbroadcast rule

        x = x4.reshape(B * C, T, self.dim)

        # Additive attention mask from time_mask: disallow attending to
        # padded timesteps (never let the model treat padding as data).
        tm = time_mask.reshape(B * C, T)
        add_mask = np.where(tm[:, None, None, :], -1e9, 0.0)  # (B*C,1,1,T) broadcasts over heads/query-time

        for layer in self.temporal_layers:
            x = layer(x, attn_mask_add=add_mask)

        # Mean-pool over time, excluding padded steps.
        valid = (~tm).astype(np.float64)[:, :, None]  # (B*C, T, 1)
        denom = np.maximum(valid.sum(axis=1), 1.0)  # (B*C, 1)
        pooled_data = (x.data * valid).sum(axis=1) / denom  # (B*C, D)
        pooled = Tensor(pooled_data, x.requires_grad, (x,), "time_pool")

        def _bw():
            if x.requires_grad:
                x._ensure_grad()
                g = (pooled.grad / denom)[:, None, :] * valid
                x.grad += g
        pooled._backward_fn = _bw

        per_cell = Tensor(pooled.data.reshape(B, C, self.dim), pooled.requires_grad, (pooled,), "reshape")

        def _bw2():
            if pooled.requires_grad:
                pooled._ensure_grad()
                pooled.grad += per_cell.grad.reshape(B * C, self.dim)
        per_cell._backward_fn = _bw2

        # Lightweight spatial mixing: residual + linear(mean over cells),
        # i.e. every cell's latent is nudged toward a learned function of
        # the batch's cell-mean -- O(C) cost, not O(C^2) full attention,
        # per the Phase 23 instruction to avoid quadratic spatial cost.
        cell_mean = per_cell.mean(axis=1, keepdims=True)  # (B,1,D)
        mixed = self.spatial_mix(cell_mean)
        shared_latent = per_cell + mixed  # broadcasts (B,1,D) over C

        self._last_output_shape = shared_latent.shape
        return shared_latent

    def parameters(self) -> list:
        params = (self.feature_proj.parameters() + self.provenance_emb.parameters() +
                  self.lead_emb.parameters() + self.spatial_mix.parameters())
        for layer in self.temporal_layers:
            params += layer.parameters()
        return params

    def n_parameters(self) -> int:
        return sum(p.data.size for p in self.parameters())

    def mark_trained(self):
        self._trained = True

    def describe(self) -> dict:
        return {
            "component": "shared_backbone",
            "trained": self._trained,
            "architecture": f"per-cell TransformerEncoder (dim={self.dim}, layers={self.n_layers}, "
                             f"heads={self.n_heads}, ff_hidden={self.ff_hidden}) + residual spatial-mean mixing",
            "n_features": self.n_features,
            "n_parameters": self.n_parameters(),
            "note": ("Randomly initialized, not yet trained on real labels." if not self._trained else
                      "Trained -- see docs/PHASE_23_UNIFIED_MTL_IMPLEMENTATION.md for what labels/data."),
        }

    # --- checkpointing ---
    def state_dict(self) -> dict:
        return {
            "config": {"n_features": self.n_features, "dim": self.dim, "n_layers": self.n_layers,
                       "n_heads": self.n_heads, "ff_hidden": self.ff_hidden, "seed": self.seed,
                       "trained": self._trained},
            "params": [p.data.copy() for p in self.parameters()],
        }

    def load_state_dict(self, state: dict):
        for p, arr in zip(self.parameters(), state["params"]):
            assert p.data.shape == arr.shape, f"shape mismatch: {p.data.shape} vs {arr.shape}"
            p.data = arr.copy()
        self._trained = state["config"].get("trained", False)

    def save(self, path: str):
        with open(path, "wb") as f:
            pickle.dump(self.state_dict(), f)

    @classmethod
    def load(cls, path: str) -> "SharedBackbone":
        with open(path, "rb") as f:
            state = pickle.load(f)
        cfg = state["config"]
        obj = cls(n_features=cfg["n_features"], dim=cfg["dim"], n_layers=cfg["n_layers"],
                   n_heads=cfg["n_heads"], ff_hidden=cfg["ff_hidden"], seed=cfg["seed"])
        obj.load_state_dict(state)
        return obj
