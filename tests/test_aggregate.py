"""Unit tests for the cross-family aggregate analysis on synthetic fixtures.

Fixtures hold only scores and rates. No prompts, no completions.
"""

import json
import math

import numpy as np
import pytest
import yaml
from scipy.stats import spearmanr

from lasa import aggregate


def test_spearman_matches_scipy_with_ties():
    rng = np.random.default_rng(1)
    x = rng.integers(0, 5, size=40).astype(float)
    y = x + rng.integers(0, 3, size=40)
    assert aggregate.spearman(x, y) == pytest.approx(spearmanr(x, y).statistic)


def test_spearman_constant_is_nan():
    assert math.isnan(aggregate.spearman([1, 1, 1], [1, 2, 3]))


def test_exact_permutation_monotone_n4():
    res = aggregate.permutation_test([1, 2, 3, 4], [10, 20, 30, 40])
    assert res["method"] == "exact"
    assert res["n_permutations"] == 24
    # identity and full reversal both reach |rho| = 1
    assert res["p"] == pytest.approx(2 / 24)


def test_stratified_permutation_floor():
    strata = ["a", "a", "a", "b", "b", "c", "c"]
    assert aggregate.count_permutations(strata, 7) == 6 * 2 * 2
    res = aggregate.permutation_test(range(7), range(7), strata=strata)
    assert res["method"] == "exact"
    assert res["min_attainable_p"] == pytest.approx(1 / 24)
    # only the identity reaches rho = 1 inside these strata
    assert res["p"] == pytest.approx(1 / 24)


def test_monte_carlo_branch():
    rng = np.random.default_rng(2)
    x = rng.normal(size=30)
    res = aggregate.permutation_test(x, x + rng.normal(size=30), n_perm=500, exact_limit=10)
    assert res["method"] == "monte_carlo"
    assert 0.0 < res["p"] <= 1.0
    assert res["min_attainable_p"] == pytest.approx(1 / 501)


def test_partial_spearman_removes_shared_driver():
    rng = np.random.default_rng(3)
    z = rng.normal(size=300)
    x = z + 0.3 * rng.normal(size=300)
    y = z + 0.3 * rng.normal(size=300)
    assert aggregate.spearman(x, y) > 0.7
    assert abs(aggregate.partial_spearman(x, y, z)["rho"]) < 0.2


def test_small_inputs_are_degenerate_not_errors():
    assert aggregate.permutation_test([1, 2], [2, 1])["method"] == "degenerate"
    assert aggregate.partial_spearman([1, 2, 3], [1, 2, 3], [3, 2, 1])["method"] == "degenerate"
    assert aggregate.bootstrap_ci([1, 2, 3], [1, 2, 3]) is None


def _write_family(root, fam, variants, rng):
    findings, sim_variants = [], []
    for vid, refcos, no_atk in variants:
        for b in range(4):
            for prim in ("refusal_direction", "gcg"):
                measured = float(min(1.0, no_atk + (0.2 * refcos if prim == "refusal_direction" else 0.0)))
                findings.append({
                    "variant": vid, "attack_id": f"b{b}__{prim}", "behavior_id": f"b{b}", "category": "Privacy",
                    "primitive": prim, "base_strength": float(rng.uniform(0, 0.5)), "base_asr": 0.0,
                    "predicted_score": float(rng.uniform()), "measured_asr": measured, "no_attack_asr": no_atk,
                    "transfer_delta_asr": measured - no_atk,
                })
        sim_variants.append({"model_id": vid, "profile": {
            "cka": {"mean": 0.95, "per_layer": [1.0, 0.97, 0.93]},
            "refusal_subspace": {"refusal_direction_cosine": refcos, "refusal_layer": 1},
        }})
    d = root / fam
    d.mkdir()
    (d / "findings.json").write_text(json.dumps({
        "findings": findings, "spotcheck_agreement": {"n_checked": 4, "agreement": 1.0, "cohens_kappa": 1.0}}))
    (d / "similarity.json").write_text(json.dumps({"variants": sim_variants}))


def test_aggregate_end_to_end(tmp_path):
    rng = np.random.default_rng(4)
    results = tmp_path / "results"
    results.mkdir()
    fams = {
        "fam_a": [("a/v1", 0.8, 0.1), ("a/v2", 0.4, 0.3), ("a/v3", 0.1, 0.9), ("a/parent", 0.2, 0.95)],
        "fam_b": [("b/v1", 0.6, 0.2), ("b/v2", -0.1, 1.0)],
    }
    lineage = {"families": {}}
    for fam, vs in fams.items():
        _write_family(results, fam, vs, rng)
        lineage["families"][fam] = {"label": fam.upper(), "base": f"{fam}/base", "variants": {
            vid: {"short": vid.split("/")[1], "lineage": "parent" if vid.endswith("parent") else "child_ft"}
            for vid, _, _ in vs}}
    lineage_path = tmp_path / "lineage.yaml"
    lineage_path.write_text(yaml.safe_dump(lineage))

    out = aggregate.run(results, lineage_path, tmp_path / "out", tmp_path / "figs")

    assert out["n_variants"] == 6 and out["n_pairs"] == 48
    assert out["measurement_status"] == "superseded_extraction_bug"
    assert "SUPERSEDED MEASUREMENTS" in (tmp_path / "out" / "aggregate.md").read_text()
    payload = json.loads((tmp_path / "out" / "aggregate.json").read_text())
    sub = payload["variant_level"]["subsets"]
    assert sub["all"]["n"] == 6 and sub["fine_tunes"]["n"] == 5
    assert sub["all"]["refcos_vs_delta"]["rho"] > 0.5
    md = (tmp_path / "out" / "aggregate.md").read_text()
    assert "Table 2" in md and "\u2014" not in md
    for name in ("similarity_profiles.png", "variant_level.png", "attack_level.png"):
        assert (tmp_path / "figs" / name).stat().st_size > 0


