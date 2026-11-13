"""Stage 2: per-layer representational similarity between base and each variant.

Runs a fixed neutral probe set through base and each variant, pools hidden
states per probe (mean over non-pad tokens), and computes one similarity
profile per variant: a value per layer for each metric, plus a scalar summary
(the mean over layers).

Choices, documented here because they drive the result:
  - Pooling: mean over non-pad tokens, per probe, per layer.
  - Layers: all hidden-state outputs, including the embedding layer (index 0).
  - Metrics: linear CKA and cosine of probe-mean vectors. Both are reported.
  - Probe set: configs/neutral_probes.txt, benign prompts only.

The math lives in lasa.similarity_metrics and needs no torch. Torch is imported
only when hidden states are collected from a real checkpoint.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from lasa.attacks.refusal_direction import (
    ACTIVATION_EXTRACTOR_VERSION,
    collect_last_token_activations,
    refusal_cosine,
    select_best_layer,
)
from lasa.benchmarks import load_contrast_pairs
from lasa.context import Context
from lasa.similarity_metrics import METRICS


def load_probes(path: str | Path) -> list[str]:
    lines = [ln.strip() for ln in Path(path).read_text().splitlines()]
    probes = [ln for ln in lines if ln and not ln.startswith("#")]
    if len(probes) < 2:
        raise ValueError(f"probe set {path} has fewer than 2 prompts")
    return probes


def profile(
    base_acts: list[np.ndarray],
    variant_acts: list[np.ndarray],
    metrics: tuple[str, ...] = tuple(METRICS),
) -> dict:
    """Compare two per-layer activation lists.

    Each element of base_acts / variant_acts is an (n_probes, d_layer) array.
    Layer counts must match. Returns {metric: {"per_layer": [...], "mean": x}}.
    """
    if len(base_acts) != len(variant_acts):
        raise ValueError(
            f"layer counts differ: base {len(base_acts)} vs variant {len(variant_acts)}"
        )
    out: dict = {}
    for name in metrics:
        fn = METRICS[name]
        per_layer = [fn(b, v) for b, v in zip(base_acts, variant_acts)]
        out[name] = {
            "per_layer": per_layer,
            "mean": float(np.mean(per_layer)),
        }
    return out


def pool_hidden_states(hidden_states, attention_mask, skip_first: bool = False) -> list[np.ndarray]:
    """Mean-pool each layer over non-pad tokens. Inputs are torch tensors.

    hidden_states: tuple of (batch, seq, d) tensors, one per layer.
    attention_mask: (batch, seq) tensor, right-padded.
    skip_first: drop position 0. Under causal attention its state does not depend
        on the rest of the probe, and a BOS or attention-sink state can dominate the
        mean. Off by default so historical measurements reproduce.
    Returns a list of (batch, d) numpy arrays, one per layer.
    """
    import torch

    mask = attention_mask.to(torch.float32).unsqueeze(-1)
    if skip_first:
        mask = mask.clone()
        mask[:, 0] = 0.0
    pooled = []
    for h in hidden_states:
        h = h.to(torch.float32)
        summed = (h * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1.0)
        pooled.append((summed / counts).cpu().numpy())
    return pooled


def collect_activations(model_path: str, probes: list[str], batch_size: int = 8,
                        skip_first: bool = False) -> list[np.ndarray]:
    """Run probes through a local checkpoint and return per-layer pooled activations."""
    import json
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"
    cfg_file = Path(model_path) / "config.json"
    mtype = json.loads(cfg_file.read_text()).get("model_type", "") if cfg_file.exists() else ""
    dtype = torch.bfloat16 if mtype == "gemma2" else torch.float16
    dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForCausalLM.from_pretrained(
        model_path, torch_dtype=dtype, local_files_only=True, trust_remote_code=False, use_safetensors=True
    ).to(dev).eval()

    per_layer: list[list[np.ndarray]] | None = None
    with torch.no_grad():
        for i in range(0, len(probes), batch_size):
            batch = tok(
                probes[i : i + batch_size],
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=256,
            ).to(model.device)
            out = model(**batch, output_hidden_states=True)
            pooled = pool_hidden_states(out.hidden_states, batch["attention_mask"], skip_first=skip_first)
            if per_layer is None:
                per_layer = [[p] for p in pooled]
            else:
                for acc, p in zip(per_layer, pooled):
                    acc.append(p)

    del model
    import gc

    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    elif torch.cuda.is_available():
        torch.cuda.empty_cache()
    return [np.concatenate(chunks, axis=0) for chunks in per_layer]


def compute_refusal_subspace_alignment(
    base_path: str,
    variant_paths: dict[str, str],
    n_pairs: int = 16,
) -> dict[str, dict[str, float]]:
    """Measure cosine alignment between the base refusal direction and each variant's refusal direction."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    harmful_c, harmless_c = load_contrast_pairs(n_pairs=n_pairs)

    def _get_acts(mpath: str):
        import json

        tok = AutoTokenizer.from_pretrained(mpath, local_files_only=True, trust_remote_code=False)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        tok.padding_side = "left"
        cfg_file = Path(mpath) / "config.json"
        mtype = json.loads(cfg_file.read_text()).get("model_type", "") if cfg_file.exists() else ""
        dtype = torch.bfloat16 if mtype == "gemma2" else torch.float16
        dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
        m = AutoModelForCausalLM.from_pretrained(
            mpath, torch_dtype=dtype, local_files_only=True, trust_remote_code=False, use_safetensors=True
        ).to(dev).eval()
        ha = collect_last_token_activations(m, tok, harmful_c)
        sa = collect_last_token_activations(m, tok, harmless_c)
        del m
        return ha, sa

    base_ha, base_sa = _get_acts(base_path)
    best_layer, base_dir = select_best_layer(base_ha, base_sa)

    out: dict[str, dict[str, float]] = {}
    for vid, vpath in variant_paths.items():
        var_ha, var_sa = _get_acts(vpath)
        rcos = refusal_cosine(base_dir, var_ha, var_sa, best_layer)
        # Also measure variant's own separation margin along base_dir relative to base's margin
        base_gap = max(
            1e-6,
            float((base_ha[best_layer] @ base_dir).mean() - (base_sa[best_layer] @ base_dir).mean()),
        )
        var_gap = float(
            (var_ha[best_layer] @ base_dir).mean() - (var_sa[best_layer] @ base_dir).mean()
        )
        retention = float(np.clip(var_gap / base_gap, 0.0, 1.5))
        out[vid] = {
            "activation_extractor": ACTIVATION_EXTRACTOR_VERSION,
            "contrast_pairs": n_pairs,
            "refusal_layer": int(best_layer),
            "refusal_direction_cosine": float(rcos),
            "refusal_margin_retention": float(retention),
        }
    return out


