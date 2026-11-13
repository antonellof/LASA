"""Similarity metrics and profiles on synthetic fixtures. No models are loaded."""

import numpy as np
import pytest

from lasa.pipeline.similarity import profile
from lasa.similarity_metrics import cosine_meanpool, linear_cka


@pytest.fixture
def rng():
    return np.random.default_rng(0)


def test_cka_identical_is_one(rng):
    X = rng.normal(size=(50, 16))
    assert linear_cka(X, X) == pytest.approx(1.0)


def test_cka_invariant_to_orthogonal_rotation(rng):
    X = rng.normal(size=(60, 12))
    Q, _ = np.linalg.qr(rng.normal(size=(12, 12)))
    assert linear_cka(X, X @ Q) == pytest.approx(1.0, abs=1e-9)


def test_cka_invariant_to_isotropic_scale(rng):
    X = rng.normal(size=(40, 8))
    assert linear_cka(X, 7.5 * X) == pytest.approx(1.0, abs=1e-9)


def test_cka_independent_is_low(rng):
    X = rng.normal(size=(200, 32))
    Y = rng.normal(size=(200, 32))
    assert linear_cka(X, Y) < 0.2


def test_cka_allows_different_feature_dims(rng):
    X = rng.normal(size=(30, 10))
    Y = rng.normal(size=(30, 24))
    value = linear_cka(X, Y)
    assert 0.0 <= value <= 1.0


def test_cka_rejects_mismatched_probe_counts(rng):
    with pytest.raises(ValueError):
        linear_cka(rng.normal(size=(10, 4)), rng.normal(size=(11, 4)))


def test_cosine_identical_is_one(rng):
    X = rng.normal(size=(20, 8))
    assert cosine_meanpool(X, X) == pytest.approx(1.0)


def test_cosine_opposite_is_minus_one(rng):
    X = rng.normal(size=(20, 8))
    assert cosine_meanpool(X, -X) == pytest.approx(-1.0)


def test_cosine_rejects_different_feature_dims(rng):
    with pytest.raises(ValueError):
        cosine_meanpool(rng.normal(size=(5, 4)), rng.normal(size=(5, 6)))


def test_profile_shape_and_summary(rng):
    base = [rng.normal(size=(40, 8)) for _ in range(5)]
    variant = [b + 0.01 * rng.normal(size=b.shape) for b in base]
    prof = profile(base, variant)
    assert set(prof) == {"cka", "cosine_meanpool"}
    for name in prof:
        assert len(prof[name]["per_layer"]) == 5
        assert prof[name]["mean"] == pytest.approx(np.mean(prof[name]["per_layer"]))
    # A small perturbation should keep the profile near 1.
    assert prof["cka"]["mean"] > 0.95


def test_profile_detects_divergent_layers(rng):
    base = [rng.normal(size=(60, 8)) for _ in range(4)]
    # Layers 0-1 kept, layers 2-3 replaced with unrelated noise.
    variant = base[:2] + [rng.normal(size=(60, 8)) for _ in range(2)]
    prof = profile(base, variant, metrics=("cka",))
    per = prof["cka"]["per_layer"]
    assert per[0] == pytest.approx(1.0)
    assert per[2] < 0.5 and per[3] < 0.5


def test_profile_rejects_layer_count_mismatch(rng):
    base = [rng.normal(size=(10, 4)) for _ in range(3)]
    variant = [rng.normal(size=(10, 4)) for _ in range(2)]
    with pytest.raises(ValueError):
        profile(base, variant)
