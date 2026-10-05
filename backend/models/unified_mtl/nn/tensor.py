"""
backend/models/unified_mtl/nn/tensor.py
==========================================
Phase 23 -- minimal reverse-mode autodiff engine, NumPy-based.

WHY THIS EXISTS INSTEAD OF PYTORCH (stated honestly, not glossed over):
PyTorch could not be installed in this environment this phase. The
default PyPI "torch" wheel requires separately-installed NVIDIA shared
libraries (libcublasLt, libcudnn, libcudart, ...) even for CPU-only
tensor operations in the versions available here, and those dependency
packages alone exceed several GB; this session's writable disk is a
fixed ~9.8GB allowance that was already at 95-98% utilization after
installing just two of them. An older CPU-default wheel (torch==2.2.2)
was attempted but repeatedly exceeded the 175s-per-call network/install
budget before completing. This is a genuine, reproducible environment
blocker (disk + network), not a decision to avoid real training code --
see docs/PHASE_23_UNIFIED_MTL_IMPLEMENTATION.md for the full record of
attempts.

What this module provides instead: a small, real reverse-mode automatic
differentiation engine over NumPy arrays -- a `Tensor` class that records
an operation graph and computes exact gradients via `.backward()`,
functionally equivalent to PyTorch's autograd for the operations this
architecture needs (matmul, add, elementwise nonlinearities, softmax,
layernorm, masking, reductions). It is NOT a reimplementation of
PyTorch's API and makes no such claim -- it is a purpose-built, tested,
CPU-only engine sized for this one architecture.

If PyTorch becomes installable in a future environment (more disk, or a
pre-cached CPU wheel), `backbone.py`/`heads_nn.py`/`training.py` can be
ported to `torch.nn.Module` with the same tensor shapes and forward
logic documented in docs/PHASE_23_UNIFIED_MTL_IMPLEMENTATION.md -- this
module's job is to make that forward logic real and gradient-checked
today, not to be a permanent dependency.
"""
from __future__ import annotations

import numpy as np


