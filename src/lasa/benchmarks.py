"""Load established public harmful-behavior benchmarks and contrast splits.

LASA never authors new harmful prompt corpora (CLAUDE.md rule 5). This module
loads the public sets (JailbreakBench, HarmBench, AdvBench) and the contrast
splits from Arditi et al. stored under configs/benchmarks/.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parents[2] / "configs" / "benchmarks"
VALID_BENCHMARKS = ("jailbreakbench", "harmbench", "advbench")


@dataclass(frozen=True)
class Behavior:
    behavior_id: str
    instruction: str
    category: str
    target: str = "Sure, here is"


def load_benchmark(
    name: str = "jailbreakbench",
    limit: int | None = None,
    benchmark_dir: Path = BENCHMARK_DIR,
) -> list[Behavior]:
    """Load behaviors from an established benchmark JSON file."""
    if name not in VALID_BENCHMARKS:
        raise ValueError(f"unknown benchmark {name!r}; valid: {VALID_BENCHMARKS}")
    path = benchmark_dir / f"{name}.json"
    raw = json.loads(path.read_text())
    if limit is not None:
        raw = raw[: int(limit)]
    behaviors = []
    for idx, entry in enumerate(raw):
        instruction = entry["instruction"]
        category = entry.get("category") or "uncategorized"
        target = entry.get("target") or "Sure, here is"
        behaviors.append(
            Behavior(
                behavior_id=f"{name}-{idx:03d}",
                instruction=instruction,
                category=category,
                target=target,
            )
        )
    return behaviors


def with_published_targets(
    behaviors: list[Behavior],
    name: str = "jailbreakbench",
    benchmark_dir: Path = BENCHMARK_DIR,
) -> list[Behavior]:
    """Replace the generic target with the benchmark's own published target string.

    The targets file is aligned by index and stores a digest of each instruction,
    so a reordered or edited behavior file fails instead of pairing the wrong target.
    """
    path = benchmark_dir / f"{name}_targets.json"
    if not path.exists():
        raise ValueError(f"no published targets recorded for {name!r}")
    targets = json.loads(path.read_text())["targets"]
    if len(targets) != len(behaviors):
        raise ValueError("published targets do not cover the benchmark")
    out = []
    for index, (behavior, entry) in enumerate(zip(behaviors, targets)):
        digest = hashlib.sha256(behavior.instruction.encode()).hexdigest()
        if entry["index"] != index or entry["instruction_sha256"] != digest:
            raise ValueError(f"published target {index} does not match its behavior")
        out.append(replace(behavior, target=entry["target"]))
    return out


def load_contrast_pairs(
    n_pairs: int = 32,
    benchmark_dir: Path = BENCHMARK_DIR,
) -> tuple[list[str], list[str]]:
    """Load harmful and harmless contrast instructions for refusal direction extraction."""
    harmful_raw = json.loads((benchmark_dir / "contrast_harmful.json").read_text())
    harmless_raw = json.loads((benchmark_dir / "contrast_harmless.json").read_text())
    if type(n_pairs) is not int or not 2 <= n_pairs <= min(len(harmful_raw), len(harmless_raw)):
        raise ValueError("contrast count must be an integer supported by both published splits")
    harmful = [x["instruction"] for x in harmful_raw[:n_pairs]]
    harmless = [x["instruction"] for x in harmless_raw[:n_pairs]]
    if len(harmful) < 2 or len(harmless) < 2:
        raise ValueError("need at least 2 contrast instructions per split")
    return harmful, harmless
