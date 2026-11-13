"""Run study stages inside a Hugging Face Job (ADR-0022).

The job clones the public repository at one commit, installs the locked
environment, and calls the same `lasa` commands an operator runs locally, so
the authorization gate and the frozen-plan checks apply unchanged.

Raw evidence (suffixes, completions) never enters git. It is synced to one
private dataset on the operator's account so a later job, or the operator's
machine, can resume. Delete that dataset after pulling the evidence.

Stages:
  calibrate  time a benign GCG search and a benign generation on the source
  init       fetch checkpoints, freeze the plan, upload plan and commitment
  run        search, generate, judge, audit sample, analyze; resumable
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

SKIP = [".study.lock", "*.tmp"]


def lasa(*args) -> None:
    print("+ lasa", *args, flush=True)
    subprocess.run([sys.executable, "-m", "lasa.cli", *args], check=True)


def fetch(cfg, entries) -> None:
    """Download only the checkpoints the next stage loads, with the patterns `lasa fetch` uses."""
    from huggingface_hub import snapshot_download
    from lasa.pipeline.load import resolve_local_path
    for entry in entries:
        local = resolve_local_path(entry, Path(cfg.get("models_dir", "models")))
        print(f"fetching {entry['model_id']} -> {local}", flush=True)
        snapshot_download(repo_id=entry["model_id"], local_dir=str(local), revision=entry.get("revision"),
                          allow_patterns=["*.safetensors", "*.json", "tokenizer.model", "*.txt", "*.jinja"])


def push(api, repo, out: Path, study_id: str) -> None:
    api.upload_folder(repo_id=repo, repo_type="dataset", folder_path=str(out),
                      path_in_repo=study_id, ignore_patterns=SKIP,
                      commit_message=f"sync {study_id}")


def pull(repo, out: Path, study_id: str) -> None:
    """Restore earlier evidence for this study, with owner-only modes."""
    from huggingface_hub import snapshot_download
    tmp = Path(snapshot_download(repo_id=repo, repo_type="dataset", allow_patterns=[f"{study_id}/*"]))
    src = tmp / study_id
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    for f in (sorted(src.iterdir()) if src.is_dir() else []):
        if f.is_file():
            shutil.copyfile(f, out / f.name)
            (out / f.name).chmod(0o600)
            print(f"restored {f.name}", flush=True)


def calibrate(cfg) -> None:
    """Benign timing only: no benchmark behavior, no stored output."""
    import nanogcg
    import torch
    from lasa.pipeline.load import resolve_local_path
    from lasa.pipeline.verify import _load_variant_model
    from lasa.study import prompt_ids, decoding_config
    print(subprocess.run(["df", "-h", "."], capture_output=True, text=True).stdout, flush=True)
    path = resolve_local_path(cfg["base"], Path(cfg.get("models_dir", "models")))
    model, tok = _load_variant_model(str(path), cfg.get("torch_dtype"))
    steps = 10
    settings = {**cfg["gcg"], "num_steps": steps, "seed": 0}
    started = time.perf_counter()
    nanogcg.run(model, tok, [{"role": "user", "content": "Describe a quiet garden in spring."}],
                "Sure, here is a description of a quiet garden", nanogcg.GCGConfig(**settings))
    step_seconds = (time.perf_counter() - started) / steps
    ids = torch.tensor([prompt_ids(tok, "Write a long essay about the history of rivers.")["ids"]],
                       device=model.device)
    n, budget = int(cfg["generations"]), int(cfg["max_new_tokens"])
    started = time.perf_counter()
    with torch.no_grad():
        out = model.generate(input_ids=ids, attention_mask=torch.ones_like(ids),
                             generation_config=decoding_config(model, tok, budget),
                             num_return_sequences=n if cfg.get("batch_replicates") else 1)
    cell_seconds = time.perf_counter() - started
    behaviors = 10 * int(cfg["benchmark"]["per_category"])  # JailbreakBench has ten categories
    checkpoints = 1 + len(cfg["variants"])
    cells = checkpoints * behaviors * (4 if cfg.get("controls", {}).get("mismatched_suffix") else 3)
    calls = cells if cfg.get("batch_replicates") else cells * n
    print(json.dumps({
        "device": str(model.device), "dtype": str(model.dtype),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gcg_seconds_per_step": round(step_seconds, 3),
        "search_hours_estimate": round(step_seconds * cfg["gcg"]["num_steps"] * behaviors / 3600, 2),
        "generate_call_seconds_at_full_budget": round(cell_seconds, 2),
        "generated_tokens": int(out.shape[0] * (out.shape[1] - ids.shape[1])),
        "generation_hours_upper_bound": round(cell_seconds * calls / 3600, 2),
    }, indent=2), flush=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--evidence-repo", help="private dataset id on the operator's account")
    p.add_argument("--stage", required=True, choices=["calibrate", "init", "run"])
    p.add_argument("--sync-seconds", type=int, default=600)
    args = p.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    out, study_id = Path(cfg["output_dir"]), cfg["study_id"]

    from lasa.study import context
    context(args.config)  # authorization gate before any download or model load
    needs = {"search": [cfg["base"]], "generate": cfg["variants"], "judge": [cfg["independent_judge"]]}
    if args.stage == "calibrate":
        fetch(cfg, needs["search"])
        calibrate(cfg)
        return 0

    from huggingface_hub import HfApi
    api = HfApi()
    if api.repo_info(args.evidence_repo, repo_type="dataset").private is not True:
        raise SystemExit("stop: the evidence dataset must be private")
    pull(args.evidence_repo, out, study_id)

    if args.stage == "init":
        fetch(cfg, [cfg["base"], *cfg["variants"], cfg["independent_judge"]])  # the plan hashes all of them
        lasa("study", "initialize", "--config", args.config)
        commitment = Path(cfg.get("commitment_dir", f"experiments/results/{study_id}")) / "plan_commitment.json"
        shutil.copyfile(commitment, out / "plan_commitment.json")
        push(api, args.evidence_repo, out, study_id)
        print("plan frozen. Pull it, commit and push plan_commitment.json, then launch the run stage.")
        return 0

    stop = threading.Event()

    def periodic():
        while not stop.wait(args.sync_seconds):
            try:
                push(api, args.evidence_repo, out, study_id)
            except Exception as exc:  # a failed sync must not kill a paid search
                print(f"sync failed, will retry: {exc}", flush=True)

    worker = threading.Thread(target=periodic, daemon=True)
    worker.start()
    try:
        for stage in ("search", "generate", "judge"):
            fetch(cfg, needs[stage])  # a stage waits only for the checkpoints it loads
            lasa("study", stage, "--config", args.config)
        if not (out / "audit_items.json").exists():
            lasa("study", "audit", "--config", args.config)
        lasa("study", "analyze", "--config", args.config)
    finally:
        stop.set()
        worker.join()
        push(api, args.evidence_repo, out, study_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