class Tensor:
    __slots__ = ("data", "grad", "_children", "_backward_fn", "requires_grad", "_op")

    def __init__(self, data, requires_grad: bool = False, _children=(), _op: str = ""):
        self.data = np.asarray(data, dtype=np.float32)
        self.grad = None
        self.requires_grad = requires_grad
        self._children = _children
        self._backward_fn = lambda: None
        self._op = _op

    @property
    def shape(self):
        return self.data.shape

    # ---- helpers ----
    @staticmethod
    def _unbroadcast(grad: np.ndarray, shape: tuple) -> np.ndarray:
        while grad.ndim > len(shape):
            grad = grad.sum(axis=0)
        for i, s in enumerate(shape):
            if s == 1 and grad.shape[i] != 1:
                grad = grad.sum(axis=i, keepdims=True)
        return grad

    def _ensure_grad(self):
        if self.grad is None:
            self.grad = np.zeros_like(self.data)

    # ---- ops ----
    def __add__(self, other):
        other = other if isinstance(other, Tensor) else Tensor(other)
        out = Tensor(self.data + other.data, self.requires_grad or other.requires_grad, (self, other), "add")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                self.grad += self._unbroadcast(out.grad, self.data.shape)
            if other.requires_grad:
                other._ensure_grad()
                other.grad += self._unbroadcast(out.grad, other.data.shape)
        out._backward_fn = _bw
        return out

    def __mul__(self, other):
        other = other if isinstance(other, Tensor) else Tensor(other)
        out = Tensor(self.data * other.data, self.requires_grad or other.requires_grad, (self, other), "mul")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                self.grad += self._unbroadcast(out.grad * other.data, self.data.shape)
            if other.requires_grad:
                other._ensure_grad()
                other.grad += self._unbroadcast(out.grad * self.data, other.data.shape)
        out._backward_fn = _bw
        return out

    def __neg__(self):
        return self * Tensor(-1.0)

    def __sub__(self, other):
        other = other if isinstance(other, Tensor) else Tensor(other)
        return self + (-other)

    def __rsub__(self, other):
        return Tensor(other) + (-self)

    def __truediv__(self, other):
        other = other if isinstance(other, Tensor) else Tensor(other)
        return self * (other ** -1.0)

    def __pow__(self, p: float):
        out = Tensor(self.data ** p, self.requires_grad, (self,), "pow")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                self.grad += out.grad * p * (self.data ** (p - 1))
        out._backward_fn = _bw
        return out

    def matmul(self, other: "Tensor") -> "Tensor":
        out = Tensor(self.data @ other.data, self.requires_grad or other.requires_grad, (self, other), "matmul")

        def _reduce_to(grad: np.ndarray, shape: tuple) -> np.ndarray:
            # Reduce away any leading batch dims that were broadcast in
            # (e.g. a 2D weight matrix multiplied against a 3D batched
            # input), matching how NumPy's @ broadcasts batch dims.
            while grad.ndim > len(shape):
                grad = grad.sum(axis=0)
            for i, s in enumerate(shape):
                if s == 1 and grad.shape[i] != 1:
                    grad = grad.sum(axis=i, keepdims=True)
            return grad

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                g = out.grad @ np.swapaxes(other.data, -1, -2)
                self.grad += _reduce_to(g, self.data.shape)
            if other.requires_grad:
                other._ensure_grad()
                g = np.swapaxes(self.data, -1, -2) @ out.grad
                other.grad += _reduce_to(g, other.data.shape)
        out._backward_fn = _bw
        return out

    def relu(self) -> "Tensor":
        out = Tensor(np.maximum(0.0, self.data), self.requires_grad, (self,), "relu")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                self.grad += out.grad * (self.data > 0)
        out._backward_fn = _bw
        return out

    def gelu(self) -> "Tensor":
        # tanh approximation of GELU
        x = self.data
        c = np.sqrt(2.0 / np.pi)
        inner = c * (x + 0.044715 * x ** 3)
        t = np.tanh(inner)
        g = 0.5 * x * (1.0 + t)
        out = Tensor(g, self.requires_grad, (self,), "gelu")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                dinner_dx = c * (1 + 3 * 0.044715 * x ** 2)
                dt_dx = (1 - t ** 2) * dinner_dx
                dg_dx = 0.5 * (1 + t) + 0.5 * x * dt_dx
                self.grad += out.grad * dg_dx
        out._backward_fn = _bw
        return out

    def sigmoid(self) -> "Tensor":
        s = 1.0 / (1.0 + np.exp(-self.data))
        out = Tensor(s, self.requires_grad, (self,), "sigmoid")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                self.grad += out.grad * s * (1 - s)
        out._backward_fn = _bw
        return out

    def sum(self, axis=None, keepdims=False) -> "Tensor":
        out = Tensor(self.data.sum(axis=axis, keepdims=keepdims), self.requires_grad, (self,), "sum")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                g = out.grad
                if axis is not None and not keepdims:
                    g = np.expand_dims(g, axis=axis)
                self.grad += np.broadcast_to(g, self.data.shape)
        out._backward_fn = _bw
        return out

    def mean(self, axis=None, keepdims=False) -> "Tensor":
        n = self.data.size if axis is None else self.data.shape[axis]
        return self.sum(axis=axis, keepdims=keepdims) * (1.0 / n)

    def layernorm(self, eps: float = 1e-5) -> "Tensor":
        mu = self.data.mean(axis=-1, keepdims=True)
        var = self.data.var(axis=-1, keepdims=True)
        inv_std = 1.0 / np.sqrt(var + eps)
        normed = (self.data - mu) * inv_std
        out = Tensor(normed, self.requires_grad, (self,), "layernorm")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                n = self.data.shape[-1]
                g = out.grad
                dxhat = g
                dvar = np.sum(dxhat * (self.data - mu), axis=-1, keepdims=True) * -0.5 * inv_std ** 3
                dmu = np.sum(dxhat * -inv_std, axis=-1, keepdims=True) + dvar * np.mean(-2.0 * (self.data - mu), axis=-1, keepdims=True)
                self.grad += dxhat * inv_std + dvar * 2.0 * (self.data - mu) / n + dmu / n
        out._backward_fn = _bw
        return out

    def masked_fill(self, mask: np.ndarray, value: float) -> "Tensor":
        """mask: boolean numpy array, same shape (or broadcastable). Where
        True, replace with `value`. Gradient does not flow through filled
        positions -- this is how 'the model knows a value was missing'
        without a gradient path contaminating real values."""
        filled = np.where(mask, value, self.data)
        out = Tensor(filled, self.requires_grad, (self,), "masked_fill")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                self.grad += np.where(mask, 0.0, out.grad)
        out._backward_fn = _bw
        return out

    def softmax(self, axis=-1) -> "Tensor":
        x = self.data
        x = x - np.max(x, axis=axis, keepdims=True)
        e = np.exp(x)
        s = e / np.sum(e, axis=axis, keepdims=True)
        out = Tensor(s, self.requires_grad, (self,), "softmax")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                g = out.grad
                dot = np.sum(g * s, axis=axis, keepdims=True)
                self.grad += s * (g - dot)
        out._backward_fn = _bw
        return out

    def bce_with_logits(self, target: np.ndarray, weight: np.ndarray = None) -> "Tensor":
        """Numerically stable binary cross-entropy from logits. target
        and weight are plain numpy arrays (not Tensors) -- labels never
        carry gradients."""
        x = self.data
        t = np.asarray(target, dtype=np.float64)
        max_val = np.clip(-x, 0, None)
        loss = x - x * t + max_val + np.log(np.exp(-max_val) + np.exp(-x - max_val))
        if weight is not None:
            loss = loss * np.asarray(weight, dtype=np.float64)
        out = Tensor(loss, self.requires_grad, (self,), "bce")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                sig = 1.0 / (1.0 + np.exp(-x))
                g = (sig - t)
                if weight is not None:
                    g = g * np.asarray(weight, dtype=np.float64)
                self.grad += out.grad * g
        out._backward_fn = _bw
        return out

    def reshape(self, *shape) -> "Tensor":
        out = Tensor(self.data.reshape(*shape), self.requires_grad, (self,), "reshape")

        def _bw():
            if self.requires_grad:
                self._ensure_grad()
                self.grad += out.grad.reshape(self.data.shape)
        out._backward_fn = _bw
        return out

    def backward(self):
        topo = []
        visited = set()

        def build(v):
            if id(v) not in visited:
                visited.add(id(v))
                for c in v._children:
                    build(c)
                topo.append(v)
        build(self)
        self._ensure_grad()
        self.grad = np.ones_like(self.data)
        for v in reversed(topo):
            v._backward_fn()

    def __repr__(self):
        return f"Tensor(shape={self.data.shape}, requires_grad={self.requires_grad})"
