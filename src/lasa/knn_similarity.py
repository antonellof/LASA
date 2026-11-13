"""Mutual k-nearest-neighbour similarity, the metric Angell et al. (2025) use.

Angell et al. measure benign-prompt representational similarity between two
models as the mutual-kNN Jaccard overlap of their embeddings: the last-token
hidden state after block floor(0.8 L), on benign prompts. This module computes
that metric for a base and each variant so the paper can test whether the
CKA null result is a metric choice or a property of within-lineage comparison.

For probe i, N_k^X(i) is the set of the k probes whose embeddings are closest to
probe i under cosine similarity in model X (excluding i). The score is the mean
over probes of |N_k^X(i) & N_k^Y(i)| / |N_k^X(i) | N_k^Y(i)|. It is invariant to
any rotation and positive scaling of either embedding space, and it compares
neighbourhood structure rather than absolute geometry.

Forward passes on benign probes only. No generation, no harmful content.

Angell et al. use 10,000 Alpaca prompts with k = 100 (1% of the set). Our probe
set has 240 prompts, so we report k in {5, 10, 20} (2% to 8%) and treat k = 10 as
the primary value.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from lasa.context import load_context

K_VALUES = (5, 10, 20)
LAYER_FRAC = 0.8


def _neighbour_sets(X: np.ndarray, k: int) -> np.ndarray:
    """Indices of the k nearest neighbours of every row under cosine similarity, self excluded."""
    Xn = X / np.clip(np.linalg.norm(X, axis=1, keepdims=True), 1e-12, None)
    sim = Xn @ Xn.T
    np.fill_diagonal(sim, -np.inf)
    return np.argpartition(-sim, kth=k - 1, axis=1)[:, :k]


def mutual_knn_jaccard(X: np.ndarray, Y: np.ndarray, k: int) -> float:
    """Mean Jaccard overlap of each probe's k-NN set in X and in Y."""
    X = np.asarray(X, dtype=np.float64)
    Y = np.asarray(Y, dtype=np.float64)
    if X.shape[0] != Y.shape[0]:
        raise ValueError(f"probe counts differ: {X.shape[0]} vs {Y.shape[0]}")
    n = X.shape[0]
    if not 1 <= k < n:
        raise ValueError(f"k must be in [1, {n - 1}], got {k}")
    nx, ny = _neighbour_sets(X, k), _neighbour_sets(Y, k)
    scores = []
    for a, b in zip(nx, ny):
        sa, sb = set(a.tolist()), set(b.tolist())
        scores.append(len(sa & sb) / len(sa | sb))
    return float(np.mean(scores))


def collect_layer(model_path: str, probes: list[str], layer_frac: float = LAYER_FRAC, batch_size: int = 8) -> dict:
    """Last-token and mean-pooled hidden state after block floor(layer_frac * L)."""
    import gc

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"
    cfg = json.loads((Path(model_path) / "config.json").read_text())
    dtype = torch.bfloat16 if cfg.get("model_type") == "gemma2" else torch.float16
    dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForCausalLM.from_pretrained(model_path, torch_dtype=dtype, local_files_only=True, trust_remote_code=False, use_safetensors=True).to(dev).eval()
    n_layers = int(cfg.get("num_hidden_layers"))
    layer = int(math.floor(layer_frac * n_layers))  # hidden_states[k] is the output of block k

    last, mean = [], []
    with torch.no_grad():
        for i in range(0, len(probes), batch_size):
            batch = tok(probes[i : i + batch_size], return_tensors="pt", padding=True,
                        truncation=True, max_length=256).to(model.device)
            h = model(**batch, output_hidden_states=True).hidden_states[layer].float()
            mask = batch["attention_mask"]
            idx = mask.sum(dim=1) - 1
            last.append(h[torch.arange(h.shape[0]), idx].cpu().numpy())
            m = mask.unsqueeze(-1).float()
            mean.append(((h * m).sum(dim=1) / m.sum(dim=1).clamp(min=1.0)).cpu().numpy())
    del model
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    return {"layer": layer, "n_layers": n_layers,
            "last": np.concatenate(last), "mean": np.concatenate(mean)}


def run(config_path: str, results_dir: str = "experiments/results") -> Path:
    from lasa.pipeline.similarity import load_probes

    ctx = load_context(config_path)
    load_art = ctx.read_artifact("load")
    paths = {m["model_id"]: m["path"] for m in load_art["models"]}
    probes = load_probes(ctx.config["similarity"]["probe_set"])

    base = collect_layer(paths[ctx.base_model_id], probes)
    variants = []
    for vid in ctx.variant_model_ids:
        var = collect_layer(paths[vid], probes)
        scores = {
            f"{pool}_k{k}": mutual_knn_jaccard(base[pool], var[pool], k)
            for pool in ("last", "mean") for k in K_VALUES
        }
        variants.append({"model_id": vid, "layer_base": base["layer"], "layer_variant": var["layer"], **scores})
        print(f"[knn] {vid}: last_k10={scores['last_k10']:.4f} mean_k10={scores['mean_k10']:.4f}")

    out = Path(results_dir) / ctx.run_id / "knn_similarity.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "run_id": ctx.run_id, "base": ctx.base_model_id, "n_probes": len(probes),
        "layer_frac": LAYER_FRAC, "k_values": list(K_VALUES),
        "metric": "mutual kNN Jaccard, cosine neighbours (Angell et al., 2025)",
        "variants": variants,
    }, indent=2, sort_keys=True) + "\n")
    return out
