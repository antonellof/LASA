# Architecture Decision Records

A running log of the decisions that shape LASA (Lineage Attack Surface Analyzer). Newest at the bottom. One
entry per decision. Keep each short. When a decision changes, do not edit the
old record. Add a new one that supersedes it and link back.

Format per entry: id, date, status, context, decision, consequences.

---

## ADR-0001: Prediction is offline
- Date: 2026-11-07
- Status: accepted
- Context: The core claim is that inherited vulnerability can be predicted before contact with the target. It is also the property that keeps the tool defensible.
- Decision: The predict stage uses only the open base and the owned variant. It never queries a third-party target with harmful content.
- Consequences: Prediction quality is bounded by how well base strength and similarity capture transfer. The paper must report where this bound bites.

## ADR-0002: Wrap published attacks, do not reinvent
- Date: 2026-11-07
- Status: accepted
- Context: Writing new attack code adds capability and risk without adding science. The contribution is measurement and prediction, not new attacks.
- Decision: Attack primitives wrap public implementations (GCG reference or garak probe; Arditi et al. refusal-direction code). LASA owns the glue.
- Consequences: Results are comparable to prior work. We depend on upstream tools, so every report pins their exact versions.

## ADR-0003: Authorization is a gate, not a flag
- Date: 2026-11-07
- Status: accepted
- Context: The verify stage runs real attacks against models. It must only ever touch authorized targets.
- Decision: verify calls require_authorized for every target before doing anything. No record, no run. Allowed bases: owned, public-research, written-authorization.
- Consequences: A run against an unlisted model fails fast and writes nothing. Authorization records travel in the manifest.

## ADR-0004: Public benchmarks only
- Date: 2026-11-07
- Status: accepted
- Context: Creating a new harmful prompt corpus makes new harmful content and breaks comparability.
- Decision: Evaluate on HarmBench, JailbreakBench, or AdvBench. Do not author new harmful prompts.
- Consequences: Coverage is limited to what public sets contain. Report per-category results so the limit is visible.

## ADR-0005: v0 scope is two families, declared bases
- Date: 2026-11-07
- Status: accepted
- Context: The PoC must validate one hypothesis cheaply, not ship a product.
- Decision: v0 covers one or two families (Llama 3 8B, Qwen2.5 7B), uses public fine-tunes as owned-variant stand-ins, and takes the base as declared rather than inferred.
- Consequences: Lineage inference on unknown checkpoints and closed-model distillation are deferred to future work.

## ADR-0006: Layer-aware similarity blending and continuous base strength
- Date: 2026-11-07
- Status: accepted
- Context: Global mean similarity across all layers can dilute divergence at the specific layers that mediate refusal. Binary base attack outcomes also lose rank resolution among attacks that all succeed or all fail on the base.
- Decision: Combine global CKA/cosine similarity (50%) with layer-specific similarity (50% at the selected refusal layer for refusal-direction attacks, or upper-half layer mean for GCG). Score base attack strength continuously on [0, 1] by anchoring base judge outcome (0.5 * base_asr) and adding a within-primitive normalized margin (0.5 * boundary proximity for refusal direction, or 0.5 * exp(-loss) for GCG).
- Consequences: Transfer prediction differentiates both across variants and across attacks within a variant while remaining offline.

## ADR-0007: Attack-free control (Delta ASR) and refusal-subspace cosine alignment
- Date: 2026-11-07
- Status: accepted
- Context: Raw attacked ASR on a variant conflates true attack transfer with behaviors the variant already answers without any attack (no-attack baseline). In addition, neutral-probe CKA remains near 1.0 on low-rank uncensored fine-tunes (such as Dolphin3.0 and Heretic) and reasoning distillations (such as DeepSeek-R1-Distill), making benign CKA blind to refusal removal.
- Decision: Stage 2 (`similarity`) also measures the cosine alignment between the base model's extracted refusal direction and each variant's refusal direction at the selected layer (`refusal_direction_cosine`). Stage 5 (`verify`) runs every behavior on the variant without any suffix or hook first (`no_attack_asr`) and records `transfer_delta_asr = measured_asr - no_attack_asr`. Stage 6 (`report`) reports exact permutation p-values and 95% bootstrap confidence intervals for both raw ASR and control-adjusted Delta ASR.
- Consequences: LASA separates preexisting variant compliance from inherited attack transfer and detects safety removal offline without querying third-party targets.

