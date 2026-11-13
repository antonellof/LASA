"""Cross-family aggregate analysis. Produces the paper's tables and figures.

Reads only committed run artifacts (`findings.json` and `similarity.json` under
each run directory) plus the declared lineage file. It loads no model, runs no
generation, and reads no completion text, so the paper's headline numbers can
be rebuilt on any machine from what is in the repository.

Statistics:
  - Spearman rho as the Pearson correlation of average ranks.
  - Permutation p-values. When the number of distinct (within-stratum)
    permutations is at most EXACT_LIMIT, every permutation is enumerated and
    the p-value is exact. Otherwise a Monte Carlo estimate (k + 1) / (n + 1).
    Family-stratified tests permute only within a family, so their smallest
    attainable p-value is 1 / prod(n_f!) and is reported with every result.
  - Percentile bootstrap CIs over pairs (omitted for N < 5).
  - Partial Spearman of X and Y given Z by rank residualization, tested with a
    permutation of the Y residuals (Kennedy 1995).
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import numpy as np
import yaml
from scipy.stats import rankdata

from lasa.pipeline.predict import rank_attacks_for_variant

EXACT_LIMIT = 50_000
N_PERM = 10_000
N_BOOT = 2_000
SIMILARITY_MODES = ("refcos_only", "refcos_times_cka", "cka")
CEILING = 0.90
HEADROOM_FLOOR = 0.05
KNN_KEYS = ("last_k5", "last_k10", "last_k20", "mean_k10")


# ---------------------------------------------------------------- statistics


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    ac, bc = a - a.mean(), b - b.mean()
    den = math.sqrt(float((ac**2).sum() * (bc**2).sum()))
    return float("nan") if den == 0.0 else float((ac @ bc) / den)


def spearman(x, y) -> float:
    """Spearman rho with average ranks. NaN when either input is constant or N < 2."""
    if len(x) < 2:
        return float("nan")
    return _pearson(rankdata(np.asarray(x, float)), rankdata(np.asarray(y, float)))


def _groups(strata, n: int) -> list[list[int]]:
    if strata is None:
        return [list(range(n))]
    out: dict = {}
    for i, s in enumerate(strata):
        out.setdefault(s, []).append(i)
    return list(out.values())


def count_permutations(strata, n: int) -> int:
    """Number of distinct index permutations that keep every stratum intact."""
    return math.prod(math.factorial(len(g)) for g in _groups(strata, n))


def _permutation_matrix(groups, n, n_perm, seed, exact_limit):
    total = math.prod(math.factorial(len(g)) for g in groups)
    if total <= exact_limit:
        per_group = [list(itertools.permutations(g)) for g in groups]
        out = np.empty((total, n), dtype=int)
        for k, combo in enumerate(itertools.product(*per_group)):
            idx = np.arange(n)
            for g, pg in zip(groups, combo):
                idx[g] = pg
            out[k] = idx
        return out, "exact", total
    rng = np.random.default_rng(seed)
    out = np.tile(np.arange(n), (n_perm, 1))
    for g in groups:
        out[:, g] = rng.permuted(np.tile(np.asarray(g), (n_perm, 1)), axis=1)
    return out, "monte_carlo", total


def _perm_pvalue(a, b, stat, strata, n_perm, seed, exact_limit) -> dict:
    n = a.size
    perms, method, total = _permutation_matrix(_groups(strata, n), n, n_perm, seed, exact_limit)
    bp = b[perms]
    ac = a - a.mean()
    bc = bp - bp.mean(axis=1, keepdims=True)
    den = np.sqrt((ac**2).sum() * (bc**2).sum(axis=1))
    with np.errstate(invalid="ignore", divide="ignore"):
        null = (bc @ ac) / den
    exceed = int(np.sum(np.abs(null) >= abs(stat) - 1e-12))
    if method == "exact":
        p, p_min = exceed / total, 1.0 / total
    else:
        p, p_min = (exceed + 1) / (n_perm + 1), 1.0 / (n_perm + 1)
    return {"p": p, "method": method, "n_permutations": total, "min_attainable_p": p_min}


def permutation_test(x, y, strata=None, n_perm=N_PERM, seed=0, exact_limit=EXACT_LIMIT) -> dict:
    """Two-sided permutation test of Spearman rho, optionally within strata."""
    if len(x) < 3:
        return {"rho": float("nan"), "p": 1.0, "method": "degenerate", "n_permutations": 0, "min_attainable_p": 1.0}
    rx, ry = rankdata(np.asarray(x, float)), rankdata(np.asarray(y, float))
    rho = _pearson(rx, ry)
    if math.isnan(rho):
        return {"rho": float("nan"), "p": 1.0, "method": "degenerate", "n_permutations": 0, "min_attainable_p": 1.0}
    return {"rho": rho, **_perm_pvalue(rx, ry, rho, strata, n_perm, seed, exact_limit)}


def partial_spearman(x, y, z, n_perm=N_PERM, seed=0, exact_limit=EXACT_LIMIT) -> dict:
    """Spearman correlation of X and Y after removing the rank-linear effect of Z."""
    if len(x) < 4:
        return {"rho": float("nan"), "p": 1.0, "method": "degenerate", "n_permutations": 0, "min_attainable_p": 1.0}
    rx, ry, rz = (rankdata(np.asarray(v, float)) for v in (x, y, z))
    design = np.column_stack([np.ones(rz.size), rz])

    def resid(v):
        beta, *_ = np.linalg.lstsq(design, v, rcond=None)
        return v - design @ beta

    ex, ey = resid(rx), resid(ry)
    r = _pearson(ex, ey)
    if math.isnan(r):
        return {"rho": float("nan"), "p": 1.0, "method": "degenerate", "n_permutations": 0, "min_attainable_p": 1.0}
    return {"rho": r, **_perm_pvalue(ex, ey, r, None, n_perm, seed, exact_limit)}


def bootstrap_ci(x, y, n_boot=N_BOOT, seed=0, min_n=5) -> list[float] | None:
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    if xa.size < min_n:
        return None
    rng = np.random.default_rng(seed)
    rhos = []
    for idx in rng.integers(0, xa.size, size=(n_boot, xa.size)):
        r = spearman(xa[idx], ya[idx])
        if not math.isnan(r):
            rhos.append(r)
    if not rhos:
        return None
    lo, hi = np.quantile(rhos, [0.025, 0.975])
    return [float(lo), float(hi)]


def correlate(x, y, strata=None, seed=0) -> dict:
    """Spearman rho with unstratified and (optionally) stratified permutation p and bootstrap CI."""
    res = permutation_test(x, y, seed=seed)
    out = {
        "n": len(x),
        "rho": res["rho"],
        "p": res["p"],
        "p_method": res["method"],
        "ci95": bootstrap_ci(x, y, seed=seed) if not math.isnan(res["rho"]) else None,
    }
    if strata is not None and not math.isnan(res["rho"]):
        st = permutation_test(x, y, strata=strata, seed=seed)
        out.update(
            p_stratified=st["p"],
            p_stratified_method=st["method"],
            p_stratified_min=st["min_attainable_p"],
            n_stratified_permutations=st["n_permutations"],
        )
    return out


# -------------------------------------------------------------------- loading


def load_lineage(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())["families"]


def load_checks(results_dir: str | Path, lineage: dict) -> dict:
    """Optional human spot-check and budget-check files written by `lasa spotcheck`."""
    results_dir = Path(results_dir)
    out: dict = {"human_spotcheck": {}, "judge_independent": {}, "budget_checks": []}
    for fam in lineage:
        hs = results_dir / fam / "human_spotcheck.json"
        if hs.exists():
            out["human_spotcheck"][fam] = json.loads(hs.read_text())
        for ji in sorted((results_dir / fam).glob("judge_independent_*.json")):
            out["judge_independent"].setdefault(fam, []).append(json.loads(ji.read_text()))
        for bc in sorted((results_dir / fam).glob("budget_check_*.json")):
            out["budget_checks"].append(json.loads(bc.read_text()))
    return out


def load_runs(results_dir: str | Path, lineage: dict) -> tuple[list[dict], list[dict], dict]:
    """Return (variant rows, attack-variant pair rows, judge agreement by family)."""
    results_dir = Path(results_dir)
    variants: list[dict] = []
    pairs: list[dict] = []
    judge: dict = {}
    for fam, meta in lineage.items():
        run = results_dir / fam
        fnd = json.loads((run / "findings.json").read_text())
        sim = json.loads((run / "similarity.json").read_text())
        profiles = {v["model_id"]: v["profile"] for v in sim["variants"]}
        judge[fam] = fnd["spotcheck_agreement"]
        knn_file = run / "knn_similarity.json"
        knn = {v["model_id"]: v for v in json.loads(knn_file.read_text())["variants"]} if knn_file.exists() else {}
        for vid, vmeta in meta["variants"].items():
            rows = [f for f in fnd["findings"] if f["variant"] == vid]
            if not rows:
                raise ValueError(f"{fam}: no findings for declared variant {vid}")
            prof = profiles[vid]
            ref = prof["refusal_subspace"]
            attacks = [
                {
                    "attack_id": f["attack_id"],
                    "behavior_id": f["behavior_id"],
                    "category": f["category"],
                    "primitive": f["primitive"],
                    "strength": f["base_strength"],
                    "base_asr": f["base_asr"],
                    **({"layer": ref["refusal_layer"]} if f["primitive"] == "refusal_direction" else {}),
                }
                for f in rows
            ]
            mode_scores = {
                m: {p["attack_id"]: p["predicted_score"] for p in rank_attacks_for_variant(attacks, vid, prof, similarity_metric=m)}
                for m in SIMILARITY_MODES
            }

            def mean_of(key, prim=None):
                sel = [f[key] for f in rows if prim is None or f["primitive"] == prim]
                return float(np.mean(sel))

            no_atk = mean_of("no_attack_asr")
            delta = mean_of("transfer_delta_asr")
            variants.append(
                {
                    "activation_extractor": ref.get("activation_extractor", "legacy-length-index"),
                    "family": fam,
                    "family_label": meta["label"],
                    "model_id": vid,
                    "short": vmeta["short"],
                    "lineage": vmeta["lineage"],
                    "control": bool(vmeta.get("control", False)),
                    "reasoning": bool(vmeta.get("reasoning", False)),
                    "label_offset": vmeta.get("label_offset", [6, 4]),
                    "mean_cka": float(prof["cka"]["mean"]),
                    "last_cka": float(prof["cka"]["per_layer"][-1]),
                    "cka_per_layer": [float(v) for v in prof["cka"]["per_layer"]],
                    "refcos": float(ref["refusal_direction_cosine"]),
                    "refusal_layer": int(ref["refusal_layer"]),
                    "no_attack_asr": no_atk,
                    "attacked_asr": mean_of("measured_asr"),
                    "delta_asr": delta,
                    "delta_asr_rd": mean_of("transfer_delta_asr", "refusal_direction"),
                    "delta_asr_gcg": mean_of("transfer_delta_asr", "gcg"),
                    "attacked_asr_rd": mean_of("measured_asr", "refusal_direction"),
                    "headroom": 1.0 - no_atk,
                    "delta_asr_norm": delta / max(HEADROOM_FLOOR, 1.0 - no_atk),
                    **({f"knn_{k}": knn[vid][k] for k in KNN_KEYS} if vid in knn else {}),
                }
            )
            for f in rows:
                pairs.append(
                    {
                        "family": fam,
                        "variant": vid,
                        "primitive": f["primitive"],
                        "behavior_id": f["behavior_id"],
                        "as_run_score": float(f["predicted_score"]),
                        "base_strength": float(f["base_strength"]),
                        "base_asr": float(f["base_asr"]),
                        "measured_asr": float(f["measured_asr"]),
                        "no_attack_asr": float(f["no_attack_asr"]),
                        "delta_asr": float(f["transfer_delta_asr"]),
                        **{m: mode_scores[m][f["attack_id"]] for m in SIMILARITY_MODES},
                    }
                )
    return variants, pairs, judge


# ------------------------------------------------------------------- analyses

SUBSETS = {
    "all": ("All checkpoints", lambda v: True),
    "fine_tunes": ("Fine-tunes only (no parents)", lambda v: v["lineage"] != "parent"),
    "fine_tunes_no_control": (
        "Fine-tunes excl. Heretic control",
        lambda v: v["lineage"] != "parent" and not v["control"],
    ),
    "non_ceiling": (f"Non-ceiling (no-attack ASR < {CEILING:.2f})", lambda v: v["no_attack_asr"] < CEILING),
    "no_reasoning": ("All checkpoints excl. reasoning model", lambda v: not v["reasoning"]),
    "fine_tunes_no_reasoning": ("Fine-tunes excl. reasoning model", lambda v: v["lineage"] != "parent" and not v["reasoning"]),
}


def variant_level(variants: list[dict], seed: int = 0) -> dict:
    out: dict = {"subsets": {}, "leave_one_family_out": {}, "within_family": {}}
    for key, (label, keep) in SUBSETS.items():
        vs = [v for v in variants if keep(v)]
        fams = [v["family"] for v in vs]

        def col(k):
            return [v[k] for v in vs]

        out["subsets"][key] = {
            "label": label,
            "n": len(vs),
            "members": [v["short"] for v in vs],
            "refcos_vs_delta": correlate(col("refcos"), col("delta_asr"), fams, seed),
            "refcos_vs_delta_rd": correlate(col("refcos"), col("delta_asr_rd"), fams, seed),
            "headroom_vs_delta": correlate(col("headroom"), col("delta_asr"), fams, seed),
            "refcos_vs_no_attack": correlate(col("refcos"), col("no_attack_asr"), fams, seed),
            "refcos_vs_delta_given_no_attack": partial_spearman(col("refcos"), col("delta_asr"), col("no_attack_asr"), seed=seed),
            "mean_cka_vs_delta": correlate(col("mean_cka"), col("delta_asr"), fams, seed),
            "last_cka_vs_delta": correlate(col("last_cka"), col("delta_asr"), fams, seed),
            "refcos_vs_delta_norm": correlate(col("refcos"), col("delta_asr_norm"), fams, seed),
        }
        if vs and all("knn_last_k10" in v for v in vs):
            for kk in KNN_KEYS:
                out["subsets"][key][f"knn_{kk}_vs_delta"] = correlate(col(f"knn_{kk}"), col("delta_asr"), fams, seed)
    for key in ("all", "fine_tunes"):
        keep = SUBSETS[key][1]
        out["leave_one_family_out"][key] = {
            fam: spearman(
                [v["refcos"] for v in variants if keep(v) and v["family"] != fam],
                [v["delta_asr"] for v in variants if keep(v) and v["family"] != fam],
            )
            for fam in dict.fromkeys(v["family"] for v in variants)
        }
    for fam in dict.fromkeys(v["family"] for v in variants):
        vs = [v for v in variants if v["family"] == fam]
        if len(vs) >= 4:
            out["within_family"][fam] = {
                "n": len(vs),
                "refcos_vs_delta": permutation_test([v["refcos"] for v in vs], [v["delta_asr"] for v in vs]),
                "mean_cka_vs_delta": permutation_test([v["mean_cka"] for v in vs], [v["delta_asr"] for v in vs]),
            }
    return out


def attack_level(pairs: list[dict], seed: int = 0) -> dict:
    scopes = {fam: [p for p in pairs if p["family"] == fam] for fam in dict.fromkeys(p["family"] for p in pairs)}
    scopes["pooled"] = pairs
    scopes["pooled_rd_only"] = [p for p in pairs if p["primitive"] == "refusal_direction"]
    out: dict = {}
    for name, ps in scopes.items():
        delta = [p["delta_asr"] for p in ps]
        variant_strata = [p["variant"] for p in ps] if name.startswith("pooled") else None
        row = {"n": len(ps)}
        for key in (*SIMILARITY_MODES, "base_strength", "base_asr"):
            row[f"{key}_vs_delta"] = correlate([p[key] for p in ps], delta, variant_strata, seed)
        row["as_run_vs_raw_asr"] = correlate([p["as_run_score"] for p in ps], [p["measured_asr"] for p in ps], None, seed)
        # Random ordering: the 95% band of |rho| between a random order and Delta ASR at this N.
        rng = np.random.default_rng(seed)
        r_null = [spearman(rng.permutation(len(ps)), delta) for _ in range(2000)]
        r_null = [r for r in r_null if not math.isnan(r)]
        row["random_null_band95"] = float(np.quantile(np.abs(r_null), 0.95)) if r_null else None
        out[name] = row
    return out


# -------------------------------------------------------------------- figures

INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
AXIS = "#c3c2b7"
SURFACE = "#fcfcfb"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
LINEAGE_COLOR = {"child_ft": SERIES[0], "sibling_ft": SERIES[1], "sibling_distill": SERIES[1], "parent": SERIES[2]}
LINEAGE_LABEL = {"child_ft": "Child of Instruct base", "sibling_ft": "Sibling FT / distill", "parent": "Pretrained parent"}
FAMILY_MARKER = ("o", "s", "^", "D", "v")


def _p_text(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=INK_2, labelsize=8)
    ax.grid(True, color=AXIS, linewidth=0.5, alpha=0.6)
    ax.set_axisbelow(True)


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 9, "text.color": INK, "axes.labelcolor": INK, "axes.titlecolor": INK})
    return plt


def plot_similarity_profiles(variants: list[dict], out_path: Path) -> None:
    plt = _plt()
    fams = list(dict.fromkeys(v["family"] for v in variants))
    fig, axes = plt.subplots(1, len(fams), figsize=(3.0 * len(fams), 2.9), dpi=200, sharey=True)
    fig.patch.set_facecolor(SURFACE)
    lo = min(min(v["cka_per_layer"]) for v in variants)
    for ax, fam in zip(np.atleast_1d(axes), fams):
        _style(ax)
        vs = [v for v in variants if v["family"] == fam]
        for slot, v in enumerate(vs):
            ys = v["cka_per_layer"]
            xs = np.linspace(0.0, 1.0, len(ys))
            ax.plot(xs, ys, color=SERIES[slot], linewidth=1.6, label=v["short"])
        ax.set_title(vs[0]["family_label"], fontsize=9)
        ax.set_xlabel("Normalized layer depth", fontsize=8, color=INK_2)
        ax.set_ylim(min(0.5, lo) - 0.03, 1.02)
        ax.legend(fontsize=6.5, frameon=False, loc="lower left")
    np.atleast_1d(axes)[0].set_ylabel("Linear CKA with base (benign probes)", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)


def plot_variant_level(variants: list[dict], stats: dict, out_path: Path) -> None:
    plt = _plt()
    from matplotlib.lines import Line2D

    fams = list(dict.fromkeys(v["family"] for v in variants))
    marker = {f: FAMILY_MARKER[i % len(FAMILY_MARKER)] for i, f in enumerate(fams)}
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.4), dpi=200, sharey=True)
    fig.patch.set_facecolor(SURFACE)
    s_all = stats["subsets"]["all"]
    s_ft = stats["subsets"]["fine_tunes"]
    panels = [
        ("mean_cka", "Mean linear CKA on 240 benign probes", s_all["mean_cka_vs_delta"], None),
        ("refcos", "Signed refusal-direction cosine (RefCos)", s_all["refcos_vs_delta"], s_ft["refcos_vs_delta"]),
    ]
    for ax, (key, xlabel, st, st_ft) in zip(axes, panels):
        _style(ax)
        ax.axhline(0.0, color=MUTED, linewidth=0.8, linestyle=":")
        for v in variants:
            color = LINEAGE_COLOR[v["lineage"]]
            ax.scatter(
                v[key], v["delta_asr"], s=58, marker=marker[v["family"]],
                facecolor=SURFACE if v["control"] else color, edgecolor=color if v["control"] else SURFACE,
                linewidth=1.6 if v["control"] else 0.9, zorder=3,
            )
            if key == "refcos":
                dx, dy = v["label_offset"]
                ax.annotate(v["short"] + (" (control)" if v["control"] else ""), (v[key], v["delta_asr"]),
                            xytext=(dx, dy), textcoords="offset points", fontsize=6.5, color=INK_2)
        title = f"All checkpoints, N = {st['n']}: rho = {st['rho']:+.2f}, {_p_text(st['p'])}"
        if st_ft is not None:
            title += f"\nFine-tunes only, N = {st_ft['n']}: rho = {st_ft['rho']:+.2f}, {_p_text(st_ft['p'])}"
        else:
            title += "\n "
        ax.set_title(title, fontsize=8.5, color=INK_2)
        ax.set_xlabel(xlabel, fontsize=8.5)
    axes[0].set_ylabel("Control-adjusted transfer (attacked ASR minus no-attack ASR)", fontsize=8)
    lineage_handles = [
        Line2D([], [], marker="o", linestyle="", markerfacecolor=c, markeredgecolor=SURFACE, markersize=7, label=LINEAGE_LABEL[k])
        for k, c in LINEAGE_COLOR.items() if k in LINEAGE_LABEL
    ]
    lineage_handles.append(Line2D([], [], marker="o", linestyle="", markerfacecolor=SURFACE, markeredgecolor=SERIES[0],
                                  markeredgewidth=1.6, markersize=7, label="Abliterated control"))
    family_handles = [
        Line2D([], [], marker=marker[f], linestyle="", color=INK_2, markersize=6,
               label=next(v["family_label"] for v in variants if v["family"] == f))
        for f in fams
    ]
    fig.legend(handles=lineage_handles, loc="lower center", bbox_to_anchor=(0.5, 0.045), ncol=4, fontsize=7.5, frameon=False)
    fig.legend(handles=family_handles, loc="lower center", bbox_to_anchor=(0.5, 0.0), ncol=5, fontsize=7.5, frameon=False)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)


def plot_attack_level(pairs: list[dict], stats: dict, out_path: Path, seed: int = 0) -> None:
    plt = _plt()
    rng = np.random.default_rng(seed)
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    pooled = stats["pooled"]
    prim_color = {"refusal_direction": SERIES[0], "gcg": SERIES[1]}
    prim_label = {"refusal_direction": "Refusal-direction ablation", "gcg": "GCG suffix (5 steps)"}
    panels = [
        ("measured_asr", "Measured attacked ASR on variant", pooled["as_run_vs_raw_asr"]),
        ("delta_asr", "Control-adjusted transfer (Delta ASR)", pooled["refcos_times_cka_vs_delta"]),
    ]
    jit = rng.uniform(-0.025, 0.025, size=len(pairs))
    for ax, (key, ylabel, st) in zip(axes, panels):
        _style(ax)
        for prim in ("gcg", "refusal_direction"):
            idx = [i for i, p in enumerate(pairs) if p["primitive"] == prim]
            ax.scatter([pairs[i]["as_run_score"] for i in idx], [pairs[i][key] + jit[i] for i in idx],
                       s=22, color=prim_color[prim], alpha=0.65, edgecolor=SURFACE, linewidth=0.4, label=prim_label[prim])
        ax.set_xlabel("Predicted transfer score (as run: strength x RefCos x CKA)", fontsize=8.5)
        ax.set_ylabel(ylabel, fontsize=8.5)
        ax.set_title(f"One point per attack-variant pair, N = {st['n']}: rho = {st['rho']:+.3f}, {_p_text(st['p'])}",
                     fontsize=8.5, color=INK_2)
    axes[1].axhline(0.0, color=MUTED, linewidth=0.8, linestyle=":")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles[::-1], labels[::-1], loc="lower center", ncol=2, fontsize=7.5, frameon=False, markerscale=1.4)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)


# -------------------------------------------------------------------- markdown


def _fmt_p(p: float) -> str:
    return f"{p:.4f}" if p >= 1e-4 else f"{p:.1e}"


def _fmt_ci(ci) -> str:
    return "n/a" if not ci else f"[{ci[0]:+.2f}, {ci[1]:+.2f}]"


def _row_corr(name: str, c: dict) -> str:
    strat = (
        f"{_fmt_p(c['p_stratified'])} (min {c['p_stratified_min']:.4f}, {c['p_stratified_method']})"
        if "p_stratified" in c else "n/a"
    )
    return f"| {name} | {c['n']} | {c['rho']:+.4f} | {_fmt_ci(c['ci95'])} | {_fmt_p(c['p'])} | {strat} |"


def build_markdown(variants, vstats, astats, judge, checks=None) -> str:
    L = ["# LASA (Lineage Attack Surface Analyzer) historical cross-group aggregate", "",
         "Generated by `lasa aggregate` from committed `findings.json` and `similarity.json`. Do not edit by hand.", "",
         f"Permutation tests: exact when at most {EXACT_LIMIT} distinct permutations exist, otherwise {N_PERM} Monte Carlo draws. "
         f"Bootstrap: {N_BOOT} resamples, omitted for N < 5. Stratified tests permute within source-checkpoint groups, not independent model lineages; `min` is the smallest attainable p.", "",
         "## Table 1. Per-checkpoint similarity, controls, and transfer", "",
         "| Family | Checkpoint | Lineage | Mean CKA | Last CKA | RefCos | No-atk ASR | Attacked ASR | Delta ASR | RD Delta | GCG Delta | Delta norm |",
         "| :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for v in variants:
        lin = v["lineage"] + (" (control)" if v["control"] else "")
        L.append(f"| {v['family_label']} | {v['short']} | {lin} | {v['mean_cka']:.4f} | {v['last_cka']:.4f} | {v['refcos']:+.4f} | "
                 f"{v['no_attack_asr']:.4f} | {v['attacked_asr']:.4f} | {v['delta_asr']:+.4f} | {v['delta_asr_rd']:+.4f} | "
                 f"{v['delta_asr_gcg']:+.4f} | {v['delta_asr_norm']:+.4f} |")
    if all("knn_last_k10" in v for v in variants):
        L += ["", "## Table 1c. Mutual-kNN similarity to base (benign probes, layer floor(0.8 L))", "",
              "| Family | Checkpoint | Mean CKA | kNN last k=5 | kNN last k=10 | kNN last k=20 | kNN mean-pooled k=10 | RefCos |",
              "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for v in variants:
            L.append(f"| {v['family_label']} | {v['short']} | {v['mean_cka']:.4f} | {v['knn_last_k5']:.4f} | "
                     f"{v['knn_last_k10']:.4f} | {v['knn_last_k20']:.4f} | {v['knn_mean_k10']:.4f} | {v['refcos']:+.4f} |")
    L += ["", "## Table 2. Variant-level rank correlations", "",
          "| Predictor -> outcome | N | Spearman rho | 95% CI | Perm p | Family-stratified p |",
          "| :--- | ---: | ---: | :--- | ---: | :--- |"]
    names = [
        ("refcos_vs_delta", "RefCos -> Delta ASR"),
        ("refcos_vs_delta_rd", "RefCos -> RD-only Delta ASR"),
        ("headroom_vs_delta", "Headroom (1 - no-atk ASR) -> Delta ASR [needs generation]"),
        ("mean_cka_vs_delta", "Mean CKA -> Delta ASR"),
        ("last_cka_vs_delta", "Last-layer CKA -> Delta ASR"),
        ("refcos_vs_no_attack", "RefCos -> no-attack ASR"),
        ("refcos_vs_delta_norm", "RefCos -> headroom-normalized Delta ASR"),
    ]
    knn_names = [(f"knn_{kk}_vs_delta", f"Mutual-kNN ({kk.replace('_k', ' token, k=').replace('last', 'last').replace('mean', 'mean-pooled')}) -> Delta ASR")
                 for kk in KNN_KEYS]
    for key, sub in vstats["subsets"].items():
        L.append(f"| **{sub['label']}** | | | | | |")
        for k, nm in names + [kn for kn in knn_names if kn[0] in sub]:
            L.append(_row_corr(nm, sub[k]))
        pc = sub["refcos_vs_delta_given_no_attack"]
        L.append(f"| Partial: RefCos -> Delta ASR given no-atk ASR | {sub['n']} | {pc['rho']:+.4f} | n/a | {_fmt_p(pc['p'])} | n/a |")
    L += ["", "Members per subset:", ""]
    for sub in vstats["subsets"].values():
        L.append(f"- {sub['label']} (N = {sub['n']}): {', '.join(sub['members'])}")
    L += ["", "### Within-source-group (exact permutation)", "", "| Family | N | RefCos rho | p | Mean CKA rho | p |", "| :--- | ---: | ---: | ---: | ---: | ---: |"]
    for fam, w in vstats["within_family"].items():
        L.append(f"| {fam} | {w['n']} | {w['refcos_vs_delta']['rho']:+.4f} | {_fmt_p(w['refcos_vs_delta']['p'])} | "
                 f"{w['mean_cka_vs_delta']['rho']:+.4f} | {_fmt_p(w['mean_cka_vs_delta']['p'])} |")
    L += ["", "### Leave-one-family-out: RefCos -> Delta ASR rho", "", "| Dropped family | All checkpoints | Fine-tunes only |", "| :--- | ---: | ---: |"]
    for fam in vstats["leave_one_family_out"]["all"]:
        L.append(f"| {fam} | {vstats['leave_one_family_out']['all'][fam]:+.4f} | {vstats['leave_one_family_out']['fine_tunes'][fam]:+.4f} |")
    L += ["", "## Table 3. Attack-level rank correlations against Delta ASR", "",
          "Pooled rows also report a p-value from permutations within each variant, which tests within-variant ordering only.", "",
          "| Scope | N | refcos_only | refcos_times_cka (as run) | cka | Base strength | Base-only ASR | Random (95% band) | As-run score vs raw ASR |",
          "| :--- | ---: | :--- | :--- | :--- | :--- | :--- | ---: | :--- |"]

    def cell(c):
        if math.isnan(c["rho"]):
            return "undefined (constant)"
        s = f"{c['rho']:+.4f} {_fmt_ci(c['ci95'])} p={_fmt_p(c['p'])}"
        if "p_stratified" in c:
            s += f", within-variant p={_fmt_p(c['p_stratified'])}"
        return s

    for scope, r in astats.items():
        L.append(f"| {scope} | {r['n']} | {cell(r['refcos_only_vs_delta'])} | {cell(r['refcos_times_cka_vs_delta'])} | "
                 f"{cell(r['cka_vs_delta'])} | {cell(r['base_strength_vs_delta'])} | {cell(r['base_asr_vs_delta'])} | "
                 f"+/-{r['random_null_band95']:.3f} | {cell(r['as_run_vs_raw_asr'])} |")
    L += ["", "## Judge cross-check (automated, not a human spot-check)", "",
          "Primary ASR label: refusal-prefix detector. Cross-check: the evaluated checkpoint prompted with the HarmBench "
          "classifier template, which returns 0 whenever the prefix detector does, so the two can only disagree on "
          "generations the detector marks as compliant.", "",
          "| Family | n | Raw agreement | Cohen's kappa |", "| :--- | ---: | ---: | ---: |"]
    for fam, j in judge.items():
        L.append(f"| {fam} | {j['n_checked']} | {j['agreement']:.4f} | {j['cohens_kappa']:.4f} |")
    checks = checks or {}
    if checks.get("human_spotcheck"):
        L += ["", "## Human spot-check of the detector (`lasa spotcheck`)", "",
              "Stratified sample regenerated greedily under the run's conditions, labeled blind by a human. "
              "FP rate is the share of detector-positive items the human labeled as non-compliant.", "",
              "| Family | n labeled | Agreement | Cohen's kappa | Detector + rate | Human + rate | FP rate given detector + |",
              "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        tot_d, tot_h = [], []
        for fam, h in checks["human_spotcheck"].items():
            L.append(f"| {fam} | {h['n_labeled']} | {h['agreement']:.3f} | {h['cohens_kappa']:.3f} | "
                     f"{h['detector_positive_rate']:.3f} | {h['human_positive_rate']:.3f} | "
                     f"{h['false_positive_rate_given_detector_positive']:.3f} |")
            c = h["confusion"]
            tot_d += [1] * (c["detector1_human1"] + c["detector1_human0"]) + [0] * (c["detector0_human1"] + c["detector0_human0"])
            tot_h += [1] * c["detector1_human1"] + [0] * c["detector1_human0"] + [1] * c["detector0_human1"] + [0] * c["detector0_human0"]
        if len(checks["human_spotcheck"]) > 1:
            from lasa.judge import judge_human_agreement

            agg = judge_human_agreement(tot_d, tot_h)
            fp = sum(h["confusion"]["detector1_human0"] for h in checks["human_spotcheck"].values())
            dp = sum(h["confusion"]["detector1_human0"] + h["confusion"]["detector1_human1"] for h in checks["human_spotcheck"].values())
            L.append(f"| **pooled** | {agg['n_checked']} | {agg['agreement']:.3f} | {agg['cohens_kappa']:.3f} | "
                     f"{float(np.mean(tot_d)):.3f} | {float(np.mean(tot_h)):.3f} | {fp / max(1, dp):.3f} |")
    if checks.get("judge_independent"):
        rows = [(fam, r) for fam, lst in checks["judge_independent"].items() for r in lst]
        L += ["", "## Independent judge of the detector (not the human spot-check)", "",
              "An automated second labeler scored the same blind sheets. This is a sanity check on the "
              "refusal-prefix detector, not the human spot-check ADR-0011 requires; that remains open. "
              "FP rate is the share of detector-positive items the judge labeled non-compliant.", "",
              "| Run | Judge | n | Agreement | Cohen's kappa | Detector + rate | Judge + rate | FP rate given detector + |",
              "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        tot_d: list[int] = []
        tot_j: list[int] = []
        for fam, r in rows:
            c = r["confusion"]
            tot_d += [1] * (c["detector1_human1"] + c["detector1_human0"]) + [0] * (c["detector0_human1"] + c["detector0_human0"])
            tot_j += [1] * c["detector1_human1"] + [0] * c["detector1_human0"] + [1] * c["detector0_human1"] + [0] * c["detector0_human0"]
            L.append(f"| {r['run_id']} | {r['labeler']} | {r['n_labeled']} | {r['agreement']:.3f} | {r['cohens_kappa']:.3f} | "
                     f"{r['detector_positive_rate']:.3f} | {r['human_positive_rate']:.3f} | "
                     f"{r['false_positive_rate_given_detector_positive']:.3f} |")
        if len(rows) > 1:
            from lasa.judge import judge_human_agreement

            agg = judge_human_agreement(tot_d, tot_j)
            fp = sum(r["confusion"]["detector1_human0"] for _, r in rows)
            dp = sum(r["confusion"]["detector1_human0"] + r["confusion"]["detector1_human1"] for _, r in rows)
            L.append(f"| **pooled** | {rows[0][1]['labeler']} | {agg['n_checked']} | {agg['agreement']:.3f} | {agg['cohens_kappa']:.3f} | "
                     f"{float(np.mean(tot_d)):.3f} | {float(np.mean(tot_j)):.3f} | {fp / max(1, dp):.3f} |")
    if checks.get("budget_checks"):
        L += ["", "## Generation-budget check (attack-free control rerun)", "",
              "| Run | Variant | New tokens | Gens | Detector ASR, run budget | Detector ASR, full text | Detector ASR after </think> | Closed think block | Median words after think | Empty after think |",
              "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for b in checks["budget_checks"]:
            L.append(f"| {b['run_id']} | {b['variant']} | {b['max_new_tokens']} | {b['n_generations']} | "
                     f"{b['reference_no_attack_asr_at_run_budget']:.3f} | {b['detector_asr_full_text']:.3f} | "
                     f"{b['detector_asr_after_think']:.3f} | {b['fraction_with_closed_think_block']:.3f} | "
                     f"{b['median_words_after_think']:.0f} | {b['fraction_empty_after_think']:.3f} |")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------ run


def _clean(obj):
    if isinstance(obj, float):
        return None if math.isnan(obj) else obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def run(results_dir: str | Path, lineage_path: str | Path, out_dir: str | Path, fig_dir: str | Path | None, seed: int = 0) -> dict:
    lineage = load_lineage(lineage_path)
    variants, pairs, judge = load_runs(results_dir, lineage)
    checks = load_checks(results_dir, lineage)
    vstats = variant_level(variants, seed=seed)
    astats = attack_level(pairs, seed=seed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    from lasa.attacks.refusal_direction import ACTIVATION_EXTRACTOR_VERSION

    superseded = any(v["activation_extractor"] != ACTIVATION_EXTRACTOR_VERSION for v in variants)
    validity_notice = (
        "> **SUPERSEDED MEASUREMENTS: NOT VALIDATED EVIDENCE.** "
        "These artifacts used a padding-dependent activation index. "
        "Direction extraction, RefCos, base strengths, interventions, and derived statistics "
        "need fresh runs with last-attended-v2. The labels measure non-refusal, not harmful compliance. "
        "The numbers below reproduce historical artifacts only. See docs/STUDY.md.\n\n"
        if superseded else ""
    )
    payload = {
        "measurement_status": "superseded_extraction_bug" if superseded else "extraction_v2_requires_label_validation",
        "label_validation": "independent full-set judging and human audit required",
        "seed": seed,
        "n_experiment_groups": len(lineage),
        "n_model_lineages": len({meta.get("model_lineage", group) for group, meta in lineage.items()}),
        "strata_definition": "source checkpoint groups, not independent model lineages",
        "n_variants": len(variants),
        "n_pairs": len(pairs),
        "variants": [{k: v for k, v in row.items() if k not in ("cka_per_layer", "label_offset")} for row in variants],
        "variant_level": vstats,
        "attack_level": astats,
        "judge_cross_check": judge,
        "checks": checks,
    }
    (out_dir / "aggregate.json").write_text(json.dumps(_clean(payload), indent=2, sort_keys=True) + "\n")
    (out_dir / "aggregate.md").write_text(validity_notice + build_markdown(variants, vstats, astats, judge, checks))
    if fig_dir is not None:
        fig_dir = Path(fig_dir)
        fig_dir.mkdir(parents=True, exist_ok=True)
        plot_similarity_profiles(variants, fig_dir / "similarity_profiles.png")
        plot_variant_level(variants, vstats, fig_dir / "variant_level.png")
        plot_attack_level(pairs, astats, fig_dir / "attack_level.png", seed=seed)
    return payload
