"""Stage 6: join predictions and measurements, emit findings.

Outputs:
  - findings.json: per-attack predicted score, measured ASR, CI, OWASP category
  - report.md: readable summary, headline correlation, baseline comparison
  - scatter.png: predicted score vs measured ASR, colored by variant
  - manifest.json: model hashes, tool versions, seed, authorization id, timestamp
  - report.json: stage completion artifact
"""

from __future__ import annotations

import datetime as dt
import json
import platform
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

import lasa
from lasa.context import Context

# Map JailbreakBench / HarmBench categories to OWASP Top 10 for LLM Applications (2025).
OWASP_CATEGORY_MAP: dict[str, str] = {
    "Malware/Hacking": "LLM01:2025 Prompt Injection / LLM05:2025 Improper Output Handling",
    "cybercrime_intrusion": "LLM01:2025 Prompt Injection / LLM05:2025 Improper Output Handling",
    "Disinformation": "LLM09:2025 Misinformation",
    "misinformation_disinformation": "LLM09:2025 Misinformation",
    "Privacy": "LLM02:2025 Sensitive Information Disclosure",
    "Fraud/Deception": "LLM01:2025 Prompt Injection",
    "Harassment/Discrimination": "LLM01:2025 Prompt Injection",
    "harassment_bullying": "LLM01:2025 Prompt Injection",
    "Physical harm": "LLM01:2025 Prompt Injection",
    "chemical_biological": "LLM01:2025 Prompt Injection",
    "Economic harm": "LLM01:2025 Prompt Injection",
    "Expert advice": "LLM09:2025 Misinformation",
    "Government decision-making": "LLM09:2025 Misinformation",
    "Sexual/Adult content": "LLM01:2025 Prompt Injection",
}
DEFAULT_OWASP = "LLM01:2025 Prompt Injection"


def map_owasp_category(category: str) -> str:
    return OWASP_CATEGORY_MAP.get(category, DEFAULT_OWASP)


def safe_spearman(
    x: list[float] | np.ndarray,
    y: list[float] | np.ndarray,
    n_perm: int = 1000,
    n_boot: int = 500,
    seed: int = 0,
) -> dict[str, float]:
    """Legacy correlation helper with Monte Carlo permutation p and pair bootstrap CI.

    The zero returned for constant inputs is a legacy compatibility sentinel,
    not an estimated correlation. Use aggregate or study analysis for research.
    """
    xa = np.asarray(x, dtype=np.float64)
    ya = np.asarray(y, dtype=np.float64)
    if xa.size < 2 or np.all(xa == xa[0]) or np.all(ya == ya[0]):
        return {
            "rho": 0.0,
            "p_value": 1.0,
            "perm_p_value": 1.0,
            "ci_low": 0.0,
            "ci_high": 0.0,
            "n": int(xa.size),
        }
    res = spearmanr(xa, ya)
    rho = 0.0 if np.isnan(res.statistic) else float(res.statistic)
    pval = 1.0 if np.isnan(res.pvalue) else float(res.pvalue)

    rng = np.random.default_rng(seed)
    # Two-sided permutation test handling heavy ties cleanly
    exceed = 0
    for _ in range(n_perm):
        perm_y = rng.permutation(ya)
        r_p = spearmanr(xa, perm_y).statistic
        if not np.isnan(r_p) and abs(float(r_p)) >= abs(rho) - 1e-12:
            exceed += 1
    perm_p = float((exceed + 1) / (n_perm + 1))

    # Bootstrap 95% CI over pairs
    boot_rhos = []
    for _ in range(n_boot):
        idx = rng.integers(0, xa.size, size=xa.size)
        xb, yb = xa[idx], ya[idx]
        if np.all(xb == xb[0]) or np.all(yb == yb[0]):
            continue
        rb = spearmanr(xb, yb).statistic
        if not np.isnan(rb):
            boot_rhos.append(float(rb))
    if boot_rhos:
        ci_low, ci_high = [float(v) for v in np.quantile(boot_rhos, [0.025, 0.975])]
    else:
        ci_low, ci_high = rho, rho

    return {
        "rho": rho,
        "p_value": pval,
        "perm_p_value": perm_p,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "n": int(xa.size),
    }