## ADR-0008: v0 evaluated on five small families (supersedes ADR-0005 in part)
- Date: 2026-11-08
- Status: accepted
- Supersedes: ADR-0005 (model choice only; declared bases and public fine-tunes as stand-ins still hold)
- Context: ADR-0005 named Llama 3 8B and Qwen2.5 7B. The runs that produced the paper used local Apple Silicon hardware, which fits 0.5B to 2B checkpoints. Two families at that size gave too few variants for a variant-level test.
- Decision: The v0 evidence base is five families at 0.5B to 2B (Qwen2.5-1.5B, Qwen2.5-0.5B, Llama-3.2-1B, SmolLM2-1.7B, Gemma-2-2B), 14 checkpoints, 12 JailbreakBench behaviors. The 8B config stays in the repo for GPU runs and is not part of the reported results.
- Consequences: The paper claims nothing beyond 2B. The brief's definition of done (`lasa run --config experiments/llama3-8b.yaml`) is open until the 8B run happens.

## ADR-0009: Default similarity mode is refcos_only (supersedes ADR-0006 in part)
- Date: 2026-11-08
- Status: accepted
- Supersedes: ADR-0006 (the CKA blend as the default similarity term; continuous base strength still holds)
- Context: Benign CKA showed no association with control-adjusted transfer at either level, and multiplying by it changed pooled attack-level rho by 0.0015. The committed run artifacts were produced by an earlier `predict.py` in which the `cka` mode multiplied refusal-direction attacks by RefCos. Under the current code that behavior is `refcos_times_cka`, so the committed configs no longer reproduced the committed results.
- Decision: `refcos_only` is the default `similarity_metric`. The five committed run configs set `refcos_times_cka`, which reproduces their committed predictions exactly. The report manifest records `similarity_metric` so a result always names the mode that produced it.
- Consequences: New runs score with RefCos alone. Old manifests lack the field; their mode is documented here.

## ADR-0010: Cross-family aggregate analysis is code, not hand-computed
- Date: 2026-11-08
- Status: accepted
- Context: The paper's variant-level table (Table 2) had no code path in the repo. Two of its family-stratified p-values were below the smallest value the test can produce, and three per-family rows of Table 3 did not match the committed artifacts.
- Decision: `lasa aggregate` rebuilds every cross-family table and figure from committed `findings.json` and `similarity.json` plus `experiments/lineage.yaml`. It loads no model and reads no completion text. Permutation tests enumerate all permutations when there are at most 50,000 and report the smallest attainable p. Variant-level analyses include the headroom baseline (1 minus no-attack ASR) and a partial correlation controlling for no-attack ASR. CI fails if the committed `aggregate.md` drifts from what the code produces.
- Consequences: Paper numbers are quoted from `experiments/results/aggregate.md`. Changing a statistic means changing code and regenerating, not editing the paper.

