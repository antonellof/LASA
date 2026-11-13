"""Check whether benign-probe CKA is driven by the first token position.

Reruns the historical benign-probe measurement for source-target pairs that are
available locally, pooling each probe twice: over all tokens (the historical
method) and without position 0. Benign probes only; no harmful content and no
attack outcomes. Run:

    python -m lasa.cka_diagnostic --out experiments/results/cka_first_position_diagnostic.json
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path

import numpy as np

from lasa.pipeline.load import resolve_local_path
from lasa.pipeline.similarity import load_probes, pool_hidden_states
from lasa.similarity_metrics import linear_cka

PAIRS = {
    "qwen25-1.5b": ("Qwen/Qwen2.5-1.5B-Instruct",
                    ["Qwen/Qwen2.5-Coder-1.5B-Instruct", "SakanaAI/TinySwallow-1.5B-Instruct"]),
    "llama32-1b": ("unsloth/Llama-3.2-1B-Instruct", ["cognitivecomputations/Dolphin3.0-Llama3.2-1B"]),
}


def activations(path: Path, probes: list[str], batch_size: int = 8):
    """Per-layer pooled states (all tokens, without position 0) and position-0 share."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"
    dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForCausalLM.from_pretrained(path, torch_dtype=torch.float16, local_files_only=True,
                                                 trust_remote_code=False, use_safetensors=True).to(dev).eval()
    full, skipped, share, first_is_bos = None, None, None, []
    with torch.no_grad():
        for i in range(0, len(probes), batch_size):
            batch = tok(probes[i:i + batch_size], return_tensors="pt", padding=True,
                        truncation=True, max_length=256).to(model.device)
            hidden = model(**batch, output_hidden_states=True).hidden_states
            a = pool_hidden_states(hidden, batch["attention_mask"])
            b = pool_hidden_states(hidden, batch["attention_mask"], skip_first=True)
            counts = batch["attention_mask"].sum(dim=1, keepdim=True).float()
            # Norm of position 0's contribution to the mean, relative to the pooled norm.
            s = [((h[:, 0].float() / counts).norm(dim=-1) / torch.from_numpy(p).to(h.device).norm(dim=-1)).cpu().numpy()
                 for h, p in zip(hidden, a)]
            first_is_bos += (batch["input_ids"][:, 0] == tok.bos_token_id).tolist() if tok.bos_token_id is not None else [False] * len(a[0])
            if full is None:
                full, skipped, share = [[x] for x in a], [[x] for x in b], [[x] for x in s]
            else:
                for acc, x in zip(full, a):
                    acc.append(x)
                for acc, x in zip(skipped, b):
                    acc.append(x)
                for acc, x in zip(share, s):
                    acc.append(x)
    del model
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    def stack(layers):
        return [np.concatenate(x, axis=0) for x in layers]

    return stack(full), stack(skipped), [float(np.concatenate(x).mean()) for x in share], bool(all(first_is_bos))


def run(models_dir: Path, probe_file: Path, results_dir: Path) -> dict:
    probes = load_probes(probe_file)
    payload = {"n_probes": len(probes), "dtype": "float16", "batch_size": 8, "pairs": [],
               "interpretation": ("Descriptive diagnostic of the historical CKA method. "
                                  "No attack outcomes or transfer claims.")}
    for group, (source, targets) in PAIRS.items():
        src_full, src_skip, src_share, src_bos = activations(resolve_local_path({"model_id": source}, models_dir), probes)
        historical = {v["model_id"]: v["profile"]["cka"]["mean"]
                      for v in json.loads((results_dir / group / "similarity.json").read_text())["variants"]}
        for target in targets:
            tgt_full, tgt_skip, tgt_share, tgt_bos = activations(resolve_local_path({"model_id": target}, models_dir), probes)
            all_tokens = [linear_cka(x, y) for x, y in zip(src_full, tgt_full)]
            without_first = [linear_cka(x, y) for x, y in zip(src_skip, tgt_skip)]
            payload["pairs"].append({
                "group": group, "source": source, "target": target,
                "first_token_is_bos": {"source": src_bos, "target": tgt_bos},
                "cka_all_tokens": {"per_layer": all_tokens, "mean": float(np.mean(all_tokens))},
                "cka_without_position_0": {"per_layer": without_first, "mean": float(np.mean(without_first))},
                "historical_mean": historical.get(target),
                "position_0_norm_share_source": {"per_layer": src_share, "mean_over_layers": float(np.mean(src_share))},
            })
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--models-dir", default="models")
    parser.add_argument("--probes", default="configs/neutral_probes.txt")
    parser.add_argument("--results-dir", default="experiments/results")
    args = parser.parse_args(argv)
    payload = run(Path(args.models_dir), Path(args.probes), Path(args.results_dir))
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    for p in payload["pairs"]:
        print(f"{p['target']}: all={p['cka_all_tokens']['mean']:.4f} skip0={p['cka_without_position_0']['mean']:.4f} "
              f"historical={p['historical_mean']} bos={p['first_token_is_bos']} pos0share={p['position_0_norm_share_source']['mean_over_layers']:.3f}")


if __name__ == "__main__":
    main()
