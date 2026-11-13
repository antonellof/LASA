"""Regression cases found during the end-to-end project review."""
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
import yaml

from lasa import study
from lasa.audit_scoring import score_audit
from lasa.authorization import AuthorizationRecord, is_authorized, load_record
from lasa.study_analysis import analyze
from lasa.study_validation import validate_evidence
from test_study import fixture_config


def completed(tmp_path, monkeypatch):
    import nanogcg
    cfg_path, cfg = fixture_config(tmp_path, monkeypatch)
    monkeypatch.setattr(nanogcg, "run", lambda *a, **kw: SimpleNamespace(best_string="apple", best_loss=0.5))
    study.initialize(cfg_path)
    study.search(cfg_path)
    study.generate(cfg_path)
    study.judge(cfg_path)
    return cfg_path, cfg, Path("reports/test")


@pytest.mark.parametrize("key,value", [("generations", True), ("variants", []), ("max_new_tokens", -1)])
def test_invalid_study_parameters_fail_before_output(tmp_path, monkeypatch, key, value):
    path, cfg = fixture_config(tmp_path, monkeypatch)
    cfg[key] = value
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="invalid study config"):
        study.initialize(path)
    assert not Path("reports").exists()


def test_judge_alias_with_identical_weights_is_rejected(tmp_path, monkeypatch):
    path, cfg = fixture_config(tmp_path, monkeypatch)
    Path("models/owned__judge/model.safetensors").write_bytes(Path("models/owned__base/model.safetensors").read_bytes())
    with pytest.raises(ValueError, match="same weight-file content"):
        study.initialize(path)
    assert not Path("reports/test/plan.json").exists()


def test_target_parse_failure_cannot_change_source_ranking_population(tmp_path, monkeypatch):
    path, _, out = completed(tmp_path, monkeypatch)
    evidence = json.loads((out / "generations.json").read_text())
    judgments = json.loads((out / "judgments.json").read_text())
    item = next(r for r in evidence["items"] if r["split"] == "heldout" and r["model_id"] == "owned/variant")
    row = next(r for r in judgments["items"] if r["id"] == item["id"])
    row.update(label=None, status="invalid_label")
    study.private_json(out / "judgments.json", judgments)
    with pytest.raises(ValueError, match="do not select complete cases"):
        analyze(path)
    assert not (out / "summary.json").exists()


def test_evidence_cartesian_keys_and_frozen_splits_are_enforced(tmp_path, monkeypatch):
    path, _, out = completed(tmp_path, monkeypatch)
    _, plan, ids, _ = study.frozen(path)
    evidence = json.loads((out / "generations.json").read_text())
    evidence["items"][0]["split"] = "another_split"
    with pytest.raises(ValueError, match="split/category"):
        validate_evidence(evidence, plan, ids)
    evidence["items"][0]["split"] = "development"
    evidence["items"][-1] = evidence["items"][0]
    with pytest.raises(ValueError, match="duplicate generation key"):
        validate_evidence(evidence, plan, ids)


def test_frozen_rubric_and_plan_selections_cannot_be_changed(tmp_path, monkeypatch):
    path, cfg = fixture_config(tmp_path, monkeypatch)
    study.initialize(path)
    rubric = Path(cfg["independent_judge"]["rubric"])
    original = rubric.read_text()
    rubric.write_text('{"prompt": "Changed rubric"}')
    with pytest.raises(ValueError, match="rubric changed"):
        study.frozen(path)
    rubric.write_text(original)
    plan = json.loads(Path("reports/test/plan.json").read_text())
    plan["behaviors"][0]["split"] = "heldout"
    study.private_json(Path("reports/test/plan.json"), plan)
    with pytest.raises(ValueError, match="behavior selection"):
        study.frozen(path)


def test_concurrent_writers_are_rejected(tmp_path, monkeypatch):
    import fcntl
    path, _ = fixture_config(tmp_path, monkeypatch)
    study.initialize(path)
    with Path("reports/test/.study.lock").open("r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="another stage"):
            study.search(path)


def test_private_writer_repairs_stale_temp_permissions(tmp_path):
    path = tmp_path / "data.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text("old")
    tmp.chmod(0o666)
    study.private_json(path, {"benign": True})
    assert path.stat().st_mode & 0o777 == 0o600


def test_blank_audit_never_becomes_completed_human_validation(tmp_path, monkeypatch):
    path, _, out = completed(tmp_path, monkeypatch)
    study.audit(path)
    with pytest.raises(ValueError, match="no supplied labels"):
        score_audit(path, labeler="Tester", labeler_kind="human")
    assert not (out / "human_audit.json").exists()
    sheet = list(csv.DictReader((out / "human_labels.csv").open()))
    with (out / "human_labels.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "human_label", "notes"])
        writer.writeheader()
        writer.writerows({**r, "human_label": "0"} for r in sheet)
    score_audit(path, labeler="synthetic-judge", labeler_kind="model")
    assert not (out / "human_audit.json").exists()
    score_audit(path, labeler="Synthetic Test Person", labeler_kind="human")
    human = json.loads((out / "human_audit.json").read_text())
    assert human["human_validation_complete"] is True
    summary = json.loads(analyze(path).read_text())
    assert summary["human_audit"]["labeler"] == "Synthetic Test Person"


def test_authorization_date_format_is_checked(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"id": "a", "operator": "Tester", "scope": ["owned/a"],
                                "basis": "owned", "authorized_on": "not-a-date"}))
    with pytest.raises(jsonschema.ValidationError):
        load_record(path)


def test_direct_record_cannot_authorize_substring_matches():
    record = AuthorizationRecord(id="a", operator="Tester", scope="owned/abc", basis="owned", authorized_on="2026-11-08")
    assert not is_authorized("owned/a", record)


def test_no_silent_metric_substitution_or_unverified_attack_execution():
    from lasa.pipeline.predict import effective_similarity
    from lasa.attacks.refusal_direction import run_refusal_direction_on_base, ablation_hooks
    profile = {"cka": {"mean": 0.9, "per_layer": [0.9, 0.9]}}
    with pytest.raises(ValueError, match="no CKA fallback"):
        effective_similarity({"primitive": "gcg"}, profile, "refcos_only")
    with pytest.raises(ValueError, match="unknown similarity"):
        effective_similarity({"primitive": "gcg"}, profile, "typo")
    with pytest.raises(NotImplementedError, match="verified reference wrapper"):
        run_refusal_direction_on_base(None, None, [], [], [], None)
    with pytest.raises(NotImplementedError, match="upstream wrapper"):
        ablation_hooks(None, None)


def test_contrast_count_cannot_silently_exceed_published_data():
    from lasa.benchmarks import load_contrast_pairs
    with pytest.raises(ValueError, match="supported by both"):
        load_contrast_pairs(1000000)


def test_packaged_authorization_schema_matches_documented_contract():
    from lasa.authorization import authorization_schema
    root = Path(__file__).resolve().parents[1]
    assert authorization_schema() == json.loads((root / "configs/authorization.schema.json").read_text())
