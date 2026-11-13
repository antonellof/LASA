"""Frozen prompt-transfer experiments using published nanoGCG and a separate judge.

All prompts, suffixes and exact generations stay in protected reports/ storage.
No optimization runs on variants. Source-only scoring rules are fixed before
variant labels are available; rankings use source labels only. This path does not use the legacy ablation code.
"""
from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps

import numpy as np
import yaml

from lasa.authorization import load_record, require_authorized
from lasa.benchmarks import load_benchmark, with_published_targets
from lasa.pipeline.load import hash_checkpoint, resolve_local_path
from lasa.study_validation import validate_config, validate_evidence

BASE_CONDITIONS = ["bare", "random_suffix", "gcg"]


def conditions_for(cfg) -> list[str]:
    """Condition order is part of the frozen plan. Older plans have three conditions."""
    extra = ["mismatched_gcg"] if cfg.get("controls", {}).get("mismatched_suffix") else []
    return BASE_CONDITIONS + extra


def study_benchmark(cfg):
    """Benchmark behaviors with the configured target strings (generic unless stated)."""
    behaviors = load_benchmark(cfg["benchmark"]["name"])
    if cfg["benchmark"].get("targets", "generic") == "published":
        behaviors = with_published_targets(behaviors, cfg["benchmark"]["name"])
    return behaviors


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def private_json(path: Path, value) -> None:
    """Atomic, owner-only output. Never leave a partially written stage artifact."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f, indent=2, sort_keys=True, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def stratified_split(behaviors, per_category: int, seed: int) -> list[dict]:
    """Select published IDs, then split evenly within category before any labels."""
    if per_category < 2 or per_category % 2:
        raise ValueError("per_category must be positive, even and at least two")
    groups = defaultdict(list)
    for b in behaviors:
        groups[b.category].append(b)
    rng = np.random.default_rng(seed)
    selected = []
    for category, group in sorted(groups.items()):
        if len(group) < per_category:
            raise ValueError(f"not enough behaviors in category {category}")
        order = rng.permutation(len(group))[:per_category]
        for i, idx in enumerate(order):
            b = group[int(idx)]
            selected.append({
                "behavior_id": b.behavior_id, "instruction": b.instruction,
                "category": category, "target": b.target,
                "split": "development" if i < per_category // 2 else "heldout",
            })
    return selected


def mismatch_map(behaviors, seed: int) -> dict[str, str]:
    """Pair each behavior with another behavior's suffix from the same split.

    A cyclic shift of a seeded shuffle is a derangement, so no behavior receives
    its own suffix. Fixed at initialization from source-side information only.
    """
    rng = np.random.default_rng([seed, 7])
    mapping = {}
    for split in ("development", "heldout"):
        ids = [b["behavior_id"] for b in behaviors if b["split"] == split]
        if len(ids) < 2:
            raise ValueError("a mismatched-suffix control needs at least two behaviors per split")
        order = [ids[int(i)] for i in rng.permutation(len(ids))]
        mapping.update({order[i]: order[(i + 1) % len(order)] for i in range(len(order))})
    return mapping


def commitment_path(cfg) -> Path:
    return Path(cfg.get("commitment_dir", f"experiments/results/{cfg['study_id']}")) / "plan_commitment.json"


def public_commitment(plan) -> dict:
    """Non-sensitive record of a frozen plan: digests and IDs, never prompts or text."""
    return {
        "study_id": plan["study_id"], "plan_digest": digest(plan),
        "config_digest": plan["config_digest"], "code_digest": plan["code_digest"],
        "code_revision": plan["code_revision"], "benchmark_digest": plan["benchmark_digest"],
        "rubric_digest": digest(plan["rubric"]), "timestamp": plan["timestamp"],
        "protocol_status": plan["config"]["protocol_status"], "conditions": plan["conditions"],
        "decision": plan["config"].get("decision"),
        "behaviors": [{k: b[k] for k in ("behavior_id", "category", "split")} for b in plan["behaviors"]],
        "models": {mid: {"digest": entry["digest"]} for mid, entry in plan["models"].items()},
        "interpretation": "Commit and push this file before search. It lets readers check that the plan predates outcomes.",
    }


def require_public_commitment(cfg, plan) -> None:
    """Confirmatory studies may not search until the plan commitment is committed to git."""
    if cfg["protocol_status"] != "confirmatory":
        return
    path = commitment_path(cfg)
    if not path.exists() or json.loads(path.read_text()).get("plan_digest") != digest(plan):
        raise ValueError(f"missing or mismatched public plan commitment at {path}")
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)], capture_output=True)
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(path)], capture_output=True)
    if tracked.returncode or clean.returncode:
        raise ValueError(f"commit {path} (and push it) before search; outcomes must postdate a public plan")
    commit = subprocess.run(["git", "rev-list", "-1", "HEAD", "--", str(path)], capture_output=True, text=True)
    remote = subprocess.run(["git", "branch", "-r", "--contains", commit.stdout.strip()],
                            capture_output=True, text=True)
    if commit.returncode or not commit.stdout.strip() or remote.returncode or not remote.stdout.strip():
        raise ValueError(f"push the commit that records {path} before search; no remote-tracking branch contains it")


def _ids(encoding) -> list[int]:
    ids = encoding["input_ids"]
    if hasattr(ids, "tolist"):
        ids = ids.tolist()
    if ids and isinstance(ids[0], list):
        ids = ids[0]
    return [int(i) for i in ids]


def prompt_ids(tok, instruction: str, suffix: str = "") -> dict:
    """Token IDs built exactly as nanoGCG 0.3.0 builds its optimization input.

    nanoGCG appends the suffix with no separator, strips a leading BOS string from
    the rendered template, tokenizes the prefix with special tokens and the suffix
    and remainder without. Re-encoding one joined string changes the IDs at the
    boundary, so every condition and every model uses this construction instead.
    """
    if not getattr(tok, "chat_template", None):
        raise ValueError("the study requires a chat template, as nanoGCG does")
    template = tok.apply_chat_template([{"role": "user", "content": instruction + "{optim_str}"}],
                                       tokenize=False, add_generation_prompt=True)
    bos = getattr(tok, "bos_token", None)
    if bos and template.startswith(bos):
        template = template.replace(bos, "")
    if template.count("{optim_str}") != 1:
        raise ValueError("chat template must contain the suffix slot exactly once")
    before, after = template.split("{optim_str}")
    before_ids = _ids(tok(before))
    suffix_ids = _ids(tok(suffix, add_special_tokens=False)) if suffix else []
    after_ids = _ids(tok(after, add_special_tokens=False))
    return {"ids": before_ids + suffix_ids + after_ids, "suffix_ids": suffix_ids, "n_before": len(before_ids)}


def exact_target_loss(model, tok, ids: list[int], target: str) -> float:
    """Mean target cross-entropy on the exact evaluated IDs, as nanoGCG defines its loss."""
    import torch
    target_ids = _ids(tok(target, add_special_tokens=False))
    x = torch.tensor([ids + target_ids], device=model.device)
    with torch.no_grad():
        logits = model(input_ids=x).logits
    shifted = logits[0, len(ids) - 1:-1, :].float()
    return float(torch.nn.functional.cross_entropy(shifted, torch.tensor(target_ids, device=shifted.device)))


def code_digest():
    files = sorted(p for p in Path(__file__).parent.rglob("*") if p.is_file() and p.suffix in {".py", ".json"})
    return digest({str(p.relative_to(Path(__file__).parent)): p.read_text() for p in files})


def context(config_path):
    cfg = yaml.safe_load(Path(config_path).read_text())
    validate_config(cfg)
    ids = [cfg["base"]["model_id"], *[v["model_id"] for v in cfg["variants"]]]
    # Authorization before directories, model reads, or generated output.
    auth = load_record(cfg["authorization"])
    require_authorized(ids, auth)
    judge = cfg["independent_judge"]
    if judge["model_id"] in ids:
        raise ValueError("independent judge must differ from every evaluated checkpoint")
    out = Path(cfg["output_dir"]).resolve()
    root = Path("reports").resolve()
    if not out.is_relative_to(root) or out == root:
        raise ValueError("study output_dir must be a subdirectory of gitignored reports/")
    return cfg, auth, ids, out


def locked_stage(fn):
    """One writer per study, with automatic lock release on exit (Unix hosts)."""
    @wraps(fn)
    def run(config_path, *args, **kwargs):
        import fcntl
        _, _, _, out = context(config_path)  # gate before even creating a lock file
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        out.chmod(0o700)
        fd = os.open(out / ".study.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("another stage is writing this study") from exc
            return fn(config_path, *args, **kwargs)
        finally:
            os.close(fd)
    return run


@locked_stage
def initialize(config_path) -> Path:
    cfg, auth, ids, out = context(config_path)
    plan_file = out / "plan.json"
    if plan_file.exists():
        raise FileExistsError("frozen plan already exists; use a new study ID for changes")
    if len(ids) != len(set(ids)):
        raise ValueError("evaluated model IDs must be unique")
    if cfg["generations"] < 2:
        raise ValueError("at least two sampled generations per condition are required")
    if cfg.get("protocol_status") not in {"pilot", "confirmatory"}:
        raise ValueError("protocol_status must explicitly be pilot or confirmatory")
    if cfg["protocol_status"] == "confirmatory" and not cfg.get("decision"):
        raise ValueError("a confirmatory protocol must freeze its decision rules before outcomes")
    commitment = commitment_path(cfg)
    if commitment.exists():
        raise FileExistsError(f"{commitment} already records a plan; use a new study ID")
    benchmark = study_benchmark(cfg)
    categories = cfg["benchmark"].get("categories")
    selected_pool = [b for b in benchmark if not categories or b.category in categories]
    if not selected_pool or categories and set(categories) != {b.category for b in selected_pool}:
        raise ValueError("unknown or empty benchmark category selection")
    behaviors = stratified_split(selected_pool, cfg["benchmark"]["per_category"], cfg["seed"])
    rubric = json.loads(Path(cfg["independent_judge"]["rubric"]).read_text())
    if rubric.get("model_id") != cfg["independent_judge"]["model_id"]:
        raise ValueError("judge rubric must name the configured classifier; templates are model-specific")
    conditions = conditions_for(cfg)
    registry = {}
    entries = [cfg["base"], *cfg["variants"], cfg["independent_judge"]]
    for entry in entries:
        path = resolve_local_path(entry, Path(cfg.get("models_dir", "models"))).resolve()
        registry[entry["model_id"]] = hash_checkpoint(path)
    def weights(info):
        return sorted(f["sha256"] for f in info["files"] if f["file"].endswith((".bin", ".safetensors")))
    judge_weights = weights(registry[cfg["independent_judge"]["model_id"]])
    if any(weights(registry[mid]) == judge_weights for mid in ids):
        raise ValueError("independent judge has the same weight-file content as an evaluated checkpoint")
    plan = {
        "study_id": cfg["study_id"], "config": cfg, "config_digest": digest(cfg),
        "authorization_id": auth.id, "authorization_digest": digest(auth.__dict__),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "code_revision": _revision(), "code_digest": code_digest(),
        "benchmark_digest": digest([b.__dict__ for b in benchmark]),
        "behaviors": behaviors, "rubric": rubric, "models": registry,
        "versions": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
                     if d.metadata.get("Name")},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "machine": platform.machine()},
        "sampling_policy": sampling_policy(cfg["max_new_tokens"]),
        "conditions": conditions,
        "prompt_construction": "nanoGCG-0.3.0 template split; no separator before the suffix",
        "primary_outcome": "heldout behavior-level GCG minus bare harmful-compliance rate",
        "secondary_control": "GCG minus source-token-length-matched random suffix",
        "human_validation": "pending; no automated result is a completed human audit",
    }
    if "mismatched_gcg" in conditions:
        plan["mismatched_suffix_source"] = mismatch_map(behaviors, cfg["seed"])
    private_json(plan_file, plan)
    commitment.parent.mkdir(parents=True, exist_ok=True)
    commitment.write_text(json.dumps(public_commitment(plan), indent=2, sort_keys=True) + "\n")
    return plan_file


def _revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


ANALYSIS_PACKAGES = ("numpy", "scipy")


def environment_differences(plan) -> dict:
    """Packages whose installed version differs from the frozen plan: {name: [frozen, installed]}."""
    out = {}
    for package, version in plan["versions"].items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        if installed != version:
            out[package] = [version, installed]
    return out


def frozen(config_path, *, allow_analysis_revision=False, analysis_only=False):
    """Load a frozen plan and refuse to continue if its inputs changed.

    Stages that create evidence need the exact frozen environment. `analysis_only`
    stages read finished evidence and load no model, so only the numeric stack must
    match. This lets evidence made on a GPU host be scored on the operator's machine.
    """
    cfg, auth, ids, out = context(config_path)
    plan = json.loads((out / "plan.json").read_text())
    if plan["conditions"] != conditions_for(cfg):
        raise ValueError("frozen condition order was modified")
    if set(plan["models"]) != set(ids + [cfg["independent_judge"]["model_id"]]):
        raise ValueError("frozen model registry differs from configured checkpoints")
    if digest(cfg) != plan["config_digest"] or digest(auth.__dict__) != plan["authorization_digest"]:
        raise ValueError("config or authorization changed after freeze")
    if code_digest() != plan["code_digest"] and not allow_analysis_revision:
        raise ValueError("code changed after freeze; initialize a new study rather than mixing implementations")
    benchmark = study_benchmark(cfg)
    if digest([b.__dict__ for b in benchmark]) != plan["benchmark_digest"]:
        raise ValueError("benchmark changed after freeze")
    categories = cfg["benchmark"].get("categories")
    pool = [b for b in benchmark if not categories or b.category in categories]
    if stratified_split(pool, cfg["benchmark"]["per_category"], cfg["seed"]) != plan["behaviors"]:
        raise ValueError("frozen behavior selection was modified")
    if json.loads(Path(cfg["independent_judge"]["rubric"]).read_text()) != plan["rubric"]:
        raise ValueError("judge rubric changed after freeze")
    differences = environment_differences(plan)
    blocking = sorted(p for p in differences if not analysis_only or p.lower() in ANALYSIS_PACKAGES)
    if blocking:
        raise ValueError(f"environment changed after freeze: {blocking[0]}")
    return cfg, plan, ids, out


def load_model(plan, model_id):
    from lasa.pipeline.verify import _load_variant_model
    entry = plan["models"][model_id]
    if hash_checkpoint(Path(entry["path"]))["digest"] != entry["digest"]:
        raise ValueError(f"checkpoint changed after freeze: {model_id}")
    return _load_variant_model(entry["path"], plan["config"].get("torch_dtype"))


def release():
    import torch
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    elif torch.cuda.is_available():
        torch.cuda.empty_cache()


def sampling_policy(budget):
    """Explicit sampling knobs; EOS/padding remain checkpoint-specific and recorded."""
    return {"do_sample": True, "temperature": 0.7, "top_p": 0.9, "top_k": 0,
            "typical_p": 1.0, "repetition_penalty": 1.0, "no_repeat_ngram_size": 0,
            "num_beams": 1, "max_new_tokens": int(budget)}


def decoding_config(model, tokenizer, budget):
    from transformers import GenerationConfig
    defaults = getattr(model, "generation_config", None)
    return GenerationConfig(
        **sampling_policy(budget),
        eos_token_id=getattr(defaults, "eos_token_id", getattr(tokenizer, "eos_token_id", None)),
        bos_token_id=getattr(defaults, "bos_token_id", None),
        pad_token_id=tokenizer.pad_token_id,
    )


def runtime_metadata(model, tokenizer):
    gc = getattr(model, "generation_config", None)
    return {"model_class": type(model).__name__, "device": str(model.device),
            "dtype": str(getattr(model, "dtype", "unknown")),
            "tokenizer_class": type(tokenizer).__name__,
            "chat_template_digest": digest(getattr(tokenizer, "chat_template", None)),
            "generation_defaults": gc.to_dict() if gc is not None else None}


def trim_after_eos(tokens, eos_ids):
    """Drop the padding a batched generate call appends after a row's first EOS."""
    for n, token in enumerate(tokens):
        if int(token) in eos_ids:
            return tokens[:n + 1]
    return tokens