def run(ctx: Context) -> Path:
    load = ctx.read_artifact("load")
    paths = {m["role"] if m["role"] == "base" else m["model_id"]: m["path"] for m in load["models"]}
    probes = load_probes(ctx.config["similarity"]["probe_set"])
    metrics = tuple(ctx.config["similarity"].get("metrics", list(METRICS)))
    # ADR-0018: new measurements should exclude position 0; the default reproduces history.
    skip_first = bool(ctx.config["similarity"].get("skip_first_position", False))

    base_acts = collect_activations(paths["base"], probes, skip_first=skip_first)
    var_paths = {v: paths[v] for v in ctx.variant_model_ids}
    contrast_n = int(ctx.config.get("attacks", {}).get("contrast_pairs", 64))
    refusal_align = (
        compute_refusal_subspace_alignment(paths["base"], var_paths, n_pairs=contrast_n)
        if ctx.config["similarity"].get("compute_refusal_alignment", True)
        else {}
    )
    variants = []
    for v in ctx.variant_model_ids:
        var_acts = collect_activations(paths[v], probes, skip_first=skip_first)
        prof = profile(base_acts, var_acts, metrics)
        if v in refusal_align:
            prof["refusal_subspace"] = refusal_align[v]
        variants.append({"model_id": v, "profile": prof})

    return ctx.write_artifact(
        "similarity",
        {
            "seed": ctx.seed,
            "n_probes": len(probes),
            "pooling": "mean without position 0" if skip_first else "mean over all tokens",
            "metrics": list(metrics),
            "base": ctx.base_model_id,
            "variants": variants,
        },
    )
