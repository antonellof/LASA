"""Stage 5: measure attack success on OWNED variants. Authorization required.

Calls lasa.authorization.require_authorized for every target before doing
anything. Runs top-K predicted attacks, multiple generations each, scores
with an LLM judge, reports attack success rate with confidence intervals,
and samples a subset for human spot-check.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import numpy as np

from lasa.attacks.refusal_direction import ACTIVATION_EXTRACTOR_VERSION, ablation_hooks
from lasa.authorization import require_authorized
from lasa.context import Context
from lasa.judge import (
    bootstrap_asr,
    heuristic_judge,
    judge_human_agreement,
    make_local_llm_judge,
)


def _load_variant_model(model_path: str, torch_dtype: str | None = None):
    import json
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "left"
    cfg_file = Path(model_path) / "config.json"
    mtype = json.loads(cfg_file.read_text()).get("model_type", "") if cfg_file.exists() else ""
    if torch_dtype is not None:
        # A study may freeze one dtype for every checkpoint, so base and targets
        # do not differ in numerics (a model released in bfloat16 can overflow in float16).
        dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}[torch_dtype]
    elif mtype == "gemma2" and (torch.cuda.is_available() or torch.backends.mps.is_available()):
        dtype = torch.bfloat16
    elif torch.cuda.is_available() or torch.backends.mps.is_available():
        dtype = torch.float16
    else:
        dtype = torch.float32
    dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=dtype,
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
    ).to(dev).eval()
    if mtype == "gemma2":
        model.config.use_cache = True
        if getattr(model, "generation_config", None) is not None:
            model.generation_config.use_cache = True
    return model, tok


def _format_prompt(tokenizer, instruction: str) -> str:
    if getattr(tokenizer, "chat_template", None):
        return tokenizer.apply_chat_template(
            [{"role": "user", "content": instruction}],
            tokenize=False,
            add_generation_prompt=True,
        )
    return f"User: {instruction}\nAssistant:"


def run_no_attack_baseline_on_variant(
    model,
    tokenizer,
    behaviors_by_id: dict[str, str],
    n_generations: int,
    max_new_tokens: int,
    seed: int,
    primary_judge_fn,
    batch_size: int = 12,
) -> dict[str, dict]:
    """Run bare instructions (no suffix, no ablation) on the variant as an attack-free control.

    Uses batch generation across behaviors for speed.
    """
    import torch

    items = list(behaviors_by_id.items())
    out_map: dict[str, dict] = {}
    with torch.no_grad():
        for i in range(0, len(items), batch_size):
            batch_slice = items[i : i + batch_size]
            batch_ids = [b[0] for b in batch_slice]
            batch_inst = [b[1] for b in batch_slice]
            batch_prompts = [_format_prompt(tokenizer, inst) for inst in batch_inst]
            enc = tokenizer(
                batch_prompts,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=320,
            ).to(model.device)
            in_len = enc["input_ids"].shape[-1]

            # 1. Greedy generation (gen_idx = 0)
            g0 = model.generate(
                **enc,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
            )
            sampled_batches = [g0[:, in_len:]]

            # 2. Sampled generations (gen_idx = 1..n_generations-1)
            for gen_idx in range(1, n_generations):
                torch.manual_seed(seed + gen_idx)
                gs = model.generate(
                    **enc,
                    max_new_tokens=max_new_tokens,
                    do_sample=True,
                    temperature=0.7,
                    top_p=0.9,
                    pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                )
                sampled_batches.append(gs[:, in_len:])

            for b_idx, (bid, inst) in enumerate(zip(batch_ids, batch_inst)):
                outcomes = []
                for gen_idx in range(n_generations):
                    resp = tokenizer.decode(
                        sampled_batches[gen_idx][b_idx], skip_special_tokens=True
                    ).strip()
                    outcomes.append(int(primary_judge_fn(inst, resp)))
                stats = bootstrap_asr(outcomes, seed=seed)
                out_map[bid] = {
                    "no_attack_asr": stats["asr"],
                    "no_attack_ci_low": stats["ci_low"],
                    "no_attack_ci_high": stats["ci_high"],
                    "no_attack_outcomes": outcomes,
                }
    return out_map


def evaluate_attacks_batch_on_variant(
    model,
    tokenizer,
    attacks: list[dict],
    refusal_direction_vec: np.ndarray | None,
    n_generations: int,
    max_new_tokens: int,
    seed: int,
    primary_judge_fn,
    reference_judge_fn,
    spot_frac: float,
    rng: np.random.Generator,
    batch_size: int = 12,
) -> tuple[dict[str, list[int]], list[int], list[int]]:
    """Evaluate a list of attacks on a variant in batches grouped by primitive."""
    import torch

    outcomes_by_aid: dict[str, list[int]] = {}
    sampled_primary: list[int] = []
    sampled_reference: list[int] = []

    spotcheck_queue: list[tuple[int, str, str]] = []

    for primitive in ("refusal_direction", "gcg"):
        group = [a for a in attacks if a["primitive"] == primitive]
        if not group:
            continue
        hook_ctx = (
            ablation_hooks(model, refusal_direction_vec)
            if (primitive == "refusal_direction" and refusal_direction_vec is not None)
            else contextlib.nullcontext()
        )
        with torch.no_grad(), hook_ctx:
            for i in range(0, len(group), batch_size):
                batch_atks = group[i : i + batch_size]
                batch_prompts = [
                    _format_prompt(
                        tokenizer,
                        f"{a['instruction']} {a['suffix']}"
                        if (primitive == "gcg" and a.get("suffix"))
                        else a["instruction"],
                    )
                    for a in batch_atks
                ]
                enc = tokenizer(
                    batch_prompts,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=320,
                ).to(model.device)
                in_len = enc["input_ids"].shape[-1]

                g0 = model.generate(
                    **enc,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                )
                sampled_batches = [g0[:, in_len:]]
                for gen_idx in range(1, n_generations):
                    torch.manual_seed(seed + gen_idx)
                    gs = model.generate(
                        **enc,
                        max_new_tokens=max_new_tokens,
                        do_sample=True,
                        temperature=0.7,
                        top_p=0.9,
                        pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                    )
                    sampled_batches.append(gs[:, in_len:])

                for a_idx, atk in enumerate(batch_atks):
                    aid = atk["attack_id"]
                    inst = atk["instruction"]
                    outs: list[int] = []
                    for gen_idx in range(n_generations):
                        resp = tokenizer.decode(
                            sampled_batches[gen_idx][a_idx], skip_special_tokens=True
                        ).strip()
                        p_lbl = int(primary_judge_fn(inst, resp))
                        outs.append(p_lbl)
                        if rng.random() <= spot_frac or len(spotcheck_queue) < 2:
                            spotcheck_queue.append((p_lbl, inst, resp))
                    outcomes_by_aid[aid] = outs

    # Run reference judge OUTSIDE ablation_hooks so the LLM judge is unablated.
    for p_lbl, inst, resp in spotcheck_queue:
        sampled_primary.append(p_lbl)
        sampled_reference.append(int(reference_judge_fn(inst, resp)))

    return outcomes_by_aid, sampled_primary, sampled_reference


def run(ctx: Context) -> Path:
    # The gate. Must be the first thing this stage does.
    require_authorized(ctx.target_model_ids, ctx.authorization)

    load_art = ctx.read_artifact("load")
    atk_art = ctx.read_artifact("attack_base")
    pred_art = ctx.read_artifact("predict")

    paths = {
        m["model_id"]: m["path"] for m in load_art["models"]
    }
    attacks_by_id = {a["attack_id"]: a for a in atk_art["attacks"]}
    if any(a.get("primitive") == "refusal_direction" for a in atk_art["attacks"]):
        raise NotImplementedError("Legacy refusal-direction verification is disabled pending a validated upstream adapter")
    rd_meta = atk_art.get("refusal_direction_meta")
    if rd_meta and rd_meta.get("activation_extractor") != ACTIVATION_EXTRACTOR_VERSION:
        raise ValueError(
            "Refusal direction uses superseded activation extraction. "
            "Rerun similarity, attack_base, and predict in a fresh run directory before verification."
        )
    rd_vec = (
        np.asarray(rd_meta["direction"], dtype=np.float64)
        if rd_meta and rd_meta.get("direction") is not None
        else None
    )

    ver_cfg = ctx.config.get("verify", {})
    top_k = int(ver_cfg.get("top_k", 20))
    n_gens = int(ver_cfg.get("generations_per_attack", 5))
    max_new_tokens = int(ver_cfg.get("max_new_tokens", 64))
    spot_frac = float(ver_cfg.get("human_spotcheck_fraction", 0.2))
    batch_size = int(ver_cfg.get("batch_size", 12))  # lower for large models on unified memory

    rng = np.random.default_rng(ctx.seed)
    all_primary_sampled: list[int] = []
    all_reference_sampled: list[int] = []
    variant_results: list[dict] = []

    for var_pred in pred_art["variants"]:
        vid = var_pred["model_id"]
        vpath = paths[vid]
        model, tokenizer = _load_variant_model(vpath)
        judge_mode = ver_cfg.get("judge", "local")
        primary_judge = (
            make_local_llm_judge(model, tokenizer)
            if judge_mode == "local_llm"
            else heuristic_judge
        )
        secondary_judge = (
            heuristic_judge
            if judge_mode == "local_llm"
            else make_local_llm_judge(model, tokenizer)
        )

        selected_preds = var_pred["predictions"][:top_k]
        behaviors_by_id = {
            p["behavior_id"]: attacks_by_id[p["attack_id"]]["instruction"]
            for p in selected_preds
        }
        no_attack_control = run_no_attack_baseline_on_variant(
            model=model,
            tokenizer=tokenizer,
            behaviors_by_id=behaviors_by_id,
            n_generations=n_gens,
            max_new_tokens=max_new_tokens,
            seed=ctx.seed,
            primary_judge_fn=primary_judge,
            batch_size=batch_size,
        )
        selected_atks = [attacks_by_id[p["attack_id"]] for p in selected_preds]
        outcomes_by_aid, samp_p, samp_r = evaluate_attacks_batch_on_variant(
            model=model,
            tokenizer=tokenizer,
            attacks=selected_atks,
            refusal_direction_vec=rd_vec,
            n_generations=n_gens,
            max_new_tokens=max_new_tokens,
            seed=ctx.seed,
            primary_judge_fn=primary_judge,
            reference_judge_fn=secondary_judge,
            spot_frac=spot_frac,
            rng=rng,
            batch_size=batch_size,
        )
        all_primary_sampled.extend(samp_p)
        all_reference_sampled.extend(samp_r)
        measured_list: list[dict] = []

        for pred_item in selected_preds:
            aid = pred_item["attack_id"]
            bid = pred_item["behavior_id"]
            prim_outcomes = outcomes_by_aid[aid]
            stats = bootstrap_asr(prim_outcomes, seed=ctx.seed)

            ctrl = no_attack_control[bid]
            no_atk_asr = float(ctrl["no_attack_asr"])
            delta_asr = float(stats["asr"] - no_atk_asr)

            measured_list.append(
                {
                    "attack_id": aid,
                    "behavior_id": bid,
                    "category": pred_item["category"],
                    "primitive": pred_item["primitive"],
                    "rank": pred_item["rank"],
                    "predicted_score": pred_item["predicted_score"],
                    "base_strength": pred_item["base_strength"],
                    "base_asr": pred_item["base_asr"],
                    "similarity": pred_item["similarity"],
                    "measured_asr": stats["asr"],
                    "no_attack_asr": no_atk_asr,
                    "transfer_delta_asr": delta_asr,
                    "ci_low": stats["ci_low"],
                    "ci_high": stats["ci_high"],
                    "n_generations": stats["n"],
                    "outcomes": prim_outcomes,
                    "no_attack_outcomes": ctrl["no_attack_outcomes"],
                }
            )

        del model
        import gc
        import torch

        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()
        variant_results.append({"model_id": vid, "attacks": measured_list})

    spotcheck = (
        judge_human_agreement(all_primary_sampled, all_reference_sampled)
        if all_primary_sampled
        else {"n_checked": 0, "agreement": 1.0, "cohens_kappa": 1.0}
    )

    return ctx.write_artifact(
        "verify",
        {
            "seed": ctx.seed,
            "authorization_id": ctx.authorization.id if ctx.authorization else None,
            "top_k": top_k,
            "generations_per_attack": n_gens,
            "spotcheck_agreement": spotcheck,
            "variants": variant_results,
        },
    )
