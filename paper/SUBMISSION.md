# Building and submitting the manuscript

## Reading copy

```sh
make -C paper pdf
```

This rebuilds figures from committed numerical pilot metadata, resolves citations from `references.bib` with Pandoc citeproc and `ieee.csl`, and writes `PAPER.pdf`. Both the reading copy and LaTeX bundle use the same title, abstract, bibliography, and typography: 11-point article text, one-inch margins, and linked references. The CSL style is distributed under the license recorded in its header.

`PAPER.md` is the manuscript source. BibTeX contains verified author lists and arXiv or PMLR links; software and model checkpoints have separate entries. No DOI, affiliation, publication venue, or result is supplied without a source.

## LaTeX source bundle

```sh
make -C paper arxiv
```

`build/arxiv/` contains `paper.tex`, referenced figures, and bibliography/style sources. Citeproc renders the bibliography into the LaTeX, so no BibTeX pass is required. The source uses Unicode fonts and compiles with XeLaTeX through Tectonic; a pdflatex build is not validated. Check arXiv's current processor rules and the venue's template before uploading. The generic article layout is not an official ICLR or ACM template.

`make -C paper validate` independently compiles the generated source and checks visible author metadata, embedded fonts, missing references and bundled figures. It writes `paper/build/validation.json` and a minimal `paper/build/submission.zip`. This local preflight is not a certificate of arXiv acceptance or research quality.

## Before submission

- [x] **Audit:** no human audit was run (ADR-0025). Two Claude models labeled each 120-output sample after outcomes were known. The frozen criterion stays `pending`. The paper reports the model audit in Section 6.8. The human sheets `human_labels.csv` and `human_labels_2.csv` are untouched if a human audit is wanted later.
- [x] **Roster:** kept as frozen. Arch-Router is a task control outside the primary family; the paper reports its result as descriptive.
- [x] **Final results and figures:** Section 6 cites `experiments/results/study-*/aggregate_results.json`, rebuilt after the model audit. `figures/study_results.png` and `experiments/evidence_inventory.json` are built from the same files.
- [x] **Claim verification:** Pilot counts, power numbers, CKA ranges, the Section 6 rate, contrast, and audit tables, and the inventory have automated checks. The prioritization table and the prose numbers are checked by hand only.
- [ ] **Responsible disclosure:** the operator stated on 2026-11-13 that the v3 notices were sent. Recipients, dates, and replies still need to be logged in `docs/disclosure/tracking.csv`.
- [x] **Provenance:** the pre-squash revisions cited by committed evidence stay unpublished (ADR-0019); the frozen code digest identifies the pilot code.
- [ ] **Venue:** None selected. Apply the venue's template and confirm page limits and artifact rules.

The current manuscript reports two confirmatory studies with classifier-based outcomes, a post hoc model audit that does not support the classifier, the pilot, and three measurement pitfalls. It makes no claim about harmful-compliance rates.
