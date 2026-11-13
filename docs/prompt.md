# Build brief: LASA proof of concept

This brief tells a coding agent what to build. Read CLAUDE.md first. The hard boundaries there govern everything below.

## Objective

Build the smallest PoC that can answer one question with data:

> For a fine-tuned model derived from a known open-weight base, can we predict which base-derived jailbreaks still transfer, using only the base and the owned variant, without querying any third-party target?

Success means: a reproducible pipeline that, given a base and one or more owned fine-tuned variants, produces a transfer-prediction score per attack and a measured attack success rate per attack, plus the correlation between prediction and measurement. That correlation is the result the paper reports.

## Scope for v0 (PoC)

In scope:
- One or two model families only. Suggested: Llama 3 8B and Qwen2.5 7B, because many public fine-tunes exist.
- Public fine-tunes of those bases, pulled locally, acting as stand-ins for "private variants the operator owns." This keeps everything authorized and offline.
- Two attack primitives, both wrapped from public code: refusal-direction ablation and GCG suffix transfer.
- Evaluation on a public harmful-behavior benchmark (HarmBench or JailbreakBench), standard subset.

Out of scope for v0:
- Closed or third-party API targets.
- Distillation of a closed model into a proxy.
- Lineage detection on unknown or heavily modified checkpoints. In v0 the base is declared, not inferred. Detection comes later.
- Any production guardrail bypass as a product feature.

## The authorization gate (build this first)

Before any verification pass, the tool loads an authorization record and refuses to proceed without one. For v0 the record asserts the targets are public research models or operator-owned. Schema in `configs/authorization.schema.json`:

- `id`: string
- `operator`: string (name and contact)
- `scope`: list of model identifiers this record authorizes
- `basis`: one of `owned`, `public-research`, `written-authorization`
- `authorized_on`: date
- `notes`: string

The verification stage checks that every target model id appears in an authorization record whose `basis` is one of the allowed values. If not, it exits with a clear message and writes nothing.

## Pipeline stages

Build these as separate, testable modules. Each stage writes an artifact the next stage reads, so the pipeline is resumable.

1. **load**: pull base and variants locally. Record model hashes. No network during later stages.
2. **similarity**: compute per-layer representational similarity between base and each variant on a neutral probe set (a few hundred benign prompts). Produce one similarity profile per variant. Use centered kernel alignment or cosine of mean-pooled hidden states. Document the choice.
3. **attack_base**: run the two wrapped attack primitives on the base against the benchmark. For each harmful behavior, record whether the attack succeeded on the base and a continuous jailbreak-strength measure (for example, mean judge score over multiple generations, or margin on the refusal direction). Store the resulting attacks with metadata.
4. **predict**: for each stored attack and each variant, compute the transfer-prediction score as a function of base jailbreak strength and base-to-variant similarity. Start with the simplest defensible form (product of normalized strength and similarity) and keep the function pluggable so alternatives can be compared.
5. **verify**: this stage requires a valid authorization record. Run the top-K predicted attacks against each owned variant. Measure attack success rate with an LLM judge, multiple generations per attack, with confidence intervals. Sample a subset for human spot-check and record judge-human agreement.
6. **report**: emit a JSON findings file and a human-readable report. Include the prediction-versus-measurement correlation, per-attack detail, and the full manifest. Map each finding to an OWASP LLM Top 10 category.

## What the PoC must output

- A results manifest per run (model hashes, tool versions, seed, authorization id, timestamp).
- Per variant: a table of attacks with predicted transfer score and measured attack success rate.
- The headline number: rank correlation (Spearman) between predicted score and measured attack success rate, with a baseline comparison against base-only attack success rate as predictor.
- A figure: predicted score on x, measured attack success rate on y, one point per attack, colored by variant.

## Baselines to beat

The prediction is only interesting if it beats the obvious baseline. Compare the transfer-prediction score against:
- Random ordering.
- Base-only attack success rate (does strength alone predict transfer, or does similarity add signal?).

Report both. If similarity adds nothing over strength alone at this scale, that is a real and publishable negative result. Do not hide it.

## Milestones

- M1: load + similarity working on one base and two public variants, with tests.
- M2: attack_base wrapping both primitives, producing stored attacks and strength scores.
- M3: predict + verify + report end to end on the M1 models, headline correlation produced.
- M4: second family added, results compared across families.

Stop at M4 and hand results to the paper stage. Do not expand scope mid-build.

## Definition of done for v0

- `lasa run --config experiments/llama3-8b.yaml` runs the full pipeline on authorized models and writes a report.
- The smoke test passes in CI.
- The headline correlation and the baseline comparison are in the report.
- No stage can run verification without an authorization record.

## Phase 2 brief: prospective transfer study (added 2026-11-12)

The v0 pipeline above is built. Its correlations are exploratory, and reviews found measurement problems in them (ADR-0013). Phase 2 answers a narrower question with rules frozen before outcomes. Read `docs/STUDY.md` and ADR-0014 to ADR-0024 first.

Question:

> Does a suffix optimized only on the open source model raise harmful compliance on declared derivatives, beyond a bare prompt and an inert suffix, on behaviors held out before any label exists? And does a source-only score pick the behaviors where it does?

Rules for an agent working on Phase 2:

- A plan is frozen by `lasa study initialize` and never edited. A protocol change means a new study ID and an ADR.
- Nothing is optimized on a target. Targets only receive prompts built from the source.
- Choose rosters from model cards before any outcome. Prefer declared children of the source. Exclude reasoning models. Do not drop a target by hand after seeing data; the frozen headroom rule does that.
- State the detectable effect before the run. A null result below that power is reported as uninformative, not as absence of transfer.
- The classifier labels are provisional until two human annotators pass the frozen audit criterion.
- Raw suffixes and completions stay in `reports/` or the private evidence dataset (ADR-0022). The repository gets digests, rates and tests only.
- New model IDs enter the authorization record only by the operator's hand.
- Cost is a constraint. Run the calibration job and report the estimate before a paid search.

Done for Phase 2 means: both v3 plans frozen with public commitments, evidence complete, the human audit scored, `summary.json` committed for each study, disclosure notices sent to the owners of every evaluated checkpoint, and the paper reporting the result that the frozen rules give.