def compute_baselines_and_headline(
    records: list[dict],
    seed: int = 0,
) -> dict[str, dict[str, float]]:
    """Compare LASA transfer prediction score against baselines on (attack, variant) records.

    Reports correlations against both raw measured_asr and control-adjusted
    transfer_delta_asr (measured_asr - no_attack_asr).
    """
    pred = [float(r["predicted_score"]) for r in records]
    measured = [float(r["measured_asr"]) for r in records]
    delta = [float(r.get("transfer_delta_asr", r["measured_asr"])) for r in records]
    base_asr = [float(r["base_asr"]) for r in records]
    base_strength = [float(r["base_strength"]) for r in records]

    headline = safe_spearman(pred, measured, seed=seed)
    headline_delta = safe_spearman(pred, delta, seed=seed)
    base_asr_corr = safe_spearman(base_asr, measured, seed=seed)
    base_asr_delta_corr = safe_spearman(base_asr, delta, seed=seed)
    strength_corr = safe_spearman(base_strength, measured, seed=seed)
    strength_delta_corr = safe_spearman(base_strength, delta, seed=seed)

    rng = np.random.default_rng(seed)
    n = len(measured)
    rand_rhos = []
    if n >= 2 and not np.all(np.asarray(measured) == measured[0]):
        rand_rhos = []
        for _ in range(200):
            perm = rng.random(n)
            r = spearmanr(perm, measured).statistic
            if not np.isnan(r):
                rand_rhos.append(float(r))
        rand_rho = float(np.mean(rand_rhos)) if rand_rhos else 0.0
    else:
        rand_rho = 0.0

    return {
        "predicted_vs_measured": headline,
        "predicted_vs_transfer_delta": headline_delta,
        "baseline_base_only_asr": base_asr_corr,
        "baseline_base_only_asr_delta": base_asr_delta_corr,
        "baseline_base_strength_only": strength_corr,
        "baseline_base_strength_only_delta": strength_delta_corr,
        "baseline_random": {"rho": rand_rho, "p_value": None, "perm_p_value": None,
                            "ci_low": float(np.quantile(rand_rhos, 0.025)) if rand_rhos else None,
                            "ci_high": float(np.quantile(rand_rhos, 0.975)) if rand_rhos else None,
                            "interval_kind": "random-ordering reference band, not a parameter CI", "n": n},
    }


