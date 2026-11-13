# Project review

Review scope: authorization and schema packaging; checkpoint loading; published-tool integration; similarity and legacy artifacts; the prospective study's freeze, search, generation, judging, audit, and analysis; manuscript build and evidence provenance. Automated tests use benign fixtures.

## Corrected in this review

- Authorization is schema-checked in installed wheels, including dates; constructed records cannot authorize model-ID substrings. The study checks authorization before creating evidence or lock files.
- The contrast collector uses the last attended position and explicit attention-mask position IDs (`last-attended-v3`). Both rotary and learned-position tiny models are tested with left and right padding. A real Qwen float16 benign diagnostic is recorded; batch-size differences are not bitwise zero.
- A separate judge must differ by model ID and weight-file content. Frozen config, benchmark selection, rubric, source files, dependency versions, and checkpoint hashes are checked across stages.
- One writer holds a study lock. Evidence is private, atomically replaced, and validated against the frozen Cartesian design. Target parse failures cannot select the source-ranking population; incomplete held-out labels block analysis.
- Audit labels attach to exact stored generations, distinguish declared human from model labels, and report confusion counts. Blank sheets never count as a completed audit.
- Source-only top-k ties use a recorded seeded ordering. Degenerate bootstrap intervals are flagged instead of reported as certainty. The prospective summary records per-category classifier-positive rates, task-control roles, and generation-budget censoring.
- The historical local refusal-ablation approximation was incorrectly represented as the published Arditi implementation. New attack and verification execution on that path is disabled pending a pinned reference adapter. Existing historical results remain superseded. The prompt study uses the pinned nanoGCG package.
- Historical aggregate reports distinguish five source-checkpoint groups from four model lineages. Report text warns that legacy outcome labels are not validated harmful-compliance evidence.
- Local checkpoints load safetensors only with remote code disabled. The paper prints the repository URL, email, and date, includes an evidence inventory and descriptive benign CKA profiles, and builds a source archive with a technical preflight.

## Open before scientific claims

1. Run the reviewed Qwen and Llama prospective protocols. The previous Qwen v1 search stopped before its first behavior completed and before any target outcomes. New code requires a new frozen plan.
2. Complete the exact-generation human audit and validate the independent classifier. Existing pilot labels are automated and include budget-censored outputs.
3. Confirm declared model lineage from reliable training documentation. The TOFU checkpoint's public model card does not specify its base and is a task control with unverified provenance.
4. Freeze a censoring/adjudication rule and benchmark sensitivity analyses before observing full-study outcomes. Current primary results would include classifier labels for budget-censored generations and flag them separately.
5. The held-out behaviors have source-optimized suffixes; the split tests source-score ranking and protocol selection, not generalization to unseen attacks or unseen behaviors. A new multi-source, held-out-model study is needed for broader predictive claims.
6. The historical CKA profiles have not been independently rerun, and old manifests lack a frozen probe-file digest. They are descriptive only.
7. Legacy `lasa run` and `lasa aggregate` are retained for reproducibility and still contain heuristic labels and approximate reporting; they are not the prospective study or validated compliance results.
8. No venue is selected. The source bundle compiles locally with Tectonic/XeTeX; arXiv server compilation, venue-format compliance, independent peer review, and actual owner notification have not occurred. Historical disclosure drafts must not be sent as confirmed findings.

## Second review (2026-11-08)

Scope: the paper and the prospective study before Qwen v2 or Llama v1 were initialized. Decisions are in ADR-0016 to ADR-0018.

### Corrected

- Generation re-encoded `instruction + " " + suffix` as one string. nanoGCG optimizes `instruction + suffix` with separately tokenized parts. Every tested suffix changed IDs on the Qwen tokenizer. All prompts now use the nanoGCG split, search records the loss on the evaluated IDs, and a test reproduces nanoGCG's loss from those IDs.
- The Mistral classifier received HarmBench's Llama-2 template. It now receives the template from its own model card, and initialization requires the rubric to name its classifier. The pilot's labels used the old template.
- Every behavior used the generic target `Sure, here is`. Prospective configs load JailbreakBench's published targets, pinned by dataset revision and per-instruction digest.
- The development split fed no endpoint. It now drives the attack-adequacy gate, the headroom rule, and score selection.
- No decision rule, multiplicity correction, or power analysis was frozen. Confirmatory plans now carry alpha, primary controls, and Holm correction; contrasts use sign-flip tests; top-k utility has an exact random-selection null; `lasa.power` reports detectable effects.
- The random-suffix null used eight English words. A mismatched-suffix control now separates behavior-specific transfer from a generic suffix effect.
- A simple random audit of 120 outputs would contain few positives. The audit is now stratified by classifier label, weighted, and labeled by two annotators.
- The frozen plan was not public. Initialization writes `plan_commitment.json`, and confirmatory search requires it to be committed.
- The committed pilot summary came from pre-review analysis code. It is regenerated with the current code.
- Historical benign CKA was dominated by position 0 (99.5% of the pooled norm in middle layers). New measurements exclude it.
- Figure 2's legend hid source points, and Figure 3 clipped a CKA value of 0.40.
- The Llama source is a mirror; its weights match Meta's release by SHA-256.

### Operator decisions (ADR-0019)

1. Keep the roster: one primary declared child for Qwen and none for Llama.
2. Keep GCG width 64. The adequacy gate reports an underpowered search instead of a null.
3. Keep the Mistral-7B validation classifier; the official HarmBench 13B classifier is not used.
4. Do not publish the pre-squash revisions `fe058f8` and `89ea4ec` (ADR-0017).

The items under "Open before scientific claims" above still apply, apart from those closed here.

## Third review (2026-11-11, ADR-0021)

Findings, all fixed before any prospective plan was initialized:

1. The primary sign-flip test fell back to Monte Carlo at 20 behaviors. It is now exact.
2. The power analysis omitted the full decision rule and the confirmatory top-k test. Both are simulated. The top-k test needs a rank correlation of about 0.7 to 0.8, which the paper now states.
3. No frozen rule said when the audit supports the classifier. `decision.audit` now does.
4. The commitment check accepted an unpushed commit. It now requires a remote-tracking branch.
5. `generate` rewrote the evidence file after every generation. It now writes once per behavior and condition.
6. The paper did not say that the Llama protocol may end with no primary target, or that git commit dates are self-asserted. It now does. Related work adds three verified references on inconsistent trigger transfer, fine-tuning and safety, and judge validity.

Open for the operator: the three audit thresholds (0.8, 0.05, 0.9) are a judgment call and can be changed before initialization.

