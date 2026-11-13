"""Score supplied labels on original-generation audits; never create annotations."""
from __future__ import annotations

import csv
import io
import json
import re

from lasa.judge import judge_human_agreement
from lasa.study import digest, frozen, private_json, locked_stage
from lasa.study_validation import validate_evidence


def audit_output_name(sheet: str, labeler_kind: str) -> str:
    match = re.fullmatch(r"human_labels(?:_(\d+))?\.csv", sheet)
    if not match:
        raise ValueError("label sheets are named human_labels.csv or human_labels_<n>.csv")
    stem = "human_audit" if labeler_kind == "human" else "independent_audit"
    return f"{stem}.json" if match.group(1) is None else f"{stem}_{match.group(1)}.json"


def weighted_estimates(pairs, weights):
    """Design-weighted classifier accuracy against labeler judgments.

    pairs: (classifier, labeler) per labeled item. weights: 1 / inclusion probability.
    """
    cells = {f"classifier{c}_labeler{h}": 0.0 for c in (0, 1) for h in (0, 1)}
    for (c, h), w in zip(pairs, weights):
        cells[f"classifier{c}_labeler{h}"] += w
    total = sum(cells.values())
    tp, fp = cells["classifier1_labeler1"], cells["classifier1_labeler0"]
    fn, tn = cells["classifier0_labeler1"], cells["classifier0_labeler0"]

    def ratio(a, b):
        return a / b if b else None

    return {
        "weighted_confusion": cells,
        "labeler_positive_prevalence": ratio(tp + fn, total),
        "classifier_positive_prevalence": ratio(tp + fp, total),
        "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
        "specificity": ratio(tn, tn + fp), "agreement": ratio(tp + tn, total),
    }


@locked_stage
def score_audit(config_path, *, labeler, labeler_kind, sheet="human_labels.csv"):
    if not labeler or labeler_kind not in {"human", "model"}:
        raise ValueError("an identified labeler and explicit human/model kind are required")
    destination = audit_output_name(sheet, labeler_kind)
    _, plan, ids, out = frozen(config_path, allow_analysis_revision=True, analysis_only=True)
    evidence = json.loads((out / "generations.json").read_text())
    if evidence["plan_digest"] != digest(plan):
        raise ValueError("audit evidence belongs to another frozen plan")
    validate_evidence(evidence, plan, ids, require_complete=True)
    items_sheet = json.loads((out / "audit_items.json").read_text())
    judged = json.loads((out / "judgments.json").read_text())
    if items_sheet["evidence_digest"] != digest(evidence) or judged["evidence_digest"] != digest(evidence):
        raise ValueError("audit or judgments do not refer to the current exact evidence")
    wanted = {r["id"] for r in items_sheet["items"]}
    if len(wanted) != len(items_sheet["items"]):
        raise ValueError("duplicate audit IDs")
    actual = {r["id"] for r in evidence["items"]}
    if not wanted <= actual:
        raise ValueError("audit contains unknown generation IDs")
    design_file = out / "audit_design.json"
    if design_file.exists():
        design = json.loads(design_file.read_text())
        if design["evidence_digest"] != digest(evidence):
            raise ValueError("audit design does not refer to the current evidence")
        inclusion = {i: design["strata"][s]["inclusion_probability"] for i, s in design["item_stratum"].items()}
        stratum_of = design["item_stratum"]
    else:  # audits drawn before the design file existed were simple random samples
        inclusion = {i: items_sheet["inclusion_probability"] for i in wanted}
        stratum_of = {i: "all" for i in wanted}
        design = {"design": "simple_random", "strata": {"all": {"inclusion_probability": items_sheet["inclusion_probability"]}}}
    text = (out / sheet).read_text()
    labels, seen = {}, set()
    for r in csv.DictReader(io.StringIO(text)):
        identifier = r["id"]
        if identifier not in wanted or identifier in seen:
            raise ValueError("unknown or duplicate supplied label ID")
        seen.add(identifier)
        value = r["human_label"].strip()
        if value not in {"", "0", "1"}:
            raise ValueError("labels must be blank, 0 or 1")
        if value:
            labels[identifier] = int(value)
    if not labels:
        raise ValueError("no supplied labels; a human audit cannot be filled automatically")
    classifier = {r["id"]: r["label"] for r in judged["items"]}
    scored = [i for i in labels if classifier.get(i) in (0, 1)]
    pairs = [(classifier[i], labels[i]) for i in scored]
    confusion = {f"classifier{c}_labeler{h}": sum(a == c and b == h for a, b in pairs)
                 for c in (0, 1) for h in (0, 1)}
    per_stratum = {}
    for name in design["strata"]:
        members = [i for i in scored if stratum_of.get(i) == name]
        per_stratum[name] = {f"classifier{c}_labeler{h}": sum(classifier[i] == c and labels[i] == h for i in members)
                             for c in (0, 1) for h in (0, 1)}
    payload = {
        "evidence_digest": digest(evidence), "audit_digest": digest(items_sheet),
        "label_file": sheet, "label_file_digest": digest(text),
        "labeler": labeler, "labeler_kind": labeler_kind,
        "is_human_spotcheck": labeler_kind == "human", "n_audit_items": len(wanted),
        "n_labeled": len(labels), "n_with_valid_classifier": len(pairs),
        "human_validation_complete": labeler_kind == "human" and len(labels) == len(wanted),
        "sample_confusion": confusion, "per_stratum_confusion": per_stratum,
        "sample_agreement": judge_human_agreement([a for a, _ in pairs], [b for _, b in pairs]) if pairs else None,
        "design": design["design"],
        "design_weighted": weighted_estimates(pairs, [1.0 / inclusion[i] for i in scored]) if pairs else None,
        "labels": labels,
        "sampling": items_sheet["sampling"],
        "interpretation": ("Sample agreement describes the drawn items. Design-weighted rates estimate the "
                           "population of exact generations. Labeler identity/kind is an operator declaration."),
    }
    path = out / destination
    private_json(path, payload)
    return path
