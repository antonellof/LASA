#!/usr/bin/env python3
"""Write the aggregate numbers the manuscript cites for the v3 studies.

Reads the gitignored study evidence under reports/ and writes aggregates only:
rates, contrasts, p-values, and counts. No suffix, prompt, or completion text
is read into the output. Rerun after the human audit and the final analyze.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUDIES = {"granite41-prompt-transfer-v1": "study-granite41-v1", "qwen15-prompt-transfer-v3": "study-qwen15-v3"}
KEEP_TOP = ("study_id", "protocol_status", "plan_digest", "evidence_digest", "analysis_code_digest", "judge_model",
            "judge_digest", "primary_family", "decision_rules", "development_gates", "classifier_validation",
            "human_audits", "inter_annotator_agreement", "n_expected_generations", "invalid_or_missing_labels",
            "budget_exhausted_generations", "max_source_loss_reproduction_gap", "heldout_behaviors_complete",
            "analysis_environment_differences")


def prioritization(entry: dict) -> dict:
    block = entry.get("source_only_prioritization")
    if not block:
        return {}
    keep = ("role", "utility", "selected_mean_delta", "random_selection_expectation", "p_one_sided")
    return {name: {k: block[name][k] for k in keep} for name in ("source_uplift", "loss_proxy")}


def model_audit(directory: Path, generations: list, heldout: set) -> dict:
    """Aggregate the model-labeled audits (ADR-0025). Counts and rates only; no label is keyed by item."""
    paths = sorted(directory.glob("independent_audit_*.json"))
    if not paths:
        return {}
    audits = [json.loads(path.read_text()) for path in paths]
    classifier = {r["id"]: r["label"] for r in json.loads((directory / "judgments.json").read_text())["items"]}
    rows = {r["id"]: r for r in generations}
    labelers = []
    for audit in audits:
        cells = audit["design_weighted"]["weighted_confusion"]
        negatives = cells["classifier0_labeler1"] + cells["classifier0_labeler0"]
        labelers.append({
            "labeler": audit["labeler"], "labeler_kind": audit["labeler_kind"], "n_labeled": audit["n_labeled"],
            "sample_confusion": audit["sample_confusion"],
            "precision": audit["design_weighted"]["precision"], "recall": audit["design_weighted"]["recall"],
            "false_omission_rate": cells["classifier0_labeler1"] / negatives if negatives else None,
            "classifier_positive_prevalence": audit["design_weighted"]["classifier_positive_prevalence"],
            "labeler_positive_prevalence": audit["design_weighted"]["labeler_positive_prevalence"],
            "sample_agreement": audit["sample_agreement"],
        })
    out = {"labelers": labelers, "status": "post hoc model audit; does not complete the frozen human criterion"}
    if len(audits) >= 2:
        first, second = audits[0]["labels"], audits[1]["labels"]
        shared = [i for i in first if i in second]
        out["inter_labeler_agreement"] = sum(first[i] == second[i] for i in shared) / len(shared)
        out["n_shared"] = len(shared)
    # Classifier positives in the sample that each labeler confirmed, by checkpoint and by condition.
    breakdown: dict = collections.defaultdict(lambda: collections.Counter())
    for audit in audits:
        for item, label in audit["labels"].items():
            if classifier.get(item) != 1:
                continue
            row = rows[item]
            for key in (f"model|{row['model_id']}", f"condition|{row['condition']}"):
                breakdown[key][f"confirmed_{audit['labeler']}"] += label
                breakdown[key][f"n_{audit['labeler']}"] += 1
    out["classifier_positives_confirmed"] = {k: dict(v) for k, v in sorted(breakdown.items())}
    return out


def build(study_id: str) -> dict:
    directory = ROOT / "reports" / study_id
    summary = json.loads((directory / "summary.json").read_text())
    generations = json.loads((directory / "generations.json").read_text())["items"]
    attacks = json.loads((directory / "source_attacks.json").read_text())["attacks"]
    plan = json.loads((directory / "plan.json").read_text())
    heldout = set(summary["analyzed_behavior_ids"])
    counts: dict = collections.defaultdict(lambda: collections.Counter())
    for row in generations:
        if row["behavior_id"] not in heldout:
            continue
        c = counts[row["model_id"]]
        c[f"budget_censored_{row['condition']}"] += bool(row["budget_censored"])
        if row["condition"] == "gcg":
            c["gcg_generations"] += 1
            c["gcg_suffix_ids_match_source"] += row["suffix_ids_match_source"] is True
    out = {k: summary[k] for k in KEEP_TOP if k in summary}
    out["tool_versions"] = {k: v for k, v in plan.get("versions", {}).items()
                            if k in ("nanogcg", "torch", "transformers", "numpy", "scipy")}
    losses = sorted(a["evaluated_source_loss"] for a in attacks)
    gaps = sorted(a["evaluated_source_loss"] - a["source_loss"] for a in attacks)
    absolute = sorted(abs(g) for g in gaps)
    out["source_search"] = {"n_behaviors": len(attacks), "median_evaluated_loss": losses[len(losses) // 2],
                            "max_evaluated_loss": losses[-1],
                            "n_probability_proxy_above_0.9": sum(a["source_probability_proxy"] > 0.9 for a in attacks),
                            "wall_hours": sum(a["search_wall_seconds"] for a in attacks) / 3600,
                            "loss_reproduction_gap": {
                                "n_above_0.001": sum(g > 0.001 for g in absolute),
                                "n_recomputed_higher": sum(g > 0 for g in gaps),
                                "median_absolute": (absolute[len(absolute) // 2 - 1] + absolute[len(absolute) // 2]) / 2,
                                "max_absolute": absolute[-1]}}
    out["model_audit"] = model_audit(directory, generations, heldout)
    out["results"] = {}
    for model_id, entry in summary["results"].items():
        out["results"][model_id] = {
            "evaluation_role": entry["evaluation_role"],
            "classifier_positive_by_condition": entry["classifier_positive_by_condition"],
            "effects": entry["effects"],
            "decision": entry.get("decision"),
            "development_headroom": entry.get("development_headroom"),
            "prioritization": prioritization(entry),
            "spearman_source_uplift": entry.get("source_uplift_vs_target_uplift"),
            "spearman_loss_proxy": entry.get("source_loss_proxy_vs_target_uplift"),
            "heldout_counts": dict(counts[model_id]),
        }
    return out


if __name__ == "__main__":
    for study_id, folder in STUDIES.items():
        target = ROOT / "experiments/results" / folder / "aggregate_results.json"
        target.write_text(json.dumps(build(study_id), indent=2, sort_keys=True) + "\n")
        print("wrote", target.relative_to(ROOT))
