#!/usr/bin/env python3
"""Rebuild figures from committed numerical artifacts; no models or completion text."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "experiments/results/study-qwen15-pilot/measurements.json"


def figures(data: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "pdf.fonttype": 42})
    # Group six steps into three phases; all connectors stay on one horizontal line.
    fig, ax = plt.subplots(figsize=(8.2, 2.65), dpi=220)
    ax.set(xlim=(-0.1, 8.2), ylim=(-0.1, 2.65))
    ax.axis("off")
    width, height, y = 2.35, 2.35, 0.1
    phases = [
        (0.0, "SOURCE ONLY", "Source preparation", [
            "1  Freeze protocol and splits",
            "2  Optimize with nanoGCG",
            "3  Commit plan; lock rules",
        ], "No target feedback", "#edf3fa", "#355d80"),
        (2.85, "AUTHORIZED TARGETS", "Controlled evaluation", [
            "4  Generate paired conditions",
            "Bare, random, mismatched, GCG",
            "Retain exact sampled outputs",
        ], "No target-side optimization", "#f0f5f0", "#49714d"),
        (5.7, "INDEPENDENT ASSESSMENT", "Judging and analysis", [
            "5  Classifier and human audit",
            "6  Estimate held-out effects",
            "Assess source-only rankings",
        ], "Frozen decision rules", "#f7f2ea", "#856341"),
    ]
    for x, label, title, lines, note, face, edge in phases:
        ax.add_patch(FancyBboxPatch((x, y), width, height,
                                   boxstyle="round,pad=0.035,rounding_size=0.06",
                                   facecolor=face, edgecolor=edge, linewidth=1.1))
        ax.text(x + width / 2, 2.16, label, ha="center", color=edge,
                fontsize=7.5, weight="bold")
        ax.text(x + width / 2, 1.83, title, ha="center", fontsize=10.5, weight="bold")
        for line_y, line in zip((1.40, 1.06, 0.72), lines):
            ax.text(x + width / 2, line_y, line, ha="center", fontsize=9)
        ax.text(x + width / 2, 0.31, note, ha="center", fontsize=7.8, color=edge)
    for start, end in ((width + 0.06, 2.79), (2.85 + width + 0.06, 5.64)):
        ax.add_patch(FancyArrowPatch((start, y + height / 2), (end, y + height / 2),
                                    arrowstyle="-|>", mutation_scale=13,
                                    linewidth=1.4, color="#4c5964"))
    fig.tight_layout(pad=0.25)
    fig.savefig(out / "study_design.png", bbox_inches="tight")
    plt.close(fig)

    fig = plt.figure(figsize=(9, 6.1), dpi=180)
    gs = fig.add_gridspec(2, 2)
    ax = fig.add_subplot(gs[0, :])
    names = sorted({r["model_id"] for r in data["rows"]})
    colors = ["#235789", "#b64b32"]
    conditions = ["bare", "random_suffix", "gcg"]
    for m, mid in enumerate(names):
        label = "Source (Qwen)" if "Qwen/" in mid else "Child (TinySwallow)"
        for c, condition in enumerate(conditions):
            rows = [r for r in data["rows"] if r["model_id"] == mid and r["condition"] == condition]
            for row in rows:
                ys = row["generation_tokens"]
                offsets = np.linspace(-0.035, 0.035, len(ys))
                ax.scatter(c + (m - 0.5) * 0.24 + offsets, ys, color=colors[m], s=34,
                           marker="o" if row["split"] == "development" else "^",
                           label=label if c == 0 and row["split"] == "development" else None)
    ax.axhline(data["max_new_tokens"], color="gray", ls="--", lw=1)
    ax.set(xticks=range(3), xticklabels=["Bare", "Random suffix", "Source GCG"],
           ylabel="Generated tokens", ylim=(0, data["max_new_tokens"] + 16),
           title="(a) Exact generation lengths (24 outputs; circles: development, triangles: held-out)")
    # Outside the axes so no plotted generation is hidden behind it.
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=9, frameon=False)

    ax = fig.add_subplot(gs[1, 0])
    for m, mid in enumerate(names):
        rows = [r for r in data["rows"] if r["model_id"] == mid]
        total = sum(r["n"] for r in rows)
        exhausted = sum(r["budget_exhausted"] for r in rows)
        ax.bar(m, exhausted, color=colors[m], width=0.55)
        ax.text(m, exhausted + 0.25, f"{exhausted}/{total}", ha="center")
    ax.set(xticks=[0, 1], xticklabels=["Source", "Child"], ylim=(0, 12),
           ylabel="Budget-exhausted outputs", title="(b) Budget exhaustion")

    ax = fig.add_subplot(gs[1, 1])
    optim = data["source_optimization"]
    ax.bar(range(len(optim)), [r["best_loss"] for r in optim], color="#527d54", width=0.55)
    for i, r in enumerate(optim):
        ax.text(i, r["best_loss"] + 0.08, f"{r['best_loss']:.3f}", ha="center")
    ax.set(xticks=range(len(optim)), xticklabels=[r["behavior_id"].replace("jailbreakbench-", "JBB-") for r in optim],
           ylim=(0, max(r["best_loss"] for r in optim) * 1.3), ylabel="Best source target loss",
           title="(c) Source search (5 steps)")
    fig.tight_layout()
    fig.savefig(out / "pilot_diagnostics.png", bbox_inches="tight")
    plt.close(fig)


# Fixed condition colors (validated for color-vision deficiency); shapes repeat the identity for print.
CONDITIONS = {"gcg": ("Source GCG suffix", "#2a78d6", "o"), "bare": ("Bare request", "#eb6834", "s"),
              "random_suffix": ("Random suffix", "#1baf7a", "^"), "mismatched_gcg": ("Mismatched suffix", "#eda100", "D")}
STUDY_ROWS = {
    "study-qwen15-v3": ("Qwen2.5 1.5B", [
        ("Qwen/Qwen2.5-1.5B-Instruct", "Source"), ("Qwen/Qwen2.5-Coder-1.5B-Instruct", "Coder (sibling)"),
        ("Qwen/Qwen2.5-Math-1.5B-Instruct", "Math (sibling)"), ("SakanaAI/TinySwallow-1.5B-Instruct", "TinySwallow (child)"),
        ("katanemo/Arch-Router-1.5B", "Arch-Router (child, task control)")]),
    "study-granite41-v1": ("Granite 4.1 3B", [
        ("ibm-granite/granite-4.1-3b", "Source"), ("hirundo-io/granite-4.1-3b-hardened", "Hardened (child)"),
        ("ertghiu256/Granite-4.1-3b-coder", "Coder (child)"),
        ("wangzhang/granite-4.1-3b-abliterated", "Abliterated (child, headroom limited)")]),
}


def study_results(out: Path) -> None:
    """Held-out classifier-positive rates and GCG contrasts for the two confirmatory studies."""
    root = HERE.parent / "experiments/results"
    ink, muted, grid = "#1f1f1e", "#6b6a64", "#e4e3dd"
    fig, axes = plt.subplots(2, 2, figsize=(9.4, 5.4), dpi=200, sharex="col",
                             gridspec_kw={"height_ratios": [5, 4], "wspace": 0.08, "hspace": 0.22})
    for r, (folder, (family, rows)) in enumerate(STUDY_ROWS.items()):
        results = json.loads((root / folder / "aggregate_results.json").read_text())["results"]
        ys = np.arange(len(rows))[::-1]
        left, right = axes[r]
        for y, (model_id, _) in zip(ys, rows):
            entry = results[model_id]
            rates = entry["classifier_positive_by_condition"]
            left.plot([min(rates.values()), max(rates.values())], [y, y], color=grid, lw=2.2, zorder=1,
                      solid_capstyle="round")
            for key in ("bare", "random_suffix", "mismatched_gcg", "gcg"):
                label, color, marker = CONDITIONS[key]
                left.scatter(rates[key], y, s=58, color=color, marker=marker, edgecolor="white", linewidth=0.9,
                             zorder=3 if key == "gcg" else 2, label=label if (r == 0 and y == ys[0]) else None)
            for offset, key in zip((0.24, 0.0, -0.24), ("bare", "random_suffix", "mismatched_gcg")):
                effect = entry["effects"][f"gcg_minus_{key}"]
                label, color, marker = CONDITIONS[key]
                low, high = effect["ci95"]
                right.plot([low, high], [y + offset, y + offset], color=color, lw=2, solid_capstyle="round", zorder=2)
                right.scatter(effect["mean"], y + offset, s=40, color=color, marker=marker, edgecolor="white",
                              linewidth=0.8, zorder=3, label=f"GCG minus {label.lower()}" if (r == 0 and y == ys[0]) else None)
        right.axvline(0, color=muted, lw=1, zorder=1)
        for ax in (left, right):
            ax.set(yticks=ys, ylim=(-0.6, len(rows) - 0.4))
            ax.grid(axis="x", color=grid, lw=0.8)
            ax.set_axisbelow(True)
            ax.tick_params(length=0, colors=muted, labelsize=8.5)
            for side in ("left", "bottom"):
                ax.spines[side].set_color(grid)
        left.set_yticklabels([label for _, label in rows], color=ink, fontsize=9)
        right.set_yticklabels([])
        left.set_xlim(-0.03, 1.0)
        right.set_xlim(-0.2, 1.0)
        left.set_ylabel(family, color=ink, fontsize=10, weight="bold", labelpad=8)
    axes[0, 0].set_title("(a) Classifier-positive rate by condition", fontsize=10, color=ink, loc="left", pad=30)
    axes[0, 1].set_title("(b) Uplift of the GCG suffix over each control, 95% interval", fontsize=10, color=ink,
                         loc="left", pad=30)
    for ax in axes[0]:
        ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=2, fontsize=8, frameon=False, handletextpad=0.3,
                  columnspacing=1.0, borderaxespad=0.2, labelcolor=ink)
    axes[1, 0].set_xlabel("Rate on 30 held-out behaviors (150 generations)", color=muted, fontsize=9)
    axes[1, 1].set_xlabel("Difference in rate", color=muted, fontsize=9)
    fig.savefig(out / "study_results.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)



def benign_profiles(out: Path) -> dict:
    """Reuse only the separately collected benign CKA, never historical outcomes."""
    root = HERE.parent
    groups = yaml.safe_load((root / "experiments/lineage.yaml").read_text())["families"]
    fig, axes = plt.subplots(3, 2, figsize=(8, 8.5), dpi=180, sharex=True, sharey=True)
    sources, targets = set(), set()
    inventory = []
    lowest = min(min(row["profile"]["cka"]["per_layer"])
                 for key in groups
                 for row in json.loads((root / "experiments/results" / key / "similarity.json").read_text())["variants"])
    floor = min(0.5, np.floor(lowest * 10) / 10)  # never clip a recorded value
    for ax, (key, group) in zip(axes.flat, groups.items()):
        directory = root / "experiments/results" / key
        sim = json.loads((directory / "similarity.json").read_text())
        manifest = json.loads((directory / "manifest.json").read_text())
        sources.update(m["model_id"] for m in manifest["models"] if m["role"] == "base")
        targets.update(m["model_id"] for m in manifest["models"] if m["role"] == "variant")
        means = []
        for row in sim["variants"]:
            profile = row["profile"]["cka"]
            values = np.asarray(profile["per_layer"], dtype=float)
            if not np.isfinite(values).all() or not np.isclose(values.mean(), profile["mean"]):
                raise ValueError("benign CKA profile does not match its reported summary")
            short = group["variants"][row["model_id"]]["short"].replace("-Instruct", "-It")
            ax.plot(np.linspace(0, 1, len(values)), values, label=short, linewidth=1.5)
            means.append(float(profile["mean"]))
        ax.set(title=group["label"], ylim=(floor, 1.02))
        ax.grid(alpha=0.25, linestyle="--")
        ax.legend(fontsize=8, frameon=False, loc="lower left")
        inventory.append({"source_group": key, "model_lineage": group["model_lineage"],
                          "n_targets": len(means), "n_benign_probes": sim["n_probes"],
                          "mean_cka_range": [min(means), max(means)]})
    for ax in axes[:, 0]:
        ax.set_ylabel("Linear CKA")
    for ax in axes[-1]:
        ax.set_xlabel("Relative depth (embedding to final output)")
    axes.flat[-1].axis("off")
    axes.flat[-1].text(0.05, 0.8, "Descriptive measurements only\n\n240 benign probes per pair\n14 source-target pairs\n5 source-checkpoint groups\n4 model lineages\n\nNo attack outcomes are plotted.",
                       transform=axes.flat[-1].transAxes, va="top", fontsize=10)
    fig.tight_layout()
    fig.savefig(out / "benign_profiles.png", bbox_inches="tight")
    plt.close(fig)
    payload = {
        "historical": {"n_source_groups": len(groups),
                       "n_model_lineages": len({g["model_lineage"] for g in groups.values()}),
                       "n_source_ids": len(sources), "n_target_ids": len(targets),
                       "n_distinct_evaluated_ids": len(sources | targets), "groups": inventory,
                       "outcome_status": "superseded; not harmful-compliance evidence",
                       "benign_cka_status": "separately collected descriptive metric; no transfer inference"},
        "pilot": {"n_model_lineages": 1, "n_evaluated_ids": 2, "n_independent_judge_ids": 1,
                  "n_generations": 24, "human_labels_available": False},
        "prospective": prospective_counts(root),
        "confirmatory": confirmatory_counts(root),
    }
    (root / "experiments/evidence_inventory.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def confirmatory_counts(root: Path) -> dict:
    """Completed v3 studies, counted from their committed aggregate result files."""
    by_study, targets, lineages = {}, 0, set()
    for folder in STUDY_ROWS:
        data = json.loads((root / "experiments/results" / folder / "aggregate_results.json").read_text())
        by_study[data["study_id"]] = data["n_expected_generations"]
        targets += len(data["results"]) - 1
        lineages.add(folder.split("-")[1])
    audited = all(json.loads((root / "experiments/results" / folder / "aggregate_results.json").read_text())
                  ["classifier_validation"]["status"] != "pending" for folder in STUDY_ROWS)
    return {"n_studies": len(by_study), "n_model_lineages": len(lineages), "n_source_ids": len(by_study),
            "n_target_entries": targets, "n_generations": sum(by_study.values()),
            "n_generations_by_study": by_study, "human_audit_complete": audited,
            "outcome_status": "classifier-based; frozen human criterion pending; model audit reported (ADR-0025)"}


def prospective_counts(root: Path) -> dict:
    """Plans that were never run: models x behaviors x conditions x replicates.

    The Qwen v2 plan was replaced by v3 and the Llama plan is not scheduled (ADR-0024).
    """
    import sys
    sys.path.insert(0, str(root / "src"))
    from lasa.benchmarks import load_benchmark
    from lasa.study import conditions_for
    planned, sources, targets, lineages = {}, 0, 0, set()
    for name in ("study-qwen15-v2.yaml", "study-llama32-v1.yaml"):
        cfg = yaml.safe_load((root / "experiments" / name).read_text())
        n_categories = len({b.category for b in load_benchmark(cfg["benchmark"]["name"])})
        n_models = 1 + len(cfg["variants"])
        planned[name] = (n_models * n_categories * cfg["benchmark"]["per_category"]
                         * len(conditions_for(cfg)) * cfg["generations"])
        sources, targets = sources + 1, targets + len(cfg["variants"])
        lineages.add(cfg["base"]["model_id"].split("/")[-1].split("-")[0])
    return {"n_study_configs": len(planned), "n_model_lineages": len(lineages), "n_source_ids": sources,
            "n_target_entries": targets, "n_generations_planned": sum(planned.values()),
            "n_generations_planned_by_config": planned, "efficacy_results_reported": False}


if __name__ == "__main__":
    figures(json.loads(DATA.read_text()), HERE / "figures")
    study_results(HERE / "figures")
    benign_profiles(HERE / "figures")
