"""Manuscript metadata, citations and plotted measurements must stay consistent."""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"


def builder():
    spec = importlib.util.spec_from_file_location("paper_builder", PAPER / "build_arxiv.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_title_abstract_and_citation_metadata_agree():
    mod = builder()
    title, author, date, abstract, body = mod.split_front_matter((PAPER / "PAPER.md").read_text())
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text())
    assert title == citation["preferred-citation"]["title"]
    assert author == "Antonello Fratepietro"
    assert "10,800 exact generations" in abstract
    assert "LASA (Lineage Attack Surface Analyzer)" in abstract
    assert "## 1. Introduction" in body
    from datetime import datetime
    assert datetime.strptime(date, "%d %B %Y").year >= 2026
    repo = "https://github.com/antonellof/lineage-attack-surface-analyzer"
    meta = mod.build_metadata_md(title, author, date, abstract, body,
                                 email="antonello.f@gmail.com", repository=repo)
    assert "abstract: |" in meta and "title:" in meta
    assert r"\nolinkurl{" + repo + "}" in meta
    assert "antonello.f@gmail.com" in meta
    assert "[Code and data]" not in meta


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc is a document-build dependency")
def test_every_citation_has_a_bibliographic_entry():
    entries = json.loads(subprocess.check_output([
        "pandoc", "--from=biblatex", "--to=csljson", str(PAPER / "references.bib")
    ], text=True))
    ids = {r["id"] for r in entries}
    cites = set(re.findall(r"(?<![\w.])@([a-zA-Z][a-zA-Z0-9_-]+)", (PAPER / "PAPER.md").read_text()))
    assert cites <= ids
    assert len(cites) >= 10
    assert all(r.get("URL") for r in entries)
    assert all(r.get("author") for r in entries)


def test_historical_counts_distinguish_groups_lineages_and_roles():
    inv = json.loads((ROOT / "experiments/evidence_inventory.json").read_text())
    assert inv["historical"]["n_source_groups"] == 5
    assert inv["historical"]["n_model_lineages"] == 4
    assert inv["historical"]["n_distinct_evaluated_ids"] == 19
    assert inv["historical"]["n_target_ids"] == 14
    assert inv["pilot"]["n_evaluated_ids"] == 2
    assert inv["pilot"]["n_model_lineages"] == 1
    assert inv["prospective"]["n_model_lineages"] == 2
    assert inv["prospective"]["n_generations_planned"] == 6400


def test_plot_metadata_matches_pilot_summary():
    root = ROOT / "experiments/results/study-qwen15-pilot"
    m = json.loads((root / "measurements.json").read_text())
    s = json.loads((root / "summary.json").read_text())
    assert m["evidence_digest"] == s["evidence_digest"]
    assert sum(r["n"] for r in m["rows"]) == s["n_expected_generations"] == 24
    assert sum(r["budget_exhausted"] for r in m["rows"]) == s["budget_exhausted_generations"] == 11
    assert all(len(r["generation_tokens"]) == r["n"] for r in m["rows"])
    assert sum(r["unscored"] for r in m["rows"]) == s["invalid_or_missing_labels"] == 0
    assert m["human_labels_available"] is False


def _aggregate(folder):
    return json.loads((ROOT / "experiments/results" / folder / "aggregate_results.json").read_text())


def _table_rows(caption_start):
    lines = (PAPER / "PAPER.md").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Table: " + caption_start))
    rows = []
    for line in lines[start + 4:]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def test_confirmatory_inventory_matches_aggregates():
    inv = json.loads((ROOT / "experiments/evidence_inventory.json").read_text())["confirmatory"]
    assert inv["n_studies"] == 2 and inv["n_model_lineages"] == 2
    assert inv["n_target_entries"] == 7 and inv["n_generations"] == 10800
    assert inv["human_audit_complete"] is False


def test_results_tables_match_committed_aggregates():
    """Section 6 rates and audit numbers must equal the aggregate files, to two decimals."""
    order = [("study-qwen15-v3", ["Qwen/Qwen2.5-1.5B-Instruct", "Qwen/Qwen2.5-Coder-1.5B-Instruct",
                                  "Qwen/Qwen2.5-Math-1.5B-Instruct", "SakanaAI/TinySwallow-1.5B-Instruct",
                                  "katanemo/Arch-Router-1.5B"]),
             ("study-granite41-v1", ["ibm-granite/granite-4.1-3b", "hirundo-io/granite-4.1-3b-hardened",
                                     "ertghiu256/Granite-4.1-3b-coder", "wangzhang/granite-4.1-3b-abliterated"])]
    models = [(folder, mid) for folder, mids in order for mid in mids]
    rates = _table_rows("Classifier-positive rate by condition")
    contrasts = _table_rows("Held-out contrasts of the source GCG suffix")
    assert len(rates) == len(models)
    for row, (folder, mid) in zip(rates, models):
        observed = _aggregate(folder)["results"][mid]["classifier_positive_by_condition"]
        for cell, key in zip(row[2:6], ("bare", "random_suffix", "mismatched_gcg", "gcg")):
            assert cell == f"{observed[key]:.2f}", (mid, key)
    targets = [m for m in models if m[1] not in ("Qwen/Qwen2.5-1.5B-Instruct", "ibm-granite/granite-4.1-3b")]
    assert len(contrasts) == len(targets)
    for row, (folder, mid) in zip(contrasts, targets):
        effects = _aggregate(folder)["results"][mid]["effects"]
        for cell, key in zip((row[1], row[3], row[5]), ("bare", "random_suffix", "mismatched_gcg")):
            effect = effects[f"gcg_minus_{key}"]
            low, high = effect["ci95"]
            assert cell == f"{effect['mean']:.2f} ({low:.2f}, {high:.2f})".replace("-0.00", "0.00"), (mid, key)
    audit = _table_rows("Model audit of the classifier")
    labelers = [l for folder, _ in order for l in _aggregate(folder)["model_audit"]["labelers"]]
    assert len(audit) == len(labelers) == 4
    for row, labeler in zip(audit, labelers):
        confirmed = labeler["sample_confusion"]["classifier1_labeler1"]
        positives = confirmed + labeler["sample_confusion"]["classifier1_labeler0"]
        assert row[2] == f"{confirmed} of {positives}"
        assert row[3] == f"{labeler['precision']:.2f}"
        assert row[4] == f"{labeler['false_omission_rate']:.2f}"
        assert row[5] == f"{labeler['recall']:.2f}"
