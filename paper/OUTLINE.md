# Paper outline

Working title: Predicting Inherited Jailbreak Risk in Fine-Tuned LLMs: Refusal-Direction Alignment Outperforms Representational Similarity

Original working title: Predicting Inherited Jailbreak Vulnerability in Fine-Tuned LLMs from Lineage and Representational Similarity. Changed after the five-family results refuted the CKA hypothesis (see paper Section 1).

This outline is written to be filled from LASA (Lineage Attack Surface Analyzer) result manifests. Claims are limited to what the experiments measure.

## Abstract
Fine-tuning an open-weight base is the default deployment path. The base becomes a shared attack surface. We ask whether inherited jailbreak vulnerability in a fine-tuned variant is predictable from base jailbreak strength and base-to-variant representational similarity, without querying the variant with harmful prompts. We measure this on public base and fine-tune pairs and report the correlation between prediction and measured attack success.

## 1. Introduction
- Fine-tuning reuses a common base across many deployed models.
- Prior work shows vulnerabilities persist from base to variant.
- Gap: predicting which inherited attacks transfer, offline, before any harmful query to the target.
- Contribution list.

## 2. Related work
- Gradient suffix attacks and their transfer (GCG, AmpleGCG, and follow-ups).
- Refusal as a single direction and weight-level jailbreaks (Arditi et al. and successors).
- Transfer predicted by strength and similarity (Angell et al.).
- Inherited vulnerability in fine-tunes (One Leak Away; grey-box VLM transfer).
- Red-team tooling (garak, PyRIT, promptfoo) and where a prediction layer fits.

## 3. Threat model
- Operator owns the target variant, or tests with authorization. This is the defensive framing.
- Adversary of concern has base access and seeks transfer to a variant.
- Prediction is offline by construction. State this as both method and safety property.

## 4. Method
- Representational similarity profile.
- Base attack strength from wrapped primitives.
- Transfer-prediction score and its pluggable forms.
- Verification protocol on owned variants.

## 5. Experimental setup
- Families and variants (declared bases, public fine-tunes).
- Benchmark (public harmful-behavior set).
- Judge and human spot-check protocol.
- Baselines: random, base-only ASR.
- Reproducibility: hashes, tool versions, seeds.

## 6. Results
- Headline correlation per family and pooled.
- Does similarity add signal over strength alone.
- Per-category breakdown.
- Safety-tuned variants: reduced but nonzero inheritance (hypothesis H2).

## 7. Negative results and threats to validity
- Where prediction fails.
- Scale ceiling.
- Metric sensitivity.
- Judge error and agreement numbers.

## 8. Ethics and responsible disclosure
- Authorized targets only.
- Public benchmarks, no new harmful corpus.
- Disclosure to owners of any publicly released model before publication.
- Release decision for code: measurement harness yes, ready-to-use exploit set no.

## 9. Limitations and future work
- Lineage inference on unknown checkpoints.
- Distillation proxy for bases with no public sibling.
- Larger scales.

## Hypotheses (restated for the experiments)
- H1: transfer correlates with similarity beyond what strength alone explains.
- H2: safety fine-tuning reduces but does not remove inherited vulnerability.
- H3: the combined score predicts transfer better than base-only ASR.

## Venue
Workshop first (an ML security or ACL/ICLR workshop), then a full paper. The L-8 thesis can serve as a first validation chapter.
