"""Stage 4: transfer-prediction score per (attack, variant). Offline.

Never queries a third-party target. Default form:
    score = normalize(strength_on_base) * similarity(base, variant)
Keep the scoring function pluggable so the paper can compare forms.

Notice on layer-specific similarity:
  - For refusal_direction attacks, similarity is evaluated at the selected
    refusal layer l* (plus summary mean) because direction transfer depends on
    whether the variant preserves the representation at that layer.
  - For GCG attacks, similarity uses the deeper-layer mean (second half of
    layers) or full-profile mean.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from lasa.context import Context


def score(strength_norm: float, similarity: float) -> float:
    return float(strength_norm * similarity)


def score_strength_only(strength_norm: float, similarity: float) -> float:
    del similarity
    return float(strength_norm)


def score_similarity_only(strength_norm: float, similarity: float) -> float:
    del strength_norm
    return float(similarity)


def score_harmonic(strength_norm: float, similarity: float) -> float:
    denom = strength_norm + similarity
    if denom <= 0.0:
        return 0.0
    return float(2.0 * strength_norm * similarity / denom)


SCORING_FUNCTIONS: dict[str, Callable[[float, float], float]] = {
    "strength_times_similarity": score,
    "strength_only": score_strength_only,
    "similarity_only": score_similarity_only,
    "harmonic": score_harmonic,
}


def normalize_strengths(strengths: list[float]) -> list[float]:
    """Min-max normalize strengths to [0, 1]. Constant vectors map to their clipped value."""
    if not strengths:
        return []
    arr = np.asarray(strengths, dtype=np.float64)
    lo, hi = float(arr.min()), float(arr.max())
    if hi - lo < 1e-12:
        return [float(np.clip(v, 0.0, 1.0)) for v in arr]
    normed = (arr - lo) / (hi - lo)
    return [float(v) for v in normed]


def effective_similarity(attack: dict, variant_profile: dict, metric: str = "refcos_only") -> float:
    """Resolve the representational similarity relevant to a specific attack.

    Supported metrics:
      - "refcos_only" (default): uses only the variant's refusal-direction cosine
        alignment clipped at 0, max(0, RefCos).
      - "cka": blends layer-specific CKA with global mean CKA (without RefCos).
      - "refcos_times_cka": product of max(0, RefCos) and layer-blended CKA.
    """
    if metric not in {"refcos_only", "refcos_times_cka", "cka", "cosine_meanpool"}:
        raise ValueError(f"unknown similarity metric: {metric}")
    ref_sub = variant_profile.get("refusal_subspace")
    if metric.startswith("refcos") and (not ref_sub or "refusal_direction_cosine" not in ref_sub):
        raise ValueError("RefCos scoring requires measured refusal-subspace alignment; no CKA fallback")
    if metric == "refcos_only":
        return max(0.0, float(ref_sub["refusal_direction_cosine"]))

    profile_key = "cosine_meanpool" if metric == "cosine_meanpool" else "cka"
    if profile_key not in variant_profile:
        raise ValueError(f"missing measured similarity profile: {profile_key}")
    prof = variant_profile[profile_key]
    per_layer = prof["per_layer"]
    mean_sim = float(prof["mean"])
    if not per_layer:
        return mean_sim

    if attack.get("primitive") == "refusal_direction" and "layer" in attack:
        # hidden_states[0] is embeddings, so block layer k is at index min(k + 1, len - 1)
        idx = min(int(attack["layer"]) + 1, len(per_layer) - 1)
        layer_sim = float(per_layer[idx])
        cka_blend = 0.5 * layer_sim + 0.5 * mean_sim
    else:
        half = max(1, len(per_layer) // 2)
        upper_sim = float(np.mean(per_layer[half:]))
        cka_blend = 0.5 * upper_sim + 0.5 * mean_sim

    if metric in {"cka", "cosine_meanpool"}:
        return cka_blend
    if ref_sub and "refusal_direction_cosine" in ref_sub:
        rcos_pos = max(0.0, float(ref_sub["refusal_direction_cosine"]))
        return rcos_pos * cka_blend
    return cka_blend


def rank_attacks_for_variant(
    attacks: list[dict],
    variant_id: str,
    variant_profile: dict,
    scoring_name: str = "strength_times_similarity",
    similarity_metric: str = "cka",
) -> list[dict]:
    """Compute transfer prediction scores and rank attacks descending for one variant.

    Normalizes only the continuous tie-breaker margin within each primitive,
    keeping the base attack success signal (base_asr) anchored on [0, 1] so
    base-successful attacks rank above base-failed attacks across families.
    """
    if scoring_name not in SCORING_FUNCTIONS:
        raise ValueError(
            f"unknown scoring function {scoring_name!r}; valid: {sorted(SCORING_FUNCTIONS)}"
        )
    fn = SCORING_FUNCTIONS[scoring_name]

    norm_by_id: dict[str, float] = {}
    primitives = sorted({a["primitive"] for a in attacks})
    for prim in primitives:
        group = [a for a in attacks if a["primitive"] == prim]
        margins = [
            max(0.0, float(a["strength"]) - 0.5 * float(a.get("base_asr", float(a.get("base_success", 0.0)))))
            for a in group
        ]
        norm_margins = normalize_strengths(margins)
        for a, m_norm in zip(group, norm_margins):
            b_asr = float(a.get("base_asr", float(a.get("base_success", 0.0))))
            norm_by_id[a["attack_id"]] = 0.5 * b_asr + 0.5 * m_norm

    predictions = []
    for atk in attacks:
        s_norm = norm_by_id[atk["attack_id"]]
        sim_val = effective_similarity(atk, variant_profile, metric=similarity_metric)
        pred_score = fn(s_norm, sim_val)
        predictions.append(
            {
                "attack_id": atk["attack_id"],
                "behavior_id": atk["behavior_id"],
                "category": atk["category"],
                "primitive": atk["primitive"],
                "variant": variant_id,
                "base_strength": float(atk["strength"]),
                "base_strength_norm": float(s_norm),
                "base_asr": float(atk.get("base_asr", float(atk.get("base_success", 0.0)))),
                "similarity": float(sim_val),
                "predicted_score": float(pred_score),
            }
        )

    predictions.sort(key=lambda x: x["predicted_score"], reverse=True)
    for rank_idx, item in enumerate(predictions, start=1):
        item["rank"] = rank_idx
    return predictions


def run(ctx: Context) -> Path:
    sim_art = ctx.read_artifact("similarity")
    atk_art = ctx.read_artifact("attack_base")

    pred_cfg = ctx.config.get("predict", {})
    scoring_name = pred_cfg.get("scoring", "strength_times_similarity")
    sim_metric = pred_cfg.get("similarity_metric", "refcos_only")

    attacks = atk_art["attacks"]
    variant_predictions = []
    for var_entry in sim_art["variants"]:
        vid = var_entry["model_id"]
        ranked = rank_attacks_for_variant(
            attacks=attacks,
            variant_id=vid,
            variant_profile=var_entry["profile"],
            scoring_name=scoring_name,
            similarity_metric=sim_metric,
        )
        variant_predictions.append({"model_id": vid, "predictions": ranked})

    return ctx.write_artifact(
        "predict",
        {
            "seed": ctx.seed,
            "scoring": scoring_name,
            "similarity_metric": sim_metric,
            "base": ctx.base_model_id,
            "variants": variant_predictions,
        },
    )
