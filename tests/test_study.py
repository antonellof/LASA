"""Scientific plumbing on benign fixtures; no jailbreak outputs in tests."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
import yaml
from transformers import BatchEncoding

from lasa import study
from lasa.benchmarks import Behavior
from lasa.study_analysis import analyze, paired_effect_ci


def test_split_is_reproducible_balanced_and_disjoint():
    fixtures = [Behavior(f"b{i}", f"Describe a garden {i}", str(i % 2)) for i in range(12)]
    a = study.stratified_split(fixtures, 4, 17)
    assert a == study.stratified_split(fixtures[::-1][::-1], 4, 17)
    assert len(a) == 8
    assert len({r["behavior_id"] for r in a}) == 8
    for cat in ("0", "1"):
        for split in ("development", "heldout"):
            assert sum(r["category"] == cat and r["split"] == split for r in a) == 2
    with pytest.raises(ValueError):
        study.stratified_split(fixtures, 3, 0)


@pytest.mark.parametrize("text,expected", [("yes", 1), ("NO\n", 0), ("yes because", None), ("unclear", None)])
def test_invalid_judge_outputs_are_not_converted_to_failures(text, expected):
    assert study.parse_judge(text) == expected


def test_paired_bootstrap_reuses_controls():
    # Exact equality between conditions must remain equality under every draw.
    a = np.random.default_rng(4).integers(0, 2, (8, 5))
    x = np.stack([a, a, a], axis=2)
    stats = paired_effect_ci(x, n_boot=100)
    assert stats["gcg_minus_bare"]["mean"] == 0.0
    assert stats["gcg_minus_bare"]["ci95"] is None
    assert stats["gcg_minus_random_suffix"]["interval_status"] == "degenerate_bootstrap_not_informative"
    with pytest.raises(ValueError):
        paired_effect_ci(np.full((3, 2, 3), np.nan))


class Tokenizer:
    chat_template = "fixture-template"
    bos_token = None
    pad_token_id = 0

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        return f"<user>{messages[-1]['content']}</user><assistant>"

    def encode(self, text, **kw):
        return [3 + ord(c) % 50 for c in text]

    def decode(self, tokens, **kw):
        tokens = list(tokens)
        if tokens and int(tokens[0]) == 91:
            return "yes"
        if tokens and int(tokens[0]) == 21:
            return "A benign description of a garden in spring."
        return "apple"

    def __call__(self, text, return_tensors=None, **kw):
        ids = self.encode(text)
        if return_tensors == "pt":
            return BatchEncoding({"input_ids": torch.tensor([ids]),
                                  "attention_mask": torch.ones((1, len(ids)), dtype=torch.long)})
        return {"input_ids": ids, "attention_mask": [1] * len(ids)}


class Model:
    device = "cpu"
    config = SimpleNamespace(max_position_embeddings=4096)

    def __init__(self, judge=False):
        self.judge = judge

    def __call__(self, input_ids, **kw):
        return SimpleNamespace(logits=torch.zeros((1, input_ids.shape[-1], 128)))

    def generate(self, input_ids, **kw):
        return torch.cat([input_ids, torch.tensor([[91 if self.judge else 21]])], dim=1)


def fixture_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(study, "_revision", lambda: "fixture-revision")
    fixtures = [Behavior(f"b{i}", f"Describe a garden {i}", str(i % 2)) for i in range(8)]
    monkeypatch.setattr(study, "load_benchmark", lambda _: fixtures)
    for name in ("base", "variant", "judge"):
        path = tmp_path / "models" / f"owned__{name}"
        path.mkdir(parents=True)
        (path / "model.safetensors").write_bytes(f"synthetic {name} weights".encode())
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"id": "test", "operator": "Test", "scope": ["owned/base", "owned/variant"],
                                "basis": "owned", "authorized_on": "2026-11-08"}))
    rubric = tmp_path / "rubric.json"
    rubric.write_text(json.dumps({"model_id": "owned/judge", "prompt": "Evaluate {behavior}: {generation}"}))
    cfg = {
        "study_id": "test", "seed": 3, "protocol_status": "pilot",
        "authorization": str(auth), "output_dir": "reports/test", "models_dir": "models",
        "base": {"model_id": "owned/base"}, "variants": [{"model_id": "owned/variant"}],
        "independent_judge": {"model_id": "owned/judge", "rubric": str(rubric)},
        "benchmark": {"name": "fixture", "per_category": 2},
        "gcg": {"num_steps": 1, "search_width": 2, "topk": 2},
        "generations": 2, "max_new_tokens": 16, "human_audit_n": 8,
    }
    path = tmp_path / "test.yaml"
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(study, "load_model", lambda plan, mid: (Model(mid == "owned/judge"), Tokenizer()))
    return path, cfg


def test_full_study_benign_smoke_resume_freeze_and_audit(tmp_path, monkeypatch):
    import nanogcg
    path, cfg = fixture_config(tmp_path, monkeypatch)
    monkeypatch.setattr(nanogcg, "run", lambda *a, **kw: SimpleNamespace(best_string="apple", best_loss=0.5))
    study.initialize(path)
    with pytest.raises(FileExistsError):
        study.initialize(path)
    study.search(path)
    study.generate(path)
    original = Path("reports/test/generations.json").read_bytes()
    study.generate(path)
    assert original == Path("reports/test/generations.json").read_bytes()
    study.judge(path)
    dest = analyze(path)
    summary = json.loads(dest.read_text())
    assert summary["heldout_behaviors_complete"] == 2
    assert summary["invalid_or_missing_labels"] == 0
    assert summary["human_validation"].startswith("pending")
    assert summary["results"]["owned/variant"]["effects"]["gcg_minus_bare"]["mean"] == 0.0
    study.audit(path)
    audit = json.loads(Path("reports/test/audit_items.json").read_text())
    evidence = json.loads(original)
    actual = {r["id"]: r["text"] for r in evidence["items"]}
    assert all(r["text"] == actual[r["id"]] for r in audit["items"])
    assert not Path("reports/test/generations.json").stat().st_mode & 0o077
    with pytest.raises(FileExistsError):
        study.audit(path)
    cfg["seed"] += 1
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="changed after freeze"):
        study.generate(path)


def test_authorization_fails_without_writing_evidence(tmp_path, monkeypatch):
    path, cfg = fixture_config(tmp_path, monkeypatch)
    cfg["variants"] = [{"model_id": "unauthorized/model"}]
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(PermissionError):
        study.initialize(path)
    assert not Path("reports").exists()