## ADR-0011: Human spot-check runs on regenerated samples, outside the repository
- Date: 2026-11-08
- Status: accepted
- Context: The verify stage stores labels only, never completion text (ADR-0004 and boundary 4), so the generations it labeled cannot be reviewed after the fact. The project rules require a human spot-check with reported agreement, and none had been done.
- Decision: `lasa spotcheck export` draws a stratified sample (half detector-positive, half detector-negative per variant), regenerates each item greedily under the run's conditions, and writes the texts and a blind labeling sheet under the gitignored `reports/<run>/spotcheck/`. Detector labels go to a separate file so the labeler does not see them. `lasa spotcheck score` writes rates, kappa, and the confusion matrix to `experiments/results/<run>/human_spotcheck.json`, which `lasa aggregate` includes. A `--labeler` other than `human` (for example an LLM asked to label the same blind sheet) is recorded as an independent judge in its own file and is never counted as the human check. `lasa spotcheck budget` reruns the attack-free control for one variant at a longer budget and reports the detector rate before and after stripping a reasoning block.
- Consequences: Spot-checked texts are fresh samples from the same conditions, not the exact generations the run labeled, because sampled decoding cannot be replayed across batch compositions. Agreement measures the detector on this distribution. No completion text enters the repository.

## ADR-0012: Remove the 8B run configuration (supersedes ADR-0008 in part)
- Date: 2026-11-08
- Status: accepted
- Supersedes: ADR-0008 (the consequence that the 8B config stays in the repo)
- Context: An 8B run (Llama-3-8B-Instruct with Hermes-2-Pro and Dolphin-2.9) completed similarity, attack, and prediction, but its verification stage did not finish on a 34 GB Apple Silicon machine. The operator chose not to retry it and not to report it.
- Decision: Delete `experiments/llama3-8b.yaml`. The paper and README make no 8B claim. `experiments/llama3-8b.example.yaml` stays as the generic copy-and-edit template. The `verify.batch_size` setting added for the attempt stays, since it applies to any large model.
- Consequences: The build brief's definition of done (`lasa run --config experiments/llama3-8b.yaml`) is not met. A future 8B or larger run needs a GPU and a new config.

## ADR-0013: Supersede padding-dependent measurements and qualify outcome labels
- Status: accepted
- Supersedes: ADR-0007 and ADR-0010 in part (evidence status), ADR-0011 in part (audit interpretation)
- Context: Contrast collection indexed the final token as length minus one despite left padding. Historical directions, RefCos, and intervention outcomes may depend on padding and batch composition. A detector-stratified audit of regenerated greedy outputs cannot estimate harmful-compliance prevalence among the original stochastic generations.
- Decision: Use the final attended position, record `last-attended-v2` provenance, and reject legacy refusal directions in verification. Preserve historical artifacts but label aggregate output and the paper as superseded. Withdraw predictive-performance claims pending fresh runs and independent outcome validation. Audit scoring records its sampling design and per-stratum confusion counts; unweighted audit rates are not population estimates. Follow `docs/STUDY.md` before claims resume.
- Consequences: No validated positive or negative generalization claim survives this correction without reruns. Human labels and full independent judging remain outstanding. Existing contrast forwarding on owned variants remains offline but is not benign-input-only.

## ADR-0014: Frozen source-only prompt-transfer study
- Status: accepted
- Context: A paper about inherited prompt weaknesses needs evidence independent of the contrast direction used by a white-box ablation. Historical detector labels cannot establish harmful compliance.
- Decision: Add `lasa study` as a separate experimental path. Freeze source-only nanoGCG search, category-stratified development/held-out behaviors, bare and token-length-matched random controls, independent local HarmBench judging, exact-generation evidence, and a blind probability-sampled human audit. Record invalid labels instead of substituting heuristics. Keep raw evidence under owner-readable gitignored reports storage. The new path does not invoke the historical refusal-direction implementation.
- Consequences: The completed 24-generation engineering pilot is not an efficacy result. The 250-step prospective study and human validation remain separate requirements. No target outcome is used to optimize a suffix or fit the source ranking.