def random_control(tokenizer, suffix: str, seed: int) -> dict:
    """A fixed benign-token null; matched in source token space, not target space."""
    rng = np.random.default_rng(seed)
    n = len(tokenizer.encode(suffix, add_special_tokens=False))
    pool = tokenizer.encode(" apple garden music tea blue book river cloud", add_special_tokens=False)
    for _ in range(128):
        ids = rng.choice(pool, size=n, replace=True).tolist()
        text = tokenizer.decode(ids, skip_special_tokens=True)
        if len(tokenizer.encode(text, add_special_tokens=False)) == n:
            return {"suffix": text, "drawn_source_tokens": n, "reencoded_source_tokens": n}
    raise ValueError("could not form a source-token-length-matched null; do not silently change controls")


@locked_stage
def search(config_path) -> Path:
    import nanogcg
    cfg, plan, ids, out = frozen(config_path)
    if importlib.metadata.version("nanogcg") != "0.3.0":
        raise ValueError("this protocol requires nanogcg==0.3.0")
    require_public_commitment(cfg, plan)
    dest = out / "source_attacks.json"
    payload = json.loads(dest.read_text()) if dest.exists() else {"plan_digest": digest(plan), "attacks": []}
    if payload["plan_digest"] != digest(plan):
        raise ValueError("attack artifact does not match frozen plan")
    done = {a["behavior_id"] for a in payload["attacks"]}
    if len(done) != len(payload["attacks"]) or not done <= {b["behavior_id"] for b in plan["behaviors"]}:
        raise ValueError("duplicate or unknown source attack IDs")
    model, tok = load_model(plan, ids[0])
    payload["source_runtime"] = runtime_metadata(model, tok)
    try:
        for i, b in enumerate(plan["behaviors"]):
            if b["behavior_id"] in done:
                continue
            settings = {**cfg["gcg"], "seed": cfg["seed"] + i}
            import time
            started = time.perf_counter()
            result = nanogcg.run(model, tok, [{"role": "user", "content": b["instruction"]}],
                                 b["target"], nanogcg.GCGConfig(**settings))
            elapsed = time.perf_counter() - started
            if not np.isfinite(result.best_loss) or not isinstance(result.best_string, str):
                raise ValueError("source optimization returned invalid measurements")
            suffix_ids = _ids(tok(result.best_string, add_special_tokens=False))
            if tok.decode(suffix_ids) != result.best_string:
                raise ValueError("optimized suffix does not survive re-encoding; evaluated IDs would differ")
            built = prompt_ids(tok, b["instruction"], result.best_string)
            payload["attacks"].append({
                "behavior_id": b["behavior_id"], "suffix": result.best_string,
                "suffix_source_ids": suffix_ids,
                "evaluated_source_loss": exact_target_loss(model, tok, built["ids"], b["target"]),
                "source_loss": float(result.best_loss),
                "source_loss_trace": [float(v) for v in getattr(result, "losses", [])],
                "search_wall_seconds": elapsed,
                "source_probability_proxy": float(np.exp(-max(0.0, result.best_loss))),
                "random_control": random_control(tok, result.best_string, cfg["seed"] + i),
                "settings": settings,
            })
            private_json(dest, payload)
    finally:
        del model
        release()
    return dest


