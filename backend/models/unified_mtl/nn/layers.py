"""
backend/models/unified_mtl/nn/layers.py
==========================================
Phase 23 -- layer primitives built on the Tensor autodiff engine
(tensor.py). Each layer is a Module with .parameters() and a
deterministic, seedable initializer.
"""
from __future__ import annotations

import numpy as np

from .tensor import Tensor


class Module:
    def parameters(self) -> list:
        raise NotImplementedError

    def zero_grad(self):
        for p in self.parameters():
            p.grad = None


class Linear(Module):
    def __init__(self, in_dim: int, out_dim: int, rng: np.random.RandomState):
        scale = np.sqrt(2.0 / in_dim)
        self.W = Tensor(rng.randn(in_dim, out_dim) * scale, requires_grad=True)
        self.b = Tensor(np.zeros(out_dim), requires_grad=True)

    def __call__(self, x: Tensor) -> Tensor:
        return x.matmul(self.W) + self.b

    def parameters(self):
        return [self.W, self.b]


class Embedding(Module):
    """Lookup table. forward(indices: np.ndarray[int]) -> Tensor of shape
    indices.shape + (dim,). Gradients accumulate into the full table via
    scatter-add on backward."""

    def __init__(self, n: int, dim: int, rng: np.random.RandomState):
        self.table = Tensor(rng.randn(n, dim) * 0.02, requires_grad=True)
        self.n = n

    def __call__(self, indices: np.ndarray) -> Tensor:
        idx = np.asarray(indices, dtype=np.int64)
        gathered = self.table.data[idx]
        out = Tensor(gathered, self.table.requires_grad, (self.table,), "embedding")

        def _bw():
            if self.table.requires_grad:
                self.table._ensure_grad()
                np.add.at(self.table.grad, idx, out.grad)
        out._backward_fn = _bw
        return out

    def parameters(self):
        return [self.table]


class LayerNorm(Module):
    """Affine layernorm: Tensor.layernorm() normalizes; this adds a
    learnable scale/shift, which the base engine doesn't need to know
    about (composed from existing ops, so no new backward rule)."""

    def __init__(self, dim: int):
        self.gamma = Tensor(np.ones(dim), requires_grad=True)
        self.beta = Tensor(np.zeros(dim), requires_grad=True)

    def __call__(self, x: Tensor) -> Tensor:
        return x.layernorm() * self.gamma + self.beta

    def parameters(self):
        return [self.gamma, self.beta]


class MultiHeadSelfAttention(Module):
    """Standard scaled-dot-product self-attention over the TIME axis for
    one cell's sequence at a time (batch dimension folds in cell too --
    see backbone.py for how this is invoked per-cell rather than across
    all 992 cells, to avoid quadratic spatial attention cost).

    Input: Tensor of shape (B, T, D). Output: same shape.
    An additive attention mask (B, T, T) or (1, T, T) may be passed with
    -1e9 in disallowed positions (e.g. padding from variable-length
    sequences) -- never silently attending over missing timesteps as if
    they were real.
    """

    def __init__(self, dim: int, n_heads: int, rng: np.random.RandomState):
        assert dim % n_heads == 0
        self.dim = dim
        self.n_heads = n_heads
        self.head_dim = dim // n_heads
        self.q_proj = Linear(dim, dim, rng)
        self.k_proj = Linear(dim, dim, rng)
        self.v_proj = Linear(dim, dim, rng)
        self.out_proj = Linear(dim, dim, rng)

    def __call__(self, x: Tensor, attn_mask_add: np.ndarray = None) -> Tensor:
        B, T, D = x.data.shape
        H, hd = self.n_heads, self.head_dim

        q = self.q_proj(x)
        k = self.k_proj(x)
        v = self.v_proj(x)

        def split_heads(t: Tensor) -> Tensor:
            r = t.data.reshape(B, T, H, hd).transpose(0, 2, 1, 3)  # (B,H,T,hd)
            out = Tensor(r, t.requires_grad, (t,), "split_heads")

            def _bw():
                if t.requires_grad:
                    t._ensure_grad()
                    t.grad += out.grad.transpose(0, 2, 1, 3).reshape(B, T, D)
            out._backward_fn = _bw
            return out

        qh, kh, vh = split_heads(q), split_heads(k), split_heads(v)

        scores = qh.matmul(_transpose_last_two(kh)) * (1.0 / np.sqrt(hd))  # (B,H,T,T)
        if attn_mask_add is not None:
            scores = scores + Tensor(attn_mask_add)
        attn = scores.softmax(axis=-1)
        ctx = attn.matmul(vh)  # (B,H,T,hd)

        merged = ctx.data.transpose(0, 2, 1, 3).reshape(B, T, D)
        out = Tensor(merged, ctx.requires_grad, (ctx,), "merge_heads")

        def _bw():
            if ctx.requires_grad:
                ctx._ensure_grad()
                ctx.grad += out.grad.reshape(B, T, H, hd).transpose(0, 2, 1, 3)
        out._backward_fn = _bw

        return self.out_proj(out)

    def parameters(self):
        return self.q_proj.parameters() + self.k_proj.parameters() + self.v_proj.parameters() + self.out_proj.parameters()


def _transpose_last_two(t: Tensor) -> Tensor:
    r = np.swapaxes(t.data, -1, -2)
    out = Tensor(r, t.requires_grad, (t,), "transpose")

    def _bw():
        if t.requires_grad:
            t._ensure_grad()
            t.grad += np.swapaxes(out.grad, -1, -2)
    out._backward_fn = _bw
    return out


class FeedForward(Module):
    def __init__(self, dim: int, hidden: int, rng: np.random.RandomState):
        self.fc1 = Linear(dim, hidden, rng)
        self.fc2 = Linear(hidden, dim, rng)

    def __call__(self, x: Tensor) -> Tensor:
        return self.fc2(self.fc1(x).gelu())

    def parameters(self):
        return self.fc1.parameters() + self.fc2.parameters()


class TransformerEncoderLayer(Module):
    """Standard pre-LN transformer block: x -> x + Attn(LN(x)) -> x + FF(LN(x))."""

    def __init__(self, dim: int, n_heads: int, ff_hidden: int, rng: np.random.RandomState):
        self.ln1 = LayerNorm(dim)
        self.attn = MultiHeadSelfAttention(dim, n_heads, rng)
        self.ln2 = LayerNorm(dim)
        self.ff = FeedForward(dim, ff_hidden, rng)

    def __call__(self, x: Tensor, attn_mask_add: np.ndarray = None) -> Tensor:
        x = x + self.attn(self.ln1(x), attn_mask_add)
        x = x + self.ff(self.ln2(x))
        return x

    def parameters(self):
        return self.ln1.parameters() + self.attn.parameters() + self.ln2.parameters() + self.ff.parameters()