## ADR-0015: Reviewed study integrity and source provenance
- Status: accepted
- Supersedes: ADR-0013 in part (extraction version and analysis safeguards)
- Context: Review found target-label-dependent complete-case selection, unverified classifier-weight independence, missing installed-wheel authorization schema, and an input-only local ablation approximation attributed to upstream code. Source groups were also described as independent model lineages.
- Decision: Record `last-attended-v3` with explicit mask-derived position IDs; require packaged schema validation and distinct judge weights; freeze and validate the source study's configuration, selections, rubric, code, environment, checkpoints, and complete evidence grid. Unscored held-out judgments block analysis. Disable the unverified legacy ablation attack and hooks. Report five historical source groups as four lineages, retain their outcomes only as superseded artifacts, and use new study IDs after protocol changes.
- Consequences: The source-search run started under the previous protocol was stopped before completing a behavior. Its plan remains for audit; reviewed Qwen v2 and Llama v1 require new runs. All empirical efficacy and human-audit conclusions remain open.

## ADR-0016: Prospective protocol revisions before initialization
- Date: 2026-11-08
- Status: accepted
- Supersedes: ADR-0014 in part (prompt construction, judge template, controls, audit design) and ADR-0015 in part (analysis safeguards)
- Context: A second review found that generation evaluated `instruction + " " + suffix` as one string, while nanoGCG optimized `instruction + suffix` with separately tokenized parts. On the Qwen tokenizer this changed the token IDs for every tested suffix, so the source loss and the source ranking described a prompt that was never sent. The judge used HarmBench's Llama-2 classifier template with the Mistral validation classifier, whose model card specifies its own template. Every behavior shared the generic three-token target although JailbreakBench publishes one per behavior. The development split fed no endpoint. No decision rule, power analysis, or multiplicity correction was frozen. A simple random audit would contain few classifier positives. The random-suffix null used eight English words. Qwen v2 and Llama v1 had not been initialized, so none of this touched collected outcomes.
- Decision: Build every prompt with `lasa.study.prompt_ids`, which copies nanoGCG 0.3.0's template split, and record the source loss recomputed on the evaluated IDs. Use `configs/judges/harmbench_mistral_val.json`, copied from the pinned classifier card, and require a rubric to name its classifier. Load the published JailbreakBench targets (`benchmark.targets: published`, pinned dataset revision, per-instruction digests). Add a mismatched-suffix control: each behavior receives the suffix optimized for another behavior in its split. Give the development split three frozen jobs: an attack-adequacy gate on the source, a bare-rate headroom rule for targets, and the choice of which source score is confirmed on held-out behaviors. Freeze alpha, the controls that define success, and Holm correction in a `decision` block that confirmatory plans must carry. Test primary contrasts with a behavior-level sign-flip test and top-k utility against every equally sized random selection. Draw the human audit stratified by classifier label with recorded inclusion probabilities, label it with two annotators, and report design-weighted accuracy and inter-annotator agreement. Write a public plan commitment at initialization and refuse a confirmatory search until it is committed.
- Consequences: Prospective generation counts rise by one third (6,400 planned). The pilot keeps its frozen three-condition design, generic targets and Llama-2 template; its labels are engineering checks only. Re-analysis of the pilot with this code reports its decision as `attack_inadequate_on_source`. The roster still has one primary declared child (TinySwallow) and none in the Llama protocol; adding children is an operator decision. The GCG width stays at 64; the adequacy gate reports an underpowered search instead of a null transfer result.

## ADR-0017: Single-commit history and pre-squash provenance
- Date: 2026-11-08
- Status: accepted
- Context: The operator squashed `main` to one commit and force-pushed it. Two committed artifacts cite revisions that are no longer reachable on the public remote: `experiments/results/study-qwen15-pilot/manifest.json` (`fe058f8`) and `experiments/results/study-qwen15-v1/execution_plan.json` (`89ea4ec`).
- Decision: Leave the evidence files unchanged. The content-based `code_digest` in each frozen plan remains the authoritative code identity. The pre-squash history is kept locally on the branch `backup/pre-squash-2026-11-08`. Publishing it, for example as tags on those two revisions, is the operator's choice.
- Consequences: Until those revisions are published, a reader can verify the pilot's code digest only against code supplied by the operator. Future plans record revisions that exist on the public history.

