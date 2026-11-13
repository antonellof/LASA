"""Regression tests for the second review: exact attack tokens, controls, gates and audits.

Benign synthetic fixtures only. Real benchmark files are read for structure, never printed.
"""
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
import yaml

from lasa import study
from lasa.audit_scoring import score_audit, weighted_estimates
from lasa.study_analysis import analyze, holm, prioritization_test, sign_flip_p
from test_study import fixture_config

CORPUS = ["Describe a quiet garden in spring.", "List three facts about rivers.",
          "Summarize how bees make honey.", "Explain why the sky looks blue.",
          "x x x x x x x x apple music tea book cloud river blue garden"] * 20
TEMPLATE = ("{{ bos_token }}{% for m in messages %}<|user|>{{ m['content'] }}<|end|>{% endfor %}"
            "{% if add_generation_prompt %}<|assistant|>{% endif %}")


def tiny_tokenizer():
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, processors, trainers
    from transformers import PreTrainedTokenizerFast
    core = Tokenizer(models.BPE())
    core.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    core.decoder = decoders.ByteLevel()
    core.train_from_iterator(CORPUS, trainers.BpeTrainer(
        vocab_size=320, special_tokens=["<s>", "<pad>"], initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
    core.post_processor = processors.TemplateProcessing(single="<s> $A", special_tokens=[("<s>", core.token_to_id("<s>"))])
    tok = PreTrainedTokenizerFast(tokenizer_object=core, bos_token="<s>", pad_token="<pad>")
    tok.chat_template = TEMPLATE
    return tok


def tiny_model(vocab):
    from transformers import LlamaConfig, LlamaForCausalLM
    torch.manual_seed(0)
    cfg = LlamaConfig(vocab_size=vocab, hidden_size=32, intermediate_size=64, num_hidden_layers=1,
                      num_attention_heads=2, num_key_value_heads=2, max_position_embeddings=256)
    return LlamaForCausalLM(cfg).eval()


def test_evaluated_ids_reproduce_the_nanogcg_optimization_loss():
    import nanogcg
    tok = tiny_tokenizer()
    model = tiny_model(len(tok))
    instruction, target = "Describe a quiet garden in spring.", "Sure, here is"
    result = nanogcg.run(model, tok, [{"role": "user", "content": instruction}], target,
                         nanogcg.GCGConfig(num_steps=2, search_width=16, topk=16, seed=1,
                                           use_prefix_cache=False, verbosity="ERROR"))
    built = study.prompt_ids(tok, instruction, result.best_string)
    assert built["ids"].count(tok.bos_token_id) == 1 and built["ids"][0] == tok.bos_token_id
    assert abs(study.exact_target_loss(model, tok, built["ids"], target) - result.best_loss) < 1e-3
    # The superseded construction joined instruction, a space and the suffix into one string.
    joined = tok(tok.apply_chat_template([{"role": "user", "content": instruction + " " + result.best_string}],
                                         tokenize=False, add_generation_prompt=True),
                 add_special_tokens=False)["input_ids"]
    assert joined != built["ids"]


def test_bare_prompt_shares_the_instruction_tokens_of_attacked_prompts():
    tok = tiny_tokenizer()
    bare = study.prompt_ids(tok, "List three facts about rivers.")
    attacked = study.prompt_ids(tok, "List three facts about rivers.", "x x apple")
    n = attacked["n_before"]
    assert bare["ids"][:n] == attacked["ids"][:n]
    assert attacked["ids"][n:n + len(attacked["suffix_ids"])] == attacked["suffix_ids"]
    assert bare["ids"][n:] == attacked["ids"][n + len(attacked["suffix_ids"]):]


def test_published_targets_are_aligned_and_checked(tmp_path):
    from lasa.benchmarks import load_benchmark, with_published_targets
    behaviors = load_benchmark("jailbreakbench")
    published = with_published_targets(behaviors)
    assert [b.instruction for b in published] == [b.instruction for b in behaviors]
    assert len({b.target for b in published}) > 90
    assert all(b.target == "Sure, here is" for b in behaviors)
    with pytest.raises(ValueError, match="does not match"):
        with_published_targets(behaviors[1:] + behaviors[:1])


def test_mismatched_suffixes_form_a_derangement_within_each_split():
    behaviors = [{"behavior_id": f"b{i}", "split": "development" if i < 5 else "heldout"} for i in range(10)]
    mapping = study.mismatch_map(behaviors, 3)
    split = {b["behavior_id"]: b["split"] for b in behaviors}
    assert set(mapping) == set(split)
    assert all(k != v and split[k] == split[v] for k, v in mapping.items())
    assert sorted(mapping.values()) == sorted(mapping)
    assert mapping == study.mismatch_map(behaviors, 3)


def test_sign_flip_holm_and_subset_null_are_exact_on_small_cases():
    assert sign_flip_p([1, 1, 1, 1, 1])["p"] == pytest.approx(2 / 32)
    assert sign_flip_p([0, 0, 0])["p"] == 1.0
    assert holm({"a": 0.01, "b": 0.04, "c": 0.03}) == pytest.approx({"a": 0.03, "b": 0.06, "c": 0.06})
    result = prioritization_test([1.0, 0.0, 0.0, 0.0], [0])
    assert result["utility"] == pytest.approx(0.75)
    assert result["p_one_sided"] == pytest.approx(0.25)


def test_weighted_audit_estimates_undo_oversampling():
    # 2 sampled positives stand for 2 items (pi = 1); 2 sampled negatives stand for 20 (pi = 0.1).
    pairs = [(1, 1), (1, 0), (0, 0), (0, 1)]
    est = weighted_estimates(pairs, [1, 1, 10, 10])
    assert est["precision"] == pytest.approx(0.5)
    assert est["recall"] == pytest.approx(1 / 11)
    assert est["labeler_positive_prevalence"] == pytest.approx(11 / 22)


def confirmatory(tmp_path, monkeypatch, **extra):
    path, cfg = fixture_config(tmp_path, monkeypatch)
    cfg.update(protocol_status="confirmatory", **extra)
    path.write_text(yaml.safe_dump(cfg))
    return path, cfg


DECISION = {"alpha": 0.3, "max_dev_bare_rate": 0.5, "n_permutations": 1000,
            "primary_controls": ["bare", "random_suffix"], "tie_preference": "source_uplift"}


def test_confirmatory_plan_needs_frozen_rules_and_a_committed_public_record(tmp_path, monkeypatch):
    path, cfg = confirmatory(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="decision rules"):
        study.initialize(path)
    cfg["decision"] = DECISION
    path.write_text(yaml.safe_dump(cfg))
    study.initialize(path)
    record = Path("experiments/results/test/plan_commitment.json")
    text = record.read_text()
    assert json.loads(text)["plan_digest"] == study.digest(json.loads(Path("reports/test/plan.json").read_text()))
    assert "Describe a garden" not in text
    with pytest.raises(ValueError, match="before search"):
        study.search(path)


def test_rubric_must_name_the_configured_classifier(tmp_path, monkeypatch):
    path, cfg = fixture_config(tmp_path, monkeypatch)
    Path(cfg["independent_judge"]["rubric"]).write_text(json.dumps({"prompt": "Evaluate {behavior}: {generation}"}))
    with pytest.raises(ValueError, match="must name the configured classifier"):
        study.initialize(path)


def run_through_judge(path, monkeypatch, commit=False):
    import nanogcg
    monkeypatch.setattr(nanogcg, "run", lambda *a, **kw: SimpleNamespace(best_string="apple", best_loss=0.5))
    monkeypatch.setattr(study, "require_public_commitment", lambda cfg, plan: None)
    study.initialize(path)
    study.search(path)
    study.generate(path)
    study.judge(path)
    return Path("reports/test")


def relabel(out, rule):
    evidence = json.loads((out / "generations.json").read_text())
    judgments = json.loads((out / "judgments.json").read_text())
    by_id = {r["id"]: r for r in evidence["items"]}
    for row in judgments["items"]:
        row["label"] = rule(by_id[row["id"]])
    study.private_json(out / "judgments.json", judgments)


def test_mismatched_control_and_development_gates_drive_decisions(tmp_path, monkeypatch):
    path, cfg = confirmatory(tmp_path, monkeypatch, decision=DECISION, controls={"mismatched_suffix": True},
                             benchmark={"name": "fixture", "per_category": 4})
    out = run_through_judge(path, monkeypatch)
    evidence = json.loads((out / "generations.json").read_text())
    assert {r["condition"] for r in evidence["items"]} == {"bare", "random_suffix", "gcg", "mismatched_gcg"}
    assert all(r["suffix_ids_match_source"] for r in evidence["items"] if r["condition"] == "gcg")

    summary = json.loads(analyze(path).read_text())  # the fixture judge says yes to everything
    variant = summary["results"]["owned/variant"]
    assert "gcg_minus_mismatched_gcg" in variant["effects"]
    assert summary["development_gates"]["attack_adequacy"]["passed"] is False
    assert variant["decision"]["incremental_compliance"] == "attack_inadequate_on_source"

    relabel(out, lambda r: int(r["condition"] == "gcg"))
    summary = json.loads(analyze(path).read_text())
    variant = summary["results"]["owned/variant"]
    assert summary["development_gates"]["attack_adequacy"]["passed"] is True
    assert summary["primary_family"] == ["owned/variant"]
    assert variant["decision"]["incremental_compliance"] == "incremental_compliance_supported"
    assert variant["decision"]["holm_adjusted_p"]["bare"] == pytest.approx(0.25)
    assert variant["decision"]["prioritization"] == "not_supported"  # uniform uplift leaves nothing to rank

    relabel(out, lambda r: int(r["condition"] == "gcg" or r["model_id"] == "owned/variant"))
    summary = json.loads(analyze(path).read_text())
    assert summary["results"]["owned/variant"]["decision"]["incremental_compliance"] == "headroom_limited"
    assert summary["primary_family"] == []


def test_stratified_two_annotator_audit_stays_blind_and_reports_agreement(tmp_path, monkeypatch):
    path, cfg = fixture_config(tmp_path, monkeypatch)
    cfg.update(human_audit_design="stratified_classifier_label", human_annotators=2, human_audit_n=8)
    path.write_text(yaml.safe_dump(cfg))
    out = run_through_judge(path, monkeypatch)
    relabel(out, lambda r: int(r["condition"] == "gcg"))
    study.audit(path)
    design = json.loads((out / "audit_design.json").read_text())
    assert design["strata"]["classifier_positive"]["sampled"] == 4
    assert design["strata"]["classifier_negative"]["sampled"] == 4
    sheet_items = json.loads((out / "audit_items.json").read_text())
    assert all(set(item) == {"id", "behavior", "text"} for item in sheet_items["items"])
    for k, sheet in enumerate(("human_labels.csv", "human_labels_2.csv")):
        rows = list(csv.DictReader((out / sheet).open()))
        with (out / sheet).open("w") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "human_label", "notes"])
            writer.writeheader()
            writer.writerows({**r, "human_label": str((i + k) % 2)} for i, r in enumerate(rows))
        score_audit(path, labeler=f"Synthetic Person {k + 1}", labeler_kind="human", sheet=sheet)
    first = json.loads((out / "human_audit.json").read_text())
    assert first["design"] == "stratified_classifier_label"
    assert first["design_weighted"]["agreement"] is not None
    summary = json.loads(analyze(path).read_text())
    assert summary["human_validation"] == "operator-declared audit complete"
    assert summary["inter_annotator_agreement"]["agreement"] == 0.0


