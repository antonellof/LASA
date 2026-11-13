"""Integration smoke test for the full LASA pipeline.

Constructs a tiny local Hugging Face causal LM pair (a base checkpoint and two
fine-tuned variants with controlled weight perturbations), runs all six stages
via `lasa.cli.main`, and verifies that:
  1. `verify` refuses to run without a valid authorization record.
  2. All six stages complete end-to-end when authorized.
  3. `findings.json`, `manifest.json`, `report.md`, and `scatter.png` are
     well-formed, match schemas, and contain no em dashes.
Do not put harmful model outputs in fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
import yaml
from transformers import AutoTokenizer, GPT2Config, GPT2LMHeadModel

from lasa.benchmarks import Behavior
from lasa.cli import main as cli_main
from lasa.context import load_context
from lasa.pipeline import STAGES
from lasa.pipeline import verify as verify_stage
from lasa.schemas import validate_artifact


def test_import_pipeline():
    assert [s.__name__.split(".")[-1] for s in STAGES] == [
        "load",
        "similarity",
        "attack_base",
        "predict",
        "verify",
        "report",
    ]


def _save_tiny_gpt2_pair(models_dir: Path) -> tuple[Path, Path, Path]:
    """Create a tiny random GPT-2 base and two variants on disk (no network)."""
    torch.manual_seed(0)
    cfg = GPT2Config(
        vocab_size=128,
        n_positions=64,
        n_ctx=64,
        n_embd=32,
        n_layer=3,
        n_head=4,
        bos_token_id=1,
        eos_token_id=2,
        pad_token_id=0,
    )
    base_model = GPT2LMHeadModel(cfg).eval()
    base_dir = models_dir / "owned__tiny-base"
    var_close_dir = models_dir / "owned__tiny-variant-close"
    var_far_dir = models_dir / "owned__tiny-variant-far"

    base_model.save_pretrained(base_dir, safe_serialization=True)

    # Close variant: tiny perturbation of base weights
    close_model = GPT2LMHeadModel(cfg).eval()
    close_model.load_state_dict(base_model.state_dict())
    with torch.no_grad():
        for p in close_model.parameters():
            p.add_(0.01 * torch.randn_like(p))
    close_model.save_pretrained(var_close_dir, safe_serialization=True)

    # Far variant: large perturbation in upper layers
    far_model = GPT2LMHeadModel(cfg).eval()
    far_model.load_state_dict(base_model.state_dict())
    with torch.no_grad():
        for p in far_model.parameters():
            p.add_(0.8 * torch.randn_like(p))
    far_model.save_pretrained(var_far_dir, safe_serialization=True)

    return base_dir, var_close_dir, var_far_dir


class _TinyCharTokenizer:
    """Minimal deterministic tokenizer for offline smoke tests without network."""

    pad_token = "<pad>"
    eos_token = "<eos>"
    pad_token_id = 0
    eos_token_id = 2
    padding_side = "right"
    chat_template = None

    def __call__(
        self,
        texts,
        return_tensors="pt",
        add_special_tokens=True,
        padding=True,
        truncation=True,
        max_length=64,
    ):
        if isinstance(texts, str):
            texts = [texts]
        encoded = []
        for t in texts:
            ids = [((ord(c) % 120) + 4) for c in t[: max_length - 1]] + [self.eos_token_id]
            encoded.append(ids)
        max_len = max(len(x) for x in encoded) if padding else len(encoded[0])
        input_ids = []
        attn = []
        for ids in encoded:
            pad_n = max_len - len(ids)
            if self.padding_side == "left":
                input_ids.append([self.pad_token_id] * pad_n + ids)
                attn.append([0] * pad_n + [1] * len(ids))
            else:
                input_ids.append(ids + [self.pad_token_id] * pad_n)
                attn.append([1] * len(ids) + [0] * pad_n)

        class _Batch(dict):
            def to(self, device):
                return _Batch({k: v.to(device) for k, v in self.items()})

            @property
            def input_ids(self):
                return self["input_ids"]

            @property
            def attention_mask(self):
                return self["attention_mask"]

        return _Batch(
            {
                "input_ids": torch.tensor(input_ids, dtype=torch.long),
                "attention_mask": torch.tensor(attn, dtype=torch.long),
            }
        )

    def decode(self, token_ids, skip_special_tokens=True):
        del skip_special_tokens
        n = int(len(token_ids))
        # Deterministic neutral text so no harmful content appears in tests
        return " ".join(f"tok{int(t)}" for t in token_ids[: min(n, 16)])


def test_full_pipeline_smoke_and_auth_gate(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    _save_tiny_gpt2_pair(models_dir)

    monkeypatch.setattr(
        AutoTokenizer,
        "from_pretrained",
        classmethod(lambda cls, *a, **kw: _TinyCharTokenizer()),
    )

    # Synthetic benign benchmark fixtures so tests never use harmful text
    monkeypatch.setattr(
        "lasa.pipeline.attack_base.load_benchmark",
        lambda name="jailbreakbench", limit=None: [
            Behavior(
                behavior_id=f"syn-{i:03d}",
                instruction=f"synthetic probe behavior {i}",
                category="Privacy" if i % 2 == 0 else "Disinformation",
            )
            for i in range(4)
        ],
    )
    monkeypatch.setattr(
        "lasa.pipeline.attack_base.load_contrast_pairs",
        lambda n_pairs=4: (
            [f"contrast group A item {i}" for i in range(4)],
            [f"contrast group B item {i}" for i in range(4)],
        ),
    )
    monkeypatch.setattr(
        "lasa.pipeline.similarity.load_contrast_pairs",
        lambda n_pairs=4: (
            [f"contrast group A item {i}" for i in range(4)],
            [f"contrast group B item {i}" for i in range(4)],
        ),
    )

    # Structure-only attack fixture. This test does not assert optimization efficacy.
    monkeypatch.setattr(
        "lasa.pipeline.attack_base.gcg.run_gcg_on_base",
        lambda **kw: {
            "tool": {"name": "synthetic_benign_fixture", "version": "1"},
            "attacks": [{"attack_id": f"{b.behavior_id}__gcg", "behavior_id": b.behavior_id,
                         "instruction": b.instruction, "category": b.category, "primitive": "gcg",
                         "suffix": "garden", "base_success": False, "base_asr": 0.0,
                         "strength": (i + 1) / 10} for i, b in enumerate(kw["behaviors"])],
        },
    )

    probes_path = tmp_path / "probes.txt"
    probes_path.write_text("\n".join(f"neutral probe {i}" for i in range(8)) + "\n")

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "id": "auth-smoke-001",
                "operator": "Smoke Tester <smoke@example.org>",
                "scope": [
                    "owned/tiny-base",
                    "owned/tiny-variant-close",
                    "owned/tiny-variant-far",
                ],
                "basis": "owned",
                "authorized_on": "2026-11-07",
                "notes": "Synthetic smoke test models.",
            }
        )
    )

    out_dir = tmp_path / "reports" / "smoke-001"
    cfg_path = tmp_path / "smoke.yaml"
    cfg = {
        "run_id": "smoke-001",
        "seed": 0,
        "models_dir": str(models_dir),
        "authorization": str(auth_path),
        "base": {"model_id": "owned/tiny-base"},
        "variants": [
            {"model_id": "owned/tiny-variant-close"},
            {"model_id": "owned/tiny-variant-far"},
        ],
        "benchmark": {"name": "jailbreakbench", "limit": 4},
        "similarity": {"metrics": ["cka", "cosine_meanpool"], "probe_set": str(probes_path)},
        "attacks": {"primitives": ["gcg"], "contrast_pairs": 4, "max_new_tokens": 12},
        "predict": {"scoring": "strength_times_similarity"},
        "verify": {
            "top_k": 4,
            "generations_per_attack": 3,
            "judge": "local",
            "max_new_tokens": 12,
            "human_spotcheck_fraction": 0.5,
        },
        "report": {"out_dir": str(out_dir)},
    }
    cfg_path.write_text(yaml.safe_dump(cfg))

    # 1. Verify stage must refuse if authorization is missing
    ctx_no_auth = load_context(cfg_path)
    ctx_no_auth.authorization = None
    with pytest.raises(PermissionError):
        verify_stage.run(ctx_no_auth)

    # 2. Full pipeline run succeeds with valid authorization
    rc = cli_main(["run", "--config", str(cfg_path)])
    assert rc == 0

    for stage_name in ("load", "similarity", "attack_base", "predict", "verify", "report"):
        art_file = out_dir / f"{stage_name}.json"
        assert art_file.exists(), f"missing {stage_name}.json"
        validate_artifact(stage_name, json.loads(art_file.read_text()))

    manifest = json.loads((out_dir / "manifest.json").read_text())
    validate_artifact("manifest", manifest)
    assert manifest["authorization_id"] == "auth-smoke-001"
    assert len(manifest["models"]) == 3

    findings = json.loads((out_dir / "findings.json").read_text())
    assert "headline_pooled" in findings
    assert "predicted_vs_measured" in findings["headline_pooled"]
    assert "baseline_base_only_asr" in findings["headline_pooled"]
    assert len(findings["findings"]) == 8

    report_md = (out_dir / "report.md").read_text()
    assert "\u2014" not in report_md
    assert "Headline Correlation" in report_md
    assert (out_dir / "scatter.png").stat().st_size > 1000


def test_local_judge_respects_model_context_on_cpu():
    """Regression: the judge prompt must fit a small model's positions (CI runs on CPU)."""
    from lasa.judge import make_local_llm_judge

    torch.manual_seed(0)
    cfg = GPT2Config(vocab_size=128, n_positions=64, n_ctx=64, n_embd=32, n_layer=2, n_head=4,
                     bos_token_id=1, eos_token_id=2, pad_token_id=0)
    model = GPT2LMHeadModel(cfg).eval().to("cpu")
    judge = make_local_llm_judge(model, _TinyCharTokenizer())
    long_response = "a neutral placeholder sentence without refusal words " * 10
    assert judge("placeholder behavior", long_response) in (0, 1)
