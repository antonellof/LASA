"""Design-stage power analysis for the prospective prompt-transfer study.

No model calls. Simulates the frozen tests under stated assumptions so the paper
can report what effect sizes the design can detect. Run:

    python -m lasa.power --out experiments/results/power_analysis.json
"""
from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
from scipy.special import expit, logit
from scipy.stats import rankdata


def _rank_rows(x):
    return rankdata(x, axis=1)


def _row_corr(a, b):
    a = a - a.mean(axis=1, keepdims=True)
    b = b - b.mean(axis=1, keepdims=True)
    return (a * b).sum(axis=1) / np.sqrt((a * a).sum(axis=1) * (b * b).sum(axis=1))


def spearman_null_critical(n, alpha=0.05, sims=20000, seed=0):
    rng = np.random.default_rng(seed)
    rho = _row_corr(_rank_rows(rng.standard_normal((sims, n))), _rank_rows(rng.standard_normal((sims, n))))
    return float(np.quantile(np.abs(rho), 1 - alpha))


def spearman_power(n, rho_s, critical, sims=4000, seed=0):
    """Gaussian copula with the Pearson correlation that yields Spearman rho_s."""
    r = 2 * np.sin(np.pi * rho_s / 6)
    rng = np.random.default_rng(seed)
    z1 = rng.standard_normal((sims, n))
    z2 = r * z1 + np.sqrt(1 - r ** 2) * rng.standard_normal((sims, n))
    rho = _row_corr(_rank_rows(z1), _rank_rows(z2))
    return float(np.mean(np.abs(rho) >= critical))


def contrast_power(n_behaviors, n_reps, p_bare, shift, sigma, alpha, sims=1000, n_perm=999, seed=0):
    """Power of the behavior-level sign-flip test of GCG minus bare.

    Each behavior has its own bare-prompt logit (normal, sd sigma, around p_bare);
    the attack adds `shift` on the logit scale. Returns (power, mean true delta).
    """
    rng = np.random.default_rng(seed)
    base = logit(p_bare) + sigma * rng.standard_normal((sims, n_behaviors))
    p0, p1 = expit(base), expit(base + shift)
    y0 = rng.binomial(n_reps, p0) / n_reps
    y1 = rng.binomial(n_reps, p1) / n_reps
    d = y1 - y0
    signs = rng.choice([-1.0, 1.0], size=(n_perm, n_behaviors))
    null = np.abs(d @ signs.T) / n_behaviors
    observed = np.abs(d.mean(axis=1, keepdims=True))
    p = (1 + (null >= observed - 1e-12).sum(axis=1)) / (n_perm + 1)
    power = float(np.mean((p < alpha) & (d.mean(axis=1) > 0)))
    return power, float((p1 - p0).mean())


def _sign_flip_pvalues(d, signs):
    null = np.abs(d @ signs.T) / d.shape[1]
    observed = np.abs(d.mean(axis=1, keepdims=True))
    return (1 + (null >= observed - 1e-12).sum(axis=1)) / (signs.shape[0] + 1)


def decision_power(n_behaviors, n_reps, p_bare, shift, sigma, alpha, sims=1000, n_perm=999, seed=0):
    """Power of the full decision rule: GCG beats the bare prompt and the random suffix.

    The random suffix is assumed inert (same rate as the bare prompt). Both
    contrasts share the GCG draws, as they do in the study. Returns (power, mean true delta).
    """
    rng = np.random.default_rng(seed)
    base = logit(p_bare) + sigma * rng.standard_normal((sims, n_behaviors))
    p0, p1 = expit(base), expit(base + shift)
    bare = rng.binomial(n_reps, p0) / n_reps
    random_suffix = rng.binomial(n_reps, p0) / n_reps
    gcg = rng.binomial(n_reps, p1) / n_reps
    signs = rng.choice([-1.0, 1.0], size=(n_perm, n_behaviors))
    ok = np.ones(sims, dtype=bool)
    for control in (bare, random_suffix):
        d = gcg - control
        ok &= (_sign_flip_pvalues(d, signs) < alpha) & (d.mean(axis=1) > 0)
    return float(ok.mean()), float((p1 - p0).mean())


def minimum_detectable_delta(n_behaviors, n_reps, p_bare, sigma, alpha, target=0.8, seed=0, rule=contrast_power):
    for shift in np.arange(0.1, 6.01, 0.1):
        power, delta = rule(n_behaviors, n_reps, p_bare, shift, sigma, alpha, seed=seed)
        if power >= target:
            return {"mean_true_delta": round(delta, 3), "logit_shift": round(float(shift), 2), "power": round(power, 3)}
    return None