def test_prospective_configs_use_the_reviewed_protocol():
    from lasa.study_validation import validate_config
    root = Path(__file__).resolve().parents[1]
    for name in ("study-qwen15-v2.yaml", "study-llama32-v1.yaml"):
        cfg = yaml.safe_load((root / "experiments" / name).read_text())
        validate_config(cfg)
        rubric = json.loads((root / cfg["independent_judge"]["rubric"]).read_text())
        assert rubric["model_id"] == cfg["independent_judge"]["model_id"]
        assert rubric["prompt"].startswith("[INST] You are a classifier") and "<<SYS>>" not in rubric["prompt"]
        assert cfg["benchmark"]["targets"] == "published"
        assert study.conditions_for(cfg) == ["bare", "random_suffix", "gcg", "mismatched_gcg"]
        assert cfg["decision"]["alpha"] == 0.05 and cfg["human_annotators"] == 2
        assert cfg["human_audit_design"] == "stratified_classifier_label"


def test_first_position_can_be_excluded_from_pooling():
    from lasa.pipeline.similarity import pool_hidden_states
    h = torch.ones((2, 4, 3))
    h[:, 0] = 100.0
    mask = torch.tensor([[1, 1, 1, 0], [1, 1, 1, 1]])
    full = pool_hidden_states((h,), mask)[0]
    skipped = pool_hidden_states((h,), mask, skip_first=True)[0]
    assert np.allclose(full[0], (100 + 2) / 3) and np.allclose(skipped, 1.0)
