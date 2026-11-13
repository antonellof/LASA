"""Command line entry point.

Usage:
    lasa run --config experiments/<name>.yaml
    lasa aggregate

`run` executes the pipeline stages in order, resuming from completed artifacts.
`aggregate` rebuilds the cross-family paper tables and figures from committed
run artifacts without loading any model.
"""

from __future__ import annotations

import argparse
import sys

from lasa.context import load_context
from lasa.pipeline import STAGES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lasa")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the pipeline from a config")
    run.add_argument("--config", required=True, help="path to an experiment config")
    run.add_argument(
        "--from-stage",
        default=None,
        help="resume from this stage instead of the start",
    )

    fetch = sub.add_parser("fetch", help="download model checkpoints from Hugging Face to local storage")
    fetch.add_argument("--config", required=True, help="path to an experiment config")

    agg = sub.add_parser(
        "aggregate",
        help="rebuild the cross-family tables and figures from committed run artifacts (no models loaded)",
    )
    agg.add_argument("--results", default="experiments/results", help="directory holding one folder per run")
    agg.add_argument("--lineage", default="experiments/lineage.yaml", help="declared lineage per checkpoint")
    agg.add_argument("--out", default="experiments/results", help="where to write aggregate.json and aggregate.md")
    agg.add_argument("--fig-dir", default="paper/figures", help="where to write figures; pass '' to skip")
    agg.add_argument("--seed", type=int, default=0)

    sc = sub.add_parser("spotcheck", help="human spot-check export/score and generation-budget check (authorized targets only)")
    sc_sub = sc.add_subparsers(dest="sc_command", required=True)
    sc_exp = sc_sub.add_parser("export", help="regenerate a stratified sample and write a blind labeling sheet")
    sc_exp.add_argument("--config", required=True)
    sc_exp.add_argument("--n-per-variant", type=int, default=10)
    sc_exp.add_argument("--max-new-tokens", type=int, default=None)
    sc_exp.add_argument("--seed", type=int, default=0)
    sc_score = sc_sub.add_parser("score", help="score a filled labeling sheet against the detector")
    sc_score.add_argument("--config", required=True)
    sc_score.add_argument("--labels", required=True)
    sc_score.add_argument("--labeler", default="human", help="'human' for the protocol spot-check; any other tag marks an independent judge")
    sc_bud = sc_sub.add_parser("budget", help="rerun the attack-free control for one variant at a longer budget")
    sc_bud.add_argument("--config", required=True)
    sc_bud.add_argument("--variant", required=True)
    sc_bud.add_argument("--max-new-tokens", type=int, default=256)

    knn = sub.add_parser("knn", help="mutual-kNN similarity (Angell et al.) between base and variants on benign probes")
    knn.add_argument("--config", required=True)

    study = sub.add_parser("study", help="frozen prompt-transfer study with exact evidence and an independent judge")
    study.add_argument("stage", choices=["initialize", "search", "generate", "judge", "audit", "audit-score", "analyze", "status"])
    study.add_argument("--config", required=True)
    study.add_argument("--labeler", help="identified person or model that supplied audit labels")
    study.add_argument("--labeler-kind", choices=["human", "model"])
    study.add_argument("--sheet", default="human_labels.csv",
                       help="label sheet to score: human_labels.csv or human_labels_<n>.csv; audit-score only")
    study.add_argument("--allow-analysis-revision", action="store_true",
                       help="reanalyze immutable evidence with explicitly recorded new analysis code; analyze only")

    args = parser.parse_args(argv)

    if args.command == "study":
        from lasa import study as experiment
        try:
            if args.allow_analysis_revision and args.stage != "analyze":
                parser.error("--allow-analysis-revision is valid only for analyze")
            if args.stage == "status":
                import json
                print(json.dumps(experiment.status(args.config), indent=2))
                return 0
            if args.stage == "audit-score":
                from lasa.audit_scoring import score_audit
                output = score_audit(args.config, labeler=args.labeler, labeler_kind=args.labeler_kind,
                                     sheet=args.sheet)
            elif args.stage == "analyze":
                from lasa.study_analysis import analyze
                output = analyze(args.config, allow_analysis_revision=args.allow_analysis_revision)
            else:
                if args.allow_analysis_revision:
                    parser.error("--allow-analysis-revision is valid only for analyze")
                output = getattr(experiment, args.stage)(args.config)
        except (PermissionError, ValueError, FileNotFoundError, FileExistsError) as exc:
            print(f"stop: {exc}")
            return 4
        print(f"[study:{args.stage}] wrote {output}")
        return 0

    if args.command == "knn":
        from lasa import knn_similarity

        print(f"[knn] wrote {knn_similarity.run(args.config)}")
        return 0

    if args.command == "spotcheck":
        from lasa import spotcheck

        try:
            if args.sc_command == "export":
                out = spotcheck.export(args.config, args.n_per_variant, args.seed, args.max_new_tokens)
            elif args.sc_command == "score":
                out = spotcheck.score(args.config, args.labels, args.labeler)
            else:
                out = spotcheck.budget(args.config, args.variant, args.max_new_tokens)
        except PermissionError as exc:
            print(f"stop: {exc}")
            return 4
        print(f"[spotcheck] wrote {out}")
        return 0

    if args.command == "aggregate":
        from lasa import aggregate

        payload = aggregate.run(
            results_dir=args.results,
            lineage_path=args.lineage,
            out_dir=args.out,
            fig_dir=args.fig_dir or None,
            seed=args.seed,
        )
        print(
            f"[aggregate] {payload['n_experiment_groups']} source groups, {payload['n_model_lineages']} lineages, {payload['n_variants']} targets, "
            f"{payload['n_pairs']} attack-variant pairs -> {args.out}"
        )
        return 0

    if args.command == "fetch":
        import os
        from pathlib import Path
        from huggingface_hub import snapshot_download
        from lasa.pipeline.load import resolve_local_path

        env_file = Path(".env")
        if env_file.exists() and not os.environ.get("HF_TOKEN"):
            for line in env_file.read_text().splitlines():
                if line.strip().startswith("HF_TOKEN="):
                    os.environ["HF_TOKEN"] = line.split("=", 1)[1].strip().strip('"').strip("'")
        ctx = load_context(args.config)
        models_dir = Path(ctx.config.get("models_dir", "models"))
        entries = [ctx.config["base"], *ctx.config.get("variants", [])]
        if "independent_judge" in ctx.config:
            entries.append(ctx.config["independent_judge"])
        allow = ["*.safetensors", "*.json", "tokenizer.model", "*.txt", "*.jinja"]
        for entry in entries:
            local = resolve_local_path(entry, models_dir)
            print(f"[{ctx.run_id}] fetching {entry['model_id']} -> {local}")
            snapshot_download(
                repo_id=entry["model_id"],
                local_dir=str(local),
                allow_patterns=allow,
                revision=entry.get("revision"),
                token=os.environ.get("HF_TOKEN"),
            )
        return 0

    if args.command == "run":
        ctx = load_context(args.config)
        names = [s.__name__.split(".")[-1] for s in STAGES]
        start = 0
        if args.from_stage:
            if args.from_stage not in names:
                print(f"unknown stage: {args.from_stage}. choose from: {', '.join(names)}")
                return 2
            start = names.index(args.from_stage)
        for stage in STAGES[start:]:
            name = stage.__name__.split(".")[-1]
            print(f"[{ctx.run_id}] stage: {name}")
            try:
                path = stage.run(ctx)
            except NotImplementedError as exc:
                print(f"stop: {name} is not implemented yet ({exc})")
                return 3
            except PermissionError as exc:
                print(f"stop: {exc}")
                return 4
            except FileNotFoundError as exc:
                print(f"stop: {name} needs local checkpoints. {exc}")
                return 5
            print(f"[{ctx.run_id}] wrote {path}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