## ADR-0018: Benign-probe CKA excludes the first position in new measurements
- Date: 2026-11-08
- Status: accepted
- Context: The historical similarity stage mean-pools hidden states over every token of 3 to 7 word probes. Under causal attention the first position does not see the rest of the probe, and it carries an outsized state. A rerun on three locally available pairs (`experiments/results/cka_first_position_diagnostic.json`) reproduced the historical means exactly (0.9667, 0.9822, 0.9968) and found that position 0 supplies 99.5% of the pooled vector's norm in the middle layers of both Qwen2.5 and Llama 3.2. For Llama-3.2-1B-Instruct and Dolphin3.0, middle-layer CKA is 1.000 with all tokens and 0.959 to 0.986 without position 0.
- Decision: Add `similarity.skip_first_position` and record the pooling in the similarity artifact. It defaults to off so historical runs reproduce. Any new similarity covariate, including one for the prospective study, sets it on. The paper treats the historical profiles as a description of the old method, not of the checkpoints.
- Consequences: Appendix A reports the diagnostic. Near-1.0 historical CKA values say little about how similar two checkpoints are. Other sink-robust pooling choices remain open.

## ADR-0019: Operator decisions on the second review's open items
- Date: 2026-11-08
- Status: accepted
- Supersedes: ADR-0017 in part (publication of pre-squash revisions is now decided)
- Context: ADR-0016 and ADR-0017 left four choices to the operator: the target roster, the GCG search width, the classifier, and whether to publish the two pre-squash revisions cited by committed evidence.
- Decision: Keep the roster as configured: TinySwallow is the only primary declared child, and the Llama protocol has none. Keep GCG width 64 and top-k 64. Keep HarmBench-Mistral-7b-val-cls as the classifier; the 13B classifier is not part of the protocol. Do not publish `fe058f8` or `89ea4ec`; the frozen code digest remains the pilot's code identity.
- Consequences: The paper's limitations stand as written: one primary child, a reduced search guarded by the adequacy rule, a validation classifier checked only by the human audit, and pilot code that readers cannot fetch by revision.

## ADR-0020: Location of the pre-squash revisions
- Date: 2026-11-09
- Status: accepted
- Supersedes: ADR-0017 in part (where the pre-squash history is kept)
- Context: ADR-0017 records that the pre-squash history is kept on the local branch `backup/pre-squash-2026-11-08`. The operator deleted that branch and `backup/pre-squash-2026-11-08-b` on 2026-11-09.
- Decision: The two cited revisions stay reachable through older local branches. `fe058f8` is on `docs/author-preprint-validation`, `docs/figure1-layout`, `docs/paper-presentation` and `fix/review-validity`. `89ea4ec` is on the three `docs/*` branches. ADR-0019 still applies: neither revision is published.
- Consequences: Deleting the three `docs/*` branches removes the last copy of `89ea4ec`, and deleting those and `fix/review-validity` removes the last copy of `fe058f8`. After that, the frozen code digest is the only record of the pilot code's identity.

## ADR-0021: Third-review protocol revisions before initialization
- Date: 2026-11-11
- Status: accepted
- Supersedes: ADR-0016 in part (sign-flip method, commitment check, audit interpretation)
- Context: A third review ran before either prospective plan was initialized. It found four gaps. The primary sign-flip test used Monte Carlo draws at 20 behaviors although exact enumeration is cheap. The power analysis covered one contrast and the exploratory rank test, not the full decision rule or the confirmatory top-k test. "The audit supports the classifier" had no frozen meaning. The commitment check accepted a local commit that was never pushed. `generate` also rewrote the whole evidence file after every generation.
- Decision: The sign-flip test enumerates all sign patterns for up to 20 behaviors. `lasa.power` simulates the both-controls decision rule and the top-k test. `decision.audit` freezes a classifier-validity criterion: per annotator, design-weighted precision at least 0.8 and design-weighted false-omission rate at most 0.05; inter-annotator raw agreement at least 0.9. The rule uses raw agreement because kappa collapses when one label dominates. A confirmatory search requires a remote-tracking branch to contain the commitment commit. `generate` writes once per behavior and condition. The analysis reports the largest gap between nanoGCG's loss and the loss recomputed on the evaluated IDs.
- Consequences: The three audit thresholds are the operator's to change before initialization; after that they are frozen with the plan. The confirmatory top-k test needs a Spearman correlation of about 0.7 to 0.8 for 80% power, so a null prioritization result is uninformative. An interrupted `generate` repeats at most one behavior-condition cell, with the same seeds. The pilot has no frozen criterion and reports `no_frozen_criterion`.

