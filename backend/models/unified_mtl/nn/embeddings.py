"""
backend/models/unified_mtl/nn/embeddings.py
==============================================
Phase 23 -- the four embedding types the backbone's input contract
requires: feature projection, provenance embedding, cell positional
embedding, and temporal/lead-time embedding.
"""
from __future__ import annotations

import numpy as np

from .layers import Embedding, Linear, Module
from .tensor import Tensor

# Must match backend/data_sources/__init__.py::SourceStatus plus the
# Phase 22 common-grid provenance enum (OBSERVED/FORECAST/REANALYSIS/
# DERIVED/PROXY/MISSING) -- one id per category, index 0 reserved for
# MISSING so an all-zero one-hot never happens to collide with a real
# category by accident.
PROVENANCE_CATEGORIES = ["MISSING", "OBSERVED", "FORECAST", "REANALYSIS", "DERIVED", "PROXY"]
PROVENANCE_TO_ID = {c: i for i, c in enumerate(PROVENANCE_CATEGORIES)}


class FeatureProjection(Module):
    """Projects the raw per-feature numeric vector (after mandatory-only
    imputation, see dataset.py) into the model's embedding dimension."""

    def __init__(self, n_features: int, dim: int, rng: np.random.RandomState):
        self.proj = Linear(n_features, dim, rng)

    def __call__(self, x: Tensor) -> Tensor:
        return self.proj(x)

    def parameters(self):
        return self.proj.parameters()


class ProvenanceEmbedding(Module):
    """One embedding vector per provenance category, added to the
    feature projection so the model has an explicit signal for 'this
    value's category is PROXY/MISSING/...', independent of the (possibly
    imputed) numeric value itself."""

    def __init__(self, dim: int, rng: np.random.RandomState):
        self.emb = Embedding(len(PROVENANCE_CATEGORIES), dim, rng)

    def __call__(self, provenance_ids: np.ndarray) -> Tensor:
        return self.emb(provenance_ids)

    def parameters(self):
        return self.emb.parameters()


class CellPositionalEmbedding(Module):
    """Encodes a cell's lat/lon as a fixed sinusoidal positional vector
    (no learned parameters -- generalizes to any of the 992 cells, or a
    future finer grid, without retraining an embedding table sized to a
    fixed cell count)."""

    def __init__(self, dim: int):
        assert dim % 4 == 0, "CellPositionalEmbedding dim must be divisible by 4 (lat/lon x sin/cos pairs)"
        self.dim = dim
        quarter = dim // 4
        self.freqs = 1.0 / (10000 ** (np.arange(quarter) / quarter))

    def __call__(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        lat = np.asarray(lat, dtype=np.float64).reshape(-1, 1)
        lon = np.asarray(lon, dtype=np.float64).reshape(-1, 1)
        lat_ang = lat * self.freqs[None, :]
        lon_ang = lon * self.freqs[None, :]
        return np.concatenate([np.sin(lat_ang), np.cos(lat_ang), np.sin(lon_ang), np.cos(lon_ang)], axis=-1)


class LeadTimeEmbedding(Module):
    """Learned embedding over the 5 supported lead hours (2,3,4,5,6) plus
    one reserved id for 'no specific lead / analysis time' (lead=0,
    used for the daily-prototype training path, Track CB-adapter)."""

    SUPPORTED_LEADS = (0, 2, 3, 4, 5, 6)
    LEAD_TO_ID = {h: i for i, h in enumerate(SUPPORTED_LEADS)}

    def __init__(self, dim: int, rng: np.random.RandomState):
        self.emb = Embedding(len(self.SUPPORTED_LEADS), dim, rng)

    def __call__(self, lead_hours: np.ndarray) -> Tensor:
        ids = np.array([self.LEAD_TO_ID[int(h)] for h in np.atleast_1d(lead_hours)])
        return self.emb(ids)

    def parameters(self):
        return self.emb.parameters()