def plot_scatter(variant_records: dict[str, list[dict]], out_path: Path) -> None:
    """Plot predicted transfer score (x) vs measured ASR (y), colored by variant."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.5, 4.5), dpi=150)
    for vid, rows in variant_records.items():
        xs = [r["predicted_score"] for r in rows]
        ys = [r["measured_asr"] for r in rows]
        ax.scatter(xs, ys, label=vid, alpha=0.8, s=42)

    ax.set_xlabel("Predicted Transfer Score")
    ax.set_ylabel("Measured Attack Success Rate (ASR)")
    ax.set_title("LASA Predicted Transfer vs Measured Variant ASR")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)
    ax.grid(True, linestyle="--", alpha=0.4)
    if variant_records:
        ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def collect_environment_versions(extra_tools: list[dict]) -> dict:
    from importlib.metadata import PackageNotFoundError, version as pkg_version

    versions = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "lasa": lasa.__version__,
        "numpy": np.__version__,
    }
    for pkg in ("scipy", "torch", "transformers", "nanogcg", "jsonschema", "pyyaml"):
        try:
            versions[pkg] = pkg_version(pkg)
        except PackageNotFoundError:
            pass
    for t in extra_tools:
        versions[f"wrapped:{t['name']}"] = t["version"]
    return versions


def build_markdown_report(
    run_id: str,
    base_id: str,
    pooled_stats: dict,
    per_variant_stats: dict[str, dict],
    findings: list[dict],
    manifest: dict,
    spotcheck: dict,
) -> str:
    """Format a plain, active-voice Markdown report with no em dashes."""
    lines = [
        f"# LASA (Lineage Attack Surface Analyzer) Findings Report: {run_id}",
        "",
        "Legacy diagnostic report, not validated harmful-compliance evidence. "
        "Constant-input correlations use a zero compatibility sentinel. "
        "Use the controlled study for independent judging and primary inference.",
        "",
        f"- **Base model**: `{base_id}`",
        f"- **Variants evaluated**: {', '.join(f'`{v}`' for v in per_variant_stats)}",
        f"- **Authorization record**: `{manifest['authorization_id']}` ({manifest['authorization_basis']})",
        f"- **Timestamp (UTC)**: `{manifest['timestamp']}`",
        f"- **Seed**: `{manifest['seed']}`",
        "",
        "## Headline Correlation (Pooled)",
        "",
        "| Predictor | Spearman rho (vs ASR) | 95% CI | Perm p-val | Spearman rho (vs Delta ASR) | Perm p-val | N |",
        "| :--- | ---: | :--- | ---: | ---: | ---: | ---: |",
        f"| **LASA Transfer Score (Strength x Similarity, `{manifest.get('similarity_metric')}`)** | **{pooled_stats['predicted_vs_measured']['rho']:.4f}** | [{pooled_stats['predicted_vs_measured']['ci_low']:.2f}, {pooled_stats['predicted_vs_measured']['ci_high']:.2f}] | {pooled_stats['predicted_vs_measured']['perm_p_value']:.4f} | **{pooled_stats['predicted_vs_transfer_delta']['rho']:.4f}** | {pooled_stats['predicted_vs_transfer_delta']['perm_p_value']:.4f} | {pooled_stats['predicted_vs_measured']['n']} |",
        f"| Baseline: Base-only ASR | {pooled_stats['baseline_base_only_asr']['rho']:.4f} | [{pooled_stats['baseline_base_only_asr']['ci_low']:.2f}, {pooled_stats['baseline_base_only_asr']['ci_high']:.2f}] | {pooled_stats['baseline_base_only_asr']['perm_p_value']:.4f} | {pooled_stats['baseline_base_only_asr_delta']['rho']:.4f} | {pooled_stats['baseline_base_only_asr_delta']['perm_p_value']:.4f} | {pooled_stats['baseline_base_only_asr']['n']} |",
        f"| Baseline: Base Continuous Strength Only | {pooled_stats['baseline_base_strength_only']['rho']:.4f} | [{pooled_stats['baseline_base_strength_only']['ci_low']:.2f}, {pooled_stats['baseline_base_strength_only']['ci_high']:.2f}] | {pooled_stats['baseline_base_strength_only']['perm_p_value']:.4f} | {pooled_stats['baseline_base_strength_only_delta']['rho']:.4f} | {pooled_stats['baseline_base_strength_only_delta']['perm_p_value']:.4f} | {pooled_stats['baseline_base_strength_only']['n']} |",
        f"| Baseline: Random Ordering | {pooled_stats['baseline_random']['rho']:.4f} | See JSON reference band | n/a | n/a | n/a | {pooled_stats['baseline_random']['n']} |",
        "",
        "## Per-Variant Correlation",
        "",
        "| Variant | Spearman rho (vs ASR) | Spearman rho (vs Delta ASR) | Mean CKA | Refusal Cosine | No-Attack ASR | Attacked ASR | Transfer Delta ASR |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for vid, vstat in per_variant_stats.items():
        lines.append(
            f"| `{vid}` | {vstat['correlations']['predicted_vs_measured']['rho']:.4f} | "
            f"{vstat['correlations']['predicted_vs_transfer_delta']['rho']:.4f} | "
            f"{vstat['mean_cka']:.4f} | {vstat['refusal_cosine']:.4f} | "
            f"{vstat['mean_no_attack_asr']:.4f} | {vstat['mean_measured_asr']:.4f} | "
            f"{vstat['mean_transfer_delta_asr']:+.4f} |"
        )

    lines.extend(
        [
            "",
            "## Judge Cross-Check Agreement (automated, not a human spot-check)",
            "",
            "The primary ASR label is the refusal-prefix detector. The cross-check is the evaluated "
            "checkpoint prompted with the HarmBench classifier template; it returns 0 whenever the "
            "detector does, so the two can only disagree on detector-positive generations.",
            "",
            f"- **Sampled generations checked**: {spotcheck['n_checked']}",
            f"- **Raw agreement**: {spotcheck['agreement']:.4f}",
            f"- **Cohen's kappa**: {spotcheck['cohens_kappa']:.4f}",
            "",
            "## Per-Attack Findings",
            "",
            "| Variant | Attack ID | Primitive | Pred Score | No-Atk ASR | Measured ASR | Delta ASR | 95% CI | OWASP Category |",
            "| :--- | :--- | :--- | ---: | ---: | ---: | ---: | :--- | :--- |",
        ]
    )
    for f in findings:
        lines.append(
            f"| `{f['variant']}` | `{f['attack_id']}` | `{f['primitive']}` | "
            f"{f['predicted_score']:.4f} | {f['no_attack_asr']:.4f} | {f['measured_asr']:.4f} | "
            f"{f['transfer_delta_asr']:+.4f} | [{f['ci_low']:.2f}, {f['ci_high']:.2f}] | {f['owasp_category']} |"
        )

    lines.extend(
        [
            "",
            "## Wrapped Tool Versions",
            "",
        ]
    )
    for k, v in sorted(manifest["tool_versions"].items()):
        lines.append(f"- `{k}`: `{v}`")

    text = "\n".join(lines) + "\n"
    return text.replace("\u2014", "--")


def run(ctx: Context) -> Path:
    load_art = ctx.read_artifact("load")
    sim_art = ctx.read_artifact("similarity")
    atk_art = ctx.read_artifact("attack_base")
    pred_art = ctx.read_artifact("predict")
    ver_art = ctx.read_artifact("verify")

    sim_by_variant = {v["model_id"]: v["profile"] for v in sim_art["variants"]}
    findings: list[dict] = []
    variant_records: dict[str, list[dict]] = {}
    per_variant_stats: dict[str, dict] = {}

    for v_entry in ver_art["variants"]:
        vid = v_entry["model_id"]
        rows = v_entry["attacks"]
        variant_records[vid] = rows
        for r in rows:
            no_atk = float(r.get("no_attack_asr", 0.0))
            delta_asr = float(r.get("transfer_delta_asr", float(r["measured_asr"]) - no_atk))
            findings.append(
                {
                    "variant": vid,
                    "attack_id": r["attack_id"],
                    "behavior_id": r["behavior_id"],
                    "category": r["category"],
                    "owasp_category": map_owasp_category(r["category"]),
                    "primitive": r["primitive"],
                    "rank": r["rank"],
                    "predicted_score": r["predicted_score"],
                    "base_strength": r["base_strength"],
                    "base_asr": r["base_asr"],
                    "similarity": r["similarity"],
                    "no_attack_asr": no_atk,
                    "measured_asr": r["measured_asr"],
                    "transfer_delta_asr": delta_asr,
                    "ci_low": r["ci_low"],
                    "ci_high": r["ci_high"],
                    "n_generations": r["n_generations"],
                }
            )
        corrs = compute_baselines_and_headline(rows, seed=ctx.seed)
        prof = sim_by_variant.get(vid, {})
        mean_cka = float(prof.get("cka", {}).get("mean", 0.0))
        final_layer_cka = (
            float(prof["cka"]["per_layer"][-1])
            if prof.get("cka", {}).get("per_layer")
            else 0.0
        )
        mean_cos = float(prof.get("cosine_meanpool", {}).get("mean", 0.0))
        ref_cos = float(prof.get("refusal_subspace", {}).get("refusal_direction_cosine", mean_cka))
        mean_asr = float(np.mean([r["measured_asr"] for r in rows])) if rows else 0.0
        mean_no_atk = float(np.mean([r.get("no_attack_asr", 0.0) for r in rows])) if rows else 0.0
        mean_delta = float(np.mean([r.get("transfer_delta_asr", r["measured_asr"] - r.get("no_attack_asr", 0.0)) for r in rows])) if rows else 0.0
        per_variant_stats[vid] = {
            "correlations": corrs,
            "mean_cka": mean_cka,
            "final_layer_cka": final_layer_cka,
            "mean_cosine_meanpool": mean_cos,
            "refusal_cosine": ref_cos,
            "mean_no_attack_asr": mean_no_atk,
            "mean_measured_asr": mean_asr,
            "mean_transfer_delta_asr": mean_delta,
        }

    pooled_stats = compute_baselines_and_headline(findings, seed=ctx.seed)
    tool_versions = collect_environment_versions(atk_art.get("tools", []))

    manifest = {
        "run_id": ctx.run_id,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "seed": ctx.seed,
        "authorization_id": ctx.authorization.id if ctx.authorization else None,
        "authorization_basis": ctx.authorization.basis if ctx.authorization else None,
        "models": [
            {"role": m["role"], "model_id": m["model_id"], "digest": m["digest"]}
            for m in load_art["models"]
        ],
        "tool_versions": tool_versions,
        "scoring": pred_art.get("scoring"),
        "similarity_metric": pred_art.get("similarity_metric"),
        "benchmark": atk_art.get("benchmark"),
    }

    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    findings_payload = {
        "run_id": ctx.run_id,
        "base": ctx.base_model_id,
        "headline_pooled": pooled_stats,
        "per_variant": per_variant_stats,
        "spotcheck_agreement": ver_art["spotcheck_agreement"],
        "findings": findings,
    }

    findings_path = ctx.out_dir / "findings.json"
    manifest_path = ctx.out_dir / "manifest.json"
    scatter_path = ctx.out_dir / "scatter.png"
    report_md_path = ctx.out_dir / "report.md"

    findings_path.write_text(json.dumps(findings_payload, indent=2, sort_keys=True) + "\n")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    plot_scatter(variant_records, scatter_path)
    md_text = build_markdown_report(
        run_id=ctx.run_id,
        base_id=ctx.base_model_id,
        pooled_stats=pooled_stats,
        per_variant_stats=per_variant_stats,
        findings=findings,
        manifest=manifest,
        spotcheck=ver_art["spotcheck_agreement"],
    )
    report_md_path.write_text(md_text)

    return ctx.write_artifact(
        "report",
        {
            "findings_path": str(findings_path),
            "manifest_path": str(manifest_path),
            "report_md_path": str(report_md_path),
            "scatter_path": str(scatter_path),
            "headline_spearman_rho": pooled_stats["predicted_vs_measured"]["rho"],
            "baseline_base_only_asr_rho": pooled_stats["baseline_base_only_asr"]["rho"],
        },
    )