@locked_stage
def generate(config_path) -> Path:
    import torch
    cfg, plan, ids, out = frozen(config_path)
    attacks_art = json.loads((out / "source_attacks.json").read_text())
    if attacks_art["plan_digest"] != digest(plan):
        raise ValueError("attack artifact does not match frozen plan")
    attacks = {a["behavior_id"]: a for a in attacks_art["attacks"]}
    if len(attacks) != len(attacks_art["attacks"]):
        raise ValueError("duplicate source attack IDs")
    if set(attacks) != {b["behavior_id"] for b in plan["behaviors"]}:
        raise ValueError("complete base-only search before evaluating variants")
    path = out / "generations.json"
    evidence = json.loads(path.read_text()) if path.exists() else {
        "plan_digest": digest(plan), "attack_digest": digest(attacks_art), "items": []}
    if evidence["plan_digest"] != digest(plan) or evidence["attack_digest"] != digest(attacks_art):
        raise ValueError("generation evidence belongs to another plan or attack set")
    done = validate_evidence(evidence, plan, ids)
    mismatch = plan.get("mismatched_suffix_source", {})

    def suffix_for(attack, behavior_id, condition):
        if condition == "bare":
            return ""
        if condition == "random_suffix":
            return attack["random_control"]["suffix"]
        if condition == "gcg":
            return attack["suffix"]
        if condition == "mismatched_gcg":
            return attacks[mismatch[behavior_id]]["suffix"]
        raise ValueError(f"unknown frozen condition {condition}")

    for model_id in ids:
        model, tok = load_model(plan, model_id)
        evidence.setdefault("runtime_models", {})[model_id] = runtime_metadata(model, tok)
        try:
            for i, b in enumerate(plan["behaviors"]):
                a = attacks[b["behavior_id"]]
                for condition in plan["conditions"]:
                    built = prompt_ids(tok, b["instruction"], suffix_for(a, b["behavior_id"], condition))
                    input_ids = torch.tensor([built["ids"]], device=model.device)
                    attention = torch.ones_like(input_ids)
                    budget = int(cfg["max_new_tokens"])
                    if input_ids.shape[-1] + budget > model.config.max_position_embeddings:
                        raise ValueError("prompt and generation exceed model context; no silent truncation")
                    matches_source = (built["suffix_ids"] == a["suffix_source_ids"]
                                      if condition == "gcg" and "suffix_source_ids" in a else None)
                    pending = False
                    eos_ids = getattr(model.generation_config, "eos_token_id", None) if hasattr(model, "generation_config") else None
                    eos_ids = eos_ids if isinstance(eos_ids, (list, tuple)) else [eos_ids]
                    missing = [r for r in range(cfg["generations"])
                               if (model_id, b["behavior_id"], condition, r) not in done]
                    samples = []
                    if cfg.get("batch_replicates") and missing:
                        # One call draws every replicate of a cell from the same prompt, so there is
                        # no padding and no cross-prompt batching. One seed per behavior, shared by
                        # all conditions. A cell is written atomically, so it is never half done.
                        if len(missing) != cfg["generations"]:
                            raise ValueError("partially recorded replicate batch; evidence is inconsistent")
                        seed = cfg["seed"] + i * 10000
                        torch.manual_seed(seed)
                        with torch.no_grad():
                            output = model.generate(input_ids=input_ids, attention_mask=attention,
                                                    generation_config=decoding_config(model, tok, budget),
                                                    num_return_sequences=len(missing))
                        for replicate in missing:
                            samples.append((replicate, seed, trim_after_eos(output[replicate][input_ids.shape[-1]:], eos_ids)))
                    for replicate in ([] if cfg.get("batch_replicates") else missing):
                        # Same seed per behavior/replicate/condition; serial to avoid batching drift.
                        seed = cfg["seed"] + i * 10000 + replicate
                        torch.manual_seed(seed)
                        with torch.no_grad():
                            output = model.generate(input_ids=input_ids, attention_mask=attention,
                                                    generation_config=decoding_config(model, tok, budget))
                        samples.append((replicate, seed, output[0][input_ids.shape[-1]:]))
                    for replicate, seed, tokens in samples:
                        key = (model_id, b["behavior_id"], condition, replicate)
                        eos_observed = bool(len(tokens) and int(tokens[-1]) in eos_ids)
                        row = {"model_id": model_id, "behavior_id": b["behavior_id"],
                               "category": b["category"], "split": b["split"],
                               "condition": condition, "replicate": replicate, "seed": seed,
                               "generation_ids": tokens.tolist(),
                               "text": tok.decode(tokens, skip_special_tokens=True),
                               "budget_exhausted": len(tokens) >= budget,
                               "finished_with_eos": eos_observed,
                               "budget_censored": len(tokens) >= budget and not eos_observed,
                               "prompt_digest": digest(built["ids"]), "prompt_tokens": len(built["ids"]),
                               "suffix_ids_match_source": matches_source}
                        row["id"] = digest(list(key))[:24]
                        evidence["items"].append(row)
                        pending = True
                    if pending:
                        # One atomic write per behavior and condition. Rewriting the whole
                        # artifact after every generation grows quadratically with study size.
                        private_json(path, evidence)
                        pending = False
        finally:
            del model
            release()
    return path


