"""Regression tests for the third review: exact tests, audit criterion, power, public commitment.

Benign synthetic fixtures only.
"""
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from lasa import power, study
from lasa.study_analysis import audit_validation, sign_flip_p
from lasa.study_validation import validate_config
from test_protocol_fixes import DECISION, confirmatory, run_through_judge
from test_study import fixture_config

CRITERIA = {"min_precision": 0.8, "max_false_omission_rate": 0.05, "min_inter_annotator_agreement": 0.9}


def test_sign_flip_is_exact_for_the_twenty_behavior_design():
    result = sign_flip_p([0.2] * 20, n_perm=1000)
    assert result["method"] == "exact sign-flip"
    assert result["p"] == pytest.approx(2 / 2 ** 20)
    mixed = sign_flip_p([0.2, -0.2] * 10)
    assert mixed["p"] == 1.0
    # ADR-0024 raised the exact range to 40 behaviors; beyond it the test samples.
    assert sign_flip_p([0.2] * 41, n_perm=1000)["method"].startswith("Monte Carlo")


def audit(precision, false_negative_weight, labeler="A"):
    cells = {"classifier1_labeler1": 10 * precision, "classifier1_labeler0": 10 * (1 - precision),
             "classifier0_labeler1": false_negative_weight, "classifier0_labeler0": 100 - false_negative_weight}
    return {"labeler": labeler, "design_weighted": {"precision": precision, "weighted_confusion": cells}}


def test_frozen_audit_criterion_decides_classifier_validity():
    agree = {"agreement": 0.95, "cohens_kappa": 0.9}
    assert audit_validation([], None, False, None)["status"] == "no_frozen_criterion"
    assert audit_validation([audit(0.9, 2)], None, False, CRITERIA)["status"] == "pending"
    good = audit_validation([audit(0.9, 2), audit(0.85, 4, "B")], agree, True, CRITERIA)
    assert good["status"] == "classifier_supported"
    assert good["per_annotator"][1]["false_omission_rate"] == pytest.approx(0.04)
    for audits, pair in (([audit(0.7, 2), audit(0.9, 2)], agree),          # too many false positives
                         ([audit(0.9, 2), audit(0.9, 9)], agree),          # too many missed positives
                         ([audit(0.9, 2), audit(0.9, 2)], {"agreement": 0.8})):  # annotators disagree
        assert audit_validation(audits, pair, True, CRITERIA)["status"] == "classifier_not_supported"
    # No classifier positives: precision is undefined and does not block; omissions still count.
    none_positive = {"labeler": "A", "design_weighted": {"precision": None, "weighted_confusion": {
        "classifier1_labeler1": 0.0, "classifier1_labeler0": 0.0,
        "classifier0_labeler1": 0.0, "classifier0_labeler0": 50.0}}}
    assert audit_validation([none_positive], None, True, CRITERIA)["status"] == "classifier_supported"


def test_summary_reports_classifier_validation_status(tmp_path, monkeypatch):
    from lasa.study_analysis import analyze
    path, _ = confirmatory(tmp_path, monkeypatch, decision={**DECISION, "audit": CRITERIA})
    run_through_judge(path, monkeypatch)
    summary = json.loads(analyze(path).read_text())
    assert summary["classifier_validation"]["status"] == "pending"
    assert summary["results"]["owned/variant"]["decision"]["classifier_validation"] == "pending"
    assert summary["max_source_loss_reproduction_gap"] is not None


def test_audit_criterion_schema_and_prospective_configs():
    root = Path(__file__).resolve().parents[1]
    for name in ("study-qwen15-v2.yaml", "study-llama32-v1.yaml"):
        cfg = yaml.safe_load((root / "experiments" / name).read_text())
        validate_config(cfg)
        assert cfg["decision"]["audit"] == CRITERIA
        cfg["decision"]["audit"] = {"min_precision": 0.8}
        with pytest.raises(ValueError):
            validate_config(cfg)


def test_power_covers_the_full_decision_rule_and_the_top_k_test():
    single, _ = power.contrast_power(20, 5, 0.1, 1.6, 1.0, 0.05, sims=300, seed=1)
    both, delta = power.decision_power(20, 5, 0.1, 1.6, 1.0, 0.05, sims=300, seed=1)
    assert 0 < both <= single + 0.05 and 0.1 < delta < 0.4
    null, _ = power.decision_power(20, 5, 0.1, 0.0, 1.0, 0.05, sims=300, seed=1)
    assert null < 0.05
    weak = power.top_k_power(12, 3, 0.0, 0.05, sims=300, seed=2)
    strong = power.top_k_power(12, 3, 0.9, 0.05, sims=300, seed=2)
    assert weak < 0.12 < 0.8 < strong
    stored = json.loads((Path(__file__).resolve().parents[1] / "experiments/results/power_analysis.json").read_text())
    assert stored["top_k_prioritization"]["rho_for_80pct_power"] == {"unadjusted_alpha": 0.7, "holm_worst_case_alpha": 0.8}
    assert all("both_controls_holm_worst_case" in row for row in stored["primary_contrast_80pct_power"].values())


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
                   cwd=cwd, check=True, capture_output=True)


def test_confirmatory_search_needs_a_pushed_commitment(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    path, cfg = confirmatory(work, monkeypatch, decision=DECISION)
    study.initialize(path)
    plan = json.loads(Path("reports/test/plan.json").read_text())
    record = "experiments/results/test/plan_commitment.json"
    git("init", "-q", "-b", "main", cwd=work)
    with pytest.raises(ValueError, match="commit"):
        study.require_public_commitment(cfg, plan)
    git("add", record, cwd=work)
    git("commit", "-q", "-m", "plan", cwd=work)
    with pytest.raises(ValueError, match="push the commit"):
        study.require_public_commitment(cfg, plan)
    remote = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(remote), cwd=tmp_path)
    git("remote", "add", "origin", str(remote), cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    study.require_public_commitment(cfg, plan)


def test_generation_checkpoints_once_per_behavior_and_condition(tmp_path, monkeypatch):
    import nanogcg
    from types import SimpleNamespace
    path, cfg = fixture_config(tmp_path, monkeypatch)
    monkeypatch.setattr(nanogcg, "run", lambda *a, **kw: SimpleNamespace(best_string="apple", best_loss=0.5))
    study.initialize(path)
    study.search(path)
    writes, original = [], study.private_json
    monkeypatch.setattr(study, "private_json", lambda p, v: (writes.append(p.name), original(p, v))[1])
    study.generate(path)
    cells = 2 * 4 * 3  # checkpoints, behaviors, conditions
    assert writes.count("generations.json") == cells
    assert len(json.loads(Path("reports/test/generations.json").read_text())["items"]) == cells * cfg["generations"]
    writes.clear()
    study.generate(path)
    assert writes == []
