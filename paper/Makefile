# PAPER.md and references.bib are the manuscript sources.
PY := ../.venv/bin/python

.PHONY: pdf figures arxiv validate clean

figures:
	$(PY) build_figures.py

pdf: figures
	$(PY) build_arxiv.py --pdf --reading-copy PAPER.pdf

arxiv: figures
	$(PY) build_arxiv.py --pdf

validate: pdf
	$(PY) validate_submission.py

clean:
	rm -rf build