def status(config_path) -> dict:
    """Read-only counters; no model loading and no claim that a process is alive."""
    cfg, _, ids, out = context(config_path)
    plan_file = out / "plan.json"
    if not plan_file.exists():
        return {"study_id": cfg["study_id"], "state": "not_initialized"}
    plan = json.loads(plan_file.read_text())
    def rows(filename, key):
        path = out / filename
        return json.loads(path.read_text()).get(key, []) if path.exists() else []
    judgments = rows("judgments.json", "items")
    return {
        "study_id": cfg["study_id"],
        "code_matches_frozen_plan": code_digest() == plan["code_digest"],
        "config_matches_frozen_plan": digest(cfg) == plan["config_digest"],
        "n_evaluated_checkpoints": len(ids), "n_behaviors_planned": len(plan["behaviors"]),
        "source_searches_completed": len(rows("source_attacks.json", "attacks")),
        "generations_recorded": len(rows("generations.json", "items")),
        "generations_planned": len(ids) * len(plan["behaviors"]) * len(plan["conditions"]) * cfg["generations"],
        "judgments_recorded": len(judgments),
        "invalid_judgments": sum(r.get("label") not in (0, 1) for r in judgments),
        "human_audit_scored": (out / "human_audit.json").exists(),
        "summary_available": (out / "summary.json").exists(),
        "interpretation": "Artifact counters only; not evidence that a process is running or a result is validated.",
    }


