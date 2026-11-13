"""Stage 3: run wrapped attack primitives on the base against the benchmark.

Wraps published open-source implementations:
  - refusal-direction adapter (Arditi et al., 2024)
  - GCG adapter (Zou et al., 2023 via nanoGCG)

Per behavior, records a success flag on the base and a continuous strength
score. Never stores harmful model generation text in the artifact.
"""

from __future__ import annotations

from pathlib import Path

from lasa.attacks import gcg, refusal_direction
from lasa.benchmarks import load_benchmark, load_contrast_pairs
from lasa.context import Context
from lasa.authorization import require_authorized
from lasa.judge import heuristic_judge, make_local_llm_judge


def _load_base_model(model_path: str):
    import json
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "left"
    cfg_file = Path(model_path) / "config.json"
    mtype = json.loads(cfg_file.read_text()).get("model_type", "") if cfg_file.exists() else ""
    if mtype == "gemma2" and (torch.cuda.is_available() or torch.backends.mps.is_available()):
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


def run(ctx: Context) -> Path:
    require_authorized([ctx.base_model_id], ctx.authorization)
    primitives = ctx.config.get("attacks", {}).get("primitives", ["refusal_direction", "gcg"])
    if "refusal_direction" in primitives:
        raise NotImplementedError("Refusal-direction execution needs a verified upstream adapter; use lasa study for prompt-transfer experiments")
    if any(p != "gcg" for p in primitives) or not primitives:
        raise ValueError("only the published gcg adapter is supported for new attack runs")
    load = ctx.read_artifact("load")
    base_entry = next(m for m in load["models"] if m["role"] == "base")
    base_path = base_entry["path"]

    bench_cfg = ctx.config.get("benchmark", {})
    bench_name = bench_cfg.get("name", "jailbreakbench")
    limit = bench_cfg.get("limit")
    behaviors = load_benchmark(name=bench_name, limit=limit)

    atk_cfg = ctx.config.get("attacks", {})
    primitives = atk_cfg.get("primitives", ["refusal_direction", "gcg"])
    contrast_n = int(atk_cfg.get("contrast_pairs", 32))
    max_new_tokens = int(atk_cfg.get("max_new_tokens", 64))

    model, tokenizer = _load_base_model(base_path)
    judge_mode = ctx.config.get("verify", {}).get("judge", "local")
    judge_fn = make_local_llm_judge(model, tokenizer) if judge_mode == "local_llm" else heuristic_judge

    all_attacks: list[dict] = []
    tools_used: list[dict] = []
    refusal_meta: dict | None = None

    if "refusal_direction" in primitives:
        harmful_c, harmless_c = load_contrast_pairs(n_pairs=contrast_n)
        rd_out = refusal_direction.run_refusal_direction_on_base(
            model=model,
            tokenizer=tokenizer,
            behaviors=behaviors,
            harmful_contrast=harmful_c,
            harmless_contrast=harmless_c,
            judge_fn=judge_fn,
            max_new_tokens=max_new_tokens,
        )
        all_attacks.extend(rd_out["attacks"])
        tools_used.append(rd_out["tool"])
        refusal_meta = {
            "activation_extractor": rd_out["activation_extractor"],
            "selected_layer": rd_out["selected_layer"],
            "direction": rd_out["direction"],
        }

    if "gcg" in primitives:
        gcg_cfg = atk_cfg.get("gcg", {})
        gcg_out = gcg.run_gcg_on_base(
            model=model,
            tokenizer=tokenizer,
            behaviors=behaviors,
            judge_fn=judge_fn,
            num_steps=int(gcg_cfg.get("num_steps", 20)),
            search_width=int(gcg_cfg.get("search_width", 64)),
            topk=int(gcg_cfg.get("topk", 64)),
            seed=ctx.seed,
            max_new_tokens=max_new_tokens,
        )
        all_attacks.extend(gcg_out["attacks"])
        tools_used.append(gcg_out["tool"])

    del model

    return ctx.write_artifact(
        "attack_base",
        {
            "seed": ctx.seed,
            "base": ctx.base_model_id,
            "benchmark": {"name": bench_name, "n_behaviors": len(behaviors)},
            "tools": tools_used,
            "refusal_direction_meta": refusal_meta,
            "attacks": all_attacks,
        },
    )
