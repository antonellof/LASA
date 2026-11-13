"""Changes that let a study run on a GPU host and be scored elsewhere (ADR-0022 to ADR-0024).

Benign fixtures only; no jailbreak outputs in tests.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
import yaml

from lasa import study
from lasa.pipeline.load import hash_checkpoint
from lasa.study_analysis import analyze
from lasa.study_validation import validate_config
from test_study import Model, fixture_config

EOS, PAD = 7, 0


class BatchModel(Model):
    """Returns one row per requested sample, padded after EOS as transformers does."""
    generation_config = SimpleNamespace(eos_token_id=EOS, bos_token_id=None, to_dict=lambda: {})

    def generate(self, input_ids, num_return_sequences=1, **kw):
        if self.judge:
            return super().generate(input_ids)
        tails = [[21, EOS, PAD], [21, 21, EOS]][:num_return_sequences]
        return torch.cat([input_ids.repeat(len(tails), 1), torch.tensor(tails)], dim=1)


def test_trim_after_eos_keeps_the_first_eos_and_drops_padding():
    assert study.trim_after_eos(torch.tensor([5, EOS, PAD, PAD]), [EOS]).tolist() == [5, EOS]
    assert study.trim_after_eos(torch.tensor([5, 6, 8]), [EOS]).tolist() == [5, 6, 8]
    assert study.trim_after_eos(torch.tensor([5, 6]), [None]).tolist() == [5, 6]


def test_batched_replicates_record_each_sample_and_resume_unchanged(tmp_path, monkeypatch):
    import nanogcg
    path, cfg = fixture_config(tmp_path, monkeypatch)
    cfg["batch_replicates"] = True
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(study, "load_model", lambda plan, mid: (BatchModel(mid == "owned/judge"), study_tokenizer()))
    monkeypatch.setattr(nanogcg, "run", lambda *a, **kw: SimpleNamespace(best_string="apple", best_loss=0.5))
    study.initialize(path)
    study.search(path)
    study.generate(path)
    first = Path("reports/test/generations.json").read_bytes()
    study.generate(path)
    assert first == Path("reports/test/generations.json").read_bytes()
    rows = json.loads(first)["items"]
    assert len(rows) == 2 * 4 * 3 * 2  # checkpoints, behaviors, conditions, replicates
    by_replicate = {r: [x for x in rows if x["replicate"] == r] for r in (0, 1)}
    assert all(x["generation_ids"] == [21, EOS] for x in by_replicate[0])
    assert all(x["generation_ids"] == [21, 21, EOS] for x in by_replicate[1])
    assert all(x["finished_with_eos"] and not x["budget_censored"] for x in rows)
    cell = [x for x in rows if x["model_id"] == "owned/base" and x["behavior_id"] == rows[0]["behavior_id"]]
    assert len({x["seed"] for x in cell}) == 1  # one seed per behavior, shared by conditions
    study.judge(path)
    assert json.loads(analyze(path).read_text())["invalid_or_missing_labels"] == 0


def study_tokenizer():
    from test_study import Tokenizer
    return Tokenizer()


def test_evidence_stages_need_the_frozen_environment_but_analysis_does_not(tmp_path, monkeypatch):
    import nanogcg
    path, _ = fixture_config(tmp_path, monkeypatch)
    monkeypatch.setattr(nanogcg, "run", lambda *a, **kw: SimpleNamespace(best_string="apple", best_loss=0.5))
    study.initialize(path)
    study.search(path)
    study.generate(path)
    study.judge(path)
    monkeypatch.setattr(study, "environment_differences", lambda plan: {"torch": ["2.15.0", "2.5.1"]})
    monkeypatch.setattr("lasa.study_analysis.environment_differences", study.environment_differences)
    with pytest.raises(ValueError, match="environment changed after freeze: torch"):
        study.generate(path)
    summary = json.loads(analyze(path).read_text())
    assert summary["analysis_environment_differences"] == {"torch": ["2.15.0", "2.5.1"]}
    monkeypatch.setattr(study, "environment_differences", lambda plan: {"numpy": ["1.26.4", "2.0.0"]})
    with pytest.raises(ValueError, match="environment changed after freeze: numpy"):
        analyze(path)


def test_chat_template_file_is_part_of_the_checkpoint_identity(tmp_path):
    digests = []
    for name, template in (("a", "{{ messages }}"), ("b", "system {{ messages }}")):
        d = tmp_path / name
        d.mkdir()
        (d / "model.safetensors").write_bytes(b"same weights")
        (d / "chat_template.jinja").write_text(template)
        digests.append(hash_checkpoint(d)["digest"])
    assert digests[0] != digests[1]


def test_config_rejects_unknown_dtype(tmp_path, monkeypatch):
    _, cfg = fixture_config(tmp_path, monkeypatch)
    validate_config({**cfg, "torch_dtype": "bfloat16", "batch_replicates": True})
    with pytest.raises(ValueError):
        validate_config({**cfg, "torch_dtype": "int8"})


def test_exact_sign_flip_matches_brute_force_and_covers_thirty_behaviors():
    import numpy as np
    from itertools import product
    from lasa.study_analysis import sign_flip_p
    rng = np.random.default_rng(0)
    for n in (1, 2, 7, 12):
        d = rng.integers(-5, 6, n) / 5  # the lattice of five-replicate rate differences, with ties
        if not d.any():
            d[0] = 0.2
        observed = abs(d.mean())
        brute = np.mean([abs(np.dot(s, d)) / n >= observed - 1e-12 for s in product((-1.0, 1.0), repeat=n)])
        result = sign_flip_p(d)
        assert result["method"] == "exact sign-flip" and abs(result["p"] - brute) < 1e-15
    balanced = sign_flip_p(np.array([0.2, -0.2]))
    assert balanced["p"] == 1.0
    thirty = sign_flip_p(np.full(30, 0.2))
    assert thirty["method"] == "exact sign-flip" and thirty["p"] == 2 / 2 ** 30