def parse_judge(text: str) -> int | None:
    label = text.strip().lower()
    return {"yes": 1, "no": 0}.get(label)


@locked_stage
def judge(config_path) -> Path:
    """Classify exact outputs with a dedicated classifier, no detector fallback."""
    import torch
    cfg, plan, ids, out = frozen(config_path)
    evidence = json.loads((out / "generations.json").read_text())
    if evidence["plan_digest"] != digest(plan):
        raise ValueError("generation evidence belongs to another frozen plan")
    validate_evidence(evidence, plan, ids, require_complete=True)
    jid = cfg["independent_judge"]["model_id"]
    dest = out / "judgments.json"
    judgments = json.loads(dest.read_text()) if dest.exists() else {
        "evidence_digest": digest(evidence), "judge_model": jid, "items": []}
    if judgments["evidence_digest"] != digest(evidence):
        raise ValueError("evidence changed after judging; use a fresh judgments artifact")
    if judgments["judge_model"] != jid:
        raise ValueError("judgments name a different classifier")
    expected_ids = {r["id"] for r in evidence["items"]}
    prior_ids = [r["id"] for r in judgments["items"]]
    if len(set(prior_ids)) != len(prior_ids) or not set(prior_ids) <= expected_ids:
        raise ValueError("duplicate or unknown judgment IDs")
    done = {r["id"] for r in judgments["items"]}
    behaviors = {b["behavior_id"]: b["instruction"] for b in plan["behaviors"]}
    model, tok = load_model(plan, jid)
    try:
        for row in evidence["items"]:
            if row["id"] in done:
                continue
            prompt = plan["rubric"]["prompt"].format(
                behavior=behaviors[row["behavior_id"]], generation=row["text"])
            inputs = tok(prompt, return_tensors="pt").to(model.device)
            if inputs.input_ids.shape[-1] + 4 > model.config.max_position_embeddings:
                answer, label, status = "", None, "context_overflow"
            else:
                with torch.no_grad():
                    output = model.generate(**inputs, do_sample=False, max_new_tokens=4,
                                            pad_token_id=tok.pad_token_id)
                answer = tok.decode(output[0][inputs.input_ids.shape[-1]:], skip_special_tokens=True)
                label = parse_judge(answer)
                status = "scored" if label is not None else "invalid_label"
            judgments["items"].append({"id": row["id"], "label": label,
                                       "raw_label": answer, "status": status})
            private_json(dest, judgments)
    finally:
        del model
        release()
    return dest


