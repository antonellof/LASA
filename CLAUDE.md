# CLAUDE.md

Standing rules for any agent working in this repository. Read this before writing code or running anything.

## What this project is

LASA (Lineage Attack Surface Analyzer) is a defensive security research tool. It measures whether a fine-tuned LLM inherits jailbreak vulnerabilities from its open-weight base model, and it predicts which inherited weaknesses will still work without sending harmful prompts to the target.

The goal is a proof of concept that validates one hypothesis and produces data for a research paper. The hypothesis: transfer of a base-derived jailbreak to a fine-tuned variant is predictable from jailbreak strength on the base and representational similarity between base and variant.

This is the same category of work as garak (NVIDIA) and PyRIT (Microsoft). Those ship as defensive tools because they are scoped and disclosed responsibly. LASA follows the same rules.

## Hard boundaries

These are not style preferences. They define whether the project is legitimate. They are recorded as decisions in docs/ADR.md (ADR-0001 to ADR-0005).

1. **Authorized targets only.** LASA runs against models the operator owns, models pulled from public hubs for research, or third-party systems with written authorization. The code must refuse to run a verification pass without an authorization record. There is no feature for attacking arbitrary closed APIs.

2. **No turnkey attack generation against third parties.** The PoC orchestrates published, open-source attack implementations (see below) against owned models to measure them. It does not add novel attack capability whose purpose is bypassing safety on systems the operator does not control.

3. **Prediction is offline.** The transfer-prediction stage uses only the open base model and the owned variant. It never queries a third-party target with harmful content. This is a design feature, not an afterthought, and it is the main scientific claim.

4. **Responsible disclosure.** Any finding about a publicly released model goes to the model owner before publication. The paper reports aggregate results and methodology, not a ready-to-use exploit set.

5. **Evaluation uses established harmful-behavior benchmarks** (HarmBench, JailbreakBench, AdvBench). Do not author new harmful prompt corpora. Reuse the public sets so results are comparable and no new harmful content is created.

If a change would weaken any of these, stop and raise it with the operator instead of implementing it.

## Decisions (ADRs)

Record every architectural decision in docs/ADR.md. Before you change something a record covers, read the record first. Do not edit an accepted record. Add a new one that supersedes it and link back. A pull request that changes an architectural choice without an ADR entry is incomplete.

## Attack primitives: reuse, do not reinvent

The PoC wraps existing open-source implementations. It does not reimplement the attacks from scratch.

- Gradient suffix search: use the published GCG reference implementation or garak's probe for it.
- Refusal-direction analysis and ablation: use the public implementation released with Arditi et al. "Refusal in Language Models Is Mediated by a Single Direction."
- Behavioral probing and scoring: use garak probes and detectors where they fit.

LASA's own code is the glue: lineage detection, similarity measurement, the transfer-prediction score, the orchestration, and the reporting. That glue is the contribution. The attacks are prior art.

## Writing conventions

- Do not use the em dash character anywhere in code, comments, docs, or commit messages.
- Prose in docs: short sentences, active voice, plain words. No marketing language.
- Keep every generated report stamped with the exact version of each wrapped attack tool, so a result is reproducible.

## Repository layout

```
lasa/
  CLAUDE.md          this file
  README.md          short intro and quickstart
  docs/
    prompt.md        the build brief for the PoC
    ARCHITECTURE.md  the design
    ADR.md           architecture decision records
  src/lasa/          package code (built per the brief)
  configs/           run configs and the authorization schema
  experiments/       experiment definitions and result manifests
  reports/           generated findings (gitignored except samples)
  paper/             the paper draft and figures
  tests/             unit and smoke tests
```

## Engineering rules

- Python 3.11+. Pin all dependencies. Record the environment in every result manifest.
- Every run writes a manifest: model hashes, tool versions, seed, authorization record id, timestamp.
- Determinism where possible. Log seeds. Transfer measurement is statistical, so report attack success rate with confidence intervals, never a single pass/fail.
- An LLM judge scores attack success. Always pair it with a human spot-check on a sampled subset and report agreement.
- No secrets in the repo. Model API keys for owned endpoints come from the environment.

## Testing

- Unit tests for lineage detection, similarity scoring, and the transfer-prediction score on synthetic fixtures.
- An integration smoke test that runs the full pipeline on one small owned model pair (for example a public base and a public fine-tune of it) and checks the manifest and report are well formed.
- Do not put harmful model outputs in test fixtures. Tests check structure and scores, not content.

## Git

- Branch off main. Do not commit to main directly.
- Commit messages end with the attribution lines the session provides.
- The `reports/` directory is gitignored except for one redacted sample.
