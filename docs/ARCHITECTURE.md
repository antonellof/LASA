# Architecture

Design for the LASA (Lineage Attack Surface Analyzer) proof of concept. Read CLAUDE.md and prompt.md first.

## One-line summary

A resumable pipeline that measures and predicts inherited jailbreak vulnerability in fine-tuned LLMs, offline, on authorized models, and emits reproducible reports for a research paper.

## Design principles

- Offline prediction. The predict stage never touches a third-party target. This is the scientific claim and the safety property at once.
- Authorization is a gate, not a flag. The verify stage cannot run without a valid record.
- Wrap, do not reinvent. Attack primitives come from published open-source code. LASA owns the measurement and prediction glue.
- Every result is reproducible. Model hashes, tool versions, and seeds travel with every artifact.
- Statistical, not binary. Transfer is a rate with a confidence interval, never a single yes or no.

## Component view

```
+-------------------+
|   authorization   |  loads and validates records; gates `verify`
+-------------------+
          |
+-------------------+     +------------------+
|      load         |---->|  model registry  |  local checkpoints, hashes, no later network
+-------------------+     +------------------+
          |
+-------------------+
|    similarity     |  per-layer base-to-variant representation similarity
+-------------------+
          |
+-------------------+     +------------------+
|   attack_base     |---->|  attack adapters |  wraps GCG, refusal-direction (public code)
+-------------------+     +------------------+
          |
+-------------------+
|     predict       |  transfer score = f(strength, similarity); pluggable f
+-------------------+
          |
+-------------------+     +------------------+
|     verify        |---->|   judge + human  |  owned variants only; ASR with CIs
+-------------------+     +------------------+
          |
+-------------------+
|     report        |  JSON findings + readable report + figure + manifest
+-------------------+
```

## Module responsibilities

### authorization
Loads records from `configs/`, validates against the schema, and exposes `is_authorized(model_id) -> bool`. The verify stage calls this for every target and aborts the run if any target is unauthorized. Writes the record id into the manifest.

### load
Pulls base and variant checkpoints to local storage. Computes a content hash per checkpoint. After this stage the pipeline runs offline for everything except the LLM judge, which itself can be a local model to stay fully offline.

### similarity
Runs a fixed neutral probe set through base and each variant, collects hidden states per layer, and computes a similarity profile. Two interchangeable metrics: centered kernel alignment, and cosine of mean-pooled hidden states. The profile is a vector over layers plus a scalar summary. Grounded in the finding that pretrained and fine-tuned models retain strong intermediate-representation similarity, which is why inherited vulnerability is plausible in the first place.

### attack_base
Runs the wrapped attack primitives on the base against the benchmark behaviors.

- Refusal-direction adapter: extracts the refusal-mediating direction from contrast prompts, measures each behavior's margin along it, and records an ablation-based success signal. Uses the public reference implementation.
- GCG adapter: runs suffix optimization on the base for each behavior and records success and loss. Uses the public reference implementation or garak's probe.

Output per behavior: a success flag on the base and a continuous strength score. Strength is the input to prediction, not just the binary.

### predict
For each (attack, variant) pair, computes a transfer-prediction score. v0 form:

```
score = normalize(strength_on_base) * similarity(base, variant)
```

The function is pluggable so the paper can compare forms (strength only, similarity only, product, learned weighting). No target queries here. This stage produces a ranked prediction per variant.

### verify
Gated by authorization. Runs the top-K predicted attacks against each owned variant, multiple generations per attack. An LLM judge scores each generation for attack success. Reports attack success rate per attack with bootstrap confidence intervals. A sampled subset goes to human spot-check; judge-human agreement is recorded. This stage exists to produce ground truth to test the prediction against, on models the operator owns.

### report
Joins predictions and measurements. Emits:
- `findings.json`: per-attack predicted score, measured ASR, CI, OWASP category.
- `report.md`: readable summary, headline correlation, baseline comparison.
- `scatter.png`: predicted score versus measured ASR.
- `manifest.json`: hashes, tool versions, seed, authorization id, timestamp.

## Data contracts

Artifacts between stages are JSON plus tensor files on disk, keyed by run id. Each stage reads the previous artifact and writes its own, so a run resumes from any completed stage. Schemas live in `src/lasa/schemas/`.

## Evaluation methodology

- Benchmark: a standard public harmful-behavior set (HarmBench or JailbreakBench). No new harmful corpus.
- Metric: attack success rate judged by an LLM judge, validated against human spot-check.
- Headline: Spearman rank correlation between predicted transfer score and measured ASR across attacks, per variant and pooled.
- Baselines: random ordering, and base-only ASR as predictor. The contribution is whether similarity adds signal over strength alone.
- Honesty: a null result (similarity adds nothing at this scale) is reported as a result.

## Threats to validity (track these, they become a paper section)

- Judge error. Mitigation: human spot-check and reported agreement.
- Benchmark narrowness. Mitigation: report per-category results, not one aggregate.
- Scale ceiling. Small models may not predict frontier behavior. Mitigation: state the scale tested and claim nothing beyond it.
- Similarity metric choice drives the result. Mitigation: report both metrics; if they disagree, say so.
- Overfit prediction function. Mitigation: if a learned weighting is used, hold out variants and report out-of-sample.

## From PoC to paper

The pipeline is the experiment. Once M4 is done, `lasa aggregate` (ADR-0010) reads the committed run artifacts and `experiments/lineage.yaml` and builds:
- The figures: per-layer similarity, variant-level transfer, and predicted versus measured per attack.
- The correlation tables with baselines (random ordering, base-only ASR, base strength, and the headroom baseline).
- `experiments/results/aggregate.md`, which the paper quotes. CI fails if it drifts from the code.

The threats-to-validity section comes from the tracked list above.

See `paper/OUTLINE.md` for the structure.

## Explicitly not built in v0

- Inference-time attack service or API.
- Any path that sends optimized attacks to systems outside the authorization scope.
- Lineage inference on unknown checkpoints (declared base only in v0).
- Closed-model distillation.

These are noted so the boundary is visible in the design, not just in prose.
