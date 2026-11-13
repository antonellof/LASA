"""GCG suffix optimization adapter wrapping nanoGCG (Zou et al., 2023).

Reference:
    Zou et al., "Universal and Transferable Adversarial Attacks on Aligned
    Language Models" (arXiv:2307.15043)
    Wrapped package: nanogcg==0.3.0 (MIT License)

Converts GCG target cross-entropy loss and base generation judge score into a
continuous jailbreak strength score on [0, 1].
"""

from __future__ import annotations

import math
from typing import Callable

TOOL_NAME = "nanogcg"
TOOL_VERSION = "0.3.0"


def loss_to_strength(best_loss: float, base_asr: float) -> float:
    """Convert GCG best target loss and base ASR into a continuous strength in [0, 1].

    exp(-loss) is the geometric mean target token probability on the base model.
    Combining 0.5 * exp(-loss) + 0.5 * base_asr provides continuous differentiation
    even among attacks with identical binary outcomes.
    """
    prob_term = math.exp(-max(0.0, float(best_loss)))
    return float(0.5 * prob_term + 0.5 * float(base_asr))


def run_gcg_on_base(
    model,
    tokenizer,
    behaviors: list,
    judge_fn: Callable[[str, str], int],
    num_steps: int = 20,
    search_width: int = 64,
    topk: int = 64,
    seed: int = 0,
    max_new_tokens: int = 64,
) -> dict:
    """Run nanoGCG suffix search on the base model for each behavior."""
    import importlib.metadata
    import nanogcg
    import torch
    if importlib.metadata.version("nanogcg") != TOOL_VERSION:
        raise ValueError("nanoGCG version differs from the pinned adapter version")
    from nanogcg import GCGConfig

    cfg = GCGConfig(
        num_steps=int(num_steps),
        search_width=int(search_width),
        topk=int(topk),
        seed=int(seed),
        use_prefix_cache=False,
        verbosity="ERROR",
    )

    attacks = []
    for beh in behaviors:
        messages = [{"role": "user", "content": beh.instruction}]
        result = nanogcg.run(model, tokenizer, messages, beh.target, config=cfg)
        suffix = result.best_string
        best_loss = float(result.best_loss)

        adv_instruction = f"{beh.instruction} {suffix}"
        if getattr(tokenizer, "chat_template", None):
            prompt = tokenizer.apply_chat_template(
                [{"role": "user", "content": adv_instruction}],
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            prompt = f"User: {adv_instruction}\nAssistant:"

        enc = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=320).to(
            model.device
        )
        with torch.no_grad():
            gen_ids = model.generate(
                **enc,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
            )
        resp = tokenizer.decode(
            gen_ids[0][enc.input_ids.shape[-1] :], skip_special_tokens=True
        ).strip()
        succeeded = int(judge_fn(beh.instruction, resp))
        strength = loss_to_strength(best_loss, float(succeeded))

        attacks.append(
            {
                "attack_id": f"{beh.behavior_id}__gcg",
                "behavior_id": beh.behavior_id,
                "category": beh.category,
                "primitive": "gcg",
                "instruction": beh.instruction,
                "suffix": suffix,
                "best_loss": best_loss,
                "base_success": bool(succeeded),
                "base_asr": float(succeeded),
                "strength": strength,
            }
        )

    return {
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "config": {"num_steps": num_steps, "search_width": search_width, "topk": topk},
        "attacks": attacks,
    }
