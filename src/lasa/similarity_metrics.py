"""Representational similarity metrics between two activation matrices.

Both functions take matrices of shape (n_probes, d) where row i of X and row i
of Y come from the same probe prompt. Hidden sizes may differ between models,
so every metric here is defined on the probe axis, not the feature axis.

Metrics:
  - linear_cka: centered kernel alignment with a linear kernel (Kornblith et al. 2019).
    Invariant to orthogonal transforms and isotropic scaling. Range [0, 1].
  - cosine_meanpool: cosine similarity of the mean over probes. Cheap and
    interpretable, but it ignores per-probe structure. Range [-1, 1].

Report both. If they disagree on a layer, the disagreement is a result.
"""

from __future__ import annotations

import numpy as np


def _check(X: np.ndarray, Y: np.ndarray) -> None:
    if X.ndim != 2 or Y.ndim != 2:
        raise ValueError("expected 2-D arrays of shape (n_probes, d)")
    if X.shape[0] != Y.shape[0]:
        raise ValueError(f"probe counts differ: {X.shape[0]} vs {Y.shape[0]}")
    if X.shape[0] < 2:
        raise ValueError("need at least 2 probes")


def linear_cka(X: np.ndarray, Y: np.ndarray) -> float:
    """Linear CKA between two (n, d) activation matrices. Returns a value in [0, 1]."""
    _check(X, Y)
    X = X.astype(np.float64)
    Y = Y.astype(np.float64)
    X = X - X.mean(axis=0, keepdims=True)
    Y = Y - Y.mean(axis=0, keepdims=True)
    # ||Y^T X||_F^2 / (||X^T X||_F * ||Y^T Y||_F). Avoids building n x n kernels.
    cross = np.linalg.norm(Y.T @ X, ord="fro") ** 2
    norm_x = np.linalg.norm(X.T @ X, ord="fro")
    norm_y = np.linalg.norm(Y.T @ Y, ord="fro")
    denom = norm_x * norm_y
    if denom == 0.0:
        return 0.0
    return float(cross / denom)


def cosine_meanpool(X: np.ndarray, Y: np.ndarray) -> float:
    """Cosine similarity between the probe-mean vectors. Returns a value in [-1, 1]."""
    _check(X, Y)
    mx = X.astype(np.float64).mean(axis=0)
    my = Y.astype(np.float64).mean(axis=0)
    # Feature dims may differ between base and variant. Cosine is only defined
    # when they match, so the caller must align them (see similarity.py).
    if mx.shape != my.shape:
        raise ValueError(f"feature dims differ: {mx.shape} vs {my.shape}")
    nx = np.linalg.norm(mx)
    ny = np.linalg.norm(my)
    if nx == 0.0 or ny == 0.0:
        return 0.0
    return float(mx @ my / (nx * ny))


METRICS = {
    "cka": linear_cka,
    "cosine_meanpool": cosine_meanpool,
}
