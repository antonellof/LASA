"""Legacy contrast-direction analysis, inspired by Arditi et al. (2024).

This local implementation is NOT a verified upstream attack wrapper. Its layer
selection and hooks differ from the published pipeline. Attack execution is
disabled pending a pinned, validated reference adapter. Pure analysis helpers
remain for historical artifacts and benign regression tests. The current
prompt-transfer study uses published nanoGCG instead.

Reference: https://github.com/andyrdt/refusal_direction
"""

from __future__ import annotations

from typing import Callable

import numpy as np

TOOL_NAME = "lasa_legacy_direction_analysis"
TOOL_VERSION = "local-unvalidated"


def normalize_direction(direction: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(direction))
    if norm < 1e-12:
        return np.zeros_like(direction, dtype=np.float64)
    return direction.astype(np.float64) / norm


def compute_mean_diff(
    harmful_acts: list[np.ndarray],
    harmless_acts: list[np.ndarray],
) -> list[np.ndarray]:
    """Difference-in-means per layer between harmful and harmless activations.

    Each element is an (n_samples, d_model) array. Returns a list of unit
    direction vectors of shape (d_model,), one per layer.
    """
    if len(harmful_acts) != len(harmless_acts):
        raise ValueError("layer count mismatch between harmful and harmless activations")
    directions = []
    for h_harm, h_safe in zip(harmful_acts, harmless_acts):
        diff = h_harm.astype(np.float64).mean(axis=0) - h_safe.astype(np.float64).mean(axis=0)
        directions.append(normalize_direction(diff))
    return directions


def select_best_layer(
    harmful_acts: list[np.ndarray],
    harmless_acts: list[np.ndarray],
    prune_layer_percentage: float = 0.20,
) -> tuple[int, np.ndarray]:
    """Select the layer whose refusal direction best separates harmful from harmless.

    Follows Arditi et al.'s layer pruning rule (discard the final 20% of layers)
    and selects the layer maximizing mean projection margin between harmful and
    harmless activations.
    """
    directions = compute_mean_diff(harmful_acts, harmless_acts)
    n_layers = len(directions)
    max_layer = max(1, int(n_layers * (1.0 - prune_layer_percentage)))
    best_layer = 0
    best_margin = -float("inf")
    for layer in range(max_layer):
        d = directions[layer]
        margin = float((harmful_acts[layer] @ d).mean() - (harmless_acts[layer] @ d).mean())
        if margin > best_margin:
            best_margin = margin
            best_layer = layer
    return best_layer, directions[best_layer]


def ablate_vector(activations: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """Project out `direction` from activations: x - (x . d_hat) d_hat."""
    d = normalize_direction(direction)
    proj = (activations @ d)[..., None] * d
    return activations - proj


def refusal_cosine(
    base_direction: np.ndarray,
    harmful_acts_variant: list[np.ndarray],
    harmless_acts_variant: list[np.ndarray],
    layer: int,
) -> float:
    """Signed cosine alignment between the base refusal direction and the variant's refusal direction at `layer`.

    Returns the signed cosine in [-1, 1] so negative alignment (e.g. unaligned
    pretrained parents) is preserved in similarity.json rather than tied at 0.
    Downstream scoring in predict.py clips at 0 when computing non-negative
    transfer scores.
    """
    var_dirs = compute_mean_diff(harmful_acts_variant, harmless_acts_variant)
    idx = min(max(0, int(layer)), len(var_dirs) - 1)
    b = normalize_direction(base_direction)
    v = normalize_direction(var_dirs[idx])
    cos = float(np.dot(b, v))
    return float(max(-1.0, min(1.0, cos)))


def ablation_hooks(model, direction_np: np.ndarray):
    """Disabled: a pinned upstream intervention adapter is required."""
    raise NotImplementedError("Legacy ablation hooks are not a verified upstream wrapper")


ACTIVATION_EXTRACTOR_VERSION = "last-attended-v3"


def last_attended_indices(attention_mask):
    """Return the final attended position, independent of padding side."""
    import torch

    if attention_mask.ndim != 2 or attention_mask.shape[1] == 0:
        raise ValueError("attention_mask must be a non-empty (batch, sequence) matrix")
    attended = attention_mask.bool()
    if not attended.any(dim=1).all():
        raise ValueError("each sequence must contain an attended token")
    positions = torch.arange(attention_mask.shape[1], device=attention_mask.device)
    return positions.expand_as(attention_mask).masked_fill(~attended, -1).max(dim=1).values


def collect_last_token_activations(
    model,
    tokenizer,
    instructions: list[str],
    batch_size: int = 8,
) -> list[np.ndarray]:
    """Collect last-token hidden states at every transformer layer for instructions."""
    import torch

    per_layer: list[list[np.ndarray]] | None = None
    with torch.no_grad():
        for i in range(0, len(instructions), batch_size):
            batch_prompts = _format_chat_batch(tokenizer, instructions[i : i + batch_size])
            enc = tokenizer(
                batch_prompts,
                return_tensors="pt",
                add_special_tokens=not bool(getattr(tokenizer, "chat_template", None)),
                padding=True,
                truncation=True,
                max_length=256,
            ).to(model.device)
            position_ids = enc["attention_mask"].long().cumsum(dim=-1) - 1
            position_ids.masked_fill_(enc["attention_mask"] == 0, 0)
            out = model(**enc, position_ids=position_ids, output_hidden_states=True)
            # Length minus one is incorrect for left-padded sequences.
            lengths = last_attended_indices(enc["attention_mask"])
            bsz = enc["input_ids"].shape[0]
            batch_idx = torch.arange(bsz, device=model.device)
            # Exclude embedding layer 0 to align with transformer blocks
            hs = out.hidden_states[1:] if len(out.hidden_states) > 1 else out.hidden_states
            layer_vecs = [
                h[batch_idx, lengths].to(torch.float32).cpu().numpy() for h in hs
            ]
            if per_layer is None:
                per_layer = [[v] for v in layer_vecs]
            else:
                for acc, v in zip(per_layer, layer_vecs):
                    acc.append(v)
    return [np.concatenate(chunks, axis=0) for chunks in per_layer]


def _format_chat_batch(tokenizer, instructions: list[str]) -> list[str]:
    if getattr(tokenizer, "chat_template", None):
        return [
            tokenizer.apply_chat_template(
                [{"role": "user", "content": inst}],
                tokenize=False,
                add_generation_prompt=True,
            )
            for inst in instructions
        ]
    return [f"User: {inst}\nAssistant:" for inst in instructions]


def run_refusal_direction_on_base(
    model,
    tokenizer,
    behaviors: list,
    harmful_contrast: list[str],
    harmless_contrast: list[str],
    judge_fn: Callable[[str, str], int],
    max_new_tokens: int = 64,
) -> dict:
    """Refuse to label a local approximation as a published reference attack."""
    raise NotImplementedError(
        "Legacy refusal-direction attack is not a verified reference wrapper. "
        "Use the controlled nanoGCG study; a pinned upstream adapter is required for ablation experiments."
    )