def test_lineage_file_covers_committed_runs():
    lineage = aggregate.load_lineage("experiments/lineage.yaml")
    for fam, meta in lineage.items():
        sim = json.loads(open(f"experiments/results/{fam}/similarity.json").read())
        assert {v["model_id"] for v in sim["variants"]} == set(meta["variants"])


def test_strip_think_and_stratified_sample():
    from lasa.spotcheck import sample_items, strip_think

    assert strip_think("<think>reasoning</think>\nI cannot help.") == "I cannot help."
    assert strip_think("plain answer") == "plain answer"
    verify_art = {"variants": [{"model_id": "x/v", "attacks": [
        {"primitive": "gcg" if i % 2 else "refusal_direction", "attack_id": f"b{i // 2}__{i % 2}",
         "behavior_id": f"b{i // 2}", "outcomes": [i % 3 == 0] * 5, "no_attack_outcomes": [0] * 5}
        for i in range(24)]}]}
    items = sample_items(verify_art, n_per_variant=10, seed=0)
    assert len(items) == 10
    labels = [it["detector_label"] for it in items]
    assert labels.count(0) == 5 and labels.count(1) == 5
    assert len({it["id"] for it in items}) == 10


def test_independent_judge_file_is_not_counted_as_human(tmp_path, monkeypatch):
    import csv as _csv
    import types
    from lasa import spotcheck

    run = "fam_x"
    # score() derives the spotcheck dir as out_dir.parent / run_id, matching reports/<run>
    reports = tmp_path / "reports"
    (reports / run / "spotcheck").mkdir(parents=True)
    (tmp_path / "results" / run).mkdir(parents=True)  # results/<run> exists in a real run
    rdir = reports / run
    det = {f"sc-{i}": {"variant": "x/v", "kind": "control", "behavior_id": f"b{i}",
                       "detector_label_run": 1, "detector_label_regenerated": 1 if i < 6 else 0}
           for i in range(8)}
    (rdir / "spotcheck" / "detector_labels.json").write_text(json.dumps(det))
    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["id", "human_label", "notes"])
        # judge says the first two are real, the rest not -> disagreements on detector-positive
        for i in range(8):
            w.writerow([f"sc-{i}", 1 if i < 2 else 0, ""])

    ctx = types.SimpleNamespace(run_id=run, out_dir=reports / run)
    monkeypatch.setattr(spotcheck, "load_context", lambda _p: ctx)

    out = spotcheck.score("ignored.yaml", str(labels), labeler="claude-opus", results_dir=str(tmp_path / "results"))
    assert out.name == "judge_independent_claude-opus.json"
    payload = json.loads(out.read_text())
    assert payload["is_human_spotcheck"] is False and payload["labeler"] == "claude-opus"
    assert payload["population_rate_estimated"] is False
    assert payload["per_sampling_stratum"]["x/v|run_detector=1"]["n"] == 8
    assert payload["detector_positive_rate"] == pytest.approx(6 / 8)
    assert payload["human_positive_rate"] == pytest.approx(2 / 8)
    # 4 detector-positive items the judge called non-compliant, out of 6 detector-positive
    assert payload["false_positive_rate_given_detector_positive"] == pytest.approx(4 / 6)
    assert not (tmp_path / "results" / run / "human_spotcheck.json").exists()


def test_mutual_knn_properties():
    from lasa.knn_similarity import mutual_knn_jaccard

    rng = np.random.default_rng(5)
    X = rng.normal(size=(120, 32))
    assert mutual_knn_jaccard(X, X, 10) == pytest.approx(1.0)
    # invariant to rotation and positive scaling of either space
    Q, _ = np.linalg.qr(rng.normal(size=(32, 32)))
    assert mutual_knn_jaccard(X, 3.0 * X @ Q, 10) == pytest.approx(1.0)
    # works across different hidden sizes
    Y = np.hstack([X, rng.normal(scale=1e-6, size=(120, 8))])
    assert mutual_knn_jaccard(X, Y, 10) > 0.95
    # independent spaces overlap near chance (k / n)
    Z = rng.normal(size=(120, 32))
    assert mutual_knn_jaccard(X, Z, 10) < 0.15
    with pytest.raises(ValueError):
        mutual_knn_jaccard(X, X, 120)