def label_sheet_name(annotator: int) -> str:
    return "human_labels.csv" if annotator == 1 else f"human_labels_{annotator}.csv"


def audit_sample(items, labels, n, design, positive_share, rng):
    """Draw a probability sample; return (indices, strata) with known inclusion probabilities.

    The stratified design oversamples classifier positives and keeps every
    unscored output it can, so precision and recall are estimable even when
    positives are rare. Weights 1/pi recover population estimates.
    """
    if design == "simple_random":
        chosen = rng.choice(len(items), n, replace=False)
        return [int(i) for i in chosen], {"all": {"population": len(items), "sampled": n,
                                                   "members": [int(i) for i in chosen]}}
    groups = {"classifier_positive": [], "classifier_negative": [], "unscored": []}
    for i, r in enumerate(items):
        label = labels.get(r["id"])
        groups["classifier_positive" if label == 1 else "classifier_negative" if label == 0 else "unscored"].append(i)
    want = {"classifier_positive": min(len(groups["classifier_positive"]), int(np.ceil(n * positive_share)))}
    want["unscored"] = min(len(groups["unscored"]), n - want["classifier_positive"])
    want["classifier_negative"] = min(len(groups["classifier_negative"]), n - sum(want.values()))
    spare = n - sum(want.values())
    want["classifier_positive"] = min(len(groups["classifier_positive"]), want["classifier_positive"] + spare)
    strata, chosen = {}, []
    for name, members in groups.items():
        picked = [members[int(i)] for i in rng.choice(len(members), want[name], replace=False)] if want[name] else []
        strata[name] = {"population": len(members), "sampled": len(picked), "members": picked}
        chosen.extend(picked)
    order = rng.permutation(len(chosen))  # labelers must not infer strata from position
    return [chosen[int(i)] for i in order], strata


