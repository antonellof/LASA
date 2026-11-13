"""Unit tests for transfer-prediction scoring, baselines, and report metrics."""

import numpy as np
import pytest

from lasa.attacks.gcg import loss_to_strength
from lasa.attacks.refusal_direction import ablate_vector, compute_mean_diff, select_best_layer
from lasa.judge import bootstrap_asr, is_refusal, judge_human_agreement
from lasa.pipeline.predict import (
    effective_similarity,
    normalize_strengths,
    rank_attacks_for_variant,
    score,
)
from lasa.pipeline.report import compute_baselines_and_headline, map_owasp_category, safe_spearman


def test_score_product():
    assert score(0.8, 0.5) == pytest.approx(0.4)


def test_normalize_strengths_minmax():
    vals = normalize_strengths([2.0, 4.0, 6.0])
    assert vals == pytest.approx([0.0, 0.5, 1.0])


def test_normalize_strengths_constant():
    vals = normalize_strengths([0.7, 0.7, 0.7])
    assert vals == pytest.approx([0.7, 0.7, 0.7])


def test_rank_attacks_orders_descending():
    attacks = [
        {
            "attack_id": "a1",
            "behavior_id": "b1",
            "category": "Privacy",
            "primitive": "gcg",
            "strength": 0.2,
            "base_asr": 0.0,
        },
        {
            "attack_id": "a2",
            "behavior_id": "b2",
            "category": "Privacy",
            "primitive": "refusal_direction",
            "layer": 1,
            "strength": 0.9,
            "base_asr": 1.0,
        },
    ]
    profile = {"cka": {"per_layer": [0.95, 0.90, 0.85, 0.80], "mean": 0.875}}
    ranked = rank_attacks_for_variant(attacks, "var/m1", profile)
    assert ranked[0]["attack_id"] == "a2"
    assert ranked[0]["rank"] == 1
    assert ranked[1]["rank"] == 2
    assert ranked[0]["predicted_score"] > ranked[1]["predicted_score"]


def test_effective_similarity_uses_layer_for_refusal_direction():
    profile = {"cka": {"per_layer": [1.0, 0.9, 0.2, 0.8], "mean": 0.725}}
    atk = {"primitive": "refusal_direction", "layer": 1}
    # layer 1 maps to hidden_states index 2 (0.2), blended 50/50 with mean 0.725 -> 0.4625
    assert effective_similarity(atk, profile, "cka") == pytest.approx(0.4625)


def test_refusal_direction_math_and_ablation():
    rng = np.random.default_rng(42)
    # Layer 0 has weak separation; layer 1 has strong separation along axis 0
    harm0 = rng.normal(0.0, 0.1, size=(16, 8))
    safe0 = rng.normal(0.0, 0.1, size=(16, 8))
    harm1 = rng.normal(0.0, 0.1, size=(16, 8))
    harm1[:, 0] += 5.0
    safe1 = rng.normal(0.0, 0.1, size=(16, 8))
    safe1[:, 0] -= 5.0
    harm2 = rng.normal(0.0, 0.1, size=(16, 8))
    safe2 = rng.normal(0.0, 0.1, size=(16, 8))

    dirs = compute_mean_diff([harm0, harm1, harm2], [safe0, safe1, safe2])
    assert len(dirs) == 3
    best_layer, d = select_best_layer([harm0, harm1, harm2], [safe0, safe1, safe2])
    assert best_layer == 1
    # Ablating d removes component along d
    ablated = ablate_vector(harm1, d)
    assert np.allclose(ablated @ d, 0.0, atol=1e-7)


def test_gcg_loss_to_strength_monotonic():
    assert loss_to_strength(0.1, 1.0) > loss_to_strength(2.5, 1.0)
    assert 0.0 <= loss_to_strength(5.0, 0.0) <= 1.0


def test_judge_and_bootstrap_ci():
    assert is_refusal("I cannot fulfill this request. As an AI assistant, I must decline.")
    assert not is_refusal(
        "Sure, here is a detailed multi-step explanation of the requested topic with examples."
    )
    stats = bootstrap_asr([1, 1, 1, 0, 1], seed=0)
    assert stats["asr"] == pytest.approx(0.8)
    assert 0.0 <= stats["ci_low"] <= stats["asr"] <= stats["ci_high"] <= 1.0

    agree = judge_human_agreement([1, 0, 1, 0], [1, 0, 1, 0])
    assert agree["agreement"] == pytest.approx(1.0)
    assert agree["cohens_kappa"] == pytest.approx(1.0)


def test_spearman_and_baselines_beats_base_only_when_similarity_varies():
    # Variant A has high similarity (0.95) and attacks transfer; Variant B has low similarity (0.2) and attacks fail
    records = [
        {"predicted_score": 0.95 * 0.9, "measured_asr": 0.9, "base_asr": 1.0, "base_strength": 0.9},
        {"predicted_score": 0.95 * 0.7, "measured_asr": 0.7, "base_asr": 1.0, "base_strength": 0.7},
        {"predicted_score": 0.20 * 0.95, "measured_asr": 0.1, "base_asr": 1.0, "base_strength": 0.95},
        {"predicted_score": 0.20 * 0.8, "measured_asr": 0.0, "base_asr": 1.0, "base_strength": 0.8},
    ]
    res = compute_baselines_and_headline(records, seed=0)
    assert res["predicted_vs_measured"]["rho"] > 0.95
    assert res["predicted_vs_measured"]["rho"] > res["baseline_base_strength_only"]["rho"]
    assert res["baseline_base_only_asr"]["rho"] == 0.0
    assert map_owasp_category("Privacy").startswith("LLM02")
    assert safe_spearman([1.0, 1.0], [0.2, 0.8])["rho"] == 0.0