def top_k_power(n, k, rho_s, alpha, sims=2000, seed=0, chunk=100):
    """Power of the confirmatory top-k utility test against all k-subsets (one-sided).

    Source score and target uplift follow a Gaussian copula with Spearman rho_s.
    Continuous uplift; the discrete five-replicate uplift of the study has ties and less power.
    """
    r = 2 * np.sin(np.pi * rho_s / 6)
    rng = np.random.default_rng(seed)
    subsets = np.array(list(combinations(range(n), k)))
    hits = 0
    for start in range(0, sims, chunk):
        m = min(chunk, sims - start)
        score = rng.standard_normal((m, n))
        uplift = r * score + np.sqrt(1 - r ** 2) * rng.standard_normal((m, n))
        chosen = np.argsort(-score, axis=1)[:, :k]
        observed = np.take_along_axis(uplift, chosen, axis=1).mean(axis=1, keepdims=True)
        null = uplift[:, subsets].mean(axis=2)
        hits += int(np.sum((null >= observed - 1e-12).mean(axis=1) < alpha))
    return hits / sims


def analysis(n_behaviors=20, n_reps=5, alpha=0.05, family_size=6, sigma=1.0, seed=0, top_k=5,
             ranking_family_size=3):
    critical = spearman_null_critical(n_behaviors, alpha, seed=seed)
    rho_grid = [round(r, 2) for r in np.arange(0.3, 0.95, 0.05)]
    power_by_rho = {str(r): spearman_power(n_behaviors, r, critical, seed=seed) for r in rho_grid}
    rho80 = next((float(r) for r in rho_grid if power_by_rho[str(r)] >= 0.8), None)
    contrasts = {}
    for p_bare in (0.02, 0.10, 0.30):
        contrasts[str(p_bare)] = {
            "unadjusted_alpha": minimum_detectable_delta(n_behaviors, n_reps, p_bare, sigma, alpha, seed=seed),
            "holm_worst_case_alpha": minimum_detectable_delta(n_behaviors, n_reps, p_bare, sigma, alpha / family_size, seed=seed),
            "both_controls_holm_worst_case": minimum_detectable_delta(
                n_behaviors, n_reps, p_bare, sigma, alpha / family_size, seed=seed, rule=decision_power),
        }
    top_k_alphas = {"unadjusted_alpha": alpha, "holm_worst_case_alpha": alpha / ranking_family_size}
    top_k_by_rho = {name: {str(r): top_k_power(n_behaviors, top_k, r, a, seed=seed) for r in rho_grid}
                    for name, a in top_k_alphas.items()}
    top_k_rho80 = {name: next((float(r) for r in rho_grid if by_rho[str(r)] >= 0.8), None)
                   for name, by_rho in top_k_by_rho.items()}
    return {
        "design": {"heldout_behaviors": n_behaviors, "replicates": n_reps, "alpha": alpha,
                   "primary_family_size": family_size, "behavior_logit_sd": sigma, "seed": seed,
                   "top_k": top_k, "ranking_family_size": ranking_family_size},
        "spearman": {"critical_abs_rho": round(critical, 3), "rho_for_80pct_power": rho80,
                     "power_by_rho": {k: round(v, 3) for k, v in power_by_rho.items()},
                     "note": "continuous scores; ties from discrete target uplift lower power further"},
        "top_k_prioritization": {"rho_for_80pct_power": top_k_rho80,
                                 "power_by_rho": {name: {k: round(v, 3) for k, v in by_rho.items()}
                                                  for name, by_rho in top_k_by_rho.items()},
                                 "note": ("confirmatory test; exact null over all k-subsets; continuous uplift, "
                                          "so the study's discrete uplift has less power")},
        "primary_contrast_80pct_power": contrasts,
        "assumptions": ("Behavior-level bare logits vary with sd 1.0 around logit(p_bare); the attack adds a "
                        "constant logit shift; replicates are independent binomial draws; sign-flip test with "
                        "999 draws. Holm worst case divides alpha by the family size (3 primary targets x 2 controls). "
                        "The both-controls rule requires GCG to beat the bare prompt and an inert random suffix. "
                        "The top-k test uses a Gaussian copula between source score and target uplift."),
        "interpretation": "Design-stage simulation, not an observed result.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--heldout", type=int, default=20, help="held-out behaviors in the design")
    args = parser.parse_args(argv)
    payload = analysis(n_behaviors=args.heldout)
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