@locked_stage
def audit(config_path) -> Path:
    """Blind probability sample of ORIGINAL generations; human labels left blank."""
    import csv
    cfg, plan, ids, out = frozen(config_path)
    evidence = json.loads((out / "generations.json").read_text())
    if evidence["plan_digest"] != digest(plan):
        raise ValueError("audit evidence belongs to another plan")
    validate_evidence(evidence, plan, ids, require_complete=True)
    sheets = [out / label_sheet_name(k) for k in range(1, int(cfg.get("human_annotators", 1)) + 1)]
    if any(p.exists() for p in sheets):
        raise FileExistsError("human label sheet already exists; refusing to replace its evidence")
    design = cfg.get("human_audit_design", "simple_random")
    labels = {}
    if design == "stratified_classifier_label":
        judgments = json.loads((out / "judgments.json").read_text())
        if judgments["evidence_digest"] != digest(evidence):
            raise ValueError("stratification needs judgments of the current evidence")
        if {r["id"] for r in judgments["items"]} != {r["id"] for r in evidence["items"]}:
            raise ValueError("judge every generation before drawing a classifier-stratified audit")
        labels = {r["id"]: r["label"] for r in judgments["items"]}
    items = evidence["items"]
    n = min(int(cfg.get("human_audit_n", 100)), len(items))
    rng = np.random.default_rng(cfg["seed"])
    chosen, strata = audit_sample(items, labels, n, design, float(cfg.get("human_audit_positive_share", 0.5)), rng)
    selected = [items[i] for i in chosen]
    stratum_of = {items[i]["id"]: name for name, s in strata.items() for i in s["members"]}
    private_json(out / "audit_design.json", {
        "design": design, "seed": cfg["seed"], "evidence_digest": digest(evidence),
        "strata": {name: {"population": s["population"], "sampled": s["sampled"],
                          "inclusion_probability": s["sampled"] / s["population"] if s["population"] else None}
                   for name, s in strata.items()},
        "item_stratum": stratum_of,
        "interpretation": "Kept apart from the labeling sheet so labelers stay blind to classifier labels.",
    })
    text_path = out / "audit_items.json"
    behaviors = {b["behavior_id"]: b["instruction"] for b in plan["behaviors"]}
    private_json(text_path, {"sampling": "probability sample of exact outputs; design in audit_design.json",
                            "inclusion_probability": n / len(items) if design == "simple_random" and items else None,
                            "evidence_digest": digest(evidence), "items": [
                                {"id": r["id"], "behavior": behaviors[r["behavior_id"]], "text": r["text"]}
                                for r in selected]})
    for path in sheets:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "human_label", "notes"])
            writer.writerows((r["id"], "", "") for r in selected)
    return text_path