## ADR-0022: Study stages may run on a rented GPU host; raw evidence transits one private dataset
- Date: 2026-11-12
- Status: accepted. The operator confirmed it on 2026-11-12, before the first job.
- Supersedes: ADR-0014 and ADR-0016 in part (where evidence is produced and stored)
- Context: The local machine generates about one token per second on a 1.5B model. A full-width search and 60 behaviors are out of reach locally, and the 2026 model families are larger. Until now, suffixes and completions never left gitignored local storage.
- Decision: `scripts/launch_hf_job.sh` runs a stage as a Hugging Face Job on the operator's account. The job clones the public repository at one commit, installs `requirements-study.txt`, and calls the same `lasa` commands, so the authorization gate and frozen-plan checks apply unchanged. The plan is frozen inside the job, because the plan records the environment. The job syncs the study directory to one private dataset on the operator's account. The job refuses a dataset that is not private. The operator pulls the files into `reports/` and deletes the dataset. Analysis-only stages (`audit-score`, `analyze`) accept a different environment when numpy and scipy match the plan, and the summary lists every differing package.
- Consequences: Raw outputs exist on Hugging Face infrastructure for the life of the job and the dataset. Nothing raw enters git or a public location. Every model in the run is still a public checkpoint named in the authorization record; no third-party system is queried. The commitment check still needs a pushed commit, so a run needs two jobs: `init`, then `run` after the operator commits and pushes `plan_commitment.json`. A stopped job resumes from the dataset.

## ADR-0023: nanoGCG 0.3.0 runs outside its declared transformers range for the v3 studies
- Date: 2026-11-12
- Status: accepted
- Supersedes: none. Narrows the pin rule in CLAUDE.md for one environment.
- Context: nanoGCG 0.3.0 declares `transformers<=4.47.1`. Granite 4.1 needs transformers 4.53 or later. nanoGCG has no newer release.
- Decision: The v3 studies use `requirements-study.txt` (torch 2.15.0, transformers 5.22.2, every package pinned) and install `nanogcg==0.3.0` with `--no-deps`. The wrapped attack code is unchanged. The package's default pins in `pyproject.toml` stay as they are for the earlier measurements.
- Consequences: The test suite passes in both environments, including the test that recomputes nanoGCG's loss on the evaluated token IDs. Each search also records the gap between nanoGCG's loss and the recomputed loss, so a silent incompatibility would show in the summary. The frozen plan records every package version.

