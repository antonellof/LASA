"""Stage 1: resolve base and variants to local checkpoints, record hashes.

After this stage, later stages run without network access. Checkpoints are
read from a local directory. Downloading is a separate, explicit step
(`lasa fetch`, not part of run) so a run never reaches the network by surprise.

Local path rule: a model id `org/name` resolves to `<models_dir>/org__name`
unless the config gives an explicit `path`.

Hash: SHA-256 over every weight and config file in the checkpoint, streamed in
sorted order. The digest is the content identity used in the manifest.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from lasa.context import Context

# .jinja: recent checkpoints ship the chat template as chat_template.jinja, and the template
# decides every evaluated token, so it is part of the content identity.
HASHED_SUFFIXES = (".safetensors", ".bin", ".json", ".model", ".txt", ".jinja")


def resolve_local_path(model_entry: dict, models_dir: Path) -> Path:
    if "path" in model_entry:
        return Path(model_entry["path"])
    return models_dir / model_entry["model_id"].replace("/", "__")


def hash_checkpoint(path: Path) -> dict:
    """Return a stable digest for a checkpoint directory."""
    if not path.is_dir():
        raise FileNotFoundError(f"checkpoint directory not found: {path}")
    files = sorted(
        p for p in path.iterdir() if p.is_file() and p.suffix in HASHED_SUFFIXES
    )
    if not any(p.suffix in (".safetensors", ".bin") for p in files):
        raise FileNotFoundError(f"no weight files in {path}")
    outer = hashlib.sha256()
    entries = []
    for p in files:
        inner = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                inner.update(chunk)
        entries.append({"file": p.name, "sha256": inner.hexdigest(), "bytes": p.stat().st_size})
        outer.update(p.name.encode())
        outer.update(inner.digest())
    return {"path": str(path), "digest": outer.hexdigest(), "files": entries}


def run(ctx: Context) -> Path:
    models_dir = Path(ctx.config.get("models_dir", "models"))
    entries = [("base", ctx.config["base"])]
    entries += [("variant", v) for v in ctx.config.get("variants", [])]

    models = []
    for role, entry in entries:
        local = resolve_local_path(entry, models_dir)
        info = hash_checkpoint(local)
        models.append({"role": role, "model_id": entry["model_id"], **info})

    return ctx.write_artifact("load", {"seed": ctx.seed, "models": models})
