> Do not send this historical draft. Replace all measurements with independently validated findings before disclosure.

Subject: Heads-up: refusal-direction transfer measured on [CHECKPOINT]

Hello [OWNER],

I am [AUTHOR], a security researcher. Ahead of a preprint on inherited jailbreak
risk in fine-tuned open-weight models, I am sharing a finding about a checkpoint
you publish, [CHECKPOINT], as a courtesy before publication.

Using the public refusal-direction method of Arditi et al. (2024), I extracted the
refusal direction from [BASE] and applied it as an activation ablation to
[CHECKPOINT]. Across 12 JailbreakBench behaviors, 5 generations each, measured
against a no-attack control, this raised the non-refusal rate by [RD_DELTA]
(signed base-to-variant refusal-direction cosine: [REFCOS]). An automated label
was used, so the figure is an upper bound on actual harmful compliance; see the
limitations section of the paper.

This is not a new attack. It confirms, on your checkpoint, the known result that a
base model's refusal direction transfers to its derivatives. I am not sharing any
harmful generations. The method, the aggregate numbers, and the code are in the
paper and repository.

I plan to submit no earlier than [DATE, >= 30 days out]. If you would like the
per-behavior detail, want a correction, or would prefer different handling, please
reply and I will hold.

Thank you,
[AUTHOR]
[CONTACT]