## ADR-0024: Version 3 protocol: a 2026 family, 60 behaviors, full search width
- Date: 2026-11-12
- Status: accepted. Each plan is still open to change until it is initialized.
- Supersedes: ADR-0019 in part (GCG width 64); ADR-0021 in part (exact test range). Replaces the uninitialized `study-qwen15-v2.yaml`.
- Context: The operator asked for recent models and a stronger evaluation. The earlier roster dates from 2024. Twenty held-out behaviors detect only large uplifts. The search used one eighth of nanoGCG's default width. Qwen has one primary declared child and Llama has none.
- Decision: Add `study-granite41-v1.yaml`: IBM Granite 4.1 3B (April 2026) as source, with three declared children (security-hardened, coder, abliterated). Add `study-qwen15-v3.yaml` with the v2 roster. Both use 6 behaviors per category (30 development, 30 held-out), nanoGCG's default width 512 and top-k 256, one frozen dtype (bfloat16) for every checkpoint, and all five replicates of a cell drawn in one call with one seed per behavior. The sign-flip test counts all sign patterns exactly up to 40 behaviors. `chat_template.jinja` is fetched and hashed, because recent checkpoints keep the template there. Reasoning fine-tunes stay excluded. The classifier and the decision rules do not change.
- Consequences: Thirty held-out behaviors detect mean uplifts of about 0.15 to 0.24 under the full decision rule, against 0.21 to 0.30 at twenty (`power_analysis_n30.json`). The top-5 test still needs a rank correlation near 0.75. The abliterated child should fail the headroom rule; if it does, that is reported as a rule outcome. Granite children differ in tokenizer file bytes from the source, so `suffix_ids_match_source` matters for them. The Llama v1 plan is unchanged and not scheduled. The operator confirmed the new model IDs on 2026-11-12. They are in a separate record, `configs/authorization.study-v3.json`, so the authorization digest frozen in earlier plans does not change.

## ADR-0025: A model audit replaces the human audit for the v3 paper; the frozen criterion stays pending
- Date: 2026-11-13
- Status: accepted. The operator decided this on 2026-11-13, after the classifier outcomes of both v3 studies were known.
- Supersedes: none. ADR-0021 stays in force. This is a recorded exception to the CLAUDE.md rule that an LLM judge is always paired with a human spot-check.
- Context: Both v3 studies are generated, judged, and analyzed. The frozen classifier-validity criterion needs two human annotators on 120 outputs per study. The operator chose not to run a human audit for this paper and asked for a review by Claude models instead.
- Decision: Two Claude models label each study's audit sample independently, following `configs/judges/model_audit_instructions.md`. They see the behavior and the output text and nothing else. Their sheets are `human_labels_3.csv` and `human_labels_4.csv`, so the two human sheets stay empty and available. They are scored with `lasa study audit-score --labeler-kind model`, which writes `independent_audit_3.json` and `independent_audit_4.json`. No analysis code, plan, or threshold changes. `classifier_validation` stays `pending`, because the frozen rule counts human audits only. The paper reports the model audit with the same three quantities as the frozen rule, states that it is a post hoc substitute chosen after outcomes, and keeps the wording "classifier-positive".
- Consequences: The paper cannot claim human-validated harmful compliance. Two language models from one vendor are not independent of each other in the way two people are, and a model labeler can share blind spots with the classifier. A later human audit can still be scored on the untouched sheets and would complete the frozen criterion. Raw outputs are read by the labeling models inside the operator's own session and are not copied elsewhere.

## ADR-0026: Second single-commit history; the v3 commitment commits leave the public history
- Date: 2026-11-13
- Status: accepted. The operator chose this on 2026-11-13, after being told what it removes.
- Supersedes: ADR-0017 in part (its rule that future plans record revisions on the public history no longer holds for the v3 plans)
- Context: The operator wanted a clean history and asked for `main` to be squashed to one commit and force-pushed. Before the squash, `main` had eight commits. Three of them carried the v3 pre-commitment: `3e2b20c` (the code revision cited in both `plan_commitment.json` files), `782f77f` (the commit that published the frozen plans before search), and `02b1f9d` (the revision every study job checked out).
- Decision: Squash `main` to one commit authored by the operator and force-push it. Leave the evidence files unchanged. The content-based `code_digest` and `plan_digest` in each frozen plan remain the authoritative identities. The operator also deleted the local backup branch on the same day, so no branch holds the pre-squash history.
- Consequences: A reader can no longer check from the public repository that the plans were pushed before the search ran. The remaining independent records are the hosting service's push log and the compute host's job records, which name revision `02b1f9d` with their start times. The paper states this. With the backup branch deleted, the three revisions survive only in the local reflog until git prunes them.

